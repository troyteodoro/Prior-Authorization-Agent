"""T-103 — the packet: `pa_agent/form.py`, and the four ways it refuses (REQ-74, D131).

**Spends no model call and touches no network.** The one determination this file
computes replays `T-15`'s extraction recording and `T-98`'s quote recording, the
same two replays `--suggest` has used since `T-99`.

Two halves, and the split is the one D65 argues for.

**Behavioural**, over the committed corpus wherever the corpus can produce the
shape: a packet whose citations are counted and validated one by one — the
denominator A13's second clause needs, because *every citation valid* is
satisfied by a packet with none; a suggestion that enters only through a
recorded acceptance; and the four refusals. The two-red case is hand-built,
because the corpus produces exactly one red per chart and with one red
`', '.join(codes)` and `codes[0]` are the same string.

**Parse-level**, because no behavioural test on this corpus separates these:
`form.py` declares no `_render` and imports nothing from `pa_agent.cli` (a
second renderer answers identically until v2.2 compares the two surfaces); it
names no clock (a clock-derived `Date:` renders identically *once*, and
`T-106`'s fixture is what would catch it a month later); and the refusal joins
**all** codes rather than indexing the first.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
import re
from datetime import date
from pathlib import Path

import pytest

from pa_agent import form
from pa_agent.contracts import (
    CodedConcept,
    Determination,
    DeterminationOutcome,
    Document,
    EffectSignal,
    EvidenceSpan,
    HistoryReview,
    IcdSuggestion,
    Intake,
    Packet,
    ReviewAction,
    ReviewEntry,
    Session,
    SessionRun,
    SessionState,
    SuggestionColour,
)
from pa_agent.determination import determine
from pa_agent.index import DocumentIndex
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.verifier import RecordedVerifierRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
FORM_SOURCE = REPO_ROOT / "pa_agent" / "form.py"
EXTRACTION_RECORDING = REPO_ROOT / "eval" / "extraction" / "results.json"
VERIFIER_RECORDING = REPO_ROOT / "eval" / "verifier" / "results.json"

#: The harness clock (`eval/run_eval.py`'s `EVAL_AS_OF`). Pinned, because a
#: verdict moves with it and the verifier recording is keyed by the verdict
#: (D78's claim digest) — a determination at today's clock raises `not_recorded`
#: on a criterion whose answer changed, which is the recording doing its job.
AS_OF = date(2026, 9, 1)

#: `E4`'s chart: the one whose qualifying run straddles both notes, so its
#: determination cites the bundle *and* two note documents (T-81, D104) and the
#: citation count this file asserts is not one span wearing a plural.
CITING_PATIENT = "07a5f345-3e7c-da0f-da0b-87fa252a5bfd"
COVERED_CODE = "43775"

#: Open vertical banded gastroplasty: the code NCD 100.1 non-covers for all
#: beneficiaries with no date qualifier (D22, D28), so a determination over it
#: short-circuits at the resolver and cites the policy corpus alone.
NON_COVERED_CODE = "43842"

#: `E12`'s chart, which `H6` grades: note-free by declaration, and the review
#: colours its one candidate **red** with nothing on the chart (D119, D123).
#: Verified at this close rather than assumed — `test_the_corpus_still_produces
#: _a_red_suggestion` is what fails if the table or the bundle moves.
RED_PATIENT = "a8edc52e-9800-0adb-c775-bd81183c355f"


# --------------------------------------------------------------------------
# The corpus, replayed
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policies() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patients() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def runner() -> RecordedExtractionRunner:
    payload = json.loads(EXTRACTION_RECORDING.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        payload["notes"], model=payload.get("model")
    )


@pytest.fixture(scope="module")
def verifier() -> RecordedVerifierRunner:
    payload = json.loads(VERIFIER_RECORDING.read_text(encoding="utf-8"))
    return RecordedVerifierRunner.from_records(
        payload["claims"], model=payload.get("model")
    )


def _determine(policies, patients, runner, verifier, patient_id, code=COVERED_CODE):
    return determine(
        policies,
        code,
        patient_id=patient_id,
        patient_store=patients,
        as_of=AS_OF,
        extraction_runner=runner,
        verifier=verifier, payer="medicare",
    )


def _session(determination: Determination, *, reviews=(), icd10=("E66.01",)) -> Session:
    """One `DETERMINED` session over a real determination, with a review log."""
    return Session(
        session_id="packet-under-test",
        created_at="2026-09-01T00:00:00+00:00",
        intake=Intake(
            patient_id=determination.patient_id,
            procedure_code=determination.procedure_code,
            icd10_codes=icd10,
            requesting_provider="Referring Clinic",
            servicing_provider="Bariatric Surgery Service", payer="medicare",
        ),
        state=SessionState.DETERMINED,
        runs=(
            SessionRun(
                ran_at="2026-09-01T00:00:01+00:00",
                as_of=AS_OF,
                policy_version_id=determination.policy_version_id,
                determination=determination,
            ),
        ),
        reviews=tuple(reviews),
    )


def _index(document_ids, policies, patients) -> DocumentIndex:
    """The **two-plane** index a determination's own citations need.

    Written out here rather than imported from `cli.py`, because importing the
    composition root into a test of the pure module would let the two drift
    together — and the ids come from `form.source_ids`, which is the traversal
    under test.

    It is deliberately **not** `cli._packet_index`, which consults three ports: a
    packet that carries an accepted suggestion cites an FDA label too, and this
    private copy agreeing with a wrong `cli.py` is exactly how `T-134` stayed
    invisible here. That claim belongs to the real builder and is checked against
    it in `tests/test_packet_index.py` (D133); every packet assembled in this
    file carries no accepted suggestion, so its citations are the
    determination's alone.
    """
    index = DocumentIndex()
    for document_id in document_ids:
        for store in (patients, policies):
            try:
                index.add(store.get_document(document_id))
            except KeyError:
                continue
            break
    return index


@pytest.fixture(scope="module")
def citing(policies, patients, runner, verifier) -> Determination:
    return _determine(policies, patients, runner, verifier, CITING_PATIENT)


@pytest.fixture(scope="module")
def red_review(policies, patients) -> HistoryReview:
    """The review the committed corpus produces for `RED_PATIENT`.

    Computed through the real reader, not written here: this file's red tests
    are about the form, and a hand-written red review would let them pass on a
    corpus that stopped producing one.
    """
    from pa_agent import history
    from pa_agent.stores.knowledge import LocalKnowledgeStore

    knowledge = LocalKnowledgeStore()
    rows = knowledge.get_medication_effect_rows()
    return history.run_review(
        quote_runner=None,
        patient_id=RED_PATIENT,
        policy_version_id="ncd-100.1-jf-v1",
        rows=rows,
        products={
            row.ingredient.code: knowledge.get_ingredient_products(row.ingredient.code)
            for row in rows
        },
        medications=patients.get_medications(RED_PATIENT),
        conditions=patients.get_conditions(RED_PATIENT),
        observations=patients.get_observations(RED_PATIENT),
        notes=patients.get_notes(RED_PATIENT),
    ).review


def test_the_corpus_still_produces_a_red_suggestion(red_review):
    """The premise every red test below rests on, asserted rather than assumed.

    A13's first clause is *over a non-empty set of red suggestions the committed
    corpus produces* (D131). If the knowledge table or the bundle moves and this
    chart stops producing one, the refusal tests below would pass by having
    nothing to refuse — the vacuous-clause failure D123 found in A11.
    """
    reds = [s for s in red_review.suggestions if s.colour is SuggestionColour.RED]
    assert reds, (
        f"{RED_PATIENT[:8]} produces no red suggestion; the refusal tests in "
        "this file would then be checking an empty set (D123's shape)"
    )
    assert all(not s.citations for s in reds), "red cites nothing by construction"


# --------------------------------------------------------------------------
# A packet, and the denominator its citation clause needs
# --------------------------------------------------------------------------


def test_a_packet_validates_every_citation_and_says_how_many(citing, policies, patients):
    """A13's second clause, with its denominator.

    *Every citation in every packet slices back* is satisfied by a packet with
    no citations, which is the defect D131 rewrote the gate over. So the count
    is asserted non-trivially — this chart's run straddles two notes, so the
    packet cites the bundle and both — and every span is then re-validated here
    against the same ports, independently of `assemble`'s own pass.
    """
    from pa_agent.spans import validate as validate_span

    session = _session(citing)
    ids = form.source_ids(determination=citing)
    packet = form.assemble(
        session=session,
        run_index=0,
        rendered_determination={"outcome": citing.outcome.value},
        review=None,
        payer="Payer <pa@payer.invalid>",
        index=_index(ids, policies, patients),
    )

    manifest = form.citations(packet)
    assert len(manifest) >= 5, (
        f"the packet cites {len(manifest)} span(s); this chart's qualifying run "
        "straddles two notes and its criteria cite several each, so a count this "
        "low means the traversal is not reading the determination (D104)"
    )
    index = _index(ids, policies, patients)
    for span in manifest:
        assert validate_span(span, index), span

    notes = {f"{CITING_PATIENT}/chart_note_{n}.txt" for n in (1, 2)}
    cited = set(packet.cited_documents)
    assert notes <= cited, (
        f"the packet cites {sorted(cited)}; this chart's qualifying run straddles "
        "both notes, so both are named at document level (D104)"
    )
    bundles = cited - notes
    assert len(bundles) == 1 and CITING_PATIENT in next(iter(bundles)), (
        f"the remaining cited document(s) are {sorted(bundles)}; a covered "
        "determination over this chart cites its bundle and nothing else"
    )
    assert packet.cited_documents == tuple(
        dict.fromkeys(span.document_id for span in manifest)
    ), "the index is the manifest's documents, deduplicated in first-cited order"


def test_a_short_circuited_packet_cites_the_policy_corpus_and_no_chart(
    policies, patients, runner, verifier
):
    """`cited_documents` is a citation index, not the records attached (D135).

    The measurement this row turned on. A `NOT_COVERED` determination answers at
    the resolver, so the packet's every citation is a **policy** document and no
    clinical record is named at all — which is why *the field is right and its
    name is wrong* was not available: a list holding the payer's own LCD has
    never been the supporting documents a prior-auth form asks for.

    It is also the case that separates the two readings in the other direction.
    A filter to the patient plane would empty this list, and every note-bearing
    packet in this file would still pass.
    """
    determination = _determine(
        policies, patients, runner, verifier, CITING_PATIENT, code=NON_COVERED_CODE
    )
    assert determination.outcome is DeterminationOutcome.NOT_COVERED, determination.outcome

    ids = form.source_ids(determination=determination)
    packet = form.assemble(
        session=_session(determination),
        run_index=0,
        rendered_determination={"outcome": determination.outcome.value},
        review=None,
        payer="Payer <pa@payer.invalid>",
        index=_index(ids, policies, patients),
    )
    assert packet.cited_documents == ("ncd_100_1", "a53028"), (
        f"the packet names {packet.cited_documents}; the non-covered claim cites "
        "the NCD's sentence with its scoping sentence and the code binding cites "
        "the MAC's article (D22, D28)"
    )
    assert not any(CITING_PATIENT in document for document in packet.cited_documents), (
        "a short-circuited determination reads no chart, so no clinical record "
        "is cited — and the field says documents cited, not records attached"
    )


def test_the_packet_carries_the_form_fields_the_intake_supplied(citing, policies, patients):
    packet = form.assemble(
        session=_session(citing),
        run_index=0,
        rendered_determination={"outcome": citing.outcome.value},
        review=None,
        payer="Payer <pa@payer.invalid>",
        index=_index(form.source_ids(determination=citing), policies, patients),
    )
    assert packet.patient_id == CITING_PATIENT
    assert packet.procedure_code == COVERED_CODE
    assert packet.diagnosis_codes == ("E66.01",)
    assert packet.requesting_provider == "Referring Clinic"
    assert packet.servicing_provider == "Bariatric Surgery Service"
    assert packet.provenance.policy_version_id == citing.policy_version_id
    assert packet.provenance.submitted_at is None, (
        "nothing submits until T-105, so a packet assembled here carries no "
        "submission timestamp and renders no Date: header"
    )


def test_a_citation_that_does_not_slice_is_refused(citing, policies, patients):
    """`UncitedPacket`, carrying the classified reason (Article III).

    Driven by handing `assemble` an index that does not hold the documents the
    determination names — which is exactly what `cli._packet_index` produces for
    an id neither port serves, and the reason that builder leaves the id out
    rather than deciding what its absence means.
    """
    with pytest.raises(form.UncitedPacket) as raised:
        form.assemble(
            session=_session(citing),
            run_index=0,
            rendered_determination={"outcome": citing.outcome.value},
            review=None,
            payer="Payer <pa@payer.invalid>",
            index=DocumentIndex(),
        )
    assert "UNKNOWN_DOCUMENT" in str(raised.value)


def test_a_citation_after_the_first_is_refused_too(citing, policies, patients):
    """The loop validates **every** span, not the span it happens to reach first.

    Measured at this close: truncating `assemble`'s loop to `citations(packet)[:1]`
    survives every other test in this file. The empty-index test above fails on
    the *first* span, so a one-iteration loop raises identically; and the count
    assertion re-validates the manifest in the test's own loop, which a truncated
    production loop does not touch. This index holds the **bundle** — the first
    document cited — and not the notes, so the first span validates and a later
    one cannot.
    """
    ids = form.source_ids(determination=citing)
    first = ids[0]
    assert len(ids) >= 3, "this chart cites the bundle and both notes"
    partial = _index([first], policies, patients)
    with pytest.raises(form.UncitedPacket) as raised:
        form.assemble(
            session=_session(citing),
            run_index=0,
            rendered_determination={"outcome": citing.outcome.value},
            review=None,
            payer="Payer <pa@payer.invalid>",
            index=partial,
        )
    message = str(raised.value)
    assert any(document_id in message for document_id in ids[1:]), (
        f"the refusal does not name any of {list(ids[1:])}, so it fired on the "
        "first citation — and a truncated loop would raise identically, which is "
        "the mutation this test exists for"
    )


def test_a_run_index_the_session_does_not_hold_is_refused(citing, policies, patients):
    with pytest.raises(form.PacketRefused, match="run 4"):
        form.assemble(
            session=_session(citing),
            run_index=4,
            rendered_determination={"outcome": citing.outcome.value},
            review=None,
            payer="Payer <pa@payer.invalid>",
            index=_index(form.source_ids(determination=citing), policies, patients),
        )


def test_a_packet_with_an_empty_determination_is_refused(citing, policies, patients):
    """The clinical-justification box is what the form exists for."""
    with pytest.raises(Exception, match="empty determination"):
        form.assemble(
            session=_session(citing),
            run_index=0,
            rendered_determination={},
            review=None,
            payer="Payer <pa@payer.invalid>",
            index=_index(form.source_ids(determination=citing), policies, patients),
        )


# --------------------------------------------------------------------------
# `source_ids` covers every citation a packet can carry (T-136, D141)
# --------------------------------------------------------------------------


def _span(document_id: str, start: int = 0) -> EvidenceSpan:
    return EvidenceSpan(document_id=document_id, char_start=start, char_end=start + 5)


def _every_colour_review() -> HistoryReview:
    """Green, yellow and red, each citing documents no other span uses.

    Hand-written because the corpus produces one red and no yellow (T-98), so
    a corpus review could not reach the yellow branch at all.
    """
    rxnorm = "http://www.nlm.nih.gov/research/umls/rxnorm"
    drug = CodedConcept(system=rxnorm, code="1", display="a drug")

    def suggestion(row_id, colour, effect_doc, citations=(), **extra):
        return IcdSuggestion(
            row_id=row_id, colour=colour, icd10_code=f"X0{len(row_id)}.0",
            icd10_title="An effect", effect_display="an effect",
            effect=_span(effect_doc), medication=drug, ingredient=drug,
            citations=citations, **extra,
        )

    return HistoryReview(
        patient_id="p1",
        policy_version_id="a-tree-v1",
        suggestions=(
            suggestion(
                "green", SuggestionColour.GREEN, "label-green",
                citations=(_span("chart-observation"),),
                signal=EffectSignal(
                    system="http://loinc.org", code="2160-0", comparator="gt",
                    threshold=1.3, unit="mg/dL", constant_name="a_threshold",
                ),
                observed_value=2.0, observed_on=date(2026, 1, 1),
            ),
            suggestion(
                "yellow", SuggestionColour.YELLOW, "label-yellow",
                citations=(_span("chart-note"),),
            ),
            suggestion("red", SuggestionColour.RED, "label-red"),
        ),
    )


def _determination_citing(document_id: str) -> Determination:
    return Determination(
        patient_id="p1",
        procedure_code="43775",
        policy_version_id="a-tree-v1",
        payer="medicare",
        outcome=DeterminationOutcome.NOT_COVERED,
        exclusion_evidence=(_span(document_id),),
    )


def _acceptances(review: HistoryReview, rows) -> tuple[ReviewEntry, ...]:
    entries = []
    for suggestion in review.suggestions:
        if suggestion.row_id not in rows:
            continue
        entries.append(_entry(ReviewAction.ACCEPT_SUGGESTION,
                              row_id=suggestion.row_id, code=suggestion.icd10_code))
        if suggestion.colour is SuggestionColour.RED:
            entries.append(_entry(ReviewAction.JUSTIFY_SUGGESTION,
                                  row_id=suggestion.row_id, code=suggestion.icd10_code,
                                  justification="Reviewed; the analyte was never drawn."))
    return tuple(entries)


def _index_from(document_ids) -> DocumentIndex:
    index = DocumentIndex()
    for document_id in document_ids:
        text = "x" * 40
        index.add(Document(document_id=document_id, text=text,
                           sha256=hashlib.sha256(text.encode()).hexdigest()))
    return index


def _assemble_over(determination, review, rows):
    session = _session(determination, reviews=_acceptances(review, rows))
    return form.assemble(
        session=session, run_index=0,
        rendered_determination={"outcome": determination.outcome.value},
        review=review, payer="a payer",
        index=_index_from(form.source_ids(determination=determination, review=review)),
    )


@pytest.mark.parametrize(
    "rows",
    [frozenset(c) for n in range(4) for c in itertools.combinations(("green", "yellow", "red"), n)],
    ids=lambda rows: "+".join(sorted(rows)) or "none",
)
def test_an_index_filled_from_source_ids_validates_every_packet_the_review_allows(rows):
    """`source_ids` is the only input to the index, so it has to name every
    document any accepted subset can cite (D141)."""
    determination = _determination_citing("chart-bundle")
    review = _every_colour_review()
    packet = _assemble_over(determination, review, rows)
    sources = set(form.source_ids(determination=determination, review=review))
    assert set(packet.cited_documents) <= sources
    assert {s.row_id for s in packet.suggestions} == rows


def test_the_fully_accepted_packet_cites_every_document_kind():
    """Non-vacuity: the property above is checked over a packet citing all six."""
    determination = _determination_citing("chart-bundle")
    packet = _assemble_over(determination, _every_colour_review(), {"green", "yellow", "red"})
    assert set(packet.cited_documents) == {
        "chart-bundle", "label-green", "chart-observation",
        "label-yellow", "chart-note", "label-red",
    }


def test_a_source_traversal_that_drops_effects_refuses_the_packet(monkeypatch):
    """The property follows its input: `T-134`'s defect, put back one layer
    down, is a refused packet and not a quiet one."""
    real = form._review_spans

    def without_effects(review):
        effects = {s.effect for s in review.suggestions} if review else set()
        return tuple(span for span in real(review) if span not in effects)

    monkeypatch.setattr(form, "_review_spans", without_effects)
    with pytest.raises(form.UncitedPacket):
        _assemble_over(_determination_citing("chart-bundle"),
                       _every_colour_review(), {"red"})


# --------------------------------------------------------------------------
# A suggestion enters only through a recorded acceptance (REQ-65, REQ-74)
# --------------------------------------------------------------------------


def _entry(action, *, row_id=None, code=None, justification=None, note=None,
           run_index=0, at="2026-09-02T09:00:00+00:00", reviewer="sam"):
    return ReviewEntry(
        at=at, reviewer=reviewer, action=action, run_index=run_index,
        row_id=row_id, icd10_code=code, justification=justification, note=note,
    )


def _red(review):
    return next(s for s in review.suggestions if s.colour is SuggestionColour.RED)


def test_no_entries_means_no_suggestion_in_the_packet(red_review):
    """REQ-65: no form without a human action. The default is empty, not *all*."""
    assert form.accepted(red_review, (), 0) == ()


def test_a_red_accepted_without_a_justification_is_refused_naming_the_code(red_review):
    red = _red(red_review)
    entries = (_entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),)
    with pytest.raises(form.UnjustifiedRedSuggestion) as raised:
        form.accepted(red_review, entries, 0)
    assert red.icd10_code in str(raised.value), (
        "the refusal must name the code; US-13's bullet says so and §11 did not "
        "(D131)"
    )


def test_two_unjustified_reds_are_both_named(red_review):
    """Hand-built, and that is the point.

    The committed corpus produces exactly **one** red per chart, and with one
    code `', '.join(codes)` and `codes[0]` render the same string — so a fixture
    with one red cannot tell the two apart. Measured at this close: that
    mutation survives every corpus-driven test in this file.
    """
    red = _red(red_review)
    second = IcdSuggestion(
        row_id="second-row",
        colour=SuggestionColour.RED,
        icd10_code="Z99.89",
        icd10_title="A second effect, written here",
        effect_display=red.effect_display,
        effect=red.effect,
        medication=red.medication,
        ingredient=red.ingredient,
    )
    review = HistoryReview(
        patient_id=red_review.patient_id,
        policy_version_id=red_review.policy_version_id,
        suggestions=(red, second),
    )
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id="second-row", code="Z99.89"),
    )
    with pytest.raises(form.UnjustifiedRedSuggestion) as raised:
        form.accepted(review, entries, 0)
    message = str(raised.value)
    assert red.icd10_code in message and "Z99.89" in message, (
        f"the refusal named only part of {message!r}; a refusal naming one code "
        "makes a reviewer fix one and re-run (D131)"
    )


def test_a_justified_red_enters_the_packet_with_its_justification(red_review):
    red = _red(red_review)
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id=red.row_id, code=red.icd10_code,
               justification="Reviewed the chart; the drug is active and the "
                             "analyte was never drawn."),
    )
    accepted = form.accepted(red_review, entries, 0)
    assert [s.icd10_code for s in accepted] == [red.icd10_code]
    assert accepted[0].justification.startswith("Reviewed the chart")
    assert accepted[0].accepted_by == "sam"
    assert accepted[0].citations == (), "red cites nothing, justified or not"


def test_a_rejection_after_an_acceptance_keeps_the_code_out(red_review):
    red = _red(red_review)
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id=red.row_id, code=red.icd10_code,
               justification="written, then withdrawn"),
        _entry(ReviewAction.REJECT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),
    )
    assert form.accepted(red_review, entries, 0) == ()


def test_a_justification_written_after_the_acceptance_still_counts(red_review):
    """Acceptance and justification are two questions (D131).

    A single latest-entry rule would read the trailing `JUSTIFY` as the row's
    state and conclude it was never accepted — which is the natural reading of
    *the latest entry per row* and the wrong one.
    """
    red = _red(red_review)
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code),
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id=red.row_id, code=red.icd10_code,
               justification="written second, and it is what lets the code in"),
    )
    assert len(form.accepted(red_review, entries, 0)) == 1


def test_an_entry_against_another_snapshot_does_not_reach_this_packet(red_review):
    """A review of snapshot 0 does not justify a code in snapshot 1's packet."""
    red = _red(red_review)
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code=red.icd10_code,
               run_index=0),
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id=red.row_id, code=red.icd10_code,
               justification="against the first snapshot only", run_index=0),
    )
    assert len(form.accepted(red_review, entries, 0)) == 1
    assert form.accepted(red_review, entries, 1) == ()


def test_the_packet_carries_the_rows_code_and_never_the_entrys(red_review):
    """The entry decides *that* a code is in; the row decides *which* code it is.

    D131's rule, and the reason it is a rule: an entry carrying its own code
    could put a code in a packet that traces to nothing, which CLAUDE.md names as
    the one thing a suggestion must never be. The entry here names a plausible
    wrong code, and the packet still carries the row's.
    """
    red = _red(red_review)
    entries = (
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id=red.row_id, code="R99"),
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id=red.row_id, code="R99",
               justification="the code on the entry is not the code in the packet"),
    )
    assert red.icd10_code != "R99", "the fixture's point is that the two differ"
    accepted = form.accepted(red_review, entries, 0)
    assert [s.icd10_code for s in accepted] == [red.icd10_code]
    assert [s.icd10_title for s in accepted] == [red.icd10_title]


def test_an_accept_naming_a_row_the_review_does_not_hold_is_refused(red_review):
    entries = (_entry(ReviewAction.ACCEPT_SUGGESTION, row_id="typo-row", code="R73.9"),)
    with pytest.raises(form.SuggestionNotInReview, match="typo-row"):
        form.accepted(red_review, entries, 0)


def test_a_note_entry_changes_no_packet_field(citing, policies, patients, red_review):
    """A note is prose about the chart. It touches no code and no field."""
    base = _session(citing)
    noted = _session(
        citing,
        reviews=(_entry(ReviewAction.NOTE, note="Called the surgeon's office."),),
    )
    kwargs = dict(
        run_index=0,
        rendered_determination={"outcome": citing.outcome.value},
        review=red_review,
        payer="Payer <pa@payer.invalid>",
        index=_index(form.source_ids(determination=citing), policies, patients),
    )
    assert (
        form.assemble(session=base, **kwargs).model_dump()
        == form.assemble(session=noted, **kwargs).model_dump()
    )


def test_a_note_entry_naming_a_row_cannot_be_constructed():
    """Both directions, `WithheldCandidate`'s idiom (D131)."""
    with pytest.raises(Exception, match="names a suggestion row"):
        _entry(ReviewAction.NOTE, row_id="r", code="R73.9", note="x")
    with pytest.raises(Exception, match="carries no note"):
        _entry(ReviewAction.NOTE)


def test_a_row_action_without_its_code_cannot_be_constructed():
    with pytest.raises(Exception, match="names no row_id and icd10_code"):
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id="r")


def test_a_whitespace_only_justification_cannot_be_constructed():
    """`Intake`'s blank-code rule, one field along (D128, D131).

    This is where blankness is decided, and the only place: `form.accepted`
    reads `is None`, because a check no input can reach is not a check. The
    mutation `not self.justification.strip()` -> `self.justification is None`
    is caught here and nowhere else.
    """
    with pytest.raises(Exception, match="no written"):
        _entry(ReviewAction.JUSTIFY_SUGGESTION, row_id="r", code="R73.9",
               justification="   ")


def test_an_acceptance_cannot_carry_its_own_justification():
    with pytest.raises(Exception, match="carries a justification"):
        _entry(ReviewAction.ACCEPT_SUGGESTION, row_id="r", code="R73.9",
               justification="one field, one reader")


def test_a_red_packet_suggestion_cannot_be_built_without_a_justification(red_review):
    """The contract-level backstop, which is what makes the clause structural.

    `form.accepted` raises first, and it has to, because that is what names
    every code. This refusal is what stops a *second* assembly path — v2.2's UI,
    a later verb — from skipping the rule.
    """
    from pa_agent.contracts import PacketSuggestion

    red = _red(red_review)
    common = dict(
        row_id=red.row_id, colour=SuggestionColour.RED,
        icd10_code=red.icd10_code, icd10_title=red.icd10_title,
        effect_display=red.effect_display, effect=red.effect,
        accepted_by="sam", accepted_at="2026-09-02T09:00:00+00:00",
    )
    with pytest.raises(Exception, match="no written justification"):
        PacketSuggestion(**common)
    # And the blank half, which `accepted()` cannot reach — `ReviewEntry` refuses
    # a whitespace-only justification, so nothing the form builds gets here with
    # one. It is checked because the backstop exists for a *second* assembly
    # path (v2.2's UI, a later verb), and a guard reading `is None` would let
    # that path through. Measured: this is the one test that fails when the
    # guard is weakened.
    with pytest.raises(Exception, match="no written justification"):
        PacketSuggestion(**common, justification="   ")


# --------------------------------------------------------------------------
# Rendering: deterministic, and the determination verbatim
# --------------------------------------------------------------------------


def _hand_packet(**overrides) -> Packet:
    """A packet over a hand-written document, for the rendering rules.

    Written here rather than taken from the corpus because the rendering checks
    are about the bytes, and a corpus determination's JSON body is 8 kB of
    incidental detail around the three lines under test.
    """
    document = Document.from_text("hand/note.txt", "The chart says this much.\n")
    span = EvidenceSpan(document_id=document.document_id, char_start=4, char_end=9,
                        quote="chart")
    determination = Determination(
        patient_id="p1", procedure_code="43775",
        policy_version_id="ncd-100.1-jf-v1",
        payer="medicare",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
    )
    session = Session(
        session_id="hand-session", created_at="2026-09-01T00:00:00+00:00",
        intake=Intake(patient_id="p1", procedure_code="43775", payer="medicare"),
        state=SessionState.DETERMINED,
        runs=(SessionRun(ran_at="r", as_of=AS_OF,
                         policy_version_id="ncd-100.1-jf-v1",
                         determination=determination),),
    )
    index = DocumentIndex()
    index.add(document)
    rendered = {"outcome": "INSUFFICIENT_EVIDENCE", "gap_list": [{"criterion_id": "c3"}]}
    kwargs = dict(
        session=session, run_index=0, rendered_determination=rendered,
        review=None, payer="Payer <pa@payer.invalid>", index=index,
    )
    kwargs.update(overrides)
    return form.assemble(**kwargs), rendered, span


def test_rendering_is_deterministic():
    """Two renders of one packet are the same bytes — `T-106`'s premise.

    `EmailMessage.as_string()` fails this: it stamps a clock-derived `Date:` and
    a randomised `Message-ID`, which is why the `.eml` is hand-rolled (D131).
    """
    packet, _, _ = _hand_packet()
    assert form.render(packet) == form.render(packet)


def test_the_message_id_is_derived_from_the_session_and_the_run():
    packet, _, _ = _hand_packet()
    other = packet.model_copy(
        update={"provenance": packet.provenance.model_copy(update={"run_index": 0})}
    )
    assert form.message_id(packet) == form.message_id(other)
    assert form.MESSAGE_ID_DOMAIN in form.render(packet)


def test_no_date_header_until_something_submits():
    packet, _, _ = _hand_packet()
    assert "\nDate:" not in form.render(packet)
    submitted, _, _ = _hand_packet(submitted_at="Wed, 02 Sep 2026 09:00:00 +0000")
    assert "Date: Wed, 02 Sep 2026 09:00:00 +0000" in form.render(submitted)


def test_the_rendered_determination_appears_verbatim():
    """`form.py` composes the composition root's output and never re-renders it.

    Asserted on the bytes as well as by the parse check below: the body must
    contain exactly what `cli._render` produced, so a second renderer here would
    have to produce identical JSON to pass — which is the claim, not a proxy for
    it (D129, D131).
    """
    packet, rendered, _ = _hand_packet()
    assert json.dumps(rendered, indent=2, ensure_ascii=False) in form.render(packet)


def test_the_headers_come_before_a_blank_line_and_the_body_after():
    packet, _, _ = _hand_packet()
    headers, blank, body = form.render(packet).partition("\n\n")
    assert blank == "\n\n"
    assert headers.startswith("To: Payer <pa@payer.invalid>")
    assert "From:" not in headers, (
        "no committed artifact names the submitting practice, so the header is "
        "absent rather than fictional (D131)"
    )
    assert "PRIOR AUTHORIZATION REQUEST" in body


# --------------------------------------------------------------------------
# Parse-level: what no behavioural test on this corpus can separate (D65)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def form_source() -> str:
    return FORM_SOURCE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def form_tree(form_source) -> ast.Module:
    return ast.parse(form_source)


def test_form_declares_no_determination_renderer(form_tree):
    """A second renderer answers identically until v2.2 compares the surfaces.

    `cli._render` is the one answer to *what does a determination look like as a
    document* (D129), and a private `_render` here would be a second one that no
    test in this repo could distinguish from the first for as long as the two
    agreed.
    """
    names = {
        node.name
        for node in ast.walk(form_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_render" not in names, (
        "pa_agent/form.py declares _render; the packet composes cli._render's "
        "output and never produces its own (D129, D131)"
    )


def test_form_imports_nothing_from_the_composition_root(form_tree):
    """And it cannot: `BOTH_PLANES` is an exact set and `form.py` would join it.

    `tests/test_planes.py` would catch this too, as a plane violation. It is
    asserted here because the *reason* is local — the packet takes the rendered
    determination as an argument — and a reader of this file should not have to
    find it in a whitelist.
    """
    for node in ast.walk(form_tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "pa_agent.cli", "form.py imports the CLI"
        if isinstance(node, ast.Import):
            assert all(a.name != "pa_agent.cli" for a in node.names)


def test_form_names_no_clock(form_tree, form_source):
    """A clock-derived value renders identically *once*.

    `T-106` commits a fixture and compares bytes, so a `datetime.now()` in the
    `Date:` header passes every test written today and fails in a month. Parsed
    rather than behavioural for exactly that reason.
    """
    for node in ast.walk(form_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {"datetime", "time", "calendar"}, (
                    f"form.py imports {alias.name}; it takes every unclockable "
                    "value as an argument (D127's rule, D131)"
                )
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {"datetime", "time", "calendar"}, (
                f"form.py imports from {node.module}"
            )
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"now", "today", "utcnow"}, (
                f"form.py calls .{node.attr}(); the clock is the composition "
                "root's (D127)"
            )


def test_form_names_no_storage_location(form_source):
    """D25's scan, on the module the plane walk will also read.

    Imported from `tests/test_planes.py` rather than re-implemented, because a
    literal string scan here would pass on a module reaching storage by any
    other spelling — the mutation `T-101`'s close measured (D128).
    """
    import test_planes

    hits = test_planes._storage_names(FORM_SOURCE)
    assert not hits, f"form.py names {sorted(hits)}; it opens nothing (REQ-41)"


def test_the_refusal_joins_every_code_rather_than_indexing_one(form_source):
    """With one red in a fixture, `codes[0]` and `', '.join(codes)` agree.

    The two-red test above is what catches the mutation behaviourally; this is
    the cheaper guard that says *the refusal is built by joining*, so a later
    edit back to an index is a red suite without anyone rebuilding a two-red
    fixture.
    """
    body = form_source[form_source.index("def accepted("):]
    body = body[: body.index("\ndef ", 1)]
    assert re.search(r"join\(\s*unjustified\s*\)", body), (
        "the unjustified-red refusal no longer joins the whole list; a refusal "
        "naming one code makes a reviewer fix one and re-run (D131)"
    )
    assert "unjustified[0]" not in body


def test_the_module_takes_the_rendered_determination_as_a_parameter(form_tree):
    """The shape that makes *no second renderer* structural rather than checked."""
    assemble = next(
        node for node in form_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "assemble"
    )
    names = [a.arg for a in assemble.args.kwonlyargs]
    assert "rendered_determination" in names
    assert assemble.args.args == [], (
        "assemble takes keyword arguments only, so a caller cannot supply the "
        "session and the index in the wrong order"
    )
