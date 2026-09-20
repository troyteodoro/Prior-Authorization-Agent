"""T-94 — the third practice's tree, over charts written here (US-10, D114).

`tests/test_ultrasound_corpus.py` asks whether the committed bundles carry
what the eval rows say; this file asks whether the tree loads, resolves and
evaluates, over charts small enough to state the whole input.

The division is D65's and it is load-bearing twice here, because the corpus
cannot distinguish two behaviours this tree depends on:

- **the care-setting carve-out.** L35755 drops inpatient-hospital and
  emergency-room studies from its own frequency arithmetic. No committed chart
  carries an abdominal vascular study at either kind of encounter — the
  declared ones are copied from ambulatory procedures — so a predicate that
  ignored the carve-out would pass every corpus check. It is caught here, on a
  chart written to have one (D113's tenth mutation, seen coming).
- **what "performed" means.** A `not-done` procedure is not a prior study and
  a `completed` one is; the corpus carries only completed procedures.

Everything else is the shape T-92's file takes for the second practice: one
new predicate kind, exercised at each of its three answers and at its
boundary, with the abstention pinned as an abstention rather than as a `MET`
nobody could cite.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    Condition,
    CriterionVerdict,
    DeterminationAborted,
    Document,
    DeterminationOutcome,
    EvidenceSpan,
    GapReason,
    PredicateKind,
    Procedure,
)
from pa_agent.determination import determine
from pa_agent.stores.policy import LocalPolicyStore, UnknownJurisdiction

from conftest import AcceptAllVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_ID = "us-abdominal-visceral-j5-j8-v1"
TREE_PATH = REPO_ROOT / "data" / "policies" / "us_abdominal_visceral_j5_j8.json"

US_CODE = "93975"
US_CODE_LIMITED = "93976"
UNLISTED_CODE = "76700"  # a non-vascular abdominal ultrasound; no tree binds it
BARIATRIC_CODE = "43775"
WPS_STATE = "IA"
PALMETTO_STATE = "AL"  # served by this tree and by both Palmetto trees
UNSERVED_STATE = "NY"  # no tree in this store serves it

SNOMED = "http://snomed.info/sct"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
HYPERTENSION = "59621000"
CKD_STAGE_2 = "431856006"
STUDY = "709640007"

#: The clock every chart below is read at. The interval criterion is the only
#: date arithmetic in this tree, and it is `as_of` minus a performed date.
AS_OF = date(2026, 9, 1)

#: The bundle these charts' spans point into. `LocalPatientStore` anchors a
#: structured fact to the extent of its JSON entry and carries no quote, so
#: these do the same: the graph re-slices every cited span through
#: `get_document` before it ships anything, and a span into a document the
#: store cannot serve is the failure that check exists for.
CHART_TEXT = "".join(f"{n:04d}-" for n in range(400))

SPAN = EvidenceSpan(document_id="chart.json", char_start=0, char_end=10)


def _span(n: int) -> EvidenceSpan:
    """A distinct span per resource, so a citation can be told apart."""
    return EvidenceSpan(
        document_id="chart.json", char_start=n * 100, char_end=n * 100 + 40
    )


class _Chart:
    """A patient store reporting the facts written in the test.

    Only the methods the graph calls, so a step reaching for anything else
    fails loudly instead of finding a stub that answers (T-18's rule).
    `get_document` serves one synthetic bundle, because the graph re-slices
    every cited span before it ships anything and these charts cite their own
    resources.
    """

    def __init__(self, conditions=(), procedures=(), state=WPS_STATE) -> None:
        self._conditions = list(conditions)
        self._procedures = list(procedures)
        self._state = state

    def get_observations(self, patient_id: str) -> list:
        return []

    def get_conditions(self, patient_id: str) -> list:
        return list(self._conditions)

    def get_medications(self, patient_id: str) -> list:
        return []

    def get_procedures(self, patient_id: str) -> list:
        return list(self._procedures)

    def get_notes(self, patient_id: str) -> list:
        return []

    def get_jurisdiction_state(self, patient_id: str) -> str:
        return self._state

    def get_document(self, document_id: str) -> Document:
        if document_id != "chart.json":
            raise KeyError(document_id)
        return Document.from_text(document_id, CHART_TEXT)


class _RaisingRunner:
    """An extraction runner that fails if the graph reaches it.

    This tree declares no note criterion, so zero model calls is a property of
    the graph rather than of a counter (A4's shape, T-92's device).
    """

    name = "raising"

    def run(self, *args, **kwargs):  # pragma: no cover - the point is the raise
        raise AssertionError("the ultrasound tree declares no note criterion")


def _indication(code: str = HYPERTENSION, system: str = SNOMED, n: int = 1) -> Condition:
    return Condition(code=code, system=system, clinical_status="active", span=_span(n))


def _study(
    when: str,
    *,
    code: str = STUDY,
    system: str = SNOMED,
    status: str = "completed",
    encounter_class: str | None = "AMB",
    n: int = 2,
) -> Procedure:
    return Procedure(
        code=code,
        system=system,
        status=status,
        performed_date=date.fromisoformat(when),
        encounter_class=encounter_class,
        span=_span(n),
    )


def _determine(chart: _Chart, code: str = US_CODE, as_of: date = AS_OF):
    return determine(
        LocalPolicyStore(),
        code,
        patient_id="patient-under-test",
        patient_store=chart,
        as_of=as_of,
        extraction_runner=_RaisingRunner(),
        verifier=AcceptAllVerifier(),
    )


def _result(determination, criterion_id: str):
    return next(r for r in determination.criterion_results if r.criterion_id == criterion_id)


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(store):
    return store.get_tree(TREE_ID)


# --------------------------------------------------------------------------
# Resolution: a third practice, and a state that now has three trees
# --------------------------------------------------------------------------


def test_one_state_serves_three_practices_and_each_code_resolves_to_one_tree(store):
    """Alabama is in L35755's own contractor table, so the state Palmetto
    serves twice is served a third time — and nothing collides, because the
    collision that matters is a *code* bound twice (D111's rule, D114)."""
    serving = {t.policy_version_id for t in store.trees_for_state(PALMETTO_STATE)}
    assert {TREE_ID, "ncd-100.1-jjm-v1", "infliximab-ra-jjm-v1"} <= serving

    assert store.resolve(US_CODE, PALMETTO_STATE).policy_version_id == TREE_ID
    assert store.resolve(BARIATRIC_CODE, PALMETTO_STATE).policy_version_id == (
        "ncd-100.1-jjm-v1"
    )
    assert store.resolve("J1745", PALMETTO_STATE).policy_version_id == (
        "infliximab-ra-jjm-v1"
    )


def test_both_group_one_codes_resolve_to_this_tree(store):
    """A57591's Group 1 paragraph names two codes and the tree binds both;
    neither the conditions nor the frequency limit distinguishes them."""
    for code in (US_CODE, US_CODE_LIMITED):
        assert store.resolve(code, WPS_STATE).policy_version_id == TREE_ID


def test_an_unlisted_ultrasound_code_in_a_served_state_is_no_policy_found(store):
    """D26's distinction, on a third jurisdiction. Iowa *is* served, so an
    unbound code is 'no policy governs this code' and not 'no tree serves this
    state' — different answers with different next actions (REQ-55)."""
    assert store.resolve(UNLISTED_CODE, WPS_STATE) is None
    assert store.trees_for_state(WPS_STATE)


def test_a_bariatric_request_in_a_wps_state_changed_answer_with_this_tree(store):
    """Worth pinning because this row moved an answer that was already
    correct. Before T-94 no tree served Iowa, so 43775 there was
    `NO_JURISDICTION_TREE` — *no tree serves this state*. The ultrasound tree
    serves it now, so the same request is `NO_POLICY_FOUND` — *a tree serves
    this state and binds no such code*. Both are right at their own moment and
    they are different answers, which is the distinction D26 and REQ-55 keep
    apart (D114)."""
    assert store.trees_for_state(WPS_STATE)
    assert store.resolve(BARIATRIC_CODE, WPS_STATE) is None


def test_an_unserved_state_raises_rather_than_falling_back(store):
    with pytest.raises(UnknownJurisdiction):
        store.resolve(US_CODE, UNSERVED_STATE)


# --------------------------------------------------------------------------
# What the tree declares
# --------------------------------------------------------------------------


def test_the_tree_declares_two_deterministic_criteria_and_three_unclaimed(tree):
    """Two of five, the same ratio the rheumatology tree came out at and for
    the same reason: these documents spend most of their words on judgments."""
    by_id = {c.id: c for c in tree.criteria}
    assert sorted(by_id) == ["a", "b", "c", "d", "e"]
    assert by_id["a"].kind is PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP
    assert by_id["b"].kind is PredicateKind.PROCEDURE_VALUE_SET_INTERVAL
    for unclaimed in ("c", "d", "e"):
        assert by_id[unclaimed].evaluation == "unclaimed"
        assert by_id[unclaimed].kind is None
        assert by_id[unclaimed].note


def test_the_tree_states_no_categorical_exclusion(tree):
    """L35755 denies nothing categorically: its four Limitations narrow
    indications and none of them says *not covered*. Asserted rather than
    left absent, because an exclusion that quietly stopped loading would
    approve past a denial and this is the tree that proves the absence is
    declared (REQ-60, D111's rule read the other way)."""
    assert not tree.categorical_exclusions


def test_the_frequency_constants_are_the_documents(tree):
    interval = tree.criterion("b")
    assert interval.require("min_months_since_prior_procedure") == 12
    assert interval.require("excluded_encounter_classes") == ["EMER", "IMP"]
    assert interval.require("value_set_id") == "abdominal_visceral_vascular_studies"


# --------------------------------------------------------------------------
# The interval predicate: three answers, each citing what it has
# --------------------------------------------------------------------------


def test_a_prior_study_inside_the_interval_is_not_met_and_cites_it():
    chart = _Chart([_indication()], [_study("2026-05-25")])
    result = _result(_determine(chart), "b")
    assert result.verdict is CriterionVerdict.NOT_MET
    assert [s.char_start for s in result.spans] == [_span(2).char_start]
    assert result.shortfall.observed == 3
    assert result.shortfall.required == 12
    assert result.shortfall.unit == "months_since_prior_procedure"


def test_every_study_inside_the_interval_is_cited_not_just_the_most_recent():
    """Under-citation is what the shortfall exists to catch (D99), and a
    frequency limit exceeded three times over is a different fact for a
    reviewer than one exceeded once."""
    chart = _Chart(
        [_indication()],
        [_study("2026-05-25", n=2), _study("2026-01-10", n=3), _study("2024-01-10", n=4)],
    )
    result = _result(_determine(chart), "b")
    assert result.verdict is CriterionVerdict.NOT_MET
    assert sorted(s.char_start for s in result.spans) == sorted(
        [_span(2).char_start, _span(3).char_start]
    )
    assert result.shortfall.observed == 3


def test_the_most_recent_study_outside_the_interval_is_met_and_cites_itself():
    chart = _Chart([_indication()], [_study("2024-06-17")])
    result = _result(_determine(chart), "b")
    assert result.verdict is CriterionVerdict.MET
    assert [s.char_start for s in result.spans] == [_span(2).char_start]
    assert result.shortfall is None


def test_a_chart_with_no_prior_study_abstains_and_never_answers_met():
    """D40's asymmetry reaching a third resource type. A chart that records no
    study has not recorded that none was performed elsewhere — and a `MET`
    here would have no span, which REQ-5 refuses (D114)."""
    result = _result(_determine(_Chart([_indication()], [])), "b")
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert result.spans == []


def test_the_interval_boundary_is_inclusive_on_the_met_side():
    """A study exactly twelve months old is outside a twelve-month interval,
    as E12's BMI is at criterion (a)'s threshold."""
    assert (
        _result(_determine(_Chart([_indication()], [_study("2025-09-01")])), "b").verdict
        is CriterionVerdict.MET
    )
    assert (
        _result(_determine(_Chart([_indication()], [_study("2025-09-02")])), "b").verdict
        is CriterionVerdict.NOT_MET
    )


# --------------------------------------------------------------------------
# The care-setting carve-out — the corpus cannot tell these apart (D65, D114)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("setting", ["EMER", "IMP"])
def test_a_study_in_an_excluded_care_setting_is_not_counted(setting):
    """L35755 drops inpatient-hospital and emergency-room studies from its own
    arithmetic. Counting one would deny a patient the document does not
    restrict, which is D101's test for a wrong verdict."""
    chart = _Chart(
        [_indication()], [_study("2026-05-25", encounter_class=setting)]
    )
    result = _result(_determine(chart), "b")
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_an_excluded_setting_does_not_hide_a_study_that_does_count():
    """The carve-out drops studies, not the criterion: an ambulatory study
    inside the window still denies even when an emergency one sits beside it."""
    chart = _Chart(
        [_indication()],
        [
            _study("2026-06-01", encounter_class="EMER", n=2),
            _study("2026-05-25", encounter_class="AMB", n=3),
        ],
    )
    result = _result(_determine(chart), "b")
    assert result.verdict is CriterionVerdict.NOT_MET
    assert [s.char_start for s in result.spans] == [_span(3).char_start]


def test_a_study_whose_setting_is_unknown_is_counted():
    """Excluded **only on positive evidence** (D114). The two directions are
    not symmetric: dropping an unresolvable study approves past a limit the
    policy states, while counting it produces a `NOT_MET` the reviewer can
    lift with the documentation L35755 itself asks for."""
    chart = _Chart(
        [_indication()], [_study("2026-05-25", encounter_class=None)]
    )
    assert _result(_determine(chart), "b").verdict is CriterionVerdict.NOT_MET


# --------------------------------------------------------------------------
# What counts as a study at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["not-done", "entered-in-error"])
def test_a_study_the_record_says_did_not_happen_is_not_counted(status):
    chart = _Chart([_indication()], [_study("2026-05-25", status=status)])
    assert (
        _result(_determine(chart), "b").verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


@pytest.mark.parametrize("status", ["completed", "in-progress", "stopped", "unknown"])
def test_a_study_the_record_does_not_disown_is_counted(status):
    chart = _Chart([_indication()], [_study("2026-05-25", status=status)])
    assert _result(_determine(chart), "b").verdict is CriterionVerdict.NOT_MET


def test_a_study_code_in_the_set_under_another_system_is_not_a_member():
    """REQ-59 over the procedure plane. The set declares SNOMED; the same
    digits in another vocabulary are a different concept."""
    chart = _Chart([_indication()], [_study("2026-05-25", system=ICD10)])
    assert (
        _result(_determine(chart), "b").verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


def test_a_procedure_outside_the_value_set_is_not_a_prior_study():
    chart = _Chart([_indication()], [_study("2026-05-25", code="171207006")])
    assert (
        _result(_determine(chart), "b").verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


# --------------------------------------------------------------------------
# Criterion (a), and the determination as a whole
# --------------------------------------------------------------------------


def test_an_indication_in_the_set_meets_criterion_a_and_cites_the_condition():
    chart = _Chart([_indication(CKD_STAGE_2, n=5)], [])
    result = _result(_determine(chart), "a")
    assert result.verdict is CriterionVerdict.MET
    assert [s.char_start for s in result.spans] == [_span(5).char_start]


def test_a_resolved_indication_is_not_one():
    chart = _Chart(
        [Condition(code=HYPERTENSION, system=SNOMED, clinical_status="resolved", span=SPAN)],
        [],
    )
    assert _result(_determine(chart), "a").verdict is (
        CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


def test_every_declared_criterion_produces_a_verdict_and_none_is_omitted(tree):
    determination = _determine(_Chart([_indication()], [_study("2024-06-17")]))
    assert sorted(r.criterion_id for r in determination.criterion_results) == sorted(
        c.id for c in tree.criteria
    )


def test_the_unclaimed_criteria_abstain_and_say_which_they_are():
    determination = _determine(_Chart([_indication()], [_study("2024-06-17")]))
    for criterion_id in ("c", "d", "e"):
        result = _result(determination, criterion_id)
        assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
        assert result.gap_reason is GapReason.NOT_EVALUATED_BY_THIS_SYSTEM
        assert result.spans == []


def test_a_not_met_frequency_makes_the_determination_not_met():
    """REQ-20's three-valued AND: a false conjunct dominates an unknown one,
    so the three unclaimed criteria do not turn a denial into an abstention."""
    determination = _determine(_Chart([_indication()], [_study("2026-05-25")]))
    assert determination.outcome is DeterminationOutcome.NOT_MET


def test_the_determination_spends_no_model_call():
    determination = _determine(_Chart([_indication()], [_study("2024-06-17")]))
    assert not determination.metrics


def test_an_unlisted_code_never_reaches_the_tree():
    result = _determine(_Chart([_indication()], [_study("2026-05-25")]), UNLISTED_CODE)
    assert type(result).__name__ == "NoPolicyResult"


# --------------------------------------------------------------------------
# The NOT_MET re-derives from what it cites (T-86, D99)
# --------------------------------------------------------------------------


def test_a_not_met_that_cited_only_some_of_its_studies_is_caught(monkeypatch):
    """`step_sufficiency` re-runs the predicate over only the cited evidence.
    A verdict citing one of two studies inside the window still re-derives
    `NOT_MET`, so the shortfall is what catches it — and the shortfall moves
    only if the *most recent* study was dropped, which is the citation a
    reviewer would miss (D99)."""
    from pa_agent import criteria as criteria_module

    real = criteria_module.evaluate_prior_procedure_interval

    def under_citing(criterion, procedures, value_set, as_of):
        result = real(criterion, procedures, value_set, as_of)
        if result.verdict is CriterionVerdict.NOT_MET and len(result.spans) > 1:
            return result.model_copy(update={"spans": result.spans[1:]})
        return result

    monkeypatch.setitem(
        criteria_module.PREDICATES,
        PredicateKind.PROCEDURE_VALUE_SET_INTERVAL,
        lambda c, i: under_citing(
            c, list(i.procedures), criteria_module._value_set(c, i), i.as_of
        ),
    )
    chart = _Chart(
        [_indication()], [_study("2026-05-25", n=2), _study("2026-01-10", n=3)]
    )
    with pytest.raises(DeterminationAborted) as raised:
        _determine(chart)
    # A code defect, not a fact about the chart: `ERROR`, the abort, and never
    # an abstention (Art. IV, D99).
    errored = [r for r in raised.value.results if r.criterion_id == "b"]
    assert [r.verdict for r in errored] == [CriterionVerdict.ERROR]
    assert errored[0].error_code is not None


# --------------------------------------------------------------------------
# The tree file itself
# --------------------------------------------------------------------------


def test_every_constant_slices_back_into_the_document_it_cites():
    """Article III over the tree's own constants, which
    `test_criteria_tree.py` runs over every tree — repeated here for the two
    the frequency limit turns on, so this file fails first when the document
    is re-fetched and re-anchored."""
    loaded = json.loads(TREE_PATH.read_text(encoding="utf-8"))
    source_dir = REPO_ROOT / "data" / "policies" / "source"
    texts = {
        document_id: (source_dir / f"{document_id}.txt").read_text(encoding="utf-8")
        for document_id in ("l35755", "a57591")
    }
    checked = 0
    for criterion in loaded["criteria"]:
        for constant in criterion["constants"].values():
            source = constant.get("source")
            if not source:
                continue
            text = texts[source["document_id"]]
            assert text[source["char_start"] : source["char_end"]] == source["quote"]
            checked += 1
    assert checked >= 6


def test_no_module_names_this_tree_s_criterion_ids():
    """T-91's rule, checked again for a tree whose letters collide with two
    other practices'. `a` is a diagnosis set here, a BMI comparison under NCD
    100.1 and a diagnosis set again under L35677 — dispatch reads the declared
    kind and nothing else (D110)."""
    offenders = []
    for path in sorted((REPO_ROOT / "pa_agent").rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Constant) and node.value in {"93975", "93976"}:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"a procedure code from this tree is a literal in {offenders}; codes "
        "live in the tree and reach the engine through the resolver"
    )
