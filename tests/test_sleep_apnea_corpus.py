"""T-108 — the fourth practice's committed charts, notes and recordings (D150).

`tests/test_sleep_apnea_tree.py` holds the arithmetic on charts written there;
this file holds what is committed. Six Synthea charts from the recorded Iowa
run, two notes each, the sleep recording that reads them, and the determination
each row labels — replayed end to end for zero model calls.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriterionVerdict,
    DeterminationAborted,
    ErrorCode,
    FactKind,
)
from pa_agent.determination import determine
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

from conftest import AcceptAllVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
POPULATION = REPO_ROOT / "data" / "patients" / "manifest.json"
EXTRACTION = REPO_ROOT / "eval" / "extraction"
AS_OF = date(2026, 9, 1)

#: Row -> (b's verdict, the overall outcome). `a` is MET on every chart,
#: because each chart's own record has an assessment before its study (D150).
EXPECTED = {
    "OSA1": ("MET", "INSUFFICIENT_EVIDENCE"),
    "OSA2": ("MET", "INSUFFICIENT_EVIDENCE"),
    "OSA3": ("MET", "INSUFFICIENT_EVIDENCE"),
    "OSA4": ("NOT_MET", "NOT_MET"),
    "OSA5": ("INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"),
    "OSA6": ("NOT_MET", "NOT_MET"),
}


@pytest.fixture(scope="module")
def manifests() -> dict[str, dict]:
    bodies = [json.loads(p.read_text(encoding="utf-8")) for p in MANIFEST_DIR.glob("*.json")]
    return {b["cases"][0]: b for b in bodies if b.get("sleep_tests")}


@pytest.fixture(scope="module")
def population() -> dict[str, dict]:
    records = json.loads(POPULATION.read_text(encoding="utf-8"))["bundles"]
    return {r["patient_id"]: r for r in records}


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


def _runner(*names: str) -> RecordedExtractionRunner:
    notes = []
    for name in names:
        notes += json.loads((EXTRACTION / name).read_text(encoding="utf-8"))["notes"]
    return RecordedExtractionRunner.from_records(notes)


def test_six_charts_one_row_each_all_in_the_sleep_cohort(manifests, population):
    assert sorted(manifests) == sorted(EXPECTED)
    for case, body in manifests.items():
        record = population[body["patient_id"]]
        assert record["cohort"] == "sleep_apnea", case
        assert record["has_active_osa"], case
        assert body["documents"] == ["chart_note_1.txt", "chart_note_2.txt"]


def test_every_note_date_is_the_charts_own(manifests, population):
    """The study is the chart's 82808001 procedure and the evaluation its
    latest 103750000 assessment strictly before it — the generator's dates,
    never the compiler's (D150)."""
    for case, body in manifests.items():
        record = population[body["patient_id"]]
        (test,) = body["sleep_tests"]
        (evaluation,) = body["sleep_evaluations"]
        study = record["sleep_study_dates"][-1][:10]
        assert test["date"] == study, case
        prior = [a[:10] for a in record["sleep_assessment_dates"] if a[:10] < study]
        assert evaluation["date"] == prior[-1], case


def test_the_split_the_rows_rest_on_is_the_charts(manifests, population):
    """OSA3's band is met by a coded comorbidity alone and OSA2's by its note
    alone; OSA5 has neither. A chart crossing sides would move a label."""
    coded = {c: bool(population[b["patient_id"]]["active_osa_comorbidity_codes"]) for c, b in manifests.items()}
    assert coded["OSA3"] and not coded["OSA2"] and not coded["OSA5"]
    assert manifests["OSA2"]["sleep_findings"] and not manifests["OSA3"]["sleep_findings"]
    assert not manifests["OSA5"]["sleep_findings"] and manifests["OSA5"]["denied_findings"]


def test_one_fact_per_document_and_no_derived_number_in_any_note(manifests, store):
    """The consultation carries the evaluation and the report the test (D104);
    the report states the index and the time and never their product, because
    the event count is Python's to compute (Art. II)."""
    for case, body in manifests.items():
        consult = store.get_document(f"{body['patient_id']}/chart_note_1.txt").text
        report = store.get_document(f"{body['patient_id']}/chart_note_2.txt").text
        assert "SLEEP MEDICINE CONSULTATION" in consult and "SLEEP STUDY REPORT" not in consult
        assert "SLEEP STUDY REPORT" in report and "CONSULTATION" not in report
        assert not re.search(r"total (respiratory )?events", report, re.IGNORECASE), case
        assert "weight management" not in (consult + report).lower(), case


def test_the_sleep_recordings_agree_with_every_declared_fact():
    """Both tiers, as committed: every evaluation, test, index and hour the
    manifests declare was read back; nothing was read off a trap or a denial."""
    for name in ("sleep_apnea_workup.json", "sleep_apnea_workup_vertex.json"):
        figures = json.loads((EXTRACTION / name).read_text(encoding="utf-8"))["aggregate"]
        assert figures["notes"] == 12, name
        assert figures["matched_evaluations"] == figures["labeled_evaluations"] == 6
        assert figures["extracted_evaluations"] == 6, "the telephone contact is not an evaluation"
        assert figures["matched_tests"] == figures["extracted_tests"] == 6
        assert figures["value_agreements"] == figures["value_total"] == 12
        assert figures["findings_matched"] == figures["extracted_findings"] == 1
        assert figures["denied_findings_extracted"] == 0
        assert figures["traps_extracted"] == 0
        assert figures["spans_anchored"] == figures["spans_emitted"]


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_each_row_replays_end_to_end_for_zero_model_calls(case, manifests, store):
    patient_id = manifests[case]["patient_id"]
    determination = determine(
        LocalPolicyStore(), "E0601", patient_id=patient_id, patient_store=store,
        as_of=AS_OF, extraction_runner=_runner("sleep_apnea_workup.json"),
        verifier=AcceptAllVerifier(),
    )
    verdicts = {r.criterion_id: r.verdict.value for r in determination.criterion_results}
    b, outcome = EXPECTED[case]
    assert verdicts == {
        "a": "MET", "b": b,
        "c": "INSUFFICIENT_EVIDENCE", "d": "INSUFFICIENT_EVIDENCE",
    }, case
    assert determination.outcome.value == outcome
    assert determination.policy_version_id == "pap-osa-dme-jd-v1"


def test_a_sleep_chart_read_from_the_weight_management_recording_is_an_error(manifests, store):
    """The request D147 exists for, on a committed chart: no payload answers
    this kind, so both note criteria are `ERROR` — never an abstention that
    would say the notes are silent (D90, D149)."""
    patient_id = manifests["OSA1"]["patient_id"]
    with pytest.raises(DeterminationAborted) as caught:
        determine(
            LocalPolicyStore(), "E0601", patient_id=patient_id, patient_store=store,
            as_of=AS_OF, extraction_runner=_runner("results.json"),
            verifier=AcceptAllVerifier(),
        )
    results = caught.value.results
    assert {r.criterion_id for r in results} == {"a", "b"}
    assert {r.verdict for r in results} == {CriterionVerdict.ERROR}
    assert {r.error_code for r in results} == {ErrorCode.SCHEMA_INVALID}


def test_the_bariatric_trees_never_read_a_sleep_note(store):
    """The reverse: the sleep recording holds no weight-management payload,
    and a weight-management request for a sleep note is a mismatch."""
    runner = _runner("sleep_apnea_workup.json")
    document = store.get_notes(
        json.loads((EXTRACTION / "sleep_apnea_workup.json").read_text(encoding="utf-8"))["notes"][0][
            "document_id"
        ].split("/")[0]
    )[0]
    with pytest.raises(Exception, match="SCHEMA_MISMATCH"):
        runner.run(document.document_id, document.text, FactKind.WEIGHT_MANAGEMENT)
