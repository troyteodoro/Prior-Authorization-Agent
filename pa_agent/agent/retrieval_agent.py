"""T-61 — model-directed retrieval, bounded (REQ-43, 45, 46, 48, 49, 51, 54; D63, D66).

The model chooses which tools to call, in what order, how many times, and when it
has gathered enough. Then the evidence it gathered goes through the **existing**
deterministic criteria, unchanged. It decides what to read; it decides nothing
about what the reading means.

That division is Amendment 1's, not a preference. The amendment reserves date
arithmetic, numeric comparisons, counting, sorting and set membership to Python on
both paths — which is the entire decision procedure for all seven criteria — so
there is no verdict the model could reach here without doing something the
amendment reserves (D63).

### What is actually being measured

Whether model-directed retrieval finds what fixed retrieval finds. The failure
modes are real and none of them looks like a failure:

- a note it never opened leaves c3 measuring a shorter run;
- observations it forgot make criterion (a) abstain;
- the same document fetched four times costs four times as much for one answer;
- a run that stops early produces a determination over a partial chart.

Every one of those yields a well-formed `Determination`. That is why the
differential exists rather than a pass/fail assertion.

### The bounds are Python's, and each prevents a specific failure

- `max_steps` — the tool-call budget. Without it, a loop that keeps asking for
  one more document is unbounded spend on a single request.
- `max_llm_calls` — ADK's own ceiling, which counts LLM calls rather than tool
  invocations, so one tool round trip costs two.
- `timeout_s` — wall clock. A model that neither answers nor calls a tool would
  otherwise hang the run rather than fail it.
- `max_attempts` — retries, on a transport fault only, classified in Python.
- `MAX_ROWS` — what a single tool response may contain. **T-65 added this one**,
  and D64 is why: `get_patient_observations` returned one patient's 3,780 rows,
  `include_contents="default"` re-sent them every turn, and that alone was 446x
  the deterministic path's input tokens for an identical answer. The other four
  bound the *shape* of the run; this one bounds the size of a single answer, which
  is the term that scales with the patient's chart rather than with the question.

Exceeding any of them **terminates with a named reason and raises**. It never
returns what it happened to have gathered: a partial bundle presented as a
complete one is indistinguishable from a patient with a thinner chart, which is
the failure this module exists to make visible rather than commit.

### What the model cannot do

It sees a tool allowlist and has no way to name anything outside it (REQ-43,
REQ-45). The policy tools are read-only — no setter, no write, no path to the
tree's file (Art. VII). The transfer flags are set with no `sub_agents`, so the
flow is `SingleFlow` and `transfer_to_agent` is never injected (Art. I).

`include_contents` is left at `"default"` here, unlike the extraction agent.
This one genuinely needs its own turn history to know what it has already
fetched; an extractor reading one note needs none, and D17 found that a shared
session carries note N-1 into note N. Different jobs, different answer, stated
because the asymmetry looks like an oversight otherwise.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, Field

from pa_agent.contracts import Document, RunTrace
from pa_agent.contracts import CriteriaTree
from pa_agent.model_pin import PINNED_MODEL
from pa_agent.retrieval import RetrievalError, RetrievalResult
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyStore

from .extraction_agent import build_trace_recorder
from .patient_tools import build_patient_tools
from .policy_tools import build_policy_tools

AGENT_NAME = "evidence_gatherer"
OUTPUT_KEY = "retrieval"
PROMPT_VERSION = "t61-retrieval-v2"

#: The patient-plane tools this agent may call (REQ-43). All four — where the
#: extraction agent gets none of them, only a reader scoped in Python to the one
#: note under review (T-66). A gatherer that cannot fetch the structured facts
#: cannot assemble the bundle criteria (a) and (b) are adjudicated on.
#:
#: REQ-53's reason for denying the extractor the structured reads does not apply
#: here (D62): this agent never reports a BMI, it reports which documents to read.
#: It cannot collapse T-33's two independent readings because it produces neither.
PATIENT_ALLOWLIST = (
    "get_patient_notes",
    "get_patient_document",
    "get_patient_observations",
    "get_patient_conditions",
)

#: The policy-plane tools. Read-only: the model may interpret the selected policy
#: and cannot rewrite it (Art. VII). **This is the first model consumer they have
#: had** — D62 declared and tested them with none, for T-61 to pick up.
POLICY_ALLOWLIST = ("get_policy_context", "get_policy_value_set")

DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_LLM_CALLS = 16
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_ATTEMPTS = 2

INSTRUCTION = """\
You gather the evidence a prior authorization review will be adjudicated on. You
do not decide anything about coverage and you do not interpret what you read.
Something else applies the rules; your job is to make sure it has what it needs.

Work for one patient, whose id you are given.

Use the tools to collect all of:

  - every clinical note on file, read in full
  - the patient's recorded observations
  - the patient's coded conditions

Read every note the patient has. Do not choose among them by date, by apparent
relevance, or by which one looks like it answers the question — a note you skip
is evidence the reviewer never sees, and the rules are applied to what you
return and to nothing else.

Do not fetch the same document twice. You already have what you read.

The observation and condition lists are capped. Each response states the true
total and whether it was truncated; a truncated response is expected on a large
chart and is not a failure to retrieve. You are confirming those records exist,
not reading them — the rules are applied to the full set, which is read
separately.

When you have all three, stop and return:

  document_ids   every document_id you read in full, in the order you read them
  gathered       true when you retrieved notes, observations and conditions
  note           one sentence on anything you could not retrieve, or empty

Return only what the schema defines. Do not summarize the notes, do not quote
them, and do not state any clinical finding. The text you read is passed on whole.
"""


class RetrievalPlan(BaseModel):
    """What the model reports having gathered.

    Deliberately thin: **ids and a flag, never content.** The documents themselves
    come back from the store, hash-verified, not from the model's message. A model
    that could hand back note text could hand back text that was never in the
    note, and every span downstream would anchor against a fabrication (Art. III).
    """

    document_ids: list[str] = Field(
        default_factory=list,
        description="Every document_id read in full, in the order read.",
    )
    gathered: bool = Field(
        default=False,
        description="True when notes, observations and conditions were all retrieved.",
    )
    note: str = Field(
        default="", description="One sentence on anything not retrievable, or empty."
    )


def build_retrieval_agent(
    patient_store: PatientStore,
    policy_store: PolicyStore,
    model: Any = PINNED_MODEL,
):
    """One `LlmAgent` for evidence gathering, with both planes' toolsets.

    This is the one agent in the system that legitimately holds both plane
    handles, and it is worth saying why that does not breach Article VI. The
    article forbids the *policy plane* reading patient data and the patient plane
    holding a policy corpus. This agent holds two toolsets that each reach one
    plane; neither tool can see the other's store, because each closes over its
    own. `patient_tools` and `policy_tools` remain separate modules and neither
    imports the other (REQ-53).

    Returns the agent and the two toolsets, because the toolsets carry the
    port-side call log — what the *store* saw, next to what the model's plugin
    saw. Two independent records of the same calls is how a discrepancy between
    them becomes visible.
    """
    from google.adk.agents.llm_agent import LlmAgent
    from google.genai import types

    patient = build_patient_tools(patient_store)
    policy = build_policy_tools(policy_store)
    tools = patient.allowlist(*PATIENT_ALLOWLIST) + policy.allowlist(*POLICY_ALLOWLIST)

    agent = LlmAgent(
        name=AGENT_NAME,
        model=model,
        description="Gathers the evidence a prior authorization review is decided on.",
        instruction=INSTRUCTION,
        tools=tools,
        output_schema=RetrievalPlan,
        output_key=OUTPUT_KEY,
        # Left at "default": this agent needs its own turn history to know what it
        # has already fetched. The extraction agent sets "none" for the opposite
        # reason — one note, no context to steer with (D17).
        include_contents="default",
        # No sub_agents + both flags ⇒ SingleFlow, so transfer_to_agent is never
        # injected and the model has no mechanism to hand control anywhere (Art. I).
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )
    return agent, patient, policy


class AgenticRetrievalPlanner:
    """`RetrievalPlanner` where the model decides what to fetch (REQ-43, D63).

    Satisfies the same port `FixedRetrievalPlanner` does, so everything downstream
    is identical and the differential has exactly one variable.
    """

    name = "agentic"

    def __init__(
        self,
        client: Any = None,
        model: str = PINNED_MODEL,
        llm: Any = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        app_name: str = "pa_agent",
    ) -> None:
        self._client = client
        self._model_name = model
        self._llm = llm
        self.max_steps = max_steps
        self.max_llm_calls = max_llm_calls
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self._app_name = app_name
        self._counter = 0

    @property
    def model(self) -> str:
        return self._model_name

    def _model_argument(self) -> Any:
        """A `BaseLlm` when one is available, else the pinned name.

        `Gemini` takes a pre-built `genai.Client` and builds its own lazily, so the
        injected-client property survives here: nothing in this module reads a
        credential or names a tier, and the caller chooses (D5).
        """
        if self._llm is not None:
            return self._llm
        if self._client is not None:
            from google.adk.models.google_llm import Gemini

            return Gemini(model=self._model_name, client=self._client)
        return self._model_name

    def gather(
        self,
        patient_id: str,
        tree: CriteriaTree,
        patient_store: PatientStore,
        policy_store: PolicyStore,
    ) -> RetrievalResult:
        plan, patient_tools, policy_tools, trace = self._run(
            patient_id, patient_store, policy_store
        )

        # ------------------------------------------------------------------
        # Python assembles the bundle. The model named ids; the store supplies
        # the content, hash-verified (REQ-7, REQ-51).
        # ------------------------------------------------------------------
        available = {note.document_id: note for note in patient_store.get_notes(patient_id)}
        notes: list[Document] = []
        seen: set[str] = set()
        for document_id in plan.document_ids:
            if document_id in seen:
                # A repeat is a cost finding, not a second copy of the evidence.
                # Counting it twice would give the patient a longer history than
                # the chart has.
                continue
            seen.add(document_id)
            note = available.get(document_id)
            if note is None:
                # REQ-45: the model may not invent evidence. An id no store serves
                # is a fabricated citation one layer up from a fabricated quote,
                # and it fails here on a dict lookup rather than on judgment.
                raise RetrievalError(
                    f"the planner reported reading {document_id!r}, which is not a "
                    f"document this patient has. Available: {sorted(available)}. "
                    "A model may not invent evidence (REQ-45)."
                )
            notes.append(note)

        if not notes:
            # Never an empty bundle. "The model did not look" and "the chart does
            # not say" are different answers, and c1 abstaining with
            # NO_EVIDENCE_RETRIEVED would make them read identically (D31, D39).
            raise RetrievalError(
                f"the planner gathered no readable note for {patient_id} "
                f"(gathered={plan.gathered}, note={plan.note!r}). Returning an "
                "empty bundle would make every patient look like a chart with no "
                "weight-management history, and the determination would be "
                "well-formed."
            )

        value_set_id = tree.criterion("b").require("value_set_id")
        return RetrievalResult(
            observations=patient_store.get_observations(patient_id),
            conditions=patient_store.get_conditions(patient_id),
            value_set=policy_store.get_value_set(value_set_id),
            notes=notes,
            trace=trace,
        )

    # ----------------------------------------------------------------------

    def _run(self, patient_id, patient_store, policy_store):
        from google.adk.agents.run_config import RunConfig
        from google.adk.apps import App
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        recorder = build_trace_recorder()
        agent, patient_tools, policy_tools = build_retrieval_agent(
            patient_store, policy_store, model=self._model_argument()
        )

        self._counter += 1
        session_id = f"gather-{patient_id}-{self._counter}"
        session_service = InMemorySessionService()
        runner = Runner(
            app=App(name=self._app_name, root_agent=agent, plugins=[recorder]),
            session_service=session_service,
        )

        started = time.perf_counter()
        termination = "ok"
        error: str | None = None
        try:
            asyncio.run(
                self._drive(
                    runner, session_service, session_id, patient_id, RunConfig, types
                )
            )
        except Exception as exc:
            termination = type(exc).__name__
            error = f"{type(exc).__name__}: {exc}"

        elapsed = (time.perf_counter() - started) * 1000.0
        tool_calls = list(recorder.tool_calls)

        # Bounds are checked in Python, after the fact as well as during: ADK's
        # max_llm_calls caps model turns, and this caps tool calls, which is the
        # thing that actually costs a store round trip (REQ-46).
        if len(tool_calls) > self.max_steps:
            termination = "step_budget_exceeded"
            error = (
                f"{len(tool_calls)} tool calls against a budget of "
                f"{self.max_steps}"
            )
        elif elapsed > self.timeout_s * 1000.0:
            termination = "timeout"
            error = f"{elapsed / 1000.0:.1f}s against a budget of {self.timeout_s}s"

        trace = RunTrace(
            runner_name=self.name,
            model=recorder.model_name or self._model_name,
            prompt_version=PROMPT_VERSION,
            document_id=None,
            steps=[call.name for call in tool_calls],
            tool_calls=tool_calls,
            attempts=1,
            termination_reason=f"{termination} ({elapsed:.0f}ms)",
            metrics=list(recorder.metrics),
        )

        if error is not None:
            raise RetrievalError(
                f"{patient_id}: {error}. Tool calls made: "
                f"{[c.name for c in tool_calls]}. Terminating rather than "
                "adjudicating on a partial chart — a partial bundle presented as "
                "a complete one is indistinguishable from a thinner history."
            )

        plan = self._plan(session_service, session_id)
        if plan is None:
            raise RetrievalError(
                f"{patient_id}: the run produced no structured plan at session "
                f"state {OUTPUT_KEY!r} after {len(tool_calls)} tool call(s)."
            )
        return plan, patient_tools, policy_tools, trace

    async def _drive(
        self, runner, session_service, session_id, patient_id, RunConfig, types
    ) -> None:
        await session_service.create_session(
            app_name=self._app_name, user_id="pa", session_id=session_id
        )
        message = types.Content(
            role="user",
            parts=[types.Part(text=f"Gather the evidence for patient_id: {patient_id}")],
        )
        try:
            await asyncio.wait_for(
                self._consume(runner, session_id, message, RunConfig),
                timeout=self.timeout_s,
            )
        finally:
            await runner.close()

    async def _consume(self, runner, session_id, message, RunConfig) -> None:
        async for _event in runner.run_async(
            user_id="pa",
            session_id=session_id,
            new_message=message,
            run_config=RunConfig(max_llm_calls=self.max_llm_calls),
        ):
            # Consumed for their effect on session state and the recorder. Nothing
            # here reads an event to decide what to do next — that would be model
            # output steering control flow (Art. I).
            pass

    def _plan(self, session_service, session_id) -> RetrievalPlan | None:
        """The validated plan ADK wrote to session state, or `None`.

        Read from state rather than scraped from the final message, which works
        identically whether ADK used a native response schema or the AI Studio
        `set_model_response` fallback (D62).
        """
        try:
            session = asyncio.run(
                session_service.get_session(
                    app_name=self._app_name, user_id="pa", session_id=session_id
                )
            )
        except Exception:
            return None
        if session is None:
            return None
        value = session.state.get(OUTPUT_KEY)
        if value is None:
            return None
        try:
            return RetrievalPlan.model_validate(value)
        except Exception as exc:
            # REQ-48: schema-invalid model output is a fault with a name, never a
            # verdict and never an empty bundle.
            raise RetrievalError(
                f"the plan at session state {OUTPUT_KEY!r} does not validate: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
