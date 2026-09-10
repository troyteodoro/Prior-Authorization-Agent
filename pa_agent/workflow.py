"""T-18 — the fixed workflow graph (REQ-1, REQ-4, REQ-19, REQ-52; D62).

The criteria half of one request: structured facts in, a `Determination` out,
with exactly one model leaf in the middle. The sc1 and sc2 short circuits run
*before* this module is reached — `pa_agent.determination` owns them, and this
module is never called for a request they answer, which is how E2 and E3 reach an
answer having never constructed an extraction runner (A4).

**Article I is satisfied structurally, and `STEPS` is how you can check.** The
sequence is a module-level tuple of named callables. The driver walks it in order
and records the names it visited. No step's identity, order, or inclusion depends
on anything a model produced: the fan-out over notes is a `for` loop over what
`PatientStore.get_notes` returned, and the number of iterations is `len(notes)`.
The model is asked one question per note and its answer goes into a list.

Read the state object below and notice what extraction can reach: `events`,
`assertions`, `current_bmi`, `current_bmi_span`, `traces`. Nothing else. No step
predicate reads any of those, and `tests/test_workflow.py` asserts it on the AST
rather than trusting this paragraph.

**Deliberately not an ADK `Workflow`.** That class is real in 2.8.0 and would make
Article I literal in a satisfying way. It would also put `google.adk` on the
import path of every deterministic test in this repo, and the three `sys.modules`
assertions that would catch that are the ones that would have to be deleted to
allow it. Beyond that, `Workflow`'s value is routable edges, and this graph has
one conditional — whether a short circuit fired — which is a `return` and not an
edge. See D62 for the rejected alternative in full.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

from pa_agent.aggregate import assemble
from pa_agent.contracts import (
    CallMetrics,
    Condition,
    CriteriaTree,
    CriterionResult,
    CriterionVerdict,
    Determination,
    EvidenceSpan,
    Document,
    Observation,
    ProgramAssertion,
    RunTrace,
    WmEvent,
)
from pa_agent.criteria import (
    QualifyingRun,
    evaluate_c1,
    evaluate_c2,
    evaluate_c3,
    evaluate_c4,
    evaluate_c5,
    evaluate_criterion_a,
    evaluate_criterion_b,
    qualifying_run,
)
from pa_agent.reconcile import reconcile_bmi
from pa_agent.runners import ExtractionOutputError, ExtractionRunner
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyRef, PolicyStore

#: How many times a retryable extraction fault is attempted. A Python constant
#: read by the driver, never a decision the model participates in (REQ-46).
DEFAULT_MAX_ATTEMPTS = 3

#: Failure reasons an identical second call could plausibly answer differently.
#: The classification is the spike's, which learned it from the AI Studio free
#: tier returning 503 under load: without it the run measures the tier and not
#: the model. A schema violation is not here, and that is the point — the same
#: prompt will produce the same invalid response, so retrying spends money to
#: reach the same answer (REQ-18a, D8).
_RETRYABLE_FAILURES = ("CALL_FAILED",)


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


@dataclass
class WorkflowState:
    """Everything one request accumulates, in one object the driver threads.

    Split into what came from where on purpose. The two BMI readings T-33
    reconciles live on opposite halves of this object — `observations` from the
    patient plane's FHIR bundles, `current_bmi` from the note — and keeping them
    apart here is the same discipline that keeps them apart in the tools the model
    can see (REQ-53).
    """

    # Request
    patient_id: str
    procedure_code: str
    as_of: date
    policy_ref: PolicyRef
    tree: CriteriaTree

    # Structured facts (patient plane, no model)
    observations: list[Observation] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    value_set: frozenset[str] = frozenset()

    # Notes and what the model read out of them
    notes: list[Document] = field(default_factory=list)
    events: list[WmEvent] = field(default_factory=list)
    assertions: list[ProgramAssertion] = field(default_factory=list)
    current_bmi: float | None = None
    current_bmi_span: EvidenceSpan | None = None

    # Deterministic products
    run: QualifyingRun | None = None
    results: list[CriterionResult] = field(default_factory=list)

    # Instrumentation (Art. X)
    traces: list[RunTrace] = field(default_factory=list)
    steps_visited: list[str] = field(default_factory=list)

    @property
    def metrics(self) -> list[CallMetrics]:
        return [m for trace in self.traces for m in trace.metrics]

    @property
    def tool_calls(self) -> list:
        return [c for trace in self.traces for c in trace.tool_calls]


@dataclass
class WorkflowRun:
    """The determination plus how it was reached.

    Two objects rather than one widened `Determination`: the determination is
    what a human reviews, and the trace is what an engineer reads. Article X
    wants the second recorded; it does not want the first to grow a tool log
    (D62).
    """

    determination: Determination
    state: WorkflowState

    @property
    def steps(self) -> list[str]:
        return list(self.state.steps_visited)

    @property
    def traces(self) -> list[RunTrace]:
        return list(self.state.traces)

    @property
    def model_calls(self) -> int:
        return self.determination.model_calls


# --------------------------------------------------------------------------
# The steps. Each takes the state and mutates it. None of them branches on
# anything a model produced.
# --------------------------------------------------------------------------


def step_load_structured_facts(state: WorkflowState, ctx: _Context) -> None:
    """REQ-41: the patient plane, through the port and nowhere else.

    Reports everything and filters nothing — an active-only or BMI-only read
    belongs to the predicates, and D39 refused it inside the adapter for the same
    reason it is refused here: a filter applied twice is a filter that can
    disagree with itself.
    """
    state.observations = ctx.patient_store.get_observations(state.patient_id)
    state.conditions = ctx.patient_store.get_conditions(state.patient_id)


def step_load_value_set(state: WorkflowState, ctx: _Context) -> None:
    """T-46: criterion (b)'s value set, by the id the tree's constant names.

    The id comes from the policy, so no caller writes a code-system literal, and
    the set arrives through the *policy* port because a value set is a compiled
    fragment of the policy and travels with it (D52).
    """
    value_set_id = state.tree.criterion("b").require("value_set_id")
    state.value_set = ctx.policy_store.get_value_set(value_set_id)


def step_load_notes(state: WorkflowState, ctx: _Context) -> None:
    """The note corpus for this patient, hash-verified by the adapter (REQ-7).

    This is where the fan-out width is decided, and it is decided here — in
    Python, from the store — rather than by the model choosing what to read.
    """
    state.notes = ctx.patient_store.get_notes(state.patient_id)


def step_extract(state: WorkflowState, ctx: _Context) -> None:
    """The one model leaf. One call per note, fanned out and fanned back in.

    `for note in state.notes` is the fan-out: N notes, N extractions, in the
    order the store returned them. The loop bound is `len(state.notes)` and
    nothing the model says can change it — no step here asks whether to read
    another note, and there is no mechanism by which it could.

    Fan-in is `extend`. Events from every note land in one list because c1
    through c5 adjudicate the patient's history, not a document's; each event
    keeps the span that cites the note it came from, so merging loses nothing an
    auditor needs (Art. III).
    """
    for note in state.notes:
        result, trace = _extract_one(note, ctx)
        state.events.extend(result.events)
        state.assertions.extend(result.assertions)
        # T-60: the note's current BMI belongs to no encounter. First one wins,
        # and with one note per patient in this corpus that is not yet a real
        # choice — recorded as a known narrowing rather than an invisible one.
        if state.current_bmi is None and result.current_bmi is not None:
            state.current_bmi = result.current_bmi
            state.current_bmi_span = result.current_bmi_span
        state.traces.append(trace)

    state.events.sort(key=lambda e: e.event_date)


def step_criterion_a(state: WorkflowState, ctx: _Context) -> None:
    """REQ-11: a numeric comparison. No model call, before or after (Art. II)."""
    state.results.append(
        evaluate_criterion_a(
            state.tree.criterion("a"), state.observations, state.as_of
        )
    )


def step_reconcile(state: WorkflowState, ctx: _Context) -> None:
    """REQ-34: criterion (a) re-read against the note. May downgrade, never up.

    Runs here rather than inside criterion (a) because the note BMI does not
    exist until extraction has run, and US-2 makes no model call (D11). Every
    comparison is Python: the model supplied a number and a quote, and which side
    of 35.0 that number falls on is not its judgment.
    """
    fact = state.tree.reconciled_fact("bmi")
    criterion_a = state.tree.criterion("a")
    index = next(
        (i for i, r in enumerate(state.results) if r.criterion_id == criterion_a.id),
        None,
    )
    if index is None:
        return
    state.results[index] = reconcile_bmi(
        fact,
        criterion_a,
        state.results[index],
        state.observations,
        state.events,
        state.current_bmi,
        state.current_bmi_span,
    )


def step_criterion_b(state: WorkflowState, ctx: _Context) -> None:
    """REQ-12: set intersection. `MET` or abstention, never `NOT_MET` — a chart
    cannot prove a comorbidity absent (D40)."""
    state.results.append(
        evaluate_criterion_b(
            state.tree.criterion("b"), state.conditions, state.value_set
        )
    )


def step_qualifying_run(state: WorkflowState, ctx: _Context) -> None:
    """c3's run, computed once and shared (D48).

    c2, c4 and c5 declare `scoped_to: "c3"` in the tree and all three read this
    object. Computing it once is not an optimisation: four independent
    computations of "the qualifying run" are four things free to disagree, and a
    disagreement would surface as a verdict about a period no other criterion
    adjudicated.
    """
    state.run = qualifying_run(state.events)


def step_criteria_c(state: WorkflowState, ctx: _Context) -> None:
    """c1 through c5 (REQ-13, REQ-36, REQ-32, REQ-14, REQ-40, REQ-37, REQ-15).

    `c3_met` gates c2, c4 and c5 per REQ-15: with no qualifying period there is
    nothing for them to be scoped to, so they abstain rather than answering
    `NOT_MET` about a run that does not exist.
    """
    assert state.run is not None, "step_qualifying_run must precede this step"
    run = state.run

    c1 = evaluate_c1(state.tree.criterion("c1"), state.events, state.assertions)
    c3 = evaluate_c3(
        state.tree.criterion("c3"), state.events, state.assertions, run
    )
    c3_met = c3.verdict is CriterionVerdict.MET

    c2 = evaluate_c2(state.tree.criterion("c2"), run, state.as_of, c3_met)
    c4 = evaluate_c4(state.tree.criterion("c4"), run, c3_met)
    c5 = evaluate_c5(state.tree.criterion("c5"), run, c3_met)

    # Appended in the tree's own criterion order, so the gap list reads the way
    # the policy reads rather than the way this function happened to compute.
    state.results.extend([c1, c2, c3, c4, c5])
    state.results.sort(key=lambda r: _criterion_order(state.tree, r.criterion_id))


#: The graph. Order is the contract; the driver walks it and records what it
#: visited, and `tests/test_workflow.py` asserts the visited list against this
#: tuple rather than against a comment.
STEPS: tuple[tuple[str, object], ...] = (
    ("load_structured_facts", step_load_structured_facts),
    ("load_value_set", step_load_value_set),
    ("load_notes", step_load_notes),
    ("extract", step_extract),
    ("criterion_a", step_criterion_a),
    ("reconcile", step_reconcile),
    ("criterion_b", step_criterion_b),
    ("qualifying_run", step_qualifying_run),
    ("criteria_c", step_criteria_c),
)

STEP_NAMES: tuple[str, ...] = tuple(name for name, _ in STEPS)


# --------------------------------------------------------------------------
# The driver
# --------------------------------------------------------------------------


@dataclass
class _Context:
    """The ports and budgets a step may reach. Assembled once by the caller.

    Both stores appear here and that is the one place in the system they meet.
    They meet as two separate handles that two different steps use — the patient
    steps never touch `policy_store` and `step_load_value_set` never touches
    `patient_store` — which is REQ-41's closing sentence read literally: a module
    able to read both planes would have to hold both handles, and the graph is
    the module that does, because the graph is what a request *is*.
    """

    patient_store: PatientStore
    policy_store: PolicyStore
    runner: ExtractionRunner
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


def _criterion_order(tree: CriteriaTree, criterion_id: str) -> int:
    for index, criterion in enumerate(tree.criteria):
        if criterion.id == criterion_id:
            return index
    return len(tree.criteria)


def _extract_one(note: Document, ctx: _Context) -> tuple[object, RunTrace]:
    """One note, up to `max_attempts` times, with the attempts recorded.

    Retry is a Python budget (REQ-46). The classifier decides whether a second
    attempt could plausibly differ; the model is never asked. Exhaustion raises,
    because the alternative — returning what we have — is a partial extraction
    presented as a complete one, and a partial extraction is indistinguishable
    from a patient with a shorter history.
    """
    started = time.perf_counter()
    last: ExtractionOutputError | None = None

    for attempt in range(1, ctx.max_attempts + 1):
        try:
            result = ctx.runner.run(note.document_id, note.text)
        except ExtractionOutputError as exc:
            last = exc
            if exc.reason.value not in _RETRYABLE_FAILURES:
                break
            if attempt == ctx.max_attempts:
                break
            continue

        inner = getattr(result, "trace", None)
        # **Every** model turn, not just the first. A tool-calling run costs two
        # LLM calls per note — one to request the tool, one to answer — and
        # `result.metrics` carries a single `CallMetrics` because that is what
        # `build_result` takes. Reading only that halves the reported cost of the
        # `tool_fetch` path, and Article X says measured rather than estimated
        # (A6). A runner with no trace, like the replay, falls back to its one.
        measured = (
            list(inner.metrics)
            if inner is not None and inner.metrics
            else ([result.metrics] if result.metrics else [])
        )
        trace = RunTrace(
            runner_name=getattr(ctx.runner, "name", type(ctx.runner).__name__),
            model=(measured[0].model if measured else None),
            document_id=note.document_id,
            steps=["extract"],
            tool_calls=list(inner.tool_calls) if inner is not None else [],
            attempts=attempt,
            termination_reason="ok",
            metrics=measured,
        )
        return result, trace

    assert last is not None
    elapsed = (time.perf_counter() - started) * 1000.0
    raise ExtractionOutputError(
        last.reason,
        f"{note.document_id}: {last.message} (after {ctx.max_attempts} "
        f"attempt(s), {elapsed:.0f}ms). A partial extraction presented as a "
        "complete one is indistinguishable from a shorter patient history, so "
        "this raises rather than returning what it has.",
    )


def run_criteria_workflow(
    policy_store: PolicyStore,
    patient_store: PatientStore,
    extraction_runner: ExtractionRunner,
    policy_ref: PolicyRef,
    patient_id: str,
    as_of: date,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> WorkflowRun:
    """Walk `STEPS` in order and assemble the determination (T-18, T-19).

    Called only for a code that resolved to the covered or the
    contractor-determined set — `pa_agent.determination` answers the other two
    outcomes before this function exists in the call stack, which is why a
    short-circuit request can be given a runner that raises and still get an
    answer.
    """
    tree = policy_store.get_tree(policy_ref.policy_version_id)
    state = WorkflowState(
        patient_id=patient_id,
        procedure_code=policy_ref.procedure_code,
        as_of=as_of,
        policy_ref=policy_ref,
        tree=tree,
    )
    ctx = _Context(
        patient_store=patient_store,
        policy_store=policy_store,
        runner=extraction_runner,
        max_attempts=max_attempts,
    )

    for name, step in STEPS:
        step(state, ctx)
        state.steps_visited.append(name)

    determination = assemble(
        patient_id=patient_id,
        procedure_code=policy_ref.procedure_code,
        policy_version_id=policy_ref.policy_version_id,
        decision_expression=tree.decision_expression,
        results=state.results,
        metrics=state.metrics,
    )
    return WorkflowRun(determination=determination, state=state)
