"""T-33 — source reconciliation for criterion (a) (REQ-34, REQ-39, REQ-31).

Runs against **real committed data**: criterion (a) is evaluated on the FHIR
bundles, and the note side comes from T-15/T-60's recorded extraction. No model
call anywhere (the recording is read from disk), and the tolerance is read from
the criteria tree rather than written here — a test carrying its own copy of a
policy constant is testing itself (D51).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
    GapReason,
    Observation,
    WmEvent,
)
from pa_agent.criteria import evaluate_criterion_a, most_recent_bmi
from pa_agent.reconcile import note_bmi, reconcile_bmi
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "eval" / "extraction" / "results.json"
AS_OF = date(2026, 9, 1)

#: Which patient each E10-family case lives on, per T-06's manifests.
CASE_PATIENTS = {
    "E10": "a2e49f37-80ef-906b-5c85-b7b10e1179c1",
    "E10b": "bc6748d3-3a0f-9734-7730-4518f5b268fb",
    "E10c": "afdcee59-dfdd-4bc5-37f1-cf7f909ede3d",
}


@pytest.fixture(scope="module")
def tree():
    return LocalPolicyStore().get_tree("ncd-100.1-jf-v1")


@pytest.fixture(scope="module")
def fact(tree):
    return tree.reconciled_fact("bmi")


@pytest.fixture(scope="module")
def patients():
    return LocalPatientStore()


@pytest.fixture(scope="module")
def extracted() -> dict[str, dict]:
    """Case id -> the note side T-15/T-60 actually recorded."""
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    by_case: dict[str, dict] = {}
    for record in results["notes"]:
        payload = {
            "events": [
                WmEvent(
                    event_date=date.fromisoformat(e["date"]),
                    span=EvidenceSpan.model_validate(e["span"]),
                    bmi=e["bmi"],
                    bmi_span=(
                        EvidenceSpan.model_validate(e["bmi_span"])
                        if e["bmi_span"] else None
                    ),
                    diet_documented=e["diet_documented"],
                    diet_span=(
                        EvidenceSpan.model_validate(e["diet_span"])
                        if e["diet_span"] else None
                    ),
                    activity_documented=e["activity_documented"],
                    activity_span=(
                        EvidenceSpan.model_validate(e["activity_span"])
                        if e["activity_span"] else None
                    ),
                )
                for e in record["events"]
            ],
            "current_bmi": record.get("current_bmi"),
            "current_bmi_span": (
                EvidenceSpan.model_validate(record["current_bmi_span"])
                if record.get("current_bmi_span") else None
            ),
        }
        for case in record.get("cases", []):
            by_case[case] = payload
    return by_case


def _reconciled(case, tree, fact, patients, extracted) -> CriterionResult:
    """Criterion (a) on the real bundle, then reconciled against the real note."""
    patient = CASE_PATIENTS[case]
    observations = patients.get_observations(patient)
    produced = evaluate_criterion_a(tree.criterion("a"), observations, AS_OF)
    note = extracted[case]
    return reconcile_bmi(
        fact, tree.criterion("a"), produced, observations,
        note["events"], note["current_bmi"], note["current_bmi_span"],
    )


# --------------------------------------------------------------------------
# The three cases spec §6 labels
# --------------------------------------------------------------------------


def test_e10_keeps_its_verdict_and_records_one_discrepancy(
    tree, fact, patients, extracted
):
    """Structured 39.23 against note 45.0 — both above 35.0, gap 5.77."""
    result = _reconciled("E10", tree, fact, patients, extracted)
    assert result.verdict is CriterionVerdict.MET, (
        "REQ-34: the structured value is authoritative, so a same-side "
        "disagreement never changes the verdict"
    )
    assert len(result.discrepancies) == 1
    entry = result.discrepancies[0]
    assert entry.authoritative_value == pytest.approx(39.23)
    assert entry.other_value == pytest.approx(45.0)
    assert entry.criterion_id == "a" and entry.fact == "bmi"


def test_e10c_records_no_discrepancy(tree, fact, patients, extracted):
    """Structured 37.65 against note 37.6 — a gap of 0.05, below tolerance.
    D14's rounding artifact: listing it beside E10's 5.77 trains Sam to ignore
    the list."""
    result = _reconciled("E10c", tree, fact, patients, extracted)
    assert result.verdict is CriterionVerdict.MET
    assert result.discrepancies == []


def test_e10b_resolves_source_conflict(tree, fact, patients, extracted):
    """Structured 34.6 against note 36.2 — opposite sides of 35.0. The
    criterion cannot be answered from sources that contradict each other across
    the threshold, and magnitude is irrelevant to that."""
    result = _reconciled("E10b", tree, fact, patients, extracted)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.SOURCE_CONFLICT
    assert result.spans == [], (
        "an abstention cites nothing (REQ-5); citing one of two contradictory "
        "values would present it as the finding"
    )


def test_e10b_is_a_downgrade_not_an_original_abstention(
    tree, fact, patients, extracted
):
    """The point of REQ-34's second branch: criterion (a) produced a verdict on
    its own, and reconciliation took it away. If (a) had abstained anyway this
    case would prove nothing."""
    observations = patients.get_observations(CASE_PATIENTS["E10b"])
    produced = evaluate_criterion_a(tree.criterion("a"), observations, AS_OF)
    assert produced.verdict is CriterionVerdict.NOT_MET, (
        f"(a) alone produced {produced.verdict}; E10b tests a downgrade"
    )
    assert _reconciled("E10b", tree, fact, patients, extracted).verdict is (
        CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


# --------------------------------------------------------------------------
# REQ-39: advisory, and never a gap
# --------------------------------------------------------------------------


def test_no_discrepancy_ever_reaches_the_gap_list(tree, fact, patients, extracted):
    """D14 kept these off the gap list because that list answers exactly one
    question — what should Sam go collect — and a disagreement between two
    recorded values is not something to go collect."""
    for case in CASE_PATIENTS:
        result = _reconciled(case, tree, fact, patients, extracted)
        determination = Determination(
            patient_id=CASE_PATIENTS[case],
            procedure_code="43775",
            policy_version_id=tree.policy_version_id,
            outcome=(
                DeterminationOutcome.MET
                if result.verdict is CriterionVerdict.MET
                else DeterminationOutcome.INSUFFICIENT_EVIDENCE
            ),
            criterion_results=[result],
            metrics=[],
        )
        gap_ids = {g.criterion_id for g in determination.gap_list}
        if result.verdict is CriterionVerdict.MET:
            assert not gap_ids, f"{case}: a discrepancy put (a) on the gap list"
        else:
            # E10b is on the gap list because its *verdict* changed, and it
            # carries SOURCE_CONFLICT rather than a discrepancy.
            assert {g.gap_reason for g in determination.gap_list} == {
                GapReason.SOURCE_CONFLICT
            }


def test_a_discrepancy_never_changes_a_verdict(tree, fact, patients, extracted):
    """REQ-39 in the strongest form available: for every case, the verdict after
    reconciliation differs from the verdict before it only when a conflict
    fired, never because an entry was recorded."""
    for case in CASE_PATIENTS:
        observations = patients.get_observations(CASE_PATIENTS[case])
        before = evaluate_criterion_a(tree.criterion("a"), observations, AS_OF)
        after = _reconciled(case, tree, fact, patients, extracted)
        if after.discrepancies:
            assert after.verdict is before.verdict


# --------------------------------------------------------------------------
# The tolerance is the tree's, and the boundary is inclusive
# --------------------------------------------------------------------------


def test_the_tolerance_is_read_from_the_tree_not_written_here(fact):
    assert fact.tolerance() == 1.0
    assert fact.discrepancy_tolerance.provisional is False, (
        "T-33 consuming a provisional constant would be supplying its own "
        "default, which §9 forbade in writing"
    )


def _synthetic(structured: float, note: float, tree, fact):
    """One structured observation and one note event, both spanned."""
    span = EvidenceSpan(document_id="bundle", char_start=0, char_end=4, quote="0000")
    note_span = EvidenceSpan(document_id="note", char_start=0, char_end=4, quote="1111")
    obs = [Observation(code="39156-5", value=structured, unit="kg/m2",
                       effective_date=date(2026, 6, 1), span=span)]
    produced = evaluate_criterion_a(tree.criterion("a"), obs, AS_OF)
    events = [WmEvent(event_date=date(2026, 6, 1), span=note_span,
                      bmi=note, bmi_span=note_span)]
    return reconcile_bmi(fact, tree.criterion("a"), produced, obs, events)


def test_a_gap_exactly_at_tolerance_is_recorded(tree, fact):
    """REQ-39 says 'at or beyond'. The boundary is the one value a reader will
    assume either way, so it is pinned."""
    assert len(_synthetic(40.0, 41.0, tree, fact).discrepancies) == 1


def test_a_gap_just_below_tolerance_is_not(tree, fact):
    assert _synthetic(40.0, 40.99, tree, fact).discrepancies == []


def test_the_tolerance_actually_governs(tree, fact):
    """If the constant moved, behaviour must move with it. Otherwise the number
    in the tree is decoration and the real threshold is hidden in code."""
    loosened = fact.model_copy(
        update={
            "discrepancy_tolerance": fact.discrepancy_tolerance.model_copy(
                update={"value": 10.0}
            )
        }
    )
    span = EvidenceSpan(document_id="bundle", char_start=0, char_end=4, quote="0000")
    obs = [Observation(code="39156-5", value=40.0, unit="kg/m2",
                       effective_date=date(2026, 6, 1), span=span)]
    produced = evaluate_criterion_a(tree.criterion("a"), obs, AS_OF)
    events = [WmEvent(event_date=date(2026, 6, 1), span=span, bmi=45.0, bmi_span=span)]
    assert reconcile_bmi(loosened, tree.criterion("a"), produced, obs, events
                         ).discrepancies == [], "a gap of 5.0 under a tolerance of 10.0"


# --------------------------------------------------------------------------
# Which note value, and the things that produce none
# --------------------------------------------------------------------------


def test_the_note_level_bmi_wins_over_an_encounter(extracted):
    """T-60's rule (D50): current_bmi is the patient's BMI now, an event BMI is
    dated in the past."""
    span = EvidenceSpan(document_id="n", char_start=0, char_end=4, quote="1111")
    events = [WmEvent(event_date=date(2026, 8, 1), span=span, bmi=30.0, bmi_span=span)]
    assert note_bmi(events, 44.0, span) == (44.0, span)


def test_an_unanchored_note_bmi_is_no_note_bmi():
    """D15: a BMI nobody can cite is not a documented BMI, so it cannot found a
    discrepancy or a conflict."""
    assert note_bmi([], 44.0, None) is None


def test_a_note_with_no_bmi_leaves_the_verdict_alone(tree, fact, patients):
    observations = patients.get_observations(CASE_PATIENTS["E10"])
    produced = evaluate_criterion_a(tree.criterion("a"), observations, AS_OF)
    assert reconcile_bmi(fact, tree.criterion("a"), produced, observations, []) == produced


def test_an_abstention_is_not_reconciled(tree, fact):
    """(a) found no structured BMI at all; there is no verdict for a note value
    to contradict, and the abstention already says what to go collect."""
    produced = evaluate_criterion_a(tree.criterion("a"), [], AS_OF)
    assert produced.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    span = EvidenceSpan(document_id="n", char_start=0, char_end=4, quote="1111")
    events = [WmEvent(event_date=date(2026, 6, 1), span=span, bmi=45.0, bmi_span=span)]
    out = reconcile_bmi(fact, tree.criterion("a"), produced, [], events)
    assert out is produced
    assert out.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_reconciliation_agrees_with_criterion_a_about_which_bmi_is_authoritative(
    tree, patients
):
    """Two copies of 'most recent BMI' would be free to disagree, and the
    disagreement would surface as a discrepancy against a value no verdict was
    based on."""
    for patient in CASE_PATIENTS.values():
        observations = patients.get_observations(patient)
        produced = evaluate_criterion_a(tree.criterion("a"), observations, AS_OF)
        latest = most_recent_bmi(observations)
        assert latest is not None
        assert str(latest.value) in (produced.detail or "")


# --------------------------------------------------------------------------
# Article II: no model, no clock
# --------------------------------------------------------------------------


def test_reconciliation_spends_no_model_call():
    assert "google.adk" not in sys.modules
    assert "google.genai" not in sys.modules


def test_the_module_imports_no_model_and_no_store():
    """Asserted on the AST rather than on the text, so prose describing the
    boundary cannot trip it and a real import cannot hide in a docstring."""
    import ast

    source = (REPO_ROOT / "pa_agent" / "reconcile.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {
        m for m in imported
        if m.startswith(("google", "pa_agent.stores", "pa_agent.extraction"))
    }
    assert not forbidden, (
        f"reconcile.py imports {sorted(forbidden)}. It takes values and spans "
        "and reaches nothing itself: no model, and no store (REQ-41)."
    )
    assert imported <= {"__future__", "pa_agent.contracts", "pa_agent.criteria"}, (
        f"reconcile.py grew an import: {sorted(imported)}"
    )
