"""T-33 — reconciling the structured BMI against the note's (REQ-34, REQ-39).

Two kinds of source report the patient's BMI: FHIR observations, which
criterion (a) adjudicates on, and the chart's notes, which the model reads.
REQ-34 makes the structured value authoritative and the disagreement advisory
— **unless the two fall on opposite sides of the coverage threshold**, in which
case the criterion cannot be answered from evidence that contradicts itself
and resolves `INSUFFICIENT_EVIDENCE` with `gap_reason` `SOURCE_CONFLICT`.
Since T-81 a chart is several documents and every note-level value is
reconciled against the structured one independently (REQ-34a, D104).

This runs **after** extraction and takes criterion (a)'s already-produced result
as input, which is why it is not part of `criteria.py`: US-2 makes no model call
and the note BMI does not exist until T-15 has run (D11). It may downgrade a
verdict; it never upgrades one.

Every comparison here is Python (Art. II). The model supplied a number and a
quote; which side of a threshold that number falls on is not its judgment.
"""

from __future__ import annotations

from pa_agent.contracts import (
    Criterion,
    CriterionResult,
    CriterionVerdict,
    Discrepancy,
    GapReason,
    NoteBmi,
    Observation,
    ReconciledFact,
    WmEvent,
)


def note_bmis(
    events: list[WmEvent],
    stated: list[NoteBmi] | None = None,
) -> list[NoteBmi]:
    """Every BMI the notes state as current, with the span that proves each.

    A stated current BMI outranks an encounter's: T-60 defines it as the
    patient's BMI *now*, stated outside any encounter, while an event BMI is
    dated in the past (D50). When no note states one, the latest encounter
    that documented one is the single note-side value. Since T-81 (D104) a
    chart is several documents and every stated value is carried — no note is
    preferred by the order the store served it in (REQ-34a).

    Returns `[]` when no note states a BMI, and when the ones stated could not
    be anchored — D15's rule, since a BMI nobody can cite is not a documented
    BMI. The workflow only appends anchored values, so the second case is a
    guard rather than a path.
    """
    if stated:
        return list(stated)

    dated = sorted(
        (e for e in events if e.bmi is not None and e.bmi_span is not None),
        key=lambda e: e.event_date,
    )
    if not dated:
        return []
    latest = dated[-1]
    assert latest.bmi is not None and latest.bmi_span is not None
    return [NoteBmi(value=latest.bmi, span=latest.bmi_span)]


def reconcile_bmi(
    fact: ReconciledFact,
    criterion_a: Criterion,
    result: CriterionResult,
    observations: list[Observation],
    events: list[WmEvent],
    stated: list[NoteBmi] | None = None,
) -> CriterionResult:
    """Criterion (a)'s result, reconciled against every note. Never a model call.

    Each note-side value is compared to the structured value independently
    (REQ-34a): any pair across the threshold resolves `SOURCE_CONFLICT`, and
    otherwise each pair at or beyond tolerance is one discrepancy entry citing
    the note that stated it. Note-vs-note disagreement is not adjudicated —
    the structured value is authoritative and each note is measured against
    it. Entries are ordered by span, so the result does not depend on the
    order the store served the notes in.

    Returns the input unchanged when there is nothing to reconcile: no note
    value, no structured value, or an abstention (which has no value to
    disagree with).
    """
    from pa_agent.criteria import most_recent_bmi

    if fact.authoritative != "structured":
        raise ValueError(
            f"{fact.fact}: this reconciler implements REQ-34's structured-wins "
            f"rule; the tree declares {fact.authoritative!r} authoritative"
        )

    notes = sorted(
        note_bmis(events, stated),
        key=lambda n: (n.span.document_id, n.span.char_start, n.value),
    )
    structured = most_recent_bmi(observations)
    if not notes or structured is None:
        return result
    if result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE:
        # (a) found nothing to adjudicate; there is no structured verdict for a
        # note value to contradict, and an abstention already says so.
        return result

    threshold = criterion_a.require("bmi_threshold")

    # REQ-34's first branch: opposite sides of the threshold. Magnitude is
    # irrelevant here — 34.9 against 35.1 is a conflict and 34.0 against 44.0
    # is not, if both answer the criterion the same way. Any one note across
    # the line is enough (REQ-34a): a second note that agrees does not settle
    # a chart that contradicts itself.
    conflicting = [
        n for n in notes if (structured.value >= threshold) != (n.value >= threshold)
    ]
    if conflicting:
        stated_values = ", ".join(f"{n.value}" for n in notes)
        return CriterionResult(
            criterion_id=result.criterion_id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            # No spans: the contract refuses them on an abstention, and citing
            # one of two contradictory values would present it as the finding.
            gap_reason=GapReason.SOURCE_CONFLICT,
            discrepancies=result.discrepancies,
            detail=(
                f"structured BMI {structured.value} on "
                f"{structured.effective_date.isoformat()} and note BMI "
                f"{', '.join(str(n.value) for n in conflicting)} fall on opposite "
                f"sides of {threshold} (note values: {stated_values}); the "
                "criterion cannot be answered from sources that disagree "
                "across the threshold (REQ-34, REQ-34a)"
            ),
        )

    # Same side: the structured value stands and each disagreement is
    # advisory, recorded only if it is material (REQ-39, D14).
    structured_span = result.spans[0] if result.spans else None
    if structured_span is None:
        # Article III: an entry naming a value nobody can look up is an
        # accusation, not a citation. The verdict is untouched either way,
        # since a discrepancy never changes one.
        return result

    entries = []
    for note in notes:
        gap = abs(structured.value - note.value)
        if gap < fact.tolerance():
            continue
        entries.append(Discrepancy(
            criterion_id=result.criterion_id,
            fact=fact.fact,
            authoritative_value=structured.value,
            other_value=note.value,
            authoritative_span=structured_span,
            other_span=note.span,
            detail=(
                f"structured {structured.value} on "
                f"{structured.effective_date.isoformat()} against note "
                f"{note.value}; difference {gap:.2f} at or beyond tolerance "
                f"{fact.tolerance()}"
            ),
        ))
    if not entries:
        return result
    return result.model_copy(
        update={"discrepancies": [*result.discrepancies, *entries]}
    )
