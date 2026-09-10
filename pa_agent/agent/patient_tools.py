"""T-62 — the patient-plane tools a model may call (REQ-41, REQ-53, REQ-54; D62, D66).

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
to read both planes would have to hold both handles. `tool_bounds.py` is imported
by both and reaches neither: it takes rows and returns rows.

Two of these four are **not** given to the extraction agent, and that is REQ-53's
point rather than an omission. See `extraction_agent.py` for the argument; the
short version is that handing a model the structured BMI while asking it for the
note's BMI is how T-33's two independent sources stop being two.

### Two things T-65 and T-66 changed here (D66)

**No tool returns a collection sized by the patient's chart.** The lists come back
bounded, declaring `total` and `truncated`; `get_patient_notes` raises instead of
truncating, because its payload is the set of ids the model may then ask for and a
silent truncation there is a shorter chart. `tool_bounds.py` carries the argument.

**Scope is supplied, never parsed.** `get_patient_document` takes the patient id
the model already holds, and `build_note_reader` fixes the scope in Python for an
agent that holds none. `_patient_of` — which recovered the owner by splitting the
id on `/` — is gone, and with it the only place in this package that read meaning
out of the shape of an identifier (D65's rule for the adapter, applied to the
tools).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pa_agent.contracts import ToolCall
from pa_agent.stores.patient import PatientStore

from .tool_bounds import bounded, refuse_if_over


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


def _recorder(toolset: PatientToolset):
    """One call-logging closure, shared by every builder in this module."""

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

    return _record


def build_patient_tools(patient_store: PatientStore) -> PatientToolset:
    """Declare the patient-plane tools over one injected store.

    The store arrives as an argument and is never constructed here, so this module
    names no storage location and holds no credential. Swapping in a production
    FHIR adapter changes the caller and nothing else — which is the whole of what
    the ports cost the code above them (D25).
    """
    toolset = PatientToolset()
    _record = _recorder(toolset)

    def get_patient_notes(patient_id: str) -> dict:
        """List the clinical notes on file for a patient.

        Returns each note's document_id and character length, not its text. Call
        get_patient_document with a document_id to read one.

        Args:
          patient_id: the patient's identifier.
        """
        started = time.perf_counter()
        arguments = {"patient_id": patient_id}
        try:
            notes = patient_store.get_notes(patient_id)
            # Faults rather than truncating: every document_id the model may ask
            # for comes from this response, so an omission here is a chart the
            # model cannot know it is missing (REQ-54, D66).
            refuse_if_over("get_patient_notes", notes)
        except Exception as exc:
            _record("get_patient_notes", arguments, started, False,
                    f"{type(exc).__name__}")
            raise
        _record("get_patient_notes", arguments, started, True)
        # Ids and lengths, not text: a model that can list notes does not
        # automatically get to read all of them in one call, and the workflow is
        # what decides which documents are in scope.
        return {
            "notes": [
                {"document_id": note.document_id, "characters": len(note.text)}
                for note in notes
            ],
            "total": len(notes),
        }

    def get_patient_document(patient_id: str, document_id: str) -> dict:
        """Read the full text of one of a patient's clinical notes.

        Args:
          patient_id: the patient's identifier.
          document_id: the note's identifier, as returned by get_patient_notes.
        """
        started = time.perf_counter()
        arguments = {"patient_id": patient_id, "document_id": document_id}
        try:
            notes = patient_store.get_notes(patient_id)
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
        #
        # This is also where a FHIR bundle filename lands: the scope is one
        # patient's *notes*, so a bundle is not in the list and there is no route
        # from here to the structured BMI (T-66, D65's refusal honoured).
        raise KeyError(
            f"no document {document_id!r} among this patient's notes; call "
            "get_patient_notes to list what is on file"
        )

    def get_patient_observations(patient_id: str) -> dict:
        """List the patient's most recent recorded quantitative observations.

        Returns at most the declared ceiling of rows, most recent first, with the
        true total and whether the response was truncated.

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
        # Most recent first, then bounded. If rows have to be dropped the only
        # defensible ones to drop are the oldest — criterion (a)'s lookback is
        # twelve months and c2 asks about recency (D66). The sort is Python's,
        # which Amendment 1 requires of sorting on both paths.
        ordered = sorted(observations, key=lambda o: o.effective_date, reverse=True)
        rows, meta = bounded(ordered)
        return {
            "observations": [
                {
                    "code": o.code,
                    "value": o.value,
                    "unit": o.unit,
                    "effective_date": o.effective_date.isoformat(),
                }
                for o in rows
            ],
            **meta,
        }

    def get_patient_conditions(patient_id: str) -> dict:
        """List the patient's most recently onset coded conditions and their status.

        Returns at most the declared ceiling of rows, with the true total and
        whether the response was truncated.

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
        # An absent onset sorts last rather than raising: `onset_date` is optional
        # on the contract because bundles genuinely omit it, and a condition with
        # no date is still a condition (D39).
        ordered = sorted(
            conditions,
            key=lambda c: (c.onset_date is not None, c.onset_date),
            reverse=True,
        )
        rows, meta = bounded(ordered)
        return {
            "conditions": [
                {
                    "code": c.code,
                    "system": c.system,
                    "clinical_status": c.clinical_status,
                    "onset_date": c.onset_date.isoformat() if c.onset_date else None,
                }
                for c in rows
            ],
            **meta,
        }

    toolset.tools = {
        "get_patient_notes": get_patient_notes,
        "get_patient_document": get_patient_document,
        "get_patient_observations": get_patient_observations,
        "get_patient_conditions": get_patient_conditions,
    }
    return toolset


def build_note_reader(
    patient_store: PatientStore, document_id: str
) -> PatientToolset:
    """One tool that reads one note, for an agent that holds no patient id (T-66).

    The extraction agent's `tool_fetch` variant is handed a `document_id` and
    nothing else — `ExtractionRunner.run(document_id, text)` has no patient id to
    pass and the model never called `get_patient_notes`. So T-66's argument that
    "the model already holds the patient id" is true of the retrieval agent and
    false here, and the scope has to come from somewhere else.

    It comes from Python. The permitted id is captured at construction, and ADK
    builds its declaration from `inspect.signature`, so the model can neither see
    the captured value nor substitute it. The tool takes a `document_id` and
    refuses every one but that: the model still has to echo the id it was given,
    which keeps the shape of the call T-63 measures, and it has no reachable
    second document — not another patient's note and not a FHIR bundle.

    That is narrower than T-66 asked for, and deliberately. It is also what makes
    reading through T-64's widened `PatientStore.get_document` safe here: the
    scope check runs *before* the resolution, so the widened namespace is never
    searched with an id the caller did not authorize. D65 refused an unscoped tool
    over that port, not a scoped one.
    """
    toolset = PatientToolset()
    _record = _recorder(toolset)
    permitted = document_id

    def read_note(document_id: str) -> dict:
        """Read the full text of the note under review.

        Args:
          document_id: the note's identifier, exactly as given in the request.
        """
        started = time.perf_counter()
        arguments = {"document_id": document_id}
        if document_id != permitted:
            _record("read_note", arguments, started, False, "out_of_scope")
            raise KeyError(
                f"{document_id!r} is not the document under review. This tool "
                f"reads one note and its id was fixed before the run started."
            )
        try:
            document = patient_store.get_document(document_id)
        except Exception as exc:
            _record("read_note", arguments, started, False, f"{type(exc).__name__}")
            raise
        _record("read_note", arguments, started, True)
        return {"document_id": document.document_id, "text": document.text}

    toolset.tools = {"read_note": read_note}
    return toolset
