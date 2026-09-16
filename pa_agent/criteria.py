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
    Shortfall,
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


def _bmi_series(observations: list[Observation]) -> list[Observation]:
    """BMI observations, oldest first.

    Extracted so reconciliation selects the *same* authoritative value criterion
    (a) did (T-33). Two copies of this ordering would be free to disagree, and
    the disagreement would surface as a discrepancy against a value no verdict
    was ever based on.
    """
    return sorted(
        (o for o in observations if o.code == BMI_LOINC),
        key=lambda o: o.effective_date,
    )


def most_recent_bmi(observations: list[Observation]) -> Observation | None:
    """The observation criterion (a) adjudicates on, or None."""
    series = _bmi_series(observations)
    return series[-1] if series else None


def evaluate_criterion_a(
    criterion: Criterion, observations: list[Observation], as_of: date
) -> CriterionResult:
    """REQ-11: numeric comparison against the most recent BMI within the
    lookback window. The threshold and window come from the tree."""
    threshold = criterion.require("bmi_threshold")
    lookback_months = criterion.require("lookback_months")

    bmis = _bmi_series(observations)
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
            shortfall=Shortfall(
                observed=_months_between(latest.effective_date, as_of),
                required=lookback_months,
                unit="months_since_bmi_observation",
            ),
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
        shortfall=(
            None if met else Shortfall(observed=latest.value, required=threshold, unit="bmi")
        ),
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


def enumerate_runs(events: list[WmEvent]) -> tuple[QualifyingRun, ...]:
    """Every maximal run of consecutive populated months, earliest first (D84).

    Pure structure and no policy: this answers *what runs does this chart
    contain*, and `qualifying_run` answers *which one the criteria adjudicate*.
    Splitting them is what lets the selector require the constants it selects on
    without dragging them into tests that are about run-finding.

    Empty input yields no runs — never one run of length zero, which is an
    abstention everywhere (D12).
    """
    if not events:
        return ()

    months = sorted({_month(e.event_date) for e in events})
    blocks: list[list[tuple[int, int]]] = []
    for month in months:
        if blocks and _month_index(month) == _month_index(blocks[-1][-1]) + 1:
            blocks[-1].append(month)
        else:
            blocks.append([month])

    runs = []
    for block in blocks:
        member = set(block)
        in_run = tuple(
            sorted(
                (e for e in events if _month(e.event_date) in member),
                key=lambda e: e.event_date,
            )
        )
        runs.append(QualifyingRun(months=tuple(block), events=in_run))
    return tuple(runs)


def qualifying_run(
    events: list[WmEvent],
    *,
    min_consecutive_months: int,
    recency_window_months: int,
    as_of: date,
) -> QualifyingRun:
    """The run the criteria adjudicate (REQ-14, REQ-32, D84).

    **Joint selection.** Among the chart's maximal runs, prefer one satisfying
    c3's length *and* c2's recency; among those take the longest, ties to the
    more recent. With no such run, fall back to the longest overall — which is
    T-16's original answer, so a chart with one run, or whose longest run
    already qualifies, is unaffected.

    D48's defect is what this replaces: a six-month run three years ago beat a
    four-month run last month, and c2 then reported a patient stale who had
    completed four consecutive supervised months inside the window. A false
    `NOT_MET` produced by the selection rule rather than by the evidence.

    **The constants are required, deliberately** (D84). An optional
    `recency_window_months` defaulting to "no preference" is a well-formed
    answer for a case nobody supplied — D31's and D63's rule — and a caller that
    forgot it would get the old behaviour with every test agreeing.

    c2, c4 and c5 all scope to whatever this returns (`scoped_to: "c3"`), so the
    run c2 judges is the run c4 and c5 measure. One run, four criteria, one
    period.
    """
    runs = enumerate_runs(events)
    if not runs:
        return QualifyingRun(months=(), events=())

    def _recent(run: QualifyingRun) -> bool:
        last = run.last_event
        return (
            last is not None
            and _months_between(last.event_date, as_of) < recency_window_months
        )

    # Ascending months mean a later run appears later, so `>=` on length breaks
    # ties toward the more recent run — the only reading that can help a patient.
    def _best(candidates: tuple[QualifyingRun, ...]) -> QualifyingRun:
        chosen = candidates[0]
        for run in candidates[1:]:
            if run.length >= chosen.length:
                chosen = run
        return chosen

    jointly = tuple(
        run for run in runs if run.length >= min_consecutive_months and _recent(run)
    )
    return _best(jointly or runs)


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
    if run is None:
        # D84: c3 cannot select its own run any more, because selection needs
        # c2's window and the clock, neither of which a criterion-(c3)-shaped
        # call has. `step_qualifying_run` computes it once and hands it here —
        # the single-run property D48 required. Raising beats defaulting to the
        # longest run: that default is the defect this task removed, and every
        # test would agree with it (D31's shape).
        raise ValueError(
            "c3 requires the qualifying run selected by step_qualifying_run; "
            "selection needs c2's recency window and `as_of` (REQ-32, D84)"
        )

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
        shortfall=(
            None
            if verdict is CriterionVerdict.MET
            else Shortfall(observed=run.length, required=required, unit="consecutive_months")
        ),
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
        shortfall=(
            None
            if verdict is CriterionVerdict.MET
            else Shortfall(observed=months_since, required=window, unit="months_since_run_end")
        ),
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
        shortfall=Shortfall(observed=len(deficient), required=0, unit="months_without_bmi"),
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
        shortfall=Shortfall(
            observed=len(deficient), required=0, unit="months_without_diet_and_activity"
        ),
        detail=(
            f"{len(deficient)} of {run.length} month(s) lack diet and activity "
            "documentation: " + ", ".join(f"{y}-{m:02d}" for y, m in deficient)
        ),
    )


# --------------------------------------------------------------------------
# T-86 (D99): a NOT_MET must re-derive from what it cites
# --------------------------------------------------------------------------


class CitationInsufficient(ValueError):
    """A `NOT_MET` whose own citations do not reproduce it.

    Under Article III that is indistinguishable from a fabricated citation,
    and it is a code defect rather than a fact about the chart — so the
    graph maps it to `ERROR/PREDICATE_EXCEPTION` and aborts (D99), never to
    an abstention.
    """


def restricted_run(run: QualifyingRun, cited: list[EvidenceSpan]) -> QualifyingRun:
    """The qualifying run reduced to the events a result cites.

    Months are re-derived from the kept events rather than copied, so a run
    that cited only some of its months is measured as the shorter run it
    actually evidenced — which is what lets the shortfall comparison catch
    under-citation.
    """
    events = tuple(e for e in run.events if e.span in cited)
    months = tuple(sorted({_month(e.event_date) for e in events}))
    return QualifyingRun(months=months, events=events)


def _span_keys(spans: list[EvidenceSpan]) -> list[tuple[str, int, int]]:
    return sorted((s.document_id, s.char_start, s.char_end) for s in spans)


def check_citation_sufficiency(
    criterion: Criterion,
    result: CriterionResult,
    *,
    observations: list[Observation],
    run: QualifyingRun,
    as_of: date,
    c3_met: bool,
) -> None:
    """Re-run the predicate over only the cited evidence; require the same answer.

    The property (D99): a `NOT_MET` cites the evidence that fell short, so the
    same predicate run over *only* that evidence must yield the same verdict,
    the same span set and the same shortfall. Nothing is re-implemented here —
    the predicate is re-run on a shorter input, which is the only
    implementation of the arithmetic there is (Art. II).

    Comparing the verdict alone would not do: a predicate that cites only the
    first deficient month still re-derives `NOT_MET`. The shortfall is what
    catches under-citation, and it is why `Shortfall` is structured.

    Returns `None` on success. Raises `CitationInsufficient` when the result is
    a `NOT_MET` without a shortfall, a `NOT_MET` this function cannot re-derive,
    or a `NOT_MET` whose citations yield a different answer. Anything that is
    not a `NOT_MET` has no shortfall to check and passes untouched.
    """
    if result.verdict is not CriterionVerdict.NOT_MET:
        return
    if result.shortfall is None:
        raise CitationInsufficient(
            f"{criterion.id}: NOT_MET without a structured shortfall; the "
            "arithmetic behind it cannot be checked (D99)"
        )

    cited = list(result.spans)
    if criterion.id == "a":
        again = evaluate_criterion_a(
            criterion, [o for o in observations if o.span in cited], as_of
        )
    elif criterion.id == "c2":
        again = evaluate_c2(criterion, restricted_run(run, cited), as_of, c3_met)
    elif criterion.id == "c3":
        narrowed = restricted_run(run, cited)
        again = evaluate_c3(criterion, list(narrowed.events), None, narrowed)
    elif criterion.id == "c4":
        again = evaluate_c4(criterion, restricted_run(run, cited), c3_met)
    elif criterion.id == "c5":
        again = evaluate_c5(criterion, restricted_run(run, cited), c3_met)
    else:
        raise CitationInsufficient(
            f"{criterion.id}: NOT_MET on a criterion with no re-derivation "
            "defined; D99 names the exception or the check does not pass it"
        )

    before = (result.verdict, _span_keys(result.spans), result.shortfall)
    after = (again.verdict, _span_keys(again.spans), again.shortfall)
    if before != after:
        raise CitationInsufficient(
            f"{criterion.id}: the cited evidence alone re-derives "
            f"{after[0].value} with shortfall {after[2]} over {len(after[1])} "
            f"span(s); the verdict claimed {before[0].value} with shortfall "
            f"{before[2]} over {len(before[1])} span(s). A NOT_MET must rest on "
            "what it cites (Art. III, D99)."
        )
