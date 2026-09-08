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
    evaluate_c1,
    evaluate_c2,
    evaluate_c3,
    evaluate_c4,
    evaluate_c5,
    qualifying_run,
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


def _evaluate_all(tree, events, assertions, as_of=AS_OF) -> dict[str, object]:
    """The five predicates, wired the way T-18's graph will wire them: c3
    identifies the run, and c2, c4, c5 scope to it (D48)."""
    run = qualifying_run(events)
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
    result = evaluate_c3(tree.criterion("c3"), events, [])
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans


def test_c3_abstains_on_zero_events_which_is_not_a_short_run(tree):
    """D12: a run of zero is not a run of length below the minimum. The two
    read alike and send Sam to different places."""
    result = evaluate_c3(tree.criterion("c3"), [], [])
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.spans == []


def test_c3_is_met_at_exactly_the_minimum(tree):
    events = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    assert evaluate_c3(tree.criterion("c3"), events, []).verdict is CriterionVerdict.MET


def test_c2_reads_its_window_from_the_tree(tree):
    """REQ-32: the window is never hardcoded. Eleven months back is inside a
    12-month window and thirteen is outside it."""
    assert tree.criterion("c2").require("recency_window_months") == 12
    recent = qualifying_run([_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)])
    stale = qualifying_run([_event(f"2025-0{m}-10") for m in (3, 4, 5, 6)])
    c2 = tree.criterion("c2")
    assert evaluate_c2(c2, recent, AS_OF, True).verdict is CriterionVerdict.MET
    assert evaluate_c2(c2, stale, AS_OF, True).verdict is CriterionVerdict.NOT_MET


def test_c4_and_c5_abstain_when_c3_fails(tree):
    """REQ-15: no qualifying period exists to scope to, so the question cannot
    be answered — not answered negatively."""
    short = [_event("2026-05-10", bmi=40.0, diet=True, activity=True)]
    run = qualifying_run(short)
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
    run = qualifying_run(events)
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
    assert evaluate_c5(c5, qualifying_run(both), True).verdict is CriterionVerdict.MET
    assert (
        evaluate_c5(c5, qualifying_run(diet_only), True).verdict
        is CriterionVerdict.NOT_MET
    )


def test_c4_never_derives_a_bmi_from_a_weight(tree):
    """D15: a month whose encounter records no BMI value fails c4, whatever
    else the chart holds. The predicate has no access to a height and must
    not acquire one."""
    events = [_event(f"2026-0{m}-10", bmi=40.0 if m != 5 else None)
              for m in (3, 4, 5, 6)]
    result = evaluate_c4(tree.criterion("c4"), qualifying_run(events), True)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert "2026-05" in result.detail


# --------------------------------------------------------------------------
# The run itself
# --------------------------------------------------------------------------


def test_the_empty_list_yields_no_run(tree):
    assert qualifying_run([]).length == 0


def test_events_out_of_order_produce_the_same_run(tree):
    ordered = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    shuffled = [ordered[2], ordered[0], ordered[3], ordered[1]]
    assert qualifying_run(ordered).months == qualifying_run(shuffled).months


def test_several_events_in_one_month_are_one_month(tree):
    events = [_event("2026-03-02"), _event("2026-03-20"), _event("2026-04-10")]
    assert qualifying_run(events).length == 2


def test_a_run_crossing_a_year_boundary_is_consecutive(tree):
    events = [_event(d) for d in ("2025-11-10", "2025-12-10", "2026-01-10", "2026-02-10")]
    assert qualifying_run(events).length == 4


def test_ties_go_to_the_more_recent_run(tree):
    """D48: REQ-14 does not say which longest run, and the recent one is the
    only reading that can help a patient under c2."""
    events = [_event(d) for d in
              ("2024-01-10", "2024-02-10", "2026-05-10", "2026-06-10")]
    run = qualifying_run(events)
    assert run.length == 2
    assert run.months == ((2026, 5), (2026, 6))


def test_the_longest_run_wins_even_when_an_older_one_is_stale(tree):
    """D48's known defect, pinned so T-42 changes it deliberately rather than
    by accident: REQ-14 selects the longest run, and c2 then asks whether
    *that* run is recent. A six-month run three years ago beats a four-month
    run last month, and the patient reads as stale."""
    old = [_event(f"2023-0{m}-10") for m in (1, 2, 3, 4, 5, 6)]
    recent = [_event(f"2026-0{m}-10") for m in (3, 4, 5, 6)]
    run = qualifying_run(old + recent)
    assert run.length == 6 and run.months[0][0] == 2023
    c2 = evaluate_c2(tree.criterion("c2"), run, AS_OF, True)
    assert c2.verdict is CriterionVerdict.NOT_MET, (
        "if this now passes, REQ-14's selection changed — that is T-42, and it "
        "needs its own decision entry, not a quiet fix here"
    )


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
