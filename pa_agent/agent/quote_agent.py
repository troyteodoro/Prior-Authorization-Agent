"""T-98 — the ADK quote runner: the review's model turn, as a real ADK agent (D122).

`AdkQuoteRunner` satisfies `pa_agent.quotes.QuoteRunner` and returns through
`build_quote_result()` like the direct runner does. That function is the trust
boundary: ADK output is untrusted model output, and nothing here constructs a
span directly.

The shape is `AdkExtractionRunner`'s, deliberately: the same `_build_agent`
(`include_contents="none"`, `SingleFlow`, a literal tool list, temperature 0),
the same `_note_tools` (nothing inline; `read_note` scoped to the one document
under tool-fetch), the same invocation methods through `_AdkRuns`, and T-89's
re-ask as a second self-contained invocation. What differs is what is asked —
`QUOTE_INSTRUCTION` and the condition list — and the conditions ride in the
**message**, as the re-ask's failed quotes do, so the agent's instruction is
static per mode and the direct and ADK turns differ only by how the note
reaches the model.

The tier changes the prompt under tool-fetch (D62): on AI Studio ADK injects a
`SetModelResponseTool` when `output_schema` and a tool are both present. The
measurement stamps `native_schema_enabled` off ADK, never off a flag (D106).
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Any

from pa_agent.contracts import MedicationEffectRow
from pa_agent.extraction import REASK_INSTRUCTION, Turn, VerbatimAnswers, extract_with_reask, format_targets
from pa_agent.model_pin import PINNED_MODEL
from pa_agent.quotes import (
    QUOTE_INSTRUCTION,
    QUOTE_PROMPT_VERSION,
    QuoteAnswers,
    QuoteFailure,
    QuoteOutputError,
    QuoteResult,
    _locate_quote,
    build_quote_result,
    format_effects,
)
from pa_agent.stores.patient import PatientStore

from .extraction_agent import (
    DEFAULT_MAX_LLM_CALLS,
    _READ_FIRST,
    _AdkRuns,
    _build_agent,
    _note_tools,
)

#: Valid Python identifiers — ADK's `BaseNode` validates `str.isidentifier()`.
QUOTE_AGENT_NAME = "effect_quote_finder"
QUOTE_OUTPUT_KEY = "quotes"
QUOTE_REASK_AGENT_NAME = "effect_quote_verbatim_reask"
QUOTE_REASK_OUTPUT_KEY = "verbatim"

#: The one tool the quote agent may reach under tool-fetch: the extractor's,
#: scoped in Python to the single document under review (T-66, D66). Its own
#: constant so the leaf names what it may reach; a test pins it to the
#: extractor's, because both leaves read one note and nothing else.
QUOTE_ALLOWLIST = ("read_note",)


def _quote_instruction(tool_fetch: bool) -> str:
    """`QUOTE_INSTRUCTION` verbatim, plus the retrieval preamble only when the
    note arrives through `read_note`."""
    if not tool_fetch:
        return QUOTE_INSTRUCTION
    return _READ_FIRST.format(verb="answer") + QUOTE_INSTRUCTION


def _quote_reask_instruction(tool_fetch: bool) -> str:
    if not tool_fetch:
        return REASK_INSTRUCTION
    return _READ_FIRST.format(verb="answer") + REASK_INSTRUCTION


def build_quote_agent(
    patient_store: PatientStore | None = None,
    tool_fetch: bool = False,
    model: Any = PINNED_MODEL,
    document_id: str | None = None,
):
    """One `LlmAgent` that finds passages documenting listed conditions.
    Constructed, not run, so a test can inspect it without a session."""
    return _build_agent(
        name=QUOTE_AGENT_NAME,
        description="Finds the passages of a clinical note that document listed conditions.",
        instruction=_quote_instruction(tool_fetch),
        tools=_note_tools(patient_store, tool_fetch, document_id, QUOTE_ALLOWLIST),
        output_schema=QuoteAnswers,
        output_key=QUOTE_OUTPUT_KEY,
        model=model,
    )


def build_quote_reask_agent(
    patient_store: PatientStore | None = None,
    tool_fetch: bool = False,
    model: Any = PINNED_MODEL,
    document_id: str | None = None,
):
    """The verbatim re-ask agent for the quote turn (REQ-56, D103): the
    finder's flags, tools and temperature exactly, with the re-ask instruction
    and `VerbatimAnswers` as the schema."""
    return _build_agent(
        name=QUOTE_REASK_AGENT_NAME,
        description="Returns the verbatim note text for quotes that could not be located.",
        instruction=_quote_reask_instruction(tool_fetch),
        tools=_note_tools(patient_store, tool_fetch, document_id, QUOTE_ALLOWLIST),
        output_schema=VerbatimAnswers,
        output_key=QUOTE_REASK_OUTPUT_KEY,
        model=model,
    )


class AdkQuoteRunner(_AdkRuns):
    """`QuoteRunner` over `google-adk` 2.8.0 (T-98, D122).

    One fresh session per note. Raises `QuoteOutputError` on no payload,
    unparseable output, or a payload the schema rejects — never an empty
    result, because a note that documents none of the conditions is the answer
    red is built on, and a transport error must not read as that answer.
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

    def build_agent(self, document_id: str | None = None):
        return build_quote_agent(
            patient_store=self._patient_store,
            tool_fetch=self._tool_fetch,
            model=self._model_argument(),
            document_id=document_id,
        )

    def build_reask_agent(self, document_id: str | None = None):
        return build_quote_reask_agent(
            patient_store=self._patient_store,
            tool_fetch=self._tool_fetch,
            model=self._model_argument(),
            document_id=document_id,
        )

    def run(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow]
    ) -> QuoteResult:
        # One session id per note, advanced once per `run()`; the re-ask
        # derives its own from it, so no two invocations share one (D17).
        self._counter += 1
        session = f"{document_id.replace('/', '_')}-quotes-{self._counter}"
        return extract_with_reask(
            document_id,
            text,
            first_turn=lambda: self._first_turn(document_id, text, rows, session),
            reask_turn=lambda targets: self._reask_turn(document_id, text, targets, session),
            runner_name=self.name,
            model=self._model_name,
            step_names=("adk_quotes", "adk_quotes_reask"),
            build=partial(build_quote_result, rows=rows),
            locate=_locate_quote,
            prompt_version=QUOTE_PROMPT_VERSION,
        )

    def _first_turn(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow], session: str
    ) -> Turn:
        """The quote invocation. Raises classified on any failure (see the
        class docstring). The conditions ride in the message in both modes,
        so the instruction is static per mode."""
        if self._tool_fetch:
            message = (
                f"Find the documented conditions in document_id: {document_id}\n\n"
                f"{format_effects(rows)}"
            )
        else:
            message = (
                f"Clinical note (document_id: {document_id}):\n\n{text}\n\n"
                f"{format_effects(rows)}"
            )
        payload, metrics, tool_calls, termination, error = self._invoke(
            self.build_agent(document_id), message, QUOTE_OUTPUT_KEY, session, "quotes"
        )
        if error is not None:
            # `_AdkRuns` classifies in the extraction vocabulary; re-raised
            # here member for member under this port's enum.
            raise QuoteOutputError(QuoteFailure[error.reason.name], error.message) from error
        if payload is None:
            raise QuoteOutputError(
                QuoteFailure.NO_PAYLOAD,
                f"{document_id}: the run produced no structured output at "
                f"session state {QUOTE_OUTPUT_KEY!r}. 'Nothing found' is a "
                "payload with an empty list, and a missing payload is not it.",
            )
        try:
            QuoteAnswers.model_validate(payload)
        except Exception as exc:
            raise QuoteOutputError(
                QuoteFailure.SCHEMA_INVALID, f"{document_id}: {type(exc).__name__}: {exc}"
            ) from exc
        return Turn(payload=payload, metrics=metrics, tool_calls=tool_calls, termination=termination)

    def _reask_turn(
        self, document_id: str, text: str, targets: list[dict], session: str
    ) -> Turn:
        """T-89's re-ask invocation, never raising (D103): a second
        self-contained invocation, because `include_contents="none"` keeps the
        first turn out of the second as it keeps note N-1 out of note N."""
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
            self.build_reask_agent(document_id), message, QUOTE_REASK_OUTPUT_KEY,
            f"{session}-reask", "quotes_reask",
        )
        return Turn(
            payload=payload,
            metrics=metrics,
            tool_calls=tool_calls,
            termination=termination,
            error=f"{error.reason.value}: {error.message}" if error is not None else None,
        )
