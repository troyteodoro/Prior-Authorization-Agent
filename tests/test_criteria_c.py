"""T-16 — c1 through c5, deterministic predicates over `wm_events` (D48).

The edge cases run against **T-15's real recorded extraction**, so the
verdicts are computed from events a model actually produced rather than from
events written to make a predicate pass. The boundary and cascade cases are
synthetic, because no corpus can be regenerated to order.

No model call anywhere here (REQ-13): the recording is read from disk.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriterionVerdict,
    EvidenceSpan,
    GapReason,
    ProgramAssertion,
    WmEvent,
)
from pa_agent.criteria import (
    CitationInsufficient,
    check_citation_sufficiency,
    enumerate_runs,
    evaluate_c1,
    evaluate_c2,
    evaluate_c3,
    evaluate_c4,
    evaluate_c5,
    qualifying_run,
    restricted_run,
)
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "eval" / "extraction" / "results.json"
AS_OF = date(2026, 9, 1)
SPAN = EvidenceSpan(document_id="d", char_start=0, char_end=8, quote="anything")


@pytest.fixture(scope="module")
def tree():
    return LocalPolicyStore().get_tree("ncd-100.1-jf-v1")


@pytest.fixture(scope="module")
def extracted() -> dict[str, dict]:
    """Case id -> the events and assertions T-15 actually extracted."""
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    by_case: dict[str, dict] = {}
    for record in results["notes"]:
        events = [
            WmEvent(
                event_date=date.fromisoformat(e["date"]),
                span=EvidenceSpan.model_validate(e["span"]),
                bmi=e["bmi"],
                bmi_span=(
                    EvidenceSpan.model_validate(e["bmi_span"]) if e["bmi_span"] else None
                ),
                diet_documented=e["diet_documented"],
                diet_span=(
                    EvidenceSpan.model_validate(e["diet_span"]) if e["diet_span"] else None
                ),
                activity_documented=e["activity_documented"],
                activity_span=(
                    EvidenceSpan.model_validate(e["activity_span"])
                    if e["activity_span"] else None
                ),
            )
            for e in record["events"]
        ]
        assertions = [
            ProgramAssertion(
                span=EvidenceSpan.model_validate(a["span"]), text=a["text"]
            )
            for a in record["assertions"]
        ]
        payload = {"events": events, "assertions": assertions, "note": record["note_id"]}
        for case in record.get("cases", []):
            by_case[case] = payload
        by_case[record["note_id"]] = payload
    return by_case


def _select(tree, events, as_of=AS_OF):
    """Selection wired exactly as `step_qualifying_run` wires it (D84).

    The constants are required arguments, so a test cannot accidentally measure
    the pre-D84 longest-run behaviour — which is the point of making them
    required. Reading them from the tree here keeps the wiring in one place.
    """
    return qualifying_run(
        events,
        min_consecutive_months=tree.criterion("c3").require("min_consecutive_months"),
        recency_window_months=tree.criterion("c2").require("recency_window_months"),
        as_of=as_of,
    )


def _evaluate_all(tree, events, assertions, as_of=AS_OF) -> dict[str, object]:
    """The five predicates, wired the way T-18's graph wires them: c3
    identifies the run, and c2, c4, c5 scope to it (D48, D84)."""
    run = _select(tree, events, as_of)
    c3 = evaluate_c3(tree.criterion("c3"), events, assertions, run)
    c3_met = c3.verdict is CriterionVerdict.MET
    return {
        "c1": evaluate_c1(tree.criterion("c1"), events, assertions),
        "c2": evaluate_c2(tree.criterion("c2"), run, as_of, c3_met),
        "c3": c3,
        "c4": evaluate_c4(tree.criterion("c4"), run, c3_met),
        "c5": evaluate_c5(tree.criterion("c5"), run, c3_met),
        "run": run,
    }


def _event(iso: str, *, bmi=None, diet=False, activity=False) -> WmEvent:
    return WmEvent(
        event_date=date.fromisoformat(iso),
        span=SPAN,
        bmi=bmi,
        bmi_span=SPAN if bmi is not None else None,
        diet_documented=diet,
        diet_span=SPAN if diet else None,
        activity_documented=activity,
        activity_span=SPAN if activity else None,
    )


# --------------------------------------------------------------------------
# The edge cases, on real extracted events
# --------------------------------------------------------------------------


def test_e4_three_month_run_is_not_met(tree, extracted):
    """E4: January, February, March, gap in April, then May and June. The
    longest run is three, and three is not four."""
    case = extracted["E4"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert verdicts["run"].length == 3
    assert verdicts["c3"].verdict is CriterionVerdict.NOT_MET
    assert verdicts["c3"].spans, "a NOT_MET cites the run that fell short (REQ-5)"


def test_e9_is_not_fooled_by_the_missed_visits(tree, extracted):
    """E9 is E4's chart: the April no-show and the failed outreach call sit in
    the gap month, and counting either as an encounter turns a run of three
    into a run of six. This is the kill criterion's case."""
    case = extracted["E9"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    april = (2026, 4)
    assert april not in verdicts["run"].months, (
        "the gap month is populated; a missed visit was counted as an encounter"
    )
    assert verdicts["c3"].verdict is CriterionVerdict.NOT_MET


def test_e5_qualifying_run_is_too_old(tree, extracted):
    """E5: a complete four-month run that ended about fourteen months ago.
    c3 `MET`, c2 `NOT_MET` — and `NOT_MET`, not an abstention, because the
    evidence exists and falls outside the window (REQ-16)."""
    case = extracted["E5"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert verdicts["c3"].verdict is CriterionVerdict.MET
    assert verdicts["c2"].verdict is CriterionVerdict.NOT_MET
    assert verdicts["c2"].gap_reason is None, "a NOT_MET is not a gap in the record"
    assert verdicts["c2"].spans, "cites when the run ended"


def test_e6_bmi_documented_in_two_of_four_months(tree, extracted):
    """E6: weight every month, BMI in two. c4 `NOT_MET` while c3 and c5 hold —
    and no BMI is derived from a weight plus an on-file height (D15)."""
    case = extracted["E6"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert verdicts["c3"].verdict is CriterionVerdict.MET
    assert verdicts["c4"].verdict is CriterionVerdict.NOT_MET
    assert verdicts["c5"].verdict is CriterionVerdict.MET, (
        "the case must isolate c4; if c5 fails too the note stopped testing it"
    )
    documented = sum(1 for e in verdicts["run"].events if e.bmi is not None)
    assert documented == 2, f"{documented} months document a BMI, expected 2"


def test_e7_no_documentation_anywhere(tree, extracted):
    """E7: c1 abstains with `NO_EVIDENCE_RETRIEVED`, never `NOT_MET` — nothing
    was found, so nothing failed (REQ-36, Art. IV)."""
    case = extracted["E7"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert case["events"] == []
    assert verdicts["c1"].verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert verdicts["c1"].gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert verdicts["c1"].spans == []


def test_e8_assertion_without_encounters(tree, extracted):
    """E8, the refusal test: zero events and a completion claim. c3 abstains
    with `UNSUBSTANTIATED_ASSERTION` — find the visit notes behind the claim,
    which is a different errand from E7's (D12)."""
    case = extracted["E8"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert case["events"] == []
    assert case["assertions"], "the note must carry a claim for this to be E8"
    assert verdicts["c3"].verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert verdicts["c3"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION
    assert verdicts["c1"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION


def test_e7_and_e8_abstain_for_different_reasons(tree, extracted):
    """The distinction the enum exists for: both charts document zero
    encounters, and Sam's next action differs."""
    e7 = _evaluate_all(tree, extracted["E7"]["events"], extracted["E7"]["assertions"])
    e8 = _evaluate_all(tree, extracted["E8"]["events"], extracted["E8"]["assertions"])
    assert e7["c1"].verdict == e8["c1"].verdict
    assert e7["c1"].gap_reason != e8["c1"].gap_reason


def test_e11_two_programs_and_the_qualifying_one_wins(tree, extracted):
    """E11: a lapsed two-month course in 2024 and a qualifying four-month run
    in 2026. Extraction returns all six encounters (REQ-8 forbids filtering by
    relevance); choosing among them is c3's, in Python."""
    case = extracted["E11"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    assert len(case["events"]) == 6, "extraction must return both programs"
    assert verdicts["run"].length == 4
    assert {m[0] for m in verdicts["run"].months} == {2026}
    assert verdicts["c3"].verdict is CriterionVerdict.MET
    assert verdicts["c2"].verdict is CriterionVerdict.MET


def test_e1_is_a_clean_approval_on_every_criterion(tree, extracted):
    """E1: all five hold. If this one is wrong nothing downstream matters."""
    case = extracted["E1"]
    verdicts = _evaluate_all(tree, case["events"], case["assertions"])
    for name in ("c1", "c2", "c3", "c4", "c5"):
        assert verdicts[name].verdict is CriterionVerdict.MET, (
            f"{name}: {verdicts[name].detail}"
        )
        assert verdicts[name].spans, f"{name}: a MET carries a span (REQ-5)"


# --------------------------------------------------------------------------
# The predicate boundaries, synthetic
# --------------------------------------------------------------------------


def test_c1_is_met_on_a_single_event_and_abstains_on_zero(tree):
    criterion = tree.criterion("c1")
    assert evaluate_c1(criterion, [_event("2026-06-01")]).verdict is CriterionVerdict.MET
    zero = evaluate_c1(criterion, [])
    assert zero.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert zero.verdict is not CriterionVerdict.NOT_MET


@pytest.mark.parametrize("months", [1, 2, 3])
def test_c3_is_not_met_on_a_short_run(tree, months):
    events = [_event(f"2026-0{m + 1}-10") for m in range(months)]
    result = evaluate_c3(tree.criterion("c3"), events, [], _select(tree, events))
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans


def test_c3_abstains_on_zero_events_which_is_not_a_short_run(tree):
    """D12: a run of zero is not a run of length below the minimum. The two
    read alike and send Sam to different places."""
    result = evaluate_c3(tree.criterion("c3"), [], [], _select(tree, []))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.spans == []


def test_c3_is_met_at_exactly_the_minimum(tree):
    events = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    assert (
        evaluate_c3(tree.criterion("c3"), events, [], _select(tree, events)).verdict
        is CriterionVerdict.MET
    )


def test_c2_reads_its_window_from_the_tree(tree):
    """REQ-32: the window is never hardcoded. Eleven months back is inside a
    12-month window and thirteen is outside it."""
    assert tree.criterion("c2").require("recency_window_months") == 12
    recent = _select(tree, [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)])
    stale = _select(tree, [_event(f"2025-0{m}-10") for m in (3, 4, 5, 6)])
    c2 = tree.criterion("c2")
    assert evaluate_c2(c2, recent, AS_OF, True).verdict is CriterionVerdict.MET
    assert evaluate_c2(c2, stale, AS_OF, True).verdict is CriterionVerdict.NOT_MET


def test_c4_and_c5_abstain_when_c3_fails(tree):
    """REQ-15: no qualifying period exists to scope to, so the question cannot
    be answered — not answered negatively."""
    short = [_event("2026-05-10", bmi=40.0, diet=True, activity=True)]
    run = _select(tree, short)
    for name in ("c4", "c5"):
        evaluate = {"c4": evaluate_c4, "c5": evaluate_c5}[name]
        result = evaluate(tree.criterion(name), run, False)
        assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
        assert result.gap_reason is not None
        assert result.spans == []


def test_c5_is_a_rate_not_a_count(tree):
    """T-37 and D24's case: a seven-month run documenting diet and activity in
    four of its months. The old count of 4 called this `MET`."""
    events = [
        _event(f"2026-0{m}-10", bmi=40.0, diet=m <= 4, activity=m <= 4)
        for m in range(1, 8)
    ]
    run = _select(tree, events)
    assert run.length == 7
    result = evaluate_c5(tree.criterion("c5"), run, True)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert "3 of 7" in result.detail


def test_c5_requires_both_diet_and_activity_in_the_same_month(tree):
    """D24 kept c4 and c5 separate criteria because the predicates differ; the
    tree's `requires_both_diet_and_activity` is why a diet-only month fails."""
    both = [_event(f"2026-0{m}-10", diet=True, activity=True) for m in (3, 4, 5, 6)]
    diet_only = [_event(f"2026-0{m}-10", diet=True, activity=(m != 5))
                 for m in (3, 4, 5, 6)]
    c5 = tree.criterion("c5")
    assert evaluate_c5(c5, _select(tree, both), True).verdict is CriterionVerdict.MET
    assert (
        evaluate_c5(c5, _select(tree, diet_only), True).verdict
        is CriterionVerdict.NOT_MET
    )


def test_c4_never_derives_a_bmi_from_a_weight(tree):
    """D15: a month whose encounter records no BMI value fails c4, whatever
    else the chart holds. The predicate has no access to a height and must
    not acquire one."""
    events = [_event(f"2026-0{m}-10", bmi=40.0 if m != 5 else None)
              for m in (3, 4, 5, 6)]
    result = evaluate_c4(tree.criterion("c4"), _select(tree, events), True)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert "2026-05" in result.detail


# --------------------------------------------------------------------------
# The run itself
# --------------------------------------------------------------------------


def test_the_empty_list_yields_no_run(tree):
    """No runs at all — never one run of length zero, which is an abstention
    everywhere (D12). Asserted on the enumerator, since this is about run
    structure and not about which run the criteria adjudicate (D84)."""
    assert enumerate_runs([]) == ()
    assert _select(tree, []).length == 0


def test_events_out_of_order_produce_the_same_run(tree):
    ordered = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
    assert enumerate_runs(ordered) == enumerate_runs(shuffled)


def test_several_events_in_one_month_are_one_month(tree):
    events = [_event("2026-03-02"), _event("2026-03-20"), _event("2026-04-10")]
    assert [r.length for r in enumerate_runs(events)] == [2]


def test_a_run_crossing_a_year_boundary_is_consecutive(tree):
    events = [_event(d) for d in ("2025-11-10", "2025-12-10", "2026-01-10", "2026-02-10")]
    assert [r.length for r in enumerate_runs(events)] == [4]


def test_ties_go_to_the_more_recent_run(tree):
    """D48: REQ-14 does not say which longest run, and the recent one is the
    only reading that can help a patient under c2."""
    events = [_event(d) for d in
              ("2024-01-10", "2024-02-10", "2026-05-10", "2026-06-10")]
    assert [r.length for r in enumerate_runs(events)] == [2, 2]
    run = _select(tree, events)
    assert run.length == 2
    assert run.months == ((2026, 5), (2026, 6))


def test_a_recent_qualifying_run_beats_a_longer_stale_one(tree):
    """D48's defect, resolved by T-42 and D84. **This test was inverted, on
    purpose** — it asserted `NOT_MET` and said "if this now passes, REQ-14's
    selection changed; that is T-42". It did, and it is.

    Selection is now joint: among the chart's maximal runs, prefer one
    satisfying c3's length *and* c2's recency. The six-month run in 2023 is
    longer; the four-month run inside the window is the one that qualifies, so
    it is the one all four scoped criteria adjudicate.
    """
    old = [_event(f"2023-0{m}-10") for m in (1, 2, 3, 4, 5, 6)]
    recent = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    events = old + recent

    assert [r.length for r in enumerate_runs(events)] == [6, 4], (
        "the chart still holds both runs; selection chooses between them and "
        "does not discard one"
    )

    run = qualifying_run(
        events,
        min_consecutive_months=tree.criterion("c3").require("min_consecutive_months"),
        recency_window_months=tree.criterion("c2").require("recency_window_months"),
        as_of=AS_OF,
    )
    assert run.length == 4 and run.months[0][0] == 2026

    c3 = evaluate_c3(tree.criterion("c3"), events, [], run)
    c2 = evaluate_c2(tree.criterion("c2"), run, AS_OF, True)
    assert c3.verdict is CriterionVerdict.MET
    assert c2.verdict is CriterionVerdict.MET, (
        "the patient completed four consecutive supervised months inside the "
        "window; reporting that stale was a false NOT_MET produced by the "
        "selection rule rather than by the evidence (D84)"
    )


def test_the_longest_run_still_wins_when_no_run_qualifies_jointly(tree):
    """The fallback, which is T-16's original answer and the reason joint
    selection changes nothing on a chart with one program. Both runs here are
    stale, so there is no jointly-qualifying candidate and length decides."""
    old = [_event(f"2022-0{m}-10") for m in (1, 2, 3, 4, 5, 6)]
    older_short = [_event(f"2020-0{m}-10") for m in (3, 4, 5, 6)]
    events = older_short + old

    run = qualifying_run(
        events,
        min_consecutive_months=tree.criterion("c3").require("min_consecutive_months"),
        recency_window_months=tree.criterion("c2").require("recency_window_months"),
        as_of=AS_OF,
    )
    assert run.length == 6 and run.months[0][0] == 2022
    c2 = evaluate_c2(tree.criterion("c2"), run, AS_OF, True)
    assert c2.verdict is CriterionVerdict.NOT_MET


def test_c3_refuses_to_select_its_own_run(tree):
    """D84: selection needs c2's window and the clock, which a c3-shaped call
    does not have. Defaulting to the longest run would be the defect this task
    removed, reinstated as a fallback nobody could see (D31's shape)."""
    events = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    with pytest.raises(ValueError, match="step_qualifying_run"):
        evaluate_c3(tree.criterion("c3"), events, [], None)


def test_the_scoped_criteria_all_read_the_run_c2_judged(tree):
    """One run, four criteria, one period — preserved by joint selection rather
    than created by it (D48's single-run property, D84's ruling on scoping).

    The rejected alternative — keep longest for c3, let c2 consider every run —
    would break exactly this: c2 answers about the recent program while c4 and
    c5 measure the old one, and the gap list names months the recency verdict
    never looked at.
    """
    old = [_event(f"2023-0{m}-10", bmi=40.0, diet=True, activity=True)
           for m in (1, 2, 3, 4, 5, 6)]
    recent = [_event(f"2026-0{m}-10", bmi=40.0, diet=True, activity=True)
              for m in (3, 4, 5, 6)]
    run = _select(tree, old + recent)

    assert run.months[0][0] == 2026
    for name in ("c4", "c5"):
        result = {"c4": evaluate_c4, "c5": evaluate_c5}[name](
            tree.criterion(name), run, True
        )
        assert result.verdict is CriterionVerdict.MET
        assert "4 month" in result.detail, (
            f"{name} reports {result.detail!r}; c2 judged a four-month run and "
            "the scoped criteria must measure that one, not the six-month run "
            "from 2023"
        )
        assert "6 month" not in result.detail


# --------------------------------------------------------------------------
# Determinism (Art. II)
# --------------------------------------------------------------------------


def test_identical_verdicts_across_three_runs(tree, extracted):
    """The same events must yield the same verdicts every time; if they do
    not, a computation is in the wrong place."""
    for case_id in ("E1", "E4", "E5", "E6", "E7", "E8", "E11"):
        case = extracted[case_id]
        runs = [
            {
                name: (r.verdict, r.gap_reason, tuple((s.char_start, s.char_end) for s in r.spans))
                for name, r in _evaluate_all(
                    tree, case["events"], case["assertions"]
                ).items()
                if name != "run"
            }
            for _ in range(3)
        ]
        assert runs[0] == runs[1] == runs[2], case_id


def test_no_model_is_reachable_from_the_predicates():
    """REQ-13: no model call after extraction. Asserted on the module's
    imports, the way T-13's gate does."""
    import ast

    module = REPO_ROOT / "pa_agent" / "criteria.py"
    tree_ = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"__future__", "dataclasses", "datetime", "pa_agent.contracts"}, (
        f"pa_agent/criteria.py imports {sorted(imported)}; the predicates are "
        "arithmetic over contracts and nothing else (REQ-13, Art. II)"
    )


# --------------------------------------------------------------------------
# T-86 (D99): a NOT_MET re-derives from its own citations
# --------------------------------------------------------------------------


def _check(tree, results: dict[str, object], criterion_id: str, as_of=AS_OF) -> None:
    """`check_citation_sufficiency` wired as `step_sufficiency` wires it."""
    check_citation_sufficiency(
        tree.criterion(criterion_id),
        results[criterion_id],
        observations=[],
        run=results["run"],
        as_of=as_of,
        c3_met=results["c3"].verdict is CriterionVerdict.MET,
    )


def _span(n: int) -> EvidenceSpan:
    return EvidenceSpan(document_id="d", char_start=n * 10, char_end=n * 10 + 8, quote="anything")


def _event_at(iso: str, n: int, *, bmi=None, diet=False, activity=False) -> WmEvent:
    """An event with a span of its own, so a citation can be told apart."""
    return WmEvent(
        event_date=date.fromisoformat(iso),
        span=_span(n),
        bmi=bmi,
        bmi_span=_span(n) if bmi is not None else None,
        diet_documented=diet,
        diet_span=_span(n) if diet else None,
        activity_documented=activity,
        activity_span=_span(n) if activity else None,
    )


def test_every_recorded_not_met_passes_the_sufficiency_check(tree, extracted):
    """On T-15's real events every shortfall verdict re-derives from what it
    cites — which is the property by construction, and the reason
    `eval/baseline.json` does not move under T-86."""
    seen = 0
    for case in extracted.values():
        results = _evaluate_all(tree, case["events"], case["assertions"])
        for cid in ("c2", "c3", "c4", "c5"):
            if results[cid].verdict is CriterionVerdict.NOT_MET:
                assert results[cid].shortfall is not None, f"{cid}: NOT_MET without a shortfall"
                _check(tree, results, cid)
                seen += 1
    assert seen >= 3, "the corpus carries shortfall verdicts (E4, E5, E6) or this test checks nothing"


def test_a_verdict_that_is_not_a_shortfall_has_nothing_to_check(tree):
    """Only a NOT_MET fell short of anything; the check passes the rest
    untouched rather than re-running them."""
    events = [_event_at(f"2026-0{m}-05", m, bmi=40.0, diet=True, activity=True) for m in range(4, 9)]
    results = _evaluate_all(tree, events, [])
    for cid in ("c1", "c2", "c3", "c4", "c5"):
        assert results[cid].verdict is CriterionVerdict.MET
        assert results[cid].shortfall is None
        _check(tree, results, cid)


def test_a_not_met_without_a_structured_shortfall_is_refused(tree):
    events = [_event_at(f"2026-0{m}-05", m, bmi=40.0) for m in range(6, 9)]  # three months: c3 short
    results = _evaluate_all(tree, events, [])
    assert results["c3"].verdict is CriterionVerdict.NOT_MET
    results["c3"] = results["c3"].model_copy(update={"shortfall": None})
    with pytest.raises(CitationInsufficient, match="without a structured shortfall"):
        _check(tree, results, "c3")


def test_c4_citing_only_the_first_deficient_month_is_caught(tree):
    """The mutation the shortfall exists to catch: the verdict alone would
    re-derive NOT_MET from one month, and only the count says two were
    claimed."""
    events = [
        _event_at("2026-05-05", 1, bmi=40.0),
        _event_at("2026-06-05", 2),           # no BMI
        _event_at("2026-07-05", 3, bmi=39.0),
        _event_at("2026-08-05", 4),           # no BMI
    ]
    results = _evaluate_all(tree, events, [])
    c4 = results["c4"]
    assert c4.verdict is CriterionVerdict.NOT_MET and c4.shortfall.observed == 2
    _check(tree, results, "c4")  # honest citation passes
    results["c4"] = c4.model_copy(update={"spans": c4.spans[:1]})
    with pytest.raises(CitationInsufficient, match="c4: the cited evidence alone re-derives NOT_MET"):
        _check(tree, results, "c4")


def test_c3_citing_half_its_run_is_caught(tree):
    events = [_event_at(f"2026-0{m}-05", m, bmi=40.0) for m in range(6, 9)]  # 3 of 4 required
    results = _evaluate_all(tree, events, [])
    c3 = results["c3"]
    assert c3.verdict is CriterionVerdict.NOT_MET and c3.shortfall.observed == 3
    _check(tree, results, "c3")
    results["c3"] = c3.model_copy(update={"spans": c3.spans[:2]})
    with pytest.raises(CitationInsufficient, match="shortfall observed=2.0"):
        _check(tree, results, "c3")


def test_c2_citing_the_first_event_instead_of_the_last_is_caught(tree):
    """A stale run: c2 cites the run's last event. Citing an earlier one
    re-derives a *larger* staleness, so the shortfall moves and the check
    refuses it."""
    events = [_event_at(f"2024-0{m}-05", m, bmi=40.0) for m in range(3, 8)]  # five months, 2024
    results = _evaluate_all(tree, events, [])
    c2 = results["c2"]
    assert results["c3"].verdict is CriterionVerdict.MET
    assert c2.verdict is CriterionVerdict.NOT_MET
    _check(tree, results, "c2")
    results["c2"] = c2.model_copy(update={"spans": [events[0].span]})
    with pytest.raises(CitationInsufficient, match="c2: the cited evidence alone"):
        _check(tree, results, "c2")


def test_c5_citing_only_one_deficient_month_is_caught(tree):
    events = [
        _event_at("2026-05-05", 1, bmi=40.0, diet=True, activity=True),
        _event_at("2026-06-05", 2, bmi=40.0, diet=True),               # no activity
        _event_at("2026-07-05", 3, bmi=40.0, diet=True, activity=True),
        _event_at("2026-08-05", 4, bmi=40.0, activity=True),           # no diet
    ]
    results = _evaluate_all(tree, events, [])
    c5 = results["c5"]
    assert c5.verdict is CriterionVerdict.NOT_MET and c5.shortfall.observed == 2
    _check(tree, results, "c5")
    results["c5"] = c5.model_copy(update={"spans": c5.spans[1:]})
    with pytest.raises(CitationInsufficient):
        _check(tree, results, "c5")


def test_restricted_run_re_derives_its_months_from_the_kept_events(tree):
    events = [_event_at(f"2026-0{m}-05", m, bmi=40.0) for m in range(5, 9)]
    run = _select(tree, events)
    narrowed = restricted_run(run, [events[1].span, events[3].span])
    assert narrowed.months == ((2026, 6), (2026, 8))
    assert [e.span for e in narrowed.events] == [events[1].span, events[3].span]


# --------------------------------------------------------------------------
# T-87 (D101): a tree with no run-length criterion passes None, explicitly
# --------------------------------------------------------------------------


def test_the_run_length_argument_stays_required():
    """D84: an argument a caller can forget is a default nobody chose. `None`
    is admitted only when spelled out."""
    with pytest.raises(TypeError):
        qualifying_run([], recency_window_months=12, as_of=AS_OF)


def test_with_no_length_requirement_selection_prefers_recency_alone(tree):
    """Palmetto's shape: a one-month run last month beats a three-month run
    two years ago, because nothing says a run must be long and something says
    it must be recent (D101). Under Noridian's four-month floor neither run
    qualifies jointly and the longer, stale one wins (D84's fallback)."""
    stale = [_event_at(f"2024-0{m}-05", m, bmi=40.0) for m in range(3, 6)]
    recent = [_event_at("2026-08-05", 9, bmi=40.0)]
    events = stale + recent
    window = tree.criterion("c2").require("recency_window_months")
    palmetto = qualifying_run(events, min_consecutive_months=None, recency_window_months=window, as_of=AS_OF)
    noridian = qualifying_run(events, min_consecutive_months=4, recency_window_months=window, as_of=AS_OF)
    assert palmetto.months == ((2026, 8),)
    assert noridian.months == ((2024, 3), (2024, 4), (2024, 5))
