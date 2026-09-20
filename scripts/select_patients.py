"""T-04 + T-41 + T-88 + T-93 — generate, select and clone the Synthea
population (D35, D73, D102, D113).

Four modes:

    python scripts/select_patients.py --generate
        Downloads the pinned Synthea release jar if absent, then runs Synthea
        three times with recorded seeds: the base run selects six bundles whose
        most-recent BMI observations span 33 to 45 (D35), the E12 run
        yields a seventh patient to whose bundle one synthetic BMI observation
        of exactly 35.0 is appended under a declared provenance block (D73),
        and the rheumatology run below supplies the second practice's charts.
        Copies them all to data/patients/bundles/, writes
        data/patients/manifest.json, then performs the clones below. Network
        and Java.

    python scripts/select_patients.py --generate-rheumatology
        The second practice's cohort (T-93, D113): one Synthea run in
        Palmetto GBA's territory under its own recorded seed, from which two
        charts are selected mechanically — one carrying an active rheumatoid
        arthritis diagnosis *and* an active methotrexate order, one carrying
        the diagnosis and no active methotrexate. Its own mode because a
        rerun of `--generate` would rebuild the bariatric six, which D73
        forbids adopting wholesale. Runs the clones after it, since one of
        them is derived from a chart this run produces. Java, no network once
        the jar is present.

    python scripts/select_patients.py --clone
        The declared derivatives. Each is a deterministic function of its
        source's bytes and its declaration in `CLONES` — the source id
        substituted everywhere it occurs, then the declared edits: an address
        rewrite (T-88's Washington chart re-addressed into Palmetto's
        territory, D102) or an appended prescription (T-93's chart carrying
        the drug L35677's limitation excludes, which Synthea's rheumatoid
        arthritis module never orders, D113). The result is serialized the way
        the E12 bundle is, so a clone can be recomputed and compared rather
        than trusted. Disk only.

    python scripts/select_patients.py [--verify]
        The exit condition. Re-reads the disk and nothing else: hashes the
        eleven committed bundles against the manifest, re-extracts each
        most-recent BMI and each rheumatology fact from the bundle itself,
        re-asserts the span conditions and the recorded seeds, checks the
        declared synthetic observation exists, matches its declaration, and is
        the most-recent BMI, and recomputes every declared clone from its
        source and compares bytes. No network, no Java, no generation, no
        model.

The committed bundles are corpus, exactly as T-02's documents are: everything
downstream (T-05, T-06, T-12) reads these eleven files, and a bundle whose hash
drifts fails the gate rather than quietly feeding a different patient to the
tests. Selection is deterministic given the generated populations —  but
Synthea itself is not byte-deterministic across runs (D73: one of six base
bundles reproduced with different bytes under an identical command), so after
a regeneration any base bundle whose hash drifts from the committed manifest
is restored from git rather than adopted.

Two cohorts, one population. The six base bundles and the two derived from
them answer to D35's BMI spread; the two rheumatology charts answer to D113's
selection rule and have no BMI condition on them at all. Each record declares
which, because a script that checked D35's band over every bundle would fail
on a chart that was never selected for it.

Tooling, not the system under test: the system reads patients only through
`PatientStore` (REQ-41); this script is the thing that puts files on disk for
the local adapter to serve.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import uuid
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENTS_DIR = REPO_ROOT / "data" / "patients"
BUNDLES_DIR = PATIENTS_DIR / "bundles"
MANIFEST_PATH = PATIENTS_DIR / "manifest.json"
WORK_DIR = PATIENTS_DIR / "work"
JAR_PATH = WORK_DIR / "synthea-with-dependencies.jar"
OUTPUT_DIR = WORK_DIR / "output"

# The pin (D35). A tagged release asset, not master-branch-latest, which is a
# moving nightly — the same URL serving different bytes after every merge.
SYNTHEA_VERSION = "v4.0.0"
JAR_URL = (
    "https://github.com/synthetichealth/synthea/releases/download/"
    f"{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
)

# Generation knobs, all recorded in the manifest. Washington is inside Noridian
# Jurisdiction F (D21), so the population is plausible for the jurisdiction the
# criteria tree governs.
SEED = 100_1  # NCD 100.1
CLINICIAN_SEED = SEED
REFERENCE_DATE = "20260901"
POPULATION = 200
AGE_RANGE = "30-60"
STATE = "Washington"

# What "spanning 33 to 45" means, mechanically (D35).
BMI_FLOOR = 33.0
BMI_CEILING = 45.0
BMI_THRESHOLD = 35.0  # criterion (a)'s boundary; at least one patient below it
BMI_HIGH_MARK = 40.0  # and at least one at or above this
BASE_BUNDLE_COUNT = 6  # the D35 selection
#: plus the E12 patient (D73), the T-88 clone (D102) and T-93's three
#: rheumatology charts — two generated, one derived (D113).
BUNDLE_COUNT = 11

LOINC_BMI = "39156-5"
SNOMED_T2DM = "44054006"

#: The cohort a bundle was selected for, and therefore the rule `--verify`
#: holds it to (D113). Not a property of the patient: the same chart selected
#: for a different practice would carry a different value.
BARIATRIC_COHORT = "bariatric"
RHEUMATOLOGY_COHORT = "rheumatology"

# The E12 patient (T-41, D73). A second Synthea run under its own recorded
# seed yields a base patient; one synthetic BMI observation — a clone of the
# patient's own most-recent BMI observation with a new id, this date, and
# value exactly 35.0 — is appended so the structured most-recent BMI sits on
# criterion (a)'s inclusive boundary. Everything here is a decision recorded
# in D73, not a measurement.
E12_SEED = 1002
E12_OUTPUT_DIR = WORK_DIR / "output_e12"
E12_BMI = 35.0  # the boundary itself
E12_BASE_BAND = (33.0, 37.0)  # the base patient's natural BMI must be nearby
E12_SYNTHETIC_DATE = "2026-08-15"  # in-window from the 2026-09-01 reference
LOOKBACK_MONTHS = 12  # D40; the synthetic observation must be in-window

# The rheumatology cohort (T-93, D113). A third Synthea run, in Palmetto GBA's
# territory so the second practice's tree is the one a request resolves to, and
# under the next seed in the sequence. The codes below are read out of
# `modules/rheumatoid_arthritis.json` inside the pinned jar, which is what makes
# them the codes a chart in *this* corpus carries rather than ones recalled:
# the module's ConditionOnset is SNOMED 69896004 and its DMARD state orders
# RxNorm 105585. It orders no biologic and no Janus kinase inhibitor at all,
# which is why the excluded drug below is appended by declaration and never
# selected for.
RA_SEED = 1003
RA_CLINICIAN_SEED = RA_SEED
RA_POPULATION = 1000  # ~1% incidence in the module's own Initial state
RA_STATE = "Alabama"  # Palmetto GBA, Jurisdictions J and M (D101, D111)
#: What the bundle actually carries, and what a request resolves by. The
#: two-letter form is asserted against each chart; that this state is served
#: by the rheumatology tree is the policy plane's claim and the suite's.
RA_STATE_CODE = "AL"
RA_OUTPUT_DIR = WORK_DIR / "output_ra"
SNOMED_RA = "69896004"
RXNORM_METHOTREXATE = "105585"
RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
#: The one drug the declared derivative adds. A member of the committed
#: `biologic_dmards_and_jak_inhibitors` value set and the brand L35677's
#: LIMITATIONS paragraph names first; the set itself is the policy plane's and
#: is not read here — the suite is where membership is asserted (Art. VI).
RXNORM_ETANERCEPT = "802652"
ETANERCEPT_DISPLAY = "1 ML etanercept 50 MG/ML Prefilled Syringe [Enbrel]"

# The second-jurisdiction patient (T-88, D102). One committed bundle cloned
# and re-addressed into Palmetto GBA's territory, so a determination can run
# under `ncd-100.1-jjm-v1` on a chart whose note T-15's recording already
# holds. The source is E4's chart: a three-month run that is a `c3`
# shortfall under Noridian and, under Palmetto, not a criterion at all. The
# new id is uuid5 over the source id and the state, so the declaration is
# the derivation and nothing here is chosen twice. Everything in this block
# is a decision recorded in D102, not a measurement.
#
# T-93 (D113) adds the second: the rheumatology chart that would otherwise
# meet both criteria the system can evaluate, carrying one appended
# prescription for the drug L35677 excludes. It stays in its source's state —
# the variant is the prescription, not the jurisdiction — so it declares no
# address, and the pair differs by exactly one resource, which is what makes
# the denial row legible.
CLONES = [
    {
        "cloned_from": "07a5f345-3e7c-da0f-da0b-87fa252a5bfd",
        "state": "AL",
        "city": "Birmingham",
        "postal_code": "35203",
        "latitude": 33.5186,
        "longitude": -86.8104,
        "task": "T-88",
        "decision": "D102",
    },
    {
        "cloned_from": "42a430ab-b7ca-87a5-279f-ee115f49fd6e",
        "variant": "concurrent-biologic",
        "add_medication": {
            # Copied from the patient's own methotrexate order so every
            # reference it carries — subject, encounter, requester — stays
            # one the bundle resolves. The drug and the resource id are the
            # only fields that move (D73's shape for the E12 observation).
            "copy_of_code": RXNORM_METHOTREXATE,
            "resource_id_suffix": "-ra-excl-synthetic",
            "code": RXNORM_ETANERCEPT,
            "system": RXNORM_SYSTEM,
            "display": ETANERCEPT_DISPLAY,
        },
        "task": "T-93",
        "decision": "D113",
    },
]


def clone_patient_id(declaration: dict) -> str:
    """uuid5 over the source id and whatever the declaration changes.

    The key names the declared difference, so two derivatives of one chart
    cannot collide and T-88's id — keyed on its state alone — is unmoved
    (D102, D113).
    """
    key = ":".join(
        [declaration["cloned_from"]]
        + [declaration[field] for field in ("state", "variant") if field in declaration]
    )
    return str(uuid.uuid5(uuid.NAMESPACE_OID, key))


def clone_bundle(source_text: str, declaration: dict) -> tuple[str, str]:
    """The clone's bytes, as a function of the source's and the declaration.

    Returns `(patient_id, text)`. Substituting the id in the *text* rewrites
    `Patient.id`, every `fullUrl`, every `subject.reference` and anything else
    that names the patient, in one pass and with no list of fields to keep
    current. The declared edits — an address (D102), an appended prescription
    (D113) — are then applied to the parsed bundle and the whole thing
    serialized compactly, D73's form for the E12 bundle and the reason
    `--verify` can compare bytes rather than fields.

    Both edits are optional and each is declared. A clone that declares
    neither is a re-identified copy, which is a thing this function will
    happily produce and no declaration asks for; what it will not do is edit
    anything the declaration does not name.
    """
    source_id = declaration["cloned_from"]
    new_id = clone_patient_id(declaration)
    if source_id not in source_text:
        sys.exit(f"the source bundle never names {source_id}; nothing to clone")
    bundle = json.loads(source_text.replace(source_id, new_id))
    patients = [
        e["resource"]
        for e in bundle["entry"]
        if e.get("resource", {}).get("resourceType") == "Patient"
    ]
    if len(patients) != 1 or patients[0].get("id") != new_id:
        sys.exit("the clone must carry exactly one Patient resource under the new id")
    if "state" in declaration:
        address = patients[0]["address"][0]
        address["city"] = declaration["city"]
        address["state"] = declaration["state"]
        address["postalCode"] = declaration["postal_code"]
        for extension in address.get("extension", []):
            if extension.get("url") == "http://hl7.org/fhir/StructureDefinition/geolocation":
                for coordinate in extension.get("extension", []):
                    if coordinate.get("url") == "latitude":
                        coordinate["valueDecimal"] = declaration["latitude"]
                    elif coordinate.get("url") == "longitude":
                        coordinate["valueDecimal"] = declaration["longitude"]
    if "add_medication" in declaration:
        bundle["entry"].append(
            _declared_medication_entry(bundle, declaration["add_medication"])
        )
    return new_id, json.dumps(bundle, ensure_ascii=False)


def _declared_medication_entry(bundle: dict, declaration: dict) -> dict:
    """One `MedicationRequest`, copied from one the chart already carries.

    The copy is what keeps the appended resource honest: its subject,
    encounter, requester and dates are the source order's, so the chart reads
    as a chart rather than as a resource written to be matched, and the
    declaration lists everything that differs — the resource id and the drug
    (D73's shape for the E12 observation, D113).

    Raises rather than inventing an order if the chart carries no copy source,
    and refuses to append a drug the chart already carries: either would make
    the derivative's one declared difference stop being one.
    """
    source = None
    for entry in bundle["entry"]:
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "MedicationRequest":
            continue
        codes = [
            c.get("code")
            for c in resource.get("medicationCodeableConcept", {}).get("coding", [])
        ]
        if declaration["code"] in codes:
            sys.exit(
                f"the source chart already carries {declaration['code']}; the "
                "declared addition would not be the difference it claims to be"
            )
        if declaration["copy_of_code"] in codes and source is None:
            source = entry
    if source is None:
        sys.exit(
            f"the source chart carries no MedicationRequest for "
            f"{declaration['copy_of_code']} to copy"
        )
    entry = copy.deepcopy(source)
    resource = entry["resource"]
    resource["id"] = resource["id"] + declaration["resource_id_suffix"]
    entry["fullUrl"] = f"urn:uuid:{resource['id']}"
    resource["medicationCodeableConcept"] = {
        "coding": [
            {
                "system": declaration["system"],
                "code": declaration["code"],
                "display": declaration["display"],
            }
        ],
        "text": declaration["display"],
    }
    return entry


def clone_filename(source_filename: str, declaration: dict) -> str:
    """Synthea's `<names>_<uuid>.json`, with the uuid swapped — the names stay,
    because the clone is the same chart re-addressed, not a new person."""
    return source_filename.replace(declaration["cloned_from"], clone_patient_id(declaration))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------
# Reading a bundle. Shared by selection and verification so the gate re-derives
# the number it checks with the same code that chose it.
# --------------------------------------------------------------------------


def _codings(resource: dict, field: str = "code"):
    return resource.get(field, {}).get("coding", [])


def read_bundle(path: Path) -> dict:
    """Patient id, most-recent BMI observation, and the coded facts the two
    cohorts are selected on: an active type 2 diabetes condition (D35's E2
    shape), and since T-93 an active rheumatoid arthritis condition, an active
    methotrexate order and an active order for the declared excluded drug
    (D113).

    Codes, not value sets. Every fact here is one code comparison against a
    constant read out of the Synthea module or named by a declaration; what
    those codes *mean* to a criteria tree is the policy plane's and is
    asserted in the suite, which may read both planes (Art. VI).
    """
    bundle = json.loads(path.read_text(encoding="utf-8"))
    patient_id = None
    bmi_obs: list[tuple[str, float]] = []
    has_t2dm = False
    has_active_ra = False
    has_active_methotrexate = False
    has_active_excluded_biologic = False
    methotrexate_orders = 0
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType")
        if rtype == "Patient" and patient_id is None:
            patient_id = resource["id"]
        elif rtype == "Observation":
            if any(c.get("code") == LOINC_BMI for c in _codings(resource)):
                when = resource.get("effectiveDateTime")
                value = resource.get("valueQuantity", {}).get("value")
                if when and value is not None:
                    bmi_obs.append((when, float(value)))
        elif rtype == "Condition":
            codes = {c.get("code") for c in _codings(resource)}
            active = any(
                c.get("code") == "active"
                for c in _codings(resource, "clinicalStatus")
            )
            if active and SNOMED_T2DM in codes:
                has_t2dm = True
            if active and SNOMED_RA in codes:
                has_active_ra = True
        elif rtype == "MedicationRequest":
            # `status` is carried by the record and read here the way the
            # predicate reads it: a completed order is not an active one.
            codes = {
                c.get("code")
                for c in _codings(resource, "medicationCodeableConcept")
            }
            if RXNORM_METHOTREXATE in codes:
                methotrexate_orders += 1
            active = resource.get("status") == "active"
            if active and RXNORM_METHOTREXATE in codes:
                has_active_methotrexate = True
            if active and RXNORM_ETANERCEPT in codes:
                has_active_excluded_biologic = True
    latest = max(bmi_obs) if bmi_obs else None  # ISO dates sort lexically
    return {
        "patient_id": patient_id,
        "latest_bmi": latest[1] if latest else None,
        "latest_bmi_date": latest[0] if latest else None,
        "has_active_t2dm": has_t2dm,
        "has_active_ra": has_active_ra,
        "has_active_methotrexate": has_active_methotrexate,
        "has_active_excluded_biologic": has_active_excluded_biologic,
        "methotrexate_orders": methotrexate_orders,
    }


# --------------------------------------------------------------------------
# Generation (--generate): network and Java, never run by the gate
# --------------------------------------------------------------------------


def _ensure_jar() -> str:
    if not JAR_PATH.exists():
        print(f"downloading Synthea {SYNTHEA_VERSION} …")
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(JAR_URL, JAR_PATH)  # noqa: S310 — pinned https URL
    digest = sha256(JAR_PATH)
    print(f"jar sha256 {digest}")
    return digest


def _run_synthea(
    seed: int,
    output_dir: Path,
    state: str = STATE,
    population: int = POPULATION,
) -> list[str]:
    """One recorded Synthea run. State and population are arguments because
    T-93's cohort is a third run in another jurisdiction (D113); everything
    else is pinned for every run so the three populations differ only in what
    the manifest says they differ in."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    command = [
        "java",
        "-jar",
        # Repo-relative in the record: the manifest is committed and read by
        # someone else's checkout.
        str(JAR_PATH.relative_to(REPO_ROOT)),
        "-s",
        str(seed),
        "-cs",
        str(seed),
        "-r",
        REFERENCE_DATE,
        "-p",
        str(population),
        "-a",
        AGE_RANGE,
        "--exporter.baseDirectory",
        str(output_dir.relative_to(REPO_ROOT)),
        "--exporter.fhir.export",
        "true",
        "--exporter.hospital.fhir.export",
        "false",
        "--exporter.practitioner.fhir.export",
        "false",
        state,
    ]
    print("running:", " ".join(command))
    subprocess.run(
        [str(REPO_ROOT / c) if c.startswith("data/patients/") else c for c in command],
        check=True,
        cwd=WORK_DIR,
    )
    return command


def _read_population(output_dir: Path) -> list[dict]:
    fhir_dir = output_dir / "fhir"
    candidates = []
    for path in sorted(fhir_dir.glob("*.json")):
        if path.name.startswith(("hospitalInformation", "practitionerInformation")):
            continue
        info = read_bundle(path)
        info["source_path"] = path
        candidates.append(info)
    return candidates


def _select(candidates: list[dict]) -> list[dict]:
    """Six patients whose most-recent BMIs span [33, 45]: one below 35 —
    preferring one that also carries active T2DM, E2's shape — one at or above
    40, and the rest spread across what remains. Deterministic over the
    generated population: sorted inputs, no randomness."""
    in_range = sorted(
        (c for c in candidates if c["latest_bmi"] is not None
         and BMI_FLOOR <= c["latest_bmi"] <= BMI_CEILING),
        key=lambda c: (c["latest_bmi"], c["patient_id"]),
    )
    low = [c for c in in_range if c["latest_bmi"] < BMI_THRESHOLD]
    high = [c for c in in_range if c["latest_bmi"] >= BMI_HIGH_MARK]
    if not low or not high:
        sys.exit(
            f"the population cannot span {BMI_FLOOR}–{BMI_CEILING}: "
            f"{len(low)} candidates below {BMI_THRESHOLD}, "
            f"{len(high)} at or above {BMI_HIGH_MARK}. D35's reversal clause: "
            "adjust POPULATION/AGE_RANGE and re-record, or supersede the entry."
        )
    low_pick = next((c for c in low if c["has_active_t2dm"]), low[0])
    high_pick = high[-1]
    chosen = [low_pick, high_pick]
    rest = [c for c in in_range if c not in chosen]
    # Fill to six, spread evenly across the remaining BMI ordering.
    needed = BASE_BUNDLE_COUNT - len(chosen)
    if len(rest) < needed:
        sys.exit(f"only {len(in_range)} candidates in range; need {BASE_BUNDLE_COUNT}")
    step = len(rest) / needed
    chosen += [rest[int(i * step)] for i in range(needed)]
    return sorted(chosen, key=lambda c: c["latest_bmi"])


def _pick_e12_base(candidates: list[dict]) -> dict:
    """The E12 base patient, deterministically (D73): no active T2DM (E2's
    shape must stay E2's), a natural latest BMI near enough to 35.0 that the
    appended value is clinically plausible, and a natural latest date strictly
    before the synthetic date so "most recent" is unambiguous."""
    low, high = E12_BASE_BAND
    eligible = [
        c for c in candidates
        if c["latest_bmi"] is not None
        and low <= c["latest_bmi"] <= high
        and not c["has_active_t2dm"]
        and c["latest_bmi_date"][:10] < E12_SYNTHETIC_DATE
    ]
    if not eligible:
        sys.exit(
            f"seed {E12_SEED} yields no E12 base patient (natural BMI in "
            f"{E12_BASE_BAND}, no active T2DM, latest observation before "
            f"{E12_SYNTHETIC_DATE}). Bump E12_SEED and re-run; the recorded "
            "seed is whichever worked (D73)."
        )
    return min(
        eligible, key=lambda c: (abs(c["latest_bmi"] - E12_BMI), c["patient_id"])
    )


def _select_rheumatology(candidates: list[dict]) -> tuple[dict, dict]:
    """The two generated rheumatology charts, deterministically (D113).

    Both carry an **active** rheumatoid arthritis diagnosis, which is criterion
    (a)'s fact; they differ on criterion (b)'s. One carries an active
    methotrexate order and one carries none, so the pair is the difference
    between the drug criterion answering `MET` and abstaining — never
    `NOT_MET`, which is the asymmetry D40 set and D111 carried to medications.

    Neither may carry the declared excluded drug: the exclusion fires before
    any criterion, so a chart that trips it cannot demonstrate a criterion at
    all. Synthea orders no biologic for rheumatoid arthritis, so this filter
    is a guard against a later module release rather than a live one — and
    that is exactly when a silent selection change would be worst.
    """
    eligible = sorted(
        (
            c
            for c in candidates
            if c["has_active_ra"] and not c["has_active_excluded_biologic"]
        ),
        key=lambda c: c["patient_id"],
    )
    with_drug = [c for c in eligible if c["has_active_methotrexate"]]
    without_drug = [c for c in eligible if not c["has_active_methotrexate"]]
    if not with_drug or not without_drug:
        sys.exit(
            f"seed {RA_SEED} yields {len(with_drug)} chart(s) with an active "
            f"methotrexate order and {len(without_drug)} without, out of "
            f"{len(eligible)} with an active rheumatoid arthritis diagnosis; "
            "one of each is required. The module's incidence is 1% and its "
            "DMARD runs 40-60 months, so raise RA_POPULATION or bump RA_SEED "
            "and re-record — the recorded seed is whichever worked (D113)."
        )
    return with_drug[0], without_drug[0]


def _append_synthetic_observation(source_path: Path, dest: Path) -> dict:
    """Clone the patient's own most-recent BMI observation into a new entry —
    new id, the synthetic date, value exactly 35.0 — append it, and write the
    bundle. Returns the provenance record the manifest declares (D73)."""
    bundle = json.loads(source_path.read_text(encoding="utf-8"))
    latest = None
    for entry in bundle["entry"]:
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Observation":
            continue
        if not any(c.get("code") == LOINC_BMI for c in _codings(resource)):
            continue
        when = resource.get("effectiveDateTime")
        if when and (latest is None or when > latest["resource"]["effectiveDateTime"]):
            latest = entry
    if latest is None:
        sys.exit("the E12 base bundle has no BMI observation to clone")

    clone = copy.deepcopy(latest)
    resource = clone["resource"]
    resource["id"] = f"{resource['id']}-e12-synthetic"
    clone["fullUrl"] = f"urn:uuid:{resource['id']}"
    # Keep the original time-of-day and offset; move only the calendar date.
    resource["effectiveDateTime"] = (
        E12_SYNTHETIC_DATE + resource["effectiveDateTime"][10:]
    )
    if "issued" in resource:
        resource["issued"] = E12_SYNTHETIC_DATE + resource["issued"][10:]
    resource["valueQuantity"]["value"] = E12_BMI
    bundle["entry"].append(clone)

    dest.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return {
        "patient_id": read_bundle(dest)["patient_id"],
        "resource_id": resource["id"],
        "code": LOINC_BMI,
        "value": E12_BMI,
        "effective_date": resource["effectiveDateTime"],
        "bundle": dest.name,
        "task": "T-41",
        "decision": "D73",
    }


def _record(path: Path, cohort: str) -> dict:
    """One bundle's manifest record: the hash that pins it, and every fact
    `read_bundle` derives from it. Written by all three writers so a record
    cannot differ in shape by which mode produced it."""
    info = read_bundle(path)
    return {
        "filename": path.name,
        "sha256": sha256(path),
        "cohort": cohort,
        "patient_id": info["patient_id"],
        "latest_bmi": info["latest_bmi"],
        "latest_bmi_date": info["latest_bmi_date"],
        "has_active_t2dm": info["has_active_t2dm"],
        "has_active_ra": info["has_active_ra"],
        "has_active_methotrexate": info["has_active_methotrexate"],
        "has_active_excluded_biologic": info["has_active_excluded_biologic"],
        "methotrexate_orders": info["methotrexate_orders"],
    }


def generate() -> int:
    jar_digest = _ensure_jar()
    command = _run_synthea(SEED, OUTPUT_DIR)
    candidates = _read_population(OUTPUT_DIR)
    print(f"{len(candidates)} patient bundles generated (base run)")
    chosen = _select(candidates)

    e12_command = _run_synthea(E12_SEED, E12_OUTPUT_DIR)
    e12_candidates = _read_population(E12_OUTPUT_DIR)
    print(f"{len(e12_candidates)} patient bundles generated (E12 run)")
    e12_base = _pick_e12_base(e12_candidates)

    if BUNDLES_DIR.exists():
        shutil.rmtree(BUNDLES_DIR)
    BUNDLES_DIR.mkdir(parents=True)
    records = []
    for c in chosen:
        dest = BUNDLES_DIR / c["source_path"].name
        shutil.copyfile(c["source_path"], dest)
        records.append(_record(dest, BARIATRIC_COHORT))

    e12_dest = BUNDLES_DIR / e12_base["source_path"].name
    provenance = _append_synthetic_observation(e12_base["source_path"], e12_dest)
    records.append(_record(e12_dest, BARIATRIC_COHORT))

    manifest = {
        "task": "T-04",
        "decision": "D35",
        "synthea": {
            "version": SYNTHEA_VERSION,
            "jar_url": JAR_URL,
            "jar_sha256": jar_digest,
            "seed": SEED,
            "clinician_seed": CLINICIAN_SEED,
            "reference_date": REFERENCE_DATE,
            "population": POPULATION,
            "age_range": AGE_RANGE,
            "state": STATE,
            "command": command,
        },
        "synthea_e12": {
            "task": "T-41",
            "decision": "D73",
            "seed": E12_SEED,
            "clinician_seed": E12_SEED,
            "reference_date": REFERENCE_DATE,
            "population": POPULATION,
            "age_range": AGE_RANGE,
            "state": STATE,
            "command": e12_command,
        },
        "synthetic_observations": [provenance],
        "selection_criteria": {
            "bundle_count": BUNDLE_COUNT,
            "base_bundle_count": BASE_BUNDLE_COUNT,
            "bmi_range": [BMI_FLOOR, BMI_CEILING],
            "min_below": BMI_THRESHOLD,
            "max_at_least": BMI_HIGH_MARK,
            "bmi_source": f"most recent Observation LOINC {LOINC_BMI}",
        },
        "bundles": records,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for r in records:
        t2dm = " (active T2DM)" if r["has_active_t2dm"] else ""
        print(f"  {r['latest_bmi']:5.1f} on {r['latest_bmi_date'][:10]}  {r['filename']}{t2dm}")
    print(f"manifest written: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return generate_rheumatology()


# --------------------------------------------------------------------------
# The rheumatology cohort (--generate-rheumatology): Java, no network once the
# jar is present (T-93, D113)
# --------------------------------------------------------------------------


def generate_rheumatology() -> int:
    """Run Synthea in Palmetto's territory and adopt the two selected charts.

    Re-runnable and additive: it replaces its own two records and leaves the
    bariatric cohort untouched, because a rerun of `--generate` would rebuild
    the six bundles D73 says are never adopted wholesale after a regeneration.
    Ends in `clone()`, since the declared derivative's source is a chart this
    run produces.
    """
    jar_digest = _ensure_jar()
    command = _run_synthea(RA_SEED, RA_OUTPUT_DIR, state=RA_STATE, population=RA_POPULATION)
    candidates = _read_population(RA_OUTPUT_DIR)
    print(f"{len(candidates)} patient bundles generated (rheumatology run)")
    with_drug, without_drug = _select_rheumatology(candidates)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    records = []
    for chosen in (with_drug, without_drug):
        dest = BUNDLES_DIR / chosen["source_path"].name
        shutil.copyfile(chosen["source_path"], dest)
        record = _record(dest, RHEUMATOLOGY_COHORT)
        manifest["bundles"] = [
            r for r in manifest["bundles"] if r["patient_id"] != record["patient_id"]
        ]
        manifest["bundles"].append(record)
        records.append(record)
        print(
            f"  {record['patient_id'][:12]} active RA, "
            f"{'active' if record['has_active_methotrexate'] else 'no active'} "
            f"methotrexate ({record['methotrexate_orders']} order(s) on the "
            f"chart)  {dest.name}"
        )

    manifest["synthea_rheumatology"] = {
        "task": "T-93",
        "decision": "D113",
        "seed": RA_SEED,
        "clinician_seed": RA_CLINICIAN_SEED,
        "reference_date": REFERENCE_DATE,
        "population": RA_POPULATION,
        "age_range": AGE_RANGE,
        "state": RA_STATE,
        "jar_sha256": jar_digest,
        "command": command,
        "selection": {
            "cohort": RHEUMATOLOGY_COHORT,
            "chart_count": 2,
            "state_code": RA_STATE_CODE,
            "diagnosis_code": SNOMED_RA,
            "drug_code": RXNORM_METHOTREXATE,
            "excluded_drug_code": RXNORM_ETANERCEPT,
            "rule": (
                "both charts carry an active Condition coded SNOMED "
                f"{SNOMED_RA} and no active MedicationRequest for RxNorm "
                f"{RXNORM_ETANERCEPT}; one carries an active MedicationRequest "
                f"for RxNorm {RXNORM_METHOTREXATE} and one carries none. "
                "Codes read from modules/rheumatoid_arthritis.json in the "
                "pinned jar (D113)."
            ),
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"manifest written: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return clone()


# --------------------------------------------------------------------------
# The clone (--clone): disk only, no Java, no network (T-88, D102)
# --------------------------------------------------------------------------


def clone() -> int:
    """Write the declared clone from its committed source and record it.

    Re-runnable: an existing clone record is replaced, never duplicated, and
    the source is read from the committed population — so after a
    regeneration that restored a drifted base bundle from git (D73), this
    re-derives the clone from the bytes the manifest pins.
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    by_patient = {r["patient_id"]: r for r in manifest["bundles"]}
    provenance = []
    for declaration in CLONES:
        source = by_patient.get(declaration["cloned_from"])
        if source is None:
            sys.exit(f"clone source {declaration['cloned_from']} is not in the population")
        source_path = BUNDLES_DIR / source["filename"]
        new_id, text = clone_bundle(source_path.read_text(encoding="utf-8"), declaration)
        dest = BUNDLES_DIR / clone_filename(source["filename"], declaration)
        dest.write_text(text, encoding="utf-8")
        manifest["bundles"] = [r for r in manifest["bundles"] if r["patient_id"] != new_id]
        # A derivative inherits its source's cohort: it is the same chart with
        # one declared edit, and holding it to a different rule would be
        # holding it to a rule nothing selected it by.
        manifest["bundles"].append(_record(dest, source.get("cohort", BARIATRIC_COHORT)))
        record = {
            "patient_id": new_id,
            "cloned_from": declaration["cloned_from"],
            "source_bundle": source["filename"],
            "bundle": dest.name,
            "task": declaration["task"],
            "decision": declaration["decision"],
        }
        if "state" in declaration:
            record["address"] = {
                "city": declaration["city"],
                "state": declaration["state"],
                "postal_code": declaration["postal_code"],
                "latitude": declaration["latitude"],
                "longitude": declaration["longitude"],
            }
        if "variant" in declaration:
            record["variant"] = declaration["variant"]
        if "add_medication" in declaration:
            added = declaration["add_medication"]
            record["synthetic_medication"] = {
                "copy_of_code": added["copy_of_code"],
                "resource_id_suffix": added["resource_id_suffix"],
                "code": added["code"],
                "system": added["system"],
                "display": added["display"],
            }
        provenance.append(record)
        changed = declaration.get("state") or declaration.get("variant")
        print(f"  clone {new_id} <- {declaration['cloned_from']} ({changed}) -> {dest.name}")
    manifest["synthetic_patients"] = provenance
    manifest["selection_criteria"]["bundle_count"] = BUNDLE_COUNT
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"manifest written: {MANIFEST_PATH.relative_to(REPO_ROOT)}")
    return verify()


# --------------------------------------------------------------------------
# Verification (the exit condition): disk only
# --------------------------------------------------------------------------


#: The coded facts a record carries beyond the BMI, re-derived by `--verify`
#: (D113). Named once so a fact added to `read_bundle` and forgotten here is a
#: fact nothing checks.
_DERIVED_FACTS = (
    "has_active_t2dm",
    "has_active_ra",
    "has_active_methotrexate",
    "has_active_excluded_biologic",
    "methotrexate_orders",
)


def _check(condition: bool, message: str, failures: list[str]) -> None:
    print(("  ok  " if condition else "  FAIL") + " " + message)
    if not condition:
        failures.append(message)


def _months_before(as_of: date, d: date) -> int:
    months = (as_of.year - d.year) * 12 + (as_of.month - d.month)
    if as_of.day < d.day:
        months -= 1
    return months


def verify() -> int:
    failures: list[str] = []
    print("T-04/T-41/T-88 verify —")

    if not MANIFEST_PATH.exists():
        print(f"  FAIL no manifest at {MANIFEST_PATH.relative_to(REPO_ROOT)}; run --generate")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    seed = manifest.get("synthea", {}).get("seed")
    _check(isinstance(seed, int), f"seed recorded: {seed!r}", failures)
    e12_seed = manifest.get("synthea_e12", {}).get("seed")
    _check(isinstance(e12_seed, int), f"E12 seed recorded: {e12_seed!r}", failures)
    ra_seed = manifest.get("synthea_rheumatology", {}).get("seed")
    _check(isinstance(ra_seed, int), f"rheumatology seed recorded: {ra_seed!r}", failures)

    records = manifest.get("bundles", [])
    _check(
        len(records) == BUNDLE_COUNT,
        f"manifest lists {len(records)} bundles (need {BUNDLE_COUNT})",
        failures,
    )
    on_disk = {p.name for p in BUNDLES_DIR.glob("*.json")} if BUNDLES_DIR.exists() else set()
    listed = {r["filename"] for r in records}
    _check(
        on_disk == listed,
        "bundles on disk match the manifest exactly "
        f"(strays: {sorted(on_disk - listed) or 'none'}, "
        f"missing: {sorted(listed - on_disk) or 'none'})",
        failures,
    )

    bmis: list[float] = []
    patient_ids: list[str] = []
    for r in records:
        path = BUNDLES_DIR / r["filename"]
        if not path.exists():
            continue  # already reported above
        _check(
            sha256(path) == r["sha256"],
            f"{r['filename']}: content hash matches the manifest",
            failures,
        )
        info = read_bundle(path)
        _check(
            info["patient_id"] == r["patient_id"]
            and info["latest_bmi"] == r["latest_bmi"]
            and info["latest_bmi_date"] == r["latest_bmi_date"],
            f"{r['filename']}: re-extracted patient and most-recent BMI "
            f"({info['latest_bmi']} on {str(info['latest_bmi_date'])[:10]}) "
            "match the manifest",
            failures,
        )
        # T-93 (D113): every coded fact the manifest records is re-derived from
        # the bundle, not just the BMI. A record that says a chart carries no
        # active methotrexate while the chart does is a label the eval set
        # rests on, and it would be invisible from the hash.
        derived = {k: info[k] for k in _DERIVED_FACTS}
        _check(
            all(r.get(k) == v for k, v in derived.items()),
            f"{r['filename']}: re-derived coded facts match the manifest "
            f"({', '.join(f'{k}={v}' for k, v in derived.items())})",
            failures,
        )
        _check(
            r.get("cohort") in (BARIATRIC_COHORT, RHEUMATOLOGY_COHORT),
            f"{r['filename']}: declares a known cohort ({r.get('cohort')!r})",
            failures,
        )
        if info["latest_bmi"] is not None and r.get("cohort") == BARIATRIC_COHORT:
            bmis.append(info["latest_bmi"])
        patient_ids.append(info["patient_id"])

    _check(
        len(set(patient_ids)) == len(records),
        f"{BUNDLE_COUNT} distinct patients",
        failures,
    )

    # The synthetic observation (T-41, D73): declared exactly once, present in
    # the declared bundle with the declared value and date, most-recent, and
    # inside criterion (a)'s lookback window. These checks are unconditional —
    # a manifest without the block fails, so deleting the declaration cannot
    # quietly re-launder the population as purely generated.
    declared = manifest.get("synthetic_observations")
    _check(
        isinstance(declared, list) and len(declared) == 1,
        "exactly one declared synthetic observation (D73)",
        failures,
    )
    if isinstance(declared, list) and len(declared) == 1:
        s = declared[0]
        bundle_path = BUNDLES_DIR / s["bundle"]
        resource = None
        if bundle_path.exists():
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            resource = next(
                (
                    e["resource"]
                    for e in bundle.get("entry", [])
                    if e.get("resource", {}).get("id") == s["resource_id"]
                ),
                None,
            )
        _check(
            resource is not None,
            f"declared resource {s['resource_id']} exists in {s['bundle']}",
            failures,
        )
        if resource is not None:
            _check(
                any(c.get("code") == s["code"] for c in _codings(resource))
                and resource.get("valueQuantity", {}).get("value") == s["value"]
                and resource.get("effectiveDateTime") == s["effective_date"],
                "the synthetic observation carries the declared code, value "
                f"({s['value']}) and date",
                failures,
            )
            _check(
                s["value"] == BMI_THRESHOLD,
                f"the synthetic value is exactly {BMI_THRESHOLD} — E12's boundary",
                failures,
            )
            info = read_bundle(bundle_path)
            _check(
                info["latest_bmi"] == s["value"]
                and info["latest_bmi_date"] == s["effective_date"]
                and info["patient_id"] == s["patient_id"],
                "the synthetic observation is the most-recent BMI the bundle "
                "yields, for the declared patient",
                failures,
            )
            reference = manifest.get("synthea", {}).get("reference_date", "")
            as_of = date(int(reference[:4]), int(reference[4:6]), int(reference[6:8]))
            effective = date.fromisoformat(s["effective_date"][:10])
            _check(
                0 <= _months_before(as_of, effective) < LOOKBACK_MONTHS,
                f"the synthetic observation ({s['effective_date'][:10]}) is "
                f"inside the {LOOKBACK_MONTHS}-month lookback from {as_of}",
                failures,
            )
    # The declared clone (T-88, D102): declared exactly once, recomputable from
    # its source and the declaration, its patient in the declared state, and
    # its record in `bundles` hashing to the recomputed bytes. Unconditional,
    # as the observation block above is: deleting the declaration cannot
    # quietly re-launder the eighth bundle as generated.
    clones = manifest.get("synthetic_patients")
    _check(
        isinstance(clones, list) and len(clones) == len(CLONES),
        f"{len(CLONES)} declared clone(s), as many as are declared here "
        "(D102, D113)",
        failures,
    )
    if isinstance(clones, list) and len(clones) == len(CLONES):
        by_patient = {r["patient_id"]: r for r in records}
        for declaration, declared in zip(CLONES, clones):
            expected_id = clone_patient_id(declaration)
            changed = declaration.get("state") or declaration.get("variant")
            _check(
                declared.get("patient_id") == expected_id
                and declared.get("cloned_from") == declaration["cloned_from"]
                and declared.get("address", {}).get("state")
                == declaration.get("state")
                and declared.get("variant") == declaration.get("variant"),
                f"clone {expected_id[:12]} is declared from {declaration['cloned_from'][:12]} "
                f"as {changed}",
                failures,
            )
            source = by_patient.get(declaration["cloned_from"])
            source_path = BUNDLES_DIR / source["filename"] if source else None
            clone_path = BUNDLES_DIR / declared.get("bundle", "")
            if source_path is None or not source_path.exists() or not clone_path.exists():
                _check(False, "the clone and its source are both on disk", failures)
                continue
            _, recomputed = clone_bundle(
                source_path.read_text(encoding="utf-8"), declaration
            )
            _check(
                clone_path.read_text(encoding="utf-8") == recomputed,
                f"{clone_path.name}: recomputing the clone from its source "
                "reproduces the committed bytes",
                failures,
            )
            _check(
                by_patient.get(expected_id, {}).get("filename") == clone_path.name,
                "the clone is listed in `bundles` under its own id",
                failures,
            )
            bundle = json.loads(clone_path.read_text(encoding="utf-8"))
            patient = next(
                (e["resource"] for e in bundle["entry"]
                 if e.get("resource", {}).get("resourceType") == "Patient"),
                {},
            )
            if "state" in declaration:
                _check(
                    (patient.get("address") or [{}])[0].get("state") == declaration["state"],
                    f"the clone's Patient.address[0].state is {declaration['state']}",
                    failures,
                )
            if "add_medication" in declaration:
                # T-93 (D113): the declared prescription is present, active,
                # carries the declared drug — and is absent from the source,
                # which is what makes the pair differ by exactly one resource
                # and the denial row say what it claims to say.
                added = declaration["add_medication"]
                orders = [
                    e["resource"]
                    for e in bundle["entry"]
                    if e.get("resource", {}).get("resourceType") == "MedicationRequest"
                    and any(
                        c.get("code") == added["code"]
                        for c in _codings(e["resource"], "medicationCodeableConcept")
                    )
                ]
                _check(
                    len(orders) == 1
                    and orders[0].get("status") == "active"
                    and orders[0].get("id", "").endswith(added["resource_id_suffix"]),
                    f"the clone carries exactly one active order for "
                    f"{added['code']}, under the declared resource id suffix",
                    failures,
                )
                _check(
                    not read_bundle(source_path)["has_active_excluded_biologic"],
                    f"the source chart carries no active {added['code']} order; "
                    "the declared addition is the whole difference",
                    failures,
                )
            _check(
                declaration["cloned_from"] not in clone_path.read_text(encoding="utf-8"),
                "the clone never names its source id",
                failures,
            )

    # The rheumatology cohort (T-93, D113). The selection rule re-applied to
    # the committed charts: it is the rule that decides which criterion
    # abstains, so a chart that stopped satisfying it would move an eval label
    # with every hash still matching.
    generated_ra = [
        r
        for r in records
        if r.get("cohort") == RHEUMATOLOGY_COHORT
        and r["patient_id"] not in {clone_patient_id(d) for d in CLONES}
    ]
    _check(
        len(generated_ra) == 2,
        f"two generated rheumatology charts ({len(generated_ra)} found)",
        failures,
    )
    if len(generated_ra) == 2:
        _check(
            all(r["has_active_ra"] for r in generated_ra),
            f"both carry an active Condition coded SNOMED {SNOMED_RA}",
            failures,
        )
        _check(
            sorted(bool(r["has_active_methotrexate"]) for r in generated_ra)
            == [False, True],
            "exactly one carries an active methotrexate order, which is the "
            "pair criterion (b) turns on",
            failures,
        )
        _check(
            not any(r["has_active_excluded_biologic"] for r in generated_ra),
            "neither carries the declared excluded drug, so neither is denied "
            "before its criteria are reached",
            failures,
        )
        recorded_state = manifest.get("synthea_rheumatology", {}).get(
            "selection", {}
        ).get("state_code")
        for r in generated_ra:
            bundle = json.loads((BUNDLES_DIR / r["filename"]).read_text(encoding="utf-8"))
            patient = next(
                (e["resource"] for e in bundle["entry"]
                 if e.get("resource", {}).get("resourceType") == "Patient"),
                {},
            )
            _check(
                (patient.get("address") or [{}])[0].get("state") == recorded_state,
                f"{r['filename']}: Patient.address[0].state is the recorded "
                f"{recorded_state}",
                failures,
            )

    if bmis:
        _check(
            all(BMI_FLOOR <= b <= BMI_CEILING for b in bmis),
            f"every bariatric-cohort most-recent BMI within "
            f"[{BMI_FLOOR}, {BMI_CEILING}]: {sorted(bmis)}",
            failures,
        )
        _check(
            min(bmis) < BMI_THRESHOLD,
            f"at least one patient below {BMI_THRESHOLD} (min {min(bmis)})",
            failures,
        )
        _check(
            max(bmis) >= BMI_HIGH_MARK,
            f"at least one patient at or above {BMI_HIGH_MARK} (max {max(bmis)})",
            failures,
        )

    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--generate", action="store_true", help="generate, select, write manifest, clone")
    mode.add_argument(
        "--generate-rheumatology",
        action="store_true",
        help="generate and adopt the rheumatology cohort only (T-93), then clone",
    )
    mode.add_argument("--clone", action="store_true", help="recompute the declared clones from their committed sources")
    mode.add_argument("--verify", action="store_true", help="verify the committed bundles (default)")
    args = parser.parse_args()
    if args.generate:
        return generate()
    if args.generate_rheumatology:
        return generate_rheumatology()
    if args.clone:
        return clone()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
