"""T-12 — BMI observations and Conditions with dates, from all six bundles (D39).

The ground truth the reads are checked against is T-04's manifest: the
most-recent BMI and its date were recorded at selection time by independent
code, so the adapter reproducing them is two implementations agreeing rather
than one implementation agreeing with itself.
"""

from __future__ import annotations

import ast
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENTS_ROOT = REPO_ROOT / "data" / "patients"
PATIENT_MODULE = REPO_ROOT / "pa_agent" / "stores" / "patient.py"

LOINC_BMI = "39156-5"
SNOMED_T2DM = "44054006"


@pytest.fixture(scope="module")
def manifest_records() -> list[dict]:
    manifest = json.loads((PATIENTS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return manifest["bundles"]


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


# --------------------------------------------------------------------------
# BMI observations, from all six bundles
# --------------------------------------------------------------------------


def test_every_patient_yields_bmi_observations_with_dates(store, manifest_records):
    assert len(manifest_records) == 6
    for record in manifest_records:
        observations = store.get_observations(record["patient_id"])
        bmis = [o for o in observations if o.code == LOINC_BMI]
        assert bmis, f"{record['filename']}: no BMI observations served"
        assert all(isinstance(o.effective_date, date) for o in bmis)


def test_the_most_recent_bmi_matches_the_manifest(store, manifest_records):
    """T-04 recorded each patient's latest BMI with independent parsing code;
    the adapter must land on the same value and the same day."""
    for record in manifest_records:
        bmis = [
            o for o in store.get_observations(record["patient_id"])
            if o.code == LOINC_BMI
        ]
        latest = max(bmis, key=lambda o: o.effective_date)
        assert latest.value == record["latest_bmi"], record["filename"]
        assert latest.effective_date.isoformat() == record["latest_bmi_date"][:10]


def test_observations_are_not_bmi_only(store, manifest_records):
    """The adapter reports facts; selecting LOINC 39156-5 is criterion (a)'s
    judgment (D31's split, D39). A BMI-only read would hide the structured
    values REQ-34's reconciliation compares against."""
    codes = {o.code for o in store.get_observations(manifest_records[0]["patient_id"])}
    assert LOINC_BMI in codes and len(codes) > 1


# --------------------------------------------------------------------------
# Conditions with dates and status, from all six bundles
# --------------------------------------------------------------------------


def test_every_patient_yields_dated_conditions(store, manifest_records):
    for record in manifest_records:
        conditions = store.get_conditions(record["patient_id"])
        assert conditions, f"{record['filename']}: no conditions served"
        assert any(c.onset_date is not None for c in conditions)


def test_the_t2dm_fact_survives_the_read(store, manifest_records):
    """The manifest recorded which patient carries active T2DM (E2's shape);
    the adapter must serve that condition as active."""
    flagged = [r for r in manifest_records if r["has_active_t2dm"]]
    assert flagged, "the population lost its E2 patient; regenerate per D35"
    for record in flagged:
        conditions = store.get_conditions(record["patient_id"])
        t2dm = [c for c in conditions if c.code == SNOMED_T2DM]
        assert t2dm and t2dm[0].clinical_status == "active"


def test_clinical_status_is_reported_not_filtered(store, manifest_records):
    """A read serving only active conditions would decide criterion (b)'s
    question inside the adapter (D39). The population carries resolved
    conditions; they must come through, labeled."""
    statuses = set()
    for record in manifest_records:
        statuses |= {c.clinical_status for c in store.get_conditions(record["patient_id"])}
    assert "active" in statuses
    assert statuses - {"active"}, (
        "only active conditions came through; either the adapter filters "
        "(D39 forbids) or the population has no resolved condition anywhere"
    )


# --------------------------------------------------------------------------
# The failure modes stay loud
# --------------------------------------------------------------------------


def test_an_unknown_patient_raises(store):
    with pytest.raises(KeyError, match="nobody-here"):
        store.get_observations("nobody-here")


def test_a_tampered_bundle_fails_on_read(tmp_path, manifest_records):
    """The get_document/sources.json pattern on the patient plane (REQ-7):
    the manifest hash is the record, the file is checked against it."""
    root = tmp_path / "patients"
    shutil.copytree(PATIENTS_ROOT / "bundles", root / "bundles")
    shutil.copy(PATIENTS_ROOT / "manifest.json", root / "manifest.json")
    victim = manifest_records[0]
    bundle_path = root / "bundles" / victim["filename"]
    bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
    store = LocalPatientStore(root=root)
    with pytest.raises(ValueError, match="REQ-7"):
        store.get_observations(victim["patient_id"])


def test_notes_are_served_hash_verified_and_never_synthea_generated(
    store, manifest_records
):
    """T-07 landed the corpus and the raise retired (D43). What must stay true
    is *which* notes are served: the manifest-driven ones, whose facts someone
    declared, never Synthea's auto-generated prose (D39)."""
    for record in manifest_records:
        documents = store.get_notes(record["patient_id"])
        assert documents, f"{record['filename']}: no note served"
        for document in documents:
            # Document's validator re-hashes on construction, so reaching here
            # means the served text matches the notes manifest (REQ-7).
            assert document.text.strip()
            assert record["patient_id"] in document.document_id


# --------------------------------------------------------------------------
# The plane stays clean
# --------------------------------------------------------------------------


def test_the_patient_module_imports_no_policy_and_no_model():
    """REQ-33 on this module's own imports: the patient plane holds no policy
    corpus, and nothing model-shaped parses a chart (Art. II, VI)."""
    tree = ast.parse(PATIENT_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "__future__", "hashlib", "json", "datetime", "pathlib", "typing",
        "pa_agent.contracts",
    }, (
        f"pa_agent/stores/patient.py imports {sorted(imported)}; no policy "
        "module, no index over the corpus, no model (REQ-33, D39)"
    )
