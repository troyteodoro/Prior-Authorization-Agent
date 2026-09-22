"""T-98 — the quote port: the one model turn the medical-history review gets (D122).

Spec §11 confines the model's role in v1.3 to quoting a note: asked for
verbatim passages documenting each candidate's effect, anchored by
`pa_agent.anchor`, dropped when unanchorable. This module is that turn, in the
shape `pa_agent.runners` and `pa_agent.verifier` already have — a `Protocol`
and three implementations:

    DirectQuoteRunner     raw `google-genai`, the measured configuration
    RecordedQuoteRunner   replays `eval/history/results.json`; zero model calls
    NullQuoteRunner       raises on any call — the proof a path was not reached

and a fourth, `AdkQuoteRunner`, under `pa_agent.agent` for D16's reason: nothing
here imports `google`.

**What the model is asked, and what it is not.** The prompt lists the
knowledge table's effects by their display — *osteoporosis, hypotension, renal
impairment, hyperglycemia, neutropenia* — and nothing else. Not the row id,
which embeds the drug (`lisinopril-renal-impairment`) and would invite the
model to quote the medication line as evidence of the condition, the one thing
D119 says a prescription may never be; not the ICD-10 title, which can say
*drug-induced*; not the chart, not a colour, not a threshold. Every note is
asked about every table effect, so the request is one fixed configuration a
chart cannot vary, and `history.review` keeps only the rows that are candidates
(D122). Adding a table row changes the request and is a new measurement
(D45), which `RecordedQuoteRunner` enforces by refusing a row it was not asked.

**Every runner returns through `build_quote_result()`**, which anchors each
quote by searching the note for the model's verbatim text (D18) and records
what it could not anchor. Model output is untrusted; that function is the trust
boundary. The re-ask is T-89's (`extraction.extract_with_reask`, D103), walked
with this module's builder and locator — one core, two payload shapes.

The recorded runner takes **records, not a path** (REQ-41): loading the file is
the harness's job, as it is for the extraction and verifier recordings.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, ValidationError

from pa_agent.anchor import AnchoredSpan, anchor
from pa_agent.contracts import CallMetrics, EvidenceSpan, MedicationEffectRow, RunTrace
from pa_agent.extraction import (
    EXTRACTION_TEMPERATURE,
    REASK_ROUNDS,
    direct_first_turn,
    direct_reask_turn,
    extract_with_reask,
)
from pa_agent.model_pin import PINNED_MODEL

#: Art. II: the same note yields the same passages. The extraction's, re-used
#: rather than re-declared, so the two direct leaves cannot drift apart.
QUOTE_TEMPERATURE = EXTRACTION_TEMPERATURE

#: The whole call configuration, in two halves like `extraction.PROMPT_VERSION`:
#: this module's instruction and the verbatim re-ask T-89 wrote, which the
#: quote turn reuses verbatim (D103, D122). Every recording carries it and the
#: gate refuses one measured on another. Bumped when either half changes —
#: and, since the rendered condition list is part of the request, a changed
#: table is pinned separately by `rows_asked` on the recording (D45).
QUOTE_PROMPT_VERSION = "t98-quotes-v1/t89-reask-v1"

#: The re-ask bound is the extraction's (REQ-56). Re-exported for the
#: recording script, which stamps it.
QUOTE_REASK_ROUNDS = REASK_ROUNDS


# --------------------------------------------------------------------------
# The response schema. Verbatim passages per condition, nothing else.
# --------------------------------------------------------------------------


class EffectQuote(BaseModel):
    quote: str = Field(
        description="Text copied verbatim and contiguously from the note."
    )
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class EffectQuotes(BaseModel):
    effect: str = Field(description="The condition's name, copied exactly as listed.")
    quotes: list[EffectQuote] = Field(default_factory=list)


class QuoteAnswers(BaseModel):
    """The turn's response. `effects` is required, as `VerbatimAnswers.quotes`
    is: a payload of another shape is a schema failure, recorded as one, never
    read as "nothing found". An empty list is "nothing found"."""

    effects: list[EffectQuotes]


# --------------------------------------------------------------------------
# The instruction. Conditions by display; no drug, no code, no colour.
# --------------------------------------------------------------------------


QUOTE_INSTRUCTION = """\
You are locating documentation in a clinical note for a prior authorization
review. You are given a list of clinical CONDITIONS. For each condition, return
every passage of the note that documents that the patient HAS that condition:
a finding, an assessment, a diagnosis, or a result stated about this patient.
Return only what the schema defines. Do not explain.

For each condition return:

  effect       the condition's name, copied exactly as it appears in the list
  quotes       zero or more passages, each with:
    quote        text copied verbatim and contiguously from the note. It must
                 appear in the note character for character.
    char_start   the offset of the quote's first character, counting from 0 at
    char_end     the start of the note, and one past its last character

Return zero passages for a condition the note does not document. None of the
following documents a condition:

  - a medication that can cause it, or any medication list
  - a plan to monitor, screen or test for it
  - a family history of it
  - a normal or unremarkable result
  - the condition named as a risk, a possibility, or something to rule out
  - a different condition, however related

Report a passage only where the note itself states the condition, or states a
result and calls it abnormal. Never infer a condition from a number. Copy;
never paraphrase, summarize, or correct the note's wording.
"""

#: The heading under which the conditions are listed. One rendering for both
#: runners, so the direct and the ADK turns differ only by how the note reaches
#: the model and never by what they are asked (the rule `format_targets` follows).
EFFECTS_HEADING = "CONDITIONS:"


def rows_asked(rows: Sequence[MedicationEffectRow]) -> tuple[tuple[str, str], ...]:
    """What a request asks, as the recording pins it: `(row_id, effect_display)`
    per row, in the order asked. Refuses two rows sharing a display, because
    the model answers by display and the answer could then not be joined back
    to one row (REQ-63's one-row rule, at the prompt)."""
    displays = [row.effect_display for row in rows]
    if len(set(displays)) != len(displays):
        duplicated = sorted({d for d in displays if displays.count(d) > 1})
        raise ValueError(
            f"two knowledge-table rows share the display {duplicated}; the model "
            "answers by display and an answer must resolve to exactly one row "
            "(REQ-63, D122)"
        )
    return tuple((row.row_id, row.effect_display) for row in rows)


def format_effects(rows: Sequence[MedicationEffectRow]) -> str:
    """The condition list the model sees: displays only, in the order given."""
    rows_asked(rows)
    lines = [EFFECTS_HEADING]
    for row in rows:
        lines.append(f"- {row.effect_display}")
    return "\n".join(lines)


def quote_contents(text: str, rows: Sequence[MedicationEffectRow]) -> str:
    """The direct runner's whole first request: instruction, conditions, note."""
    return f"{QUOTE_INSTRUCTION}\n\n{format_effects(rows)}\n\nNOTE:\n{text}"


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass
class QuoteResult:
    """What one note yielded for the conditions asked, with the anchoring
    evidence kept alongside.

    `spans` has a key for **every** row asked — `()` when the model returned
    nothing anchorable for it — so a caller can tell "consulted, nothing" from
    "never asked" (D90's distinction, at the runner). `dropped` counts what the
    model returned that Python could not use: an unanchorable quote (re-asked
    once, then dropped), a blank one, or a passage filed under a condition that
    was not asked. The audit fields are `ExtractionResult`'s, by name, because
    the re-ask core and the recording script read them by name.
    """

    document_id: str
    rows_asked: tuple[tuple[str, str], ...]
    spans: dict[str, tuple[EvidenceSpan, ...]] = field(default_factory=dict)
    anchored_spans: list[AnchoredSpan] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    metrics: CallMetrics | None = None
    raw: dict | None = None
    raw_first_turn: dict | None = None
    reask: dict | None = None
    trace: RunTrace | None = None

    @property
    def quotes_anchored(self) -> int:
        return sum(len(spans) for spans in self.spans.values())


#: The drop reasons this builder can record. Closed, and the one that ends in
#: `_quote_unanchorable` is the one `reask_targets` re-asks about.
DROP_REASONS = frozenset({"effect_quote_unanchorable", "blank_quote", "unknown_effect"})


def build_quote_result(
    document_id: str,
    text: str,
    payload: dict,
    metrics: CallMetrics | None = None,
    *,
    rows: Sequence[MedicationEffectRow],
) -> QuoteResult:
    """Turn a model payload into anchored spans per row. Pure and model-free,
    so `pytest` verifies a recording rather than making one (D45).

    The first four parameters are `build_result`'s, in its order, because the
    re-ask core calls whichever builder it is handed the same way; `rows` is
    keyword-only and bound by `partial` at the call site.

    Every quote is located by searching the note for its verbatim text (D18);
    the model's own offsets are recorded on the `AnchoredSpan` and never
    trusted. A passage quoted twice for one condition cites once.
    """
    answers = QuoteAnswers.model_validate(payload)
    asked = rows_asked(rows)
    by_display = {row.effect_display: row for row in rows}
    result = QuoteResult(
        document_id=document_id, rows_asked=asked, metrics=metrics, raw=payload
    )
    located: dict[str, list[EvidenceSpan]] = {row.row_id: [] for row in rows}
    seen: set[tuple[str, str, int, int]] = set()

    for i, block in enumerate(answers.effects):
        row = by_display.get(block.effect)
        if row is None:
            # A condition nobody asked about. Not a citation problem — no
            # verbatim text would repair it — so no path, and never re-asked.
            result.dropped.append({"reason": "unknown_effect", "quote": block.effect[:120]})
            continue
        for j, item in enumerate(block.quotes):
            path = f"effects[{i}].quotes[{j}].quote"
            if not item.quote.strip():
                # `apply_verbatim`'s own rule: an empty quote would anchor at
                # offset zero and cite nothing.
                result.dropped.append({"reason": "blank_quote", "quote": item.quote[:120]})
                continue
            span = anchor(document_id, text, item.quote, item.char_start, item.char_end)
            result.anchored_spans.append(span)
            if not span.anchored:
                # Truncated for the record; the re-ask reads the full quote
                # back out of the payload at `path` (T-89).
                result.dropped.append(
                    {"reason": "effect_quote_unanchorable", "quote": item.quote[:120], "path": path}
                )
                continue
            evidence = span.to_span()
            key = (row.row_id, evidence.document_id, evidence.char_start, evidence.char_end)
            if key in seen:
                continue
            seen.add(key)
            located[row.row_id].append(evidence)

    result.spans = {row.row_id: tuple(located[row.row_id]) for row in rows}
    return result


# --------------------------------------------------------------------------
# The re-ask, on this payload shape (T-89's core, D103; D122)
# --------------------------------------------------------------------------


_QUOTE_PATH = re.compile(r"^effects\[(\d+)\]\.quotes\[(\d+)\]\.quote$")


def _locate_quote(payload: dict, path: str) -> tuple[dict, str]:
    """The container and key a quote path names, or `KeyError` for a path that
    names nothing in this payload — a model-invented path is never applied.
    The only field this can ever name is a `quote`, so `apply_verbatim` can
    write nothing but a quote the first turn already had."""
    match = _QUOTE_PATH.match(path)
    if match is None:
        raise KeyError(path)
    i, j = int(match.group(1)), int(match.group(2))
    effects = payload.get("effects") or []
    if i >= len(effects):
        raise KeyError(path)
    quotes = effects[i].get("quotes") or []
    if j >= len(quotes):
        raise KeyError(path)
    return quotes[j], "quote"


def consult(
    document_id: str,
    text: str,
    rows: Sequence[MedicationEffectRow],
    client,
    model: str = PINNED_MODEL,
) -> QuoteResult:
    """One note in, anchored passages per condition out. At most
    `1 + REASK_ROUNDS` model calls (Art. X): the quote turn, and one re-ask only
    when a quote could not be located (D103). `client` is injected: nothing
    here reads a credential or names a tier (D5)."""
    return extract_with_reask(
        document_id,
        text,
        first_turn=lambda: direct_first_turn(
            client, model, quote_contents(text, rows), QuoteAnswers, "quotes"
        ),
        reask_turn=lambda targets: direct_reask_turn(
            client, model, text, targets, "quotes_reask"
        ),
        runner_name="direct",
        model=model,
        step_names=("quotes", "quotes_reask"),
        build=partial(build_quote_result, rows=rows),
        locate=_locate_quote,
        prompt_version=QUOTE_PROMPT_VERSION,
    )


# --------------------------------------------------------------------------
# Failure is a classified exception, and it is never an empty result
# --------------------------------------------------------------------------


class QuoteFailure(str, Enum):
    """Why a runner could not produce an answer. Closed set, mirroring
    `ExtractionFailure` member for member; its own enum because a quote
    failure is classified by the review's caller, not at the workflow
    boundary (D122)."""

    NO_PAYLOAD = "NO_PAYLOAD"
    UNPARSEABLE = "UNPARSEABLE"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    CALL_FAILED = "CALL_FAILED"
    DOCUMENT_CHANGED = "DOCUMENT_CHANGED"
    NOT_RECORDED = "NOT_RECORDED"


class QuoteOutputError(ValueError):
    """No answer, classified. A note that documents nothing is an answer —
    `QuoteResult` with every row at `()` — and is never this."""

    def __init__(self, reason: QuoteFailure, message: str) -> None:
        super().__init__(f"{reason.value}: {message}")
        self.reason = reason
        self.message = message


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


@runtime_checkable
class QuoteRunner(Protocol):
    """One note and the conditions to look for in, anchored passages out.

    `name` is recorded on the run trace, so a review's provenance says which
    implementation read the notes. A recorded run and a live run are not the
    same claim.
    """

    name: str

    def run(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow]
    ) -> QuoteResult:
        """Consult one note for every row asked.

        Raises `QuoteOutputError` when no answer could be produced. It must
        never return an empty result to signal failure — a note that documents
        none of the conditions is the answer red is built on.
        """
        ...


# --------------------------------------------------------------------------
# Direct: the configuration the measurement records
# --------------------------------------------------------------------------


class DirectQuoteRunner:
    """`google-genai` with a native `response_schema` — `consult()`, and never a
    reimplementation of it (D45's rule, as `DirectExtractionRunner` states it).
    The client is injected, so nothing here reads a credential or names a tier."""

    name = "direct"

    def __init__(self, client, model: str = PINNED_MODEL) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def run(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow]
    ) -> QuoteResult:
        try:
            return consult(document_id, text, rows, self._client, self._model)
        except QuoteOutputError:
            raise
        except json.JSONDecodeError as exc:
            # Terminal, one attempt: the same prompt returns the same invalid
            # response (D8, D76's classification).
            raise QuoteOutputError(
                QuoteFailure.UNPARSEABLE, f"{document_id}: response is not JSON: {exc}"
            ) from exc
        except ValidationError as exc:
            raise QuoteOutputError(
                QuoteFailure.SCHEMA_INVALID,
                f"{document_id}: response failed schema validation: {exc}",
            ) from exc
        except Exception as exc:  # re-raised classified, never swallowed (REQ-27)
            raise QuoteOutputError(
                QuoteFailure.CALL_FAILED, f"{type(exc).__name__}: {exc}"
            ) from exc


# --------------------------------------------------------------------------
# Recorded: every gate consults the notes for nothing
# --------------------------------------------------------------------------


class RecordedQuoteRunner:
    """Replays a recorded payload through `build_quote_result()`. No model call.

    `RecordedExtractionRunner`'s shape (D102): keyed by content sha256 first,
    then by `document_id`, so a declared clone's byte-identical note answers
    under its own id with spans pointing into its own document; a recorded id
    whose bytes moved is `DOCUMENT_CHANGED` (D18's rule); a note nobody
    measured is `NOT_RECORDED`, naming the script. And one rule of its own:
    the recording says which `(row_id, effect_display)` pairs were asked, and a
    request naming a pair outside that set is `NOT_RECORDED` too — adding a
    table row is a new measurement, never a replay of an old one (D45, D122).
    """

    name = "recorded"

    def __init__(
        self,
        payloads: dict[str, dict],
        rows_asked: Sequence[tuple[str, str]],
        note_hashes: dict[str, str] | None = None,
        model: str | None = None,
        metrics: dict[str, CallMetrics] | None = None,
        traces: dict[str, RunTrace] | None = None,
    ) -> None:
        self._payloads = dict(payloads)
        self._rows_asked = tuple((str(a), str(b)) for a, b in rows_asked)
        self._hashes = dict(note_hashes or {})
        self._model = model
        self._metrics = dict(metrics or {})
        self._traces = dict(traces or {})
        self._by_hash = {digest: document_id for document_id, digest in self._hashes.items()}

    @classmethod
    def from_records(
        cls,
        records: list[dict],
        rows_asked: Sequence,
        model: str | None = None,
    ):
        """Build one from a recording's `notes[]` and `rows_asked[]`, as written
        by `scripts/run_quote_measurement.py`. Skips records with no `raw`."""
        payloads: dict[str, dict] = {}
        hashes: dict[str, str] = {}
        metrics: dict[str, CallMetrics] = {}
        traces: dict[str, RunTrace] = {}
        for record in records:
            document_id = record.get("document_id")
            raw = record.get("raw")
            if not document_id or raw is None:
                continue
            payloads[document_id] = raw
            if record.get("note_sha256"):
                hashes[document_id] = record["note_sha256"]
            if record.get("metrics"):
                metrics[document_id] = CallMetrics.model_validate(record["metrics"])
            if record.get("trace"):
                traces[document_id] = RunTrace.model_validate(record["trace"])
        asked = [
            (entry["row_id"], entry["effect_display"]) if isinstance(entry, dict) else tuple(entry)
            for entry in rows_asked
        ]
        return cls(payloads, asked, hashes, model=model, metrics=metrics, traces=traces)

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def rows_asked(self) -> tuple[tuple[str, str], ...]:
        return self._rows_asked

    def __contains__(self, document_id: str) -> bool:
        return document_id in self._payloads

    def run(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow]
    ) -> QuoteResult:
        wanted = rows_asked(rows)
        missing = [pair for pair in wanted if pair not in self._rows_asked]
        if missing:
            raise QuoteOutputError(
                QuoteFailure.NOT_RECORDED,
                f"the recording was not asked about {missing}; adding or renaming a "
                "knowledge-table row is a new measurement (D45) — run "
                "`python scripts/run_quote_measurement.py` (it spends model calls) "
                "or pass a live runner",
            )

        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual in self._by_hash:
            recorded_id = self._by_hash[actual]
        elif document_id in self._payloads:
            recorded_hash = self._hashes.get(document_id)
            if recorded_hash is not None:
                raise QuoteOutputError(
                    QuoteFailure.DOCUMENT_CHANGED,
                    f"{document_id} hashes to {actual[:12]} and the payload was "
                    f"recorded against {recorded_hash[:12]}. Re-anchoring would "
                    "score a quote against a document it never came from (D18).",
                )
            recorded_id = document_id
        else:
            raise QuoteOutputError(
                QuoteFailure.NOT_RECORDED,
                f"no recorded quotes for {document_id!r} (sha256 {actual[:12]}); "
                "run `python scripts/run_quote_measurement.py` (it spends model "
                "calls) or pass a live runner",
            )

        try:
            # Built under the *requesting* id, so spans point into the document
            # the caller handed in; the recorded metrics carried through
            # unchanged, because no call happens here (Art. X).
            result = build_quote_result(
                document_id, text, self._payloads[recorded_id],
                rows=rows, metrics=self._metrics.get(recorded_id),
            )
        except QuoteOutputError:
            raise
        except Exception as exc:
            raise QuoteOutputError(
                QuoteFailure.SCHEMA_INVALID,
                f"recorded payload for {document_id} no longer validates: "
                f"{type(exc).__name__}: {exc}",
            ) from exc
        trace = self._traces.get(recorded_id)
        if trace is not None:
            result.trace = trace.model_copy(update={"document_id": document_id})
        return result


class NullQuoteRunner:
    """Raises on any call. The runner a note-free chart's test hands in: an
    answer reached anyway proves no note was consulted (A4's pattern)."""

    name = "null"

    def __init__(self, note: str = "") -> None:
        self._note = note

    def run(
        self, document_id: str, text: str, rows: Sequence[MedicationEffectRow]
    ) -> QuoteResult:
        raise AssertionError(
            f"the quote runner was called for {document_id!r}; this path is "
            f"supposed to consult no note. {self._note}".strip()
        )
