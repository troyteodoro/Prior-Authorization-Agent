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

A manifest that declares `cloned_from` (T-88, D102) is rendered from its own
facts but seeded from the *source* patient's id, so the clone's notes are
byte-identical to its source's — which is what lets T-15's recording answer
them for zero calls. `--verify` asserts that identity by hash, per document.

Since T-81 (D104) a manifest declares `documents`, a list of basenames, and
every encounter, assertion and trap names the one document it is rendered
into. One scalar per fact means a fact cannot appear in two documents. The
practice, MRN and supervisor are drawn once per patient; each document's
prose comes from its own RNG stream, so a patient's two documents read
differently and editing one document's facts cannot reshuffle the other's.
A later document whose program also has visits in an earlier one is headed
as a continuation. `--verify` refuses a note-bearing manifest with fewer than
two records and any two non-clone documents with the same bytes, because a
digest-keyed replay maps one digest to one id.
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
# T-108 (D150): the fourth practice's notes. A sleep chart is a consultation
# and a study report, rendered from the manifest's `sleep_*` facts by
# `render_sleep_document` and by nothing the weight-management path reads, so
# no bariatric note's bytes can move. Its own practice and clinician lists,
# drawn from the same per-patient stream in the same order.
SLEEP_PRACTICES = [
    "PRAIRIE LAKES SLEEP CENTER",
    "CEDAR VALLEY PULMONARY AND SLEEP MEDICINE",
    "RIVER BEND SLEEP DISORDERS CLINIC",
]
SLEEP_CLINICIANS = [
    "M. Okafor, MD", "D. Lindqvist, DO", "A. Ferreira, MD", "K. Haugen, MD",
]
#: How a study report names its type, by the manifest's `study_type`.
STUDY_TYPES = {
    "type_i": "Attended in-laboratory polysomnogram (Type I)",
    "type_iii": "Unattended home sleep test (Type III)",
}
INDEX_NAMES = {
    "AHI": "Apnea-hypopnea index (AHI)",
    "RDI": "Respiratory disturbance index (RDI)",
}
#: A documented finding, as a consultation states it. Each names its category
#: in the plain words a chart uses and never the manifest's label.
FINDING_PHRASES = {
    "excessive_daytime_sleepiness": (
        "Reports excessive daytime sleepiness, falling asleep while reading and "
        "in afternoon meetings; Epworth Sleepiness Scale 15 of 24."
    ),
    "impaired_cognition": "Reports difficulty concentrating and frequent forgetfulness at work.",
    "mood_disorder": "Reports persistent low mood and irritability over several months.",
    "insomnia": "Reports difficulty maintaining sleep, waking three to four times nightly.",
    "hypertension": "History of hypertension, treated.",
    "ischemic_heart_disease": "History of ischemic heart disease.",
    "history_of_stroke": "History of stroke.",
}
#: A finding the patient denies. It must read as a denial, or the extractor is
#: being asked to guess (the sleep kind's instruction names it, D150).
DENIED_PHRASES = {
    "excessive_daytime_sleepiness": "Denies excessive daytime sleepiness.",
}
TELEPHONE_PHRASE = (
    "Telephone contact with the patient to arrange the home sleep test. No "
    "examination was performed and the patient was not seen."
)
# T-109 (D154): the fifth practice's notes. A knee chart is a clinic visit and
# a follow-up visit, rendered from the manifest's `knee_*` facts and
# `conservative_therapies` by `render_knee_document` and by nothing either
# other path reads, so no committed note's bytes can move.
KNEE_PRACTICES = [
    "LINN COUNTY ORTHOPEDICS AND SPORTS MEDICINE",
    "CEDAR VALLEY BONE AND JOINT CLINIC",
    "DES MOINES RIVER ORTHOPEDIC ASSOCIATES",
]
KNEE_CLINICIANS = [
    "R. Abernathy, MD", "S. Nakamura, DO", "T. Oyelaran, PA-C", "J. Brandt, MD",
]
#: A symptom documented as present, in the plain words a chart uses and never
#: the manifest's label (the knee kind's instruction names the four, D154).
KNEE_SYMPTOM_PHRASES = {
    "pain_limiting_daily_activities": (
        "Reports right knee pain that limits walking to about two blocks and "
        "makes prolonged standing difficult."
    ),
    "pain_interrupting_sleep": "Reports right knee pain that wakes the patient at night.",
    "crepitus": "Crepitus is palpable over the right knee through flexion and extension.",
    "knee_stiffness": "Reports morning stiffness of the right knee lasting about twenty minutes.",
}
#: A symptom the patient denies. It must read as a denial (D154).
KNEE_DENIED_PHRASES = {
    "knee_pain": "Denies knee pain at rest, with walking or at night.",
    "knee_stiffness": "Denies morning stiffness.",
}
#: One sentence per radiographic finding, so each can be quoted on its own.
RADIOGRAPH_FINDING_PHRASES = {
    "joint_space_narrowing": "Medial joint space narrowing.",
    "osteophytes": "Marginal osteophytes at the tibiofemoral joint.",
    "subchondral_sclerosis": "Subchondral sclerosis of the medial tibial plateau.",
    "subchondral_cysts": "Subchondral cysts in the medial femoral condyle.",
}
#: A knee radiograph reporting none of the four, stated as absences.
RADIOGRAPH_NORMAL = (
    "Joint spaces preserved. No osteophytes, subchondral sclerosis or "
    "subchondral cysts. Soft tissues unremarkable."
)
KNEE_THERAPY_PHRASES = {
    "naproxen": "Naproxen sodium 220 mg by mouth twice daily, started {date}.",
    "home_exercise": "Home exercise program for quadriceps strengthening, begun {date}.",
    "physical_therapy": "Outpatient physical therapy for the knee, begun {date}.",
}
HIP_RADIOGRAPH_PHRASE = (
    "{date} - Radiographs of the right hip: osteophytes at the acetabular "
    "margin. Hip joint space preserved."
)
RADIOGRAPH_NOT_PERFORMED_PHRASE = (
    "Radiographs of the right knee were scheduled for {date}. The patient did "
    "not attend and no images were obtained."
)
THERAPY_DECLINED_PHRASE = (
    "Outpatient physical therapy for the knee was offered at this visit; the "
    "patient declined."
)
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


def _identity(rng: random.Random, sleep: bool = False, knee: bool = False) -> dict:
    """The per-patient randomness: one practice, one MRN, one supervisor,
    shared by every document of the chart (D104). A sleep or knee chart draws
    from its own lists in the same order, so the call sequence is the same
    shape (D150, D154)."""
    practices = KNEE_PRACTICES if knee else SLEEP_PRACTICES if sleep else PRACTICES
    clinicians = KNEE_CLINICIANS if knee else SLEEP_CLINICIANS if sleep else SUPERVISORS
    return {
        "practice": rng.choice(practices),
        "mrn": rng.randrange(200000, 899999),
        "supervisor": rng.choice(clinicians),
    }


def is_knee_chart(manifest: dict) -> bool:
    """A chart the fifth practice's facts describe (T-109, D154)."""
    return bool(manifest.get("knee_visits"))


def render_knee_document(
    manifest: dict,
    basename: str,
    demo: dict,
    identity: dict,
    rng: random.Random,
) -> str:
    """One document of a knee chart: the clinic visit or the follow-up visit,
    whichever facts the manifest assigns to `basename` (D104, D154).

    Every date comes from the manifest, and the manifest's dates are the
    chart's own encounters and order, or declared. The note states start dates
    and findings and never a duration or a verdict, because the arithmetic is
    the engine's (Art. II).
    """
    documents = manifest["documents"]
    ordinal = documents.index(basename) + 1
    lines: list[str] = [
        identity["practice"],
        f"Patient: {demo['family']}, {demo['given']}"
        f"{' ' * max(1, 38 - len(demo['family']) - len(demo['given']))}"
        f"MRN: {identity['mrn']}",
        f"DOB: {demo['birth_date']}                       Sex: {demo['sex']}",
        f"Document {ordinal} of {len(documents)}",
        "",
    ]
    visits = _in(manifest["knee_visits"], basename)
    symptoms = _in(manifest.get("knee_symptoms", []), basename)
    denied = _in(manifest.get("denied_symptoms", []), basename)
    radiographs = _in(manifest.get("knee_radiographs", []), basename)
    therapies = _in(manifest.get("conservative_therapies", []), basename)
    traps = _in(manifest["traps"], basename)
    follow_up = ordinal > 1

    for visit in visits:
        lines.append("FOLLOW-UP VISIT" if follow_up else "ORTHOPEDIC CLINIC VISIT")
        lines.append("")
        purpose = (
            "follow-up of osteoarthritis of the right knee" if follow_up
            else "evaluation of the right knee"
        )
        lines.append(wrap(
            f"{us(visit['date'])} - Seen in person in clinic by "
            f"{identity['supervisor']} for {purpose}."
        ))
        lines.append("")
    lines.append("HISTORY OF PRESENT ILLNESS")
    history = (
        "Here for reassessment of the right knee." if follow_up
        else "Referred for evaluation of the right knee."
    )
    for symptom in symptoms:
        history += " " + KNEE_SYMPTOM_PHRASES[symptom["category"]]
    for symptom in denied:
        history += " " + KNEE_DENIED_PHRASES[symptom["category"]]
    lines.append(wrap(history))
    lines.append("")

    imaging = [t for t in traps if t["type"] in ("hip_radiograph", "radiograph_not_performed")]
    if radiographs or imaging:
        lines.append("IMAGING")
        for radiograph in radiographs:
            report = (
                " ".join(RADIOGRAPH_FINDING_PHRASES[f] for f in radiograph["findings"])
                if radiograph["findings"] else RADIOGRAPH_NORMAL
            )
            lines.append(wrap(
                f"{us(radiograph['date'])} - Weight-bearing radiographs of the "
                f"right knee, standing AP and lateral views: {report}"
            ))
        for trap in imaging:
            phrase = (
                HIP_RADIOGRAPH_PHRASE if trap["type"] == "hip_radiograph"
                else RADIOGRAPH_NOT_PERFORMED_PHRASE
            )
            lines.append(wrap(phrase.format(date=us(trap["date"]))))
        lines.append("")

    declined = [t for t in traps if t["type"] == "therapy_declined"]
    if therapies or declined:
        lines.append("TREATMENT")
        for therapy in therapies:
            phrase = KNEE_THERAPY_PHRASES[therapy["therapy"]].format(date=us(therapy["date"]))
            if therapy["therapy"] == "naproxen" and not visits_include(visits, therapy["date"]):
                phrase = phrase[:-1] + " at a telehealth visit."
            lines.append(wrap(phrase))
        for _ in declined:
            lines.append(wrap(THERAPY_DECLINED_PHRASE))
        lines.append("")

    lines.append("ASSESSMENT AND PLAN")
    if follow_up:
        lines.append(wrap(
            "Osteoarthritis of the right knee. Intra-articular hyaluronan "
            "injection of the right knee is requested."
        ))
    else:
        lines.append(wrap(
            "Osteoarthritis of the right knee. Continue the treatment above and "
            "return for reassessment."
        ))
    return "\n".join(lines).rstrip() + "\n"


def visits_include(visits: list[dict], iso: str) -> bool:
    return any(v["date"] == iso for v in visits)


def is_sleep_chart(manifest: dict) -> bool:
    """A chart the fourth practice's facts describe (T-108, D150)."""
    return bool(manifest.get("sleep_tests") or manifest.get("sleep_evaluations"))


def _hours(value: float) -> str:
    return f"{value:.1f}"


def _index(value: float) -> str:
    return f"{value:g}"


def render_sleep_document(
    manifest: dict,
    basename: str,
    demo: dict,
    identity: dict,
    rng: random.Random,
) -> str:
    """One document of a sleep chart: the consultation or the study report,
    whichever facts the manifest assigns to `basename` (D104, D150).

    Every value comes from the manifest: the dates are the chart's own, and
    the index, the recording time and the findings are declared there. The
    report states the index and the recording time and never their product
    or a verdict, because the arithmetic is the engine's (Art. II).
    """
    documents = manifest["documents"]
    ordinal = documents.index(basename) + 1
    lines: list[str] = [
        identity["practice"],
        f"Patient: {demo['family']}, {demo['given']}"
        f"{' ' * max(1, 38 - len(demo['family']) - len(demo['given']))}"
        f"MRN: {identity['mrn']}",
        f"DOB: {demo['birth_date']}                       Sex: {demo['sex']}",
        f"Document {ordinal} of {len(documents)}",
        "",
    ]
    evaluations = _in(manifest.get("sleep_evaluations", []), basename)
    tests = _in(manifest.get("sleep_tests", []), basename)
    findings = _in(manifest.get("sleep_findings", []), basename)
    denied = _in(manifest.get("denied_findings", []), basename)
    contacts = [t for t in _in(manifest["traps"], basename) if t["type"] == "telephone_contact"]

    for evaluation in evaluations:
        lines.append("SLEEP MEDICINE CONSULTATION")
        lines.append("")
        lines.append(wrap(
            f"{us(evaluation['date'])} - Seen in person in clinic by "
            f"{identity['supervisor']} for evaluation of suspected obstructive "
            "sleep apnea. History taken and examination performed."
        ))
        lines.append("")
        lines.append("HISTORY OF PRESENT ILLNESS")
        history = "Bed partner reports loud nightly snoring and witnessed pauses in breathing."
        for finding in findings:
            history += " " + FINDING_PHRASES[finding["category"]]
        for finding in denied:
            history += " " + DENIED_PHRASES[finding["category"]]
        lines.append(wrap(history))
        lines.append("")
        lines.append("PLAN")
        lines.append(wrap("Diagnostic sleep study ordered to assess for obstructive sleep apnea."))
        lines.append("")
    for contact in contacts:
        lines.append("TELEPHONE ENCOUNTER")
        lines.append(wrap(f"{us(contact['date'])} - {TELEPHONE_PHRASE}"))
        lines.append("")
    for test in tests:
        lines.append("SLEEP STUDY REPORT")
        lines.append("")
        lines.append(wrap(
            f"Study date: {us(test['date'])}. {STUDY_TYPES[test['study_type']]}, "
            "performed without positive airway pressure."
        ))
        recording = f"Total recording time: {_hours(test['recording_hours'])} hours."
        if test["recording_hours"] < 2.0:
            recording += " The study ended early at the patient's request."
        lines.append(wrap(recording))
        lines.append(wrap(
            f"{INDEX_NAMES[test['index_name']]}: {_index(test['index'])} events per hour."
        ))
        lines.append("")

    lines.append("ASSESSMENT")
    if tests:
        lines.append(wrap(
            "Sleep-disordered breathing evaluated by the study reported above. "
            "Results reviewed with the patient."
        ))
    else:
        lines.append(wrap(
            "Suspected obstructive sleep apnea. Diagnostic testing has been "
            "ordered and is documented separately."
        ))
    return "\n".join(lines).rstrip() + "\n"


def _in(facts: list[dict], basename: str) -> list[dict]:
    return [f for f in facts if f["document"] == basename]


def render_document(
    manifest: dict,
    basename: str,
    demo: dict,
    height_m: float,
    identity: dict,
    rng: random.Random,
) -> str:
    """One document of a chart: the facts the manifest assigns to `basename`,
    and nothing else. Every section branches on this document's facts."""
    documents = manifest["documents"]
    ordinal = documents.index(basename) + 1
    earlier = documents[:ordinal - 1]

    lines: list[str] = []
    lines.append(identity["practice"])
    lines.append(
        f"Patient: {demo['family']}, {demo['given']}"
        f"{' ' * max(1, 38 - len(demo['family']) - len(demo['given']))}"
        f"MRN: {identity['mrn']}"
    )
    lines.append(f"DOB: {demo['birth_date']}                       Sex: {demo['sex']}")
    lines.append(f"Document {ordinal} of {len(documents)}")
    lines.append("")

    programs = [
        {**p, "encounters": _in(p["encounters"], basename)}
        for p in manifest["wm_programs"]
    ]
    programs = [p for p in programs if p["encounters"]]
    continued = any(
        e["document"] in earlier
        for p in manifest["wm_programs"] if any(
            e["document"] == basename for e in p["encounters"]
        )
        for e in p["encounters"]
    )
    series_traps = [
        t for t in _in(manifest["traps"], basename)
        if t["type"] in ("missed_visit", "unsuccessful_contact")
    ]
    assertions = _in(manifest["program_assertions"], basename)

    if programs:
        heading = "MEDICAL WEIGHT MANAGEMENT PROGRAM - CONSOLIDATED VISIT RECORD"
        if continued:
            heading += " (CONTINUED)"
        lines.append(heading)
        supervisor = f"Physician-supervised program directed by {identity['supervisor']}."
        if continued:
            supervisor += (
                " Earlier visits in this program are held in a separate "
                "export of this record."
            )
        lines.append(wrap(supervisor))
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

    for assertion in assertions:
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

    other_traps = [t for t in _in(manifest["traps"], basename) if t not in series_traps]
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
    elif assertions:
        lines.append(wrap(
            "Obesity. Candidate pending documentation of the weight management "
            "program described above."
        ))
    else:
        lines.append(wrap(
            "Obesity. No weight management program documentation is present in "
            "this record at this time."
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
        sleep = is_sleep_chart(manifest)
        knee = is_knee_chart(manifest)
        # A sleep or knee chart reads no height: it renders no weight (D150,
        # D154).
        height_m = None if sleep or knee else _height_m(store, patient_id)
        # One generator per patient, seeded from the shared seed and the
        # patient id, so adding a patient cannot reshuffle everyone's prose.
        # A declared clone seeds from its source (D102): the practice line
        # and MRN are the only per-patient randomness, and the clone's note
        # must be its source's bytes.
        seed_patient_id = manifest.get("cloned_from") or patient_id
        identity = _identity(
            random.Random(f"{SEED}:{seed_patient_id}"), sleep=sleep, knee=knee
        )

        out_dir = NOTES_DIR / patient_id
        out_dir.mkdir()
        for basename in manifest["documents"]:
            # One stream per (patient, document): the two documents read
            # differently, and a fact moved between them cannot reshuffle
            # the other's prose (D104).
            rng = random.Random(f"{SEED}:{seed_patient_id}:{basename}")
            if knee:
                text = render_knee_document(manifest, basename, demo, identity, rng)
            elif sleep:
                text = render_sleep_document(manifest, basename, demo, identity, rng)
            else:
                text = render_document(manifest, basename, demo, height_m, identity, rng)
            note_path = out_dir / basename
            note_path.write_text(text, encoding="utf-8")
            record = {
                "patient_id": patient_id,
                "document_id": f"{patient_id}/{basename}",
                "sha256": sha256(note_path),
                "cases": manifest["cases"],
                "characters": len(text),
            }
            if manifest.get("cloned_from"):
                record["cloned_from"] = manifest["cloned_from"]
            records.append(record)
            print(f"  {manifest['cases']} -> {note_path.relative_to(REPO_ROOT)} ({len(text)} chars)")

    NOTES_MANIFEST.write_text(
        json.dumps(
            {"task": "T-81", "decision": "D104", "seed": SEED, "wrap_columns": WRAP,
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
    by_id = {r["document_id"]: r for r in manifest["notes"]}
    by_patient: dict[str, list[dict]] = {}
    for r in manifest["notes"]:
        by_patient.setdefault(r["patient_id"], []).append(r)
    clone_of: dict[str, str] = {}
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        facts = json.loads(path.read_text(encoding="utf-8"))
        patient_id = facts["patient_id"]
        if facts.get("note") is False:
            continue
        # T-81 (D104): every note-bearing chart is at least two documents,
        # exactly the ones the fact manifest declares.
        expected = {f"{patient_id}/{b}" for b in facts["documents"]}
        served = {r["document_id"] for r in by_patient.get(patient_id, [])}
        ok = len(expected) >= 2 and served == expected
        print(("  ok   " if ok else "  FAIL ")
              + f"{patient_id[:12]} has {len(served)} documents, {len(expected)} declared")
        if not ok:
            failures.append(f"documents {patient_id}")
        if facts.get("cloned_from"):
            clone_of[patient_id] = facts["cloned_from"]
    # A declared clone's documents are its source's bytes, per basename
    # (T-88, D102). Checked from the fact manifests, not from the notes
    # manifest's own `cloned_from`, so a clone whose record dropped the field
    # is still held to it.
    for clone_id, source_id in clone_of.items():
        for record in by_patient.get(clone_id, []):
            basename = record["document_id"].split("/", 1)[1]
            source_record = by_id.get(f"{source_id}/{basename}")
            ok = (
                source_record is not None
                and record["sha256"] == source_record["sha256"]
                and record.get("cloned_from") == source_id
            )
            print(("  ok   " if ok else "  FAIL ")
                  + f"{clone_id[:12]}/{basename} is byte-identical to its source {source_id[:12]}")
            if not ok:
                failures.append(f"clone {record['document_id']}")
    # No two documents share bytes unless they are a clone's and its source's:
    # the recorded runner keys its replay by digest, and one digest names one
    # id (D102, D104).
    by_hash: dict[str, list[str]] = {}
    for r in manifest["notes"]:
        by_hash.setdefault(r["sha256"], []).append(r["document_id"])
    for digest, ids in by_hash.items():
        if len(ids) == 1:
            continue
        patients = {i.split("/", 1)[0] for i in ids}
        basenames = {i.split("/", 1)[1] for i in ids}
        legitimate = (
            len(ids) == 2 and len(basenames) == 1
            and any(clone_of.get(a) == b for a in patients for b in patients)
        )
        if not legitimate:
            print(f"  FAIL identical bytes across {sorted(ids)}")
            failures.append(f"duplicate {digest[:12]}")
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
