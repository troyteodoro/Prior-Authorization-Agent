"""The `wm_events` extraction agent — the one model leaf in the system (D45).

Article I is satisfied by construction: this is a declared leaf. It reads one
note and returns structured facts. It routes nothing, branches on nothing, and
decides no verdict — c1 through c5 are deterministic predicates over what
comes out of here (D2, Art. II).

The instruction and schema are the spike's, promoted essentially verbatim
because D19 measured event precision 1.000 and recall 1.000 on that exact
formulation with no tuning spent, and rewriting it would restart the prompt's
history at zero. **Two widenings:** REQ-38's per-field spans for diet
and activity, which the spike deferred here, and the BMI as a *value* with its
own span rather than a boolean — the contract stores `WmEvent.bmi`, and T-33's
reconciliation has nothing to compare against without it. So this is a new
measurement on a wider schema, not D19's re-run.

Spans are located by `pa_agent.anchor`, never taken from the model: D19 found
zero of eighty model-emitted offset pairs usable.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from pydantic import BaseModel, Field, ValidationError

from pa_agent.anchor import AnchoredSpan, anchor
from pa_agent.contracts import (
    CallMetrics,
    EvidenceSpan,
    ProgramAssertion,
    RunTrace,
    ToolCall,
    WmEvent,
)
from pa_agent.model_pin import PINNED_MODEL

EXTRACTION_TEMPERATURE = 0.0  # Art. II: the same note yields the same events

#: A version identifier for the whole extraction call configuration, recorded
#: on every trace and every recording (REQ-49). Two halves, because the
#: configuration has two: the instruction the first turn carries, unchanged
#: since T-15, and the verbatim re-ask T-89 added (D103). Bumped when either
#: changes — a measurement is only attributable while the configuration it
#: was measured on is identifiable (D20's argument about the model pin,
#: applied to the request; D45 and D64 on what counts as a changed request).
PROMPT_VERSION = "t15-instruction-v1/t89-reask-v1"

#: How many times a note's unanchorable quotes are re-asked for their verbatim
#: text (REQ-56, D103). One. A second answer is verbatim or it is not, and a
#: third ask with the same note and the same quotes is a loop; the first turn
#: already had the instruction to copy character for character. This is a
#: different axis from `pa_agent.workflow.DEFAULT_MAX_ATTEMPTS`, which retries
#: *failures* — this retries a partial success. Zero turns the mechanism off
#: without removing it, which is D103's reversal condition.
REASK_ROUNDS = 1


# --------------------------------------------------------------------------
# The response schema. REQ-8's shape, plus REQ-38's per-field spans.
# --------------------------------------------------------------------------


class ExtractedEvent(BaseModel):
    date: str = Field(description="Encounter date, normalized to YYYY-MM-DD.")
    quote: str = Field(
        description="Text copied verbatim and contiguously from the note."
    )
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")
    bmi: float | None = Field(
        default=None,
        description="The BMI value recorded at this encounter, or null if none is.",
    )
    bmi_quote: str = Field(
        default="",
        description="If bmi is present, the verbatim phrase recording it. Else empty.",
    )
    diet_documented: bool = False
    activity_documented: bool = False
    diet_quote: str = Field(
        default="",
        description="If diet_documented, the verbatim phrase documenting it. Else empty.",
    )
    activity_quote: str = Field(
        default="",
        description="If activity_documented, the verbatim phrase documenting it. Else empty.",
    )


class ExtractedAssertion(BaseModel):
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int
    char_end: int
    claim: str = Field(description="What the passage claims, in a few words.")


class Extraction(BaseModel):
    wm_events: list[ExtractedEvent] = Field(default_factory=list)
    program_assertions: list[ExtractedAssertion] = Field(default_factory=list)
    # T-60: the note's own current BMI, which belongs to no encounter. A
    # different fact from `ExtractedEvent.bmi` — criterion (a) asks what the
    # patient's BMI is now, c4 asks what each month of a run documented (D50).
    current_bmi: float | None = Field(
        default=None,
        description=(
            "The patient's current BMI as stated in this note outside any "
            "listed encounter, or null if the note states none."
        ),
    )
    current_bmi_quote: str = Field(
        default="",
        description=(
            "If current_bmi is present, the verbatim phrase recording it. "
            "Else empty."
        ),
    )


class VerbatimQuote(BaseModel):
    """One re-ask answer: the verbatim passage for a path the runner listed."""

    path: str = Field(description="The path the quote was listed under, copied exactly.")
    verbatim: str = Field(
        description=(
            "The passage exactly as it appears in the note, copied character "
            "for character, or an empty string if the note has no such passage."
        )
    )


class VerbatimAnswers(BaseModel):
    """The re-ask's response schema (REQ-56, D103). `quotes` is required: a
    payload of another shape arriving here is a schema failure, recorded as
    one, never read as "no answers"."""

    quotes: list[VerbatimQuote]


# The spike's instruction, unchanged except for the two REQ-38 quote fields.
INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review. Return only what the schema defines. Do not explain.

A wm_event is one documented, physician-supervised weight management ENCOUNTER
that actually took place. For each one return:

  date         the encounter date, normalized to YYYY-MM-DD
  quote        text copied verbatim and contiguously from the note, long enough
               to show both the date and that an encounter occurred. It must
               appear in the note character for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character
  bmi                   the BMI value recorded at that encounter, as a
                        number. Null if the encounter records no BMI. A weight
                        with no BMI value is null; never compute one.
  bmi_quote             when bmi is present, the verbatim phrase from the note
                        that records it. Empty otherwise.
  diet_documented       true only if dietary counseling, a meal plan, or a food
                        diary is addressed at that encounter.
  activity_documented   true only if physical activity or exercise is addressed
                        at that encounter.
  diet_quote            when diet_documented is true, the verbatim phrase from
                        the note that documents it. Empty otherwise.
  activity_quote        when activity_documented is true, the verbatim phrase
                        from the note that documents it. Empty otherwise.

Never return a wm_event for any of the following. None of them is an encounter:

  - a missed, cancelled, no-show, or rescheduled appointment
  - an unsuccessful contact attempt: an unanswered call, a voicemail, a letter
  - a weight loss attempt that was not physician supervised, including
    commercial programs and self-directed diets
  - a date in an unrelated section: family history, immunizations, past
    surgical history, health maintenance, referrals, or administrative and
    insurance dates

A program_assertion is a passage claiming participation in or completion of a
weight management program without documenting the underlying encounters. Record
it with the same quote and offset fields. An assertion is never also a
wm_event: if the note documents the encounters themselves, those are wm_events.

Return every qualifying encounter in the note, including encounters from
programs that are old, discontinued, or appear irrelevant to the current
request. Do not filter by date, by recency, or by which program looks most
relevant. Choosing among them happens elsewhere.

Separately from the encounters, the note may state the patient's CURRENT BMI
outside any listed encounter — a measurement taken at this visit, or a figure
given as the patient's present status. Return it as:

  current_bmi        that BMI value, as a number. Null if the note states no
                     such value. Never compute one, never carry one over from
                     an encounter, and never use a target or goal weight.
  current_bmi_quote  when current_bmi is present, the verbatim phrase from the
                     note that records it. Empty otherwise.

A BMI belonging to a listed encounter goes on that wm_event and not here. A
note may have both, one, or neither.
"""

# T-89's second turn (REQ-56, D103). Sent only when the first turn returned a
# quote Python could not locate, with the note and the failed quotes. It asks
# for text and nothing else: no offsets (D17, D19), no dates, no judgment.
REASK_INSTRUCTION = """\
You are correcting citations from a clinical note for a prior authorization
review. Earlier, quotes were copied from this note, and the ones listed below
do not appear in it character for character.

For each listed quote, find the passage in the note it was taken from and
return that passage exactly as it appears in the note: the same words, the
same spelling, the same punctuation, copied contiguously. Do not paraphrase,
shorten, summarize, or correct it. Return each answer under the path it was
listed with.

If the note contains no passage the quote could have been taken from, return
an empty string for that path. Return nothing for paths that were not listed.
Return only what the schema defines. Do not explain.
"""

#: The heading under which the failed quotes are listed in the re-ask message.
REASK_QUOTES_HEADING = "QUOTES THAT DO NOT APPEAR IN THE NOTE:"


def format_targets(targets: list[dict]) -> str:
    """The failed quotes, listed for the model under their paths.

    One rendering shared by both runners, so the two re-asks differ only by
    how the note reaches the model — inline in the message, or through
    `read_note` — and not by what they are asked.
    """
    lines = [REASK_QUOTES_HEADING]
    for target in targets:
        lines.append(f"- path: {target['path']}")
        lines.append(f"  quote: {json.dumps(target['quote'], ensure_ascii=False)}")
    return "\n".join(lines)


def reask_contents(text: str, targets: list[dict]) -> str:
    """The direct runner's whole second request: instruction, note, quotes."""
    return f"{REASK_INSTRUCTION}\n\nNOTE:\n{text}\n\n{format_targets(targets)}"


# --------------------------------------------------------------------------
# Result
# --------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """What one note yielded, with the anchoring evidence kept alongside.

    `dropped` counts facts the model returned that Python could not anchor.
    They are never silently discarded: an unanchorable quote is a fabricated
    or mangled citation, which is the failure Article III exists to catch, and
    a count of zero is a claim worth being able to check.
    """

    document_id: str
    events: list[WmEvent] = field(default_factory=list)
    assertions: list[ProgramAssertion] = field(default_factory=list)
    # T-60: the note's current BMI and where it is stated. `None` when the note
    # states none, and also when it stated one Python could not anchor — D15's
    # rule, applied here: a BMI nobody can cite is not a documented BMI.
    current_bmi: float | None = None
    current_bmi_span: EvidenceSpan | None = None
    anchored_spans: list[AnchoredSpan] = field(default_factory=list)
    # `{"reason", "quote", "path"}` per drop; `path` names the payload field
    # the quote came from (`wm_events[2].bmi_quote`), which is how T-89's
    # re-ask knows what to ask about. A drop that is not a quote — an
    # unparseable date — carries no path and is never re-asked.
    dropped: list[dict] = field(default_factory=list)
    # Turn one's measurement. Every turn is on `trace.metrics` (D71, D103).
    metrics: CallMetrics | None = None
    # The payload this result was built from. After a re-ask that is the
    # *patched* payload, so replaying `raw` through `build_result` reproduces
    # the result exactly; the model's first answer is `raw_first_turn`.
    raw: dict | None = None
    raw_first_turn: dict | None = None
    # T-89 (REQ-56, D103): what was re-asked and what came of it — `targets`,
    # `answers`, `recovered`, `unrecovered`, `error` — or `None` when the first
    # turn left nothing to ask about.
    reask: dict | None = None
    # T-62: how the runner reached this result — tool-call sequence, attempts,
    # termination reason (REQ-49). Optional because a replay of a recorded
    # payload made no calls to trace, and `None` says so rather than an empty
    # trace implying a run that recorded nothing.
    trace: RunTrace | None = None


def _anchor_or_drop(
    document_id: str, text: str, quote: str, m_start: int, m_end: int,
    dropped: list[dict], reason: str, spans: list[AnchoredSpan],
    prefer_near: tuple[int, int] | None = None, *, path: str,
) -> AnchoredSpan | None:
    located = anchor(document_id, text, quote, m_start, m_end, prefer_near)
    spans.append(located)
    if not located.anchored:
        # The quote is truncated for the record; the re-ask reads the full one
        # back out of the payload at `path` (T-89).
        dropped.append({"reason": reason, "quote": quote[:120], "path": path})
        return None
    return located


def build_result(
    document_id: str, text: str, payload: dict, metrics: CallMetrics | None = None
) -> ExtractionResult:
    """Turn a model payload into contract objects, anchoring every quote.

    Pure and model-free, so the anchoring half is testable without a call —
    which is what lets `pytest` verify a recording rather than make one (D45).
    """
    extraction = Extraction.model_validate(payload)
    result = ExtractionResult(document_id=document_id, metrics=metrics, raw=payload)

    for index, raw_event in enumerate(extraction.wm_events):
        at = f"wm_events[{index}]"
        located = _anchor_or_drop(
            document_id, text, raw_event.quote, raw_event.char_start,
            raw_event.char_end, result.dropped, "event_quote_unanchorable",
            result.anchored_spans, path=f"{at}.quote",
        )
        if located is None:
            continue
        try:
            event_date = date.fromisoformat(raw_event.date)
        except ValueError:
            result.dropped.append({"reason": "unparseable_date", "quote": raw_event.date})
            continue

        # REQ-38: a flag is asserted only when its own span anchors. The
        # contract refuses the flag without the span, so a phrase Python could
        # not locate demotes the flag rather than failing the note.
        # D46: a per-field span documents *this* encounter, so a repeated
        # phrase resolves to the occurrence nearest the event, not the first
        # one in the document.
        near = (located.char_start, located.char_end)
        bmi_span = diet_span = activity_span = None
        bmi = raw_event.bmi
        diet = raw_event.diet_documented
        activity = raw_event.activity_documented
        if bmi is not None:
            located_bmi = _anchor_or_drop(
                document_id, text, raw_event.bmi_quote, -1, -1, result.dropped,
                "bmi_quote_unanchorable", result.anchored_spans, near,
                path=f"{at}.bmi_quote",
            )
            if located_bmi is None:
                # D15: a BMI nobody can cite is not a documented BMI. Dropping
                # the value rather than the event keeps c4 honest and c3 intact.
                bmi = None
            else:
                bmi_span = located_bmi.to_span()
        if diet:
            located_diet = _anchor_or_drop(
                document_id, text, raw_event.diet_quote, -1, -1, result.dropped,
                "diet_quote_unanchorable", result.anchored_spans, near,
                path=f"{at}.diet_quote",
            )
            if located_diet is None:
                diet = False
            else:
                diet_span = located_diet.to_span()
        if activity:
            located_activity = _anchor_or_drop(
                document_id, text, raw_event.activity_quote, -1, -1, result.dropped,
                "activity_quote_unanchorable", result.anchored_spans, near,
                path=f"{at}.activity_quote",
            )
            if located_activity is None:
                activity = False
            else:
                activity_span = located_activity.to_span()

        result.events.append(
            WmEvent(
                event_date=event_date,
                span=located.to_span(),
                bmi=bmi,
                bmi_span=bmi_span,
                diet_documented=diet,
                diet_span=diet_span,
                activity_documented=activity,
                activity_span=activity_span,
            )
        )

    # T-60: the note-level BMI, anchored like any other claim. No `prefer_near`
    # — it belongs to no encounter, which is the whole point of the field.
    if extraction.current_bmi is not None:
        located_current = _anchor_or_drop(
            document_id, text, extraction.current_bmi_quote, -1, -1,
            result.dropped, "current_bmi_quote_unanchorable",
            result.anchored_spans, path="current_bmi_quote",
        )
        if located_current is not None:
            result.current_bmi = extraction.current_bmi
            result.current_bmi_span = located_current.to_span()

    for index, raw_assertion in enumerate(extraction.program_assertions):
        located = _anchor_or_drop(
            document_id, text, raw_assertion.quote, raw_assertion.char_start,
            raw_assertion.char_end, result.dropped, "assertion_quote_unanchorable",
            result.anchored_spans, path=f"program_assertions[{index}].quote",
        )
        if located is None:
            continue
        result.assertions.append(
            ProgramAssertion(span=located.to_span(), text=raw_assertion.claim)
        )

    return result


# --------------------------------------------------------------------------
# The verbatim re-ask (T-89, REQ-56, D103). Model-free: what to ask, how to
# apply an answer, and the fixed two-step that every live runner walks.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    """What one model turn produced, as a runner reports it.

    `payload` is the parsed answer or `None`; `metrics` and `tool_calls` are
    everything the turn spent (a tool round trip is two entries); `error` is a
    classified `"REASON: detail"` string when the turn failed. A first turn
    raises instead — the contract is `first_turn` raises, `reask_turn` never
    does, and `Turn.error` is how the second reports.
    """

    payload: dict | None
    metrics: list[CallMetrics] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    termination: str = "ok"
    error: str | None = None


_PATH = re.compile(r"^(wm_events|program_assertions)\[(\d+)\]\.(\w+)$|^(current_bmi_quote)$")

#: The payload fields a re-ask may write. Anything else — a date, a flag, a
#: BMI value, a claim — is not a quote and stays as the first turn left it.
_QUOTE_FIELDS = frozenset({"quote", "bmi_quote", "diet_quote", "activity_quote"})


def _locate(payload: dict, path: str) -> tuple[dict, str]:
    """The container and key a path names, or `KeyError` for a path that names
    nothing in this payload — a model-invented path is never applied."""
    match = _PATH.match(path)
    if match is None:
        raise KeyError(path)
    if match.group(4):
        return payload, "current_bmi_quote"
    collection, index, fieldname = match.group(1), int(match.group(2)), match.group(3)
    if fieldname not in _QUOTE_FIELDS:
        raise KeyError(path)
    items = payload.get(collection) or []
    if index >= len(items):
        raise KeyError(path)
    return items[index], fieldname


def reask_targets(result: ExtractionResult) -> list[dict]:
    """The quotes to re-ask about: every drop that is a quote Python could not
    locate, with the full quote read back out of the payload.

    Only `*_quote_unanchorable` reasons qualify. An unparseable date is not a
    citation problem and no verbatim text would repair it.
    """
    if result.raw is None:
        return []
    targets = []
    for drop in result.dropped:
        path = drop.get("path")
        if not path or not str(drop.get("reason", "")).endswith("_quote_unanchorable"):
            continue
        container, key = _locate(result.raw, path)
        targets.append({"path": path, "reason": drop["reason"], "quote": container[key]})
    return targets


def apply_verbatim(payload: dict, targets: list[dict], answers: VerbatimAnswers) -> dict:
    """The first turn's payload with the answered quotes replaced — at the
    targeted paths and nowhere else.

    A copy, so `raw_first_turn` stays what the model first said. An answer for
    a path that was not asked about is ignored; so is a blank one, because an
    empty quote would anchor at offset zero and cite nothing. Nothing here can
    add, remove or re-date an event or an assertion: the only writes are to
    quote fields the first turn already had.
    """
    patched = copy.deepcopy(payload)
    wanted = {target["path"] for target in targets}
    for answer in answers.quotes:
        if answer.path not in wanted or not answer.verbatim.strip():
            continue
        container, key = _locate(patched, answer.path)
        container[key] = answer.verbatim
    return patched


def extract_with_reask(
    document_id: str,
    text: str,
    first_turn: Callable[[], Turn],
    reask_turn: Callable[[list[dict]], Turn],
    *,
    runner_name: str,
    model: str | None,
    step_names: tuple[str, str] = ("extract", "reask"),
) -> ExtractionResult:
    """The fixed two-step every live runner walks (REQ-56, D103).

    Turn one, then `build_result`; if Python could not locate a quote, one
    re-ask — `REASK_ROUNDS` of them — for the verbatim text, the answer
    patched in at the paths asked about, and `build_result` again. The
    decision to re-ask is a Python predicate over string search, never model
    output (Art. I), and the anchorer admits the new quote or drops it
    exactly as it did the old one (Art. III).

    A failed re-ask never raises: the first turn's result stands, `reask`
    carries the classified reason and the trace names it. Every turn's
    metrics land on the trace (Art. X, D71); `result.metrics` stays turn one.
    """
    first = first_turn()
    assert first.payload is not None, "a first turn raises rather than returning nothing"
    metrics = list(first.metrics)
    tool_calls = list(first.tool_calls)
    steps = [step_names[0]]
    terminations = [first.termination]

    result = build_result(document_id, text, first.payload, metrics[0] if metrics else None)
    targets = reask_targets(result)
    if targets:
        first_payload = first.payload
        reask: dict = {
            "targets": targets, "answers": [], "recovered": [],
            "unrecovered": [t["path"] for t in targets], "error": None,
        }
        for _ in range(REASK_ROUNDS):
            second = reask_turn(targets)
            metrics.extend(second.metrics)
            tool_calls.extend(second.tool_calls)
            steps.append(step_names[1])
            error = second.error
            answers: VerbatimAnswers | None = None
            if error is None and second.payload is None:
                error = "NO_PAYLOAD: the re-ask produced no structured output"
            if error is None:
                try:
                    answers = VerbatimAnswers.model_validate(second.payload)
                except ValidationError as exc:
                    error = f"SCHEMA_INVALID: {exc.error_count()} error(s): {exc.errors()[0]['msg']}"
            if answers is not None:
                reask["answers"] = [a.model_dump() for a in answers.quotes]
                patched = apply_verbatim(first_payload, targets, answers)
                result = build_result(document_id, text, patched, metrics[0] if metrics else None)
                still = {drop.get("path") for drop in result.dropped}
                reask["recovered"] = [t["path"] for t in targets if t["path"] not in still]
                reask["unrecovered"] = [t["path"] for t in targets if t["path"] in still]
            reask["error"] = error
            terminations.append(
                f"reask {second.termination}" + (f" [{error.split(':', 1)[0]}]" if error else "")
            )
        result.raw_first_turn = first_payload
        result.reask = reask

    result.metrics = metrics[0] if metrics else None
    result.trace = RunTrace(
        runner_name=runner_name,
        model=metrics[0].model if metrics else model,
        prompt_version=PROMPT_VERSION,
        document_id=document_id,
        steps=steps,
        tool_calls=tool_calls,
        attempts=1,
        termination_reason="; ".join(terminations),
        metrics=metrics,
    )
    return result


# --------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------


def _generate(client, model: str, contents: str, schema, purpose: str) -> tuple[str, CallMetrics]:
    """One `generate_content`, measured (Art. X). Raises whatever the SDK
    raises; the caller classifies."""
    started = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_schema": schema,
            "temperature": EXTRACTION_TEMPERATURE,
        },
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    usage = getattr(response, "usage_metadata", None)
    metrics = CallMetrics(
        model=model,
        purpose=purpose,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        wall_time_ms=elapsed_ms,
    )
    return response.text, metrics


def extract(document_id: str, text: str, client, model: str = PINNED_MODEL) -> ExtractionResult:
    """One note in, structured facts out. At most `1 + REASK_ROUNDS` model
    calls (Art. X): the extraction, and one re-ask only when a quote could
    not be located (T-89, D103).

    `client` is injected rather than constructed here so nothing in this module
    reads a credential or names a tier — D5 keeps AI Studio and Vertex as two
    credentials behind one pinned identifier, and the caller chooses.
    """

    def first_turn() -> Turn:
        response_text, metrics = _generate(
            client, model, f"{INSTRUCTION}\n\nNOTE:\n{text}", Extraction, "extraction"
        )
        return Turn(
            payload=json.loads(response_text),
            metrics=[metrics],
            termination=f"ok ({metrics.wall_time_ms:.0f}ms)",
        )

    def reask_turn(targets: list[dict]) -> Turn:
        try:
            response_text, metrics = _generate(
                client, model, reask_contents(text, targets), VerbatimAnswers,
                "extraction_reask",
            )
        except Exception as exc:  # recorded classified, never raised (D103)
            return Turn(
                payload=None,
                termination=type(exc).__name__,
                error=f"CALL_FAILED: {type(exc).__name__}: {exc}",
            )
        try:
            payload = json.loads(response_text)
        except (json.JSONDecodeError, TypeError) as exc:
            return Turn(
                payload=None,
                metrics=[metrics],
                termination=f"unparseable ({metrics.wall_time_ms:.0f}ms)",
                error=f"UNPARSEABLE: {exc}",
            )
        if not isinstance(payload, dict):
            return Turn(
                payload=None,
                metrics=[metrics],
                termination=f"schema_invalid ({metrics.wall_time_ms:.0f}ms)",
                error=f"SCHEMA_INVALID: response is {type(payload).__name__}, not an object",
            )
        return Turn(
            payload=payload,
            metrics=[metrics],
            termination=f"ok ({metrics.wall_time_ms:.0f}ms)",
        )

    return extract_with_reask(
        document_id, text, first_turn, reask_turn, runner_name="direct", model=model
    )
