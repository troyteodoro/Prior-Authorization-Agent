"""T-09 — the data contracts and the two storage ports.

The exit condition asks for the seven models, a `Document` carrying text and a
hash, the two Protocols with file-backed implementations, an assertion that no
class satisfies both, and a round trip proving the port preserves Article III.

Every test here is structural. No model call, no network, and nothing that reads
a file except through a store (REQ-41) — which is itself one of the assertions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    CallMetrics,
    CodeBinding,
    Condition,
    CoverageClaim,
    CriteriaTree,
    Criterion,
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    Document,
    EvidenceSpan,
    Observation,
    PolicyConstant,
    ProcedureEntry,
    ProcedureSets,
    ProgramAssertion,
    WmEvent,
)
from pa_agent.stores.patient import LocalPatientStore, PatientStore
from pa_agent.stores.policy import LocalPolicyStore, PolicyRef, PolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSWERS_PATH = REPO_ROOT / "data" / "policies" / "source" / "answers.json"
TREE_VERSION = "ncd-100.1-jf-v1"


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(policy_store: LocalPolicyStore) -> CriteriaTree:
    return policy_store.get_tree(TREE_VERSION)


# --------------------------------------------------------------------------
# The models the exit condition names
# --------------------------------------------------------------------------


def test_the_seven_models_exist_and_construct() -> None:
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4, quote="text")
    assert len(span) == 4

    criterion = Criterion(
        id="c1",
        label="Supervised program",
        constants={"min_events": PolicyConstant(value=1, type="integer")},
    )
    assert criterion.require("min_events") == 1

    result = CriterionResult(
        criterion_id="c1", verdict=CriterionVerdict.MET, spans=[span]
    )
    metrics = CallMetrics(
        model="m", purpose="extraction", input_tokens=10, output_tokens=2, wall_time_ms=1.0
    )
    determination = Determination(
        patient_id="p",
        procedure_code="43775",
        policy_version_id=TREE_VERSION,
        outcome=DeterminationOutcome.MET,
        criterion_results=[result],
        metrics=[metrics],
    )
    event = WmEvent(event_date=date(2025, 1, 15), span=span)

    assert determination.model_calls == 1
    assert determination.total_input_tokens == 10
    assert event.bmi is None
    assert CriterionVerdict.NOT_MET != CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_error_state_is_absent_and_belongs_to_t26() -> None:
    """Article IV names three states; two are built and the third has a task.

    Asserted rather than left implicit so that adding `ERROR` here without doing
    T-26 — the enum classifying every code retryable or terminal, and the
    validator refusing a determination that carries one — fails loudly.
    """
    assert not hasattr(CriterionVerdict, "ERROR")
    assert {v.value for v in CriterionVerdict} == {
        "MET",
        "NOT_MET",
        "INSUFFICIENT_EVIDENCE",
    }


# --------------------------------------------------------------------------
# Article III at the model level
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end",
    [(10, 10), (10, 4), (0, 0)],
    ids=["empty", "reversed", "zero-length-at-origin"],
)
def test_a_span_anchored_to_no_text_is_refused(start: int, end: int) -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(document_id="d", char_start=start, char_end=end)


def test_a_negative_offset_is_refused() -> None:
    with pytest.raises(ValidationError):
        EvidenceSpan(document_id="d", char_start=-1, char_end=5)


def test_a_document_whose_text_does_not_match_its_hash_is_refused() -> None:
    """REQ-7, enforced at construction rather than checked by whoever remembers."""
    with pytest.raises(ValidationError, match="Every span into this document is suspect"):
        Document(document_id="d", text="hello", sha256="0" * 64)


def test_document_from_text_hashes_what_it_wraps() -> None:
    doc = Document.from_text("d", "hello")
    assert doc.sha256 == hashlib.sha256(b"hello").hexdigest()


def test_slicing_past_the_end_raises_rather_than_truncating() -> None:
    doc = Document.from_text("d", "hello")
    with pytest.raises(ValueError, match="runs past the end"):
        doc.slice(EvidenceSpan(document_id="d", char_start=0, char_end=99))


def test_slicing_with_a_span_into_another_document_raises() -> None:
    doc = Document.from_text("d", "hello")
    with pytest.raises(ValueError, match="span cites other"):
        doc.slice(EvidenceSpan(document_id="other", char_start=0, char_end=2))


# --------------------------------------------------------------------------
# REQ-5 — the verdict and its spans move together
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", [CriterionVerdict.MET, CriterionVerdict.NOT_MET])
def test_a_substantiated_verdict_without_a_span_is_refused(
    verdict: CriterionVerdict,
) -> None:
    with pytest.raises(ValidationError, match="carries no span"):
        CriterionResult(criterion_id="c1", verdict=verdict, spans=[])


def test_an_abstention_carrying_a_span_is_refused() -> None:
    """The half of REQ-5 that is easy to skip.

    An `INSUFFICIENT_EVIDENCE` shipping a citation reads as a weak finding
    rather than as an absence, which is the collapse Article IV forbids dressed
    up as helpfulness.
    """
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    with pytest.raises(ValidationError, match="An abstention cites nothing"):
        CriterionResult(
            criterion_id="c1",
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            spans=[span],
        )


# --------------------------------------------------------------------------
# REQ-38 — each asserted flag carries its own span
# --------------------------------------------------------------------------


def test_a_flag_asserted_without_its_own_span_is_refused() -> None:
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    with pytest.raises(ValidationError, match="without a span of its own"):
        WmEvent(event_date=date(2025, 1, 1), span=span, diet_documented=True)


def test_a_span_without_its_flag_is_refused() -> None:
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    with pytest.raises(ValidationError, match="carries a span"):
        WmEvent(event_date=date(2025, 1, 1), span=span, activity_span=span)


def test_diet_and_activity_are_independently_spanned() -> None:
    """D24's reason c4 and c5 stay separate criteria, at the model level."""
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    diet = EvidenceSpan(document_id="d", char_start=10, char_end=20)
    event = WmEvent(
        event_date=date(2025, 1, 1),
        span=span,
        diet_documented=True,
        diet_span=diet,
    )
    assert event.diet_documented and not event.activity_documented


def test_a_program_assertion_is_not_an_event() -> None:
    """REQ-35: an assertion and an event are different types, so E8 cannot count one."""
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    assertion = ProgramAssertion(span=span)
    assert not isinstance(assertion, WmEvent)


# --------------------------------------------------------------------------
# REQ-20 and REQ-21 on the determination
# --------------------------------------------------------------------------


def test_met_over_an_unsupported_criterion_cannot_be_constructed() -> None:
    result = CriterionResult(
        criterion_id="c3", verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE
    )
    with pytest.raises(ValidationError, match="Unsupported never becomes met"):
        Determination(
            patient_id="p",
            procedure_code="43775",
            policy_version_id=TREE_VERSION,
            outcome=DeterminationOutcome.MET,
            criterion_results=[result],
        )


def test_the_gap_list_names_every_criterion_not_met() -> None:
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    results = [
        CriterionResult(criterion_id="a", verdict=CriterionVerdict.MET, spans=[span]),
        CriterionResult(criterion_id="c2", verdict=CriterionVerdict.NOT_MET, spans=[span]),
        CriterionResult(
            criterion_id="c3", verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE
        ),
    ]
    determination = Determination(
        patient_id="p",
        procedure_code="43775",
        policy_version_id=TREE_VERSION,
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
        criterion_results=results,
    )
    gaps = {g.criterion_id: g.verdict for g in determination.gap_list}
    assert gaps == {
        "c2": CriterionVerdict.NOT_MET,
        "c3": CriterionVerdict.INSUFFICIENT_EVIDENCE,
    }


def test_a_short_circuit_determination_carries_no_criteria(
) -> None:
    """E2 and E3 exit before any criterion is evaluated, and still record REQ-4."""
    determination = Determination(
        patient_id="p",
        # 43842, because pairing 43775 with NOT_COVERED — even in a synthetic
        # where any string would do — restates the claim D22 disproved.
        procedure_code="43842",
        policy_version_id=TREE_VERSION,
        outcome=DeterminationOutcome.NOT_COVERED,
    )

    assert determination.gap_list == []
    assert determination.model_calls == 0


# --------------------------------------------------------------------------
# D30 — an identity binding is sourced, and no code has two answers
# --------------------------------------------------------------------------


def _binding(**overrides):
    fields = dict(
        code="43842", system="CPT", identity=True, in_corpus=True,
        document_id="r931cp", char_start=15038, char_end=15092,
        quote="Open vertical banded gastroplasty ( HCPCS \ncode 43842)",
    )
    fields.update(overrides)
    return CodeBinding(**fields)


def test_an_unsourced_identity_binding_cannot_be_constructed() -> None:
    """The mechanism that put 43775 in the spec, refused at load (D29, D30)."""
    with pytest.raises(ValidationError, match="not in_corpus"):
        _binding(in_corpus=False)


def test_an_identity_quote_that_does_not_name_its_code_is_refused() -> None:
    with pytest.raises(ValidationError, match="does not name the code"):
        _binding(quote="Open vertical banded gastroplasty")


def test_a_code_bound_in_two_sets_cannot_be_constructed() -> None:
    """D26's collapse, refused by the contract itself so a production adapter
    cannot serve a colliding projection (D31)."""
    claim = CoverageClaim(document_id="d", char_start=0, char_end=4, quote="text")
    entry = ProcedureEntry(
        procedure="p", coverage_claim=claim, codes=[_binding()]
    )
    with pytest.raises(ValidationError, match="no code has two answers"):
        ProcedureSets(
            nationally_covered=[entry], nationally_non_covered=[entry]
        )


# --------------------------------------------------------------------------
# D23 — a constant is sourced or it is flagged
# --------------------------------------------------------------------------


def test_a_provisional_constant_carrying_a_value_is_refused() -> None:
    with pytest.raises(ValidationError, match="a flagged default is still a default"):
        PolicyConstant(value=6, type="integer_months", provisional=True, open_question=4)


def test_a_provisional_constant_without_its_question_is_refused() -> None:
    with pytest.raises(ValidationError, match="names the open question"):
        PolicyConstant(value=None, type="integer_months", provisional=True)


def test_requiring_a_provisional_constant_raises_rather_than_defaulting(
    tree: CriteriaTree,
) -> None:
    """Questions 4 and 5 are open, and T-13 and T-33 are told not to default them.

    This is that instruction made mechanical. `require()` on criterion (a)'s
    lookback fails today and will keep failing until the question is answered,
    which is the correct behavior for a window nobody has sourced.
    """
    criterion_a = tree.criterion("a")
    assert criterion_a.constant("lookback_months").provisional
    with pytest.raises(ValueError, match="Resolve the question, do not default it"):
        criterion_a.require("lookback_months")

    assert criterion_a.require("bmi_threshold") == 35.0


# --------------------------------------------------------------------------
# The ports
# --------------------------------------------------------------------------


def test_both_protocols_are_satisfied_by_their_local_adapters() -> None:
    assert isinstance(LocalPolicyStore(), PolicyStore)
    assert isinstance(LocalPatientStore(), PatientStore)


def test_no_single_class_satisfies_both_protocols() -> None:
    """REQ-41's load-bearing clause, and Article VI's.

    A combined handle is a module that can reach both planes, and T-32's
    import-graph walk would have nothing left to prove. Asserted on the adapters
    that exist and on the protocols themselves, so a later `Store` that inherits
    from both fails here rather than in review.
    """
    assert not isinstance(LocalPolicyStore(), PatientStore)
    assert not isinstance(LocalPatientStore(), PolicyStore)

    policy_methods = {"resolve", "get_tree", "get_document"}
    patient_methods = {"get_observations", "get_conditions", "get_notes"}
    assert policy_methods.isdisjoint(patient_methods)

    for candidate in (LocalPolicyStore, LocalPatientStore):
        has_policy = policy_methods.issubset(dir(candidate))
        has_patient = patient_methods.issubset(dir(candidate))
        assert not (has_policy and has_patient), f"{candidate.__name__} reaches both planes"


def test_the_stores_package_imports_neither_plane() -> None:
    """A package-level re-export would be exactly the module Article VI denies."""
    import pa_agent.stores as stores

    assert not hasattr(stores, "PolicyStore")
    assert not hasattr(stores, "PatientStore")


def test_the_policy_store_returns_the_t01_tree(tree: CriteriaTree) -> None:
    assert tree.policy_version_id == TREE_VERSION
    assert [c.id for c in tree.criteria] == ["a", "b", "c1", "c2", "c3", "c4", "c5"]
    assert tree.jurisdiction.authority == "mac_jurisdiction_f"
    assert tree.criterion("c5").require("documentation_rate") == "every_month_of_run"


def test_an_unknown_policy_version_raises(policy_store: LocalPolicyStore) -> None:
    with pytest.raises(KeyError, match="no criteria tree"):
        policy_store.get_tree("ncd-100.1-jx-v9")


def test_the_policy_store_returns_the_t02_corpus_at_its_recorded_hash(
    policy_store: LocalPolicyStore,
) -> None:
    """The store hands back documents whose hashes match `sources.json`.

    `Document`'s validator does the comparison, so reaching this assertion at
    all means every document on disk still hashes to what T-02 recorded.
    """
    manifest = json.loads(
        (REPO_ROOT / "data" / "policies" / "source" / "sources.json").read_text()
    )
    recorded = {d["document_id"]: d["sha256"] for d in manifest["documents"]}
    assert policy_store.document_ids() == sorted(recorded)

    for document_id, sha256 in recorded.items():
        assert policy_store.get_document(document_id).sha256 == sha256


def test_an_unknown_document_raises(policy_store: LocalPolicyStore) -> None:
    with pytest.raises(KeyError, match="no document"):
        policy_store.get_document("lcd_99999")


def test_a_tree_source_hash_matches_the_document_the_store_serves(
    policy_store: LocalPolicyStore, tree: CriteriaTree
) -> None:
    """The tree records what it was compiled against; the store serves the same bytes."""
    for source in tree.sources:
        assert policy_store.get_document(source.document_id).sha256 == source.sha256


# --------------------------------------------------------------------------
# The round trip — the port preserves Article III
# --------------------------------------------------------------------------


def test_a_t02_answer_slices_back_out_through_the_store(
    policy_store: LocalPolicyStore,
) -> None:
    """`get_document()` then slice at a recorded offset returns the recorded quote.

    This is the assertion that makes the port worth having. Storage moved from
    a file read to a method call, and a citation written in T-02 still resolves
    to the same characters. When the adapter becomes a database, this test is
    what says the guarantee survived the move.
    """
    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))["answers"]
    assert answers, "T-02 recorded no answers"

    checked = 0
    for answer in answers:
        citations = [answer] + (
            [answer["corroborating_quote"]] if "corroborating_quote" in answer else []
        )
        for citation in citations:
            document = policy_store.get_document(citation["document_id"])
            span = EvidenceSpan(
                document_id=citation["document_id"],
                char_start=citation["char_start"],
                char_end=citation["char_end"],
                quote=citation["quote"],
            )
            assert document.slice(span) == span.quote
            checked += 1

    assert checked >= 4, f"expected T-02's three answers plus a corroboration, got {checked}"


def test_an_offset_shifted_by_one_no_longer_slices_to_its_quote(
    policy_store: LocalPolicyStore,
) -> None:
    """Mutation check: the round trip is asserting something.

    A test that only ever slices correct offsets would pass against a `slice()`
    that ignored them.
    """
    answer = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))["answers"][0]
    document = policy_store.get_document(answer["document_id"])
    shifted = EvidenceSpan(
        document_id=answer["document_id"],
        char_start=answer["char_start"] + 1,
        char_end=answer["char_end"] + 1,
    )
    assert document.slice(shifted) != answer["quote"]


# --------------------------------------------------------------------------
# The unimplemented halves, asserted rather than assumed
# --------------------------------------------------------------------------


def test_resolve_reports_membership_facts(policy_store: LocalPolicyStore) -> None:
    """T-24: the port reports which set binds a code, and judges nothing (D31).

    Until T-38 this asserted a raise with `match="T-38"` — and kept passing
    after the message was re-cited to T-24, because "T-38" survived in a
    parenthetical. D31 records the miss; the replacement asserts behavior, not
    message substrings. The sc1 judgment tests live in `tests/test_resolver.py`.
    """
    ref = policy_store.resolve("43842")
    assert ref is not None and ref.coverage.value == "nationally_non_covered"
    assert ref.policy_version_id == TREE_VERSION
    assert policy_store.resolve("99213") is None, (
        "a code no tree binds resolves to None — NO_POLICY_FOUND, which is not "
        "a denial (D26)"
    )


def test_the_patient_store_refuses_unknown_patients(method: str = "") -> None:
    """An empty list would manufacture E7 for every patient while looking
    correct, so a patient the manifest does not list raises (T-09's argument,
    kept through T-12's close)."""
    store = LocalPatientStore()
    for method in ("get_observations", "get_conditions"):
        with pytest.raises(KeyError, match="patient-1"):
            getattr(store, method)("patient-1")


def test_the_patient_store_refuses_notes_until_t07() -> None:
    """The FHIR reads are built (T-12, D39); the note corpus is not. The raise
    names T-07 and deliberately does not contain "T-12", so a stale match
    fails loudly instead of passing by substring (D31)."""
    with pytest.raises(NotImplementedError, match="T-07"):
        LocalPatientStore().get_notes("patient-1")


def test_the_patient_contracts_exist_for_t12_to_fill() -> None:
    observation = Observation(code="39156-5", value=36.2, unit="kg/m2",
                              effective_date=date(2025, 3, 1))
    condition = Condition(code="44054006", system="http://snomed.info/sct")
    assert observation.value == 36.2 and condition.onset_date is None


def test_no_model_is_imported_by_the_contracts_or_the_stores() -> None:
    """Article II's boundary, checked the way T-11's exit condition checks it."""
    import sys

    for module in (
        "pa_agent.contracts",
        "pa_agent.stores.policy",
        "pa_agent.stores.patient",
    ):
        __import__(module)
    assert "google.adk" not in sys.modules
