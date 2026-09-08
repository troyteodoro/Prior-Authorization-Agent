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

Both abstentions carry `NO_EVIDENCE_RETRIEVED` (REQ-31, D44): nothing was
found for the criterion, and the next action is to go find documentation.

No model is imported here and never will be (Art. II, REQ-11, REQ-12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pa_agent.contracts import (
    CategoricalExclusion,
    Condition,
    Criterion,
    CriterionResult,
    CriterionVerdict,
    ExclusionMatch,
    GapReason,
    Observation,
    ProgramAssertion,
    WmEvent,
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
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
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
        gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
        detail=(
            f"{len(qualifying)} active condition(s) intersect the value set; "
            f"{minimum} required. Absence of a documented comorbidity is not "
            "evidence of its absence (D40)."
        ),
    )


def evaluate_sc2(
    exclusion: CategoricalExclusion,
    observations: list[Observation],
    conditions: list[Condition],
    as_of: date,
    lookback_months: int,
) -> ExclusionMatch | None:
    """REQ-3: the categorical exclusion, deterministic, zero model calls.

    Fires only when the most recent BMI is **inside** the lookback window and
    below the bound, and the excluded condition is active — a categorical
    denial issued on evidence the criteria path would refuse to approve on is
    confidence asymmetry in the wrong direction (D41). The window is borrowed
    from criterion (a) by the caller: one window, one constant.

    `None` means the exclusion does not apply and the request proceeds; it
    never means "insufficient evidence", because sc2 is not a criterion and
    abstention is the criteria path's vocabulary.
    """
    bound = exclusion.constants["bmi_upper_bound"].value
    excluded_code = exclusion.condition_binding["code"]

    bmis = sorted(
        (o for o in observations if o.code == BMI_LOINC),
        key=lambda o: o.effective_date,
    )
    if not bmis:
        return None
    latest = bmis[-1]
    if _months_between(latest.effective_date, as_of) >= lookback_months:
        return None  # stale evidence cannot categorically deny (D41)
    if latest.value >= bound:
        return None

    matching = [
        c
        for c in conditions
        if c.code == excluded_code and c.clinical_status == ACTIVE_STATUS
    ]
    if not matching:
        return None

    evidence = [s for s in (latest.span, matching[0].span) if s is not None]
    if not evidence:
        return None  # nothing citable; a denial nobody can check is not issued
    return ExclusionMatch(
        exclusion_id=exclusion.id, claim=exclusion.claim, evidence=evidence
    )


# --------------------------------------------------------------------------
# c1 through c5 — deterministic predicates over `wm_events` (REQ-13, D48)
#
# No model call after extraction, and the same event list yields the same
# verdicts on every run (Art. II). c3 identifies the qualifying run once and
# c2, c4 and c5 scope to it, which is what the tree's `scoped_to: "c3"` says.
# --------------------------------------------------------------------------


def _month(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def _month_index(month: tuple[int, int]) -> int:
    return month[0] * 12 + month[1]


@dataclass(frozen=True)
class QualifyingRun:
    """The longest block of consecutive populated months, and its events.

    REQ-14's object. Empty when there are no events at all — which is an
    abstention everywhere, never a run of length zero (D12).
    """

    months: tuple[tuple[int, int], ...]
    events: tuple[WmEvent, ...]

    @property
    def length(self) -> int:
        return len(self.months)

    def events_in(self, month: tuple[int, int]) -> list[WmEvent]:
        return [e for e in self.events if _month(e.event_date) == month]

    @property
    def last_event(self) -> WmEvent | None:
        return max(self.events, key=lambda e: e.event_date) if self.events else None


def qualifying_run(events: list[WmEvent]) -> QualifyingRun:
    """The longest run of consecutive calendar months holding events (REQ-14).

    Ties go to the **most recent** run: REQ-14 does not say which longest run,
    and the recent one is the only reading that can help a patient under c2.

    **Known defect, D48, tracked as T-42:** the longest run is not always the
    one that qualifies. A six-month run three years ago beats a four-month run
    last month, and c2 then reports the patient stale. REQ-14 says longest, so
    longest is what this returns; changing the selection is T-42's to decide.
    """
    if not events:
        return QualifyingRun(months=(), events=())

    months = sorted({_month(e.event_date) for e in events})
    best: list[tuple[int, int]] = []
    current: list[tuple[int, int]] = []
    for month in months:
        if current and _month_index(month) == _month_index(current[-1]) + 1:
            current.append(month)
        else:
            current = [month]
        # `>=` breaks ties toward the later run, since months ascend.
        if len(current) >= len(best):
            best = list(current)

    in_run = tuple(
        sorted(
            (e for e in events if _month(e.event_date) in set(best)),
            key=lambda e: e.event_date,
        )
    )
    return QualifyingRun(months=tuple(best), events=in_run)


def _no_events_reason(assertions: list[ProgramAssertion]) -> GapReason:
    """D12: zero events plus a claim is a different gap from zero events and
    nothing. E8 sends Sam to find the visit notes behind the claim; E7 sends
    her to find documentation of a program at all."""
    return (
        GapReason.UNSUBSTANTIATED_ASSERTION
        if assertions
        else GapReason.NO_EVIDENCE_RETRIEVED
    )


def evaluate_c1(
    criterion: Criterion,
    events: list[WmEvent],
    assertions: list[ProgramAssertion] | None = None,
) -> CriterionResult:
    """REQ-36: membership is the supervision claim, because REQ-9 already
    excluded unsupervised attempts during extraction. Zero events is an
    abstention, never `NOT_MET` (D13)."""
    minimum = criterion.require("min_events")
    if len(events) >= minimum:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.MET,
            spans=[e.span for e in events],
            detail=f"{len(events)} documented supervised encounter(s)",
        )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
        gap_reason=_no_events_reason(assertions or []),
        detail="no documented supervised weight-management encounter",
    )


def evaluate_c3(
    criterion: Criterion,
    events: list[WmEvent],
    assertions: list[ProgramAssertion] | None = None,
    run: QualifyingRun | None = None,
) -> CriterionResult:
    """REQ-14: the longest run of consecutive populated months. A run of one to
    three months is `NOT_MET` — the program happened and was too short. Zero
    events is `INSUFFICIENT_EVIDENCE`, because nothing was documented at all,
    and collapsing the two is what D12 forbade."""
    required = criterion.require("min_consecutive_months")
    run = run if run is not None else qualifying_run(events)

    if not events:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=_no_events_reason(assertions or []),
            detail="no encounters, so no run to measure",
        )
    verdict = (
        CriterionVerdict.MET if run.length >= required else CriterionVerdict.NOT_MET
    )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=verdict,
        spans=[e.span for e in run.events],
        detail=(
            f"longest run of consecutive months is {run.length}; "
            f"{required} required"
        ),
    )


def evaluate_c2(
    criterion: Criterion,
    run: QualifyingRun,
    as_of: date,
    c3_met: bool,
) -> CriterionResult:
    """REQ-32: the run c3 identified must have ended inside the recency window,
    which is read from the tree and never hardcoded (Art. VII)."""
    window = criterion.require("recency_window_months")
    last = run.last_event
    if last is None or not c3_met:
        # No qualifying run to be recent about. REQ-15's shape: the question
        # cannot be answered rather than answered negatively.
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
            detail="no qualifying run for the recency window to apply to",
        )
    months_since = _months_between(last.event_date, as_of)
    verdict = (
        CriterionVerdict.MET if months_since < window else CriterionVerdict.NOT_MET
    )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=verdict,
        spans=[last.span],
        detail=(
            f"run ended {last.event_date.isoformat()}, {months_since} month(s) "
            f"before {as_of.isoformat()}; window is {window}"
        ),
    )


def _rate_months_required(criterion: Criterion, run: QualifyingRun) -> int:
    """The tree speaks a rate, not a count (D24). `every_month_of_run` is the
    only value in the vocabulary, and an unknown one raises rather than
    guessing a threshold."""
    rate = criterion.require("documentation_rate")
    if rate != "every_month_of_run":
        raise ValueError(
            f"{criterion.id}: unknown documentation_rate {rate!r}; the predicate "
            "will not invent a reading of a policy constant"
        )
    return run.length


def evaluate_c4(
    criterion: Criterion, run: QualifyingRun, c3_met: bool
) -> CriterionResult:
    """REQ-40: every month of the qualifying run contains an encounter with a
    **documented** BMI. A weight plus a height on file elsewhere does not
    count — a derived value has no single span to cite (D15)."""
    if not c3_met:
        return CriterionResult(  # REQ-15
            criterion_id=criterion.id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
            detail="c3 identified no qualifying run to scope to",
        )
    _rate_months_required(criterion, run)
    deficient = [
        month for month in run.months
        if not any(e.bmi is not None for e in run.events_in(month))
    ]
    if not deficient:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.MET,
            spans=[e.bmi_span for e in run.events if e.bmi_span is not None],
            detail=f"BMI documented in all {run.length} month(s) of the run",
        )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.NOT_MET,
        # The encounters that happened and documented no BMI — evidence that
        # falls short, which is what a NOT_MET cites (REQ-5, D48).
        spans=[e.span for month in deficient for e in run.events_in(month)],
        detail=(
            f"{len(deficient)} of {run.length} month(s) document no BMI: "
            + ", ".join(f"{y}-{m:02d}" for y, m in deficient)
        ),
    )


def evaluate_c5(
    criterion: Criterion, run: QualifyingRun, c3_met: bool
) -> CriterionResult:
    """REQ-37: every month of the run carries an encounter documenting **both**
    diet and activity. A rate, not a count (D24): a seven-month run documented
    in four months is `NOT_MET`, where the old count shape called it `MET`."""
    if not c3_met:
        return CriterionResult(  # REQ-15
            criterion_id=criterion.id,
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
            detail="c3 identified no qualifying run to scope to",
        )
    _rate_months_required(criterion, run)
    both_required = criterion.require("requires_both_diet_and_activity")

    def qualifies(event: WmEvent) -> bool:
        if both_required:
            return event.diet_documented and event.activity_documented
        return event.diet_documented or event.activity_documented

    deficient = [
        month for month in run.months
        if not any(qualifies(e) for e in run.events_in(month))
    ]
    if not deficient:
        spans = []
        for event in run.events:
            if qualifies(event):
                spans.extend(s for s in (event.diet_span, event.activity_span) if s)
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.MET,
            spans=spans,
            detail=f"diet and activity documented in all {run.length} month(s)",
        )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.NOT_MET,
        spans=[e.span for month in deficient for e in run.events_in(month)],
        detail=(
            f"{len(deficient)} of {run.length} month(s) lack diet and activity "
            "documentation: " + ", ".join(f"{y}-{m:02d}" for y, m in deficient)
        ),
    )
