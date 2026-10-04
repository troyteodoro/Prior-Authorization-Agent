"""T-108 — the fourth practice's tree and its two predicates (REQ-73, REQ-79, D150).

`pap-osa-dme-jd-v1` is compiled from L33718, the DME MACs' joint PAP LCD, as
Noridian operationalizes it in DME Jurisdiction D, with NCD 240.4 as the
national layer. This file holds the tree and the arithmetic on charts written
**here**; `tests/test_sleep_apnea_corpus.py` holds the committed charts. D114's
division: the corpus has six charts, and six charts cannot reach every branch —
no committed chart has an evaluation only after its study, because each chart's
own record has an assessment before it, and a note claiming otherwise would
contradict the bundle (D150 clause 10).

Every branch of both predicates is exercised in both directions, and each
`NOT_MET` is put through `check_citation_sufficiency`, which re-derives it from
what it cites (D99).
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    ClinicalEvaluation,
    CodedValueSet,
    Condition,
    CoverageStatus,
    CriteriaTree,
    CriterionVerdict,
    DocumentedFinding,
    EvidenceSpan,
    FactKind,
    GapReason,
    SleepFindingCategory,
    SleepTest,
)
from pa_agent.criteria import (
    PredicateInputs,
    QualifyingRun,
    check_citation_sufficiency,
    evaluate,
    index_sleep_test,
)
from pa_agent.stores.policy import LocalPolicyStore, UnknownJurisdiction

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = REPO_ROOT / "data" / "policies" / "pap_osa_dme_jd.json"
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
TREE_ID = "pap-osa-dme-jd-v1"
AS_OF = date(2026, 9, 1)
SNOMED = "http://snomed.info/sct"
SLEEP = FactKind.SLEEP_APNEA_WORKUP


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(store) -> CriteriaTree:
    return store.get_tree(TREE_ID)


@pytest.fixture(scope="module")
def comorbidities(store) -> CodedValueSet:
    return store.get_value_set("osa_qualifying_comorbidities")


def _raw() -> dict:
    return json.loads(TREE_PATH.read_text(encoding="utf-8"))


def _span(name: str, start: int = 0) -> EvidenceSpan:
    return EvidenceSpan(document_id=f"note/{name}", char_start=start, char_end=start + 5, quote=None)


_counter = iter(range(10_000))


def _test(when: str, index: float | None, hours: float | None) -> SleepTest:
    at = next(_counter) * 10
    return SleepTest(
        test_date=date.fromisoformat(when),
        span=_span("report", at),
        index=index,
        index_span=_span("index", at) if index is not None else None,
        recording_hours=hours,
        hours_span=_span("hours", at) if hours is not None else None,
    )


def _evaluation(when: str) -> ClinicalEvaluation:
    return ClinicalEvaluation(
        evaluation_date=date.fromisoformat(when), span=_span("consult", next(_counter) * 10)
    )


def _finding(category: SleepFindingCategory) -> DocumentedFinding:
    return DocumentedFinding(category=category, span=_span("finding", next(_counter) * 10))


def _condition(code: str, status: str = "active", system: str | None = SNOMED) -> Condition:
    return Condition(
        code=code, system=system, clinical_status=status,
        span=EvidenceSpan(document_id="bundle.json", char_start=next(_counter), char_end=next(_counter) + 9_000),
    )


def _inputs(*facts, conditions=(), value_sets=None) -> PredicateInputs:
    return PredicateInputs(
        as_of=AS_OF,
        facts={SLEEP: tuple(facts)},
        conditions=tuple(conditions),
        value_sets=value_sets or {},
    )


def _b(tree, comorbidities, *facts, conditions=()):
    return evaluate(
        tree.criterion("b"),
        _inputs(*facts, conditions=conditions,
                value_sets={"osa_qualifying_comorbidities": comorbidities}),
    )


def _a(tree, *facts):
    return evaluate(tree.criterion("a"), _inputs(*facts))


def _rederives(tree, comorbidities, result, *facts, conditions=()) -> None:
    """D99: a `NOT_MET` re-derives from its citations alone, or this raises."""
    check_citation_sufficiency(
        tree.criterion(result.criterion_id),
        result,
        observations=[],
        procedures=[],
        value_sets={"osa_qualifying_comorbidities": comorbidities},
        run=QualifyingRun(months=(), events=()),
        as_of=AS_OF,
        c3_met=False,
        conditions=list(conditions),
        facts={SLEEP: tuple(facts)},
    )


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def test_the_tree_declares_its_practice_kinds_letters_and_jurisdiction(tree):
    assert tree.practice == "sleep_medicine"
    assert tree.fact_kinds == (SLEEP,)
    assert [c.id for c in tree.criteria] == ["a", "b", "c", "d"]
    assert [c.evaluation for c in tree.criteria] == [
        "deterministic", "deterministic", "unclaimed", "unclaimed",
    ]
    assert tree.decision_expression == "a AND b AND c AND d"
    assert "IA" in tree.jurisdiction.states and "WA" in tree.jurisdiction.states
    assert "TX" not in tree.jurisdiction.states, "Texas is CGS's J-C, not this tree's"
    assert len(tree.jurisdiction.states) == 20


def test_e0601_resolves_here_and_its_neighbours_do_not(store):
    ref = store.resolve("E0601", "IA", "medicare")
    assert ref is not None and ref.policy_version_id == TREE_ID
    assert ref.coverage is CoverageStatus.NATIONALLY_COVERED
    # The bi-level device the same LCD names is not bound: its criterion D, a
    # failed E0601 trial, is outside this tree (D150).
    assert store.resolve("E0470", "IA", "medicare") is None
    # A state no tree serves stays REQ-55's answer, never a default (D111).
    with pytest.raises(UnknownJurisdiction):
        store.resolve("E0601", "TX", "medicare")


def test_the_binding_and_the_coverage_claim_slice_back(tree):
    (entry,) = tree.procedure_sets.nationally_covered
    claim = entry.coverage_claim
    ncd = (SOURCE_DIR / "ncd_240_4.txt").read_text(encoding="utf-8")
    assert ncd[claim.char_start:claim.char_end] == claim.quote
    lcd = (SOURCE_DIR / "l33718.txt").read_text(encoding="utf-8")
    corroborating = claim.corroborating_quote
    assert lcd[corroborating.char_start:corroborating.char_end] == corroborating.quote
    (binding,) = entry.codes
    assert binding.code == "E0601" and binding.system == "HCPCS" and binding.identity
    assert lcd[binding.char_start:binding.char_end] == binding.quote
    assert "E0601" in binding.quote and "E0470" not in binding.quote


# --------------------------------------------------------------------------
# Two national floors on one criterion (REQ-73, D150)
# --------------------------------------------------------------------------


def test_both_floors_cite_the_ncd_and_hold_at_equality(tree):
    floors = tree.criterion("b").floors()
    assert [f.constant for f in floors] == ["min_index", "min_index_with_findings"]
    ncd = (SOURCE_DIR / "ncd_240_4.txt").read_text(encoding="utf-8")
    for floor in floors:
        assert floor.document_id == "ncd_240_4"
        sliced = ncd[floor.char_start:floor.char_end]
        assert sliced == floor.quote and str(int(floor.value)) in sliced
        assert tree.criterion("b").require(floor.constant) == floor.value
    assert tree.criterion("a").floors() == ()


@pytest.mark.parametrize("constant, looser", [("min_index", 14), ("min_index_with_findings", 4)])
def test_either_threshold_loosened_below_its_floor_fails_to_load(constant, looser):
    """Flooring only one would let a tree loosen the other with every check
    green, which is the reason the field takes a list (D150)."""
    raw = _raw()
    b = next(c for c in raw["criteria"] if c["id"] == "b")
    b["constants"][constant]["value"] = looser
    with pytest.raises(ValidationError, match="looser than the floor"):
        CriteriaTree.model_validate(raw)


def test_a_stricter_threshold_loads():
    raw = _raw()
    b = next(c for c in raw["criteria"] if c["id"] == "b")
    b["constants"]["min_index_with_findings"]["value"] = 6
    CriteriaTree.model_validate(raw)


def test_two_floors_on_one_constant_fail_to_load():
    raw = _raw()
    b = next(c for c in raw["criteria"] if c["id"] == "b")
    second = copy.deepcopy(b["national_floor"][0])
    b["national_floor"][1] = second
    with pytest.raises(ValidationError, match="same constant"):
        CriteriaTree.model_validate(raw)


# --------------------------------------------------------------------------
# Criterion b: the sleep test's index
# --------------------------------------------------------------------------


def test_branch_one_met_cites_the_test_its_index_and_its_hours(tree, comorbidities):
    test = _test("2026-03-01", 27, 6.4)
    result = _b(tree, comorbidities, test)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [test.span, test.index_span, test.hours_span]


def test_branch_one_boundary_is_inclusive_on_index_and_events(tree, comorbidities):
    """15 per hour over exactly two hours is exactly thirty events: MET."""
    assert _b(tree, comorbidities, _test("2026-03-01", 15, 2.0)).verdict is CriterionVerdict.MET


def test_a_short_recording_misses_branch_one_and_says_by_how_many_events(tree, comorbidities):
    test = _test("2026-03-01", 16, 1.5)
    result = _b(tree, comorbidities, test)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert (result.shortfall.observed, result.shortfall.required, result.shortfall.unit) == (
        24.0, 30.0, "events",
    )
    assert result.spans == [test.span, test.index_span, test.hours_span]
    _rederives(tree, comorbidities, result, test)


def test_an_index_below_the_band_is_not_met_whatever_is_documented(tree, comorbidities):
    test = _test("2026-03-01", 4.9, 7.0)
    finding = _finding(SleepFindingCategory.EXCESSIVE_DAYTIME_SLEEPINESS)
    condition = _condition("59621000")
    result = _b(tree, comorbidities, test, finding, conditions=[condition])
    assert result.verdict is CriterionVerdict.NOT_MET
    assert (result.shortfall.observed, result.shortfall.required, result.shortfall.unit) == (
        4.9, 5.0, "events_per_hour",
    )
    assert result.spans == [test.span, test.index_span], "the hours are not what fell short"
    _rederives(tree, comorbidities, result, test, finding, conditions=[condition])


def test_the_band_is_met_by_a_documented_finding(tree, comorbidities):
    test = _test("2026-03-01", 9, 6.1)
    finding = _finding(SleepFindingCategory.EXCESSIVE_DAYTIME_SLEEPINESS)
    result = _b(tree, comorbidities, test, finding)
    assert result.verdict is CriterionVerdict.MET
    assert finding.span in result.spans and test.span in result.spans


def test_the_band_is_met_by_an_active_coded_comorbidity(tree, comorbidities):
    test = _test("2026-03-01", 12, 5.8)
    condition = _condition("414545008")
    result = _b(tree, comorbidities, test, conditions=[condition])
    assert result.verdict is CriterionVerdict.MET
    assert condition.span in result.spans


@pytest.mark.parametrize(
    "condition",
    [
        _condition("414545008", status="resolved"),
        _condition("414545008", system="http://hl7.org/fhir/sid/icd-10-cm"),
        _condition("44054006"),  # type 2 diabetes: coded, and not on the list
    ],
    ids=["resolved", "another-system", "not-a-member"],
)
def test_the_band_abstains_on_a_condition_that_does_not_qualify(tree, comorbidities, condition):
    result = _b(tree, comorbidities, _test("2026-03-01", 8, 6.5), conditions=[condition])
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_a_finding_qualifies_by_the_trees_list_and_not_by_its_label(tree, comorbidities):
    """Membership is Python's: a tree whose list omits a category is not met
    by it, whatever the extractor labelled (Art. II)."""
    narrowed = tree.criterion("b").model_copy(deep=True)
    constant = narrowed.constants["qualifying_findings"].model_copy(
        update={"value": ["hypertension"]}
    )
    narrowed = narrowed.model_copy(
        update={"constants": {**narrowed.constants, "qualifying_findings": constant}}
    )
    finding = _finding(SleepFindingCategory.INSOMNIA)
    result = evaluate(
        narrowed,
        _inputs(_test("2026-03-01", 8, 6.5), finding,
                value_sets={"osa_qualifying_comorbidities": comorbidities}),
    )
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_a_fractional_index_between_the_two_sentences_reads_into_the_band(tree, comorbidities):
    """14.5 is neither '>= 15' nor '<= 14'. Read into the branch that asks for
    more documentation, never denied for falling between two sentences (D150)."""
    test = _test("2026-03-01", 14.5, 6.0)
    assert _b(tree, comorbidities, test).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    finding = _finding(SleepFindingCategory.INSOMNIA)
    assert _b(tree, comorbidities, test, finding).verdict is CriterionVerdict.MET


def test_the_band_misses_its_ten_event_minimum(tree, comorbidities):
    test = _test("2026-03-01", 6, 1.5)
    finding = _finding(SleepFindingCategory.MOOD_DISORDER)
    result = _b(tree, comorbidities, test, finding)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert (result.shortfall.observed, result.shortfall.required) == (9.0, 10.0)
    _rederives(tree, comorbidities, result, test, finding)


@pytest.mark.parametrize(
    "facts",
    [(), (_test("2026-03-01", None, 6.0),), (_test("2026-03-01", 30, None),)],
    ids=["no-test", "no-index", "no-hours"],
)
def test_an_undocumented_test_index_or_time_abstains(tree, comorbidities, facts):
    result = _b(tree, comorbidities, *facts)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert result.spans == []


def test_the_most_recent_test_on_or_before_the_clock_is_the_one_judged(tree, comorbidities):
    older = _test("2025-01-01", 30, 7.0)
    newer = _test("2026-02-01", 3, 7.0)
    future = _test("2026-12-01", 40, 7.0)
    assert index_sleep_test([older, newer, future], AS_OF) is newer
    result = _b(tree, comorbidities, older, newer, future)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans == [newer.span, newer.index_span]
    _rederives(tree, comorbidities, result, older, newer, future)


# --------------------------------------------------------------------------
# Criterion a: an in-person evaluation before the sleep test
# --------------------------------------------------------------------------


def test_a_prior_evaluation_is_met_citing_the_latest_one_and_the_test(tree):
    early, late = _evaluation("2026-01-05"), _evaluation("2026-02-20")
    test = _test("2026-03-01", 20, 6.0)
    result = _a(tree, early, late, test)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [late.span, test.span]


@pytest.mark.parametrize("when", ["2026-03-01", "2026-03-10"], ids=["same-day", "after"])
def test_an_evaluation_not_strictly_before_the_test_is_not_met(tree, comorbidities, when):
    """Strictly before: a date carries no time, and a same-day evaluation
    cannot be shown to precede that night's study (D150)."""
    evaluation = _evaluation(when)
    test = _test("2026-03-01", 20, 6.0)
    result = _a(tree, evaluation, test)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans == [test.span, evaluation.span]
    assert (result.shortfall.observed, result.shortfall.unit) == (0, "evaluations_before_sleep_test")
    _rederives(tree, comorbidities, result, evaluation, test)


@pytest.mark.parametrize(
    "facts",
    [(_test("2026-03-01", 20, 6.0),), (_evaluation("2026-02-01"),)],
    ids=["no-evaluation", "no-test"],
)
def test_a_missing_evaluation_or_test_abstains(tree, facts):
    result = _a(tree, *facts)
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_a_and_b_judge_the_same_test(tree, comorbidities):
    """One selector serves both, so `a` cannot approve against a test `b` did
    not judge: an evaluation between two tests precedes only the newer one."""
    older, newer = _test("2025-06-01", 30, 7.0), _test("2026-03-01", 3, 7.0)
    between = _evaluation("2025-12-01")
    assert _a(tree, older, newer, between).spans[-1] == newer.span
    assert _b(tree, comorbidities, older, newer, between).spans[0] == newer.span


def test_the_narrower_keeps_only_what_a_verdict_cites(tree, comorbidities):
    """`check_citation_sufficiency` re-derives over what `_cited_workup` keeps.
    On a correct predicate an identity narrower re-derives the same answer, so
    the check passing proves nothing about the narrower; this holds it
    directly: an uncited test, finding or condition is dropped (D99, D150)."""
    from pa_agent.contracts import PredicateKind
    from pa_agent.criteria import NARROWERS

    cited_test, other_test = _test("2026-03-01", 3, 7.0), _test("2025-03-01", 30, 7.0)
    finding = _finding(SleepFindingCategory.INSOMNIA)
    cited_condition, other_condition = _condition("414545008"), _condition("59621000")
    inputs = _inputs(
        cited_test, other_test, finding,
        conditions=[cited_condition, other_condition],
    )
    for kind in (PredicateKind.NOTE_SLEEP_TEST_INDEX, PredicateKind.NOTE_EVALUATION_BEFORE_SLEEP_TEST):
        narrowed = NARROWERS[kind](inputs, [cited_test.span, cited_test.index_span, cited_condition.span])
        assert narrowed.facts[SLEEP] == (cited_test,)
        assert narrowed.conditions == (cited_condition,)
