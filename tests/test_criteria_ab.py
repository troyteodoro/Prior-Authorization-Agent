"""T-13 — criteria (a) and (b), deterministic, zero model calls (D40).

The real-patient cases run against the six committed bundles through the
ports, and every span a verdict carries is validated by T-11 against the
bundle document itself — Article III on the patient plane. The boundary and
window cases run synthetically, because the population cannot be regenerated
to order (D35) and a boundary case that depends on a lucky draw is not a test.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import Condition, CriterionVerdict, EvidenceSpan, Observation
from pa_agent.criteria import BMI_LOINC, evaluate_criterion_a, evaluate_criterion_b
from pa_agent.index import DocumentIndex
from pa_agent.spans import validate
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
VALUESET_PATH = REPO_ROOT / "data" / "policies" / "value_sets" / "obesity_comorbidities.json"
PATIENT_MANIFEST = REPO_ROOT / "data" / "patients" / "manifest.json"
CRITERIA_MODULE = REPO_ROOT / "pa_agent" / "criteria.py"

AS_OF = date(2026, 9, 1)  # the population's recorded reference date (D35)
SNOMED_T2DM = "44054006"
SYNTH_SPAN = EvidenceSpan(document_id="synthetic", char_start=0, char_end=10)


@pytest.fixture(scope="module")
def criterion_a():
    return LocalPolicyStore().get_tree("ncd-100.1-jf-v1").criterion("a")


@pytest.fixture(scope="module")
def criterion_b():
    return LocalPolicyStore().get_tree("ncd-100.1-jf-v1").criterion("b")


@pytest.fixture(scope="module")
def value_set() -> frozenset[str]:
    data = json.loads(VALUESET_PATH.read_text(encoding="utf-8"))
    return frozenset(e["code"] for e in data["entries"])


@pytest.fixture(scope="module")
def patients() -> list[dict]:
    return json.loads(PATIENT_MANIFEST.read_text(encoding="utf-8"))["bundles"]


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


def _bmi_obs(value: float, when: date) -> Observation:
    return Observation(
        code=BMI_LOINC, value=value, effective_date=when, span=SYNTH_SPAN
    )


# --------------------------------------------------------------------------
# Criterion (a) — the boundary, the window, and the three verdicts
# --------------------------------------------------------------------------


def test_bmi_exactly_35_is_met(criterion_a):
    """E12: the boundary is inclusive."""
    result = evaluate_criterion_a(
        criterion_a, [_bmi_obs(35.0, date(2026, 8, 1))], AS_OF
    )
    assert result.verdict is CriterionVerdict.MET


def test_bmi_just_below_35_is_not_met(criterion_a):
    result = evaluate_criterion_a(
        criterion_a, [_bmi_obs(34.99, date(2026, 8, 1))], AS_OF
    )
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans, "a NOT_MET cites the observation that decided it (REQ-5)"


def test_the_window_comes_from_the_tree_not_a_constant(criterion_a):
    """REQ-32's rule applied to (a): 11 months old is within D40's 12-month
    window, 13 months old is outside it. A hardcoded window moves both."""
    assert criterion_a.require("lookback_months") == 12
    inside = evaluate_criterion_a(
        criterion_a, [_bmi_obs(40.0, date(2025, 10, 1))], AS_OF
    )
    assert inside.verdict is CriterionVerdict.MET
    outside = evaluate_criterion_a(
        criterion_a, [_bmi_obs(40.0, date(2025, 8, 1))], AS_OF
    )
    assert outside.verdict is CriterionVerdict.NOT_MET


def test_a_stale_bmi_is_not_met_not_insufficient(criterion_a):
    """REQ-16: evidence present but outside a required window is NOT_MET, and
    the verdict cites the stale observation rather than pretending the chart
    is empty. A 40.0 from two years ago does not approve anyone."""
    result = evaluate_criterion_a(
        criterion_a, [_bmi_obs(40.0, date(2024, 3, 15))], AS_OF
    )
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans
    assert "outside" in result.detail


def test_no_bmi_anywhere_is_insufficient_evidence(criterion_a):
    """Article IV: evidence that cannot be found, distinct from evidence that
    fails. No span — an abstention cites nothing (REQ-5)."""
    result = evaluate_criterion_a(criterion_a, [], AS_OF)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert not result.spans

    non_bmi = Observation(
        code="8867-4", value=72.0, effective_date=date(2026, 8, 1), span=SYNTH_SPAN
    )
    result = evaluate_criterion_a(criterion_a, [non_bmi], AS_OF)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_the_most_recent_bmi_decides_not_the_highest(criterion_a):
    """A 36 last year and a 34 last month is a 34."""
    result = evaluate_criterion_a(
        criterion_a,
        [_bmi_obs(36.0, date(2025, 11, 1)), _bmi_obs(34.0, date(2026, 8, 1))],
        AS_OF,
    )
    assert result.verdict is CriterionVerdict.NOT_MET


# --------------------------------------------------------------------------
# Criterion (a) — against the real population, spans checked by T-11
# --------------------------------------------------------------------------


def _patient_index(store: LocalPatientStore, filename: str) -> DocumentIndex:
    index = DocumentIndex()
    index.add(store.get_document(filename))
    return index


def test_the_real_population_adjudicates_as_the_manifest_says(
    criterion_a, store, patients
):
    """Every committed patient gets the verdict their recorded latest BMI and
    its date imply, and every verdict's span survives T-11 against the bundle
    document, slicing to a BMI observation resource."""
    checked_verdicts = set()
    for record in patients:
        observations = store.get_observations(record["patient_id"])
        result = evaluate_criterion_a(criterion_a, observations, AS_OF)

        obs_date = date.fromisoformat(record["latest_bmi_date"][:10])
        months_old = (AS_OF.year - obs_date.year) * 12 + (AS_OF.month - obs_date.month)
        if AS_OF.day < obs_date.day:
            months_old -= 1
        if months_old >= 12:
            expected = CriterionVerdict.NOT_MET  # stale (REQ-16)
        elif record["latest_bmi"] >= 35.0:
            expected = CriterionVerdict.MET
        else:
            expected = CriterionVerdict.NOT_MET
        assert result.verdict is expected, record["filename"]
        checked_verdicts.add((result.verdict, months_old >= 12))

        sliced = validate(
            result.spans[0], _patient_index(store, record["filename"])
        )
        assert BMI_LOINC in sliced, "the span does not cite a BMI observation"
        assert str(record["latest_bmi"]) in sliced, (
            "the span cites a different observation than the one that decided"
        )
    assert (CriterionVerdict.MET, False) in checked_verdicts
    assert (CriterionVerdict.NOT_MET, True) in checked_verdicts, (
        "the population no longer exercises the stale branch; re-check D35"
    )


# --------------------------------------------------------------------------
# Criterion (b) — intersection, active-only, and the missing verdict
# --------------------------------------------------------------------------


def test_the_t2dm_patient_is_met_with_a_citable_condition(
    criterion_b, store, patients, value_set
):
    record = next(r for r in patients if r["has_active_t2dm"])
    conditions = store.get_conditions(record["patient_id"])
    result = evaluate_criterion_b(criterion_b, conditions, value_set)
    assert result.verdict is CriterionVerdict.MET
    sliced = validate(result.spans[0], _patient_index(store, record["filename"]))
    assert SNOMED_T2DM in sliced or "59621000" in sliced


def test_a_patient_with_no_valueset_comorbidity_abstains(
    criterion_b, store, patients, value_set
):
    """Never NOT_MET: the chart failed to document a comorbidity, which is not
    the same claim as the patient having none (D40, Art. IV)."""
    abstained = 0
    for record in patients:
        conditions = store.get_conditions(record["patient_id"])
        result = evaluate_criterion_b(criterion_b, conditions, value_set)
        assert result.verdict in (
            CriterionVerdict.MET,
            CriterionVerdict.INSUFFICIENT_EVIDENCE,
        ), "criterion (b) has no NOT_MET (D40)"
        if result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE:
            assert not result.spans
            abstained += 1
    assert abstained, "every patient qualified; the abstention branch went untested"


def test_a_resolved_comorbidity_does_not_count(criterion_b, value_set):
    """The false MET the clinical_status field exists to prevent (D39)."""
    resolved = Condition(
        code=SNOMED_T2DM, clinical_status="resolved", span=SYNTH_SPAN
    )
    result = evaluate_criterion_b(criterion_b, [resolved], value_set)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE

    active = Condition(code=SNOMED_T2DM, clinical_status="active", span=SYNTH_SPAN)
    result = evaluate_criterion_b(criterion_b, [active], value_set)
    assert result.verdict is CriterionVerdict.MET


def test_a_condition_outside_the_value_set_does_not_count(criterion_b, value_set):
    """Prediabetes is in the population and not in Group 1."""
    prediabetes = Condition(
        code="714628002", clinical_status="active", span=SYNTH_SPAN
    )
    result = evaluate_criterion_b(criterion_b, [prediabetes], value_set)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------
# Zero model calls
# --------------------------------------------------------------------------


def test_the_criteria_module_makes_no_model_call():
    """Asserted on the imports: nothing model-shaped, no pin, no store, no
    file API is reachable from the module that adjudicates (Art. II).

    `dataclasses` joined the list when T-16 added `QualifyingRun`; the set is
    asserted exactly so a new import is a decision someone makes rather than
    one that arrives unnoticed."""
    tree = ast.parse(CRITERIA_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "__future__", "dataclasses", "datetime", "pa_agent.contracts",
    }, (
        f"pa_agent/criteria.py imports {sorted(imported)}; criteria are "
        "arithmetic over contracts and nothing else (Art. II, D40)"
    )
