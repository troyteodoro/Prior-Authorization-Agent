"""T-103 — the packet: the form a specialist sends, assembled from what was determined.

**Pure.** This module names no path, opens nothing, imports no clock and builds
no store. Every value it cannot derive arrives as an argument, which is the rule
`_now()` states for the composition root one module up (D127): the payer, the
submission timestamp, the rendered determination and the document index are all
handed **down**.

Three things it deliberately does not do, each for a measured reason.

- **It never re-renders a determination.** `assemble` takes
  `rendered_determination`, the dict `cli._render` produced, and embeds it
  verbatim. A second renderer is a second answer to one question (D129), and
  v2.2's `T-116` compares the CLI's surface with the UI's byte for byte. It also
  **cannot** import `pa_agent.cli`: `tests/test_planes.py`'s `BOTH_PLANES` is an
  exact-set assertion, and a `form.py` that imported the composition root would
  join it — making the module that assembles a packet a module that reaches both
  storage planes (Article VI).
- **It never decides what a suggestion is.** `history.py` computes the review and
  Python colours it (REQ-64); this module reads the review and the review log,
  and a suggestion enters a packet **only** through a recorded
  `ACCEPT_SUGGESTION` (REQ-65, REQ-74).
- **It never opens a document.** `assemble` validates every citation through
  `pa_agent.spans` against an index the caller filled — `source_ids` reports what
  to fill it with, so the traversal that *collects* citations and the traversal
  that *needs* them are one function.

The `.eml` is written by hand rather than through `email.message.EmailMessage`.
`as_string()` stamps a clock-derived `Date:` and a randomised `Message-ID`, so no
committed fixture could equal two renders of one packet — which is `T-106`'s
whole exit — and it line-folds to a stdlib policy that a Python upgrade may
change with no diff in this repo. `Message-ID` here is a digest of
`(session_id, run_index)` at the reserved `.invalid` domain: deterministic, and
unable to collide with a real host. There is no `From:`, because no committed
artifact names the submitting practice and a header written from memory is a
claim enforced as though it had a source (D118, D128; D131 states it).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence

from pa_agent.contracts import (
    ACCEPTANCE_ACTIONS,
    Determination,
    EvidenceSpan,
    HistoryReview,
    IcdSuggestion,
    Packet,
    PacketProvenance,
    PacketSuggestion,
    ReviewAction,
    ReviewEntry,
    Session,
    SuggestionColour,
)
from pa_agent.index import DocumentIndex
from pa_agent.spans import SpanValidationError, validate as validate_span


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


class PacketRefused(ValueError):
    """A packet that cannot be assembled, and the reason a reviewer can act on.

    A `ValueError` subclass with three children rather than one exception with a
    `reason` field: the three have different next actions — write a
    justification, fix a `row_id`, or a citation that no longer slices — and
    `resolver.py`'s four types are the precedent for not collapsing them (D32).
    """


class UnjustifiedRedSuggestion(PacketRefused):
    """An accepted red suggestion with no written justification (REQ-65, REQ-74).

    Names **every** unjustified code, joined, never the first: a refusal naming
    one makes a reviewer write one justification and re-run four times.
    `_determine_or_report` printing one stderr line per errored criterion is the
    precedent (REQ-29, D76).
    """


class SuggestionNotInReview(PacketRefused):
    """An acceptance naming a `row_id` this run's review does not hold.

    Refused rather than skipped. A silent skip puts nothing in the packet and
    says nothing about it; carrying the entry's own code instead would put a code
    in a packet that traces to nothing, which is the one thing a suggestion may
    never be (REQ-63, D131).
    """


class UncitedPacket(PacketRefused):
    """A citation that does not slice back (Article III, REQ-74).

    Carries the classified `SpanRejection` from `pa_agent.spans`, so *the
    document is not in the index*, *the offsets run past the end* and *the slice
    is not the quote* stay three different findings — `workflow`'s span pass, one
    surface over.
    """


# --------------------------------------------------------------------------
# The evidence traversal — one function, three readers
# --------------------------------------------------------------------------


def _determination_spans(determination: Determination) -> tuple[EvidenceSpan, ...]:
    """Every span a determination cites, in a declared order.

    Coverage first, then the categorical exclusion's facts, then the criteria in
    tree order. The nested quotes on a `CoverageClaim` are collected too: a §C
    bullet alone slices back perfectly and means nothing without its scoping
    sentence (D22, D28), so a packet that cited the bullet and not the sentence
    would validate and mislead.
    """
    spans: list[EvidenceSpan] = []
    claim = determination.coverage_claim
    if claim is not None:
        spans.append(
            EvidenceSpan(
                document_id=claim.document_id,
                char_start=claim.char_start,
                char_end=claim.char_end,
                quote=claim.quote,
            )
        )
        for nested in (claim.scoping_quote, claim.corroborating_quote):
            if nested is not None:
                spans.append(nested)
    spans.extend(determination.exclusion_evidence)
    for result in determination.criterion_results:
        spans.extend(result.spans)
    return tuple(spans)


def _review_spans(review: HistoryReview | None) -> tuple[EvidenceSpan, ...]:
    """Every span the *whole* review could cite, at any colour.

    A superset of what a packet ends up carrying, on purpose: this feeds
    `source_ids`, and the caller has to fill the index before `assemble` knows
    which suggestions were accepted. Over-filling an index costs a dict entry;
    under-filling it is an `UncitedPacket` on a citation that was fine.
    """
    if review is None:
        return ()
    spans: list[EvidenceSpan] = []
    for suggestion in review.suggestions:
        spans.append(suggestion.effect)
        spans.extend(suggestion.citations)
    for withheld in review.withheld:
        spans.extend(withheld.citations)
    return tuple(spans)


def source_ids(
    *, determination: Determination, review: HistoryReview | None = None
) -> tuple[str, ...]:
    """Every document id a packet over this run could cite, first-cited order.

    The composition root needs this to fill the index before `assemble`
    validates against it, and it lives here so that one traversal answers both
    *what does the packet cite* and *what must be loaded to check it*. Two
    traversals would be two answers with no test able to separate them while
    they happened to agree (D65's shape).
    """
    seen: dict[str, None] = {}
    for span in _determination_spans(determination) + _review_spans(review):
        seen.setdefault(span.document_id, None)
    return tuple(seen)


def citations(packet: Packet) -> tuple[EvidenceSpan, ...]:
    """Every span in this packet: the determination's, then each accepted
    suggestion's provenance span and chart evidence.

    The one traversal. `assemble` validates what this returns, `render` writes
    the manifest from it, and `T-106`'s gate counts it — so *every citation in
    every packet slices back* is a claim about the same set in all three places.
    """
    spans: list[EvidenceSpan] = list(packet.evidence)
    for suggestion in packet.suggestions:
        spans.append(suggestion.effect)
        spans.extend(suggestion.citations)
    return tuple(spans)


# --------------------------------------------------------------------------
# The review log, read
# --------------------------------------------------------------------------


def _latest_by_row(
    entries: Sequence[ReviewEntry], run_index: int, actions: Iterable[ReviewAction]
) -> dict[str, ReviewEntry]:
    """The last entry per `row_id` that speaks to `actions`, in **log order**.

    Never by comparing `at`: two entries written in the same second compare
    equal, and a sort on a string clock lets whichever one the sort put last
    decide. The log is append-only, so its order *is* the history (D131).

    Scoped to `run_index`, because a review of snapshot 0 does not justify a code
    in snapshot 1's packet — a second run is a new snapshot of a chart that may
    have moved (US-12, D127).
    """
    wanted = tuple(actions)
    latest: dict[str, ReviewEntry] = {}
    for entry in entries:
        if entry.run_index != run_index or entry.action not in wanted:
            continue
        # `row_id` is required on every member of ROW_ACTIONS by `ReviewEntry`'s
        # own validator, so this cannot be None for any action asked for here.
        latest[str(entry.row_id)] = entry
    return latest


def accepted(
    review: HistoryReview | None,
    entries: Sequence[ReviewEntry],
    run_index: int,
) -> tuple[PacketSuggestion, ...]:
    """The suggestions a human put in this run's packet, in acceptance order.

    **Acceptance and justification are two questions** (D131). The latest
    `ACCEPT`/`REJECT` for a row decides whether it is in; the latest `JUSTIFY`
    supplies the text. One latest-entry rule would be wrong in both directions:
    an accept followed by a justification would un-accept the row, and a
    justification followed by a rejection would keep it.

    Every clinical field is copied from the **row's** suggestion. The entry
    contributes that it was accepted, by whom, when, and — for a red — why.

    Raises `SuggestionNotInReview` on an accept the review does not hold, and
    `UnjustifiedRedSuggestion` naming **every** unjustified code when any
    accepted red has none.
    """
    by_row: Mapping[str, IcdSuggestion] = {
        suggestion.row_id: suggestion
        for suggestion in (review.suggestions if review is not None else ())
    }
    decisions = _latest_by_row(entries, run_index, ACCEPTANCE_ACTIONS)
    justifications = _latest_by_row(
        entries, run_index, (ReviewAction.JUSTIFY_SUGGESTION,)
    )

    unjustified: list[str] = []
    built: list[PacketSuggestion] = []
    for row_id, decision in decisions.items():
        if decision.action is not ReviewAction.ACCEPT_SUGGESTION:
            continue
        suggestion = by_row.get(row_id)
        if suggestion is None:
            raise SuggestionNotInReview(
                f"review entry accepts {row_id!r}, and this run's review holds "
                f"{sorted(by_row) or 'no suggestion at all'}. A packet carries "
                "codes that trace to a row of the knowledge table and to nothing "
                "else (REQ-63, REQ-74)"
            )
        justifying = justifications.get(row_id)
        justification = justifying.justification if justifying is not None else None
        if suggestion.colour is SuggestionColour.RED and justification is None:
            unjustified.append(suggestion.icd10_code)
            continue
        built.append(
            PacketSuggestion(
                row_id=suggestion.row_id,
                colour=suggestion.colour,
                icd10_code=suggestion.icd10_code,
                icd10_title=suggestion.icd10_title,
                effect_display=suggestion.effect_display,
                effect=suggestion.effect,
                citations=suggestion.citations,
                justification=justification,
                accepted_by=decision.reviewer,
                accepted_at=decision.at,
            )
        )

    if unjustified:
        raise UnjustifiedRedSuggestion(
            f"{len(unjustified)} accepted red suggestion(s) carry no written "
            f"justification: {', '.join(unjustified)}. Red means nothing on the "
            "chart supports the code, so it enters a form on the reviewer's "
            "writing and nothing else (REQ-65, REQ-74)"
        )
    return tuple(built)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def assemble(
    *,
    session: Session,
    run_index: int,
    rendered_determination: dict,
    review: HistoryReview | None,
    payer: str,
    index: DocumentIndex,
    submitted_at: str | None = None,
) -> Packet:
    """One packet over one snapshot, with every citation validated.

    `rendered_determination` is `cli._render(run.determination)`, handed down and
    embedded verbatim — this module holds no renderer (D129, D131).

    `submitted_at` is `None` until a submission exists; `T-105` supplies it and
    no existing call changes, which is why it is a defaulted keyword rather than
    a field this row invents a value for.

    Every span is validated through `pa_agent.spans` against `index` before the
    packet is returned, and the first rejection raises `UncitedPacket` carrying
    the classified reason. That is `workflow._validate_result_spans`' shape: the
    validator is the same function, the index is built by the caller because only
    the composition root may name both ports (REQ-41), and a rejection is
    classified rather than turned into a boolean.
    """
    if not 0 <= run_index < len(session.runs):
        raise PacketRefused(
            f"session {session.session_id} holds {len(session.runs)} run(s) and "
            f"a packet was asked for run {run_index}; a packet is over one "
            "snapshot and says which"
        )
    run = session.runs[run_index]
    determination = run.determination

    suggestions = accepted(review, session.reviews, run_index)
    spans = _determination_spans(determination)

    packet = Packet(
        provenance=PacketProvenance(
            session_id=session.session_id,
            run_index=run_index,
            policy_version_id=run.policy_version_id,
            payer=payer,
            submitted_at=submitted_at,
        ),
        patient_id=session.intake.patient_id,
        requesting_provider=session.intake.requesting_provider,
        servicing_provider=session.intake.servicing_provider,
        procedure_code=session.intake.procedure_code,
        diagnosis_codes=session.intake.icd10_codes,
        determination=rendered_determination,
        evidence=spans,
        supporting_documents=tuple(
            dict.fromkeys(span.document_id for span in spans)
        ),
        suggestions=suggestions,
    )

    for span in citations(packet):
        try:
            validate_span(span, index)
        except (SpanValidationError, KeyError) as exc:
            raise UncitedPacket(
                f"packet for session {session.session_id} run {run_index} cites "
                f"{span.document_id}[{span.char_start}:{span.char_end}], which "
                f"does not slice back: {exc}"
            ) from exc
    return packet


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

#: Reserved by RFC 2606, so a generated `Message-ID` cannot name a real host.
MESSAGE_ID_DOMAIN = "pa-agent.invalid"

_RULE = "=" * 72


def message_id(packet: Packet) -> str:
    """Deterministic from `(session_id, run_index)`, so two renders agree.

    `EmailMessage` randomises this, which is exactly why it is not used here:
    `T-106` commits a fixture and compares bytes.
    """
    digest = hashlib.sha256(
        f"{packet.provenance.session_id}:{packet.provenance.run_index}".encode("utf-8")
    ).hexdigest()
    return f"<{digest[:32]}@{MESSAGE_ID_DOMAIN}>"


def _span_line(span: EvidenceSpan) -> str:
    quote = " ".join((span.quote or "").split())
    where = f"{span.document_id}[{span.char_start}:{span.char_end}]"
    return f"{where} {quote!r}" if quote else where


def render(packet: Packet) -> str:
    """The `.eml` bytes, as text. Hand-rolled, deterministic, no clock.

    Headers in a fixed order; `Date:` only when the packet carries a submission
    timestamp; no `From:`, because nothing in this repository states who sends
    (D131). The body is the form, then the accepted suggestions with their
    justifications, then the determination **verbatim** as the composition root
    rendered it, then the citation manifest `citations()` returns.
    """
    provenance = packet.provenance
    lines = [
        f"To: {provenance.payer}",
        f"Subject: Prior authorization request — {packet.procedure_code} — "
        f"patient {packet.patient_id}",
        f"Message-ID: {message_id(packet)}",
    ]
    if provenance.submitted_at is not None:
        lines.append(f"Date: {provenance.submitted_at}")
    lines += [
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        f"X-PA-Session: {provenance.session_id}",
        f"X-PA-Run: {provenance.run_index}",
        f"X-PA-Policy-Version: {provenance.policy_version_id}",
        "",
        "PRIOR AUTHORIZATION REQUEST",
        _RULE,
        "",
        "Administrative",
        f"  Patient:             {packet.patient_id}",
        f"  Requesting provider: {packet.requesting_provider or '(not supplied)'}",
        f"  Servicing provider:  {packet.servicing_provider or '(not supplied)'}",
        "",
        "Code alignment",
        f"  Procedure:       {packet.procedure_code}",
        "  Diagnosis codes: "
        + (", ".join(packet.diagnosis_codes) or "(none carried by the request)"),
        "",
        f"Accepted ICD-10 suggestions ({len(packet.suggestions)})",
    ]
    if not packet.suggestions:
        lines.append("  (none accepted by the reviewer)")
    for suggestion in packet.suggestions:
        lines.append(
            f"  {suggestion.icd10_code}  {suggestion.icd10_title} "
            f"[{suggestion.colour.value}] — row {suggestion.row_id}"
        )
        lines.append(
            f"      accepted by {suggestion.accepted_by} at {suggestion.accepted_at}"
        )
        lines.append(f"      effect: {suggestion.effect_display}")
        if suggestion.justification is not None:
            lines.append(f"      justification: {suggestion.justification}")
        for span in suggestion.citations:
            lines.append(f"      cited: {_span_line(span)}")
    lines += [
        "",
        f"Supporting documents ({len(packet.supporting_documents)})",
    ]
    for document_id in packet.supporting_documents:
        lines.append(f"  {document_id}")
    manifest = citations(packet)
    lines += [
        "",
        "Determination",
        _RULE,
        json.dumps(packet.determination, indent=2, ensure_ascii=False),
        "",
        f"Citation manifest ({len(manifest)})",
        _RULE,
    ]
    for position, span in enumerate(manifest, start=1):
        lines.append(f"  {position:>3}. {_span_line(span)}")
    lines.append("")
    return "\n".join(lines)
