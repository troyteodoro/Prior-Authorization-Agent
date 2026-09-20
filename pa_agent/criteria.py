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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date

from pa_agent.contracts import (
    CategoricalExclusion,
    CodedValueSet,
    Condition,
    Criterion,
    CriterionResult,
    CriterionVerdict,
    EvidenceSpan,
    ExclusionKind,
    ExclusionMatch,
    GapReason,
    Medication,
    Observation,
    PredicateKind,
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
    criterion: Criterion, conditions: list[Condition], value_set: CodedValueSet
) -> CriterionResult:
    """REQ-12: set intersection between the patient's active condition codes
    and the comorbidity value set. `MET` or abstention — never `NOT_MET`.

    Membership is tested **within the set's declared system** since T-92
    (REQ-59, D111). Measured before the comparison was narrowed: of the 421
    conditions in the committed bundles, 419 are SNOMED and the two that are
    not are resolved dental ICD-10 codes outside every value set, so no
    verdict, span or eval row moves.
    """
    minimum = criterion.require("min_comorbidity_count")

    qualifying = [
        c
        for c in conditions
        if c.clinical_status == ACTIVE_STATUS and value_set.admits(c.code, c.system)
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


def _active_members(
    medications: list[Medication], value_set: CodedValueSet
) -> list[Medication]:
    """The active medications this value set admits, in the store's order.

    `status == "active"` is the filter, and it is the predicate's judgment
    rather than the adapter's (D31, D39): `get_medications` reports every
    `MedicationRequest` the bundle carries, and what counts as on the chart
    now is decided here, once, for both medication kinds.
    """
    return [
        m
        for m in medications
        if m.status == ACTIVE_STATUS and value_set.admits(m.code, m.system)
    ]


def evaluate_medication_present(
    criterion: Criterion, medications: list[Medication], value_set: CodedValueSet
) -> CriterionResult:
    """T-92: the document requires a concurrent drug. `MET` or abstention.

    D40's asymmetry, and for D40's reason. L35677 covers infliximab for
    rheumatoid arthritis *"when used in combination with methotrexate"*, and a
    chart with no active methotrexate has not recorded that the patient is not
    taking it — the document's own note covers patients *"unable to tolerate
    methotrexate"* whose reason is documented in the record, and a `NOT_MET`
    would deny them on a chart that does not disagree (D111).
    """
    minimum = criterion.require("min_medication_count")
    value_set_id = criterion.require("value_set_id")

    qualifying = _active_members(medications, value_set)
    if len(qualifying) >= minimum:
        return CriterionResult(
            criterion_id=criterion.id,
            verdict=CriterionVerdict.MET,
            spans=[m.span for m in qualifying if m.span is not None],
            detail=(
                f"active medications in {value_set_id}: "
                + ", ".join(sorted(m.code for m in qualifying))
            ),
        )
    return CriterionResult(
        criterion_id=criterion.id,
        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
        gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
        detail=(
            f"{len(qualifying)} active medication(s) in {value_set_id}; "
            f"{minimum} required. Absence of a prescription is not evidence "
            "the patient is not taking the drug, and this document covers a "
            "documented reason it was not prescribed (D40's shape, D111)."
        ),
    )


def evaluate_excluded_medication(
    exclusion: CategoricalExclusion,
    medications: list[Medication],
    value_set: CodedValueSet,
) -> ExclusionMatch | None:
    """T-92: the policy denies the procedure when a named drug is on the chart.

    L35677's LIMITATIONS paragraph. Fires on positive evidence and cites every
    prescription that fired it; `None` when nothing matches, which is not an
    abstention — an exclusion is not a criterion and has no verdict to abstain
    with (D41's shape, D111).

    This is where an absence stops needing a citation. Written as a criterion
    the same rule would have to answer `MET` for a clean chart, and REQ-5
    refuses a `MET` with no span, because there is no span for an absence.
    """
    offending = [
        m
        for m in medications
        if m.status == ACTIVE_STATUS and value_set.admits(m.code, m.system)
    ]
    spans = [m.span for m in offending if m.span is not None]
    if not spans:
        # Every match with no citable span is the same as no match: Article III
        # admits no uncited denial, and `ExclusionMatch` requires at least one
        # span rather than letting one be constructed without (D41).
        return None
    return ExclusionMatch(
        exclusion_id=exclusion.id, claim=exclusion.claim, evidence=spans
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
    min_consecutive_months: int | None,
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

    `min_consecutive_months=None` is admitted **explicitly** and means the tree
    declares no run-length criterion — Palmetto's L34576 states none (T-87,
    D101). The argument stays required: the graph passes `None` only when the
    tree has no `c3`, so the data decides and a caller still cannot forget.
    With no length to prefer, joint selection prefers recency alone.
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
        run
        for run in runs
        if (min_consecutive_months is None or run.length >= min_consecutive_months)
        and _recent(run)
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
# T-91 (REQ-57, D110): the engine's vocabulary
#
# One entry per predicate above. The dict is what "the engine can evaluate
# this kind" *means* — `workflow` resolves every criterion through it, so a
# kind with no entry cannot be dispatched, and a predicate with no entry
# cannot be reached. `tests/test_predicate_kinds.py` requires the coverage to
# be exact in both directions.
# --------------------------------------------------------------------------


class UnknownPredicateKind(LookupError):
    """A criterion declaring a kind this engine does not implement.

    Raised rather than abstained on. An abstention would say the *chart* did
    not support the criterion, when the fact is about the system — D90's
    distinction, and the reason the tree has a separate way to declare a limit
    it has reviewed (`evaluation: "unclaimed"`, REQ-58). Unbuilt is not
    unclaimed.
    """


@dataclass(frozen=True)
class PredicateInputs:
    """Everything a predicate could need, bound to the fields it actually gets.

    Predicates keep their narrow signatures on purpose: `evaluate_c4(criterion,
    run, run_established)` **cannot** read the structured BMI, and E10b depends
    on criterion (a)'s reading and the note's being two independent readings
    (D62). Handing every predicate one bundle would turn that from a signature
    into a convention. So this object is the *dispatcher's* argument, and each
    binder in `PREDICATES` names the slice its predicate receives.
    """

    as_of: date
    observations: tuple[Observation, ...] = ()
    conditions: tuple[Condition, ...] = ()
    medications: tuple[Medication, ...] = ()
    #: `{value_set_id -> the set}`. Plural since T-92: one tree declares three,
    #: and each membership criterion names its own in a constant (D111).
    value_sets: Mapping[str, CodedValueSet] = field(default_factory=dict)
    events: tuple[WmEvent, ...] = ()
    assertions: tuple[ProgramAssertion, ...] = ()
    run: QualifyingRun | None = None
    #: Whether the criterion under evaluation has a run to be scoped to
    #: (REQ-15). Computed per criterion from its `scoped_to`, by the graph.
    run_established: bool = False


Predicate = Callable[[Criterion, PredicateInputs], CriterionResult]


def _value_set(criterion: Criterion, inputs: PredicateInputs) -> CodedValueSet:
    """The set this criterion names, or a raise naming what was not gathered.

    A missing set is a retrieval fault, not an empty set: an empty one would
    make the criterion abstain about codes nothing looked for, which is D39's
    empty list one layer in.
    """
    value_set_id = criterion.require("value_set_id")
    try:
        return inputs.value_sets[value_set_id]
    except KeyError:
        raise ValueError(
            f"criterion {criterion.id} names value set {value_set_id!r}, which "
            f"the planner did not gather (it gathered {sorted(inputs.value_sets)}). "
            "An empty set here would abstain about codes nothing looked for (D39)."
        ) from None


def _require_run(inputs: PredicateInputs) -> QualifyingRun:
    """The run, or a raise naming the step that was skipped.

    `step_qualifying_run` precedes every run-scoped criterion in `STEPS`. A
    `None` here means the graph changed shape, and a predicate that invented an
    empty run would answer `NOT_MET` about a period nothing computed.
    """
    if inputs.run is None:
        raise ValueError(
            "this predicate is scoped to the qualifying run, which "
            "step_qualifying_run computes before criteria_c (D48, D84)"
        )
    return inputs.run


#: kind -> the call. Read this as the specification of what each kind consumes.
PREDICATES: dict[PredicateKind, Predicate] = {
    PredicateKind.BMI_OBSERVATION_THRESHOLD: lambda c, i: evaluate_criterion_a(
        c, list(i.observations), i.as_of
    ),
    PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP: lambda c, i: evaluate_criterion_b(
        c, list(i.conditions), _value_set(c, i)
    ),
    PredicateKind.MEDICATION_VALUE_SET_ACTIVE: lambda c, i: evaluate_medication_present(
        c, list(i.medications), _value_set(c, i)
    ),
    PredicateKind.NOTE_EVENT_COUNT: lambda c, i: evaluate_c1(
        c, list(i.events), list(i.assertions)
    ),
    PredicateKind.NOTE_EVENT_RUN_LENGTH: lambda c, i: evaluate_c3(
        c, list(i.events), list(i.assertions), _require_run(i)
    ),
    PredicateKind.NOTE_EVENT_RUN_RECENCY: lambda c, i: evaluate_c2(
        c, _require_run(i), i.as_of, i.run_established
    ),
    PredicateKind.NOTE_EVENT_RUN_BMI_RATE: lambda c, i: evaluate_c4(
        c, _require_run(i), i.run_established
    ),
    PredicateKind.NOTE_EVENT_RUN_BEHAVIOR_RATE: lambda c, i: evaluate_c5(
        c, _require_run(i), i.run_established
    ),
}


def evaluate(criterion: Criterion, inputs: PredicateInputs) -> CriterionResult:
    """Run the predicate the criterion declares.

    The single dispatch point, and the backstop for the two checks in front of
    it: the contract rejects an unknown kind at load, and the suite rejects a
    kind `PREDICATES` does not cover. Deleting an entry from `PREDICATES` is
    the mutation that proves this raise fires rather than a criterion silently
    evaluating to nothing.
    """
    if criterion.kind is None:
        raise UnknownPredicateKind(
            f"criterion {criterion.id} declares no kind; only a criterion the "
            "tree declares deterministic is dispatched (REQ-57, D110)"
        )
    try:
        predicate = PREDICATES[criterion.kind]
    except KeyError:
        raise UnknownPredicateKind(
            f"criterion {criterion.id} declares kind {criterion.kind.value!r}, "
            "which this engine does not implement. Unbuilt is not unclaimed: "
            "either write the predicate or declare the criterion unclaimed with "
            "a note (REQ-57, REQ-58, D110)"
        ) from None
    return predicate(criterion, inputs)


class UnknownExclusionKind(LookupError):
    """An exclusion declaring a kind this engine does not implement.

    `UnknownPredicateKind`'s counterpart, and the more dangerous of the two:
    an unimplemented *criterion* that quietly produced nothing would approve
    past a requirement, and an unimplemented *exclusion* that quietly produced
    nothing would approve past a denial the policy states outright. Both raise.
    """


@dataclass(frozen=True)
class ExclusionInputs:
    """What the exclusion dispatcher is handed, sliced per kind by its binder.

    `PredicateInputs`' counterpart and for its reason: each binder names the
    slice its evaluator receives, so an evaluator cannot reach a fact nobody
    decided to give it (D62's rule about narrow signatures).
    """

    as_of: date
    observations: tuple[Observation, ...] = ()
    conditions: tuple[Condition, ...] = ()
    medications: tuple[Medication, ...] = ()
    value_sets: Mapping[str, CodedValueSet] = field(default_factory=dict)
    #: Criterion (a)'s window, borrowed by the BMI exclusion — one window, one
    #: constant (D41). `None` when the tree declares no BMI criterion, which is
    #: the shape every non-bariatric tree takes; the binder that needs it
    #: raises rather than choosing a default.
    lookback_months: int | None = None


def _require_lookback(inputs: ExclusionInputs) -> int:
    if inputs.lookback_months is None:
        raise ValueError(
            "this exclusion is scoped to criterion (a)'s lookback window, and "
            "the tree declares no bmi_observation_threshold criterion to read "
            "it from. A default window here would categorically deny on "
            "evidence of unknown age (D41, D84)."
        )
    return inputs.lookback_months


def _exclusion_value_set(
    exclusion: CategoricalExclusion, inputs: ExclusionInputs
) -> CodedValueSet:
    value_set_id = exclusion.constants["value_set_id"].value
    try:
        return inputs.value_sets[str(value_set_id)]
    except KeyError:
        raise ValueError(
            f"exclusion {exclusion.id} names value set {value_set_id!r}, which "
            f"was not gathered (gathered {sorted(inputs.value_sets)}). An empty "
            "set here would let a stated denial pass silently."
        ) from None


Exclusion = Callable[[CategoricalExclusion, ExclusionInputs], "ExclusionMatch | None"]

#: kind -> the call. `PREDICATES`' counterpart (T-92, D111).
EXCLUSIONS: dict[ExclusionKind, Exclusion] = {
    ExclusionKind.BMI_BELOW_BOUND_WITH_ACTIVE_CONDITION: lambda e, i: evaluate_sc2(
        e, list(i.observations), list(i.conditions), i.as_of, _require_lookback(i)
    ),
    ExclusionKind.ACTIVE_MEDICATION_VALUE_SET: lambda e, i: evaluate_excluded_medication(
        e, list(i.medications), _exclusion_value_set(e, i)
    ),
}


def evaluate_exclusion(
    exclusion: CategoricalExclusion, inputs: ExclusionInputs
) -> ExclusionMatch | None:
    """Run the evaluator the exclusion declares, or raise naming the kind."""
    try:
        evaluator = EXCLUSIONS[exclusion.kind]
    except KeyError:
        raise UnknownExclusionKind(
            f"exclusion {exclusion.id} declares kind {exclusion.kind.value!r}, "
            "which this engine does not implement. Skipping it would approve "
            "past a denial the policy states (D111)."
        ) from None
    return evaluator(exclusion, inputs)


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


def _cited_observations(
    inputs: PredicateInputs, cited: list[EvidenceSpan]
) -> PredicateInputs:
    return replace(
        inputs, observations=tuple(o for o in inputs.observations if o.span in cited)
    )


def _cited_run(inputs: PredicateInputs, cited: list[EvidenceSpan]) -> PredicateInputs:
    narrowed = restricted_run(_require_run(inputs), cited)
    return replace(inputs, run=narrowed, events=tuple(narrowed.events))


#: kind -> how to reduce the inputs to what a verdict cited (T-86, D99; D110).
#: `NOTE_EVENT_COUNT` and `CONDITION_VALUE_SET_MEMBERSHIP` are absent because
#: neither can answer `NOT_MET` — both abstain instead (D13, D40) — so a
#: `NOT_MET` from either is already a defect, and it is reported as one.
NARROWERS: dict[
    PredicateKind, Callable[[PredicateInputs, list[EvidenceSpan]], PredicateInputs]
] = {
    PredicateKind.BMI_OBSERVATION_THRESHOLD: _cited_observations,
    PredicateKind.NOTE_EVENT_RUN_LENGTH: _cited_run,
    PredicateKind.NOTE_EVENT_RUN_RECENCY: _cited_run,
    PredicateKind.NOTE_EVENT_RUN_BMI_RATE: _cited_run,
    PredicateKind.NOTE_EVENT_RUN_BEHAVIOR_RATE: _cited_run,
}


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
    inputs = PredicateInputs(
        as_of=as_of,
        observations=tuple(observations),
        events=tuple(run.events),
        run=run,
        run_established=c3_met,
    )
    # T-91 (D110): narrowing is declared per kind, like the predicate itself.
    # A kind that can answer `NOT_MET` and has no narrower would otherwise
    # re-derive over the full chart and agree with itself — a check that passes
    # because it checked nothing, which is the shape D31 refuses.
    narrow = NARROWERS.get(criterion.kind) if criterion.kind is not None else None
    if narrow is None:
        raise CitationInsufficient(
            f"{criterion.id}: NOT_MET on a criterion of kind "
            f"{criterion.kind.value if criterion.kind else None!r} with no "
            "re-derivation defined; D99 names the exception or the check does "
            "not pass it"
        )
    again = evaluate(criterion, narrow(inputs, cited))

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
