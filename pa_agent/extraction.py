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
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, Field, ValidationError

from pa_agent.anchor import AnchoredSpan, anchor
from pa_agent.contracts import (
    CallMetrics,
    ClinicalEvaluation,
    ConservativeTherapy,
    ConservativeTherapyCategory,
    DiseaseActivityAssessment,
    DiseaseActivityLevel,
    DocumentedFinding,
    DocumentedWeight,
    EvaluationComponent,
    EvaluationComponentCategory,
    EvidenceSpan,
    FactKind,
    HeartFailureAssessment,
    HeartFailureClass,
    KneeRadiograph,
    KneeSymptom,
    KneeSymptomCategory,
    ProgramAssertion,
    RadiographicFinding,
    RadiographicFindingCategory,
    RunTrace,
    SleepFindingCategory,
    SleepTest,
    ToolCall,
    TuberculosisScreen,
    TuberculosisScreenResult,
    TuberculosisTreatment,
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
    #: The fact kind whose builder produced this result (T-108, D150). `None`
    #: only on a result built by hand; both registered builders set it.
    kind: FactKind | None = None
    #: The generic slot (REQ-79): every kind but `weight_management` returns its
    #: facts here, as contract objects, and leaves the `WmEvent`-shaped fields
    #: below empty. `weight_management` keeps its typed fields unchanged, so
    #: no recording, span or verdict built from them moved (D150).
    facts: list = field(default_factory=list)
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
    result = ExtractionResult(
        document_id=document_id, kind=FactKind.WEIGHT_MANAGEMENT,
        metrics=metrics, raw=payload,
    )

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


class ReaskResult(Protocol):
    """What the re-ask core reads and writes on a result, whatever built it.

    `ExtractionResult` is the first shape and `quotes.QuoteResult` the second
    (T-98, D122). The core touches these fields and nothing else, which is
    why one core serves two payload shapes without knowing either.
    """

    dropped: list[dict]
    metrics: CallMetrics | None
    raw: dict | None
    raw_first_turn: dict | None
    reask: dict | None
    trace: RunTrace | None


ResultT = TypeVar("ResultT", bound=ReaskResult)

#: A locator: `(payload, path) -> (container, key)`, raising `KeyError` for a
#: path that names nothing in that payload shape. `_locate` is the
#: extraction one; `quotes._locate_quote` is the second (D122).
Locator = Callable[[dict, str], tuple[dict, str]]


def _resolve_reask_defaults(
    build: Callable[..., Any] | None,
    locate: Locator | None,
    prompt_version: str | None,
) -> tuple[Callable[..., Any], Locator, str]:
    """The extraction objects, bound at call time (D122).

    Resolved here rather than in the signature so a test can pin what the
    core does when a caller passes nothing. Since T-107 `extract()` and
    `AdkExtractionRunner.run()` pass `FACT_SCHEMAS[kind]`'s three, which for
    `weight_management` are these same objects (D149), so the proof that
    T-98's generalisation moved no extraction recording still rests on these
    defaults being the extraction ones.
    """
    return (
        build_result if build is None else build,
        _locate if locate is None else locate,
        PROMPT_VERSION if prompt_version is None else prompt_version,
    )


def reask_targets(result: ReaskResult, locate: Locator | None = None) -> list[dict]:
    """The quotes to re-ask about: every drop that is a quote Python could not
    locate, with the full quote read back out of the payload.

    Only `*_quote_unanchorable` reasons qualify. An unparseable date is not a
    citation problem and no verbatim text would repair it. `locate` reads the
    payload shape the result was built from; it defaults to the extraction
    locator (D122).
    """
    if result.raw is None:
        return []
    _, locate, _ = _resolve_reask_defaults(None, locate, None)
    targets = []
    for drop in result.dropped:
        path = drop.get("path")
        if not path or not str(drop.get("reason", "")).endswith("_quote_unanchorable"):
            continue
        container, key = locate(result.raw, path)
        targets.append({"path": path, "reason": drop["reason"], "quote": container[key]})
    return targets


def apply_verbatim(
    payload: dict,
    targets: list[dict],
    answers: VerbatimAnswers,
    locate: Locator | None = None,
) -> dict:
    """The first turn's payload with the answered quotes replaced — at the
    targeted paths and nowhere else.

    A copy, so `raw_first_turn` stays what the model first said. An answer for
    a path that was not asked about is ignored; so is a blank one, because an
    empty quote would anchor at offset zero and cite nothing. Nothing here can
    add, remove or re-date an event or an assertion: the only writes are to
    quote fields the first turn already had. `locate` is the payload shape's
    locator and defaults to the extraction one (D122); whichever it is, it can
    only name a quote field the first turn already had, which is what keeps
    this the one place a model answer is written into a payload.
    """
    _, locate, _ = _resolve_reask_defaults(None, locate, None)
    patched = copy.deepcopy(payload)
    wanted = {target["path"] for target in targets}
    for answer in answers.quotes:
        if answer.path not in wanted or not answer.verbatim.strip():
            continue
        container, key = locate(patched, answer.path)
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
    build: Callable[..., ResultT] | None = None,
    locate: Locator | None = None,
    prompt_version: str | None = None,
) -> ResultT:
    """The fixed two-step every live runner walks (REQ-56, D103).

    Turn one, then `build`; if Python could not locate a quote, one re-ask —
    `REASK_ROUNDS` of them — for the verbatim text, the answer patched in at
    the paths asked about, and `build` again. The decision to re-ask is a
    Python predicate over string search, never model output (Art. I), and the
    anchorer admits the new quote or drops it exactly as it did the old one
    (Art. III).

    A failed re-ask never raises: the first turn's result stands, `reask`
    carries the classified reason and the trace names it. Every turn's
    metrics land on the trace (Art. X, D71); `result.metrics` stays turn one.

    `build`, `locate` and `prompt_version` name the payload shape being
    walked (T-98, D122). Left unset they resolve to `build_result`, `_locate`
    and `PROMPT_VERSION` at call time — the extraction configuration, which
    is what both extraction runners pass by passing nothing. The quote turn
    passes `quotes.build_quote_result`, `quotes._locate_quote` and
    `quotes.QUOTE_PROMPT_VERSION`; nothing else about the walk differs.
    """
    build, locate, version = _resolve_reask_defaults(build, locate, prompt_version)
    first = first_turn()
    assert first.payload is not None, "a first turn raises rather than returning nothing"
    metrics = list(first.metrics)
    tool_calls = list(first.tool_calls)
    steps = [step_names[0]]
    terminations = [first.termination]

    result = build(document_id, text, first.payload, metrics[0] if metrics else None)
    targets = reask_targets(result, locate)
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
                patched = apply_verbatim(first_payload, targets, answers, locate)
                result = build(document_id, text, patched, metrics[0] if metrics else None)
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
        prompt_version=version,
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


def direct_first_turn(client, model: str, contents: str, schema, purpose: str) -> Turn:
    """The direct runner's first turn: one measured call, the answer parsed.

    Raises whatever the SDK or the parser raises — a first turn that produced
    nothing has nothing to build a result from, and the runner classifies
    (`DirectExtractionRunner.run`). Shared with the quote turn since T-98
    (D122): the two direct leaves differ only in what they ask.
    """
    response_text, metrics = _generate(client, model, contents, schema, purpose)
    return Turn(
        payload=json.loads(response_text),
        metrics=[metrics],
        termination=f"ok ({metrics.wall_time_ms:.0f}ms)",
    )


def direct_reask_turn(client, model: str, text: str, targets: list[dict], purpose: str) -> Turn:
    """The direct runner's re-ask turn. Never raises: a failure is returned
    classified on `Turn.error` and the first turn's result stands (D103).
    Shared with the quote turn since T-98 (D122)."""
    try:
        response_text, metrics = _generate(
            client, model, reask_contents(text, targets), VerbatimAnswers, purpose,
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


def extract(
    document_id: str, text: str, client, model: str = PINNED_MODEL, *, kind: FactKind
) -> ExtractionResult:
    """One note in, the facts of one declared `kind` out. At most
    `1 + REASK_ROUNDS` model calls (Art. X): the extraction, and one re-ask
    only when a quote could not be located (T-89, D103).

    `kind` is required and keyword-only (T-107, D149): which facts to ask for
    is the request, and a default would be a tree's declaration made silently
    on its behalf.

    `client` is injected rather than constructed here so nothing in this module
    reads a credential or names a tier — D5 keeps AI Studio and Vertex as two
    credentials behind one pinned identifier, and the caller chooses.
    """
    schema = FACT_SCHEMAS[kind]
    return extract_with_reask(
        document_id,
        text,
        first_turn=lambda: direct_first_turn(
            client, model, f"{schema.instruction}\n\nNOTE:\n{text}",
            schema.response_model, "extraction",
        ),
        reask_turn=lambda targets: direct_reask_turn(
            client, model, text, targets, "extraction_reask"
        ),
        runner_name="direct",
        model=model,
        build=schema.build,
        locate=schema.locate,
        prompt_version=schema.prompt_version,
    )


# --------------------------------------------------------------------------
# The second fact kind: the sleep apnea workup (T-108, REQ-79, D150)
#
# Its own response model, instruction, builder, locator and prompt version.
# The builder anchors through `_anchor_or_drop` and the live runners walk
# `extract_with_reask` with this locator, so the trust boundary and the re-ask
# are the ones every extraction already passes through; nothing here
# constructs a `WmEvent`, and `tests/test_fact_kinds.py` parses that.
# --------------------------------------------------------------------------

#: The sleep kind's configuration: its instruction, and T-89's re-ask unchanged.
SLEEP_PROMPT_VERSION = "t108-sleep-workup-v1/t89-reask-v1"


class ExtractedEvaluation(BaseModel):
    date: str = Field(description="Evaluation date, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedSleepTest(BaseModel):
    date: str = Field(description="The date the study was performed, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")
    index: float | None = Field(
        default=None,
        description=(
            "The AHI or RDI the study measured without positive airway "
            "pressure, in events per hour, or null if none is stated."
        ),
    )
    index_quote: str = Field(
        default="",
        description="If index is present, the verbatim phrase stating it. Else empty.",
    )
    recording_hours: float | None = Field(
        default=None,
        description=(
            "The total recording or sleep time stated for the study, in hours, "
            "or null if none is stated in hours."
        ),
    )
    recording_hours_quote: str = Field(
        default="",
        description="If recording_hours is present, the verbatim phrase stating it. Else empty.",
    )


class ExtractedFinding(BaseModel):
    category: SleepFindingCategory = Field(
        description="Which of the listed symptoms or conditions the passage documents as present."
    )
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class SleepApneaWorkupExtraction(BaseModel):
    clinical_evaluations: list[ExtractedEvaluation] = Field(default_factory=list)
    sleep_tests: list[ExtractedSleepTest] = Field(default_factory=list)
    findings: list[ExtractedFinding] = Field(default_factory=list)


SLEEP_INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review of a positive airway pressure device. Return only what the schema
defines. Do not explain.

A clinical_evaluation is one IN-PERSON clinical evaluation of the patient for
sleep apnea that actually took place. For each one return:

  date         the evaluation date, normalized to YYYY-MM-DD
  quote        text copied verbatim and contiguously from the note, long enough
               to show both the date and that an in-person evaluation took
               place. It must appear in the note character for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character

Never return a clinical_evaluation for a telephone call, a video or portal
message, a scheduling contact, a referral, or an appointment that did not take
place.

A sleep_test is one DIAGNOSTIC sleep study that was performed: an attended
polysomnogram or a home sleep test. For each one return:

  date                    the date the study was performed, normalized to
                          YYYY-MM-DD
  quote                   text copied verbatim and contiguously from the note,
                          showing the study and its date
  char_start, char_end    as above
  index                   the apnea-hypopnea index (AHI) or respiratory
                          disturbance index (RDI) the study measured without
                          positive airway pressure, in events per hour, as a
                          number. Null if the note states none. Never compute
                          one, and never report an index measured on CPAP or
                          during a titration.
  index_quote             when index is present, the verbatim phrase from the
                          note that states it. Empty otherwise.
  recording_hours         the total recording time or total sleep time the
                          note states for that study, in hours, as a number.
                          Null if the note states none in hours. Never compute
                          or convert one.
  recording_hours_quote   when recording_hours is present, the verbatim phrase
                          from the note that states it. Empty otherwise.

A finding is a symptom or condition the note documents as PRESENT in the
patient. Return one for each passage documenting any of the following, with
the category that names it:

  excessive_daytime_sleepiness, impaired_cognition, mood_disorder, insomnia,
  hypertension, ischemic_heart_disease, history_of_stroke

with quote, char_start and char_end as above. Never return a finding the note
denies or records as absent, and never return a category for a symptom that is
not in that list.

Return every qualifying evaluation, test and finding in the note. Do not filter
by date, by recency, or by which looks most relevant. Choosing among them
happens elsewhere.
"""


def build_sleep_result(
    document_id: str, text: str, payload: dict, metrics: CallMetrics | None = None
) -> ExtractionResult:
    """The sleep kind's trust boundary: a payload in, anchored facts out.

    `build_result`'s shape exactly, over another schema (REQ-79): every quote
    goes through `_anchor_or_drop`, a quote Python cannot locate is a drop
    with the payload path the re-ask needs, and a stated value whose own
    phrase cannot be located is demoted to unstated rather than kept without
    a span (D15's rule, REQ-38's shape). Facts land on `result.facts`; the
    `WmEvent`-shaped fields stay empty.
    """
    extraction = SleepApneaWorkupExtraction.model_validate(payload)
    result = ExtractionResult(
        document_id=document_id, kind=FactKind.SLEEP_APNEA_WORKUP,
        metrics=metrics, raw=payload,
    )

    for index, raw in enumerate(extraction.clinical_evaluations):
        at = f"clinical_evaluations[{index}]"
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "evaluation_quote_unanchorable", result.anchored_spans,
            path=f"{at}.quote",
        )
        if located is None:
            continue
        try:
            when = date.fromisoformat(raw.date)
        except ValueError:
            result.dropped.append({"reason": "unparseable_date", "quote": raw.date})
            continue
        result.facts.append(ClinicalEvaluation(evaluation_date=when, span=located.to_span()))

    for index, raw in enumerate(extraction.sleep_tests):
        at = f"sleep_tests[{index}]"
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "sleep_test_quote_unanchorable", result.anchored_spans,
            path=f"{at}.quote",
        )
        if located is None:
            continue
        try:
            when = date.fromisoformat(raw.date)
        except ValueError:
            result.dropped.append({"reason": "unparseable_date", "quote": raw.date})
            continue
        near = (located.char_start, located.char_end)
        value, value_span = raw.index, None
        if value is not None:
            located_value = _anchor_or_drop(
                document_id, text, raw.index_quote, -1, -1, result.dropped,
                "index_quote_unanchorable", result.anchored_spans, near,
                path=f"{at}.index_quote",
            )
            if located_value is None:
                value = None
            else:
                value_span = located_value.to_span()
        hours, hours_span = raw.recording_hours, None
        if hours is not None:
            located_hours = _anchor_or_drop(
                document_id, text, raw.recording_hours_quote, -1, -1,
                result.dropped, "recording_hours_quote_unanchorable",
                result.anchored_spans, near, path=f"{at}.recording_hours_quote",
            )
            if located_hours is None:
                hours = None
            else:
                hours_span = located_hours.to_span()
        result.facts.append(
            SleepTest(
                test_date=when,
                span=located.to_span(),
                index=value,
                index_span=value_span,
                recording_hours=hours,
                hours_span=hours_span,
            )
        )

    for index, raw in enumerate(extraction.findings):
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "finding_quote_unanchorable", result.anchored_spans,
            path=f"findings[{index}].quote",
        )
        if located is None:
            continue
        result.facts.append(DocumentedFinding(category=raw.category, span=located.to_span()))

    return result


_SLEEP_PATH = re.compile(
    r"^(clinical_evaluations|sleep_tests|findings)\[(\d+)\]\."
    r"(quote|index_quote|recording_hours_quote)$"
)
_SLEEP_QUOTE_FIELDS: dict[str, frozenset[str]] = {
    "clinical_evaluations": frozenset({"quote"}),
    "sleep_tests": frozenset({"quote", "index_quote", "recording_hours_quote"}),
    "findings": frozenset({"quote"}),
}


def _locate_sleep(payload: dict, path: str) -> tuple[dict, str]:
    """`_locate`'s counterpart for the sleep payload: the container and key a
    path names, or `KeyError` for one naming nothing in this payload — a
    model-invented path is never applied (T-89's rule, D150)."""
    match = _SLEEP_PATH.match(path)
    if match is None:
        raise KeyError(path)
    collection, index, fieldname = match.group(1), int(match.group(2)), match.group(3)
    if fieldname not in _SLEEP_QUOTE_FIELDS[collection]:
        raise KeyError(path)
    items = payload.get(collection) or []
    if index >= len(items):
        raise KeyError(path)
    return items[index], fieldname


# --------------------------------------------------------------------------
# The third fact kind: the knee osteoarthritis workup (T-109, REQ-79, D154)
#
# Built the way the sleep kind is: its own response model, instruction,
# builder, locator and prompt version, through the same anchorer and the same
# re-ask core, into the same result type. Nothing here constructs a `WmEvent`.
# --------------------------------------------------------------------------

#: The knee kind's configuration: its instruction, and T-89's re-ask unchanged.
KNEE_PROMPT_VERSION = "t109-knee-oa-workup-v1/t89-reask-v1"


class ExtractedKneeSymptom(BaseModel):
    category: KneeSymptomCategory = Field(
        description="Which of the listed knee symptoms the passage documents as present."
    )
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedRadiographicFinding(BaseModel):
    category: RadiographicFindingCategory = Field(
        description="Which of the listed findings the radiograph reports as present."
    )
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedKneeRadiograph(BaseModel):
    date: str = Field(description="The date the radiograph was taken, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")
    findings: list[ExtractedRadiographicFinding] = Field(default_factory=list)


class ExtractedConservativeTherapy(BaseModel):
    category: ConservativeTherapyCategory = Field(
        description="Whether the therapy is nonpharmacologic or a simple analgesic or NSAID."
    )
    start_date: str = Field(description="The date the therapy began, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class KneeOsteoarthritisWorkupExtraction(BaseModel):
    symptoms: list[ExtractedKneeSymptom] = Field(default_factory=list)
    radiographs: list[ExtractedKneeRadiograph] = Field(default_factory=list)
    conservative_therapies: list[ExtractedConservativeTherapy] = Field(default_factory=list)


KNEE_INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review of a hyaluronan injection into the knee. Return only what the schema
defines. Do not explain.

A symptom is a knee symptom the note documents as PRESENT in the patient. Return
one for each passage documenting any of the following, with the category that
names it:

  pain_limiting_daily_activities   knee pain that interferes with daily
                                   activities such as walking or standing
  pain_interrupting_sleep          knee pain that wakes the patient or
                                   interrupts sleep
  crepitus                         crepitus of the knee
  knee_stiffness                   stiffness of the knee

  quote        text copied verbatim and contiguously from the note. It must
               appear in the note character for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character

Never return a symptom the note denies or records as absent or resolved.

A radiograph is one plain radiograph (X-ray) OF THE KNEE that was performed.
For each one return:

  date                    the date it was taken, normalized to YYYY-MM-DD
  quote                   text copied verbatim and contiguously from the note,
                          showing the radiograph and its date
  char_start, char_end    as above
  findings                one entry for each of the following the radiograph
                          reports as present, with the category that names it
                          and a verbatim quote stating it:
                            joint_space_narrowing, subchondral_sclerosis,
                            osteophytes, subchondral_cysts
                          Empty if it reports none of them. Never return a
                          finding the report states is absent.

Never return a radiograph of any other joint, and never return one that was
ordered or scheduled but not performed.

A conservative_therapy is one treatment for the knee the note documents the
patient as having started. For each one return:

  category      nonpharmacologic for a home exercise program, physical therapy,
                education or a weight-loss program; simple_analgesic_or_nsaid
                for acetaminophen or a non-steroidal anti-inflammatory drug
  start_date    the date the therapy began, normalized to YYYY-MM-DD. Never
                substitute the date of the visit that mentions it.
  quote         text copied verbatim and contiguously from the note, showing
                the therapy and the date it began
  char_start, char_end    as above

Never return a therapy that was only recommended, offered or declined.

Return every qualifying symptom, radiograph and therapy in the note. Do not
filter by date, by recency, or by which looks most relevant. Choosing among
them happens elsewhere.
"""


def build_knee_result(
    document_id: str, text: str, payload: dict, metrics: CallMetrics | None = None
) -> ExtractionResult:
    """The knee kind's trust boundary: a payload in, anchored facts out.

    `build_sleep_result`'s shape over another schema (REQ-79): every quote goes
    through `_anchor_or_drop`, a quote Python cannot locate is a drop with the
    payload path the re-ask needs, and a radiograph finding whose own phrase
    cannot be located is dropped from the radiograph rather than kept without
    a span (D15's rule). Facts land on `result.facts`; the `WmEvent`-shaped
    fields stay empty.
    """
    extraction = KneeOsteoarthritisWorkupExtraction.model_validate(payload)
    result = ExtractionResult(
        document_id=document_id, kind=FactKind.KNEE_OSTEOARTHRITIS_WORKUP,
        metrics=metrics, raw=payload,
    )

    for index, raw in enumerate(extraction.symptoms):
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "symptom_quote_unanchorable", result.anchored_spans,
            path=f"symptoms[{index}].quote",
        )
        if located is None:
            continue
        result.facts.append(KneeSymptom(category=raw.category, span=located.to_span()))

    for index, raw in enumerate(extraction.radiographs):
        at = f"radiographs[{index}]"
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "radiograph_quote_unanchorable", result.anchored_spans,
            path=f"{at}.quote",
        )
        if located is None:
            continue
        try:
            when = date.fromisoformat(raw.date)
        except ValueError:
            result.dropped.append({"reason": "unparseable_date", "quote": raw.date})
            continue
        near = (located.char_start, located.char_end)
        findings: list[RadiographicFinding] = []
        for position, finding in enumerate(raw.findings):
            located_finding = _anchor_or_drop(
                document_id, text, finding.quote, finding.char_start, finding.char_end,
                result.dropped, "finding_quote_unanchorable", result.anchored_spans,
                near, path=f"{at}.findings[{position}].quote",
            )
            if located_finding is None:
                continue
            findings.append(
                RadiographicFinding(category=finding.category, span=located_finding.to_span())
            )
        result.facts.append(
            KneeRadiograph(
                radiograph_date=when, span=located.to_span(), findings=tuple(findings)
            )
        )

    for index, raw in enumerate(extraction.conservative_therapies):
        located = _anchor_or_drop(
            document_id, text, raw.quote, raw.char_start, raw.char_end,
            result.dropped, "therapy_quote_unanchorable", result.anchored_spans,
            path=f"conservative_therapies[{index}].quote",
        )
        if located is None:
            continue
        try:
            began = date.fromisoformat(raw.start_date)
        except ValueError:
            result.dropped.append({"reason": "unparseable_date", "quote": raw.start_date})
            continue
        result.facts.append(
            ConservativeTherapy(category=raw.category, start_date=began, span=located.to_span())
        )

    return result


_KNEE_PATH = re.compile(
    r"^(?:(symptoms|radiographs|conservative_therapies)\[(\d+)\]\.quote"
    r"|radiographs\[(\d+)\]\.findings\[(\d+)\]\.quote)$"
)


def _locate_knee(payload: dict, path: str) -> tuple[dict, str]:
    """`_locate`'s counterpart for the knee payload, nested findings included:
    the container and key a path names, or `KeyError` for one naming nothing in
    this payload -- a model-invented path is never applied (T-89's rule, D154)."""
    match = _KNEE_PATH.match(path)
    if match is None:
        raise KeyError(path)
    collection, index, outer, inner = match.groups()
    if collection is not None:
        items = payload.get(collection) or []
        position = int(index)
        if position >= len(items):
            raise KeyError(path)
        return items[position], "quote"
    radiographs = payload.get("radiographs") or []
    if int(outer) >= len(radiographs):
        raise KeyError(path)
    findings = radiographs[int(outer)].get("findings") or []
    if int(inner) >= len(findings):
        raise KeyError(path)
    return findings[int(inner)], "quote"


# --------------------------------------------------------------------------
# The fourth and fifth fact kinds: the bariatric surgical workup and the
# rheumatoid arthritis workup (T-110, REQ-79, D155)
#
# Built the way the sleep and knee kinds are: each its own response model,
# instruction, builder, locator and prompt version, through the same anchorer
# and the same re-ask core, into the same result type. Nothing here constructs
# a `WmEvent`. Every item is one dated fact with one quote, so the two builders
# share `_dated_facts`; the label each item carries is the model's, and
# whether it qualifies is the criterion's declared list (Art. II).
# --------------------------------------------------------------------------

#: The fourth kind's configuration: its instruction, and T-89's re-ask unchanged.
BARIATRIC_PROMPT_VERSION = "t110-bariatric-surgical-workup-v1/t89-reask-v1"
#: The fifth kind's.
RHEUMATOID_PROMPT_VERSION = "t110-rheumatoid-arthritis-workup-v1/t89-reask-v1"


class ExtractedWeight(BaseModel):
    date: str = Field(description="The date the weight was measured, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedEvaluationComponent(BaseModel):
    category: EvaluationComponentCategory = Field(
        description="Which component of the multidisciplinary evaluation the passage documents."
    )
    date: str = Field(description="The date the component took place, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class BariatricSurgicalWorkupExtraction(BaseModel):
    weights: list[ExtractedWeight] = Field(default_factory=list)
    evaluations: list[ExtractedEvaluationComponent] = Field(default_factory=list)


BARIATRIC_INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review of bariatric surgery. Return only what the schema defines. Do not
explain.

A weight is one body weight the note records as measured at a visit. For each
one return:

  date         the date the weight was measured, normalized to YYYY-MM-DD
  quote        text copied verbatim and contiguously from the note, long enough
               to show both the date and the weight. It must appear in the
               note character for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character

Return a weight whether or not a BMI is stated beside it. Never return a weight
that was not measured: a visit at which the patient was not weighed, a goal or
target weight, or a weight carried over from another visit.

An evaluation is one completed component of a multidisciplinary evaluation
before bariatric surgery. Return one for each passage documenting any of the
following, with the category that names it:

  bariatric_surgeon       an evaluation by a bariatric surgeon recommending
                          surgical treatment
  primary_care_referral   a referral for bariatric surgery by the patient's
                          primary care provider
  mental_health           an evaluation for bariatric surgery by a mental
                          health provider
  nutrition               a nutritional evaluation for bariatric surgery by a
                          physician or registered dietitian

For each one return:

  date                    the date it took place, normalized to YYYY-MM-DD
  quote                   text copied verbatim and contiguously from the note,
                          showing the component and its date
  char_start, char_end    as above

Never return a component that was only ordered, scheduled or recommended, or
that did not take place. Dietary counseling at a weight management program
visit is not a nutritional evaluation.

Return every qualifying weight and evaluation in the note. Do not filter by
date, by recency, or by which looks most relevant. Choosing among them happens
elsewhere.
"""


class ExtractedHeartFailureAssessment(BaseModel):
    status: HeartFailureClass = Field(
        description="No heart failure, or the New York Heart Association class the note states."
    )
    date: str = Field(description="The date of the assessment, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedTuberculosisScreen(BaseModel):
    result: TuberculosisScreenResult = Field(description="The result the note reports.")
    date: str = Field(description="The date the test was performed, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedTuberculosisTreatment(BaseModel):
    start_date: str = Field(description="The date the treatment began, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class ExtractedDiseaseActivity(BaseModel):
    level: DiseaseActivityLevel = Field(
        description="The disease activity level the note states in words."
    )
    date: str = Field(description="The date of the assessment, normalized to YYYY-MM-DD.")
    quote: str = Field(description="Text copied verbatim and contiguously from the note.")
    char_start: int = Field(description="Estimated offset of the quote's first character.")
    char_end: int = Field(description="Estimated offset one past the quote's last character.")


class RheumatoidArthritisWorkupExtraction(BaseModel):
    heart_failure_assessments: list[ExtractedHeartFailureAssessment] = Field(
        default_factory=list
    )
    tuberculosis_screens: list[ExtractedTuberculosisScreen] = Field(default_factory=list)
    tuberculosis_treatments: list[ExtractedTuberculosisTreatment] = Field(default_factory=list)
    disease_activity_assessments: list[ExtractedDiseaseActivity] = Field(default_factory=list)


RHEUMATOID_INSTRUCTION = """\
You extract structured facts from a clinical note for a prior authorization
review of infliximab for rheumatoid arthritis. Return only what the schema
defines. Do not explain.

Every item below carries:

  quote        text copied verbatim and contiguously from the note, showing
               the fact and its date. It must appear in the note character
               for character.
  char_start   the offset of the quote's first character, counting from 0 at
  char_end     the start of the note, and one past its last character

A heart_failure_assessment is a passage stating the patient's heart failure
status. Return:

  status       no_heart_failure when the note states the patient has no heart
               failure; class_i, class_ii, class_iii or class_iv when it
               states a New York Heart Association (NYHA) class
  date         the date of the assessment, normalized to YYYY-MM-DD

Never infer a class the note does not state.

A tuberculosis_screen is a tuberculin skin test or an interferon-gamma release
assay whose result the note reports. Return:

  result       negative or positive, as reported
  date         the date the test was performed or drawn, normalized to
               YYYY-MM-DD

Never return a test that was only ordered, or whose result is indeterminate.

A tuberculosis_treatment is a treatment for active or latent tuberculosis that
the note documents the patient as having started. Return:

  start_date   the date the treatment began, normalized to YYYY-MM-DD

Never return a treatment that was only recommended, offered, deferred or
declined.

A disease_activity_assessment is a passage stating the rheumatoid arthritis
disease activity level IN WORDS. Return:

  level        remission, low, moderate or high, as the note states it;
               moderately active is moderate and severely active is high
  date         the date of the assessment, normalized to YYYY-MM-DD

Never derive a level from a score, a joint count or a laboratory value. If the
note gives a score without stating the level in words, return nothing for it.

Return every qualifying item in the note. Do not filter by date, by recency,
or by which looks most relevant. Choosing among them happens elsewhere.
"""


#: One list of a flat payload: its items, the payload key the re-ask path
#: names, the item's date field, the drop reason, and how a located item
#: becomes a contract object. The label rides through untouched.
_FlatList = tuple[list, str, str, str, Callable[[Any, date, EvidenceSpan], Any]]


def build_bariatric_workup_result(
    document_id: str, text: str, payload: dict, metrics: CallMetrics | None = None
) -> ExtractionResult:
    """The fourth kind's trust boundary: a payload in, anchored facts out
    (T-110, REQ-79, D155). Every quote goes through `_anchor_or_drop`; a quote
    Python cannot locate is a drop carrying the payload path the re-ask needs,
    and an unparseable date is a drop. Neither is kept without a span (D15).
    Facts land on `result.facts`; the `WmEvent`-shaped fields stay empty."""
    extraction = BariatricSurgicalWorkupExtraction.model_validate(payload)
    result = ExtractionResult(
        document_id=document_id, kind=FactKind.BARIATRIC_SURGICAL_WORKUP,
        metrics=metrics, raw=payload,
    )
    lists: list[_FlatList] = [
        (extraction.weights, "weights", "date", "weight_quote_unanchorable",
         lambda raw, when, span: DocumentedWeight(weight_date=when, span=span)),
        (extraction.evaluations, "evaluations", "date", "evaluation_quote_unanchorable",
         lambda raw, when, span: EvaluationComponent(
             category=raw.category, evaluation_date=when, span=span)),
    ]
    for items, collection, date_field, reason, make in lists:
        for index, raw in enumerate(items):
            located = _anchor_or_drop(
                document_id, text, raw.quote, raw.char_start, raw.char_end,
                result.dropped, reason, result.anchored_spans,
                path=f"{collection}[{index}].quote",
            )
            if located is None:
                continue
            stated = getattr(raw, date_field)
            try:
                when = date.fromisoformat(stated)
            except ValueError:
                result.dropped.append({"reason": "unparseable_date", "quote": stated})
                continue
            result.facts.append(make(raw, when, located.to_span()))
    return result


def build_rheumatoid_workup_result(
    document_id: str, text: str, payload: dict, metrics: CallMetrics | None = None
) -> ExtractionResult:
    """The fifth kind's trust boundary (T-110, REQ-79, D155), in the fourth's
    shape over four lists."""
    extraction = RheumatoidArthritisWorkupExtraction.model_validate(payload)
    result = ExtractionResult(
        document_id=document_id, kind=FactKind.RHEUMATOID_ARTHRITIS_WORKUP,
        metrics=metrics, raw=payload,
    )
    lists: list[_FlatList] = [
        (extraction.heart_failure_assessments, "heart_failure_assessments", "date",
         "heart_failure_quote_unanchorable",
         lambda raw, when, span: HeartFailureAssessment(
             status=raw.status, assessment_date=when, span=span)),
        (extraction.tuberculosis_screens, "tuberculosis_screens", "date",
         "screen_quote_unanchorable",
         lambda raw, when, span: TuberculosisScreen(
             result=raw.result, screen_date=when, span=span)),
        (extraction.tuberculosis_treatments, "tuberculosis_treatments", "start_date",
         "treatment_quote_unanchorable",
         lambda raw, when, span: TuberculosisTreatment(start_date=when, span=span)),
        (extraction.disease_activity_assessments, "disease_activity_assessments", "date",
         "activity_quote_unanchorable",
         lambda raw, when, span: DiseaseActivityAssessment(
             level=raw.level, assessment_date=when, span=span)),
    ]
    for items, collection, date_field, reason, make in lists:
        for index, raw in enumerate(items):
            located = _anchor_or_drop(
                document_id, text, raw.quote, raw.char_start, raw.char_end,
                result.dropped, reason, result.anchored_spans,
                path=f"{collection}[{index}].quote",
            )
            if located is None:
                continue
            stated = getattr(raw, date_field)
            try:
                when = date.fromisoformat(stated)
            except ValueError:
                result.dropped.append({"reason": "unparseable_date", "quote": stated})
                continue
            result.facts.append(make(raw, when, located.to_span()))
    return result


def _flat_locator(collections: tuple[str, ...]) -> Locator:
    """`_locate`'s counterpart for a payload of flat quoted lists: the container
    and key a path names, or `KeyError` for one naming nothing in this payload
    -- a model-invented path is never applied (T-89's rule, D155)."""
    pattern = re.compile(
        r"^(" + "|".join(re.escape(c) for c in collections) + r")\[(\d+)\]\.quote$"
    )

    def locate(payload: dict, path: str) -> tuple[dict, str]:
        match = pattern.match(path)
        if match is None:
            raise KeyError(path)
        collection, index = match.groups()
        items = payload.get(collection) or []
        if int(index) >= len(items):
            raise KeyError(path)
        return items[int(index)], "quote"

    return locate


_locate_bariatric_workup = _flat_locator(("weights", "evaluations"))
_locate_rheumatoid_workup = _flat_locator(
    (
        "heart_failure_assessments",
        "tuberculosis_screens",
        "tuberculosis_treatments",
        "disease_activity_assessments",
    )
)


# --------------------------------------------------------------------------
# The fact-kind registry (T-107, REQ-78, D147, D149)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FactSchema:
    """Everything one `FactKind` asks the model and how its answer is read.

    The response model and the instruction are the prompt. The builder and the
    locator are the trust boundary and the re-ask shape (D122's
    parameterisation). The prompt version is what a recording is keyed by
    (D149). `digest` hashes the first three together with the re-ask
    instruction, and `tests/test_fact_kinds.py` pins it beside the version, so
    the prompt cannot change while the version stays the same.
    """

    kind: FactKind
    response_model: type[BaseModel]
    instruction: str
    build: Callable[..., Any]
    locate: Locator
    prompt_version: str

    @property
    def digest(self) -> str:
        material = json.dumps(
            {
                "kind": self.kind.value,
                "schema": self.response_model.model_json_schema(),
                "instruction": self.instruction,
                "reask_instruction": REASK_INSTRUCTION,
                "prompt_version": self.prompt_version,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


#: The closed registry a tree's `fact_kinds` selects from. The first entry is
#: the configuration every weight-management recording was measured under,
#: unchanged: `Extraction`, `INSTRUCTION`, `build_result`, `_locate`,
#: `PROMPT_VERSION`. The second is the sleep workup's (T-108, D150); the
#: third the knee osteoarthritis workup's (T-109, D154); the fourth and fifth
#: the bariatric surgical and rheumatoid arthritis workups' (T-110, D155).
FACT_SCHEMAS: dict[FactKind, FactSchema] = {
    FactKind.WEIGHT_MANAGEMENT: FactSchema(
        kind=FactKind.WEIGHT_MANAGEMENT,
        response_model=Extraction,
        instruction=INSTRUCTION,
        build=build_result,
        locate=_locate,
        prompt_version=PROMPT_VERSION,
    ),
    FactKind.SLEEP_APNEA_WORKUP: FactSchema(
        kind=FactKind.SLEEP_APNEA_WORKUP,
        response_model=SleepApneaWorkupExtraction,
        instruction=SLEEP_INSTRUCTION,
        build=build_sleep_result,
        locate=_locate_sleep,
        prompt_version=SLEEP_PROMPT_VERSION,
    ),
    FactKind.KNEE_OSTEOARTHRITIS_WORKUP: FactSchema(
        kind=FactKind.KNEE_OSTEOARTHRITIS_WORKUP,
        response_model=KneeOsteoarthritisWorkupExtraction,
        instruction=KNEE_INSTRUCTION,
        build=build_knee_result,
        locate=_locate_knee,
        prompt_version=KNEE_PROMPT_VERSION,
    ),
    FactKind.BARIATRIC_SURGICAL_WORKUP: FactSchema(
        kind=FactKind.BARIATRIC_SURGICAL_WORKUP,
        response_model=BariatricSurgicalWorkupExtraction,
        instruction=BARIATRIC_INSTRUCTION,
        build=build_bariatric_workup_result,
        locate=_locate_bariatric_workup,
        prompt_version=BARIATRIC_PROMPT_VERSION,
    ),
    FactKind.RHEUMATOID_ARTHRITIS_WORKUP: FactSchema(
        kind=FactKind.RHEUMATOID_ARTHRITIS_WORKUP,
        response_model=RheumatoidArthritisWorkupExtraction,
        instruction=RHEUMATOID_INSTRUCTION,
        build=build_rheumatoid_workup_result,
        locate=_locate_rheumatoid_workup,
        prompt_version=RHEUMATOID_PROMPT_VERSION,
    ),
}
