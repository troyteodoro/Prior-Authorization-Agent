"""T-25 — assembly turns a resolution into the artifact US-1 ships.
**T-19 — and the aggregator that turns criterion verdicts into an outcome (D62).**

T-25 gates the minimal assembly:

- E3's code becomes a `NOT_COVERED` determination that records its
  `policy_version_id` (REQ-4), cites its denial with spans that slice back out
  of the hashed corpus (Art. III), carries an empty gap list (REQ-21), and
  reads zero on the model-call counter (REQ-2, A4).
- a code no policy governs becomes `NoPolicyResult` — a type that is not a
  `Determination`, because REQ-4's version id cannot exist for it (D32).
- the two D32 validators refuse the combinations they exist to refuse.

T-19 adds the aggregator:

- the **policy's** `decision_expression` is parsed and evaluated in Python, not
  hardcoded as a conjunction — a tree carrying an `OR` must be honoured, and the
  truth tables are three-valued because `INSUFFICIENT_EVIDENCE` is not false.
- REQ-20: one unsubstantiated criterion sinks the whole outcome, never to `MET`.
- the gap list names every criterion not `MET`, and distinguishes a documentation
  gap from a substantive failure by `gap_reason` rather than by prose.
- REQ-39's discrepancies are a separate list, disjoint from the gap list.
- a contractor-determined determination cites the delegation **and** the MAC's
  exercise of it, never the NCD alone (D33's obligation, handed here by T-24).
- E1 and E8 run end to end on the committed corpus and produce what spec §6 says.

**No model anywhere in this file.** The end-to-end rows replay T-15's recording
through `RecordedExtractionRunner`, so every number here is reproducible and free
(REQ-52). The CLI is exercised as a subprocess, because its exit code is T-25's
exit condition and a test that imports `main()` would never notice a broken
`python -m` entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.aggregate import (
    DecisionExpressionError,
    aggregate_outcome,
    contractor_citations,
    evaluate_decision_expression,
)
from pa_agent.contracts import (
    CoverageClaim,
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
    GapReason,
)
from pa_agent.determination import NoPolicyResult, determine
from pa_agent.index import DocumentIndex
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.spans import validate
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_VERSION = "ncd-100.1-jf-v1"
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"

E3_CODE = "43842"
FOREIGN_CODE = "99213"
CONTRACTOR_CODE = "43775"
COVERED_CODE = "43644"

# T-06 pinned the ground truth to this date and every recency verdict moves with
# it, so it is a module constant rather than `date.today()` — a gate whose answer
# changes tomorrow is not a gate.
AS_OF = date(2026, 9, 1)

SYNTH_SPAN = EvidenceSpan(document_id="synthetic", char_start=0, char_end=10)


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def tree(store):
    return store.get_tree(TREE_VERSION)


@pytest.fixture(scope="module")
def runner() -> RecordedExtractionRunner:
    """T-15's recording, replayed: the whole chain for zero model calls (REQ-52)."""
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def case_patients() -> dict[str, str]:
    """`case_id -> patient_id`, read from T-07's note manifest.

    Derived rather than written out, so a case reassigned in the corpus moves here
    with it instead of silently testing the wrong chart.
    """
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return {
        case: record["patient_id"]
        for record in manifest["notes"]
        for case in record["cases"]
    }


@pytest.fixture(scope="module")
def e1_patient(case_patients) -> str:
    return case_patients["E1"]


@pytest.fixture(scope="module")
def e8_patient(case_patients) -> str:
    return case_patients["E8"]


@pytest.fixture(scope="module")
def e3_determination(store) -> Determination:
    result = determine(store, E3_CODE, patient_id=None)
    assert isinstance(result, Determination)
    return result


# --------------------------------------------------------------------------
# E3: NOT_COVERED, versioned, cited, zero calls (REQ-2, REQ-4, REQ-21, A4)
# --------------------------------------------------------------------------


def test_e3_is_not_covered_with_the_policy_version(e3_determination):
    assert e3_determination.outcome is DeterminationOutcome.NOT_COVERED
    assert e3_determination.policy_version_id == TREE_VERSION


def test_e3_reads_zero_on_the_model_call_counter(e3_determination):
    """A4's counter, zero by construction: metrics is written empty and
    nothing in the sc1 path can append to it."""
    assert e3_determination.model_calls == 0
    assert e3_determination.metrics == []


def test_e3_cites_its_denial_and_the_citation_slices_back(e3_determination, store):
    claim = e3_determination.coverage_claim
    assert claim is not None, (
        "a NOT_COVERED determination with no coverage_claim asserts a denial "
        "and cites nothing (Art. III, D32)"
    )
    document = store.get_document(claim.document_id)
    assert document.slice(claim) == claim.quote
    assert claim.scoping_quote is not None
    scope_doc = store.get_document(claim.scoping_quote.document_id)
    assert scope_doc.slice(claim.scoping_quote) == claim.scoping_quote.quote


def test_e3_has_an_empty_gap_list(e3_determination):
    """REQ-21: the gap list names every criterion not resolved MET. sc1
    evaluated none, so nothing is missing — an entry here would be inventing a
    criterion nobody adjudicated."""
    assert e3_determination.gap_list == []
    assert e3_determination.criterion_results == []


def test_e3_carries_no_patient_when_none_was_consulted(e3_determination):
    assert e3_determination.patient_id is None


# --------------------------------------------------------------------------
# NO_POLICY_FOUND is not a denial, and not a Determination (REQ-1, D32)
# --------------------------------------------------------------------------


def test_an_ungoverned_code_yields_no_policy_result(store):
    result = determine(store, FOREIGN_CODE)
    assert isinstance(result, NoPolicyResult)
    assert not isinstance(result, Determination)
    assert result.procedure_code == FOREIGN_CODE
    assert not hasattr(result, "policy_version_id"), (
        "NoPolicyResult grew a policy_version_id; REQ-4's impossibility for an "
        "ungoverned code is the reason this type exists (D32)"
    )


# --------------------------------------------------------------------------
# A criteria request refuses to answer without what it needs
# --------------------------------------------------------------------------
#
# These two used to assert the `NotImplementedError` citing T-19, which was the
# strongest statement available while the chain was unbuilt. T-18 and T-19 built
# it, so what remains to assert is the guard that replaced the raise — and that
# guard matters more than the raise ever did (D62).


def test_a_covered_code_refuses_to_answer_without_a_patient(store):
    """No patient, no criteria. A determination over criterion verdicts with no
    patient is adjudicating nobody, which D32 already refused at the contract
    level; refusing it here means the request never gets that far."""
    with pytest.raises(NotImplementedError, match="needs a patient"):
        determine(store, COVERED_CODE)
    with pytest.raises(NotImplementedError, match="needs a patient"):
        determine(store, CONTRACTOR_CODE)


def test_a_covered_code_refuses_to_answer_without_an_extraction_runner(
    store, patient_store, e1_patient
):
    """The most dangerous default in the system, refused in writing.

    A missing runner that silently produced no events would adjudicate every
    patient as having no weight-management history: c1 abstains with
    `NO_EVIDENCE_RETRIEVED`, c3 follows, the determination is
    `INSUFFICIENT_EVIDENCE`, and **it is a perfectly well-formed answer.** Every
    downstream test would agree with it, because there is nothing structurally
    wrong with it — it is E7's shape applied to a patient who is not E7.

    That is exactly the failure D31 named for `resolve` returning `None` and D39
    named for a patient store returning `[]`. Third time, same rule: the absence
    raises and names what to pass (REQ-52).
    """
    with pytest.raises(NotImplementedError, match="extraction_runner"):
        determine(
            store,
            COVERED_CODE,
            patient_id=e1_patient,
            patient_store=patient_store,
            as_of=AS_OF,
        )


def test_the_refusal_names_the_runners_a_caller_could_pass(store, patient_store, e1_patient):
    """A guard that says "missing" and not "missing what" gets worked around."""
    with pytest.raises(NotImplementedError) as excinfo:
        determine(
            store, COVERED_CODE, patient_id=e1_patient,
            patient_store=patient_store, as_of=AS_OF,
        )
    message = str(excinfo.value)
    for name in ("RecordedExtractionRunner", "DirectExtractionRunner", "AdkExtractionRunner"):
        assert name in message


# --------------------------------------------------------------------------
# T-19: the policy's expression is parsed and evaluated in Python
# --------------------------------------------------------------------------


def _results(**verdicts) -> list[CriterionResult]:
    """Criterion results with the given verdicts, in the tree's own order."""
    built = []
    for criterion_id, verdict in verdicts.items():
        if verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE:
            built.append(
                CriterionResult(
                    criterion_id=criterion_id,
                    verdict=verdict,
                    gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
                )
            )
        else:
            built.append(
                CriterionResult(
                    criterion_id=criterion_id, verdict=verdict, spans=[SYNTH_SPAN]
                )
            )
    return built


ALL_MET = dict.fromkeys(("a", "b", "c1", "c2", "c3", "c4", "c5"), CriterionVerdict.MET)


def test_the_trees_own_expression_decides_the_outcome(tree):
    """REQ-19. The expression comes from the policy file, and the tree under test
    is the committed one — so this asserts the real rule, not a stand-in."""
    assert tree.decision_expression == "a AND b AND c1 AND c2 AND c3 AND c4 AND c5"
    assert (
        aggregate_outcome(tree.decision_expression, _results(**ALL_MET))
        is DeterminationOutcome.MET
    )


@pytest.mark.parametrize("criterion_id", ["a", "b", "c1", "c2", "c3", "c4", "c5"])
def test_any_single_not_met_sinks_the_conjunction(tree, criterion_id):
    verdicts = dict(ALL_MET, **{criterion_id: CriterionVerdict.NOT_MET})
    assert (
        aggregate_outcome(tree.decision_expression, _results(**verdicts))
        is DeterminationOutcome.NOT_MET
    )


@pytest.mark.parametrize("criterion_id", ["a", "b", "c1", "c2", "c3", "c4", "c5"])
def test_any_single_abstention_propagates(tree, criterion_id):
    """REQ-20, on every leg. `INSUFFICIENT_EVIDENCE` is not false and it is not
    true: the honest overall answer is that the system could not tell."""
    verdicts = dict(ALL_MET, **{criterion_id: CriterionVerdict.INSUFFICIENT_EVIDENCE})
    assert (
        aggregate_outcome(tree.decision_expression, _results(**verdicts))
        is DeterminationOutcome.INSUFFICIENT_EVIDENCE
    )


def test_a_substantive_failure_outranks_an_abstention(tree):
    """`NOT_MET` beats `INSUFFICIENT_EVIDENCE` under AND, and the reason is
    clinical rather than logical: the chart has a real failure, and Sam does not
    need to go collect anything to learn the request will not clear."""
    verdicts = dict(
        ALL_MET,
        c2=CriterionVerdict.NOT_MET,
        c4=CriterionVerdict.INSUFFICIENT_EVIDENCE,
    )
    assert (
        aggregate_outcome(tree.decision_expression, _results(**verdicts))
        is DeterminationOutcome.NOT_MET
    )


def test_the_expression_is_parsed_and_not_hardcoded_as_a_conjunction():
    """The mutation this whole design exists to catch.

    Under today's tree, `all(v is MET for v in verdicts)` produces identical
    answers — which is exactly why the shortcut is tempting. A tree carrying an
    `OR` is where the two diverge, and a second jurisdiction's "one comorbidity
    *or* a documented attempt" is that tree. Article VII wants the clinical rule
    in the reviewed file; a rule the code does not read is not in the file.
    """
    verdicts = {"a": CriterionVerdict.NOT_MET, "b": CriterionVerdict.MET}
    assert (
        evaluate_decision_expression("a OR b", verdicts) is CriterionVerdict.MET
    ), "an OR is not being honoured; the expression is not being parsed"
    assert (
        evaluate_decision_expression("a AND b", verdicts)
        is CriterionVerdict.NOT_MET
    )
    assert (
        evaluate_decision_expression("(a OR b) AND b", verdicts)
        is CriterionVerdict.MET
    ), "grouping is not being honoured"


def test_an_or_still_cannot_approve_over_an_unsupported_leg(tree):
    """REQ-20 is enforced independently of the boolean, and this is the case that
    shows why. `MET OR INSUFFICIENT_EVIDENCE` is `MET` as a disjunction — one leg
    really is satisfied — and Article IV still refuses to approve while a
    criterion sits unsubstantiated. The expression decides the boolean; the
    article decides whether that boolean may be an approval."""
    verdicts = {"a": CriterionVerdict.MET, "b": CriterionVerdict.INSUFFICIENT_EVIDENCE}
    assert evaluate_decision_expression("a OR b", verdicts) is CriterionVerdict.MET
    assert (
        aggregate_outcome("a OR b", _results(**verdicts))
        is DeterminationOutcome.INSUFFICIENT_EVIDENCE
    )


def test_a_criterion_the_expression_names_and_nobody_evaluated_raises(tree):
    """Not defaulted in either direction. Defaulting it true approves on a rule
    nobody applied; defaulting it false denies on one."""
    verdicts = dict(ALL_MET)
    verdicts.pop("c5")
    with pytest.raises(DecisionExpressionError, match="produced no verdict"):
        aggregate_outcome(tree.decision_expression, _results(**verdicts))


def test_an_unimplemented_operator_raises_rather_than_being_approximated():
    with pytest.raises(DecisionExpressionError, match="does not implement"):
        evaluate_decision_expression(
            "a NOT b", {"a": CriterionVerdict.MET, "b": CriterionVerdict.MET}
        )


def test_an_empty_expression_is_not_an_approval():
    with pytest.raises(DecisionExpressionError):
        evaluate_decision_expression("   ", {"a": CriterionVerdict.MET})


def test_two_verdicts_for_one_criterion_raise(tree):
    """Silently keeping the last would make the outcome depend on the order the
    steps happened to append in."""
    doubled = _results(**ALL_MET) + _results(a=CriterionVerdict.NOT_MET)
    with pytest.raises(DecisionExpressionError, match="two results"):
        aggregate_outcome(tree.decision_expression, doubled)


def test_zero_criteria_is_not_an_outcome(tree):
    with pytest.raises(DecisionExpressionError, match="zero"):
        aggregate_outcome(tree.decision_expression, [])


# --------------------------------------------------------------------------
# The D32 validators refuse what they exist to refuse
# --------------------------------------------------------------------------


def _claim() -> CoverageClaim:
    return CoverageClaim(document_id="d", char_start=0, char_end=4, quote="text")


def test_a_patientless_determination_with_verdicts_is_refused():
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    result = CriterionResult(
        criterion_id="a", verdict=CriterionVerdict.MET, spans=[span]
    )
    with pytest.raises(ValidationError, match="adjudicating nobody"):
        Determination(
            patient_id=None,
            procedure_code=E3_CODE,
            policy_version_id=TREE_VERSION,
            outcome=DeterminationOutcome.MET,
            criterion_results=[result],
        )


def test_a_coverage_claim_on_a_met_determination_is_refused():
    with pytest.raises(ValidationError, match="belongs only to NOT_COVERED"):
        Determination(
            patient_id="p",
            procedure_code=E3_CODE,
            policy_version_id=TREE_VERSION,
            outcome=DeterminationOutcome.MET,
            coverage_claim=_claim(),
        )


# --------------------------------------------------------------------------
# The CLI — T-25's exit condition, exercised the way it is invoked
# --------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_the_cli_prints_e3s_determination_and_exits_zero():
    proc = _run_cli("--patient", "X", "--procedure", E3_CODE)
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["outcome"] == "NOT_COVERED"
    assert printed["policy_version_id"] == TREE_VERSION
    assert printed["model_calls"] == 0
    assert printed["gap_list"] == []
    assert printed["coverage_claim"]["document_id"] == "ncd_100_1"
    assert printed["patient_id"] == "X"


def test_the_cli_reports_no_policy_found_without_denying():
    proc = _run_cli("--patient", "X", "--procedure", FOREIGN_CODE)
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["result"] == "NO_POLICY_FOUND"
    assert "outcome" not in printed


def test_the_cli_answers_a_covered_code_end_to_end(e1_patient):
    """*Replaces the exit-2 test T-25 wrote (D62).*

    That test asserted the covered path was unbuilt, which was true and is the
    kind of assertion that stops meaning anything the moment the path lands. What
    it was really guarding — that a request either answers completely or does not
    answer at all — is asserted here instead: exit 0, a full determination, and
    the instrumentation Article X wants beside it.
    """
    proc = _run_cli(
        "--patient", e1_patient, "--procedure", CONTRACTOR_CODE,
        "--as-of", AS_OF.isoformat(),
    )
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["outcome"] == "MET"
    assert printed["policy_version_id"] == TREE_VERSION
    assert [r["criterion_id"] for r in printed["criterion_results"]] == [
        "a", "b", "c1", "c2", "c3", "c4", "c5",
    ]
    assert printed["gap_list"] == []
    assert printed["discrepancies"] == []
    # Art. X: recorded, not estimated. The default runner replays T-15's
    # recording, so the counters are that call's real measurements.
    assert printed["model_calls"] == 1
    assert printed["total_input_tokens"] > 0
    assert printed["total_wall_time_ms"] > 0
    assert printed["coverage_claim"] is None, (
        "a criteria determination is not a denial under a coverage rule (D32)"
    )


def test_the_cli_pins_its_answer_to_an_as_of(e1_patient):
    """Every recency verdict moves with the date, so a demo that does not pin it
    is a demo whose output changes tomorrow. The flag exists for that, and this
    asserts the two dates actually produce different answers rather than the flag
    being decorative."""
    pinned = _run_cli(
        "--patient", e1_patient, "--procedure", CONTRACTOR_CODE,
        "--as-of", AS_OF.isoformat(),
    )
    stale = _run_cli(
        "--patient", e1_patient, "--procedure", CONTRACTOR_CODE,
        "--as-of", "2030-01-01",
    )
    assert pinned.returncode == 0 and stale.returncode == 0
    assert json.loads(pinned.stdout)["outcome"] == "MET"
    assert json.loads(stale.stdout)["outcome"] == "NOT_MET", (
        "four years on, the same chart's program is stale (REQ-32) and its BMI is "
        "outside the lookback (REQ-16); an answer that did not move would mean "
        "--as-of is not reaching the criteria"
    )


def test_the_unbuilt_path_handler_still_maps_to_exit_two():
    """Exit 2 has no reachable route from the CLI today, and the handler stays.

    T-17's verifier and T-29's fault mapping are unbuilt and will reach it again,
    and a handler that exists is how the next unbuilt path reports itself instead
    of crashing with a traceback. Asserted on `main()`'s source rather than by
    subprocess, because there is currently no input that triggers it — which is
    the honest reason to test it this way and is stated so nobody later reads a
    green suite as evidence that exit 2 was exercised.
    """
    source = (REPO_ROOT / "pa_agent" / "cli.py").read_text(encoding="utf-8")
    assert "except NotImplementedError as exc:" in source
    assert "return 2" in source


def test_the_cli_exits_one_on_an_unknown_patient():
    """A bad request is not an answer and not an unbuilt path (D41)."""
    proc = _run_cli("--patient", "nobody-here", "--procedure", COVERED_CODE)
    assert proc.returncode == 1
    assert "bad request" in proc.stderr
    assert proc.stdout == ""


# --------------------------------------------------------------------------
# T-19: E1 and E8 end to end, on the committed corpus, for zero model calls
# --------------------------------------------------------------------------


def _determine_case(store, patient_store, runner, patient_id, code=CONTRACTOR_CODE):
    result = determine(
        store,
        code,
        patient_id=patient_id,
        patient_store=patient_store,
        as_of=AS_OF,
        extraction_runner=runner,
    )
    assert isinstance(result, Determination)
    return result


def test_e1_is_met_on_all_seven_criteria(store, patient_store, runner, e1_patient):
    """Spec §6 E1: clean approval, all criteria met.

    US-5's story is the gap list, and the case that proves the list means
    something is the one where it is empty. If a `MET` determination still named
    gaps, the list would be noise a reviewer learns to skip.
    """
    determination = _determine_case(store, patient_store, runner, e1_patient)
    assert determination.outcome is DeterminationOutcome.MET
    assert all(
        r.verdict is CriterionVerdict.MET for r in determination.criterion_results
    ), [(r.criterion_id, r.verdict.value) for r in determination.criterion_results]
    assert determination.gap_list == []


def test_e1s_every_verdict_cites_a_span_that_slices_back(
    store, patient_store, runner, e1_patient
):
    """Article III on the assembled artifact, not just on the pieces.

    Each criterion's spans are validated against a real index built from the
    documents they name — the FHIR bundle for (a) and (b), the note for c1–c5 —
    so a span that survived construction and points at nothing fails here.
    """
    determination = _determine_case(store, patient_store, runner, e1_patient)

    # One accessor, because T-64 made the patient plane one document namespace
    # (D65): `get_document` resolves a bundle filename and a note id alike, so
    # this loop never asks which read served a span. The two-read version that
    # used to sit here — notes first, then bundles for whatever was left — is
    # what the port now owns.
    index = DocumentIndex()
    wanted = {
        span.document_id
        for result in determination.criterion_results
        for span in result.spans
    }
    for document_id in wanted:
        index.add(patient_store.get_document(document_id))

    checked = 0
    for result in determination.criterion_results:
        assert result.spans, f"{result.criterion_id} is MET with no span (REQ-5)"
        for span in result.spans:
            assert validate(span, index), f"{result.criterion_id}: empty slice"
            checked += 1
    assert checked >= 7, "the gate is vacuous; nothing was validated"


def test_e8_abstains_and_says_what_to_go_collect(
    store, patient_store, runner, e8_patient
):
    """Spec §6 E8 — the refusal test, and US-5's actual product.

    The note claims a completed program and documents no visit behind it. The
    honest answer is not `NOT_MET`, which would tell Sam the chart failed, but
    `INSUFFICIENT_EVIDENCE` with `UNSUBSTANTIATED_ASSERTION`, which tells her to
    go find the visit notes behind the claim. Two different next actions, and
    D12 refused to collapse them.
    """
    determination = _determine_case(store, patient_store, runner, e8_patient)
    assert determination.outcome is DeterminationOutcome.INSUFFICIENT_EVIDENCE

    by_id = {r.criterion_id: r for r in determination.criterion_results}
    assert by_id["c1"].verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert by_id["c1"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION
    assert by_id["c3"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION
    assert by_id["c1"].spans == [], "an abstention cites nothing (REQ-5)"


def test_e8s_gap_list_distinguishes_its_reasons_by_reason_not_prose(
    store, patient_store, runner, e8_patient
):
    """REQ-21 and REQ-31 together. This chart produces two different gaps —
    `UNSUBSTANTIATED_ASSERTION` at c1 and c3, `SOURCE_CONFLICT` at (a), because
    this patient is also E10b — and Sam's next action differs for each. A gap list
    that expressed the difference only in `detail` prose would be a gap list
    nothing can group, count, or route.
    """
    determination = _determine_case(store, patient_store, runner, e8_patient)
    gaps = {g.criterion_id: g for g in determination.gap_list}
    assert set(gaps) == {"a", "c1", "c2", "c3", "c4", "c5"}
    assert gaps["a"].gap_reason is GapReason.SOURCE_CONFLICT
    assert gaps["c1"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION
    assert len({g.gap_reason for g in determination.gap_list}) >= 3, (
        "this chart has at least three distinct next actions and the list must "
        "carry them as values"
    )
    assert all(g.verdict is not CriterionVerdict.MET for g in determination.gap_list)


def test_a_recorded_discrepancy_is_never_also_a_gap(
    store, patient_store, runner, case_patients
):
    """REQ-39, on the case that produces one. E10's chart disagrees by 5.77 BMI
    points on the same side of 35.0: the structured value stands, the criterion is
    untouched, and the disagreement is recorded advisorily.

    The disjointness is the assertion that matters. The gap list answers "what
    should Sam go collect", and two values that were both recorded is not
    something to go collect (D14) — so an entry appearing on both lists would be
    telling her to fetch a number the system already has.
    """
    determination = _determine_case(
        store, patient_store, runner, case_patients["E10"]
    )
    assert len(determination.discrepancies) == 1
    entry = determination.discrepancies[0]
    assert entry.criterion_id == "a"
    assert entry.fact == "bmi"
    assert entry.authoritative_value != entry.other_value

    by_id = {r.criterion_id: r for r in determination.criterion_results}
    assert by_id["a"].verdict is CriterionVerdict.MET, (
        "a discrepancy never changes a verdict (REQ-39)"
    )
    gap_ids = {g.criterion_id for g in determination.gap_list}
    assert "a" not in gap_ids
    assert not gap_ids & {d.criterion_id for d in determination.discrepancies}


def test_a_contractor_determination_cites_the_delegation_and_the_exercise(store):
    """D33's obligation, handed to T-19 by T-24 and asserted here.

    The NCD deliberately does not answer for 43775 — it non-covers laparoscopic
    sleeve gastrectomy only *"prior to June 27, 2012"* and delegates the question
    to the contractors after that. So a packet citing only the NCD cites a
    document that declines to decide, and the MAC's exercise of the delegation is
    the other half of the answer. Both quotes, and they are not the same quote.
    """
    ref = store.resolve(CONTRACTOR_CODE)
    assert ref is not None
    citations = contractor_citations(ref.coverage_claim)
    assert len(citations) == 2, (
        "a contractor-determined code needs both the delegation and the MAC's "
        "exercise of it, never the NCD alone (REQ-42, D33)"
    )
    delegation, exercise = citations
    assert delegation.quote != exercise.quote
    # Which field holds which half differs by coverage status: for a delegated
    # procedure the delegation *is* the claim, and there is nothing to scope.
    # E3's shape is the other one, where a bullet needs a sentence to become a
    # denial — `tests/test_e3_code.py` asserts that half.
    assert delegation.document_id == "ncd_100_1"
    assert "may determine coverage" in (delegation.quote or "")
    assert exercise.document_id == "a53028"
    assert ref.coverage_claim.scoping_quote is None


def test_the_contractor_citations_slice_back_out_of_the_corpus(store):
    """Article III: both halves are spans into hashed documents, not prose."""
    ref = store.resolve(CONTRACTOR_CODE)
    assert ref is not None
    index = DocumentIndex()
    for document_id in ("ncd_100_1", "a53028"):
        index.add(store.get_document(document_id))
    for span in contractor_citations(ref.coverage_claim):
        assert validate(span, index) == span.quote


def test_the_aggregator_reaches_no_store_and_no_model():
    """REQ-19's "in Python" as an import-graph fact.

    A module that could see the verdicts *and* reach a model could recombine
    them, and recombination is the last place a hallucination can flip an outcome
    after every span has already been checked. Asserted on the AST rather than the
    text, so prose describing the boundary cannot trip it.
    """
    import ast

    source = (REPO_ROOT / "pa_agent" / "aggregate.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"__future__", "collections", "pa_agent.contracts"}, (
        f"aggregate.py imports {sorted(imported)}; the aggregator sees every "
        "verdict and must reach neither a store nor a model"
    )
