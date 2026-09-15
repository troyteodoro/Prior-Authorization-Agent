"""Spike 002 — step-therapy extraction fidelity on hand-written rheumatology notes.

Answers the question D97 makes everything downstream wait on: can a single
model call read rheumatology documentation and return correct methotrexate
encounters, with citable spans, without counting missed visits, contact
attempts, or other agents' drug courses?

Three modes, in spike 001's shape (D17, D18 — the scoring and anchoring rules
are reused, not redecided):

    python spike/spike_002/run.py            measure. Spends model calls.
    python spike/spike_002/run.py --rescore  re-score and re-anchor the recorded
                                             output. Spends none.
    python spike/spike_002/run.py --verify   check the recorded artifacts.
                                             Spends none. This is T-85's gate.

The model extracts and Python scores. Nothing here lets a model decide anything
(Article I), and every number below is computed in Python from the model's
output (Article II). The kill criteria this spike is measured against are the
v1.5 set in docs/decisions.md, dated before this file existed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parents[1]
NOTES_DIR = SPIKE_DIR / "notes"
LABELS_PATH = SPIKE_DIR / "labels.json"
RESULTS_PATH = SPIKE_DIR / "results.json"
DECISIONS_PATH = REPO_ROOT / "docs" / "decisions.md"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

# Run directly (`python spike/spike_002/run.py`) and sys.path[0] is this
# directory, not the repo root, so `pa_agent` is not importable. Same insertion
# spike 001 makes, for the same reason.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.model_pin import PINNED_MODEL  # noqa: E402

# D20: the model measured on is the pinned one, never a literal written here.
DEFAULT_MODEL = PINNED_MODEL
DEFAULT_RUNS = 3
EXPECTED_NOTE_COUNT = 5

# A run in which any note errored is not a run. Two complete runs is the floor
# for claiming a measurement, because one run cannot show whether the same note
# yields the same events twice.
MIN_COMPLETE_RUNS = 2

# The v1.5 kill criteria this spike answers to (docs/decisions.md, standing
# section, dated 2026-09-14 — set before any measurement existed).
PRECISION_FLOOR = 0.8
EXCLUSION_RECALL_FLOOR = 0.9
ANCHOR_DROP_CEILING = 0.1


# --------------------------------------------------------------------------
# Extraction schema. The step-therapy analogue of REQ-8's shape, narrowed to
# what T-85 measures. Per-field spans are T-88's; widening the schema here
# would test something other than event fidelity.
# --------------------------------------------------------------------------


class ExtractedEvent(BaseModel):
    date: str = Field(description="Encounter date, normalized to YYYY-MM-DD.")
    quote: str = Field(
        description="Text copied verbatim and contiguously from the note."
    )
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")
    dose_documented: bool = False
    response_assessed: bool = False
    labs_reviewed: bool = False


class ExtractedAssertion(BaseModel):
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int
    char_end: int
    claim: str = Field(description="What the passage claims, in a few words.")


class Extraction(BaseModel):
    mtx_events: list[ExtractedEvent] = Field(default_factory=list)
    trial_assertions: list[ExtractedAssertion] = Field(default_factory=list)


INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review. Return only what the schema defines. Do not explain.

An mtx_event is one documented clinical ENCOUNTER that actually took place and
at which the patient's methotrexate therapy is actively managed: methotrexate
is administered, prescribed, started, restarted, or its ongoing dose is
stated, adjusted, or continued. For each one return:

  date         the encounter date, normalized to YYYY-MM-DD
  quote        text copied verbatim and contiguously from the note, long enough
               to show both the date and the methotrexate management. It must
               appear in the note character for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character
  dose_documented    true only if a numeric methotrexate dose is recorded at
                     that encounter. "Continued at current dose" with no
                     number is false.
  response_assessed  true only if disease activity or response to therapy is
                     assessed at that encounter: joint counts, morning
                     stiffness, a disease activity score, or an explicit
                     response statement.
  labs_reviewed      true only if laboratory monitoring (blood counts, liver
                     enzymes, metabolic panel) is reviewed at that encounter.

Never return an mtx_event for any of the following. None of them is a
methotrexate encounter:

  - a missed, cancelled, no-show, or rescheduled appointment or infusion
  - an unsuccessful contact attempt: an unanswered call, a voicemail, a letter
  - an encounter or drug course involving a different agent —
    hydroxychloroquine, sulfasalazine, leflunomide, or any biologic — unless
    methotrexate itself is also managed at that same encounter
  - a patient's recollection of past methotrexate treatment with no record of
    the underlying encounters. That is a trial_assertion, below.
  - a date in an unrelated section: family history, immunizations, past
    surgical history, health maintenance, referrals, or administrative and
    insurance dates

A trial_assertion is a passage claiming that a methotrexate trial happened,
failed, or was not tolerated, without documenting the underlying encounters.
Record it with the same quote and offset fields. An assertion is never also an
mtx_event: if the note documents the encounters themselves, those are
mtx_events.

Return every qualifying encounter in the note, including encounters from
courses that are old, interrupted, or appear irrelevant to the current
request. Do not filter by date, by recency, or by which course looks most
relevant. Choosing among them happens elsewhere.
"""


# --------------------------------------------------------------------------
# Loading and label self-checks
# --------------------------------------------------------------------------


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_env() -> None:
    """Read the gitignored .env. Never commit a real key (working rule 10)."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_corpus() -> tuple[dict[str, Any], dict[str, str]]:
    labels = json.loads(LABELS_PATH.read_text())
    texts = {
        note["note_id"]: (SPIKE_DIR / note["file"]).read_text()
        for note in labels["notes"]
    }
    return labels, texts


ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_labels(labels: dict[str, Any], texts: dict[str, str]) -> list[str]:
    """The labels are as likely to be wrong as the model.

    A typo in a hint or a date silently depresses the measured score and looks
    like a model failure, so it fails the gate loudly instead.
    """
    problems: list[str] = []
    notes = labels["notes"]

    if len(notes) != EXPECTED_NOTE_COUNT:
        problems.append(f"expected {EXPECTED_NOTE_COUNT} notes, labels hold {len(notes)}")

    for note in notes:
        nid = note["note_id"]
        text = texts[nid]
        seen: set[str] = set()
        for kind in ("events", "traps"):
            for item in note[kind]:
                d = item["date"]
                if not ISO.match(d):
                    problems.append(f"{nid}: {kind} date {d!r} is not YYYY-MM-DD")
                else:
                    try:
                        date.fromisoformat(d)
                    except ValueError:
                        problems.append(f"{nid}: {kind} date {d!r} is not a real date")
                if d in seen:
                    problems.append(
                        f"{nid}: date {d} appears twice; D17 scores on the date, "
                        "so it has to be a unique key within a note"
                    )
                seen.add(d)
                hint = item["hint"]
                if hint not in text:
                    problems.append(f"{nid}: hint {hint!r} does not occur in the note")

    if not any(gap_months_with_missed_visits(n) for n in notes):
        problems.append(
            "T-85 requires at least one note carrying missed-visit dates inside "
            "a gap month (E17's seed); no labeled note has that shape"
        )
    if not any(n["trial_assertion_required"] for n in notes):
        problems.append(
            "T-85 requires at least one note carrying a bare trial assertion "
            "with no encounter detail (E16's seed); no labeled note requires one"
        )
    return problems


def gap_months_with_missed_visits(note: dict[str, Any]) -> list[str]:
    """Months holding a missed visit, with events on both sides and none within.

    E17's shape. Counting such a date as an encounter closes the gap and flips
    the trial-length criterion from NOT_MET to MET, which is why T-85 requires
    the case exist.
    """
    event_months = sorted({e["date"][:7] for e in note["events"]})
    if len(event_months) < 2:
        return []
    found = []
    for trap in note["traps"]:
        if trap["reason"] != "missed_visit":
            continue
        month = trap["date"][:7]
        if month in event_months:
            continue
        if any(m < month for m in event_months) and any(m > month for m in event_months):
            found.append(month)
    return sorted(set(found))


# --------------------------------------------------------------------------
# Span anchoring (D17, D18): the model's quote is located by Python. Its own
# offsets are recorded but never trusted. Matching is exact first and
# whitespace-normalized second; the offsets recorded are always raw.
# --------------------------------------------------------------------------

WHITESPACE = re.compile(r"\s+")


def collapse(text: str) -> str:
    """Every run of whitespace becomes one space (D18)."""
    return WHITESPACE.sub(" ", text).strip()


def collapse_with_index(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace, keeping a map from each collapsed offset to its raw one.

    `index[i]` is the offset in `text` that produced `collapsed[i]`. For a
    collapsed run of whitespace that is the offset of the run's first character.
    The map is what lets a normalized hit come back as offsets into the
    unmodified document: the span recorded and cited is raw, only the
    comparison was relaxed.
    """
    out: list[str] = []
    index: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            out.append(" ")
            index.append(i)
            i = j
        else:
            out.append(text[i])
            index.append(i)
            i += 1
    if out and out[0] == " ":
        out.pop(0)
        index.pop(0)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


@dataclass
class AnchoredSpan:
    document_id: str
    quote: str
    model_start: int
    model_end: int
    char_start: int | None = None
    char_end: int | None = None
    anchor_mode: str | None = None
    occurrences: int = 0
    slice_non_empty: bool = False
    model_offsets_exact: bool = False
    model_offsets_yield_quote: bool = False

    @property
    def anchored(self) -> bool:
        return self.char_start is not None


def anchor(document_id: str, text: str, quote: str, m_start: int, m_end: int) -> AnchoredSpan:
    span = AnchoredSpan(document_id, quote, m_start, m_end)
    if not quote:
        return span

    found = text.find(quote)
    if found != -1:
        span.anchor_mode = "exact"
        span.occurrences = text.count(quote)
        span.char_start = found
        span.char_end = found + len(quote)
    else:
        # D18: these notes hard-wrap as EHR-exported text does, so a quote
        # crossing a wrap point arrives with its newline normalized to a
        # space — a property of the file, not of the claim. Retry under
        # whitespace normalization. A fabricated quote still misses.
        flat_text, index = collapse_with_index(text)
        flat_quote = collapse(quote)
        found = flat_text.find(flat_quote) if flat_quote else -1
        if found == -1:
            # Article III: Python could not locate this quote, so no span is
            # produced for it. Still a string comparison, still no judgment.
            return span
        span.anchor_mode = "normalized"
        span.occurrences = flat_text.count(flat_quote)
        span.char_start = index[found]
        span.char_end = index[found + len(flat_quote) - 1] + 1

    fragment = text[span.char_start : span.char_end]
    if collapse(fragment) != collapse(quote):
        span.char_start = None
        span.char_end = None
        span.anchor_mode = None
        return span

    span.slice_non_empty = bool(fragment.strip())
    span.model_offsets_exact = (m_start == span.char_start and m_end == span.char_end)
    if 0 <= m_start < m_end <= len(text):
        span.model_offsets_yield_quote = collapse(text[m_start:m_end]) == collapse(quote)
    return span


def _record_span(span: AnchoredSpan, note_id: str, **extra: Any) -> dict[str, Any]:
    """The recorded shape of a span, in one place because --rescore rebuilds it."""
    record: dict[str, Any] = {**extra, **span.__dict__}
    record["document_id"] = note_id
    return record


# --------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------


@dataclass
class CallMetrics:
    prompt_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    wall_seconds: float = 0.0
    attempts: int = 1


RETRYABLE_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 4.0


def _retryable(exc: BaseException) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in RETRYABLE_STATUS:
        return True
    text = str(exc)
    return any(f" {c} " in f" {text} " or f"{c} " in text for c in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))


async def extract_one(note_id: str, text: str, model: str, run_index: int) -> tuple[dict[str, Any] | None, CallMetrics, str | None]:
    """One extraction call over one note, in a session of its own (D17)."""
    from google.adk import Runner
    from google.adk.agents.llm_agent import Agent
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = Agent(
        model=model,
        name="spike_002_extractor",
        description="Extracts methotrexate-trial encounters from a clinical note.",
        instruction=INSTRUCTION,
        output_schema=Extraction,
        output_key="extraction",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=f"Clinical note (document_id: {note_id}):\n\n{text}")],
    )

    metrics = CallMetrics()
    payload: dict[str, Any] | None = None
    error: str | None = None
    started = time.monotonic()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        metrics.attempts = attempt
        payload, error = None, None
        session_id = f"{note_id}-r{run_index}-a{attempt}"
        session_service = InMemorySessionService()
        runner = Runner(app_name="spike_002", agent=agent, session_service=session_service)
        await session_service.create_session(
            app_name="spike_002", user_id="spike", session_id=session_id
        )
        try:
            async for event in runner.run_async(
                user_id="spike", session_id=session_id, new_message=message
            ):
                if event.usage_metadata:
                    metrics.prompt_tokens += event.usage_metadata.prompt_token_count or 0
                    metrics.output_tokens += event.usage_metadata.candidates_token_count or 0
                    metrics.total_tokens += event.usage_metadata.total_token_count or 0
                if event.actions and event.actions.state_delta.get("extraction") is not None:
                    payload = event.actions.state_delta["extraction"]
        except Exception as exc:  # recorded, never swallowed (REQ-27)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await runner.close()

        if error is None:
            break
        if attempt == MAX_ATTEMPTS or not _retryable(Exception(error)):
            break
        delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        print(f"    transient failure, retrying in {delay:.0f}s ({error[:70]})", flush=True)
        await asyncio.sleep(delay)

    metrics.wall_seconds = round(time.monotonic() - started, 3)
    return payload, metrics, error


# --------------------------------------------------------------------------
# Scoring. Every number below is computed here, in Python.
# --------------------------------------------------------------------------


@dataclass
class NoteScore:
    note_id: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float | None = None
    recall: float | None = None
    traps_total: int = 0
    traps_excluded: int = 0
    exclusion_recall: float | None = None
    trap_leaks: list[dict[str, str]] = field(default_factory=list)
    unmatched_extracted: list[str] = field(default_factory=list)
    missed_labeled: list[str] = field(default_factory=list)
    malformed_dates: list[str] = field(default_factory=list)
    trial_assertions: int = 0
    assertion_required: bool = False
    assertion_satisfied: bool = True
    spans: list[dict[str, Any]] = field(default_factory=list)
    quotes_not_found: int = 0
    spans_anchored: int = 0
    spans_normalized: int = 0
    spans_slice_non_empty: int = 0
    spans_multi_occurrence: int = 0
    model_offsets_exact: int = 0
    model_offsets_yield_quote: int = 0
    field_agreement: dict[str, int] = field(default_factory=dict)
    errored: bool = False
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def score_note(
    note: dict[str, Any],
    text: str,
    payload: dict[str, Any] | None,
    error: str | None = None,
) -> NoteScore:
    nid = note["note_id"]
    s = NoteScore(note_id=nid)
    s.assertion_required = note["trial_assertion_required"]
    s.error = error

    if error is not None or payload is None:
        # The call never produced an extraction, so there is nothing to score.
        # A fault and a finding are different things (Article IV, REQ-28).
        s.errored = True
        s.traps_total = len(note["traps"])
        s.traps_excluded = 0
        s.exclusion_recall = None
        s.precision = None
        s.recall = None
        return s

    labeled = {e["date"]: e for e in note["events"]}
    traps = {t["date"]: t for t in note["traps"]}
    s.traps_total = len(traps)

    events = (payload or {}).get("mtx_events", []) or []
    assertions = (payload or {}).get("trial_assertions", []) or []
    s.trial_assertions = len(assertions)
    s.assertion_satisfied = (not s.assertion_required) or len(assertions) >= 1

    matched: set[str] = set()
    extracted_dates: list[str] = []
    agree = {"dose_documented": 0, "response_assessed": 0, "labs_reviewed": 0}

    for ev in events:
        raw = str(ev.get("date", ""))
        span = anchor(nid, text, str(ev.get("quote", "")), int(ev.get("char_start", -1)), int(ev.get("char_end", -1)))
        s.spans.append(_record_span(span, nid, kind="mtx_event", date=raw))
        _tally_span(s, span)

        if not ISO.match(raw):
            s.malformed_dates.append(raw)
            s.false_positives += 1
            continue
        extracted_dates.append(raw)
        if raw in labeled and raw not in matched:
            matched.add(raw)
            s.true_positives += 1
            truth = labeled[raw]
            for fld in agree:
                if bool(ev.get(fld, False)) == bool(truth[fld]):
                    agree[fld] += 1
        else:
            # A repeat of an already-matched date is a duplicate, and a
            # duplicate encounter is a false encounter.
            s.false_positives += 1
            s.unmatched_extracted.append(raw)
            if raw in traps:
                s.trap_leaks.append(
                    {"date": raw, "reason": traps[raw]["reason"], "detail": traps[raw]["detail"]}
                )

    for a in assertions:
        span = anchor(nid, text, str(a.get("quote", "")), int(a.get("char_start", -1)), int(a.get("char_end", -1)))
        s.spans.append(
            _record_span(span, nid, kind="trial_assertion", claim=str(a.get("claim", "")))
        )
        _tally_span(s, span)

    s.missed_labeled = sorted(set(labeled) - matched)
    s.false_negatives = len(s.missed_labeled)

    leaked = {t["date"] for t in s.trap_leaks}
    s.traps_excluded = s.traps_total - len(leaked & set(extracted_dates))
    s.exclusion_recall = (
        round(s.traps_excluded / s.traps_total, 4) if s.traps_total else None
    )

    denom_p = s.true_positives + s.false_positives
    denom_r = s.true_positives + s.false_negatives
    s.precision = round(s.true_positives / denom_p, 4) if denom_p else None
    s.recall = round(s.true_positives / denom_r, 4) if denom_r else None
    s.field_agreement = agree
    return s


def _tally_span(s: NoteScore, span: AnchoredSpan) -> None:
    if not span.anchored:
        s.quotes_not_found += 1
        return
    s.spans_anchored += 1
    if span.anchor_mode == "normalized":
        s.spans_normalized += 1
    if span.slice_non_empty:
        s.spans_slice_non_empty += 1
    if span.occurrences > 1:
        s.spans_multi_occurrence += 1
    if span.model_offsets_exact:
        s.model_offsets_exact += 1
    if span.model_offsets_yield_quote:
        s.model_offsets_yield_quote += 1


def aggregate(all_notes: list[NoteScore]) -> dict[str, Any]:
    """Micro-average across the notes that actually produced an extraction.

    Micro rather than macro because r03 labels zero events, so its per-note
    precision is 0/0 and undefined. Micro handles that correctly: r03
    contributes nothing unless the model invents an encounter, in which case
    it contributes a false positive and the corpus number moves.

    Errored notes are excluded from every rate and counted on their own. A run
    that did not complete is not a run that scored badly.
    """
    note_scores = [s for s in all_notes if not s.errored]
    errored = [s for s in all_notes if s.errored]

    tp = sum(s.true_positives for s in note_scores)
    fp = sum(s.false_positives for s in note_scores)
    fn = sum(s.false_negatives for s in note_scores)
    traps = sum(s.traps_total for s in note_scores)
    excluded = sum(s.traps_excluded for s in note_scores)
    anchored = sum(s.spans_anchored for s in note_scores)
    normalized = sum(s.spans_normalized for s in note_scores)
    non_empty = sum(s.spans_slice_non_empty for s in note_scores)
    produced = anchored + sum(s.quotes_not_found for s in note_scores)
    return {
        "notes_scored": len(note_scores),
        "notes_errored": len(errored),
        "errored_note_ids": [s.note_id for s in errored],
        "complete": not errored,
        "event_precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "event_recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "exclusion_count": excluded,
        "exclusion_traps_total": traps,
        "exclusion_recall": round(excluded / traps, 4) if traps else None,
        "quotes_emitted": produced,
        "quotes_not_found": produced - anchored,
        "spans_anchored": anchored,
        "spans_normalized": normalized,
        "normalized_anchor_rate": round(normalized / anchored, 4) if anchored else None,
        "spans_slice_non_empty": non_empty,
        "span_validity_rate": round(non_empty / anchored, 4) if anchored else None,
        "spans_multi_occurrence": sum(s.spans_multi_occurrence for s in note_scores),
        "model_offsets_exact": sum(s.model_offsets_exact for s in note_scores),
        "model_offsets_yield_quote": sum(s.model_offsets_yield_quote for s in note_scores),
        "assertion_requirements_met": all(s.assertion_satisfied for s in note_scores),
    }


# --------------------------------------------------------------------------
# Measure
# --------------------------------------------------------------------------


async def measure(model: str, runs: int) -> dict[str, Any]:
    labels, texts = load_corpus()
    problems = check_labels(labels, texts)
    if problems:
        print("Label self-check failed. Fix the labels before measuring:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        raise SystemExit(2)

    def snapshot(runs_so_far: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "spike": "002",
            "question": "Can one model call extract correct methotrexate-trial encounters "
                        "with citable spans from rheumatology documentation, excluding "
                        "missed visits, contact attempts, and other agents' courses? (D97)",
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "temperature": 0.0,
            "runs": runs,
            "scoring": "D17: an event matches on ISO date; spans anchored by quote (D18).",
            "note_hashes": {nid: sha256(t) for nid, t in sorted(texts.items())},
            "labels_hash": sha256(LABELS_PATH.read_text()),
            "per_run": runs_so_far,
            "corpus": _across_runs(runs_so_far),
        }

    all_runs: list[dict[str, Any]] = []
    for run_index in range(runs):
        note_scores: list[NoteScore] = []
        for note in labels["notes"]:
            nid = note["note_id"]
            payload, metrics, error = await extract_one(nid, texts[nid], model, run_index)
            s = score_note(note, texts[nid], payload, error)
            s.metrics = metrics.__dict__
            note_scores.append(s)
            print(
                f"  run {run_index} {nid:34s} "
                f"P={_fmt(s.precision)} R={_fmt(s.recall)} "
                f"excl={s.traps_excluded}/{s.traps_total} "
                f"{metrics.total_tokens:>6d} tok {metrics.wall_seconds:>6.2f}s"
                + (f" x{metrics.attempts}" if metrics.attempts > 1 else "")
                + (f"  ERROR {s.error}" if s.error else ""),
                flush=True,
            )
        agg = aggregate(note_scores)
        all_runs.append(
            {
                "run_index": run_index,
                "notes": [_asdict(s) for s in note_scores],
                "aggregate": agg,
            }
        )
        # Written after every run, so a later run dying on quota cannot take
        # the completed ones down with it.
        RESULTS_PATH.write_text(json.dumps(snapshot(all_runs), indent=2) + "\n")
        print(
            f"  run {run_index} {'complete' if agg['complete'] else 'INCOMPLETE'}; "
            f"{RESULTS_PATH.name} updated ({len(all_runs)} run(s) recorded)",
            flush=True,
        )

    return snapshot(all_runs)


def _across_runs(all_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Corpus numbers, computed over complete runs only."""
    complete = [r for r in all_runs if r["aggregate"]["complete"]]
    incomplete = [r for r in all_runs if not r["aggregate"]["complete"]]

    precisions = [r["aggregate"]["event_precision"] for r in complete]
    recalls = [r["aggregate"]["event_recall"] for r in complete]
    exclusions = [r["aggregate"]["exclusion_recall"] for r in complete]

    def mean(xs: list[float | None]) -> float | None:
        vals = [x for x in xs if x is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    # Stability: does the same note yield the same event dates every run?
    per_note_dates: dict[str, list[list[str]]] = {}
    for r in complete:
        for n in r["notes"]:
            dates = sorted(s["date"] for s in n["spans"] if s["kind"] == "mtx_event")
            per_note_dates.setdefault(n["note_id"], []).append(dates)
    stable = {nid: len({tuple(d) for d in runs_}) == 1 for nid, runs_ in per_note_dates.items()}

    prec_mean = mean(precisions)
    excl_mean = mean(exclusions)
    anchored_total = sum(r["aggregate"]["spans_anchored"] for r in complete)
    normalized_total = sum(r["aggregate"].get("spans_normalized", 0) for r in complete)
    emitted_total = sum(r["aggregate"]["quotes_emitted"] for r in complete)
    not_found_total = sum(r["aggregate"]["quotes_not_found"] for r in complete)
    drop_rate = round(not_found_total / emitted_total, 4) if emitted_total else None
    return {
        "runs_attempted": len(all_runs),
        "runs_complete": len(complete),
        "runs_incomplete": len(incomplete),
        "incomplete_run_indexes": [r["run_index"] for r in incomplete],
        "aggregated_over": "complete runs only (REQ-28's discipline)",
        "event_precision_per_run": precisions,
        "event_recall_per_run": recalls,
        "exclusion_recall_per_run": exclusions,
        "event_precision_mean": prec_mean,
        "event_recall_mean": mean(recalls),
        "exclusion_recall_mean": excl_mean,
        "exclusion_count_total": sum(r["aggregate"]["exclusion_count"] for r in complete),
        "exclusion_traps_total": sum(r["aggregate"]["exclusion_traps_total"] for r in complete),
        "spans_anchored_total": anchored_total,
        "spans_normalized_total": normalized_total,
        "normalized_anchor_rate": (
            round(normalized_total / anchored_total, 4) if anchored_total else None
        ),
        "quotes_emitted_total": emitted_total,
        "quotes_not_found_total": not_found_total,
        "anchoring_drop_rate": drop_rate,
        "spans_multi_occurrence_total": sum(
            r["aggregate"]["spans_multi_occurrence"] for r in complete
        ),
        "model_offsets_exact_total": sum(r["aggregate"]["model_offsets_exact"] for r in complete),
        "model_offsets_yield_quote_total": sum(
            r["aggregate"]["model_offsets_yield_quote"] for r in complete
        ),
        "identical_events_across_runs": stable,
        "stable_on_all_notes": bool(stable) and all(stable.values()),
        "total_tokens": sum(
            n["metrics"].get("total_tokens", 0) for r in all_runs for n in r["notes"]
        ),
        "total_wall_seconds": round(
            sum(n["metrics"].get("wall_seconds", 0.0) for r in all_runs for n in r["notes"]), 2
        ),
        # The v1.5 kill criteria, measured. Thresholds are the dated entry's,
        # restated here so the recording carries its own verdict.
        "kill_criteria": {
            "event_precision_floor": PRECISION_FLOOR,
            "event_precision_measured": prec_mean,
            "precision_clears_floor": prec_mean is not None and prec_mean >= PRECISION_FLOOR,
            "exclusion_recall_floor": EXCLUSION_RECALL_FLOOR,
            "exclusion_recall_measured": excl_mean,
            "exclusion_clears_floor": excl_mean is not None and excl_mean >= EXCLUSION_RECALL_FLOOR,
            "anchoring_drop_rate_ceiling": ANCHOR_DROP_CEILING,
            "anchoring_drop_rate_measured": drop_rate,
            "anchoring_clears_ceiling": drop_rate is not None and drop_rate <= ANCHOR_DROP_CEILING,
        },
    }


def _fmt(x: float | None) -> str:
    return "  n/a" if x is None else f"{x:.3f}"


def _asdict(s: NoteScore) -> dict[str, Any]:
    return {k: v for k, v in s.__dict__.items()}


def _reanchor(s: NoteScore, text: str) -> None:
    """Re-locate every recorded quote under the current anchor rule (D18)."""
    recorded = list(s.spans)
    s.spans = []
    s.quotes_not_found = 0
    s.spans_anchored = 0
    s.spans_normalized = 0
    s.spans_slice_non_empty = 0
    s.spans_multi_occurrence = 0
    s.model_offsets_exact = 0
    s.model_offsets_yield_quote = 0

    for rec in recorded:
        span = anchor(
            s.note_id,
            text,
            str(rec.get("quote", "")),
            int(rec.get("model_start", -1)),
            int(rec.get("model_end", -1)),
        )
        extra = {k: rec[k] for k in ("kind", "date", "claim") if k in rec}
        s.spans.append(_record_span(span, s.note_id, **extra))
        _tally_span(s, span)


def rescore() -> int:
    """Recompute the aggregates from the recorded per-note data. No model call.

    A bug in the aggregation must not cost a re-measurement. Re-anchoring is
    only sound because the note hashes are checked first — re-anchoring a
    quote against a document it never came from would be scoring one
    measurement's output against another's input.
    """
    if not RESULTS_PATH.exists():
        print("FAIL  results.json is absent; nothing to rescore.", file=sys.stderr)
        return 1

    results = json.loads(RESULTS_PATH.read_text())
    _, texts = load_corpus()
    recorded_hashes = results.get("note_hashes", {})
    drifted = [nid for nid, t in sorted(texts.items()) if recorded_hashes.get(nid) != sha256(t)]
    if drifted:
        for nid in drifted:
            print(
                f"FAIL  note {nid} has changed since measurement; its recorded quotes "
                "cannot be re-anchored against it. Re-measure.",
                file=sys.stderr,
            )
        return 1

    for run in results["per_run"]:
        scores = []
        for n in run["notes"]:
            s = NoteScore(note_id=n["note_id"])
            for k, v in n.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            if n.get("error") is not None:
                s.errored = True
                s.traps_excluded = 0
                s.exclusion_recall = None
                s.precision = None
                s.recall = None
            if not s.errored:
                _reanchor(s, texts[s.note_id])
            scores.append(s)
            n.clear()
            n.update(_asdict(s))
        run["aggregate"] = aggregate(scores)
    results["corpus"] = _across_runs(results["per_run"])
    results["rescored_at"] = datetime.now(timezone.utc).isoformat()
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")

    c = results["corpus"]
    print(
        f"Rescored {RESULTS_PATH.relative_to(REPO_ROOT)} from recorded data.\n"
        f"  {c['runs_complete']}/{c['runs_attempted']} runs complete"
        + (f", incomplete: {c['incomplete_run_indexes']}" if c["runs_incomplete"] else "")
        + f"\n  event precision {_fmt(c['event_precision_mean'])}   "
        f"recall {_fmt(c['event_recall_mean'])}"
    )
    return 0


# --------------------------------------------------------------------------
# Verify — T-85's exit condition. No model call.
# --------------------------------------------------------------------------


def verify() -> int:
    failures: list[str] = []
    checks: list[str] = []

    def ok(msg: str) -> None:
        checks.append(msg)

    def bad(msg: str) -> None:
        failures.append(msg)

    if not RESULTS_PATH.exists():
        print("FAIL  results.json is absent. Run without --verify to measure first.", file=sys.stderr)
        return 1

    results = json.loads(RESULTS_PATH.read_text())
    labels, texts = load_corpus()

    # 1. Five hand-labeled notes: one with missed visits in a gap month (E17's
    #    seed), one requiring a bare trial assertion (E16's seed).
    problems = check_labels(labels, texts)
    for p in problems:
        bad(f"labels: {p}")
    if not problems:
        shaped = [n["note_id"] for n in labels["notes"] if gap_months_with_missed_visits(n)]
        ok(f"{len(labels['notes'])} hand-labeled notes; gap-month missed visits in {', '.join(shaped)}")

    # 2. The measurement describes the notes on disk right now.
    recorded = results.get("note_hashes", {})
    current = {nid: sha256(t) for nid, t in texts.items()}
    if recorded != current:
        for nid in sorted(set(recorded) | set(current)):
            if recorded.get(nid) != current.get(nid):
                bad(f"note {nid} has changed since measurement; re-measure")
    else:
        ok(f"all {len(current)} note hashes match the recorded measurement")
    if results.get("labels_hash") != sha256(LABELS_PATH.read_text()):
        bad("labels.json has changed since measurement; re-measure")
    else:
        ok("labels.json hash matches the recorded measurement")

    # 3. Per-note scores are present, no errored note carries a score, and
    #    enough runs completed to have measured anything.
    per_run = results.get("per_run", [])
    if not per_run:
        bad("results.json holds no runs")
    for run in per_run:
        for n in run["notes"]:
            for key in ("precision", "recall", "traps_excluded", "traps_total"):
                if key not in n:
                    bad(f"run {run['run_index']} {n['note_id']}: missing {key}")
            if n.get("errored") and (n.get("traps_excluded") or n.get("precision") is not None):
                bad(
                    f"run {run['run_index']} {n['note_id']}: errored yet carries a score. "
                    "A fault must never be credited as a finding."
                )

    corpus = results.get("corpus", {})
    complete = corpus.get("runs_complete", 0)
    if complete < MIN_COMPLETE_RUNS:
        bad(
            f"only {complete} complete run(s); {MIN_COMPLETE_RUNS} are required. "
            "A run in which any note errored measures nothing."
        )
    else:
        ok(
            f"{complete} complete run(s) x {len(labels['notes'])} notes carry per-note "
            "precision, recall and exclusion counts"
        )
    if corpus.get("runs_incomplete"):
        ok(
            f"{corpus['runs_incomplete']} incomplete run(s) "
            f"{corpus['incomplete_run_indexes']} recorded and excluded from every rate"
        )

    # 4. Every produced span slices non-empty from the note on disk and still
    #    yields its quote. Re-sliced here rather than trusted from the
    #    recording; a span recorded as `exact` is held to exact equality.
    sliced = 0
    normalized = 0
    for run in per_run:
        for n in run["notes"]:
            text = texts[n["note_id"]]
            for span in n["spans"]:
                where = f"run {run['run_index']} {n['note_id']}"
                if span["char_start"] is None:
                    bad(f"{where}: a {span['kind']} was recorded with no anchored span")
                    continue
                fragment = text[span["char_start"] : span["char_end"]]
                mode = span.get("anchor_mode")
                if not fragment.strip():
                    bad(f"{where}: span [{span['char_start']}:{span['char_end']}] slices empty")
                elif collapse(fragment) != collapse(span["quote"]):
                    bad(
                        f"{where}: span [{span['char_start']}:{span['char_end']}] "
                        "no longer yields its quote"
                    )
                elif mode == "exact" and fragment != span["quote"]:
                    bad(
                        f"{where}: span [{span['char_start']}:{span['char_end']}] is recorded "
                        "as an exact anchor but only matches under normalization"
                    )
                elif mode not in ("exact", "normalized"):
                    bad(f"{where}: span carries no anchor mode; D18 requires one per span")
                else:
                    sliced += 1
                    normalized += mode == "normalized"
    if sliced:
        ok(
            f"{sliced} recorded spans re-sliced non-empty and still yield their quote "
            f"({normalized} anchored only under D18 whitespace normalization)"
        )

    # 5. The kill-criteria block is present and internally consistent with the
    #    measured corpus numbers — the recording carries its own verdict, and
    #    a hand-edited verdict that disagrees with its inputs fails here.
    kc = corpus.get("kill_criteria", {})
    if not kc:
        bad("corpus carries no kill_criteria block; the v1.5 thresholds must be measured")
    else:
        expectations = {
            "event_precision_measured": corpus.get("event_precision_mean"),
            "exclusion_recall_measured": corpus.get("exclusion_recall_mean"),
            "anchoring_drop_rate_measured": corpus.get("anchoring_drop_rate"),
        }
        for key, want in expectations.items():
            if kc.get(key) != want:
                bad(f"kill_criteria.{key} disagrees with the corpus figure it restates")
        floors = (
            ("precision_clears_floor", kc.get("event_precision_measured"), PRECISION_FLOOR, True),
            ("exclusion_clears_floor", kc.get("exclusion_recall_measured"), EXCLUSION_RECALL_FLOOR, True),
            ("anchoring_clears_ceiling", kc.get("anchoring_drop_rate_measured"), ANCHOR_DROP_CEILING, False),
        )
        for key, measured, bound, is_floor in floors:
            expected = measured is not None and (measured >= bound if is_floor else measured <= bound)
            if bool(kc.get(key)) != expected:
                bad(f"kill_criteria.{key} does not follow from its measured value and bound")
        if not failures:
            ok(
                "kill criteria measured: precision "
                f"{_fmt(kc.get('event_precision_measured'))} (floor {PRECISION_FLOOR}), "
                f"exclusion {_fmt(kc.get('exclusion_recall_measured'))} (floor {EXCLUSION_RECALL_FLOOR}), "
                f"anchor drop {_fmt(kc.get('anchoring_drop_rate_measured'))} (ceiling {ANCHOR_DROP_CEILING})"
            )

    # 6. docs/decisions.md holds a Spike 002 result entry quoting the measured
    #    precision at full precision alongside the model it was measured on.
    precision = results.get("corpus", {}).get("event_precision_mean")
    model = results.get("model", "")
    if precision is None:
        bad("no corpus event precision was measured")
    else:
        text = DECISIONS_PATH.read_text()
        headings = [
            m for m in re.finditer(r"^##\s+.*$", text, re.MULTILINE)
            if re.search(r"spike 002", m.group(), re.IGNORECASE)
            and re.search(r"result|measur|finding", m.group(), re.IGNORECASE)
        ]
        needle = f"{precision:.3f}"
        satisfied = None
        for h in headings:
            entry = text[h.end():]
            nxt = re.search(r"^## ", entry, re.MULTILINE)
            if nxt:
                entry = entry[: nxt.start()]
            if needle in entry and (not model or model in entry):
                satisfied = h.group().lstrip("# ").strip()
                break
        if satisfied:
            ok(f"result entry quotes precision {needle} on {model} — {satisfied}")
        elif not headings:
            bad(
                "docs/decisions.md holds no Spike 002 result entry. A heading "
                "naming the spike is not enough; it has to report the measurement."
            )
        else:
            bad(
                f"no Spike 002 entry quotes the measured precision {needle} "
                f"alongside the model it was measured on ({model}). "
                f"Entries searched: {len(headings)}"
            )

    for c in checks:
        print(f"ok    {c}")
    for f in failures:
        print(f"FAIL  {f}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} check(s) failed.", file=sys.stderr)
        return 1

    print(
        f"\nSpike 002 verified against {results['model']}, "
        f"{corpus['runs_complete']} complete run(s) of {corpus['runs_attempted']} attempted.\n"
        f"  event precision   {_fmt(corpus['event_precision_mean'])}   per run {corpus['event_precision_per_run']}\n"
        f"  event recall      {_fmt(corpus['event_recall_mean'])}   per run {corpus['event_recall_per_run']}\n"
        f"  exclusions        {corpus['exclusion_count_total']}/{corpus['exclusion_traps_total']} "
        f"(recall {_fmt(corpus['exclusion_recall_mean'])}, floor {EXCLUSION_RECALL_FLOOR})\n"
        f"  anchoring drop    {_fmt(corpus['anchoring_drop_rate'])} (ceiling {ANCHOR_DROP_CEILING})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="check the recorded artifacts and exit. Spends no model call.",
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="recompute the aggregates from recorded data. Spends no model call.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.verify:
        return verify()
    if args.rescore:
        return rescore()

    import logging

    logging.getLogger("google_adk").setLevel(logging.CRITICAL)
    logging.getLogger("google.adk").setLevel(logging.CRITICAL)

    load_env()
    if not os.environ.get("GOOGLE_API_KEY"):
        print(
            f"GOOGLE_API_KEY is unset and {ENV_PATH} did not supply one.",
            file=sys.stderr,
        )
        return 2

    print(f"Spike 002: {args.runs} run(s) over 5 notes against {args.model}, temperature 0.")
    results = asyncio.run(measure(args.model, args.runs))
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")

    c = results["corpus"]
    print(
        f"\nWrote {RESULTS_PATH.relative_to(REPO_ROOT)}\n"
        f"  event precision   {_fmt(c['event_precision_mean'])}   per run {c['event_precision_per_run']}\n"
        f"  event recall      {_fmt(c['event_recall_mean'])}   per run {c['event_recall_per_run']}\n"
        f"  exclusions        {c['exclusion_count_total']}/{c['exclusion_traps_total']} "
        f"(recall {_fmt(c['exclusion_recall_mean'])}, floor {EXCLUSION_RECALL_FLOOR})\n"
        f"  anchoring drop    {_fmt(c['anchoring_drop_rate'])} (ceiling {ANCHOR_DROP_CEILING})\n"
        f"  stable across runs {c['stable_on_all_notes']}\n"
        f"  {c['total_tokens']} tokens, {c['total_wall_seconds']}s wall\n"
        f"\nNow write the Spike 002 result entry in docs/decisions.md quoting the "
        f"precision, then run with --verify."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
