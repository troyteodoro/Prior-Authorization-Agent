"""T-33 — reconciling the structured BMI against the note's (REQ-34, REQ-39).

Two sources report the patient's BMI: FHIR observations, which criterion (a)
adjudicates on, and the chart note, which the model reads. REQ-34 makes the
structured value authoritative and the disagreement advisory — **unless the two
fall on opposite sides of the coverage threshold**, in which case the criterion
cannot be answered from evidence that contradicts itself and resolves
`INSUFFICIENT_EVIDENCE` with `gap_reason` `SOURCE_CONFLICT`.

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
    EvidenceSpan,
    GapReason,
    Observation,
    ReconciledFact,
    WmEvent,
)


def note_bmi(
    events: list[WmEvent],
    current_bmi: float | None = None,
    current_bmi_span: EvidenceSpan | None = None,
) -> tuple[float, EvidenceSpan] | None:
    """The most recent BMI the note states, with the span that proves it.

    `current_bmi` wins whenever it exists: T-60 defines it as the patient's BMI
    *now*, stated outside any encounter, while an event BMI is dated in the past
    (D50). Otherwise the latest encounter that documented one.

    Returns `None` when the note states no BMI, and when it stated one Python
    could not anchor — D15's rule, since a BMI nobody can cite is not a
    documented BMI.
    """
    if current_bmi is not None and current_bmi_span is not None:
        return current_bmi, current_bmi_span

    dated = sorted(
        (e for e in events if e.bmi is not None and e.bmi_span is not None),
        key=lambda e: e.event_date,
    )
    if not dated:
        return None
    latest = dated[-1]
    assert latest.bmi is not None and latest.bmi_span is not None
    return latest.bmi, latest.bmi_span


def reconcile_bmi(
    fact: ReconciledFact,
    criterion_a: Criterion,
    result: CriterionResult,
    observations: list[Observation],
    events: list[WmEvent],
    current_bmi: float | None = None,
    current_bmi_span: EvidenceSpan | None = None,
) -> CriterionResult:
    """Criterion (a)'s result, reconciled against the note. Never a model call.

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

    note = note_bmi(events, current_bmi, current_bmi_span)
    structured = most_recent_bmi(observations)
    if note is None or structured is None:
        return result
    if result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE:
        # (a) found nothing to adjudicate; there is no structured verdict for a
        # note value to contradict, and an abstention already says so.
        return result

    note_value, note_span = note
    threshold = criterion_a.require("bmi_threshold")

    # REQ-34's first branch: opposite sides of the threshold. Magnitude is
    # irrelevant here — 34.9 against 35.1 is a conflict and 34.0 against 44.0
    # is not, if both answer the criterion the same way.
    if (structured.value >= threshold) != (note_value >= threshold):
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
                f"{note_value} fall on opposite sides of {threshold}; "
                "the criterion cannot be answered from sources that disagree "
                "across the threshold (REQ-34)"
            ),
        )

    # Same side: the structured value stands and the disagreement is advisory,
    # recorded only if it is material (REQ-39, D14).
    gap = abs(structured.value - note_value)
    if gap < fact.tolerance():
        return result

    structured_span = result.spans[0] if result.spans else None
    if structured_span is None:
        # Article III: an entry naming a value nobody can look up is an
        # accusation, not a citation. The verdict is untouched either way,
        # since a discrepancy never changes one.
        return result

    entry = Discrepancy(
        criterion_id=result.criterion_id,
        fact=fact.fact,
        authoritative_value=structured.value,
        other_value=note_value,
        authoritative_span=structured_span,
        other_span=note_span,
        detail=(
            f"structured {structured.value} on "
            f"{structured.effective_date.isoformat()} against note "
            f"{note_value}; difference {gap:.2f} at or beyond tolerance "
            f"{fact.tolerance()}"
        ),
    )
    return result.model_copy(
        update={"discrepancies": [*result.discrepancies, entry]}
    )
