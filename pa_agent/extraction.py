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

import json
import time
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel, Field

from pa_agent.anchor import AnchoredSpan, anchor
from pa_agent.contracts import (
    CallMetrics,
    EvidenceSpan,
    ProgramAssertion,
    RunTrace,
    WmEvent,
)
from pa_agent.model_pin import PINNED_MODEL

EXTRACTION_TEMPERATURE = 0.0  # Art. II: the same note yields the same events


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
    dropped: list[dict] = field(default_factory=list)
    metrics: CallMetrics | None = None
    raw: dict | None = None
    # T-62: how the runner reached this result — tool-call sequence, attempts,
    # termination reason (REQ-49). Optional because a replay of a recorded
    # payload made no calls to trace, and `None` says so rather than an empty
    # trace implying a run that recorded nothing.
    trace: RunTrace | None = None


def _anchor_or_drop(
    document_id: str, text: str, quote: str, m_start: int, m_end: int,
    dropped: list[dict], reason: str, spans: list[AnchoredSpan],
    prefer_near: tuple[int, int] | None = None,
) -> AnchoredSpan | None:
    located = anchor(document_id, text, quote, m_start, m_end, prefer_near)
    spans.append(located)
    if not located.anchored:
        dropped.append({"reason": reason, "quote": quote[:120]})
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

    for raw_event in extraction.wm_events:
        located = _anchor_or_drop(
            document_id, text, raw_event.quote, raw_event.char_start,
            raw_event.char_end, result.dropped, "event_quote_unanchorable",
            result.anchored_spans,
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
            )
            if located_diet is None:
                diet = False
            else:
                diet_span = located_diet.to_span()
        if activity:
            located_activity = _anchor_or_drop(
                document_id, text, raw_event.activity_quote, -1, -1, result.dropped,
                "activity_quote_unanchorable", result.anchored_spans, near,
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
            result.anchored_spans,
        )
        if located_current is not None:
            result.current_bmi = extraction.current_bmi
            result.current_bmi_span = located_current.to_span()

    for raw_assertion in extraction.program_assertions:
        located = _anchor_or_drop(
            document_id, text, raw_assertion.quote, raw_assertion.char_start,
            raw_assertion.char_end, result.dropped, "assertion_quote_unanchorable",
            result.anchored_spans,
        )
        if located is None:
            continue
        result.assertions.append(
            ProgramAssertion(span=located.to_span(), text=raw_assertion.claim)
        )

    return result


# --------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------


def extract(document_id: str, text: str, client, model: str = PINNED_MODEL) -> ExtractionResult:
    """One note in, structured facts out. Exactly one model call (Art. X).

    `client` is injected rather than constructed here so nothing in this module
    reads a credential or names a tier — D5 keeps AI Studio and Vertex as two
    credentials behind one pinned identifier, and the caller chooses.
    """
    started = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents=f"{INSTRUCTION}\n\nNOTE:\n{text}",
        config={
            "response_mime_type": "application/json",
            "response_schema": Extraction,
            "temperature": EXTRACTION_TEMPERATURE,
        },
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    usage = getattr(response, "usage_metadata", None)
    metrics = CallMetrics(
        model=model,
        purpose="extraction",
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        wall_time_ms=elapsed_ms,
    )
    payload = json.loads(response.text)
    return build_result(document_id, text, payload, metrics)
