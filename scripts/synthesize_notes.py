"""T-07 — turn T-06's fact manifests into chart notes (D43).

    python scripts/synthesize_notes.py --generate
        Writes data/patients/notes/<patient_id>/*.txt from eval/manifests/,
        plus a notes manifest recording the seed and each note's sha256.

    python scripts/synthesize_notes.py [--verify]
        Re-hashes the committed notes against that manifest. Disk only.

**No model writes these notes.** They are the input to the model T-15
measures, and a model-written corpus would be phrased the way models phrase
things — extraction scored on it would measure model-to-model agreement and
come out high for the wrong reason (D43). Generation is deterministic
templating from a seeded phrase bank, so the corpus is byte-stable and T-15's
regression cases cannot drift under a re-run.

Two properties the gate depends on, both deliberate:

- **Every date emitted comes from the manifest** (or is the patient's
  structured date of birth). An invented date would be extracted, scored
  against ground truth that never mentioned it, and counted as a model
  failure that was really a corpus defect.
- **Lines wrap hard at 78 columns**, the way EHR exports do. D18's
  whitespace-insensitive anchoring exists because quotes cross wrap points;
  a corpus of clean single lines would leave that path untested until
  production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import sys
import textwrap
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
NOTES_DIR = REPO_ROOT / "data" / "patients" / "notes"
NOTES_MANIFEST = NOTES_DIR / "manifest.json"
BUNDLES_DIR = REPO_ROOT / "data" / "patients" / "bundles"

SEED = 7  # T-07's, recorded so the corpus is reproducible
WRAP = 78
LOINC_HEIGHT = "8302-2"

PRACTICES = [
    "CASCADE VALLEY FAMILY MEDICINE",
    "PUGET RIDGE INTERNAL MEDICINE",
    "OLYMPIC CREST MEDICAL GROUP",
    "SOUND VIEW PRIMARY CARE",
    "EVERGREEN BASIN MEDICAL CLINIC",
    "NORTH FORK HEALTH PARTNERS",
]
SUPERVISORS = [
    "R. Ellery, MD", "T. Nakashima, DO", "P. Adeyemi, MD",
    "J. Vandermolen, MD", "S. Kowalczyk, DO", "L. Brantley, MD",
]
DIET_PHRASES = [
    "Reviewed the {kcal} kcal reduced-calorie meal plan; patient adherent",
    "Dietary recall reviewed in detail against the {kcal} kcal plan",
    "Nutrition counseling provided; meal plan held at {kcal} kcal",
    "Food diary reviewed; {kcal} kcal plan continued without change",
    "Discussed portion control and protein intake on the {kcal} kcal plan",
]
ACTIVITY_PHRASES = [
    "Reports {minutes} minutes of brisk walking, {days} days per week",
    "Exercise log shows {minutes} minutes of stationary cycling, {days} days weekly",
    "Activity goal of {minutes} minutes daily met on {days} days this month",
    "Continues supervised exercise, {minutes} minutes, {days} days per week",
    "Reports {minutes} minutes of pool exercise {days} days per week",
]
PLAIN_VISIT_PHRASES = [
    "Program visit. Interval history reviewed",
    "Scheduled program follow-up. No new complaints",
    "Monthly program visit completed",
    "Routine supervised program visit",
]
MISSED_PHRASES = [
    "Patient did not attend the scheduled appointment. Recorded as a no-show "
    "by front desk staff. No clinical encounter took place.",
    "Appointment cancelled by the patient the morning of the visit. Patient "
    "not seen and no assessment was performed.",
]
CONTACT_PHRASES = [
    "Outreach call placed regarding the lapse in attendance. No answer; "
    "voicemail left. No clinical contact was established.",
    "Two telephone attempts made to reach the patient to reschedule. Line "
    "not answered on either attempt. Patient not reached.",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def us(iso: str) -> str:
    """ISO to the MM/DD/YYYY the corpus writes, matching spike 001."""
    d = date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(
        " ".join(text.split()),
        width=WRAP,
        initial_indent=indent,
        subsequent_indent=indent,
    )


def _demographics(patient_id: str, filename: str) -> dict:
    bundle = json.loads((BUNDLES_DIR / filename).read_text(encoding="utf-8"))
    patient = next(
        e["resource"]
        for e in bundle["entry"]
        if e["resource"].get("resourceType") == "Patient"
    )
    name = patient["name"][0]
    return {
        "family": name["family"],
        "given": name["given"][0],
        "birth_date": patient["birthDate"],
        "sex": "M" if patient.get("gender") == "male" else "F",
    }


def _height_m(store: LocalPatientStore, patient_id: str) -> float:
    heights = [
        o for o in store.get_observations(patient_id) if o.code == LOINC_HEIGHT
    ]
    latest = max(heights, key=lambda o: o.effective_date)
    return latest.value / 100.0


def _encounter_line(rng, encounter: dict, height_m: float) -> str:
    """One dated visit entry. Weight is derived from the note's BMI and the
    patient's structured height, so the numbers inside a note agree (D43)."""
    parts = [f"{us(encounter['date'])} - "]
    body = rng.choice(PLAIN_VISIT_PHRASES) + ". "

    if encounter.get("bmi_documented") and "note_bmi" in encounter:
        bmi = encounter["note_bmi"]
        weight = bmi * height_m * height_m
        body += f"Weight {weight:.1f} kg, BMI {bmi:.1f}. "
    elif encounter.get("weight_documented"):
        # D15's case: a weight with no BMI in the encounter. The height is on
        # file elsewhere and deriving from it is exactly what c4 refuses.
        weight = 40.0 * height_m * height_m  # plausible, no BMI stated
        body += f"Weight {weight:.1f} kg. BMI not calculated at this visit. "

    if encounter.get("diet_documented"):
        body += rng.choice(DIET_PHRASES).format(
            kcal=rng.choice([1200, 1400, 1500, 1600, 1800])
        ) + ". "
    if encounter.get("activity_documented"):
        body += rng.choice(ACTIVITY_PHRASES).format(
            minutes=rng.choice([15, 20, 25, 30, 40]),
            days=rng.choice(["three", "four", "five"]),
        ) + "."
    return wrap(parts[0] + body)


def _trap_line(rng, trap: dict) -> str:
    """A trap reads like the chart event it is, never like its own label —
    a note saying 'missed visit' would test keyword matching, not REQ-9."""
    stamp = f"{us(trap['date'])} - "
    if trap["type"] == "missed_visit":
        return wrap(stamp + rng.choice(MISSED_PHRASES))
    if trap["type"] == "unsuccessful_contact":
        return wrap(stamp + rng.choice(CONTACT_PHRASES))
    if trap["type"] == "unsupervised_attempt":
        return wrap(
            f"Patient reports a self-directed attempt beginning {us(trap['date'])}, "
            "undertaken without clinical supervision and with no visits or "
            "monitoring by this or any other practice."
        )
    # The manifest's `description` is ground-truth annotation for a human
    # reader and may name the section it belongs in ("... under HEALTH
    # MAINTENANCE"). The chart prints the event, not the placement note.
    description = re.split(r"\s+under\s+[A-Z]{2,}", trap["description"])[0]
    return wrap(f"{us(trap['date'])} - {description.capitalize()}.")


def render(manifest: dict, demo: dict, height_m: float, rng: random.Random) -> str:
    lines: list[str] = []
    lines.append(rng.choice(PRACTICES))
    lines.append(
        f"Patient: {demo['family']}, {demo['given']}"
        f"{' ' * max(1, 38 - len(demo['family']) - len(demo['given']))}"
        f"MRN: {rng.randrange(200000, 899999)}"
    )
    lines.append(f"DOB: {demo['birth_date']}                       Sex: {demo['sex']}")
    lines.append("")

    programs = manifest["wm_programs"]
    series_traps = [
        t for t in manifest["traps"]
        if t["type"] in ("missed_visit", "unsuccessful_contact")
    ]

    if programs:
        lines.append("MEDICAL WEIGHT MANAGEMENT PROGRAM - CONSOLIDATED VISIT RECORD")
        lines.append(wrap(f"Physician-supervised program directed by {rng.choice(SUPERVISORS)}."))
        lines.append("")
        # Entries and in-series traps interleave in date order, the way a
        # consolidated record reads. The gap month is where the traps land.
        entries: list[tuple[str, str]] = []
        for program in programs:
            for encounter in program["encounters"]:
                entries.append((encounter["date"], _encounter_line(rng, encounter, height_m)))
        for trap in series_traps:
            entries.append((trap["date"], _trap_line(rng, trap)))
        for _, block in sorted(entries):
            lines.append(block)
            lines.append("")

    for assertion in manifest["program_assertions"]:
        lines.append("BARIATRIC SURGERY CONSULTATION")
        lines.append("")
        lines.append("HISTORY OF PRESENT ILLNESS")
        lines.append(wrap(
            f"Seen {us(assertion['date'])} for evaluation. {assertion['claim'].capitalize()}. "
            "Records from the outside facility have been requested and are not "
            "available at the time of this consultation."
        ))
        if "note_bmi" in assertion:
            weight = assertion["note_bmi"] * height_m * height_m
            lines.append("")
            lines.append(wrap(
                f"Measured in clinic today: weight {weight:.1f} kg, "
                f"BMI {assertion['note_bmi']:.1f}."
            ))
        lines.append("")

    other_traps = [t for t in manifest["traps"] if t not in series_traps]
    unsupervised = [t for t in other_traps if t["type"] == "unsupervised_attempt"]
    unrelated = [t for t in other_traps if t["type"] == "unrelated_section_date"]

    if unsupervised:
        lines.append("PRIOR WEIGHT LOSS HISTORY")
        for trap in unsupervised:
            lines.append(_trap_line(rng, trap))
        lines.append("")

    if unrelated:
        lines.append("HEALTH MAINTENANCE")
        for trap in unrelated:
            lines.append(_trap_line(rng, trap))
        lines.append("")

    lines.append("ASSESSMENT")
    if programs:
        lines.append(wrap(
            "Obesity with continued participation in the supervised weight "
            "management program documented above."
        ))
    elif manifest["program_assertions"]:
        lines.append(wrap(
            "Obesity. Candidate pending documentation of the weight management "
            "program described above."
        ))
    else:
        lines.append(wrap(
            "Obesity. No weight management program documentation is present in "
            "the record at this time."
        ))
    return "\n".join(lines).rstrip() + "\n"


def generate() -> int:
    store = LocalPatientStore()
    if NOTES_DIR.exists():
        shutil.rmtree(NOTES_DIR)
    NOTES_DIR.mkdir(parents=True)

    records = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("note") is False:
            # A declared note-free chart (D73: E12 reads structured data
            # only). Skipped before any per-patient work, so the other
            # patients' notes are byte-stable across its addition.
            continue
        patient_id = manifest["patient_id"]
        demo = _demographics(patient_id, manifest["bundle"])
        height_m = _height_m(store, patient_id)
        # One generator per patient, seeded from the shared seed and the
        # patient id, so adding a patient cannot reshuffle everyone's prose.
        rng = random.Random(f"{SEED}:{patient_id}")
        text = render(manifest, demo, height_m, rng)

        out_dir = NOTES_DIR / patient_id
        out_dir.mkdir()
        note_path = out_dir / "chart_note.txt"
        note_path.write_text(text, encoding="utf-8")
        records.append({
            "patient_id": patient_id,
            "document_id": f"{patient_id}/chart_note.txt",
            "sha256": sha256(note_path),
            "cases": manifest["cases"],
            "characters": len(text),
        })
        print(f"  {manifest['cases']} -> {note_path.relative_to(REPO_ROOT)} ({len(text)} chars)")

    NOTES_MANIFEST.write_text(
        json.dumps(
            {"task": "T-07", "decision": "D43", "seed": SEED, "wrap_columns": WRAP,
             "notes": records},
            indent=2, ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"manifest written: {NOTES_MANIFEST.relative_to(REPO_ROOT)}")
    return verify()


def verify() -> int:
    if not NOTES_MANIFEST.exists():
        print("no notes manifest; run --generate")
        return 1
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    failures = []
    for record in manifest["notes"]:
        path = NOTES_DIR / record["document_id"]
        ok = path.exists() and sha256(path) == record["sha256"]
        print(("  ok   " if ok else "  FAIL ") + record["document_id"])
        if not ok:
            failures.append(record["document_id"])
    on_disk = {
        str(p.relative_to(NOTES_DIR)) for p in NOTES_DIR.rglob("*.txt")
    }
    listed = {r["document_id"] for r in manifest["notes"]}
    if on_disk != listed:
        print(f"  FAIL notes on disk {sorted(on_disk ^ listed)} not in the manifest")
        failures.append("stray")
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    return generate() if args.generate else verify()


if __name__ == "__main__":
    sys.exit(main())
