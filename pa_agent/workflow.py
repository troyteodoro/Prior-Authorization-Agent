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
    DeterminationAborted,
    ErrorCode,
    EvidenceSpan,
    GapReason,
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
from pa_agent.index import DocumentIndex
from pa_agent.reconcile import reconcile_bmi
from pa_agent.retrieval import (
    FixedRetrievalPlanner,
    RetrievalPlanner,
)
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    ExtractionRunner,
)
from pa_agent.spans import SpanValidationError
from pa_agent.spans import validate as validate_span
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyRef, PolicyStore
from pa_agent.verifier import (
    VerifierAnswer,
    VerifierFailure,
    VerifierOutputError,
    VerifierRunner,
    build_claim_payload,
)

#: How many times a retryable extraction fault is attempted. A Python constant
#: read by the driver, never a decision the model participates in (REQ-46).
DEFAULT_MAX_ATTEMPTS = 3

#: T-29 (D75): what a runner's failure means for a criterion. `runners.py`
#: refused to host this in writing — it knows the response was malformed; only
#: this module knows which criteria were waiting on it. The replay faults
#: (`DOCUMENT_CHANGED`, `NOT_RECORDED`) are terminal by D8's own test: an
#: identical second replay of the same recording cannot answer differently.
ERROR_CODE_FOR: dict[ExtractionFailure, ErrorCode] = {
    ExtractionFailure.CALL_FAILED: ErrorCode.MODEL_CALL_FAILED,
    ExtractionFailure.NO_PAYLOAD: ErrorCode.SCHEMA_INVALID,
    ExtractionFailure.UNPARSEABLE: ErrorCode.SCHEMA_INVALID,
    ExtractionFailure.SCHEMA_INVALID: ErrorCode.SCHEMA_INVALID,
    ExtractionFailure.DOCUMENT_CHANGED: ErrorCode.SCHEMA_INVALID,
    ExtractionFailure.NOT_RECORDED: ErrorCode.SCHEMA_INVALID,
}

#: Failure reasons an identical second call could plausibly answer differently.
#: The classification learned it from the AI Studio free tier returning 503
#: under load: without it the run measures the tier and not the model. A schema
#: violation is not here, and that is the point — the same prompt will produce
#: the same invalid response, so retrying spends money to reach the same answer
#: (REQ-18a, D8). **Derived from the mapping above**, so retryability has one
#: source of truth: REQ-30's classification on the mapped `ErrorCode`.
_RETRYABLE_FAILURES = tuple(
    failure.value for failure, code in ERROR_CODE_FOR.items() if code.retryable
)

#: The criteria that consume extraction — what `step_qualifying_run` and
#: `step_criteria_c` evaluate over `state.events`. On an extraction failure
#: these resolve to `ERROR`; (a) and (b) read structured FHIR and never touched
#: the model, so a fault they never saw is not theirs to report (D75).
EXTRACTION_CRITERIA = ("c1", "c2", "c3", "c4", "c5")

#: T-17 (D77): what a verifier fault means for the criterion under check.
#: Same shape as `ERROR_CODE_FOR` and the same D8 test for the replay faults:
#: an identical second replay of the same recording cannot answer differently,
#: so `NOT_RECORDED` and `RECORD_TAMPERED` are terminal.
VERIFIER_ERROR_CODE_FOR: dict[VerifierFailure, ErrorCode] = {
    VerifierFailure.CALL_FAILED: ErrorCode.MODEL_CALL_FAILED,
    VerifierFailure.UNPARSEABLE: ErrorCode.SCHEMA_INVALID,
    VerifierFailure.SCHEMA_INVALID: ErrorCode.SCHEMA_INVALID,
    VerifierFailure.NOT_RECORDED: ErrorCode.SCHEMA_INVALID,
    VerifierFailure.RECORD_TAMPERED: ErrorCode.SCHEMA_INVALID,
}

_RETRYABLE_VERIFIER_FAILURES = tuple(
    failure.value
    for failure, code in VERIFIER_ERROR_CODE_FOR.items()
    if code.retryable
)


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


def step_gather(state: WorkflowState, ctx: _Context) -> None:
    """Assemble the evidence this request is adjudicated on, through the planner.

    *Was three steps — `load_structured_facts`, `load_value_set`, `load_notes` —
    until T-61 put a port here (D63).* They were three fixed store reads;
    `FixedRetrievalPlanner` is those same three reads and nothing else changed for
    the deterministic path.

    The port is what lets a model-directed planner decide what to fetch while
    everything below this line stays identical. Note what the step does **not**
    do: it does not inspect what came back and decide whether to ask for more.
    A planner returns a bundle or raises; the graph does not negotiate with it.

    Fan-out width is still decided here rather than by the model mid-extraction —
    `len(state.notes)` is fixed the moment this step returns.
    """
    plan = ctx.planner.gather(
        state.patient_id, state.tree, ctx.patient_store, ctx.policy_store
    )
    state.observations = plan.observations
    state.conditions = plan.conditions
    state.value_set = plan.value_set
    state.notes = plan.notes
    if plan.trace is not None:
        state.traces.append(plan.trace)


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

    A failure surviving `_extract_one`'s budget resolves the extraction-consuming
    criteria to `ERROR` and aborts (REQ-23, REQ-24; T-29, D75). The abort carries
    the attempt count: a terminal fault was tried once, a retryable one exhausted
    the budget — the same classification that drove the loop.
    """
    for note in state.notes:
        try:
            result, trace = _extract_one(note, ctx)
        except ExtractionOutputError as exc:
            attempts = (
                ctx.max_attempts
                if exc.reason.value in _RETRYABLE_FAILURES
                else 1
            )
            raise DeterminationAborted(
                _extraction_error_results(ERROR_CODE_FOR[exc.reason], str(exc)),
                attempts=attempts,
            ) from exc
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
        _predicate(
            "a",
            evaluate_criterion_a,
            state.tree.criterion("a"),
            state.observations,
            state.as_of,
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
    state.results[index] = _predicate(
        criterion_a.id,
        reconcile_bmi,
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
        _predicate(
            "b",
            evaluate_criterion_b,
            state.tree.criterion("b"),
            state.conditions,
            state.value_set,
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
    state.run = _predicate("c3", qualifying_run, state.events)


def step_criteria_c(state: WorkflowState, ctx: _Context) -> None:
    """c1 through c5 (REQ-13, REQ-36, REQ-32, REQ-14, REQ-40, REQ-37, REQ-15).

    `c3_met` gates c2, c4 and c5 per REQ-15: with no qualifying period there is
    nothing for them to be scoped to, so they abstain rather than answering
    `NOT_MET` about a run that does not exist.
    """
    assert state.run is not None, "step_qualifying_run must precede this step"
    run = state.run

    c1 = _predicate(
        "c1", evaluate_c1, state.tree.criterion("c1"), state.events, state.assertions
    )
    c3 = _predicate(
        "c3", evaluate_c3, state.tree.criterion("c3"), state.events,
        state.assertions, run,
    )
    c3_met = c3.verdict is CriterionVerdict.MET

    c2 = _predicate(
        "c2", evaluate_c2, state.tree.criterion("c2"), run, state.as_of, c3_met
    )
    c4 = _predicate("c4", evaluate_c4, state.tree.criterion("c4"), run, c3_met)
    c5 = _predicate("c5", evaluate_c5, state.tree.criterion("c5"), run, c3_met)

    # Appended in the tree's own criterion order, so the gap list reads the way
    # the policy reads rather than the way this function happened to compute.
    state.results.extend([c1, c2, c3, c4, c5])
    state.results.sort(key=lambda r: _criterion_order(state.tree, r.criterion_id))


def step_verify(state: WorkflowState, ctx: _Context) -> None:
    """Article V (REQ-17, REQ-18; T-17, D77): every cited verdict is checked.

    Runs on the **final** cited verdicts — after `reconcile` has applied
    REQ-34's downgrade and `criteria_c` has filled the tree — so what the
    verifier checks is what the determination will say. `MET` and `NOT_MET`
    only: an abstention or an `ERROR` cites nothing (REQ-5), so there is no
    claim to check, and a verifier asked to bless an absence would be theater.

    Each claim's quotes are recovered by slicing the span from its source
    document (`validate_span` returns the slice), never taken from anything a
    model wrote — the payload the verifier sees is `build_claim_payload`'s
    output and nothing else. A rejection resolves the criterion to
    `INSUFFICIENT_EVIDENCE`/`VERIFIER_REJECTED` on the first occurrence — no
    retry, no `error_code`, determination still emitted (REQ-18). A *fault* is
    REQ-18a's separate loop and aborts like any other (REQ-24).
    """
    cited = [
        (position, result)
        for position, result in enumerate(state.results)
        if result.verdict in (CriterionVerdict.MET, CriterionVerdict.NOT_MET)
    ]
    if not cited:
        return
    if ctx.verifier is None:
        # D31's rule, `_criteria_determination`'s shape: a missing verifier
        # that silently accepted everything would pass every citation forever,
        # and every downstream test would agree with it.
        raise NotImplementedError(
            "this determination carries cited verdicts and Article V requires "
            "a verifier (REQ-17, T-17). Pass RecordedVerifierRunner for a "
            "replay of eval/verifier/results.json, LiveVerifierRunner to spend "
            "a call, or NullVerifierRunner to prove a path verifies nothing. "
            "Defaulting to accept-all would be the silent skip D77 refuses."
        )

    index = DocumentIndex()
    for position, result in cited:
        quotes: list[str] = []
        try:
            for span in result.spans:
                if span.document_id not in index:
                    index.add(_document_for(span.document_id, ctx))
                quotes.append(validate_span(span, index))
        except (SpanValidationError, KeyError) as exc:
            # The same classification `_validate_result_spans` gives a broken
            # span, raised here because this step reads the spans first.
            error = CriterionResult(
                criterion_id=result.criterion_id,
                verdict=CriterionVerdict.ERROR,
                error_code=ErrorCode.SPAN_VALIDATION_FAILED,
                error_detail=f"{result.criterion_id}: {exc}",
            )
            raise DeterminationAborted([error], attempts=None) from exc

        payload = build_claim_payload(
            state.tree.criterion(result.criterion_id),
            result.verdict.value,
            quotes,
        )
        try:
            answer, trace = _verify_one(result.criterion_id, payload, ctx)
        except VerifierOutputError as exc:
            attempts = (
                ctx.max_attempts
                if exc.reason.value in _RETRYABLE_VERIFIER_FAILURES
                else 1
            )
            error = CriterionResult(
                criterion_id=result.criterion_id,
                verdict=CriterionVerdict.ERROR,
                error_code=VERIFIER_ERROR_CODE_FOR[exc.reason],
                error_detail=str(exc),
            )
            raise DeterminationAborted([error], attempts=attempts) from exc

        state.traces.append(trace)
        if not answer.accept:
            # REQ-18 verbatim: first rejection, no retry. The contract
            # validators force the abstention's shape — no spans, a
            # `gap_reason` — so a rejection cannot collapse into `NOT_MET`
            # or ship the citation it just refused (Art. IV).
            state.results[position] = CriterionResult(
                criterion_id=result.criterion_id,
                verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
                gap_reason=GapReason.VERIFIER_REJECTED,
                discrepancies=result.discrepancies,
                detail=answer.reason or "the verifier rejected the citation",
            )


#: The graph. Order is the contract; the driver walks it and records what it
#: visited, and `tests/test_workflow.py` asserts the visited list against this
#: tuple rather than against a comment.
STEPS: tuple[tuple[str, object], ...] = (
    ("gather", step_gather),
    ("extract", step_extract),
    ("criterion_a", step_criterion_a),
    ("reconcile", step_reconcile),
    ("criterion_b", step_criterion_b),
    ("qualifying_run", step_qualifying_run),
    ("criteria_c", step_criteria_c),
    ("verify", step_verify),
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
    planner: RetrievalPlanner = field(default_factory=FixedRetrievalPlanner)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # T-17 (D77): `None` is not accept-all — `step_verify` raises the moment a
    # cited verdict has no verifier to check it, naming what to pass.
    verifier: VerifierRunner | None = None


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
    # `attempt` holds the loop's last value: the budget for a retryable fault,
    # 1 for a terminal one. Printing `ctx.max_attempts` here claimed three
    # attempts for faults that were deliberately tried once (T-29).
    raise ExtractionOutputError(
        last.reason,
        f"{note.document_id}: {last.message} (after {attempt} "
        f"attempt(s), {elapsed:.0f}ms). A partial extraction presented as a "
        "complete one is indistinguishable from a shorter patient history, so "
        "this raises rather than returning what it has.",
    )


def _verify_one(
    criterion_id: str, payload: dict, ctx: _Context
) -> tuple[VerifierAnswer, RunTrace]:
    """One claim, up to `max_attempts` times, with the attempts recorded.

    `_extract_one`'s shape (REQ-18a, REQ-46): the classifier decides whether a
    second attempt could plausibly differ, the model is never asked, and
    exhaustion raises. A rejection is not a failure — it returns, because it
    is an answer about the claim (REQ-18), and the caller applies it.
    """
    assert ctx.verifier is not None, "step_verify checks before calling"
    started = time.perf_counter()
    last: VerifierOutputError | None = None

    for attempt in range(1, ctx.max_attempts + 1):
        try:
            answer = ctx.verifier.run(payload)
        except VerifierOutputError as exc:
            last = exc
            if exc.reason.value not in _RETRYABLE_VERIFIER_FAILURES:
                break
            if attempt == ctx.max_attempts:
                break
            continue

        trace = RunTrace(
            runner_name=getattr(ctx.verifier, "name", type(ctx.verifier).__name__),
            model=(answer.metrics.model if answer.metrics else None),
            prompt_version=None,
            document_id=None,
            steps=[f"verify:{criterion_id}"],
            attempts=attempt,
            termination_reason="ok",
            metrics=[answer.metrics] if answer.metrics else [],
        )
        return answer, trace

    assert last is not None
    elapsed = (time.perf_counter() - started) * 1000.0
    raise VerifierOutputError(
        last.reason,
        f"criterion {criterion_id}: {last.message} (after {attempt} "
        "attempt(s), "
        f"{elapsed:.0f}ms). A claim that could not be checked is not a claim "
        "that passed, so this raises rather than accepting by default.",
    )


def _extraction_error_results(
    code: ErrorCode, detail: str
) -> list[CriterionResult]:
    """One `ERROR` per extraction-consuming criterion (T-29, D75).

    Each result self-validates: `CriterionResult`'s shape rules require the
    code and the exception text and forbid spans and a `gap_reason`, so a
    malformed `ERROR` cannot be built here or anywhere.
    """
    return [
        CriterionResult(
            criterion_id=criterion_id,
            verdict=CriterionVerdict.ERROR,
            error_code=code,
            error_detail=detail,
        )
        for criterion_id in EXTRACTION_CRITERIA
    ]


def _predicate(criterion_id: str, fn, /, *args, **kwargs):
    """REQ-23's fourth trigger: an unhandled exception in a predicate.

    Runs one deterministic predicate and maps a raise onto that criterion's
    `ERROR` with `PREDICATE_EXCEPTION` — terminal, first occurrence, because
    the same inputs raise the same way (D8). The handler re-raises classified
    (REQ-27); nothing is answered on a criterion whose code crashed, and
    REQ-24 aborts the determination before `assemble` can run.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # mapped to PREDICATE_EXCEPTION and re-raised (REQ-27)
        result = CriterionResult(
            criterion_id=criterion_id,
            verdict=CriterionVerdict.ERROR,
            error_code=ErrorCode.PREDICATE_EXCEPTION,
            error_detail=f"{type(exc).__name__}: {exc}",
        )
        raise DeterminationAborted([result], attempts=None) from exc


def _document_for(document_id: str, ctx: _Context) -> Document:
    """Either plane's document, for the span pass — the scorer's rule (D74):
    criterion spans point into the patient's bundle and notes, coverage spans
    into the corpus, so the check reads both ports or it cannot check."""
    try:
        return ctx.patient_store.get_document(document_id)
    except KeyError:
        return ctx.policy_store.get_document(document_id)


def _validate_result_spans(state: WorkflowState, ctx: _Context) -> None:
    """Article III at runtime: every cited span slices back, or nothing ships.

    Until T-29 `pa_agent.spans` was imported by tests and the eval harness
    only, so a runner conforming to the `ExtractionRunner` protocol could hand
    the graph an out-of-range span and no production code would notice —
    `anchor()` re-anchors by quote search and drops what it cannot find, which
    is a different guarantee than validation (D18, D75). A span that fails
    resolves its criterion to `ERROR`/`SPAN_VALIDATION_FAILED` and aborts
    (REQ-23, REQ-24). The index is built lazily from exactly the documents the
    spans name, through the ports; the pass costs string slicing and no model
    call.
    """
    index = DocumentIndex()
    for result in state.results:
        for span in result.spans:
            try:
                if span.document_id not in index:
                    index.add(_document_for(span.document_id, ctx))
                validate_span(span, index)
            except (SpanValidationError, KeyError) as exc:
                error = CriterionResult(
                    criterion_id=result.criterion_id,
                    verdict=CriterionVerdict.ERROR,
                    error_code=ErrorCode.SPAN_VALIDATION_FAILED,
                    error_detail=(
                        f"{span.document_id}[{span.char_start}:{span.char_end}]"
                        f": {exc}"
                    ),
                )
                raise DeterminationAborted([error], attempts=None) from exc


def run_criteria_workflow(
    policy_store: PolicyStore,
    patient_store: PatientStore,
    extraction_runner: ExtractionRunner,
    policy_ref: PolicyRef,
    patient_id: str,
    as_of: date,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    planner: RetrievalPlanner | None = None,
    verifier: VerifierRunner | None = None,
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
        planner=planner if planner is not None else FixedRetrievalPlanner(),
        max_attempts=max_attempts,
        verifier=verifier,
    )

    for name, step in STEPS:
        step(state, ctx)
        state.steps_visited.append(name)

    _validate_result_spans(state, ctx)

    determination = assemble(
        patient_id=patient_id,
        procedure_code=policy_ref.procedure_code,
        policy_version_id=policy_ref.policy_version_id,
        decision_expression=tree.decision_expression,
        results=state.results,
        metrics=state.metrics,
    )
    return WorkflowRun(determination=determination, state=state)
