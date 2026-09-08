"""T-07 — every note honors its manifest, by assertion rather than by reading (D43).

The exit condition's phrase is the whole design of this file: nobody proves a
corpus correct by looking at it. Each manifest fact is checked mechanically
against the note text, and — the assertion that makes the corpus usable as
ground truth — **no date appears in a note that the manifest does not
declare**. An invented date would be extracted by T-15, scored against ground
truth that never mentioned it, and counted as a model failure that was really
a corpus defect.

Notes are read through `PatientStore` (REQ-41), so the hashes are verified on
the way in and these checks run against the same bytes T-15 will.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"
BUNDLES_DIR = REPO_ROOT / "data" / "patients" / "bundles"

WRAP_LIMIT = 78
US_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b")
ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Any four-digit year with separators — broader than the two patterns the
# synthesizer emits, so a date in a shape nobody planned for is still caught.
ANY_DATE = re.compile(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b")

# Words that would turn REQ-9 into keyword matching: a real chart says "did
# not attend", never "missed_visit".
LEAKED_LABELS = [
    "missed_visit", "unsuccessful_contact", "unsupervised_attempt",
    "unrelated_section_date", "trap", "manifest", "ground truth",
    "wm_event", "program_assertion",
]
# A non-encounter entry has to say so in the prose, or extraction is being
# asked to guess (REQ-9, E9).
NON_ENCOUNTER_PHRASES = [
    "no clinical encounter", "not seen", "no assessment was performed",
    "patient not reached", "no clinical contact", "not answered",
]


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def manifests() -> dict[str, dict]:
    return {
        body["patient_id"]: body
        for body in (
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(MANIFEST_DIR.glob("*.json"))
        )
    }


@pytest.fixture(scope="module")
def notes(store, manifests) -> dict[str, str]:
    """patient_id -> note text, served through the port and hash-verified."""
    text_by_patient = {}
    for patient_id in manifests:
        documents = store.get_notes(patient_id)
        assert len(documents) == 1, f"{patient_id}: expected one chart note"
        text_by_patient[patient_id] = documents[0].text
    return text_by_patient


def _iso(match: re.Match) -> str:
    raw = match.group(0)
    if "/" in raw:
        month, day, year = raw.split("/")
        return f"{year}-{month}-{day}"
    return raw


def _dates_in(text: str) -> set[str]:
    return {_iso(m) for m in ANY_DATE.finditer(text)}


def _declared_dates(manifest: dict) -> set[str]:
    dates = {
        e["date"]
        for p in manifest["wm_programs"]
        for e in p["encounters"]
    }
    dates |= {t["date"] for t in manifest["traps"]}
    dates |= {a["date"] for a in manifest["program_assertions"]}
    return dates


def _birth_date(manifest: dict) -> str:
    bundle = json.loads(
        (BUNDLES_DIR / manifest["bundle"]).read_text(encoding="utf-8")
    )
    patient = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"].get("resourceType") == "Patient"
    )
    return patient["birthDate"]


def _norm(text: str) -> str:
    """D18's equivalence class, and this file needs it for the same reason the
    anchorer does: the corpus wraps at 78 columns, so a fact like "BMI 42.7"
    genuinely lands split across a line break. Matching raw text here would
    fail on honest notes — and, worse, a *negative* assertion on raw text
    would pass on a wrapped BMI the block really does state."""
    return " ".join(text.split())


def _blocks(text: str) -> list[str]:
    return [b for b in text.split("\n\n") if b.strip()]


def _block_for(text: str, iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    stamp = f"{month}/{day}/{year}"
    matching = [b for b in _blocks(text) if stamp in b]
    assert matching, f"no block mentions {stamp}"
    return matching[0]


# --------------------------------------------------------------------------
# The corpus exists, verified, one note per manifest
# --------------------------------------------------------------------------


def test_one_hash_verified_note_per_manifest(notes, manifests):
    assert set(notes) == set(manifests)
    recorded = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    assert recorded["seed"] is not None, "the corpus records the seed that made it"
    assert {r["patient_id"] for r in recorded["notes"]} == set(manifests)


def test_every_note_wraps_like_an_ehr_export(notes):
    """D43: the wrap is load-bearing. D18 chose whitespace-insensitive
    anchoring because quotes cross wrap points, and a corpus of clean single
    lines would leave that path untested until production."""
    wrapped_blocks = 0
    for patient_id, text in notes.items():
        for line in text.splitlines():
            assert len(line) <= WRAP_LIMIT, (
                f"{patient_id}: line of {len(line)} chars exceeds the wrap"
            )
        wrapped_blocks += sum(1 for b in _blocks(text) if "\n" in b)
    assert wrapped_blocks >= 6, (
        "almost nothing wraps; T-15's anchoring would never meet a quote "
        "crossing a line break"
    )


# --------------------------------------------------------------------------
# Every declared fact is in the note
# --------------------------------------------------------------------------


def test_every_encounter_date_appears(notes, manifests):
    for patient_id, manifest in manifests.items():
        present = _dates_in(notes[patient_id])
        for program in manifest["wm_programs"]:
            for encounter in program["encounters"]:
                assert encounter["date"] in present, (
                    f"{patient_id}: encounter {encounter['date']} is not in the note"
                )


def test_every_trap_and_assertion_date_appears(notes, manifests):
    for patient_id, manifest in manifests.items():
        present = _dates_in(notes[patient_id])
        for trap in manifest["traps"]:
            assert trap["date"] in present, f"{patient_id}: trap {trap['date']} missing"
        for assertion in manifest["program_assertions"]:
            assert assertion["date"] in present


def test_a_documented_bmi_appears_in_its_own_encounter_block(notes, manifests):
    for patient_id, manifest in manifests.items():
        for program in manifest["wm_programs"]:
            for encounter in program["encounters"]:
                if not encounter.get("bmi_documented"):
                    continue
                block = _norm(_block_for(notes[patient_id], encounter["date"]))
                assert f"BMI {encounter['note_bmi']:.1f}" in block, (
                    f"{patient_id}: {encounter['date']} claims a documented BMI "
                    f"of {encounter['note_bmi']} and the block does not state it"
                )


def test_an_undocumented_bmi_month_states_a_weight_and_no_bmi(notes, manifests):
    """D15's case, which E6 turns on: a weight with the height on file
    elsewhere is not a documented BMI, and the note must not quietly supply
    one anyway."""
    checked = 0
    for patient_id, manifest in manifests.items():
        for program in manifest["wm_programs"]:
            for encounter in program["encounters"]:
                if encounter.get("bmi_documented") or not encounter.get("weight_documented"):
                    continue
                block = _norm(_block_for(notes[patient_id], encounter["date"]))
                assert re.search(r"Weight [\d.]+ kg", block), (
                    f"{patient_id}: {encounter['date']} documents a weight and "
                    "the block does not state one"
                )
                assert not re.search(r"BMI [\d.]+", block), (
                    f"{patient_id}: {encounter['date']} states a BMI the "
                    "manifest says is not documented"
                )
                checked += 1
    assert checked, "no weight-only encounter in the corpus; E6 has nothing to fail on"


def test_the_assertion_note_makes_its_claim_in_prose(notes, manifests):
    for patient_id, manifest in manifests.items():
        for assertion in manifest["program_assertions"]:
            text = notes[patient_id]
            # The distinctive words of the claim survive into the chart.
            for word in ("supervised", "program"):
                assert word in text.lower()
            if "note_bmi" in assertion:
                assert f"BMI {assertion['note_bmi']:.1f}" in _norm(text)


# --------------------------------------------------------------------------
# And nothing else is
# --------------------------------------------------------------------------


def test_no_note_contains_a_date_the_manifest_does_not_declare(notes, manifests):
    """The assertion that makes this corpus usable as ground truth (D43)."""
    for patient_id, manifest in manifests.items():
        allowed = _declared_dates(manifest) | {_birth_date(manifest)}
        found = _dates_in(notes[patient_id])
        unaccounted = found - allowed
        assert not unaccounted, (
            f"{patient_id}: note contains {sorted(unaccounted)}, which the "
            "manifest does not declare. Extraction would surface it and it "
            "would be scored as a model failure that was really a corpus one."
        )


def test_no_note_leaks_a_trap_label(notes):
    for patient_id, text in notes.items():
        lowered = text.lower()
        for label in LEAKED_LABELS:
            assert label not in lowered, (
                f"{patient_id}: the chart says {label!r}; a note that names its "
                "own traps tests keyword matching, not REQ-9"
            )


def test_every_non_encounter_entry_says_so_in_the_prose(notes, manifests):
    """E9's substance: a missed visit and a failed contact must be legible as
    non-encounters from the text alone, or extraction is being asked to guess."""
    checked = 0
    for patient_id, manifest in manifests.items():
        for trap in manifest["traps"]:
            if trap["type"] not in ("missed_visit", "unsuccessful_contact"):
                continue
            block = _norm(_block_for(notes[patient_id], trap["date"])).lower()
            assert any(p in block for p in NON_ENCOUNTER_PHRASES), (
                f"{patient_id}: {trap['date']} reads like an encounter"
            )
            assert not re.search(r"BMI [\d.]+", block), (
                "a non-encounter block states a BMI, which would make it look "
                "like a documented visit"
            )
            checked += 1
    assert checked, "no missed-visit or contact trap in the corpus; E9 is untested"


def test_e8s_note_carries_an_assertion_and_no_dated_visit(notes, manifests):
    """The refusal test's corpus half: zero encounters, one claim."""
    manifest = next(m for m in manifests.values() if "E8" in m["cases"])
    text = notes[manifest["patient_id"]]
    assert manifest["wm_programs"] == []
    assert manifest["program_assertions"]
    assert "MEDICAL WEIGHT MANAGEMENT PROGRAM" not in text, (
        "the visit-series section implies encounters this chart does not have"
    )


def test_e7s_note_has_no_weight_management_content(notes, manifests):
    manifest = next(m for m in manifests.values() if "E7" in m["cases"])
    text = _norm(notes[manifest["patient_id"]]).lower()
    assert "weight management" in text, (
        "the assessment states the absence explicitly, so c1's abstention is "
        "about missing documentation rather than a missing chart"
    )
    assert "program visit" not in text
    assert not re.search(r"BMI [\d.]+", text, re.IGNORECASE)


# --------------------------------------------------------------------------
# The corpus is reproducible
# --------------------------------------------------------------------------


def test_regenerating_the_corpus_is_byte_identical(tmp_path, notes):
    """No model wrote these (D43), so the seed reproduces them exactly — which
    is what lets T-15 reuse them as regression cases."""
    import subprocess
    import sys

    before = {p: p.read_bytes() for p in
              (REPO_ROOT / "data/patients/notes").rglob("*.txt")}
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "synthesize_notes.py"), "--generate"],
        capture_output=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr.decode()
    after = {p: p.read_bytes() for p in
             (REPO_ROOT / "data/patients/notes").rglob("*.txt")}
    assert before == after, "regeneration changed the corpus; the seed is not holding"
