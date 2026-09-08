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

from pa_agent.contracts import Condition, Document, EvidenceSpan, Observation

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
        """The unstructured chart. Every span a model produces points in here,
        which is why these arrive as hash-verified `Document`s (Art. III)."""
        ...

    def get_document(self, document_id: str) -> Document:
        """A patient-plane source document, content-verified, for spans to be
        checked against — the symmetry D25's reversal note anticipated when it
        demanded stores expose stable document identity (D40)."""
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
        self._raw_text: dict[str, str] = {}
        self._extent_cache: dict[str, list[tuple[int, int] | None]] = {}
        self._notes_dir = self._root / "notes"
        self._notes_manifest: dict[str, list[dict]] | None = None

    # -- resolution and verification ---------------------------------------

    def _load_manifest(self) -> dict[str, dict]:
        if self._manifest is None:
            records = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            self._manifest = {r["patient_id"]: r for r in records["bundles"]}
        return self._manifest

    def _load_notes_manifest(self) -> dict[str, list[dict]]:
        if self._notes_manifest is None:
            path = self._notes_dir / "manifest.json"
            by_patient: dict[str, list[dict]] = {}
            if path.exists():
                for record in json.loads(path.read_text(encoding="utf-8"))["notes"]:
                    by_patient.setdefault(record["patient_id"], []).append(record)
            self._notes_manifest = by_patient
        return self._notes_manifest

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
        text = raw.decode("utf-8")
        self._raw_text[patient_id] = text
        self._parsed[patient_id] = json.loads(text)
        return self._parsed[patient_id]

    def _extents(self, patient_id: str) -> list[tuple[int, int] | None]:
        """Each entry's resource object located exactly in the raw bundle text.

        The entry's unique `fullUrl` anchors the search and
        `JSONDecoder.raw_decode` finds the object's end, so a served span
        slices to the resource JSON that was actually parsed (D40). One
        forward pass; an entry that cannot be located gets `None` rather than
        a guessed offset.
        """
        if patient_id not in self._extent_cache:
            bundle = self._bundle(patient_id)
            text = self._raw_text[patient_id]
            decoder = json.JSONDecoder()
            extents: list[tuple[int, int] | None] = []
            position = 0
            for entry in bundle.get("entry", []):
                full_url = entry.get("fullUrl")
                anchor = text.find(json.dumps(full_url), position) if full_url else -1
                if anchor == -1:
                    extents.append(None)
                    continue
                resource_key = text.find('"resource"', anchor)
                brace = text.find("{", resource_key)
                if resource_key == -1 or brace == -1:
                    extents.append(None)
                    continue
                _, end = decoder.raw_decode(text, brace)
                extents.append((brace, end))
                position = end
            self._extent_cache[patient_id] = extents
        return self._extent_cache[patient_id]

    def _entries_with_spans(self, patient_id: str, resource_type: str):
        bundle = self._bundle(patient_id)
        record = self._load_manifest()[patient_id]
        extents = self._extents(patient_id)
        for i, entry in enumerate(bundle.get("entry", [])):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != resource_type:
                continue
            extent = extents[i]
            span = (
                EvidenceSpan(
                    document_id=record["filename"],
                    char_start=extent[0],
                    char_end=extent[1],
                )
                if extent
                else None
            )
            yield resource, span

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
        for resource, span in self._entries_with_spans(patient_id, "Observation"):
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
                    span=span,
                )
            )
        return observations

    def get_conditions(self, patient_id: str) -> list[Condition]:
        """Every coded condition, clinical status carried and never filtered."""
        conditions = []
        for resource, span in self._entries_with_spans(patient_id, "Condition"):
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
                    span=span,
                )
            )
        return conditions

    def get_notes(self, patient_id: str) -> list[Document]:
        """The manifest-driven note corpus T-07 synthesized (D43).

        Synthea's own auto-generated notes are deliberately not served: they
        carry no ground truth, and a corpus whose facts nobody declared would
        let T-15's eval cases grade against labels that do not exist (D39).
        Each note is hash-verified against the notes manifest before it is
        returned, so a span into one points at the text that was measured
        (REQ-7).
        """
        self._bundle(patient_id)  # a patient the population does not list raises
        records = self._load_notes_manifest().get(patient_id, [])
        documents = []
        for record in records:
            path = self._notes_dir / record["document_id"]
            text = path.read_text(encoding="utf-8")
            documents.append(
                Document(
                    document_id=record["document_id"],
                    text=text,
                    sha256=record["sha256"],
                )
            )
        return documents

    def get_document(self, document_id: str) -> Document:
        """A bundle file as a `Document`, so patient-plane spans are checkable
        the way policy-plane spans are (Art. III, D40). `document_id` is the
        bundle filename; the hash is the manifest's record, and `Document`'s
        own validator re-verifies it against the text."""
        by_filename = {
            r["filename"]: r for r in self._load_manifest().values()
        }
        try:
            record = by_filename[document_id]
        except KeyError:
            raise KeyError(
                f"no document {document_id!r} in the population; the manifest "
                f"lists {sorted(by_filename)}"
            ) from None
        self._bundle(record["patient_id"])  # reads and hash-verifies
        return Document(
            document_id=document_id,
            text=self._raw_text[record["patient_id"]],
            sha256=record["sha256"],
        )
