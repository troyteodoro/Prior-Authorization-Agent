"""T-18 — the fixed workflow graph (REQ-1, REQ-4, REQ-19, REQ-52; D62).

Spends no model call. The chain runs end to end on the committed corpus by
replaying T-15's recording through `RecordedExtractionRunner`, and the branches
that must not reach a model are proved by handing in a runner that raises rather
than by reading a counter afterwards.

**Article I is what this file is really about.** The claim is not "we did not let
the model route" — that is a promise. The claims asserted here are structural: the
step sequence is a constant, the visited sequence equals it, every loop's iterable
is named and none of them is model output, two very different extractions visit
the same steps, the driver's body contains no branch on an extraction field, and
the number of branches anywhere in the module that read one is pinned at **one**
(the note-level BMI merge, which routes nothing). The AST checks exist because a
code review of a 450-line module is not a check, and because T-18's original exit
asked for a grep — which D10 says is not one.

Fan-out over two notes needs a patient with two notes, and the corpus has one per
patient by design (T-07). So that case uses a small local fake store, written the
way `eval/run_eval.py` writes its fakes — a class with the two methods the step
calls and nothing else. Everything else runs against the real adapters.
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import CriterionVerdict, Document
from pa_agent.determination import determine
from pa_agent.index import DocumentIndex
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    NullExtractionRunner,
    RecordedExtractionRunner,
)
from pa_agent.spans import validate
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.workflow import (
    DEFAULT_MAX_ATTEMPTS,
    STEP_NAMES,
    STEPS,
    run_criteria_workflow,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"
PATIENT_MANIFEST = REPO_ROOT / "data" / "patients" / "manifest.json"

TREE_VERSION = "ncd-100.1-jf-v1"
CONTRACTOR_CODE = "43775"
COVERED_CODE = "43644"
NON_COVERED_CODE = "43842"
FOREIGN_CODE = "99213"

AS_OF = date(2026, 9, 1)
# The T2DM patient's sub-35 BMI is inside its window here, so sc2 fires (D41).
E2_AS_OF = date(2024, 6, 1)


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def recording() -> dict:
    assert EXTRACTION_RESULTS.exists(), (
        "no recording; run `python scripts/run_extraction.py` (it spends model "
        "calls). This suite replays it and spends none."
    )
    return json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runner(recording) -> RecordedExtractionRunner:
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def case_patients() -> dict[str, str]:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return {
        case: record["patient_id"]
        for record in manifest["notes"]
        for case in record["cases"]
    }


@pytest.fixture(scope="module")
def ref(policy_store):
    resolved = policy_store.resolve(CONTRACTOR_CODE)
    assert resolved is not None
    return resolved


def _run(policy_store, patient_store, runner, patient_id, ref, **kwargs):
    return run_criteria_workflow(
        policy_store=policy_store,
        patient_store=patient_store,
        extraction_runner=runner,
        policy_ref=ref,
        patient_id=patient_id,
        as_of=AS_OF,
        **kwargs,
    )


# --------------------------------------------------------------------------
# The graph is a declared sequence, and the run walks it
# --------------------------------------------------------------------------


def test_the_step_sequence_is_a_module_constant() -> None:
    """A graph you can print is a graph a reviewer can check. The names are data,
    which is what lets the run below be compared against them rather than against
    a comment."""
    assert isinstance(STEPS, tuple)
    assert STEP_NAMES == tuple(name for name, _ in STEPS)
    assert len(set(STEP_NAMES)) == len(STEP_NAMES), "a step name is repeated"
    for name, step in STEPS:
        assert callable(step)
        assert step.__name__ == f"step_{name}", (
            f"{step.__name__} is registered under {name!r}; the constant and the "
            "function must not be able to drift"
        )


def test_a_run_visits_exactly_the_declared_steps_in_order(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    run = _run(policy_store, patient_store, runner, case_patients["E1"], ref)
    assert run.steps == list(STEP_NAMES)


def test_the_facts_are_loaded_before_the_criteria_that_read_them() -> None:
    """Order is the contract, so the parts of it that matter are asserted rather
    than left to whoever edits the tuple next.

    Two orderings are load-bearing. Extraction precedes reconciliation, because
    the note BMI does not exist until the model has read the note (D11). And
    `qualifying_run` precedes `criteria_c`, because c2, c4 and c5 all scope to the
    run c3 identified and computing it four times is four things free to disagree
    (D48).
    """
    order = list(STEP_NAMES)
    assert order.index("load_notes") < order.index("extract")
    assert order.index("extract") < order.index("reconcile")
    assert order.index("criterion_a") < order.index("reconcile")
    assert order.index("load_structured_facts") < order.index("criterion_a")
    assert order.index("load_value_set") < order.index("criterion_b")
    assert order.index("qualifying_run") < order.index("criteria_c")


def test_the_results_come_back_in_the_trees_own_criterion_order(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """The gap list reads the way the policy reads, not the way the steps happened
    to compute. (a) and (b) are evaluated in different steps from c1–c5, so
    without the sort the list would come out in execution order."""
    run = _run(policy_store, patient_store, runner, case_patients["E1"], ref)
    tree = policy_store.get_tree(TREE_VERSION)
    assert [r.criterion_id for r in run.determination.criterion_results] == [
        c.id for c in tree.criteria
    ]


# --------------------------------------------------------------------------
# Fan-out and fan-in
# --------------------------------------------------------------------------


class _TwoNoteStore:
    """A patient with two notes. The corpus has one each, by design (T-07).

    Only the methods the graph calls, so a step reaching for anything else fails
    loudly instead of finding a stub that answers.
    """

    def __init__(self, patient_id: str, notes: list[Document]) -> None:
        self._patient_id = patient_id
        self._notes = notes
        self.note_calls = 0

    def get_observations(self, patient_id: str) -> list:
        return []

    def get_conditions(self, patient_id: str) -> list:
        return []

    def get_notes(self, patient_id: str) -> list[Document]:
        assert patient_id == self._patient_id
        self.note_calls += 1
        return list(self._notes)


class _CountingRunner:
    """Wraps a runner and records every `(document_id, text_sha)` it was asked
    for, in order."""

    name = "counting"

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple[str, str]] = []

    def run(self, document_id: str, text: str):
        self.calls.append(
            (document_id, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
        )
        return self._inner.run(document_id, text)


def test_n_notes_produce_n_extraction_calls_one_per_note(
    policy_store, runner, ref, case_patients, patient_store
) -> None:
    """Fan-out, and the assertion is about *width*: the loop bound is
    `len(notes)`, so two notes mean exactly two calls with the two documents.
    Nothing the model returns can add an iteration, because no step asks whether
    to read another note.
    """
    first = patient_store.get_notes(case_patients["E1"])[0]
    second = patient_store.get_notes(case_patients["E5"])[0]
    store = _TwoNoteStore("two-note-patient", [first, second])
    counting = _CountingRunner(runner)

    run = run_criteria_workflow(
        policy_store=policy_store,
        patient_store=store,
        extraction_runner=counting,
        policy_ref=ref,
        patient_id="two-note-patient",
        as_of=AS_OF,
    )

    assert store.note_calls == 1, "the note list is read once, not per criterion"
    assert [document_id for document_id, _ in counting.calls] == [
        first.document_id,
        second.document_id,
    ]
    assert len(run.traces) == 2
    assert [t.document_id for t in run.traces] == [
        first.document_id,
        second.document_id,
    ]
    assert run.determination.model_calls == 2, (
        "one CallMetrics per note reaches the determination (REQ-22)"
    )


def test_events_fan_in_across_notes_and_keep_the_span_of_their_own_note(
    policy_store, runner, ref, case_patients, patient_store
) -> None:
    """Fan-in, and the property that makes it safe.

    Merging is right because c1–c5 adjudicate the patient's history rather than a
    document's. It is safe because every event still carries the span that cites
    the note it came from — so a merged list is still auditable back to two
    different documents, which is what Article III has to survive here.
    """
    first = patient_store.get_notes(case_patients["E1"])[0]
    second = patient_store.get_notes(case_patients["E5"])[0]
    store = _TwoNoteStore("two-note-patient", [first, second])

    run = run_criteria_workflow(
        policy_store=policy_store,
        patient_store=store,
        extraction_runner=runner,
        policy_ref=ref,
        patient_id="two-note-patient",
        as_of=AS_OF,
    )

    events = run.state.events
    documents = {e.span.document_id for e in events}
    assert documents == {first.document_id, second.document_id}, (
        "events from both notes must be present and distinguishable by span"
    )

    index = DocumentIndex()
    index.add(first)
    index.add(second)
    checked = 0
    for event in events:
        for span in (event.span, event.bmi_span, event.diet_span, event.activity_span):
            if span is None:
                continue
            assert validate(span, index), "a merged span no longer slices back"
            checked += 1
    assert checked > 0, "the gate is vacuous; no merged span was validated"


def test_merged_events_are_ordered_by_date_not_by_note(
    policy_store, runner, ref, case_patients, patient_store
) -> None:
    """c3 buckets by calendar month and walks for consecutive runs, so an event
    list ordered by which file it came from would make the run depend on the
    store's iteration order."""
    first = patient_store.get_notes(case_patients["E1"])[0]
    second = patient_store.get_notes(case_patients["E5"])[0]
    store = _TwoNoteStore("two-note-patient", [first, second])
    run = run_criteria_workflow(
        policy_store=policy_store,
        patient_store=store,
        extraction_runner=runner,
        policy_ref=ref,
        patient_id="two-note-patient",
        as_of=AS_OF,
    )
    dates = [e.event_date for e in run.state.events]
    assert dates == sorted(dates)


# --------------------------------------------------------------------------
# Retry is a Python budget
# --------------------------------------------------------------------------


class _FlakyRunner:
    """Fails `failures` times with a retryable fault, then delegates."""

    name = "flaky"

    def __init__(self, inner, failures: int) -> None:
        self._inner = inner
        self._remaining = failures
        self.attempts = 0

    def run(self, document_id: str, text: str):
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            raise ExtractionOutputError(
                ExtractionFailure.CALL_FAILED, "503 UNAVAILABLE (synthetic)"
            )
        return self._inner.run(document_id, text)


class _AlwaysFailingRunner:
    name = "always-failing"

    def __init__(self, reason: ExtractionFailure) -> None:
        self._reason = reason
        self.attempts = 0

    def run(self, document_id: str, text: str):
        self.attempts += 1
        raise ExtractionOutputError(self._reason, "synthetic")


def test_a_retryable_fault_is_retried_within_the_budget(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    flaky = _FlakyRunner(runner, failures=2)
    run = _run(
        policy_store, patient_store, flaky, case_patients["E1"], ref, max_attempts=3
    )
    assert flaky.attempts == 3
    assert run.traces[0].attempts == 3, (
        "the attempt count is recorded; Article X says measured, not estimated"
    )
    assert run.determination.outcome is not None


def test_exhausting_the_budget_raises_rather_than_returning_a_partial_result(
    policy_store, patient_store, ref, case_patients
) -> None:
    """The failure this whole design exists to prevent.

    A partial extraction presented as a complete one is indistinguishable from a
    patient with a shorter history: c3 measures a short run, c2 calls it stale,
    and the determination is a well-formed `NOT_MET` about a chart nobody
    finished reading.
    """
    failing = _AlwaysFailingRunner(ExtractionFailure.CALL_FAILED)
    with pytest.raises(ExtractionOutputError) as caught:
        _run(
            policy_store, patient_store, failing, case_patients["E1"], ref,
            max_attempts=2,
        )
    assert failing.attempts == 2
    assert caught.value.reason is ExtractionFailure.CALL_FAILED
    assert "2 attempt(s)" in caught.value.message


def test_a_terminal_fault_is_not_retried(
    policy_store, patient_store, ref, case_patients
) -> None:
    """REQ-18a's asymmetry. The same prompt will produce the same invalid
    response, so retrying a schema violation spends money to reach the same
    answer. Only a transport failure could plausibly differ (D8)."""
    failing = _AlwaysFailingRunner(ExtractionFailure.SCHEMA_INVALID)
    with pytest.raises(ExtractionOutputError):
        _run(
            policy_store, patient_store, failing, case_patients["E1"], ref,
            max_attempts=5,
        )
    assert failing.attempts == 1, (
        "a terminal fault consumed more than one attempt; the classifier is not "
        "being read"
    )


def test_the_retry_budget_is_a_python_constant_the_model_never_sees() -> None:
    assert isinstance(DEFAULT_MAX_ATTEMPTS, int) and DEFAULT_MAX_ATTEMPTS >= 1


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


class _EmptyPayloadRunner:
    """Returns a schema-valid payload with nothing in it.

    Not the same as a malformed response, and worth its own test: this is what a
    model does when it decides the note says nothing, and the system must be able
    to tell that apart from a fault. E7 is a real answer.
    """

    name = "empty-payload"

    def __init__(self) -> None:
        self.calls = 0

    def run(self, document_id: str, text: str):
        from pa_agent.extraction import build_result

        self.calls += 1
        return build_result(document_id, text, {"wm_events": [], "program_assertions": []})


def test_a_malformed_payload_raises_and_never_becomes_a_zero_event_extraction(
    policy_store, patient_store, ref, case_patients
) -> None:
    """The trap `spike/spike_001/run.py` documents in writing: a note that
    extracts nothing leaks no REQ-9 traps, so it scores perfect exclusion, and
    contributes no false positives, so it scores perfect precision. A transport
    failure would read as flawless extraction."""
    failing = _AlwaysFailingRunner(ExtractionFailure.UNPARSEABLE)
    with pytest.raises(ExtractionOutputError):
        _run(policy_store, patient_store, failing, case_patients["E1"], ref)


def test_an_honestly_empty_extraction_is_an_answer_and_not_a_fault(
    policy_store, patient_store, ref, case_patients
) -> None:
    """The other side of the line above, and the reason the line matters.

    A model that reads the note and finds no encounter produces E7, which is a
    determination with a gap list telling Sam to go find documentation of a
    program. Collapsing that into an error would withhold a useful answer;
    collapsing a fault into it would invent one.
    """
    empty = _EmptyPayloadRunner()
    run = _run(policy_store, patient_store, empty, case_patients["E1"], ref)
    assert empty.calls == 1
    by_id = {r.criterion_id: r for r in run.determination.criterion_results}
    assert by_id["c1"].verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert by_id["c1"].gap_reason.value == "NO_EVIDENCE_RETRIEVED"
    assert run.determination.outcome.value == "INSUFFICIENT_EVIDENCE"


def test_a_recorded_payload_for_a_changed_note_refuses_to_anchor(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """D18's rule, reached through the graph. Re-anchoring against a document the
    payload never came from would find most quotes somewhere, which is what makes
    it dangerous rather than merely wrong."""
    note = patient_store.get_notes(case_patients["E1"])[0]
    edited = Document.from_text(note.document_id, note.text + "\n\nAddendum.\n")
    store = _TwoNoteStore("edited-patient", [edited])
    with pytest.raises(ExtractionOutputError) as caught:
        run_criteria_workflow(
            policy_store=policy_store,
            patient_store=store,
            extraction_runner=runner,
            policy_ref=ref,
            patient_id="edited-patient",
            as_of=AS_OF,
        )
    assert caught.value.reason is ExtractionFailure.DOCUMENT_CHANGED


# --------------------------------------------------------------------------
# Zero model calls, proved rather than counted
# --------------------------------------------------------------------------


def test_a_non_covered_code_answers_with_a_runner_that_cannot_be_called(
    policy_store, patient_store
) -> None:
    """A4 for E3. Reading `model_calls == 0` afterwards proves the counter reads
    zero; getting an answer from a runner that raises on call proves the path was
    never reached."""
    determination = determine(
        policy_store,
        NON_COVERED_CODE,
        patient_id="X",
        patient_store=patient_store,
        as_of=AS_OF,
        extraction_runner=NullExtractionRunner("E3 is sc1"),
    )
    assert determination.outcome.value == "NOT_COVERED"
    assert determination.model_calls == 0


def test_an_ungoverned_code_answers_with_a_runner_that_cannot_be_called(
    policy_store, patient_store
) -> None:
    result = determine(
        policy_store,
        FOREIGN_CODE,
        patient_id="X",
        patient_store=patient_store,
        as_of=AS_OF,
        extraction_runner=NullExtractionRunner("no policy governs this code"),
    )
    assert type(result).__name__ == "NoPolicyResult"


def test_sc2_answers_with_a_runner_that_cannot_be_called(
    policy_store, patient_store
) -> None:
    """A4 for E2. The categorical exclusion is a rule about the patient's
    structured facts, so it resolves before a note is read."""
    manifest = json.loads(PATIENT_MANIFEST.read_text(encoding="utf-8"))
    t2dm = next(r for r in manifest["bundles"] if r["has_active_t2dm"])
    determination = determine(
        policy_store,
        COVERED_CODE,
        patient_id=t2dm["patient_id"],
        patient_store=patient_store,
        as_of=E2_AS_OF,
        extraction_runner=NullExtractionRunner("E2 is sc2"),
    )
    assert determination.outcome.value == "NOT_COVERED"
    assert determination.model_calls == 0
    assert determination.exclusion_evidence, "sc2 cites the patient's facts (D41)"


# --------------------------------------------------------------------------
# Article I: nothing routes on model output
# --------------------------------------------------------------------------

#: Every attribute of an extraction result. A branch reading one of these is a
#: branch a model can steer.
_MODEL_DERIVED = frozenset(
    {
        "events",
        "assertions",
        "current_bmi",
        "current_bmi_span",
        "wm_events",
        "program_assertions",
        "raw",
        "dropped",
        "anchored_spans",
    }
)


#: The one place in the graph a branch legitimately reads model output, named so
#: that a second one is a visible failure rather than a silent addition.
#:
#: `step_extract` merges the note-level BMI with a first-non-null rule, and
#: selecting a value out of model output cannot avoid comparing it to `None`. That
#: is data handling, not routing: it changes no step, no iteration count, and no
#: model call. The distinction is the whole of Article I — the article prohibits
#: *routing, branching, looping and terminating* on model output, and a
#: null-coalesce does none of those.
#:
#: Pinned as a count rather than allowed by pattern, the way D51 pinned the
#: provisional-constant count at zero: the number is the check.
_ALLOWED_MODEL_BRANCHES = 1
_ALLOWED_IN = "step_extract"


def _module_tree(name: str) -> ast.Module:
    return ast.parse((REPO_ROOT / "pa_agent" / name).read_text(encoding="utf-8"))


def _model_branches(tree: ast.AST) -> list[str]:
    """Every `if`/`while`/ternary whose test reads an extraction attribute."""
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If, ast.While, ast.IfExp)):
            continue
        attributes = {
            n.attr for n in ast.walk(node.test) if isinstance(n, ast.Attribute)
        }
        touched = attributes & _MODEL_DERIVED
        if touched:
            found.append(f"line {node.lineno}: {sorted(touched)}")
    return found


def test_the_driver_contains_no_branch_on_model_output() -> None:
    """Article I where it actually lives: the driver.

    `run_criteria_workflow` decides which steps run and in what order. If nothing
    in *its* body reads model output, then the graph's shape cannot depend on what
    a model said, whatever the individual steps do with the data they were handed.

    Asserted on the AST rather than by grep because T-18's original exit said
    "grep the module for branching on model output and find none", and a grep for
    a string in a source file is not a check (D10 — and the reason that exit was
    rewritten).
    """
    tree = _module_tree("workflow.py")
    driver = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_criteria_workflow"
    )
    assert _model_branches(driver) == []
    # And it contains no conditional at all around the walk, so no step can be
    # skipped or repeated for any reason.
    walk = next(
        node for node in ast.walk(driver) if isinstance(node, ast.For)
    )
    assert ast.unparse(walk.iter) == "STEPS"
    assert not [n for n in ast.walk(walk) if isinstance(n, (ast.If, ast.Break, ast.Continue))], (
        "the step walk contains a branch, a break or a continue; every step runs "
        "on every request or the sequence is not fixed"
    )


def test_exactly_one_branch_in_the_module_reads_model_output_and_it_is_named() -> None:
    """The count is the check.

    One branch legitimately compares a model-supplied value to `None` — the
    note-level BMI merge — and it routes nothing. Pinning the count at one means a
    second one cannot arrive without this failing, so the next person to add a
    branch on extraction output has to argue for it in a diff.
    """
    tree = _module_tree("workflow.py")
    by_function: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            branches = _model_branches(node)
            if branches:
                by_function[node.name] = branches

    total = sum(len(v) for v in by_function.values())
    assert total == _ALLOWED_MODEL_BRANCHES, (
        f"{total} branch(es) in workflow.py read model output, expected "
        f"{_ALLOWED_MODEL_BRANCHES}: {by_function}. A new one is a design "
        "decision and needs a decisions entry, not a passing suite."
    )
    assert set(by_function) == {_ALLOWED_IN}, (
        f"the branch moved out of {_ALLOWED_IN} into {sorted(by_function)}"
    )


def test_every_loop_iterates_over_store_data_or_a_python_constant() -> None:
    """Where the iteration counts come from, enumerated exactly.

    A loop over `state.events` would make the number of iterations depend on what
    the model returned, which is how an agent system spends an unbounded amount of
    money on one request. Listing the four iterables rather than pattern-matching
    them means a fifth loop has to be justified.
    """
    tree = _module_tree("workflow.py")
    iterables = sorted(
        ast.unparse(node.iter)
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor))
    )
    assert iterables == [
        "STEPS",                          # the declared graph
        "enumerate(tree.criteria)",       # the policy's own criterion order
        "range(1, ctx.max_attempts + 1)", # the retry budget, a Python constant
        "state.notes",                    # the fan-out: PatientStore's answer
    ], f"workflow.py loops over {iterables}"


def test_two_different_extractions_visit_the_same_steps(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """Article I behaviourally: the graph is the same graph whatever the model
    said. One chart yields six encounters, another yields none, and the visited
    step sequence is identical — no step was skipped because there was nothing to
    do and none was added because there was more."""
    rich = _run(policy_store, patient_store, runner, case_patients["E1"], ref)
    barren = _run(policy_store, patient_store, runner, case_patients["E8"], ref)

    assert len(rich.state.events) > 0
    assert len(barren.state.events) == 0
    assert rich.steps == barren.steps == list(STEP_NAMES)
    assert (
        rich.determination.outcome is not barren.determination.outcome
    ), "the two charts must actually differ, or this proves nothing"


def test_the_same_extraction_twice_produces_the_same_determination(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """Article II's test, as written: if the same input could produce a different
    verdict on a second run, the computation is in the wrong place."""
    first = _run(policy_store, patient_store, runner, case_patients["E6"], ref)
    second = _run(policy_store, patient_store, runner, case_patients["E6"], ref)
    assert first.determination.model_dump(mode="json") == second.determination.model_dump(
        mode="json"
    )


def test_the_workflow_imports_no_model_and_no_adk() -> None:
    """The boundary `pa_agent/__init__.py` exists to hold, and the reason the
    graph is not an ADK `Workflow` (D62). Checked in a fresh interpreter, because
    this suite imports the recording elsewhere and a `sys.modules` check here
    would pass or fail by test ordering."""
    import subprocess
    import sys

    probe = (
        "import sys; import pa_agent.workflow, pa_agent.aggregate, "
        "pa_agent.determination; "
        "assert 'google.adk' not in sys.modules, 'the workflow pulled in the ADK'; "
        "assert 'google.genai' not in sys.modules, 'the workflow pulled in genai'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr


def test_the_workflow_reaches_data_only_through_the_two_ports() -> None:
    """REQ-41: no path, no connection, no storage location.

    The graph is the one module that holds both plane handles, which REQ-41
    explicitly allows — "a module that could read both would have to hold both
    handles" — and what it must not do is reach around them.
    """
    tree = _module_tree("workflow.py")
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "time",
        "dataclasses",
        "datetime",
        "pa_agent.aggregate",
        "pa_agent.contracts",
        "pa_agent.criteria",
        "pa_agent.reconcile",
        "pa_agent.runners",
        "pa_agent.stores.patient",
        "pa_agent.stores.policy",
    }, f"workflow.py imports {sorted(imported)}"

    source = (REPO_ROOT / "pa_agent" / "workflow.py").read_text(encoding="utf-8")
    for forbidden in ("open(", "Path(", "read_text", "json.load", "sqlite3"):
        assert forbidden not in source, (
            f"workflow.py contains {forbidden!r}; data reaches the graph through "
            "the ports and nowhere else (REQ-41)"
        )


# --------------------------------------------------------------------------
# Article X: every model turn is counted, not just the first
# --------------------------------------------------------------------------


class _TwoTurnRunner:
    """A runner whose trace carries two model turns, as a tool call does.

    Modelled on `AdkExtractionRunner` under `tool_fetch=True`: the model spends
    one LLM call asking for the tool and another answering, so the note costs two
    turns. Written as a local fake rather than driving the ADK, because the claim
    under test is about what the *workflow* does with a trace it is handed.
    """

    name = "two-turn"

    def __init__(self, inner) -> None:
        self._inner = inner

    def run(self, document_id: str, text: str):
        from pa_agent.contracts import CallMetrics, RunTrace

        result = self._inner.run(document_id, text)
        turn = CallMetrics(
            model="two-turn-model",
            purpose="extraction",
            input_tokens=100,
            output_tokens=10,
            wall_time_ms=5.0,
        )
        result.metrics = turn
        result.trace = RunTrace(
            runner_name=self.name,
            document_id=document_id,
            metrics=[turn, turn],
        )
        return result


def test_every_model_turn_reaches_the_determination_not_just_the_first(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """A tool-calling extraction costs two LLM calls per note (ADK's
    `max_llm_calls` counts calls, not tool invocations). `build_result` takes a
    single `CallMetrics`, so reading only `result.metrics` would report half the
    tokens the request actually spent — and it would look entirely plausible.

    Article X says recorded from the first model call, and A6 asks for cost
    reported from instrumentation. A number that is quietly half is worse than no
    number, because nobody goes looking for it.
    """
    two_turn = _TwoTurnRunner(runner)
    run = _run(policy_store, patient_store, two_turn, case_patients["E1"], ref)

    assert run.determination.model_calls == 2, (
        "only one turn reached the determination; a tool-calling run costs two "
        "and the second was dropped at the workflow boundary"
    )
    assert run.determination.total_input_tokens == 200
    assert run.determination.total_output_tokens == 20
    assert run.traces[0].metrics == run.determination.metrics


def test_a_runner_without_a_trace_still_reports_its_one_call(
    policy_store, patient_store, runner, ref, case_patients
) -> None:
    """The fallback, so the fix above cannot silently zero the replay path — the
    recorded runner carries no trace by design and its single recorded
    measurement must still reach the determination."""
    run = _run(policy_store, patient_store, runner, case_patients["E1"], ref)
    assert run.determination.model_calls == 1
    assert run.determination.total_input_tokens > 0
