"""REQ-52 — the extraction port, and the three things that satisfy it (D62).

One note and one declared `FactKind` in, one `ExtractionResult` out (the kind
is on the port since T-107, D149 — the quote port's shape). Three
implementations:

    DirectExtractionRunner     raw `google-genai`, D45's measured configuration
    RecordedExtractionRunner   replays a recorded payload; zero model calls
    AdkExtractionRunner        `google-adk` 2.8.0 — in `pa_agent.agent`, not here

The port exists so the deterministic chain below extraction cannot tell which one
it is talking to. That buys two things worth the interface:

- the model leaf becomes replaceable without touching a predicate. T-63 will
  measure the ADK runner against the direct one; whichever wins, nothing above
  changes, and if the ADK runner loses it stops being the default and no
  criterion notices.
- `pytest` can evaluate the whole chain — extraction, anchoring, span validation,
  reconciliation, seven criteria, aggregation — for zero model calls and zero
  non-determinism, by replaying the payloads T-15 already recorded. That is what
  makes T-18's and T-19's exit conditions runnable at all (D45's `--rescore`
  built the replay path; this makes it a first-class runner).

**Every runner returns through `build_result()`**, which anchors each quote and
counts what it cannot anchor. Model output is untrusted; that function is the
trust boundary, and it is not optional or bypassable from here.

`AdkExtractionRunner` deliberately lives elsewhere. This module imports nothing
from `google`, because `pa_agent/__init__.py` keeps the ADK off the import path
of the deterministic core and three tests assert it — one of them in a fresh
interpreter, because `sys.modules` pollution is order-dependent. The ADK lives in
`pa_agent.agent`, which is the subpackage that exists to make that boundary a
path rather than a convention (D16).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from pa_agent.contracts import CallMetrics, FactKind, RunTrace
from pa_agent.extraction import FACT_SCHEMAS, ExtractionResult, extract
from pa_agent.model_pin import PINNED_MODEL


# --------------------------------------------------------------------------
# Rejection is a classified exception, and it is never an empty extraction
# --------------------------------------------------------------------------


class ExtractionFailure(str, Enum):
    """Why a runner could not produce an extraction. Closed set.

    Mapped onto `ErrorCode` by **T-29**, not here. This module knows the
    response was malformed; only the criterion knows what that means for the
    criterion, which is the line D38 drew for `SpanValidationError` and the same
    line applies (D62).
    """

    NO_PAYLOAD = "NO_PAYLOAD"
    UNPARSEABLE = "UNPARSEABLE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    CALL_FAILED = "CALL_FAILED"
    DOCUMENT_CHANGED = "DOCUMENT_CHANGED"
    NOT_RECORDED = "NOT_RECORDED"
    #: A recorded payload asked for under a fact kind whose prompt version is
    #: not the one it was measured with (T-107, REQ-78, D149). Terminal: the
    #: recording holds no answer to this request, and asking again changes
    #: nothing. Raised before the payload is looked up, so a payload measured
    #: under one schema is never built under another (D147).
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"


class ExtractionOutputError(ValueError):
    """A runner could not produce an extraction, and says which way it failed.

    Raised rather than returning an empty `ExtractionResult`, and that choice is
    the whole point of this class. `spike/spike_001/run.py` documents the trap in
    writing: a note that extracts nothing leaks no REQ-9 traps, so it scores
    perfect exclusion, and contributes no false positives, so it scores perfect
    precision. **A transport failure reads as flawless extraction.** Returning a
    zero-event result on a malformed response is that same bug with a different
    cause, and it is silent in exactly the same way.

    Shaped like `pa_agent.spans.SpanValidationError` on purpose: a closed reason
    plus a message, so a caller can branch on the reason and a human can read the
    message.
    """

    def __init__(self, reason: ExtractionFailure, message: str) -> None:
        super().__init__(f"{reason.value}: {message}")
        self.reason = reason
        self.message = message


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


@runtime_checkable
class ExtractionRunner(Protocol):
    """One note in, the facts of one declared kind out (REQ-52, REQ-78).

    `name` is recorded on the run trace, so a determination's provenance says
    which implementation produced its note facts. A recorded run and a live run
    are not the same claim and the artifact should not read as though they were.
    """

    name: str

    def run(self, document_id: str, text: str, kind: FactKind) -> ExtractionResult:
        """Extract the facts of `kind` from one note.

        Raises `ExtractionOutputError` when no extraction could be produced. It
        must never return a zero-event result to signal failure — a note that
        genuinely documents nothing is E7, and E7 is an answer.
        """
        ...


# --------------------------------------------------------------------------
# Direct: the configuration D45 measured
# --------------------------------------------------------------------------


class DirectExtractionRunner:
    """`google-genai` with a native `response_schema` — `extract()`.

    A thin adapter over `pa_agent.extraction.extract`, never a reimplementation
    of it: D45's numbers were measured through exactly that call, and T-89's
    (D103) through its two-turn successor. Anything that would change the
    request is a new measurement, not a refinement (D45's own rule) — which is
    why the re-ask was a re-measurement of every extraction recording.

    The client is injected, so nothing here reads a credential or names a tier —
    D5 keeps AI Studio and Vertex as two credentials behind one pinned model
    name, and the caller chooses which.
    """

    name = "direct"

    def __init__(self, client, model: str = PINNED_MODEL) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def run(self, document_id: str, text: str, kind: FactKind) -> ExtractionResult:
        try:
            return extract(document_id, text, self._client, self._model, kind=kind)
        except ExtractionOutputError:
            raise
        except json.JSONDecodeError as exc:
            # T-29 (D76): before this clause, a response that did not parse
            # fell into the catch-all below and was classified `CALL_FAILED` —
            # retryable, though the same prompt returns the same invalid
            # response (D8). Terminal, one attempt.
            raise ExtractionOutputError(
                ExtractionFailure.UNPARSEABLE,
                f"{document_id}: response is not JSON: {exc}",
            ) from exc
        except ValidationError as exc:
            # Parsed but not our schema — the same misclassification (T-29).
            raise ExtractionOutputError(
                ExtractionFailure.SCHEMA_INVALID,
                f"{document_id}: response failed schema validation: {exc}",
            ) from exc
        except Exception as exc:  # re-raised classified, never swallowed (REQ-27)
            raise ExtractionOutputError(
                ExtractionFailure.CALL_FAILED,
                f"{type(exc).__name__}: {exc}",
            ) from exc


# --------------------------------------------------------------------------
# Recorded: the chain evaluates end to end for nothing
# --------------------------------------------------------------------------


class RecordedExtractionRunner:
    """Replays a recorded payload through `build_result()`. No model call.

    Takes payloads, **not a path**. REQ-41 says no module outside a store adapter
    names a storage location, and the recording is read by the CLI, the eval
    harness and the tests — all of which already read files. `from_records()`
    below turns a loaded recording into one of these; loading it stays theirs.

    Refuses a note whose sha256 has moved since the payload was recorded. That is
    `--rescore`'s existing rule and the reason is D18's: re-anchoring a quote
    against a document it never came from scores the model against text it never
    saw, and the quote will usually still be found somewhere, which is what makes
    it dangerous rather than merely wrong.

    **Keyed by content first, then by id** (T-88, D102). A payload is a claim
    about bytes, so a note whose sha256 matches a recorded one replays under
    whatever `document_id` the caller asks for — that is how a declared clone's
    byte-identical note answers for zero calls, with its spans pointing into
    its own document. The `document_id` route survives for payloads recorded
    without a hash, and a recorded id whose bytes moved is still
    `DOCUMENT_CHANGED`: the two routes never produce a payload the other
    would refuse.

    **And keyed by the prompt version each note was measured under** (T-107,
    REQ-78, D149). Every recorded note carries its own `trace.prompt_version`.
    A request for a kind whose `FACT_SCHEMAS` entry names another version is
    `SCHEMA_MISMATCH`, raised before the payload is built: a payload measured
    under one schema, built under another, either fails validation or, if the
    new schema's fields default, becomes an empty extraction every downstream
    check agrees with (D31, D147). A record with no version is not replayable.
    """

    name = "recorded"

    def __init__(
        self,
        payloads: dict[str, dict],
        note_hashes: dict[str, str] | None = None,
        model: str | None = None,
        metrics: dict[str, CallMetrics] | None = None,
        traces: dict[str, RunTrace] | None = None,
        prompt_versions: dict[str, str] | None = None,
    ) -> None:
        # document_id -> {prompt version -> (payload, metrics, trace)}. Keyed
        # by version since T-108 (D150): one note may be read under two fact
        # kinds, and each reading is its own measurement. A version of `None`
        # is an unversioned payload, which is held so it can be refused by
        # name rather than looked past (D149).
        self._entries: dict[str, dict[str | None, tuple]] = {}
        self._hashes = dict(note_hashes or {})
        self._model = model
        # sha256 -> the recorded document_id that carries the payload.
        self._by_hash = {digest: document_id for document_id, digest in self._hashes.items()}
        versions = dict(prompt_versions or {})
        for document_id, payload in payloads.items():
            # The recorded run's trace, when the recording holds one: every
            # turn the measurement spent, so a replay reports the re-ask a note
            # cost and not only its first turn (T-89, D103; D71's rule one
            # layer down).
            self._add(
                document_id,
                versions.get(document_id),
                payload,
                (metrics or {}).get(document_id),
                (traces or {}).get(document_id),
            )

    def _add(
        self,
        document_id: str,
        version: str | None,
        payload: dict,
        metrics: CallMetrics | None,
        trace: RunTrace | None,
    ) -> None:
        readings = self._entries.setdefault(document_id, {})
        if version in readings:
            raise ValueError(
                f"two recorded payloads for {document_id!r} under prompt version "
                f"{version!r}; one reading of one note under one configuration "
                "is one measurement, and keeping the last would make replay "
                "depend on load order (D150)"
            )
        readings[version] = (payload, metrics, trace)

    @classmethod
    def from_records(cls, records: list[dict], model: str | None = None):
        """Build one from a recording's `notes[]`, as written by
        `scripts/run_extraction.py` — or from several recordings' notes
        concatenated, one per fact kind (T-108, D150).

        Skips records with no `raw` — a note the recording holds a score for but
        no payload cannot be replayed, and pretending otherwise would replay an
        empty extraction, which is the failure `ExtractionOutputError` exists to
        prevent.
        """
        runner = cls({}, model=model)
        for record in records:
            document_id = record.get("document_id")
            raw = record.get("raw")
            if not document_id or raw is None:
                continue
            if record.get("note_sha256"):
                runner._hashes[document_id] = record["note_sha256"]
                runner._by_hash[record["note_sha256"]] = document_id
            # The version the payload was measured under (D149): the record's
            # own field when it states one, else its trace's. Every committed
            # recording states it on the trace.
            version = record.get("prompt_version") or (record.get("trace") or {}).get(
                "prompt_version"
            )
            runner._add(
                document_id,
                version or None,
                raw,
                CallMetrics.model_validate(record["metrics"]) if record.get("metrics") else None,
                RunTrace.model_validate(record["trace"]) if record.get("trace") else None,
            )
        return runner

    @property
    def model(self) -> str | None:
        return self._model

    def __contains__(self, document_id: str) -> bool:
        return document_id in self._entries

    def run(self, document_id: str, text: str, kind: FactKind) -> ExtractionResult:
        schema = FACT_SCHEMAS[kind]
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual in self._by_hash:
            # The bytes were measured, under this id or another (D102).
            recorded_id = self._by_hash[actual]
        elif document_id in self._entries:
            recorded_hash = self._hashes.get(document_id)
            if recorded_hash is not None:
                # A recorded id, and the bytes are not the recorded bytes.
                raise ExtractionOutputError(
                    ExtractionFailure.DOCUMENT_CHANGED,
                    f"{document_id} hashes to {actual[:12]} and the payload was "
                    f"recorded against {recorded_hash[:12]}. Re-anchoring would "
                    "score a quote against a document it never came from (D18).",
                )
            recorded_id = document_id
        else:
            raise ExtractionOutputError(
                ExtractionFailure.NOT_RECORDED,
                f"no recorded payload for {document_id!r} (sha256 {actual[:12]}); "
                "run `python scripts/run_extraction.py` (it spends model calls) "
                "or pass a live runner",
            )

        readings = self._entries[recorded_id]
        if schema.prompt_version not in readings:
            measured = sorted(v for v in readings if v is not None)
            if not measured:
                raise ExtractionOutputError(
                    ExtractionFailure.NOT_RECORDED,
                    f"the payload for {recorded_id!r} records no prompt version, so "
                    "nothing says which schema it answers; it is not replayed under "
                    "an assumed one (D149)",
                )
            raise ExtractionOutputError(
                ExtractionFailure.SCHEMA_MISMATCH,
                f"{document_id} was asked for {kind.value!r} under "
                f"{schema.prompt_version!r}; the payload for {recorded_id!r} was "
                f"measured under {measured!r}. A changed configuration "
                "is a new measurement, never a replay (D45, D149).",
            )
        payload, metrics, trace = readings[schema.prompt_version]

        try:
            # The recorded metrics are carried through unchanged: they measure the
            # call that produced this payload, and no call happens here. A replay
            # that reported zero tokens would understate what the answer cost.
            # The result is built under the *requesting* id, so spans point into
            # the document the caller handed in.
            result = schema.build(document_id, text, payload, metrics)
        except ExtractionOutputError:
            raise
        except Exception as exc:
            raise ExtractionOutputError(
                ExtractionFailure.SCHEMA_INVALID,
                f"recorded payload for {document_id} no longer validates: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        if trace is not None:
            # Every recorded turn, under the requesting id (D102's re-addressing
            # applied to the trace). `None` stays `None` for a record without
            # one: a replay invents no trace (T-62).
            result.trace = trace.model_copy(update={"document_id": document_id})
        return result


class NullExtractionRunner:
    """Raises on any call. The runner a short-circuit test hands in.

    Proving "zero model calls" by reading a counter proves the counter reads
    zero. Handing the workflow a runner that cannot be called and getting an
    answer anyway proves the path was never reached — which is what A4 is about
    for E2 and E3, and what T-18's exit asks for.
    """

    name = "null"

    def __init__(self, note: str = "") -> None:
        self._note = note

    def run(self, document_id: str, text: str, kind: FactKind) -> ExtractionResult:
        raise AssertionError(
            f"the extraction runner was called for {document_id!r}; this path is "
            f"supposed to reach an answer with zero model calls. {self._note}"
        )
