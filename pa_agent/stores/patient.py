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
    """`PatientStore` over Synthea bundles on disk. Awaiting T-12.

    The six bundles exist — selected under `data/patients/bundles/` with a
    manifest, per D35 — but parsing them into `Observation`/`Condition`
    contracts is the FHIR work T-12's exit names, so the methods keep raising
    until T-12 builds the reads. (The raise cited T-04 while the bundles did
    not exist; D35 moved it here when T-04 closed, per D31's lesson about
    stale task citations.)

    Every method raises rather than returning an empty list. An empty chart is a
    valid input that produces `INSUFFICIENT_EVIDENCE` with
    `NO_EVIDENCE_RETRIEVED`, and a store that fakes one would manufacture E7 for
    every patient while looking like it worked.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_PATIENT_ROOT

    def _unavailable(self, what: str) -> NotImplementedError:
        return NotImplementedError(
            f"T-12 has not built the FHIR reads, so the bundles under "
            f"{self._root} cannot yet be served as {what}. Empty results would "
            "be indistinguishable from a patient with no documentation."
        )

    def get_observations(self, patient_id: str) -> list[Observation]:
        raise self._unavailable("observations")

    def get_conditions(self, patient_id: str) -> list[Condition]:
        raise self._unavailable("conditions")

    def get_notes(self, patient_id: str) -> list[Document]:
        raise self._unavailable("notes")
