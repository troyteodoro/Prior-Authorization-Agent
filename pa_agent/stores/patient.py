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
    """`PatientStore` over Synthea bundles on disk. Awaiting T-04.

    Defined now and empty on purpose. The alternative was leaving the patient
    port undeclared until something needed it, which makes T-12 the task that
    invents the shape of the plane it reads from — the pattern D24 and D26 both
    argue against, one requirement at a time.

    Every method raises rather than returning an empty list. An empty chart is a
    valid input that produces `INSUFFICIENT_EVIDENCE` with
    `NO_EVIDENCE_RETRIEVED`, and a store that fakes one would manufacture E7 for
    every patient while looking like it worked.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_PATIENT_ROOT

    def _unavailable(self, what: str) -> NotImplementedError:
        return NotImplementedError(
            f"T-04 has not selected the Synthea population, so there are no "
            f"{what} to read from {self._root}. Empty results would be "
            "indistinguishable from a patient with no documentation."
        )

    def get_observations(self, patient_id: str) -> list[Observation]:
        raise self._unavailable("observations")

    def get_conditions(self, patient_id: str) -> list[Condition]:
        raise self._unavailable("conditions")

    def get_notes(self, patient_id: str) -> list[Document]:
        raise self._unavailable("notes")
