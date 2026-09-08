"""The patient plane's port, and the adapter that will read Synthea bundles (D25).

`PatientStore` is three methods: structured observations, structured conditions,
and the unstructured notes spans point into. The production adapter is a FHIR
server or an EHR export; nothing above this module changes when it arrives.

D25 records the one thing that could invalidate the port design, and it lives on
this side of the boundary: a production patient store that cannot expose stable
document identity — notes that cannot be addressed and re-fetched byte-identical
— leaves Article III's spans with nothing to anchor to. Measure that against one
real store before writing the production adapter.

This module holds no policy corpus, imports nothing from the policy plane, and
must never be made to (REQ-33). `Criterion` is the only object that crosses, and
it crosses into the adjudicator, not into here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from pa_agent.contracts import Condition, Document, Observation

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATIENT_ROOT = REPO_ROOT / "data" / "patients"


@runtime_checkable
class PatientStore(Protocol):
    """Everything the system reads from the patient plane."""

    def get_observations(self, patient_id: str) -> list[Observation]:
        """Structured measurements. BMI is the only one v1 reads (REQ-11).

        Structured values are authoritative over note-extracted ones when the
        two disagree (REQ-34), which is why they arrive by their own method
        rather than as another thing to find in the prose.
        """
        ...

    def get_conditions(self, patient_id: str) -> list[Condition]:
        """Coded diagnoses, for criterion (b)'s set intersection (REQ-12)."""
        ...

    def get_notes(self, patient_id: str) -> list[Document]:
        """The unstructured chart. Every span a model produces points in here."""
        ...


class LocalPatientStore:
    """`PatientStore` over T-04's committed Synthea bundles (D35, D39).

    Patients resolve through `data/patients/manifest.json`, and every bundle
    is verified against its recorded sha256 before parsing — the
    `get_document`/`sources.json` pattern (REQ-7): a bundle edited on disk
    raises instead of silently feeding a different patient to every criterion.

    The adapter reports what the record says and filters nothing: every
    quantitative observation, every coded condition with its clinical status.
    Selecting BMI is criterion (a)'s judgment and selecting active conditions
    is criterion (b)'s (D31's split, applied to this plane by D39).

    An unknown patient raises rather than returning an empty chart, because an
    empty chart is a valid input that produces `INSUFFICIENT_EVIDENCE` with
    `NO_EVIDENCE_RETRIEVED`, and a store that fakes one would manufacture E7
    for every patient while looking like it worked (T-09's argument, kept).
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_PATIENT_ROOT
        self._manifest_path = self._root / "manifest.json"
        self._bundles_dir = self._root / "bundles"
        self._manifest: dict[str, dict] | None = None
        self._parsed: dict[str, dict] = {}

    # -- resolution and verification ---------------------------------------

    def _load_manifest(self) -> dict[str, dict]:
        if self._manifest is None:
            records = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            self._manifest = {r["patient_id"]: r for r in records["bundles"]}
        return self._manifest

    def _bundle(self, patient_id: str) -> dict:
        if patient_id in self._parsed:
            return self._parsed[patient_id]

        manifest = self._load_manifest()
        try:
            record = manifest[patient_id]
        except KeyError:
            raise KeyError(
                f"no patient {patient_id!r} in the population; the manifest "
                f"lists {len(manifest)} patients"
            ) from None

        path = self._bundles_dir / record["filename"]
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != record["sha256"]:
            raise ValueError(
                f"{record['filename']} hashes to {actual[:12]} but the manifest "
                f"says {record['sha256'][:12]}. The bundle changed on disk; every "
                "fact read from it is suspect (REQ-7)."
            )
        self._parsed[patient_id] = json.loads(raw.decode("utf-8"))
        return self._parsed[patient_id]

    @staticmethod
    def _resources(bundle: dict, resource_type: str):
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") == resource_type:
                yield resource

    @staticmethod
    def _first_coding(resource: dict, field: str = "code") -> dict:
        codings = resource.get(field, {}).get("coding", [])
        return codings[0] if codings else {}

    # -- the port -----------------------------------------------------------

    def get_observations(self, patient_id: str) -> list[Observation]:
        """Every observation carrying a top-level value and a date.

        Component-valued observations (blood pressure) have no top-level
        `valueQuantity` and are not served — stated scope, not an oversight
        (D39). Dates truncate to the day; criterion arithmetic is per-month.
        """
        observations = []
        for resource in self._resources(self._bundle(patient_id), "Observation"):
            coding = self._first_coding(resource)
            value = resource.get("valueQuantity", {}).get("value")
            when = resource.get("effectiveDateTime")
            if not coding.get("code") or value is None or not when:
                continue
            observations.append(
                Observation(
                    code=coding["code"],
                    value=float(value),
                    unit=resource.get("valueQuantity", {}).get("unit"),
                    effective_date=date.fromisoformat(when[:10]),
                )
            )
        return observations

    def get_conditions(self, patient_id: str) -> list[Condition]:
        """Every coded condition, clinical status carried and never filtered."""
        conditions = []
        for resource in self._resources(self._bundle(patient_id), "Condition"):
            coding = self._first_coding(resource)
            if not coding.get("code"):
                continue
            status = self._first_coding(resource, "clinicalStatus").get("code")
            onset = resource.get("onsetDateTime")
            conditions.append(
                Condition(
                    code=coding["code"],
                    system=coding.get("system"),
                    onset_date=date.fromisoformat(onset[:10]) if onset else None,
                    clinical_status=status,
                )
            )
        return conditions

    def get_notes(self, patient_id: str) -> list[Document]:
        """The note corpus is T-07's: manifest-driven, traps placed on purpose,
        labeled before extraction runs. Synthea's auto-generated notes carry no
        ground truth, so serving them would hand T-15 a corpus whose eval cases
        grade against labels that do not exist (D39)."""
        raise NotImplementedError(
            f"T-07 has not synthesized the note corpus, so there are no notes "
            f"to read from {self._root}. Empty results would be "
            "indistinguishable from a patient with no documentation."
        )
