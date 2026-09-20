"""T-62 — the ADK extraction runner: the model leaf, as a real ADK agent (D62).

`AdkExtractionRunner` satisfies `ExtractionRunner` (REQ-52) and returns through
`build_result()` like every other implementation. That function is the trust
boundary: ADK output is untrusted model output, and nothing here constructs a
`WmEvent` or a span directly.

**This is a return to the configuration D19 was measured on**, not a new idea.
`spike/spike_001/run.py` built exactly this — `Agent(..., output_schema=Extraction,
output_key="extraction")`, one `Runner` and one `InMemorySessionService` per note,
payload read off session state, tokens summed from `event.usage_metadata`. T-15
then wrote `pa_agent/extraction.py` against raw `google-genai`, so the production
path and the founding measurement have been different SDKs since D45. Still a new
measurement relative to D45 — see T-63 and the tier note below.

### Article I holds by construction, and each part is assertable

- `include_contents="none"` — one shot per note. No accumulated context to steer
  with, and no note N-1 bleeding into note N (the spike's D17 finding).
- `disallow_transfer_to_parent=True`, `disallow_transfer_to_peers=True`, and no
  `sub_agents`. ADK's `_llm_flow` property then selects `SingleFlow`, so the
  `transfer_to_agent` tool is **never injected**. The model has no mechanism to
  hand control anywhere, which is stronger than it not choosing to.
- `tools=` is a literal allowlist (REQ-53), and the workflow — not the model —
  decides which documents are in scope and loops over them in fixed Python.
- `RunConfig(max_llm_calls=...)` is a hard ceiling that raises
  `LlmCallsLimitExceededError`. It is ADK's only budget knob and it counts LLM
  calls, so one tool round trip costs two.
- Retry is a Python budget in `pa_agent.workflow`, never a model decision.

### The tier changes the prompt, which is why `tool_fetch` is a flag

`output_schema` and `tools` are usable together in 2.8.0, but natively only on
Vertex: `flows/llm_flows/basic.py` sets a native response schema only when
`model.capabilities.output_schema_and_tools`, and `models/_capabilities.py` gates
that on the Vertex variant. On AI Studio with tools present, ADK instead injects a
`SetModelResponseTool` and appends an instruction telling the model to answer
*through* that tool.

D5 develops on AI Studio and evals on Vertex. So the tool-calling path runs a
measurably different prompt on the two tiers, and a number from one is not a number
for the other. `tool_fetch=False` reproduces the spike's exact configuration — note
text in the message, no tools — so a comparison against D19 is apples to apples.
`tool_fetch=True` puts a real tool call on the critical path. T-63 measures both and
names the tier.

The client is injected, so nothing here reads a credential or names a tier (D5).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pa_agent.contracts import CallMetrics, ToolCall
from pa_agent.extraction import (
    EXTRACTION_TEMPERATURE,
    INSTRUCTION,
    PROMPT_VERSION,
    REASK_INSTRUCTION,
    Extraction,
    ExtractionResult,
    Turn,
    VerbatimAnswers,
    extract_with_reask,
    format_targets,
)
from pa_agent.model_pin import PINNED_MODEL
from pa_agent.runners import ExtractionFailure, ExtractionOutputError
from pa_agent.stores.patient import PatientStore

from .patient_tools import build_note_reader

#: The agent's name. Must be a valid Python identifier — ADK's `BaseNode`
#: validates `str.isidentifier()`.
AGENT_NAME = "wm_event_extractor"

#: Where ADK writes the validated payload in session state.
OUTPUT_KEY = "extraction"

#: T-89's second agent: the verbatim re-ask (REQ-56, D103). A separate agent
#: with a separate schema and output key, because `include_contents="none"`
#: means a follow-up message would arrive with no history — so the re-ask is a
#: second self-contained invocation, not a second turn of the first.
REASK_AGENT_NAME = "wm_event_verbatim_reask"
REASK_OUTPUT_KEY = "verbatim"

#: `PROMPT_VERSION` lives beside the instruction in `pa_agent.extraction` since
#: T-89 and names the whole configuration — both runners share it. Re-exported
#: here so the recording script and the tests keep resolving it (D103).
__all__ = ["PROMPT_VERSION"]

#: The one tool the extraction agent may reach under `tool_fetch` (REQ-53, T-66).
#:
#: **`get_patient_observations` and `get_patient_conditions` are deliberately
#: excluded**, and so now is every other patient-plane tool. T-33 and T-60 exist
#: because the structured BMI and the note BMI are two independent readings of one
#: fact, and REQ-34 turns a disagreement across 35.0 into `SOURCE_CONFLICT`. Give
#: the extractor the structured value and the cheapest way to report a note BMI
#: becomes reporting the one it just looked up — E10b, where the two disagree by
#: 1.6 across the threshold, quietly starts agreeing, and every test still passes
#: because the tests compare the two values and would now find them equal.
#:
#: T-66 narrowed this from two tools to one. `read_note` is built by
#: `build_note_reader` and scoped in Python to the single document under review,
#: so the exclusion above is no longer a list of names withheld — it is the
#: absence of any reachable second document (D66). `get_patient_notes` was here
#: only so the model could resolve an id it was given, which a tool taking that id
#: does not need, and it took a `patient_id` this runner does not have.
EXTRACTION_ALLOWLIST = ("read_note",)

#: Hard ceiling on LLM calls per **invocation** — ADK's `max_llm_calls` counts
#: one `run_async`. Two turns is a tool call and its answer; six leaves room
#: for a retried tool call and no room for a loop. A note has at most
#: `1 + REASK_ROUNDS` invocations (the extraction, and T-89's re-ask when a
#: quote could not be located), so the per-note ceiling is that many times
#: this. Not split between the two: ADK disables enforcement when the value
#: reaches zero, so a "remaining budget" scheme would be a ceiling that
#: vanishes exactly when it is reached (D103).
DEFAULT_MAX_LLM_CALLS = 6


_READ_FIRST = (
    "First call read_note with the document_id you are given, to read the "
    "note. Then {verb} from the text it returns.\n\n"
)


def _instruction(tool_fetch: bool) -> str:
    """T-15's prompt, verbatim, plus a retrieval preamble only when needed.

    The instruction is not rewritten for the ADK. D19 and D45 were measured on
    this exact formulation with no tuning spent, and paraphrasing it would restart
    the prompt's history at zero for no gain.
    """
    if not tool_fetch:
        return INSTRUCTION
    return _READ_FIRST.format(verb="extract") + INSTRUCTION


def _reask_instruction(tool_fetch: bool) -> str:
    """T-89's re-ask, with the same retrieval preamble shape as the extractor's."""
    if not tool_fetch:
        return REASK_INSTRUCTION
    return _READ_FIRST.format(verb="answer") + REASK_INSTRUCTION


def _note_tools(
    patient_store: PatientStore | None, tool_fetch: bool, document_id: str | None
) -> list:
    """The allowlist, built once for both agents: `read_note` scoped to the
    document under review, or nothing (REQ-53, T-66)."""
    if not tool_fetch:
        return []
    if patient_store is None:
        raise ValueError(
            "tool_fetch=True needs a PatientStore to build the note tools "
            "over; the model has no other way to read a document (REQ-41)"
        )
    if document_id is None:
        raise ValueError(
            "tool_fetch=True needs the document_id under review: the note "
            "reader's scope is fixed in Python before the run, not parsed "
            "out of whatever id the model asks for (T-66, D66)"
        )
    return build_note_reader(patient_store, document_id).allowlist(*EXTRACTION_ALLOWLIST)


def build_extraction_agent(
    patient_store: PatientStore | None = None,
    tool_fetch: bool = False,
    model: Any = PINNED_MODEL,
    document_id: str | None = None,
):
    """One `LlmAgent` for note extraction. Constructed, not run.

    Separated from the runner so a test can inspect the agent — its flow type, its
    tool list, whether `transfer_to_agent` was injected — without a session, a
    runner, or a credential.

    `model` accepts a string or a `BaseLlm`. A `BaseLlm` instance is how a test
    drives the real ADK flow with no network: ADK resolves a string through
    `LLMRegistry` and uses an instance as given.
    """
    return _build_agent(
        name=AGENT_NAME,
        description="Extracts weight-management encounters from a clinical note.",
        instruction=_instruction(tool_fetch),
        tools=_note_tools(patient_store, tool_fetch, document_id),
        output_schema=Extraction,
        output_key=OUTPUT_KEY,
        model=model,
    )


def build_reask_agent(
    patient_store: PatientStore | None = None,
    tool_fetch: bool = False,
    model: Any = PINNED_MODEL,
    document_id: str | None = None,
):
    """The verbatim re-ask agent (T-89, REQ-56, D103). Constructed, not run.

    The extractor's flags, tools and temperature exactly — one constructor,
    `_build_agent`, is what makes that true by construction rather than by
    review — with the re-ask instruction and `VerbatimAnswers` as the schema.
    It can copy text from the one note in scope and do nothing else.
    """
    return _build_agent(
        name=REASK_AGENT_NAME,
        description="Returns the verbatim note text for quotes that could not be located.",
        instruction=_reask_instruction(tool_fetch),
        tools=_note_tools(patient_store, tool_fetch, document_id),
        output_schema=VerbatimAnswers,
        output_key=REASK_OUTPUT_KEY,
        model=model,
    )


def _build_agent(*, name, description, instruction, tools, output_schema, output_key, model):
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types

    return LlmAgent(
        name=name,
        model=model,
        description=description,
        instruction=instruction,
        tools=tools,
        output_schema=output_schema,
        output_key=output_key,
        # One note, no history. Art. I: nothing to steer with, and D17's finding
        # that a shared session carries note N-1 into note N.
        include_contents="none",
        # With no sub_agents, these two make ADK select `SingleFlow`, so the
        # `transfer_to_agent` tool is never injected and the model cannot hand
        # control anywhere (Art. I).
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        # Art. II: the same note yields the same events, or the computation is in
        # the wrong place. `response_schema` is refused here by ADK's own
        # validator and belongs on `output_schema` above.
        generate_content_config=types.GenerateContentConfig(
            temperature=EXTRACTION_TEMPERATURE
        ),
    )


def _classify(exc: Exception) -> ExtractionFailure:
    """A run's exception, as REQ-27's closed set. Pydantic reports text that
    is not JSON as a `ValidationError` of type `json_invalid`, which is
    `UNPARSEABLE`; any other `ValidationError` is the schema refusing the
    output; everything else is the call."""
    if type(exc).__name__ != "ValidationError":
        return ExtractionFailure.CALL_FAILED
    errors = getattr(exc, "errors", lambda: [])()
    if errors and all(e.get("type") == "json_invalid" for e in errors):
        return ExtractionFailure.UNPARSEABLE
    return ExtractionFailure.SCHEMA_INVALID


def build_trace_recorder(purpose: str = "extraction"):
    """A `BasePlugin` that records tokens and the tool-call sequence (REQ-49).

    `purpose` is what every `CallMetrics` this recorder writes is labelled with —
    `"extraction"` for the first invocation, `"extraction_reask"` for T-89's.

    The class is defined inside the function so importing this module does not
    require ADK's plugin machinery, and **every hook body is wrapped**: ADK
    re-raises a plugin exception as a `RuntimeError` that aborts the whole run, so
    an observability bug must not be able to kill a determination. A hook that
    fails is not silent about it (REQ-27, T-29): it notes itself on `failures`,
    which the runner writes into the trace's termination reason — a recording
    bug that killed the run it was observing would invert the point of
    observability, but one that hid itself would be the audit's own target.
    """
    from google.adk.plugins.base_plugin import BasePlugin

    class Recorder(BasePlugin):
        def __init__(self) -> None:
            super().__init__(name="pa_trace")
            self.metrics: list[CallMetrics] = []
            self.tool_calls: list[ToolCall] = []
            self.failures: list[str] = []
            self.model_name: str | None = None
            self._model_started: float | None = None
            self._tool_started: dict[str, float] = {}

        def _note(self, hook: str, exc: Exception) -> None:
            """The mapped, named form REQ-27 asks for when raising is wrong."""
            self.failures.append(f"{hook}: {type(exc).__name__}: {exc}")

        async def before_model_callback(self, *, callback_context, llm_request):
            try:
                self._model_started = time.perf_counter()
                self.model_name = getattr(llm_request, "model", None)
            except Exception as exc:  # pragma: no cover - noted, never kills a run
                self._note("before_model", exc)
            return None

        async def after_model_callback(self, *, callback_context, llm_response):
            try:
                usage = getattr(llm_response, "usage_metadata", None)
                elapsed = 0.0
                if self._model_started is not None:
                    elapsed = (time.perf_counter() - self._model_started) * 1000.0
                self.metrics.append(
                    CallMetrics(
                        model=self.model_name or "unknown",
                        purpose=purpose,
                        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
                        output_tokens=(
                            getattr(usage, "candidates_token_count", 0) or 0
                        ),
                        wall_time_ms=max(elapsed, 0.0),
                    )
                )
            except Exception as exc:  # pragma: no cover - noted, never kills a run
                self._note("after_model", exc)
            return None

        async def before_tool_callback(self, *, tool, tool_args, tool_context):
            try:
                self._tool_started[tool.name] = time.perf_counter()
            except Exception as exc:  # pragma: no cover - noted, never kills a run
                self._note("before_tool", exc)
            return None

        async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
            try:
                started = self._tool_started.pop(tool.name, time.perf_counter())
                self.tool_calls.append(
                    ToolCall(
                        name=tool.name,
                        # A digest, not the arguments: a trace holding
                        # `patient_id` verbatim is patient data in an
                        # instrumentation record (Art. VI, D62).
                        arguments_digest=ToolCall.digest(dict(tool_args or {})),
                        ok=not (
                            isinstance(result, dict) and "error" in result
                        ),
                        wall_time_ms=(time.perf_counter() - started) * 1000.0,
                    )
                )
            except Exception as exc:  # pragma: no cover - noted, never kills a run
                self._note("after_tool", exc)
            return None

        async def on_tool_error_callback(
            self, *, tool, tool_args, tool_context, error
        ):
            try:
                started = self._tool_started.pop(tool.name, time.perf_counter())
                self.tool_calls.append(
                    ToolCall(
                        name=tool.name,
                        arguments_digest=ToolCall.digest(dict(tool_args or {})),
                        ok=False,
                        wall_time_ms=(time.perf_counter() - started) * 1000.0,
                        detail=type(error).__name__,
                    )
                )
            except Exception as exc:  # pragma: no cover - noted, never kills a run
                self._note("on_tool_error", exc)
            return None

    return Recorder()


class AdkExtractionRunner:
    """`ExtractionRunner` over `google-adk` 2.8.0 (REQ-52, T-62).

    One fresh session per note. Not `run_debug`, whose shared default session id
    would carry note N-1 into note N's context — the spike found that and D17
    recorded it.

    Raises `ExtractionOutputError` on no payload, unparseable output, or a payload
    the schema rejects. **It never returns a zero-event result to signal a
    failure**, because a note that extracts nothing leaks no REQ-9 traps and
    contributes no false positives, so a transport error would score as flawless
    extraction — the trap the spike documents in writing.
    """

    name = "adk"

    def __init__(
        self,
        client: Any = None,
        patient_store: PatientStore | None = None,
        tool_fetch: bool = False,
        model: str = PINNED_MODEL,
        llm: Any = None,
        max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
        app_name: str = "pa_agent",
    ) -> None:
        self._tool_fetch = tool_fetch
        self._max_llm_calls = max_llm_calls
        self._app_name = app_name
        self._patient_store = patient_store
        self._model_name = model
        self._llm = llm
        self._client = client
        self._counter = 0

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def tool_fetch(self) -> bool:
        return self._tool_fetch

    def _model_argument(self) -> Any:
        """A `BaseLlm` when one is available, else the pinned model name.

        `Gemini` accepts a pre-built `genai.Client` and creates its own only
        lazily, so injecting the client here preserves the property
        `extraction.extract()` already has: nothing in this module reads a
        credential or names a tier, and the caller chooses which (D5).
        """
        if self._llm is not None:
            return self._llm
        if self._client is not None:
            from google.adk.models.google_llm import Gemini

            return Gemini(model=self._model_name, client=self._client)
        return self._model_name

    def build_agent(self, document_id: str | None = None):
        """The agent for one note.

        Takes the `document_id` because under `tool_fetch` the note reader is
        scoped to it in Python before the run starts (T-66). A fresh agent per
        note was already the shape — `_invoke` builds one each time — so this
        makes an existing per-note construction carry the thing that makes it
        per-note.
        """
        return build_extraction_agent(
            patient_store=self._patient_store,
            tool_fetch=self._tool_fetch,
            model=self._model_argument(),
            document_id=document_id,
        )

    def run(self, document_id: str, text: str) -> ExtractionResult:
        # One session id per note, advanced once per `run()` — the re-ask
        # invocation derives its own from it, so two notes never share one
        # (D17) and a re-ask never shares the extraction's.
        self._counter += 1
        session = f"{document_id.replace('/', '_')}-{self._counter}"
        return extract_with_reask(
            document_id,
            text,
            first_turn=lambda: self._first_turn(document_id, text, session),
            reask_turn=lambda targets: self._reask_turn(document_id, text, targets, session),
            runner_name=self.name,
            model=self._model_name,
            step_names=("adk_run", "adk_reask"),
        )

    def _first_turn(self, document_id: str, text: str, session: str) -> Turn:
        """The extraction invocation. Raises classified on any failure, because
        a note that produced no extraction has nothing to build a result from
        and returning an empty one would score as flawless (see the class
        docstring)."""
        if self._tool_fetch:
            message = (
                f"Extract the weight-management facts from document_id: "
                f"{document_id}"
            )
        else:
            message = f"Clinical note (document_id: {document_id}):\n\n{text}"

        payload, metrics, tool_calls, termination, error = self._invoke(
            self.build_agent(document_id), message, OUTPUT_KEY, session, "extraction"
        )
        if error is not None:
            raise error
        if payload is None:
            raise ExtractionOutputError(
                ExtractionFailure.NO_PAYLOAD,
                f"{document_id}: the run produced no structured output at "
                f"session state {OUTPUT_KEY!r}. An empty extraction is not the "
                "same claim as no extraction, and returning one here would make "
                "a transport failure score as flawless precision.",
            )
        try:
            Extraction.model_validate(payload)
        except Exception as exc:
            raise ExtractionOutputError(
                ExtractionFailure.SCHEMA_INVALID,
                f"{document_id}: {type(exc).__name__}: {exc}",
            ) from exc
        return Turn(payload=payload, metrics=metrics, tool_calls=tool_calls, termination=termination)

    def _reask_turn(
        self, document_id: str, text: str, targets: list[dict], session: str
    ) -> Turn:
        """T-89's re-ask invocation (REQ-56, D103). Never raises: a failure is
        returned classified on `Turn.error` and the first turn's result stands.

        A second self-contained invocation rather than a follow-up message,
        because `include_contents="none"` — the property that keeps note N-1
        out of note N — also keeps the first turn out of the second, so the
        note reaches the model again the same way it did the first time: in
        the message, or through `read_note`.
        """
        if self._tool_fetch:
            message = (
                f"Find the verbatim text in document_id: {document_id}\n\n"
                f"{format_targets(targets)}"
            )
        else:
            message = (
                f"Clinical note (document_id: {document_id}):\n\n{text}\n\n"
                f"{format_targets(targets)}"
            )
        payload, metrics, tool_calls, termination, error = self._invoke(
            self.build_reask_agent(document_id), message, REASK_OUTPUT_KEY,
            f"{session}-reask", "extraction_reask",
        )
        return Turn(
            payload=payload,
            metrics=metrics,
            tool_calls=tool_calls,
            termination=termination,
            error=f"{error.reason.value}: {error.message}" if error is not None else None,
        )

    def build_reask_agent(self, document_id: str | None = None):
        return build_reask_agent(
            patient_store=self._patient_store,
            tool_fetch=self._tool_fetch,
            model=self._model_argument(),
            document_id=document_id,
        )

    def _invoke(
        self, agent, message: str, output_key: str, session_id: str, purpose: str
    ) -> tuple[dict | None, list[CallMetrics], list[ToolCall], str, ExtractionOutputError | None]:
        """One agent, one fresh session, one message; what came back and what it
        cost. Classifies a failure and returns it rather than raising, so the
        caller decides whether that failure is fatal (`_first_turn`) or
        recorded (`_reask_turn`)."""
        from google.adk.agents.run_config import RunConfig
        from google.adk.apps import App
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        recorder = build_trace_recorder(purpose)
        session_service = InMemorySessionService()
        # Plugins arrive via `App`, not via `Runner(plugins=...)` — the latter is
        # deprecated in 2.8.0 and warns on every construction.
        runner = Runner(
            app=App(
                name=self._app_name, root_agent=agent, plugins=[recorder]
            ),
            session_service=session_service,
        )

        started = time.perf_counter()
        termination = "ok"
        error: ExtractionOutputError | None = None
        try:
            asyncio.run(
                self._drive(
                    runner, session_service, session_id, message, RunConfig, types
                )
            )
        except Exception as exc:
            termination = type(exc).__name__
            # ADK validates the final answer against `output_schema` and raises
            # pydantic's `ValidationError` out of the run: that is the model's
            # output failing to parse or failing the schema, not the call
            # failing (D103, on D68's precedent for a small fold).
            reason = _classify(exc)
            error = ExtractionOutputError(
                reason,
                f"{session_id}: {type(exc).__name__}: {exc}. Tool calls made: "
                f"{[c.name for c in recorder.tool_calls]}",
            )

        elapsed = (time.perf_counter() - started) * 1000.0
        metrics = recorder.metrics or []
        if not metrics and error is None:
            # No model response reached the recorder, so no measurement exists.
            # Recording a zero would be an estimate presented as instrumentation
            # (Art. X), so the trace carries none and the wall time rides on the
            # termination reason instead.
            termination = "no_model_response"

        payload: dict | None = None
        if error is None:
            try:
                payload = self._payload(session_service, session_id, output_key)
            except ExtractionOutputError as exc:
                error = exc
        if recorder.failures:
            # T-29 (D76): a hook that failed says so in the trace it produced.
            termination = (
                f"{termination}; recorder_failures: {recorder.failures}"
            )
        return (
            payload,
            metrics,
            list(recorder.tool_calls),
            f"{termination} ({elapsed:.0f}ms)",
            error,
        )

    async def _drive(
        self, runner, session_service, session_id, message, RunConfig, types
    ) -> None:
        await session_service.create_session(
            app_name=self._app_name, user_id="pa", session_id=session_id
        )
        try:
            async for _event in runner.run_async(
                user_id="pa",
                session_id=session_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=message)]
                ),
                run_config=RunConfig(max_llm_calls=self._max_llm_calls),
            ):
                # The events are consumed for their side effects on session state
                # and on the recorder. Nothing here reads an event to decide what
                # to do next — that would be model output steering control flow
                # (Art. I).
                pass
        finally:
            await runner.close()

    def _payload(self, session_service, session_id, output_key: str) -> dict | None:
        """The payload ADK wrote to session state under `output_key`, or `None`.

        Read from state rather than scraped from the final message, which works
        identically whether ADK used a native response schema or the AI Studio
        `set_model_response` fallback. ADK writes it via
        `model_dump(exclude_none=True)`, so `None` fields are absent rather than
        null — Pydantic's defaults restore them on `model_validate`.
        """
        try:
            session = asyncio.run(
                session_service.get_session(
                    app_name=self._app_name, user_id="pa", session_id=session_id
                )
            )
        except Exception as exc:  # re-raised classified, never swallowed (REQ-27)
            raise ExtractionOutputError(
                ExtractionFailure.NO_PAYLOAD,
                f"the session read for {output_key!r} failed: "
                f"{type(exc).__name__}: {exc}. Returning None here would "
                "report a transport fault as a model that produced nothing.",
            ) from exc
        if session is None:
            return None
        value = session.state.get(output_key)
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ExtractionOutputError(
                    ExtractionFailure.UNPARSEABLE,
                    f"session state {output_key!r} holds text that is not JSON: "
                    f"{exc}",
                ) from exc
        if not isinstance(value, dict):
            raise ExtractionOutputError(
                ExtractionFailure.SCHEMA_INVALID,
                f"session state {output_key!r} holds {type(value).__name__}, "
                "not an object",
            )
        return value
