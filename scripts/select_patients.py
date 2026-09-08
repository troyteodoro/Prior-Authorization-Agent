"""T-04 — generate and select the Synthea population (D35).

Two modes:

    python scripts/select_patients.py --generate
        Downloads the pinned Synthea release jar if absent, generates a
        population with a recorded seed, selects six bundles whose most-recent
        BMI observations span 33 to 45, copies them to data/patients/bundles/
        and writes data/patients/manifest.json. Network and Java.

    python scripts/select_patients.py [--verify]
        The exit condition. Re-reads the disk and nothing else: hashes the six
        committed bundles against the manifest, re-extracts each most-recent
        BMI from the bundle itself, and re-asserts the span conditions and the
        recorded seed. No network, no Java, no generation, no model.

The committed bundles are corpus, exactly as T-02's documents are: everything
downstream (T-05, T-06, T-12) reads these six files, and a bundle whose hash
drifts fails the gate rather than quietly feeding a different patient to the
tests. Selection is deterministic given the generated population.

Tooling, not the system under test: the system reads patients only through
`PatientStore` (REQ-41); this script is the thing that puts files on disk for
the local adapter to serve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
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
BUNDLE_COUNT = 6

LOINC_BMI = "39156-5"
SNOMED_T2DM = "44054006"


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


def _generate_population() -> list[str]:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    command = [
        "java",
        "-jar",
        str(JAR_PATH),
        "-s",
        str(SEED),
        "-cs",
        str(CLINICIAN_SEED),
        "-r",
        REFERENCE_DATE,
        "-p",
        str(POPULATION),
        "-a",
        AGE_RANGE,
        "--exporter.baseDirectory",
        str(OUTPUT_DIR),
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
    needed = BUNDLE_COUNT - len(chosen)
    if len(rest) < needed:
        sys.exit(f"only {len(in_range)} candidates in range; need {BUNDLE_COUNT}")
    step = len(rest) / needed
    chosen += [rest[int(i * step)] for i in range(needed)]
    return sorted(chosen, key=lambda c: c["latest_bmi"])


def generate() -> int:
    jar_digest = _ensure_jar()
    command = _generate_population()

    fhir_dir = OUTPUT_DIR / "fhir"
    candidates = []
    for path in sorted(fhir_dir.glob("*.json")):
        if path.name.startswith(("hospitalInformation", "practitionerInformation")):
            continue
        info = read_bundle(path)
        info["source_path"] = path
        candidates.append(info)
    print(f"{len(candidates)} patient bundles generated")

    chosen = _select(candidates)

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
        "selection_criteria": {
            "bundle_count": BUNDLE_COUNT,
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


def verify() -> int:
    failures: list[str] = []
    print("T-04 verify —")

    if not MANIFEST_PATH.exists():
        print(f"  FAIL no manifest at {MANIFEST_PATH.relative_to(REPO_ROOT)}; run --generate")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    seed = manifest.get("synthea", {}).get("seed")
    _check(isinstance(seed, int), f"seed recorded: {seed!r}", failures)

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
        "six distinct patients",
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
