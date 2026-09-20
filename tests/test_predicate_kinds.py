"""T-91 — the engine's predicate vocabulary, and the tree that declares it.

**Spends no model call and touches no network.**

D101 made the graph evaluate *what the tree declares* rather than Noridian's
seven criteria. That was true of which criteria run and not of which
arithmetic each one gets: every predicate in this engine was chosen by the
criterion's **id**, so a criterion lettered `a` was BMI, `c3` was a run
length, and `b` was the comorbidity value set — in any tree, from any
practice. Coverage documents letter their criteria `a`, `b`, `c` as a matter
of course, so a rheumatology tree would have been evaluated by bariatric
arithmetic against rheumatology constants, answered, cited a span, and passed
every test in this repo (D110).

So the checks here are mostly about **absence** — no id literal decides a
predicate, no kind is unimplemented, no kind is unevaluated — and an
assertion about absence passes for free if it parses nothing. Each one below
is pinned against a literal first.
"""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent import criteria as criteria_module
from pa_agent.contracts import (
    CriteriaTree,
    Criterion,
    CriterionResult,
    CriterionVerdict,
    EvidenceSpan,
    PolicyConstant,
    PredicateKind,
    Shortfall,
    WmEvent,
)
from pa_agent.criteria import (
    NARROWERS,
    PREDICATES,
    CitationInsufficient,
    PredicateInputs,
    UnknownPredicateKind,
    check_citation_sufficiency,
    evaluate,
)
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.workflow import (
    MEMBERSHIP_KINDS,
    NOTE_EVENT_KINDS,
    OBSERVATION_KINDS,
    PROCEDURE_HISTORY_KINDS,
    STEP_KINDS,
    STRUCTURED_KINDS,
    _declared,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_ROOT = REPO_ROOT / "data" / "policies"
AS_OF = date(2026, 9, 1)
SPAN = EvidenceSpan(document_id="d", char_start=0, char_end=8, quote="anything")

#: What each committed tree declares, written out. Deriving this from the file
#: would make every assertion below agree with whatever the file says.
DECLARED: dict[str, dict[str, str | None]] = {
    "ncd_100_1_jf.json": {
        "a": "bmi_observation_threshold",
        "b": "condition_value_set_membership",
        "c1": "note_event_count",
        "c2": "note_event_run_recency",
        "c3": "note_event_run_length",
        "c4": "note_event_run_bmi_rate",
        "c5": "note_event_run_behavior_rate",
    },
    "ncd_100_1_jjm.json": {
        "a": "bmi_observation_threshold",
        "b": "condition_value_set_membership",
        "c1": "note_event_count",
        "c2": "note_event_run_recency",
        "c4": None,  # unclaimed: L34576 documents weight, not BMI (D101)
        "c5": "note_event_run_behavior_rate",
        "d": None,  # unclaimed: a multidisciplinary evaluation (D101)
    },
}


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


def _tree_dict(name: str = "ncd_100_1_jf.json") -> dict:
    return json.loads((POLICY_ROOT / name).read_text(encoding="utf-8"))


def _criterion(tree: dict, criterion_id: str) -> dict:
    return next(c for c in tree["criteria"] if c["id"] == criterion_id)


# --------------------------------------------------------------------------
# What the committed trees declare
# --------------------------------------------------------------------------


def test_every_deterministic_criterion_declares_a_kind_and_no_other_does(store):
    """REQ-57 on the two trees that exist, against the table above."""
    for filename, expected in DECLARED.items():
        tree = CriteriaTree.model_validate(_tree_dict(filename))
        found = {c.id: (c.kind.value if c.kind else None) for c in tree.criteria}
        assert found == expected, filename
        for criterion in tree.criteria:
            if criterion.evaluation == "deterministic":
                assert criterion.kind in PREDICATES, (
                    f"{filename}: {criterion.id} declares a kind no predicate "
                    "implements"
                )
            else:
                assert criterion.kind is None
                assert criterion.note, "an unclaimed criterion says why (D101)"


def test_the_two_trees_differ_in_which_kinds_they_declare(store):
    """The property a module-level constant could not have.

    Palmetto's tree declares no run-length criterion and two unclaimed ones,
    so the engine's view of it differs from Noridian's by kind and not only by
    id (D101).
    """
    jf = store.get_tree("ncd-100.1-jf-v1")
    jjm = store.get_tree("ncd-100.1-jjm-v1")
    assert PredicateKind.NOTE_EVENT_RUN_LENGTH in {c.kind for c in jf.criteria}
    assert PredicateKind.NOTE_EVENT_RUN_LENGTH not in {c.kind for c in jjm.criteria}
    assert PredicateKind.NOTE_EVENT_RUN_BMI_RATE not in {c.kind for c in jjm.criteria}


# --------------------------------------------------------------------------
# A kind the engine lacks raises at load
# --------------------------------------------------------------------------


def test_a_tree_naming_a_kind_the_engine_lacks_fails_to_load(tmp_path):
    """REQ-57's whole point, exercised through the store that loads trees.

    Not an abstention: an abstention would report a fact about the *chart*
    when the fact is about the system (D90, Art. IV). Unbuilt is not
    unclaimed.
    """
    tree = _tree_dict()
    _criterion(tree, "c3")["kind"] = "medication_trial_duration"
    (tmp_path / "bad.json").write_text(json.dumps(tree), encoding="utf-8")

    with pytest.raises(ValidationError, match="medication_trial_duration"):
        LocalPolicyStore(tmp_path).get_tree("ncd-100.1-jf-v1")


def test_a_deterministic_criterion_without_a_kind_is_refused():
    with pytest.raises(ValidationError, match="deterministic without a kind"):
        Criterion(id="a", label="a", constants={})


def test_an_unclaimed_criterion_may_not_declare_a_kind():
    """Unbuilt is not unclaimed, and neither is the reverse.

    A criterion the engine can evaluate is evaluated; declaring both would let
    a tree take the abstention while naming a predicate that exists.
    """
    with pytest.raises(ValidationError, match="Unbuilt is not unclaimed"):
        Criterion(
            id="d",
            label="d",
            evaluation="unclaimed",
            note="a reviewer evaluates it",
            kind=PredicateKind.NOTE_EVENT_COUNT,
        )


# --------------------------------------------------------------------------
# The vocabulary, the registry and the steps agree
# --------------------------------------------------------------------------


def test_the_vocabulary_is_the_kinds_that_have_predicates():
    """Pinned as a literal, so the three checks below cannot pass vacuously.

    Seven at T-91, eight since T-92 and nine since T-94: each new practice
    needed exactly one kind its predecessors did not — active medications
    against a named value set (D111), then the interval to a prior procedure
    (D114) — and no more than one, because the rest of what those documents
    say is unclaimed by the document rather than unbuilt by the engine.
    """
    assert {k.value for k in PredicateKind} == {
        "bmi_observation_threshold",
        "condition_value_set_membership",
        "medication_value_set_active",
        "procedure_value_set_interval",
        "note_event_count",
        "note_event_run_length",
        "note_event_run_recency",
        "note_event_run_bmi_rate",
        "note_event_run_behavior_rate",
    }


def test_every_kind_has_a_predicate_and_every_predicate_has_a_kind():
    """A kind with no predicate is a criterion that evaluates to nothing —
    an approval past a criterion the policy requires."""
    assert set(PREDICATES) == set(PredicateKind)


def test_every_kind_is_evaluated_by_exactly_one_step():
    """`STEP_KINDS` partitions the vocabulary.

    Exhaustive, so a new kind cannot be declared, implemented, and then never
    reached by the graph; disjoint, so no criterion is evaluated twice into
    two verdicts under one id.
    """
    claimed = [kind for kinds in STEP_KINDS.values() for kind in kinds]
    assert sorted(claimed, key=lambda k: k.value) == sorted(
        PredicateKind, key=lambda k: k.value
    )
    assert len(claimed) == len(set(claimed))
    assert STEP_KINDS == {
        "criterion_a": OBSERVATION_KINDS,
        "criterion_b": STRUCTURED_KINDS,
        "criteria_c": NOTE_EVENT_KINDS,
    }
    # T-94 (D114): `criterion_b` gained a kind rather than the graph gaining a
    # step, and the union is asserted so the procedure-history kind cannot be
    # dropped out of it while the partition check still passes on the rest.
    assert STRUCTURED_KINDS == MEMBERSHIP_KINDS + PROCEDURE_HISTORY_KINDS
    assert PROCEDURE_HISTORY_KINDS == (PredicateKind.PROCEDURE_VALUE_SET_INTERVAL,)


def test_dispatch_raises_rather_than_skipping_a_kind_with_no_predicate(monkeypatch):
    """The backstop, and the mutation that proves it fires.

    Deleting an entry from `PREDICATES` is exactly how a kind would go
    unimplemented, and the answer has to be a raise — a `None` result or a
    skipped criterion is a determination that silently dropped a requirement.
    """
    patched = dict(PREDICATES)
    del patched[PredicateKind.NOTE_EVENT_COUNT]
    monkeypatch.setattr(criteria_module, "PREDICATES", patched)

    criterion = Criterion(
        id="c1",
        label="events",
        kind=PredicateKind.NOTE_EVENT_COUNT,
        constants={"min_events": PolicyConstant(value=1, type="integer")},
    )
    with pytest.raises(UnknownPredicateKind, match="does not implement"):
        evaluate(criterion, PredicateInputs(as_of=AS_OF))


# --------------------------------------------------------------------------
# The id does not choose the arithmetic
# --------------------------------------------------------------------------


def test_a_criterion_lettered_a_is_evaluated_by_the_kind_it_declares():
    """The regression this task exists for.

    A tree whose criterion `a` is an event count and whose `c1` is a BMI
    threshold — the shape a document from another practice can produce —
    reaches the predicates its kinds name. Under the old dispatch `a` was
    handed to the BMI comparison and `c1` to the event count, and both
    answered.
    """
    raw = _tree_dict()
    a, c1 = _criterion(raw, "a"), _criterion(raw, "c1")
    a["kind"], c1["kind"] = c1["kind"], a["kind"]
    a["constants"], c1["constants"] = c1["constants"], a["constants"]
    tree = CriteriaTree.model_validate(raw)

    assert [c.id for c in _declared(tree, OBSERVATION_KINDS)] == ["c1"]
    assert _declared(tree, NOTE_EVENT_KINDS)[0].id == "a"

    events = [WmEvent(event_date=date(2026, 6, 1), span=SPAN)]
    result = evaluate(tree.criterion("a"), PredicateInputs(as_of=AS_OF, events=tuple(events)))
    assert result.verdict is CriterionVerdict.MET
    assert "supervised encounter" in result.detail, (
        "the criterion lettered `a` was evaluated by the event count it "
        "declares, not by the BMI comparison its letter used to select"
    )


def test_no_module_chooses_a_predicate_from_a_criterion_id():
    """Parsed, not exercised (D65's shape).

    Both committed trees use the ids the old dispatch hardcoded, so reverting
    any of it answers identically on every case this repo can produce and
    every behavioural test passes. Only the source says which it is.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "pa_agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in {"c1", "c2", "c3", "c4", "c5"}
            ):
                offenders.append(f"{path.name}:{node.lineno} literal {node.value!r}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"criterion", "criteria_of_kind"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    assert offenders == [], (
        "a criterion id is a label, not a statement about what the criterion "
        f"means (REQ-57, D110): {offenders}"
    )


# --------------------------------------------------------------------------
# `scoped_to` is resolved, not compared
# --------------------------------------------------------------------------


def test_scoped_to_must_name_a_criterion_the_tree_declares():
    raw = _tree_dict()
    _criterion(raw, "c2")["scoped_to"] = "c9"
    with pytest.raises(ValidationError, match="which ncd-100.1-jf-v1 does not define"):
        CriteriaTree.model_validate(raw)


def test_run_kinds_is_the_note_event_kinds_that_need_a_run():
    """`RUN_KINDS` decides whether `step_qualifying_run` selects a run at all,
    so it is written out and pinned rather than sliced off another tuple."""
    from pa_agent.workflow import RUN_KINDS

    assert set(RUN_KINDS) < set(NOTE_EVENT_KINDS)
    assert set(NOTE_EVENT_KINDS) - set(RUN_KINDS) == {PredicateKind.NOTE_EVENT_COUNT}, (
        "counting encounters needs no qualifying run; everything else in the "
        "note-event family scopes to one"
    )


def test_scoped_to_may_not_name_a_criterion_the_tree_does_not_evaluate():
    """Gating a criterion on an unclaimed one can only ever abstain, so the
    tree says so at load instead of the determination saying it per patient."""
    raw = _tree_dict("ncd_100_1_jjm.json")
    _criterion(raw, "c5")["scoped_to"] = "d"  # `d` is declared unclaimed
    with pytest.raises(ValidationError, match="does not evaluate"):
        CriteriaTree.model_validate(raw)


def test_scoped_to_may_not_chain():
    """Two passes are a topological order only at a depth of one (D110)."""
    raw = _tree_dict()
    _criterion(raw, "c3")["scoped_to"] = "c1"
    with pytest.raises(ValidationError, match="itself scoped"):
        CriteriaTree.model_validate(raw)


def test_only_criterion_of_kind_refuses_a_duplicate_rather_than_taking_the_first(store):
    """Where the engine needs *the* criterion, two is a question it will not
    answer — the value set to fetch, sc2's lookback window."""
    raw = _tree_dict()
    duplicate = deepcopy(_criterion(raw, "b"))
    duplicate["id"] = "b2"
    raw["criteria"].append(duplicate)
    tree = CriteriaTree.model_validate(raw)

    assert len(tree.criteria_of_kind(PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP)) == 2
    with pytest.raises(KeyError, match="declares 2 criteria of kind"):
        tree.only_criterion_of_kind(PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP)

    jf = store.get_tree("ncd-100.1-jf-v1")
    assert jf.only_criterion_of_kind(
        PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP
    ).id == "b"


# --------------------------------------------------------------------------
# Narrowing is declared per kind too (T-86, D99)
# --------------------------------------------------------------------------


def test_only_the_kinds_that_can_answer_not_met_declare_a_narrower():
    """The membership kinds and `note_event_count` abstain instead of
    answering `NOT_MET` (D13, D40, D111), so none has a re-derivation."""
    assert set(NARROWERS) <= set(PREDICATES)
    assert set(PredicateKind) - set(NARROWERS) == {
        PredicateKind.NOTE_EVENT_COUNT,
        PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP,
        PredicateKind.MEDICATION_VALUE_SET_ACTIVE,
    }


def test_a_not_met_from_a_kind_with_no_narrower_is_reported_as_a_defect(store):
    """Not silently passed. A kind re-derived over inputs nothing narrowed
    would agree with itself, which is a check that checked nothing."""
    jf = store.get_tree("ncd-100.1-jf-v1")
    impossible = CriterionResult(
        criterion_id="c1",
        verdict=CriterionVerdict.NOT_MET,
        spans=[SPAN],
        shortfall=Shortfall(observed=0, required=1, unit="events"),
    )
    with pytest.raises(CitationInsufficient, match="no re-derivation defined"):
        check_citation_sufficiency(
            jf.criterion("c1"),
            impossible,
            observations=[],
            procedures=[],
            value_sets={},
            run=criteria_module.QualifyingRun(months=(), events=()),
            as_of=AS_OF,
            c3_met=False,
        )
