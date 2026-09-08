"""T-24 — the resolver answers sc1 from the tree, and the three answers stay three.

The exit condition: E3's code returns `NOT_COVERED`, an unknown code returns
`NO_POLICY_FOUND`, and the model-call counter reads zero. Underneath those sit
the distinctions this repo exists to keep apart:

- "a policy says no" and "no policy says anything" are two result *types*, not
  one absence (D26, REQ-1, REQ-2).
- the denial a `NotCovered` carries slices back out of the hashed corpus
  through the store (Art. III) — a resolution that cannot cite its policy is
  someone's memory with a verdict attached.
- a contractor-determined code is a third outcome, `ResolvedByContractor`
  (REQ-42, D33 — T-36's decision, landed in this suite as its exit promised).
  Same flow as a covered code, distinct type: the delegation must be
  acknowledged before the code is treated as covered, so that a second
  jurisdiction whose MAC answers differently arrives as a type error instead
  of a silent merge.

No model is involved anywhere here (Art. II), and the test proves it by
imports, not by trust.
"""

from __future__ import annotations

import pytest

from pa_agent.contracts import CoverageStatus
from pa_agent.resolver import (
    NoPolicyFound,
    NotCovered,
    Resolved,
    ResolvedByContractor,
    resolve_sc1,
)
from pa_agent.stores.policy import LocalPolicyStore

TREE_VERSION = "ncd-100.1-jf-v1"

# E3's code (D28) and a code no bariatric policy governs — a routine E/M office
# visit, REQ-1's case. 43775 is the contractor-determined code D22 moved.
E3_CODE = "43842"
FOREIGN_CODE = "99213"
CONTRACTOR_CODE = "43775"
COVERED_CODE = "43644"


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


# --------------------------------------------------------------------------
# The exit condition's two named answers
# --------------------------------------------------------------------------


def test_e3s_code_resolves_not_covered(store):
    result = resolve_sc1(store, E3_CODE)
    assert isinstance(result, NotCovered)
    assert result.procedure_code == E3_CODE
    assert result.policy_version_id == TREE_VERSION, (
        "REQ-4: the determination built from this must be replayable against "
        "the exact policy version"
    )


def test_an_unknown_code_resolves_no_policy_found(store):
    result = resolve_sc1(store, FOREIGN_CODE)
    assert isinstance(result, NoPolicyFound)
    assert result.procedure_code == FOREIGN_CODE


def test_the_two_absent_from_covered_answers_are_different_types(store):
    """D26's defect, asserted on the results themselves: both 43842 and 99213
    are absent from the covered set, and they must not resolve alike."""
    denial = resolve_sc1(store, E3_CODE)
    silence = resolve_sc1(store, FOREIGN_CODE)
    assert type(denial) is not type(silence)
    assert not isinstance(silence, NotCovered)


# --------------------------------------------------------------------------
# The denial cites its policy, and the citation survives slicing (Art. III)
# --------------------------------------------------------------------------


def test_not_covered_carries_a_claim_that_slices_back(store):
    claim = resolve_sc1(store, E3_CODE).coverage_claim
    document = store.get_document(claim.document_id)
    assert document.slice(claim) == claim.quote, (
        "the resolver's denial does not slice back to its quote; it is citing "
        "a document that changed or a span someone typed"
    )


def test_the_claim_is_scoped_by_the_non_covered_sentence(store):
    """A bullet alone names a procedure. The scoping sentence is the denial,
    and it must arrive with the claim so T-25 can cite both (D28)."""
    claim = resolve_sc1(store, E3_CODE).coverage_claim
    assert claim.scoping_quote is not None
    assert "non-covered for all Medicare beneficiaries" in claim.scoping_quote.quote
    scope_doc = store.get_document(claim.scoping_quote.document_id)
    assert scope_doc.slice(claim.scoping_quote) == claim.scoping_quote.quote


# --------------------------------------------------------------------------
# A covered code proceeds to the tree
# --------------------------------------------------------------------------


def test_a_covered_code_resolves_to_the_tree(store):
    result = resolve_sc1(store, COVERED_CODE)
    assert isinstance(result, Resolved)
    assert result.policy_ref.policy_version_id == TREE_VERSION
    assert result.policy_ref.coverage is CoverageStatus.NATIONALLY_COVERED
    # The version id it names loads a real tree through the same port.
    tree = store.get_tree(result.policy_ref.policy_version_id)
    assert tree.policy_version_id == TREE_VERSION


# --------------------------------------------------------------------------
# The contractor-determined branch: a third outcome, distinct in type
# (REQ-42, D33 — T-36's exit lands here)
# --------------------------------------------------------------------------


def test_the_store_reports_the_contractor_fact_without_raising(store):
    ref = store.resolve(CONTRACTOR_CODE)
    assert ref is not None, (
        "the store hid a membership fact T-38 landed and spanned. Facts live "
        "in the port; refusal lives in the resolver (D31)."
    )
    assert ref.coverage is CoverageStatus.CONTRACTOR_DETERMINED


def test_a_contractor_code_is_a_third_outcome(store):
    """The exit condition's phrase, on types: distinct from both `NOT_COVERED`
    and a covered code, and from no-policy silence while we are at it."""
    result = resolve_sc1(store, CONTRACTOR_CODE)
    assert isinstance(result, ResolvedByContractor)
    for other in (NotCovered, Resolved, NoPolicyFound):
        assert not isinstance(result, other)
    assert type(result) is not type(resolve_sc1(store, COVERED_CODE)), (
        "a delegated procedure resolved as the same type as a nationally "
        "covered one; the merge D33 exists to forbid is back"
    )


def test_the_contractor_outcome_proceeds_to_the_tree(store):
    """Same flow as covered (D33): the ref names a loadable tree and records
    the membership fact, and REQ-4's version id rides along."""
    result = resolve_sc1(store, CONTRACTOR_CODE)
    assert result.policy_ref.policy_version_id == TREE_VERSION
    assert result.policy_ref.coverage is CoverageStatus.CONTRACTOR_DETERMINED
    tree = store.get_tree(result.policy_ref.policy_version_id)
    assert tree.policy_version_id == TREE_VERSION


def test_the_delegation_and_the_macs_exercise_both_slice_back(store):
    """REQ-42's two citations (Art. III): the NCD's delegation is the claim,
    and the MAC's exercise of it is the corroborating quote. An artifact citing
    the NCD alone would cite a document that deliberately does not answer."""
    claim = resolve_sc1(store, CONTRACTOR_CODE).policy_ref.coverage_claim
    assert claim.document_id == "ncd_100_1"
    assert "may determine coverage" in claim.quote
    assert store.get_document(claim.document_id).slice(claim) == claim.quote

    exercise = claim.corroborating_quote
    assert exercise is not None, (
        "the delegation arrived without the MAC's exercise; T-19 cannot cite "
        "what the resolution does not carry (D33)"
    )
    assert exercise.document_id == "a53028"
    exercise_doc = store.get_document(exercise.document_id)
    assert exercise_doc.slice(exercise) == exercise.quote


# --------------------------------------------------------------------------
# Zero model calls, by construction and by import graph
# --------------------------------------------------------------------------


def test_no_model_is_imported_by_the_resolver():
    """The exit condition's counter reads zero because nothing can increment
    it: no model client is loaded, and no result type has anywhere to record a
    call. Checked in a fresh interpreter, because this suite itself imports
    `pa_agent.model_pin` elsewhere and a `sys.modules` check here would inherit
    that pollution and pass or fail by test ordering."""
    import subprocess
    import sys

    probe = (
        "import sys; import pa_agent.resolver; "
        "assert 'google.adk' not in sys.modules, 'resolver pulled in the ADK'; "
        "assert 'pa_agent.model_pin' not in sys.modules, "
        "'resolver pulled in the model pin'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_no_result_type_can_carry_call_metrics(store):
    for code in (E3_CODE, FOREIGN_CODE, COVERED_CODE, CONTRACTOR_CODE):
        result = resolve_sc1(store, code)
        assert "metrics" not in type(result).model_fields, (
            "a resolution result grew a metrics field; sc1 is reached with "
            "zero model calls by construction (REQ-2, A4)"
        )


def test_the_resolver_imports_nothing_from_the_patient_plane():
    """REQ-33: resolution is policy-plane. One patient import here and the
    import-graph assertion T-32 builds would have a counterexample already."""
    import pa_agent.resolver as resolver_module

    source = open(resolver_module.__file__, encoding="utf-8").read()
    assert "patient" not in source.lower()


# --------------------------------------------------------------------------
# Deterministic (REQ-1)
# --------------------------------------------------------------------------


def test_resolution_is_deterministic(store):
    for code in (E3_CODE, FOREIGN_CODE, COVERED_CODE, CONTRACTOR_CODE):
        assert resolve_sc1(store, code) == resolve_sc1(store, code)
