"""T-14 — short-circuit sc2: E2 returns NOT_COVERED, zero model calls (D41).

The end-to-end case runs on a real patient: the population's T2DM patient
carries a sub-35 BMI, so at an `as_of` inside that BMI's window the request is
exactly E2's shape. Both citations on the artifact are validated by T-11 —
the policy claim against the NCD, the patient evidence against the bundle.
The non-firing branches (stale BMI, high BMI, no T2DM, contractor scope) each
fall through to the criteria chain and are asserted to reach a determination that
is **not** `NOT_COVERED`.

*Updated by D62.* Those four branches used to assert the `NotImplementedError`
citing T-19 — the strongest statement available while the covered path was
unbuilt, and the shape D31 warned about: a test that passes because a feature is
missing keeps passing for the wrong reason the moment it lands. T-18 and T-19
built the path, so each branch now asserts the thing it always meant, which is
that sc2 declined to deny this chart.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    Condition,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
    Observation,
)
from pa_agent.criteria import BMI_LOINC, evaluate_sc2
from pa_agent.determination import determine
from pa_agent.index import DocumentIndex
from pa_agent.spans import validate
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENT_MANIFEST = REPO_ROOT / "data" / "patients" / "manifest.json"
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"

COVERED_CODE = "43644"  # laparoscopic RYGB, nationally covered
CONTRACTOR_CODE = "43775"
NON_COVERED_CODE = "43842"
SNOMED_T2DM = "44054006"

# The T2DM patient's most recent BMI is 34.26 on 2024-03-15; this as_of sits
# inside that BMI's 12-month window, so the chart is E2's shape on this day.
E2_AS_OF = date(2024, 6, 1)
TODAY_ISH = date(2026, 9, 1)  # the same BMI is stale here

SYNTH_SPAN = EvidenceSpan(document_id="synthetic", char_start=0, char_end=10)


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def patients() -> list[dict]:
    return json.loads(PATIENT_MANIFEST.read_text(encoding="utf-8"))["bundles"]


@pytest.fixture(scope="module")
def t2dm_patient(patients) -> dict:
    return next(r for r in patients if r["has_active_t2dm"])


@pytest.fixture(scope="module")
def runner() -> RecordedExtractionRunner:
    """T-15's recording, replayed. The fall-through branches need to reach a
    real determination and this reaches one for nothing (REQ-52, D62)."""
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def exclusion(policy_store):
    tree = policy_store.get_tree("ncd-100.1-jf-v1")
    assert len(tree.categorical_exclusions) == 1
    return tree.categorical_exclusions[0]


# --------------------------------------------------------------------------
# E2, end to end on a real chart
# --------------------------------------------------------------------------


def test_e2_returns_not_covered_with_zero_model_calls(
    policy_store, patient_store, t2dm_patient
):
    result = determine(
        policy_store,
        COVERED_CODE,
        patient_id=t2dm_patient["patient_id"],
        patient_store=patient_store,
        as_of=E2_AS_OF,
    )
    assert isinstance(result, Determination)
    assert result.outcome is DeterminationOutcome.NOT_COVERED
    assert result.model_calls == 0 and result.metrics == []
    assert result.patient_id == t2dm_patient["patient_id"]
    assert result.policy_version_id == "ncd-100.1-jf-v1"


def test_e2s_both_citations_survive_t11(
    policy_store, patient_store, t2dm_patient
):
    """Article III on both sides: the rule from the NCD, the facts from the
    bundle — each sliced from its own hashed document."""
    result = determine(
        policy_store,
        COVERED_CODE,
        patient_id=t2dm_patient["patient_id"],
        patient_store=patient_store,
        as_of=E2_AS_OF,
    )
    policy_index = DocumentIndex()
    policy_index.add(policy_store.get_document("ncd_100_1"))
    claim_text = validate(result.coverage_claim, policy_index)
    assert "BMI less than 35" in claim_text
    assert "therefore are not covered" in claim_text

    patient_index = DocumentIndex()
    patient_index.add(patient_store.get_document(t2dm_patient["filename"]))
    evidence_texts = [validate(s, patient_index) for s in result.exclusion_evidence]
    assert any(BMI_LOINC in t for t in evidence_texts), "no BMI evidence cited"
    assert any(SNOMED_T2DM in t for t in evidence_texts), "no T2DM evidence cited"


def test_e2_is_distinguishable_from_a_criteria_failure(
    policy_store, patient_store, t2dm_patient
):
    """US-3's second bullet: Sam's next action differs, so the artifact must
    differ structurally — a categorical denial carries the exclusion's claim
    and evidence and no criterion verdicts at all."""
    result = determine(
        policy_store,
        COVERED_CODE,
        patient_id=t2dm_patient["patient_id"],
        patient_store=patient_store,
        as_of=E2_AS_OF,
    )
    assert result.coverage_claim is not None
    assert result.exclusion_evidence
    assert result.criterion_results == []
    assert result.gap_list == []


# --------------------------------------------------------------------------
# The branches that must NOT fire
# --------------------------------------------------------------------------


def _expect_fall_through(
    policy_store, patient_store, runner, code, patient_id, as_of
) -> Determination:
    """Assert sc2 declined, and return the determination the chain produced.

    "sc2 did not fire" and "the request was denied for some other reason" are
    different outcomes, and the distinction is the whole of D41: a categorical
    exclusion denies on a rule, while the criteria chain adjudicates evidence.
    Asserting the outcome is not `NOT_COVERED` separates them — and asserting
    `criterion_results` is non-empty proves the chain actually ran rather than
    returning some third empty thing.
    """
    determination = determine(
        policy_store,
        code,
        patient_id=patient_id,
        patient_store=patient_store,
        as_of=as_of,
        extraction_runner=runner,
    )
    assert isinstance(determination, Determination)
    assert determination.outcome is not DeterminationOutcome.NOT_COVERED, (
        f"{code} for {patient_id} at {as_of} was denied categorically; sc2 was "
        "not supposed to fire on this chart (D41)"
    )
    assert determination.criterion_results, (
        "no criterion results: the request did not reach the criteria chain"
    )
    assert determination.coverage_claim is None, (
        "a criteria determination carries no coverage_claim — that field is the "
        "denial's citation and belongs only to NOT_COVERED (D32)"
    )
    return determination


def test_a_stale_sub35_bmi_does_not_categorically_deny(
    policy_store, patient_store, runner, t2dm_patient
):
    """The same chart at today's date: the BMI is outside the window, so sc2
    declines to deny and the request falls through to the criteria chain —
    which handles staleness as NOT_MET (REQ-16), not as a categorical denial
    issued on evidence the approval path would refuse (D41)."""
    determination = _expect_fall_through(
        policy_store, patient_store, runner, COVERED_CODE,
        t2dm_patient["patient_id"], TODAY_ISH,
    )
    criterion_a = next(
        r for r in determination.criterion_results if r.criterion_id == "a"
    )
    assert criterion_a.verdict is CriterionVerdict.NOT_MET, (
        "REQ-16: evidence present but outside the window is NOT_MET. That is the "
        "answer sc2 declined to pre-empt, and it reads differently on the gap "
        "list from a categorical denial"
    )


def test_a_high_bmi_patient_is_not_excluded(
    policy_store, patient_store, runner, patients
):
    high = next(r for r in patients if r["latest_bmi"] >= 40)
    _expect_fall_through(
        policy_store, patient_store, runner, COVERED_CODE,
        high["patient_id"], TODAY_ISH,
    )


def test_a_sub35_patient_without_t2dm_is_not_excluded(
    policy_store, patient_store, runner, patients
):
    record = next(
        r for r in patients
        if r["latest_bmi"] < 35 and not r["has_active_t2dm"]
        and r["latest_bmi_date"] >= "2026"
    )
    _expect_fall_through(
        policy_store, patient_store, runner, COVERED_CODE,
        record["patient_id"], TODAY_ISH,
    )


def test_the_contractor_code_skips_sc2(
    policy_store, patient_store, runner, t2dm_patient
):
    """The 04/2009 exclusion predates the LSG delegation and never names LSG;
    a contractor-determined request proceeds to the MAC's criteria even for
    the chart that fires sc2 on a covered code (D41's scope rule).

    This is the sharpest of the four: the *same patient at the same as_of* is
    denied categorically on `COVERED_CODE` two tests above and adjudicated on
    evidence here. Nothing about the chart changed; the procedure's coverage
    status did.
    """
    _expect_fall_through(
        policy_store, patient_store, runner, CONTRACTOR_CODE,
        t2dm_patient["patient_id"], E2_AS_OF,
    )


def test_sc1_still_answers_first_for_a_non_covered_code(
    policy_store, patient_store, t2dm_patient
):
    result = determine(
        policy_store,
        NON_COVERED_CODE,
        patient_id=t2dm_patient["patient_id"],
        patient_store=patient_store,
        as_of=E2_AS_OF,
    )
    assert result.outcome is DeterminationOutcome.NOT_COVERED
    assert result.exclusion_evidence == [], "sc1's denial cites no patient facts"


# --------------------------------------------------------------------------
# The predicate itself, at the boundary
# --------------------------------------------------------------------------


def _obs(value: float, when: date) -> Observation:
    return Observation(code=BMI_LOINC, value=value, effective_date=when, span=SYNTH_SPAN)


T2DM_ACTIVE = Condition(code=SNOMED_T2DM, clinical_status="active", span=SYNTH_SPAN)


def test_bmi_exactly_35_does_not_fire(exclusion):
    """The exclusion is 'less than 35' (exclusive); coverage begins at 35.0
    (E12). Both read the same boundary from opposite sides."""
    match = evaluate_sc2(
        exclusion, [_obs(35.0, date(2026, 8, 1))], [T2DM_ACTIVE], TODAY_ISH, 12
    )
    assert match is None
    fired = evaluate_sc2(
        exclusion, [_obs(34.99, date(2026, 8, 1))], [T2DM_ACTIVE], TODAY_ISH, 12
    )
    assert fired is not None and fired.exclusion_id == "t2dm_bmi_under_35"


def test_resolved_t2dm_does_not_fire(exclusion):
    resolved = Condition(code=SNOMED_T2DM, clinical_status="resolved", span=SYNTH_SPAN)
    match = evaluate_sc2(
        exclusion, [_obs(33.0, date(2026, 8, 1))], [resolved], TODAY_ISH, 12
    )
    assert match is None


def test_the_most_recent_bmi_decides_the_exclusion(exclusion):
    """An old 33 under a newer 36 is a 36: no exclusion."""
    match = evaluate_sc2(
        exclusion,
        [_obs(33.0, date(2026, 1, 1)), _obs(36.0, date(2026, 8, 1))],
        [T2DM_ACTIVE],
        TODAY_ISH,
        12,
    )
    assert match is None


def test_uncitable_evidence_denies_nobody(exclusion):
    """A categorical denial nobody can check is not issued: spanless facts
    fall through to the criteria path instead of firing (D41, Art. III)."""
    spanless_obs = Observation(
        code=BMI_LOINC, value=33.0, effective_date=date(2026, 8, 1)
    )
    spanless_t2dm = Condition(code=SNOMED_T2DM, clinical_status="active")
    match = evaluate_sc2(
        exclusion, [spanless_obs], [spanless_t2dm], TODAY_ISH, 12
    )
    assert match is None
