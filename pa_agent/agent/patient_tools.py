"""T-62 — the patient-plane tools a model may call (REQ-41, REQ-53; D62).

Four declared tools over an injected `PatientStore`. Each one is a closure, and
the closure is the security property rather than a style choice: ADK builds a
tool's function declaration from `inspect.signature`, and a closure's captured
cells never appear in a signature. So the model sees `get_patient_notes(patient_id)`
and has no way to reach, name, or substitute the store behind it.

Every tool body is one call through the port. No path, no `open`, no connection —
REQ-41, and the reason a production Postgres adapter is a second implementation
rather than a rewrite of this file (D25).

**This module lives in `pa_agent/agent/`, which is the subpackage that exists to
make the Article I and II boundary a path rather than a convention (D16).**
`pa_agent/__init__.py` deliberately does not import it, so the ADK stays off the
import path of the deterministic core.

**It holds one plane and only one.** `policy_tools.py` is the other, and no module
imports both — Article VI, and REQ-41's closing line read literally: a module able
to read both planes would have to hold both handles.

Two of these four are **not** given to the extraction agent, and that is REQ-53's
point rather than an omission. See `extraction_agent.py` for the argument; the
short version is that handing a model the structured BMI while asking it for the
note's BMI is how T-33's two independent sources stop being two.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pa_agent.contracts import ToolCall
from pa_agent.stores.patient import PatientStore


@dataclass
class PatientToolset:
    """The declared tools, plus the ordered record of what was called.

    The log lives beside the tools rather than inside a plugin because a tool call
    that failed still happened, and Article X wants the sequence. The plugin in
    `extraction_agent.py` records the model's *view* of the same calls; this one
    records the port's.
    """

    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)
    calls: list[ToolCall] = field(default_factory=list)

    def allowlist(self, *names: str) -> list[Callable[..., Any]]:
        """The subset an agent is permitted to see, by name.

        A `KeyError` on an unknown name is deliberate: an agent asking for a tool
        that does not exist is a typo that would otherwise silently produce an
        agent with fewer tools than its author thought (REQ-45 — the model may not
        invent tools, and neither may a caller).
        """
        missing = [name for name in names if name not in self.tools]
        if missing:
            raise KeyError(
                f"no such tool(s) {missing}; declared: {sorted(self.tools)}. An "
                "allowlist naming a tool that does not exist would silently "
                "produce an agent with fewer tools than intended (REQ-43)."
            )
        return [self.tools[name] for name in names]

    @property
    def names(self) -> list[str]:
        return sorted(self.tools)


def build_patient_tools(patient_store: PatientStore) -> PatientToolset:
    """Declare the patient-plane tools over one injected store.

    The store arrives as an argument and is never constructed here, so this module
    names no storage location and holds no credential. Swapping in a production
    FHIR adapter changes the caller and nothing else — which is the whole of what
    the ports cost the code above them (D25).
    """
    toolset = PatientToolset()

    def _record(name: str, arguments: dict[str, Any], started: float, ok: bool,
                detail: str | None = None) -> None:
        toolset.calls.append(
            ToolCall(
                name=name,
                arguments_digest=ToolCall.digest(arguments),
                ok=ok,
                wall_time_ms=(time.perf_counter() - started) * 1000.0,
                detail=detail,
            )
        )

    def get_patient_notes(patient_id: str) -> dict:
        """List the clinical notes on file for a patient.

        Returns each note's document_id and character length, not its text. Call
        get_patient_document with a document_id to read one.

        Args:
          patient_id: the patient's identifier.
        """
        started = time.perf_counter()
        try:
            notes = patient_store.get_notes(patient_id)
        except Exception as exc:
            _record("get_patient_notes", {"patient_id": patient_id}, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_patient_notes", {"patient_id": patient_id}, started, True)
        # Ids and lengths, not text: a model that can list notes does not
        # automatically get to read all of them in one call, and the workflow is
        # what decides which documents are in scope.
        return {
            "notes": [
                {"document_id": note.document_id, "characters": len(note.text)}
                for note in notes
            ]
        }

    def get_patient_document(document_id: str) -> dict:
        """Read the full text of one clinical note.

        Args:
          document_id: the note's identifier, as returned by get_patient_notes.
        """
        started = time.perf_counter()
        arguments = {"document_id": document_id}
        try:
            notes = patient_store.get_notes(_patient_of(document_id))
        except Exception as exc:
            _record("get_patient_document", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        for note in notes:
            if note.document_id == document_id:
                _record("get_patient_document", arguments, started, True)
                return {"document_id": note.document_id, "text": note.text}
        _record("get_patient_document", arguments, started, False, "not_found")
        # An empty string would be a note that documents nothing, which is a
        # clinical claim. Raising keeps a lookup failure a lookup failure (D31).
        raise KeyError(
            f"no document {document_id!r} for this patient; call "
            "get_patient_notes to list what is on file"
        )

    def get_patient_observations(patient_id: str) -> dict:
        """List the patient's recorded quantitative observations.

        Args:
          patient_id: the patient's identifier.
        """
        started = time.perf_counter()
        arguments = {"patient_id": patient_id}
        try:
            observations = patient_store.get_observations(patient_id)
        except Exception as exc:
            _record("get_patient_observations", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_patient_observations", arguments, started, True)
        return {
            "observations": [
                {
                    "code": o.code,
                    "value": o.value,
                    "unit": o.unit,
                    "effective_date": o.effective_date.isoformat(),
                }
                for o in observations
            ]
        }

    def get_patient_conditions(patient_id: str) -> dict:
        """List the patient's coded conditions and their clinical status.

        Args:
          patient_id: the patient's identifier.
        """
        started = time.perf_counter()
        arguments = {"patient_id": patient_id}
        try:
            conditions = patient_store.get_conditions(patient_id)
        except Exception as exc:
            _record("get_patient_conditions", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_patient_conditions", arguments, started, True)
        return {
            "conditions": [
                {
                    "code": c.code,
                    "system": c.system,
                    "clinical_status": c.clinical_status,
                    "onset_date": c.onset_date.isoformat() if c.onset_date else None,
                }
                for c in conditions
            ]
        }

    toolset.tools = {
        "get_patient_notes": get_patient_notes,
        "get_patient_document": get_patient_document,
        "get_patient_observations": get_patient_observations,
        "get_patient_conditions": get_patient_conditions,
    }
    return toolset


def _patient_of(document_id: str) -> str:
    """The patient a note document_id belongs to.

    T-07 names every note `<patient_id>/chart_note.txt`, so the id carries its
    owner. This is a convention rather than a port guarantee, and **T-66 is the
    task that removes it** — by passing the patient id the model already holds,
    not by reaching for the widened port.

    T-64 widened `PatientStore.get_document` to resolve the whole patient plane,
    and this function deliberately does not use it (D65). What it is doing is
    *scoping*, not resolving: the tool answers for one patient's notes and for
    nothing else. Point it at the widened namespace and the extraction agent —
    whose allowlist is this tool plus `get_patient_notes` — can read a FHIR
    bundle by filename and so obtain the structured BMI that T-62 withheld from
    it on purpose, which is exactly how T-33's two independent readings stop
    being two. Written as one function with a name so there is exactly one place
    to delete when T-66 lands.
    """
    return document_id.split("/", 1)[0]
