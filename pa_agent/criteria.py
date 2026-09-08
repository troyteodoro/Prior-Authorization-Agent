"""Criteria (a) and (b): numeric comparison and set intersection, no model (D40).

Both functions read their constants from the compiled `Criterion` via
`require()`, which still raises on a provisional constant — the window is
never hardcoded (REQ-32's rule, applied to (a)'s lookback), and `as_of` is an
explicit parameter because a hidden `now()` would let the same chart answer
differently on two days with neither input changing (Art. II).

Verdict shapes, per D40:

- (a): most recent BMI within the lookback decides `MET`/`NOT_MET`, boundary
  inclusive (E12); BMI observations only outside the window are `NOT_MET`
  citing the most recent stale one (REQ-16 — evidence present, outside a
  required window); no BMI at all is `INSUFFICIENT_EVIDENCE`.
- (b): enough active conditions intersecting the value set is `MET` citing
  each contributing condition; anything less is `INSUFFICIENT_EVIDENCE`,
  never `NOT_MET` — a chart cannot prove the absence of a comorbidity, only
  fail to document one (Art. IV).

No model is imported here and never will be (Art. II, REQ-11, REQ-12).
"""

from __future__ import annotations

from datetime import date

from pa_agent.contracts import (
    Condition,
    Criterion,
    CriterionResult,
    CriterionVerdict,
    Observation,
)

# The LOINC code denoting a BMI observation. A terminology binding, not a
# policy quantity — the tree carries what the policy states, and the policy
# states "BMI", not a code system (same distinction D28 drew for CPT).
BMI_LOINC = "39156-5"

ACTIVE_STATUS = "active"


def _months_between(earlier: date, later: date) -> int:
    """Whole calendar months from `earlier` to `later`, day-sensitive:
    2025-09-15 to 2026-09-01 is 11 months, not 12."""
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return months


def evaluate_criterion_a(
    criterion: Criterion, observations: list[Observation], as_of: date
) -> CriterionResult:
    """REQ-11: numeric comparison against the most recent BMI within the
    lookback window. The threshold and window come from the tree."""
    threshold = criterion.require("bmi_threshold")
    lookback_months = criterion.require("lookback_months")

    bmis = sorted(
        (o for o in observations if o.code == BMI_LOINC),
        key=lambda o: o.effective_date,
    )
    if not bmis:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            detail="no BMI observation anywhere in the record",
        )

    latest = bmis[-1]
    stale = _months_between(latest.effective_date, as_of) >= lookback_months
    spans = [latest.span] if latest.span is not None else []
    if stale:
        # REQ-16: evidence present but outside a required window is NOT_MET.
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.NOT_MET,
            spans=spans,
            detail=(
                f"most recent BMI ({latest.value}) observed "
                f"{latest.effective_date.isoformat()}, outside the "
                f"{lookback_months}-month lookback from {as_of.isoformat()}"
            ),
        )

    met = latest.value >= threshold  # inclusive boundary (E12)
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.MET if met else CriterionVerdict.NOT_MET,
        spans=spans,
        detail=(
            f"most recent BMI {latest.value} on "
            f"{latest.effective_date.isoformat()} vs threshold {threshold}"
        ),
    )


def evaluate_criterion_b(
    criterion: Criterion, conditions: list[Condition], value_set: frozenset[str]
) -> CriterionResult:
    """REQ-12: set intersection between the patient's active condition codes
    and the comorbidity value set. `MET` or abstention — never `NOT_MET`."""
    minimum = criterion.require("min_comorbidity_count")

    qualifying = [
        c
        for c in conditions
        if c.clinical_status == ACTIVE_STATUS and c.code in value_set
    ]
    if len(qualifying) >= minimum:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.MET,
            spans=[c.span for c in qualifying if c.span is not None],
            detail=(
                "active comorbidities in the value set: "
                + ", ".join(sorted(c.code for c in qualifying))
            ),
        )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
        detail=(
            f"{len(qualifying)} active condition(s) intersect the value set; "
            f"{minimum} required. Absence of a documented comorbidity is not "
            "evidence of its absence (D40)."
        ),
    )
