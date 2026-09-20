"""T-92 — the second practice loads, resolves and evaluates (US-10, D111).

v1.2 asks one question: does the engine take a tree from an unrelated practice
without a rewrite? This file answers it for rheumatoid arthritis, compiled from
Palmetto GBA's L35677 and A56432.

What it pins, in the order the question breaks down:

- **Resolution.** Palmetto serves the same seven states for bariatric surgery
  and for infliximab, so a state is served by one tree *per practice*. Each
  code still resolves to exactly one tree, and a code no tree binds for a
  served state is `NO_POLICY_FOUND` — never a tree picked by load order.
- **Dispatch.** The tree letters its criteria `a` and `b`, as the bariatric
  trees do and as coverage documents do generally. `a` is a diagnosis set and
  `b` a medication set, and both answer by the **kind** they declare (D110).
  Nothing here would pass if dispatch still read the letter.
- **Declared limits.** Four of six statements in the document are judgment or
  are absent from the coded record. They are declared, abstained on and never
  omitted (REQ-58), and each is unclaimed because of the document or the
  chart — never because the engine lacks a predicate (REQ-57).
- **The exclusion.** L35677 *denies* when infliximab is combined with another
  biologic, and an exclusion is how that is expressed: it fires citing the
  prescriptions that fired it, or is silent. A criterion could not, because
  `MET` for a clean chart would be a verdict with no span (REQ-5, D111).

No model is involved: the runner handed in raises if it is ever called, which
is A4's shape and the reason `zero model calls` is a property of the graph
rather than of a counter.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriterionVerdict,
    DeterminationOutcome,
    ExclusionKind,
    GapReason,
    Medication,
    PredicateKind,
)
from pa_agent.determination import determine
from pa_agent.contracts import Condition
from pa_agent.resolver import NoPolicyFound, ResolvedByContractor, resolve_sc1
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

from conftest import AcceptAllVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_ID = "infliximab-ra-jjm-v1"
J_CODE = "J1745"
BARIATRIC_CODE = "43775"
PALMETTO_STATE = "AL"
NORIDIAN_STATE = "WA"
AS_OF = date(2026, 3, 1)

SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
RA_SNOMED = "69896004"
#: Synthea's own rheumatoid arthritis module orders this one.
METHOTREXATE_TABLET = "105585"
ETANERCEPT = "253014"

#: Every criterion span is re-sliced by the graph's span pass before a
#: determination ships (Article III at runtime, D76), so the facts written in
#: this file cite a **real** committed bundle. The condition whose extent is
#: borrowed is any one of a real patient's; what is synthetic here is which
#: conditions and medications the chart reports, not the document they cite.
_PATIENTS = LocalPatientStore()
_HOST_PATIENT = json.loads(
    (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
)["bundles"][0]["patient_id"]
SPAN = next(
    c.span for c in _PATIENTS.get_conditions(_HOST_PATIENT) if c.span is not None
)


class _RaisingRunner:
    """A4: reached only if the graph asks a model something. It must not."""

    name = "raises"

    def run(self, *args, **kwargs):  # pragma: no cover - the point is it is unused
        raise AssertionError(
            "the rheumatology tree declares no note-event criterion; extraction "
            "must never be reached"
        )


class _Chart:
    """A patient store reporting the facts written in the test.

    Only the methods the graph calls, so a step reaching for anything else
    fails loudly instead of finding a stub that answers (T-18's rule) — with
    `get_document` delegated to the real adapter, because the graph re-slices
    every cited span before it ships anything.
    """

    def __init__(self, conditions=(), medications=(), state=PALMETTO_STATE) -> None:
        self._conditions = list(conditions)
        self._medications = list(medications)
        self._state = state

    def get_document(self, document_id: str):
        return _PATIENTS.get_document(document_id)

    def get_observations(self, patient_id: str) -> list:
        return []

    def get_conditions(self, patient_id: str) -> list:
        return list(self._conditions)

    def get_medications(self, patient_id: str) -> list:
        return list(self._medications)

    def get_notes(self, patient_id: str) -> list:
        return []

    def get_jurisdiction_state(self, patient_id: str) -> str:
        return self._state


def _ra() -> Condition:
    return Condition(
        code=RA_SNOMED, system=SNOMED, clinical_status="active", span=SPAN
    )


def _drug(code: str, status: str = "active") -> Medication:
    return Medication(code=code, system=RXNORM, status=status, span=SPAN)


def _determine(chart: _Chart):
    return determine(
        LocalPolicyStore(),
        J_CODE,
        patient_id="patient-under-test",
        patient_store=chart,
        as_of=AS_OF,
        extraction_runner=_RaisingRunner(),
        verifier=AcceptAllVerifier(),
    )


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(store):
    return store.get_tree(TREE_ID)


# --------------------------------------------------------------------------
# Resolution: one state, two practices
# --------------------------------------------------------------------------


def test_one_state_serves_two_practices_and_each_code_resolves_to_one_tree(store):
    """The rule T-92 replaced would have refused to load this store at all."""
    serving = {t.policy_version_id for t in store.trees_for_state(PALMETTO_STATE)}
    assert {TREE_ID, "ncd-100.1-jjm-v1"} <= serving

    infliximab = store.resolve(J_CODE, PALMETTO_STATE)
    bariatric = store.resolve(BARIATRIC_CODE, PALMETTO_STATE)
    assert infliximab is not None and infliximab.policy_version_id == TREE_ID
    assert bariatric is not None
    assert bariatric.policy_version_id == "ncd-100.1-jjm-v1"


def test_the_j_code_is_contractor_determined_and_cites_both_halves(store):
    """Part B drug coverage is the contractor's under 1862(a)(1)(A), so the
    claim is the statute and the corroborating quote is Palmetto applying it —
    D33's two-part shape, and the reason the outcome type is distinct."""
    result = resolve_sc1(store, J_CODE, PALMETTO_STATE)
    assert isinstance(result, ResolvedByContractor)
    claim = result.policy_ref.coverage_claim
    assert claim.document_id == "l35677"
    document = store.get_document(claim.document_id)
    assert document.slice(claim) == claim.quote
    assert claim.corroborating_quote is not None
    assert document.slice(claim.corroborating_quote) == claim.corroborating_quote.quote


def test_a_practice_no_tree_serves_in_this_state_is_no_policy_found(store):
    """Noridian serves Washington for bariatric surgery and this store holds no
    rheumatology tree for it. `NO_POLICY_FOUND` is the honest answer — no policy
    *here* binds the code — and it is not a denial (D26). `NO_JURISDICTION_TREE`
    keeps its own meaning: no tree serves the state at all (REQ-55)."""
    assert isinstance(resolve_sc1(store, J_CODE, NORIDIAN_STATE), NoPolicyFound)


# --------------------------------------------------------------------------
# Dispatch reads the declared kind, not the letter
# --------------------------------------------------------------------------


def test_the_letters_collide_with_the_bariatric_trees_and_the_kinds_do_not(store):
    """`a` and `b` mean different arithmetic in the two practices. If dispatch
    still read the id, this tree's `a` would be measured against a BMI
    threshold it does not declare (D110)."""
    ra = store.get_tree(TREE_ID)
    bariatric = store.get_tree("ncd-100.1-jjm-v1")
    assert {c.id for c in ra.criteria} & {c.id for c in bariatric.criteria} == {
        "a",
        "b",
        "d",
    }
    assert ra.criterion("a").kind is PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP
    assert bariatric.criterion("a").kind is PredicateKind.BMI_OBSERVATION_THRESHOLD
    assert ra.criterion("b").kind is PredicateKind.MEDICATION_VALUE_SET_ACTIVE


def test_the_unclaimed_criteria_are_the_documents_limits_not_the_engines(tree):
    """REQ-57's distinction, asserted on the tree a reviewer reads. Each
    unclaimed criterion names a reason about the document or the chart; none of
    them says the engine lacks a predicate."""
    unclaimed = [c for c in tree.criteria if c.evaluation == "unclaimed"]
    assert [c.id for c in unclaimed] == ["c", "d", "e"]
    for criterion in unclaimed:
        assert criterion.kind is None, "an unclaimed criterion declares no kind"
        assert criterion.note and criterion.note.strip()
    assert "not in the coded record" in unclaimed[0].note
    assert "screening result" in unclaimed[1].note
    assert "clinical assessment" in unclaimed[2].note


# --------------------------------------------------------------------------
# A determination, end to end, with no model call
# --------------------------------------------------------------------------


def _verdicts(determination) -> dict[str, CriterionVerdict]:
    return {r.criterion_id: r.verdict for r in determination.criterion_results}


def test_a_chart_with_the_diagnosis_and_the_drug_meets_both_claimed_criteria():
    determination = _determine(_Chart([_ra()], [_drug(METHOTREXATE_TABLET)]))
    verdicts = _verdicts(determination)
    assert verdicts["a"] is CriterionVerdict.MET
    assert verdicts["b"] is CriterionVerdict.MET
    assert [r.spans for r in determination.criterion_results if r.criterion_id == "b"][
        0
    ], "a MET with no span is a verdict nobody can check (REQ-5)"


def test_every_declared_criterion_produces_a_verdict_and_none_is_omitted(tree):
    determination = _determine(_Chart([_ra()], [_drug(METHOTREXATE_TABLET)]))
    assert _verdicts(determination).keys() == {c.id for c in tree.criteria}


def test_the_unclaimed_criteria_abstain_and_say_which_they_are():
    """REQ-58: declared, abstained on with `NOT_EVALUATED_BY_THIS_SYSTEM`, and
    never omitted — so the determination cannot come out `MET` overall and it
    names what a reviewer still owes (D101's rule, on a second practice)."""
    determination = _determine(_Chart([_ra()], [_drug(METHOTREXATE_TABLET)]))
    for criterion_id in ("c", "d", "e"):
        result = next(
            r for r in determination.criterion_results if r.criterion_id == criterion_id
        )
        assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
        assert result.gap_reason is GapReason.NOT_EVALUATED_BY_THIS_SYSTEM
        assert not result.spans
    assert determination.outcome is DeterminationOutcome.INSUFFICIENT_EVIDENCE


def test_no_methotrexate_abstains_and_never_answers_not_met():
    """D40's asymmetry, on a medication. The document covers a patient unable
    to tolerate methotrexate whose reason is documented in the record, and this
    system does not read that record — so a `NOT_MET` would deny a patient on a
    chart that does not disagree."""
    determination = _determine(_Chart([_ra()], []))
    result = next(r for r in determination.criterion_results if r.criterion_id == "b")
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert result.verdict is not CriterionVerdict.NOT_MET


def test_a_completed_prescription_is_not_an_active_one():
    """`status` is carried by the adapter and filtered by the predicate (D31,
    D39). The false `MET` this keeps out is a drug the patient finished taking."""
    determination = _determine(
        _Chart([_ra()], [_drug(METHOTREXATE_TABLET, status="completed")])
    )
    verdicts = _verdicts(determination)
    assert verdicts["b"] is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_a_code_in_the_set_under_another_system_is_not_a_member():
    """REQ-59. Membership is tested within the set's declared system, so an
    RxNorm identifier arriving labelled SNOMED does not match. Two vocabularies
    collide on short numeric codes, and a bare code comparison cannot tell."""
    determination = _determine(
        _Chart(
            [_ra()],
            [Medication(code=METHOTREXATE_TABLET, system=SNOMED, status="active", span=SPAN)],
        )
    )
    assert _verdicts(determination)["b"] is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_the_determination_spends_no_model_call():
    """Article X's counter, and the runner that would have raised (A4)."""
    determination = _determine(_Chart([_ra()], [_drug(METHOTREXATE_TABLET)]))
    assert sum(m.calls for m in determination.metrics) == 0


# --------------------------------------------------------------------------
# The exclusion denies; it does not fail a criterion
# --------------------------------------------------------------------------


def test_a_concurrent_biologic_is_not_covered_and_cites_the_prescription():
    determination = _determine(
        _Chart([_ra()], [_drug(METHOTREXATE_TABLET), _drug(ETANERCEPT)])
    )
    assert determination.outcome is DeterminationOutcome.NOT_COVERED
    assert determination.exclusion_evidence, (
        "a denial with no citation is Article III skipped"
    )
    assert determination.criterion_results == [], (
        "a short circuit answers before the criteria chain (D41)"
    )
    assert "not covered" in determination.coverage_claim.quote


def test_the_exclusion_is_silent_on_a_chart_that_does_not_trip_it():
    """Not an abstention: an exclusion is not a criterion and has no verdict to
    abstain with. Silence is how it does not fire (D41, D111)."""
    determination = _determine(_Chart([_ra()], [_drug(METHOTREXATE_TABLET)]))
    assert determination.outcome is not DeterminationOutcome.NOT_COVERED
    assert not determination.exclusion_evidence


def test_a_completed_biologic_does_not_deny():
    determination = _determine(
        _Chart([_ra()], [_drug(METHOTREXATE_TABLET), _drug(ETANERCEPT, status="completed")])
    )
    assert determination.outcome is not DeterminationOutcome.NOT_COVERED


def test_the_exclusion_declares_a_kind_and_is_scoped_to_this_codes_set(tree):
    """Scope is compared against the set the request resolved in. The NCD's own
    exclusion scopes to `nationally_covered` and therefore still never reaches
    a delegated procedure, which is D41's rule restated as a comparison."""
    exclusion = tree.categorical_exclusions[0]
    assert exclusion.kind is ExclusionKind.ACTIVE_MEDICATION_VALUE_SET
    assert exclusion.procedure_scope == "contractor_determined"


def test_an_exclusion_kind_the_engine_lacks_fails_at_load():
    """`UnknownExclusionKind`'s reason, at the contract boundary: an exclusion
    the dispatcher cannot place would have to be skipped, and a skipped
    exclusion approves past a denial the policy states outright."""
    from pydantic import ValidationError

    from pa_agent.contracts import CriteriaTree

    raw = json.loads(
        (REPO_ROOT / "data" / "policies" / "infliximab_ra_jjm.json").read_text(
            encoding="utf-8"
        )
    )
    raw["categorical_exclusions"][0]["kind"] = "active_allergy_value_set"
    with pytest.raises(ValidationError):
        CriteriaTree.model_validate(raw)


# --------------------------------------------------------------------------
# The value sets this tree names
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value_set_id,system",
    [
        ("rheumatoid_arthritis", SNOMED),
        ("methotrexate", RXNORM),
        ("biologic_dmards_and_jak_inhibitors", RXNORM),
    ],
)
def test_each_value_set_declares_the_system_it_is_matched_in(
    store, value_set_id, system
):
    value_set = store.get_value_set(value_set_id)
    assert value_set.system == system
    assert value_set.codes


def test_the_diagnosis_set_matches_the_code_syntheas_module_emits(store):
    """The reason the SNOMED code is this one and not one recalled: D52's
    failure is a set that loads cleanly and matches nobody, and no
    credential-free source binds SNOMED to ICD-10 (D36, re-checked for T-92)."""
    assert store.get_value_set("rheumatoid_arthritis").admits(RA_SNOMED, SNOMED)


def test_infliximab_is_not_in_its_own_exclusion_set(store):
    """The limitation is about what infliximab is combined with. A set holding
    infliximab would deny every request this tree governs."""
    biologics = store.get_value_set("biologic_dmards_and_jak_inhibitors")
    assert not biologics.admits("191831", RXNORM), "the ingredient itself is a member"
    assert biologics.admits(ETANERCEPT, RXNORM)


def test_the_methotrexate_set_and_the_exclusion_set_are_disjoint(store):
    """`b` requires the drug and the exclusion denies for it would be a tree
    that cannot be satisfied."""
    methotrexate = store.get_value_set("methotrexate")
    biologics = store.get_value_set("biologic_dmards_and_jak_inhibitors")
    assert not (methotrexate.codes & biologics.codes)
