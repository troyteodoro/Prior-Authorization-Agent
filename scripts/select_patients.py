"""T-04 + T-41 — generate and select the Synthea population (D35, D73).

Two modes:

    python scripts/select_patients.py --generate
        Downloads the pinned Synthea release jar if absent, then runs Synthea
        twice with recorded seeds: the base run selects six bundles whose
        most-recent BMI observations span 33 to 45 (D35), and the E12 run
        yields a seventh patient to whose bundle one synthetic BMI observation
        of exactly 35.0 is appended under a declared provenance block (D73).
        Copies all seven to data/patients/bundles/ and writes
        data/patients/manifest.json. Network and Java.

    python scripts/select_patients.py [--verify]
        The exit condition. Re-reads the disk and nothing else: hashes the
        seven committed bundles against the manifest, re-extracts each
        most-recent BMI from the bundle itself, re-asserts the span conditions
        and the recorded seeds, and checks the declared synthetic observation
        exists, matches its declaration, and is the most-recent BMI. No
        network, no Java, no generation, no model.

The committed bundles are corpus, exactly as T-02's documents are: everything
downstream (T-05, T-06, T-12) reads these seven files, and a bundle whose hash
drifts fails the gate rather than quietly feeding a different patient to the
tests. Selection is deterministic given the generated populations —  but
Synthea itself is not byte-deterministic across runs (D73: one of six base
bundles reproduced with different bytes under an identical command), so after
a regeneration any base bundle whose hash drifts from the committed manifest
is restored from git rather than adopted.

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
BUNDLE_COUNT = 7  # plus the E12 patient (D73)

LOINC_BMI = "39156-5"
SNOMED_T2DM = "44054006"

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
    """Patient id, most-recent BMI observation, and whether an active type 2
    diabetes condition is on the record."""
    bundle = json.loads(path.read_text(encoding="utf-8"))
    patient_id = None
    bmi_obs: list[tuple[str, float]] = []
    has_t2dm = False
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
            is_t2dm = any(c.get("code") == SNOMED_T2DM for c in _codings(resource))
            active = any(
                c.get("code") == "active"
                for c in _codings(resource, "clinicalStatus")
            )
            if is_t2dm and active:
                has_t2dm = True
    latest = max(bmi_obs) if bmi_obs else None  # ISO dates sort lexically
    return {
        "patient_id": patient_id,
        "latest_bmi": latest[1] if latest else None,
        "latest_bmi_date": latest[0] if latest else None,
        "has_active_t2dm": has_t2dm,
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


def _run_synthea(seed: int, output_dir: Path) -> list[str]:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    command = [
        "java",
        "-jar",
        str(JAR_PATH),
        "-s",
        str(seed),
        "-cs",
        str(seed),
        "-r",
        REFERENCE_DATE,
        "-p",
        str(POPULATION),
        "-a",
        AGE_RANGE,
        "--exporter.baseDirectory",
        str(output_dir),
        "--exporter.fhir.export",
        "true",
        "--exporter.hospital.fhir.export",
        "false",
        "--exporter.practitioner.fhir.export",
        "false",
        STATE,
    ]
    print("running:", " ".join(command))
    subprocess.run(command, check=True, cwd=WORK_DIR)
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
        records.append(
            {
                "filename": dest.name,
                "sha256": sha256(dest),
                "patient_id": c["patient_id"],
                "latest_bmi": c["latest_bmi"],
                "latest_bmi_date": c["latest_bmi_date"],
                "has_active_t2dm": c["has_active_t2dm"],
            }
        )

    e12_dest = BUNDLES_DIR / e12_base["source_path"].name
    provenance = _append_synthetic_observation(e12_base["source_path"], e12_dest)
    e12_info = read_bundle(e12_dest)
    records.append(
        {
            "filename": e12_dest.name,
            "sha256": sha256(e12_dest),
            "patient_id": e12_info["patient_id"],
            "latest_bmi": e12_info["latest_bmi"],
            "latest_bmi_date": e12_info["latest_bmi_date"],
            "has_active_t2dm": e12_info["has_active_t2dm"],
        }
    )

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
    return verify()


# --------------------------------------------------------------------------
# Verification (the exit condition): disk only
# --------------------------------------------------------------------------


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
    print("T-04/T-41 verify —")

    if not MANIFEST_PATH.exists():
        print(f"  FAIL no manifest at {MANIFEST_PATH.relative_to(REPO_ROOT)}; run --generate")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    seed = manifest.get("synthea", {}).get("seed")
    _check(isinstance(seed, int), f"seed recorded: {seed!r}", failures)
    e12_seed = manifest.get("synthea_e12", {}).get("seed")
    _check(isinstance(e12_seed, int), f"E12 seed recorded: {e12_seed!r}", failures)

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
        if info["latest_bmi"] is not None:
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
    if bmis:
        _check(
            all(BMI_FLOOR <= b <= BMI_CEILING for b in bmis),
            f"every most-recent BMI within [{BMI_FLOOR}, {BMI_CEILING}]: {sorted(bmis)}",
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
    mode.add_argument("--generate", action="store_true", help="generate, select, write manifest")
    mode.add_argument("--verify", action="store_true", help="verify the committed bundles (default)")
    args = parser.parse_args()
    return generate() if args.generate else verify()


if __name__ == "__main__":
    sys.exit(main())
