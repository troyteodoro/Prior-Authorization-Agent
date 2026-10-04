"""T-109 — the fifth practice's committed charts, notes and recordings (D154).

`tests/test_knee_oa_tree.py` holds the arithmetic on charts written there;
this file holds what is committed. Seven Synthea charts from the recorded Iowa
run, two notes each, the knee recording that reads them, and the determination
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
CASES = REPO_ROOT / "eval" / "cases.json"
RECORDING = "knee_osteoarthritis_workup.json"

#: Row -> (a, b, d, the overall outcome), transcribed from D154 clause 10. c and
#: e are declared unclaimed and abstain on every chart.
EXPECTED = {
    "KNEE1": ("MET", "MET", "MET", "INSUFFICIENT_EVIDENCE"),
    "KNEE2": ("MET", "INSUFFICIENT_EVIDENCE", "MET", "INSUFFICIENT_EVIDENCE"),
    "KNEE3": ("MET", "MET", "NOT_MET", "NOT_MET"),
    "KNEE4": ("INSUFFICIENT_EVIDENCE", "MET", "MET", "INSUFFICIENT_EVIDENCE"),
    "KNEE5": ("MET", "INSUFFICIENT_EVIDENCE", "MET", "INSUFFICIENT_EVIDENCE"),
    "KNEE6": ("MET", "MET", "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"),
    "KNEE7": ("MET", "MET", "MET", "INSUFFICIENT_EVIDENCE"),
}


@pytest.fixture(scope="module")
def manifests() -> dict[str, dict]:
    bodies = [json.loads(p.read_text(encoding="utf-8")) for p in MANIFEST_DIR.glob("*.json")]
    return {b["cases"][0]: b for b in bodies if b.get("knee_visits")}


@pytest.fixture(scope="module")
def population() -> dict[str, dict]:
    records = json.loads(POPULATION.read_text(encoding="utf-8"))["bundles"]
    return {r["patient_id"]: r for r in records}


@pytest.fixture(scope="module")
def rows() -> dict[str, dict]:
    return {r["case_id"]: r for r in json.loads(CASES.read_text(encoding="utf-8"))["cases"]}


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


def _runner(*names: str) -> RecordedExtractionRunner:
    notes = []
    for name in names:
        notes += json.loads((EXTRACTION / name).read_text(encoding="utf-8"))["notes"]
    return RecordedExtractionRunner.from_records(notes)


def _encounter_dates(bundle_name: str) -> set[str]:
    bundle = json.loads(
        (REPO_ROOT / "data" / "patients" / "bundles" / bundle_name).read_text(encoding="utf-8")
    )
    return {
        e["resource"]["period"]["start"][:10]
        for e in bundle["entry"]
        if e["resource"].get("resourceType") == "Encounter"
    }


def test_seven_charts_one_row_each_all_in_the_knee_cohort(manifests, population):
    assert sorted(manifests) == sorted(EXPECTED)
    for case, body in manifests.items():
        record = population[body["patient_id"]]
        assert record["cohort"] == "knee_osteoarthritis", case
        assert record["knee_oa_onset_dates"] and record["naproxen_order_dates"], case
        assert body["documents"] == ["chart_note_1.txt", "chart_note_2.txt"]


def test_every_visit_is_an_encounter_of_the_charts_own(manifests, population):
    """Each note's visit is dated on an encounter the bundle records, and the
    naproxen start on the chart's own order — the generator's dates, never the
    compiler's (D154 clause 9)."""
    for case, body in manifests.items():
        record = population[body["patient_id"]]
        encounters = _encounter_dates(record["filename"])
        for visit in body["knee_visits"]:
            assert visit["date"] in encounters, (case, visit["date"])
        naproxen = [t for t in body["conservative_therapies"] if t["therapy"] == "naproxen"]
        assert [t["date"] for t in naproxen] == [d[:10] for d in record["naproxen_order_dates"]], case


def test_every_row_pins_its_own_as_of_after_its_last_visit(manifests, rows):
    for case, body in manifests.items():
        row = rows[case]
        assert row["procedure_code"] == "J7325" and row.get("state") is None
        last_visit = max(v["date"] for v in body["knee_visits"])
        assert last_visit < row["as_of"] <= "2026-09-01", case


def test_one_fact_per_document_and_no_duration_in_any_note(manifests, store):
    """The notes state start dates and never a duration, because the months are
    Python's to count (Art. II); and nothing in them is weight-management
    content another kind's extractor could read."""
    for case, body in manifests.items():
        for basename in body["documents"]:
            text = store.get_document(f"{body['patient_id']}/{basename}").text
            assert not re.search(r"\b\d+\s+months?\b", text), (case, basename)
            assert "weight management" not in text.lower()
            assert not re.search(r"BMI [\d.]+", text)


def test_the_knee_recordings_agree_with_every_declared_fact():
    """Both tiers, as committed: every symptom, radiograph, finding set and
    therapy the manifests declare was read back; nothing off a denial, a hip
    radiograph, an unperformed study or a declined therapy."""
    for name in (RECORDING, "knee_osteoarthritis_workup_vertex.json"):
        figures = json.loads((EXTRACTION / name).read_text(encoding="utf-8"))["aggregate"]
        assert figures["notes"] == 14, name
        assert figures["symptoms_matched"] == figures["extracted_symptoms"] == 12
        assert figures["denied_symptoms_extracted"] == 0
        assert figures["matched_radiographs"] == figures["extracted_radiographs"] == 7
        assert figures["finding_agreements"] == figures["finding_total"] == 7
        assert figures["matched_therapies"] == figures["extracted_therapies"] == 13
        assert figures["traps_extracted"] == 0
        assert figures["spans_anchored"] == figures["spans_emitted"]


@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_each_row_replays_end_to_end_for_zero_model_calls(case, manifests, rows, store):
    patient_id = manifests[case]["patient_id"]
    determination = determine(
        LocalPolicyStore(), "J7325", patient_id=patient_id, patient_store=store,
        as_of=date.fromisoformat(rows[case]["as_of"]), extraction_runner=_runner(RECORDING),
        verifier=AcceptAllVerifier(),
    )
    verdicts = {r.criterion_id: r.verdict.value for r in determination.criterion_results}
    a, b, d, outcome = EXPECTED[case]
    assert verdicts == {
        "a": a, "b": b, "c": "INSUFFICIENT_EVIDENCE", "d": d, "e": "INSUFFICIENT_EVIDENCE",
    }, case
    assert determination.outcome.value == outcome
    assert determination.policy_version_id == "hyaluronan-knee-oa-j5-j8-v1"


def test_knee3_ages_into_met_under_the_harness_date(manifests, store):
    """Why KNEE3 pins its as_of: four months of physical therapy by 2026-09-01."""
    determination = determine(
        LocalPolicyStore(), "J7325", patient_id=manifests["KNEE3"]["patient_id"],
        patient_store=store, as_of=date(2026, 9, 1), extraction_runner=_runner(RECORDING),
        verifier=AcceptAllVerifier(),
    )
    d = next(r for r in determination.criterion_results if r.criterion_id == "d")
    assert d.verdict is CriterionVerdict.MET


def test_a_knee_chart_read_from_another_kinds_recording_is_an_error(manifests, rows, store):
    """No payload answers this kind, so the three note criteria are `ERROR` —
    never an abstention that would say the notes are silent (D90, D149)."""
    case = "KNEE1"
    with pytest.raises(DeterminationAborted) as caught:
        determine(
            LocalPolicyStore(), "J7325", patient_id=manifests[case]["patient_id"],
            patient_store=store, as_of=date.fromisoformat(rows[case]["as_of"]),
            extraction_runner=_runner("results.json", "sleep_apnea_workup.json"),
            verifier=AcceptAllVerifier(),
        )
    results = caught.value.results
    assert {r.criterion_id for r in results} == {"a", "b", "d"}
    assert {r.verdict for r in results} == {CriterionVerdict.ERROR}
    assert {r.error_code for r in results} == {ErrorCode.SCHEMA_INVALID}


def test_no_other_kind_reads_a_knee_note(store):
    runner = _runner(RECORDING)
    record = json.loads((EXTRACTION / RECORDING).read_text(encoding="utf-8"))["notes"][0]
    text = store.get_document(record["document_id"]).text
    for kind in (FactKind.WEIGHT_MANAGEMENT, FactKind.SLEEP_APNEA_WORKUP):
        with pytest.raises(Exception, match="SCHEMA_MISMATCH"):
            runner.run(record["document_id"], text, kind)
