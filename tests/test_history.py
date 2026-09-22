"""T-97 — the medical-history review: candidates, the tri-state, `would_affect`.

Covers **REQ-63** (a suggested code comes only from a row of the table, reached
through a pinned expansion), **REQ-64** (Python assigns the colour, and "nobody
looked" is not a colour), **REQ-65** (a suggestion is never a code assignment,
and a withheld candidate is recorded rather than dropped) and **REQ-66**
(`would_affect` is set membership that changes nothing).

Most of this is hand-built contracts rather than the corpus, because the corpus
cannot separate the mutations that matter: it holds one creatinine on the chart
that matters, no chart with a completed table drug and no active one, and no
resolved already-coded condition anywhere. Where the corpus *can* answer — does
the pinned expansion actually reach the codes Synthea writes — it is asked
directly, because that is the failure the expansion exists to fix and it is a
fact about the committed bundles rather than about a fixture.

**The quote port is `pa_agent.quotes` (T-98, D122)** and `run_review` is what
consults it. `review` still takes the anchored quotes as a mapping, so the
tri-state is pinned here by passing one; the REQ-67 tests below drive
`run_review` with a stub runner whose payloads go through the **real**
anchorer, because the statement they mint — a refused quote is red, never
yellow — is the anchorer's refusal and not a fixture's. The stubs live here and
never in the package (D78's rule).
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent import history
from pa_agent.contracts import (
    CallMetrics,
    CodedConcept,
    CodedValueSet,
    Condition,
    Criterion,
    Document,
    EffectSignal,
    EvidenceSpan,
    IcdSuggestion,
    Medication,
    MedicationEffectRow,
    Observation,
    PolicyConstant,
    SuggestionColour,
    WithholdReason,
)
from pa_agent.contracts import RunTrace
from pa_agent.quotes import NullQuoteRunner, RecordedQuoteRunner, build_quote_result
from pa_agent.spans import validate as validate_span
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.verifier import RecordedVerifierRunner, VerifierAnswer

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLES = REPO_ROOT / "data" / "patients" / "bundles"
PACKAGE = REPO_ROOT / "pa_agent"

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"

#: The chart H1 runs on, and the tree that governs 93975 in its state.
H1_PATIENT = "455d3f7d-3b99-dad6-c0b2-d5405144e793"
ULTRASOUND_TREE = "us-abdominal-visceral-j5-j8-v1"


# --------------------------------------------------------------------------
# Builders — one row, one drug, one signal, and nothing incidental
# --------------------------------------------------------------------------


def a_row(
    *,
    row_id: str = "drug-effect",
    ingredient: str = "1",
    comparator: str = "gt",
    threshold: float = 1.3,
    signal_code: str = "2160-0",
    already_coded: tuple[str, ...] = ("111",),
    unsourced_reason: str | None = None,
) -> MedicationEffectRow:
    return MedicationEffectRow(
        row_id=row_id,
        ingredient=CodedConcept(system=RXNORM, code=ingredient, display="drug"),
        ingredient_source={"origin": "a test"},
        effect_display="an effect",
        effect=EvidenceSpan(
            document_id="spl_drug", char_start=0, char_end=5, quote="an ef"
        ),
        icd10_code="X00.0",
        icd10_title="An effect, unspecified",
        icd10_source={"origin": "a test"},
        already_coded=tuple(
            CodedConcept(system=SNOMED, code=code) for code in already_coded
        ),
        unsourced_reason=unsourced_reason,
        signal=EffectSignal(
            system=LOINC,
            code=signal_code,
            comparator=comparator,
            threshold=threshold,
            unit="mg/dL",
            constant_name="a_threshold",
        ),
    )


def products_for(row: MedicationEffectRow, *codes: str) -> dict[str, CodedValueSet]:
    return {
        row.ingredient.code: CodedValueSet(
            value_set_id=f"rxnorm_products_{row.ingredient.code}",
            system=RXNORM,
            codes=frozenset({row.ingredient.code, *codes}),
        )
    }


def a_medication(code: str = "999", status: str = "active", system: str = RXNORM):
    return Medication(code=code, system=system, status=status, display="a product")


def an_observation(value: float, code: str = "2160-0", when: str = "2026-01-01", span=True):
    return Observation(
        code=code,
        value=value,
        unit="mg/dL",
        effective_date=date.fromisoformat(when),
        span=(
            EvidenceSpan(document_id="bundle.json", char_start=1, char_end=9)
            if span
            else None
        ),
    )


def a_note(document_id: str = "chart_note_1.txt") -> Document:
    text = "the note"
    import hashlib

    return Document(
        document_id=document_id,
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


def run(row: MedicationEffectRow, **kwargs):
    defaults = dict(
        patient_id="p1",
        policy_version_id="a-tree-v1",
        rows=[row],
        products=products_for(row, "999"),
        medications=[a_medication()],
        conditions=[],
        observations=[],
    )
    defaults.update(kwargs)
    return history.review(**defaults)


# --------------------------------------------------------------------------
# REQ-63 — the code comes from a row, and the expansion is how a row matches
# --------------------------------------------------------------------------


def test_every_suggestion_carries_its_rows_code_and_the_span_it_was_asserted_from():
    """A suggestion is traceable to one row or it is not a suggestion."""
    row = a_row()
    review = run(row, observations=[an_observation(2.0)])
    assert len(review.suggestions) == 1
    suggestion = review.suggestions[0]
    assert suggestion.row_id == row.row_id
    assert suggestion.icd10_code == row.icd10_code
    assert suggestion.effect == row.effect
    assert suggestion.ingredient == row.ingredient


def test_the_pinned_expansion_reaches_every_code_the_corpus_prescribes():
    """The failure this file exists to fix, measured on the committed bundles.

    A row declares an ingredient; Synthea writes clinical drugs. Comparing the
    two directly matches **nothing** — so a matcher without the expansion loads
    cleanly, compares cleanly and suggests nothing, on every chart, forever
    (D52's shape, D119's measurement).
    """
    store = LocalKnowledgeStore()
    rows = store.get_medication_effect_rows()
    prescribed: set[str] = set()
    ingredient_hits = 0
    ingredients = {row.ingredient.code for row in rows}
    for path in BUNDLES.glob("*.json"):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        for entry in bundle["entry"]:
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "MedicationRequest":
                continue
            for coding in resource.get("medicationCodeableConcept", {}).get(
                "coding", []
            ):
                code = coding.get("code")
                if resource.get("status") == "active":
                    prescribed.add(code)
                if code in ingredients:
                    ingredient_hits += 1

    assert ingredient_hits == 0, (
        "a bundle codes a MedicationRequest with a bare ingredient rxcui; the "
        "premise of the pinned expansion is that none does"
    )
    expansions = [store.get_ingredient_products(code) for code in ingredients]
    matched = {
        code
        for code in prescribed
        if any(vs.admits(code, RXNORM) for vs in expansions)
    }
    assert {"310798", "314076", "105585"} <= matched, (
        "the expansion no longer admits the clinical-drug codes this corpus "
        f"prescribes; matched {sorted(matched)}"
    )


def test_a_drug_in_another_vocabulary_is_not_a_candidate():
    """REQ-59's comparison, on the knowledge plane's sets.

    Short numeric codes collide across vocabularies, so the system is part of
    the comparison. A medication declaring SNOMED is not an RxNorm member even
    when the digits match.
    """
    row = a_row()
    review = run(row, medications=[a_medication(code="999", system=SNOMED)])
    assert review.suggestions == () and review.withheld == ()


def test_a_completed_prescription_is_not_a_candidate():
    """No chart in the corpus can catch this: every chart with a completed table
    drug also carries an active one."""
    row = a_row()
    assert run(row, medications=[a_medication(status="completed")]).suggestions == ()


# --------------------------------------------------------------------------
# REQ-64 — Python assigns the colour
# --------------------------------------------------------------------------


def test_a_crossed_threshold_is_green_and_cites_the_measurement():
    row = a_row()
    review = run(row, observations=[an_observation(2.1)])
    suggestion = review.suggestions[0]
    assert suggestion.colour is SuggestionColour.GREEN
    assert suggestion.observed_value == 2.1
    assert suggestion.signal == row.signal
    assert len(suggestion.citations) == 1


@pytest.mark.parametrize(
    "comparator, threshold, value, crosses",
    [
        ("gt", 1.3, 1.3, False),
        ("gt", 1.3, 1.31, True),
        ("gte", 126.0, 126.0, True),
        ("lt", 1.5, 1.5, False),
        ("lt", 1.5, 1.49, True),
        ("lte", -2.5, -2.5, True),
    ],
)
def test_the_boundary_is_the_comparator_the_row_declares(
    comparator, threshold, value, crosses
):
    """Each comparator's own boundary. A flipped comparator fails here, which no
    behavioural test on the corpus can see: every committed reading is far from
    its bound."""
    row = a_row(comparator=comparator, threshold=threshold)
    review = run(row, observations=[an_observation(value)])
    if crosses:
        assert review.suggestions[0].colour is SuggestionColour.GREEN
    else:
        assert review.suggestions == ()
        assert review.withheld[0].reason is WithholdReason.SIGNAL_NOT_CROSSED


def test_a_measured_signal_that_does_not_cross_is_withheld_and_never_red():
    """Red means *nothing on the chart*. A normal result is the chart answering,
    and suggesting the condition over it is a claim the record refutes (D119)."""
    row = a_row()
    review = run(row, observations=[an_observation(0.9)])
    assert review.suggestions == ()
    withheld = review.withheld[0]
    assert withheld.reason is WithholdReason.SIGNAL_NOT_CROSSED
    assert withheld.observed_value == 0.9
    assert withheld.signal == row.signal


def test_the_most_recent_reading_decides_and_never_store_order():
    """Two readings straddling the bound, the crossing one older.

    REQ-34a's lesson on a second fact type: the chart H1 runs on carries one
    creatinine, so store order and recency agree there and the corpus cannot
    tell a positional selector from a dated one.
    """
    row = a_row()
    readings = [
        an_observation(2.5, when="2024-01-01"),
        an_observation(0.8, when="2026-01-01"),
    ]
    for ordered in (readings, list(reversed(readings))):
        review = run(row, observations=ordered)
        assert review.suggestions == ()
        assert review.withheld[0].observed_value == 0.8


def test_nothing_on_the_chart_is_red_on_a_chart_with_no_notes():
    row = a_row()
    review = run(row)
    suggestion = review.suggestions[0]
    assert suggestion.colour is SuggestionColour.RED
    assert suggestion.citations == ()
    assert suggestion.signal is None


def test_an_anchored_quote_is_yellow_and_carries_it():
    row = a_row()
    quote = EvidenceSpan(
        document_id="chart_note_1.txt", char_start=0, char_end=8, quote="the note"
    )
    review = run(row, notes=[a_note()], quotes={row.row_id: (quote,)})
    suggestion = review.suggestions[0]
    assert suggestion.colour is SuggestionColour.YELLOW
    assert suggestion.citations == (quote,)
    assert suggestion.signal is None


def test_a_quote_source_consulted_and_empty_is_red():
    """"Looked and found nothing" is red; "did not look" is the raise below."""
    row = a_row()
    review = run(row, notes=[a_note()], quotes={})
    assert review.suggestions[0].colour is SuggestionColour.RED


def test_no_quote_source_on_a_chart_with_notes_raises_and_names_the_runner():
    """D90, on a second wire. Answering red here would report *the chart does not
    say* for a chart nobody read. The message names the way to consult one."""
    row = a_row()
    with pytest.raises(history.QuoteSourceNotConsulted) as caught:
        run(row, notes=[a_note()], quotes=None)
    assert "run_review" in str(caught.value)


def test_a_crossing_reading_with_no_span_raises_rather_than_reporting_red():
    """REQ-5's shape: a colour above red carries its evidence. Demoting an
    uncitable crossing to red would state the opposite of what the chart says."""
    row = a_row()
    with pytest.raises(history.UnciteableSignal):
        run(row, observations=[an_observation(2.0, span=False)])


def test_the_model_is_never_asked_for_a_colour_or_a_code():
    """Structural, not behavioural: no colour and no ICD-10 code may be read out
    of model output, so nothing under `pa_agent/` may name one outside this
    module and the contracts that define them.

    An AST scan rather than a test of behaviour, because a module that took a
    colour from a model would answer identically on every input this corpus can
    produce (D65's method).
    """
    allowed = {"pa_agent/history.py", "pa_agent/contracts.py"}
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = str(path.relative_to(REPO_ROOT))
        if relative in allowed:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in {"green", "yellow", "red"}:
                    offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        f"{offenders} name a suggestion colour outside the review and its "
        "contracts; Python assigns the colour (REQ-64)"
    )


# --------------------------------------------------------------------------
# REQ-65 — a suggestion is not a code assignment, and a withhold is recorded
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# REQ-67 — a refused quote is red, never yellow; a rejected yellow is red and
# says so. REQ-68 — every turn of every note is counted. Through `run_review`
# with a stub runner whose payloads go through the real anchorer (T-98, D122).
# --------------------------------------------------------------------------


HAND_NOTE = (
    "ASSESSMENT\n"
    "Chronic kidney disease stage 2 noted on labs.\n"
    "Blood pressure 118/76, unremarkable.\n"
)
HAND_VERBATIM = "Chronic kidney disease stage 2 noted on labs."
HAND_PARAPHRASE = "Chronic kidney disease stage two noted on labs."


def a_hand_note(document_id: str = "p1/chart_note_1.txt", text: str = HAND_NOTE) -> Document:
    return Document.from_text(document_id, text)


class _StubQuoteRunner:
    """Answers each note with a prepared payload, through `build_quote_result`
    and therefore through the real anchorer; records what it was asked."""

    name = "stub"

    def __init__(self, payload_by_document: dict[str, dict]) -> None:
        self._payloads = payload_by_document
        self.asked: list[tuple[str, tuple[str, ...]]] = []

    def run(self, document_id, text, rows):
        self.asked.append((document_id, tuple(r.row_id for r in rows)))
        payload = self._payloads.get(document_id, {"effects": []})
        metric = CallMetrics(
            model="fake-under-test", purpose="quotes", input_tokens=5,
            output_tokens=2, wall_time_ms=1.0,
        )
        result = build_quote_result(document_id, text, payload, metric, rows=rows)
        result.trace = RunTrace(
            runner_name=self.name, model="fake-under-test", prompt_version="stub",
            document_id=document_id, steps=["quotes"], metrics=[metric],
        )
        return result


class _Verdict:
    """A verifier fake with one fixed answer. Lives here, never in the package."""

    name = "verdict"

    def __init__(self, accept: bool, reason: str = "test double") -> None:
        self._accept, self._reason = accept, reason
        self.payloads: list[dict] = []

    def run(self, payload: dict) -> VerifierAnswer:
        self.payloads.append(payload)
        return VerifierAnswer(
            accept=self._accept, reason=self._reason,
            metrics=CallMetrics(
                model="fake-under-test", purpose="verification", input_tokens=3,
                output_tokens=1, wall_time_ms=1.0,
            ),
        )


def _quote_payload(display: str, quote: str) -> dict:
    return {"effects": [{"effect": display, "quotes": [
        {"quote": quote, "char_start": 0, "char_end": 1}]}]}


def run_with_runner(row: MedicationEffectRow, runner, **kwargs) -> history.HistoryRun:
    defaults = dict(
        quote_runner=runner,
        patient_id="p1",
        policy_version_id="a-tree-v1",
        rows=[row],
        products=products_for(row, "999"),
        medications=[a_medication()],
        conditions=[],
        observations=[],
        notes=[a_hand_note()],
    )
    defaults.update(kwargs)
    return history.run_review(**defaults)


def test_a_paraphrased_quote_is_refused_by_the_anchorer_and_the_candidate_is_red():
    """REQ-67, through the real `anchor.py`: one word changed and the quote does
    not occur in the note, so it is dropped and the candidate is red citing
    nothing — never yellow on a passage nobody can locate (D18)."""
    row = a_row()
    runner = _StubQuoteRunner({"p1/chart_note_1.txt": _quote_payload("an effect", HAND_PARAPHRASE)})
    run_ = run_with_runner(row, runner)
    (suggestion,) = run_.review.suggestions
    assert suggestion.colour is SuggestionColour.RED
    assert suggestion.citations == ()
    assert suggestion.verifier_rejected is False
    assert run_.notes_consulted == 1 and run_.rejections == ()


def test_a_verbatim_quote_is_yellow_and_the_validator_accepts_its_span():
    row = a_row()
    runner = _StubQuoteRunner({"p1/chart_note_1.txt": _quote_payload("an effect", HAND_VERBATIM)})
    run_ = run_with_runner(row, runner)
    (suggestion,) = run_.review.suggestions
    assert suggestion.colour is SuggestionColour.YELLOW
    (span,) = suggestion.citations
    from pa_agent.index import DocumentIndex

    index = DocumentIndex()
    index.add(a_hand_note())
    assert validate_span(span, index) == HAND_VERBATIM
    assert suggestion.signal is None


def test_a_yellow_the_verifier_rejects_is_red_and_says_so():
    """REQ-67's second clause and Article V on the review: the claim the
    verifier sees is the candidate and its passages, sliced from the note;
    a rejection demotes to red with `verifier_rejected` set, no retry."""
    row = a_row()
    runner = _StubQuoteRunner({"p1/chart_note_1.txt": _quote_payload("an effect", HAND_VERBATIM)})
    verifier = _Verdict(accept=False, reason="the passage is about something else")
    run_ = run_with_runner(row, runner, verifier=verifier)
    (suggestion,) = run_.review.suggestions
    assert suggestion.colour is SuggestionColour.RED
    assert suggestion.citations == ()
    assert suggestion.verifier_rejected is True
    assert run_.rejections == ((row.row_id, "the passage is about something else"),)
    (payload,) = verifier.payloads
    assert sorted(payload) == ["candidate", "quotes"]
    assert payload["quotes"] == [HAND_VERBATIM]
    assert payload["candidate"] == {"effect": "an effect", "icd10_title": row.icd10_title}
    assert run_.model_calls == 2, "one quote turn and one verification, both counted"


def test_a_yellow_the_verifier_accepts_stays_yellow_and_the_call_is_counted():
    row = a_row()
    runner = _StubQuoteRunner({"p1/chart_note_1.txt": _quote_payload("an effect", HAND_VERBATIM)})
    verifier = _Verdict(accept=True)
    run_ = run_with_runner(row, runner, verifier=verifier)
    (suggestion,) = run_.review.suggestions
    assert suggestion.colour is SuggestionColour.YELLOW
    assert suggestion.verifier_rejected is False
    assert len(run_.verifications) == 1 and run_.model_calls == 2
    assert run_.total_input_tokens == 5 + 3


def test_a_red_with_no_quote_never_reaches_the_verifier():
    row = a_row()
    runner = _StubQuoteRunner({})
    verifier = _Verdict(accept=False)
    run_ = run_with_runner(row, runner, verifier=verifier)
    assert run_.review.suggestions[0].colour is SuggestionColour.RED
    assert verifier.payloads == [] and run_.verifications == []


def test_a_note_free_chart_never_consults_the_runner():
    row = a_row()
    run_ = run_with_runner(row, NullQuoteRunner("note-free"), notes=[])
    assert run_.review.suggestions[0].colour is SuggestionColour.RED
    assert run_.notes_consulted == 0 and run_.model_calls == 0


def test_a_chart_with_no_candidate_never_consults_the_runner():
    row = a_row()
    run_ = run_with_runner(row, NullQuoteRunner("no candidate"), medications=[])
    assert run_.review.suggestions == () and run_.notes_consulted == 0


def test_no_runner_on_a_note_bearing_chart_raises_one_layer_up():
    row = a_row()
    with pytest.raises(history.QuoteSourceNotConsulted):
        run_with_runner(row, None)


def test_the_runner_is_asked_about_every_row_on_every_note_and_python_keeps_the_candidates():
    """D122: the request is one fixed configuration. Two rows, one candidate;
    the runner is asked both on both notes, and only the candidate is
    reviewed."""
    on_chart = a_row(row_id="drug-a", ingredient="1")
    absent = a_row(row_id="drug-b", ingredient="2", signal_code="2345-7").model_copy(
        update={"effect_display": "another effect"}
    )
    products = {**products_for(on_chart, "999"), **products_for(absent, "888")}
    runner = _StubQuoteRunner({})
    run_ = run_with_runner(
        on_chart, runner, rows=[on_chart, absent], products=products,
        notes=[a_hand_note("p1/chart_note_1.txt"), a_hand_note("p1/chart_note_2.txt", "Second note.\n")],
    )
    assert runner.asked == [
        ("p1/chart_note_1.txt", ("drug-a", "drug-b")),
        ("p1/chart_note_2.txt", ("drug-a", "drug-b")),
    ]
    assert [s.row_id for s in run_.review.suggestions] == ["drug-a"]
    assert run_.notes_consulted == 2 and run_.model_calls == 2


def test_quotes_from_two_notes_are_merged_in_store_order():
    row = a_row()
    second = "Follow-up: an effect is documented again here.\n"
    runner = _StubQuoteRunner({
        "p1/chart_note_1.txt": _quote_payload("an effect", HAND_VERBATIM),
        "p1/chart_note_2.txt": _quote_payload("an effect", "an effect is documented again here."),
    })
    run_ = run_with_runner(
        row, runner, notes=[a_hand_note(), a_hand_note("p1/chart_note_2.txt", second)],
    )
    (suggestion,) = run_.review.suggestions
    assert [c.document_id for c in suggestion.citations] == [
        "p1/chart_note_1.txt", "p1/chart_note_2.txt",
    ]


def test_a_condition_the_chart_already_codes_is_withheld_with_the_codes():
    row = a_row(already_coded=("111", "222"))
    review = run(
        row,
        observations=[an_observation(2.0)],
        conditions=[Condition(code="222", system=SNOMED, clinical_status="active")],
    )
    assert review.suggestions == ()
    withheld = review.withheld[0]
    assert withheld.reason is WithholdReason.ALREADY_CODED
    assert [c.code for c in withheld.suppressed_by] == ["222"]


def test_a_resolved_condition_does_not_withhold():
    """No chart in the corpus carries a resolved already-coded condition, so the
    corpus cannot tell a status-filtered suppression from an unfiltered one."""
    row = a_row(already_coded=("222",))
    review = run(
        row,
        observations=[an_observation(2.0)],
        conditions=[Condition(code="222", system=SNOMED, clinical_status="resolved")],
    )
    assert review.suggestions[0].colour is SuggestionColour.GREEN


def test_a_condition_in_another_vocabulary_does_not_withhold():
    row = a_row(already_coded=("222",))
    review = run(
        row,
        observations=[an_observation(2.0)],
        conditions=[Condition(code="222", system=RXNORM, clinical_status="active")],
    )
    assert review.suggestions[0].colour is SuggestionColour.GREEN


def test_a_row_that_declares_no_code_can_never_be_withheld_as_already_coded():
    """The hypotension row's shape: no reachable SNOMED code, declared as such.
    It is suggested again on a chart that codes the condition, and the row says
    so rather than the engine hiding it (REQ-62)."""
    row = a_row(already_coded=(), unsourced_reason="no reachable code (D36)")
    review = run(
        row,
        observations=[an_observation(2.0)],
        conditions=[Condition(code="111", system=SNOMED, clinical_status="active")],
    )
    assert review.suggestions[0].colour is SuggestionColour.GREEN
    assert review.withheld == ()


def test_a_red_suggestion_cannot_carry_a_citation():
    """The contract refuses it, so a caller cannot build one (REQ-5's shape)."""
    row = a_row()
    with pytest.raises(ValueError, match="red and cites"):
        IcdSuggestion(
            row_id="r",
            colour=SuggestionColour.RED,
            icd10_code="X00.0",
            icd10_title="t",
            effect_display="e",
            effect=row.effect,
            medication=CodedConcept(system=RXNORM, code="1"),
            ingredient=row.ingredient,
            citations=(EvidenceSpan(document_id="d", char_start=0, char_end=1),),
        )


def test_a_green_suggestion_whose_measurement_does_not_cross_is_refused():
    """Green **is** the comparison, so the contract redoes it.

    Without this, deleting the withholding branch would produce a green citing a
    normal result — a suggestion the chart refutes, built from the chart's own
    evidence, which every downstream check would agree with.
    """
    row = a_row()
    with pytest.raises(ValueError, match="does not cross"):
        IcdSuggestion(
            row_id="r",
            colour=SuggestionColour.GREEN,
            icd10_code="X00.0",
            icd10_title="t",
            effect_display="e",
            effect=row.effect,
            medication=CodedConcept(system=RXNORM, code="1"),
            ingredient=row.ingredient,
            citations=(EvidenceSpan(document_id="d", char_start=0, char_end=1),),
            signal=row.signal,
            observed_value=0.9,
            observed_on=date(2026, 1, 1),
        )


def test_no_suggestion_reaches_a_determination():
    """REQ-65 made structural: `Determination` has no field for one, and no
    module on the criteria path imports the review."""
    from pa_agent.contracts import Determination

    assert not [
        name
        for name in Determination.model_fields
        if "suggestion" in name or "history" in name
    ]
    for module in ("workflow", "aggregate", "criteria", "determination", "resolver"):
        source = (PACKAGE / f"{module}.py").read_text(encoding="utf-8")
        assert "history" not in [
            node.module.split(".")[-1] if node.module else ""
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        ], f"{module}.py imports the review; a suggestion enters no verdict"


def test_the_cli_emits_no_suggestions_block_at_this_close():
    """Working rule 1: `--suggest` is T-99's, and this row must not build ahead.

    Structural rather than a subprocess: the CLI does not import the review and
    names no suggestion key, so there is no path by which a determination printed
    today could carry one.
    """
    source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("history" in name for name in imported), (
        "cli.py imports the review; the --suggest surface is T-99's"
    )
    assert "icd_suggestions" not in source and "--suggest" not in source, (
        "cli.py names the suggestion surface T-99 has not built yet"
    )


# --------------------------------------------------------------------------
# REQ-66 — would_affect is membership, and it changes nothing
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ultrasound_tree():
    return LocalPolicyStore().get_tree(ULTRASOUND_TREE)


@pytest.fixture(scope="module")
def ultrasound_value_sets(ultrasound_tree):
    store = LocalPolicyStore()
    sets = {}
    for criterion in ultrasound_tree.criteria:
        constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
        if constant is not None:
            sets[str(constant.value)] = store.get_value_set(str(constant.value))
    return sets


def test_would_affect_names_the_criterion_whose_set_admits_the_coded_condition(
    ultrasound_tree, ultrasound_value_sets
):
    """The real tree and the real row, because this is the one non-empty
    `would_affect` the corpus can produce."""
    row = next(
        r
        for r in LocalKnowledgeStore().get_medication_effect_rows()
        if r.row_id == "lisinopril-renal-impairment"
    )
    review = history.review(
        patient_id="p1",
        policy_version_id=ULTRASOUND_TREE,
        rows=[row],
        products=products_for(row, "314076"),
        medications=[a_medication(code="314076")],
        conditions=[],
        observations=[an_observation(2.1, code=row.signal.code)],
        criteria=ultrasound_tree.criteria,
        value_sets=ultrasound_value_sets,
    )
    suggestion = review.suggestions[0]
    assert suggestion.would_affect == ("a",)
    assert suggestion.would_affect_note is None


def test_the_icd10_code_matches_no_value_set_in_this_corpus(ultrasound_value_sets):
    """Why `would_affect` reads the already-coded SNOMED codes and not the row's
    own ICD-10 code: the ICD-keyed comparison is empty everywhere, so it passes
    every behavioural test while reporting nothing (D119)."""
    store = LocalKnowledgeStore()
    policy = LocalPolicyStore()
    every_set = [
        policy.get_value_set(path.stem)
        for path in sorted(
            (REPO_ROOT / "data" / "policies" / "value_sets").glob("*.json")
        )
    ]
    for row in store.get_medication_effect_rows():
        assert not any(
            value_set.admits(row.icd10_code, value_set.system)
            for value_set in every_set
        ), f"{row.icd10_code} is admitted by a value set; the ICD-keyed reading works"


def test_a_row_with_no_reachable_code_reports_why_would_affect_is_empty(
    ultrasound_tree, ultrasound_value_sets
):
    row = a_row(already_coded=(), unsourced_reason="no reachable code (D36)")
    review = history.review(
        patient_id="p1",
        policy_version_id=ULTRASOUND_TREE,
        rows=[row],
        products=products_for(row, "999"),
        medications=[a_medication()],
        conditions=[],
        observations=[an_observation(2.0)],
        criteria=ultrasound_tree.criteria,
        value_sets=ultrasound_value_sets,
    )
    suggestion = review.suggestions[0]
    assert suggestion.would_affect == ()
    assert "nothing to compare" in suggestion.would_affect_note


def test_a_criterion_naming_no_value_set_is_skipped_rather_than_raising(
    ultrasound_value_sets
):
    """An unclaimed criterion declares no value set; `would_affect` steps over it
    rather than treating a missing constant as a match or a fault."""
    row = a_row()
    unclaimed = Criterion(
        id="z",
        label="a criterion this system does not evaluate",
        evaluation="unclaimed",
        note="declared unclaimed by the document",
    )
    review = history.review(
        patient_id="p1",
        policy_version_id="a-tree-v1",
        rows=[row],
        products=products_for(row, "999"),
        medications=[a_medication()],
        conditions=[],
        observations=[an_observation(2.0)],
        criteria=[unclaimed],
        value_sets=ultrasound_value_sets,
    )
    assert review.suggestions[0].would_affect == ()


def test_a_criterion_whose_set_was_not_supplied_is_skipped(ultrasound_tree):
    row = a_row()
    review = history.review(
        patient_id="p1",
        policy_version_id=ULTRASOUND_TREE,
        rows=[row],
        products=products_for(row, "999"),
        medications=[a_medication()],
        conditions=[],
        observations=[an_observation(2.0)],
        criteria=ultrasound_tree.criteria,
        value_sets={},
    )
    assert review.suggestions[0].would_affect == ()


# --------------------------------------------------------------------------
# The committed charts the three eval rows run on
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_review():
    knowledge = LocalKnowledgeStore()
    patient = LocalPatientStore()
    policy = LocalPolicyStore()
    rows = knowledge.get_medication_effect_rows()
    products = {
        row.ingredient.code: knowledge.get_ingredient_products(row.ingredient.code)
        for row in rows
    }

    def review_for(patient_id: str, procedure_code: str):
        tree = policy.get_tree(
            policy.resolve(
                procedure_code, patient.get_jurisdiction_state(patient_id)
            ).policy_version_id
        )
        sets = {}
        for criterion in tree.criteria:
            constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
            if constant is not None:
                sets[str(constant.value)] = policy.get_value_set(str(constant.value))
        return history.review(
            patient_id=patient_id,
            policy_version_id=tree.policy_version_id,
            rows=rows,
            products=products,
            medications=patient.get_medications(patient_id),
            conditions=patient.get_conditions(patient_id),
            observations=patient.get_observations(patient_id),
            criteria=tree.criteria,
            value_sets=sets,
            notes=patient.get_notes(patient_id),
        )

    return review_for


def test_h1s_chart_yields_one_green_suggestion_citing_its_declared_creatinine(
    corpus_review,
):
    review = corpus_review(H1_PATIENT, "93975")
    assert review.by_colour == {"green": 1, "yellow": 0, "red": 0}
    suggestion = review.suggestions[0]
    assert (suggestion.row_id, suggestion.icd10_code) == (
        "lisinopril-renal-impairment",
        "N28.9",
    )
    assert suggestion.observed_value == 2.1
    assert suggestion.would_affect == ("a",)
    assert suggestion.citations[0].document_id.endswith(".json")


def test_h2s_chart_withholds_both_ways_and_suggests_nothing(corpus_review):
    """One chart, both declared reasons: the same drug as H1 at a crossing
    creatinine the chart already codes, and a glucose series that answered no."""
    review = corpus_review("b1bbb34a-aa4b-5c18-a550-8bd26e0da528", "93975")
    assert review.suggestions == ()
    assert [(w.row_id, w.reason.value) for w in review.withheld] == [
        ("hydrochlorothiazide-hyperglycemia", "SIGNAL_NOT_CROSSED"),
        ("lisinopril-renal-impairment", "ALREADY_CODED"),
    ]


def test_h3s_chart_yields_one_red_suggestion_citing_nothing(corpus_review):
    review = corpus_review("42a430ab-b7ca-87a5-279f-ee115f49fd6e", "J1745")
    assert review.by_colour == {"green": 0, "yellow": 0, "red": 1}
    suggestion = review.suggestions[0]
    assert (suggestion.row_id, suggestion.icd10_code) == (
        "methotrexate-neutropenia",
        "D70.2",
    )
    assert suggestion.citations == ()


H4_PATIENT = "bc6748d3-3a0f-9734-7730-4518f5b268fb"
HISTORY_RESULTS = REPO_ROOT / "eval" / "history" / "results.json"
VERIFIER_RESULTS = REPO_ROOT / "eval" / "verifier" / "results.json"


def test_the_note_bearing_chart_with_a_candidate_raises_with_no_quote_source(corpus_review):
    """`bc6748d3` carries an active lisinopril, no creatinine, and notes that
    mention nothing renal, so `review` with no quote source raises rather
    than answering red (D90, D119) — still, with the runner one layer up."""
    with pytest.raises(history.QuoteSourceNotConsulted):
        corpus_review(H4_PATIENT, "43775")


@pytest.fixture(scope="module")
def corpus_run():
    """`corpus_review`'s twin through `run_review`, with the recorded quote
    runner and the recorded verifier — the two things every gate replays."""
    assert HISTORY_RESULTS.exists(), (
        "eval/history/results.json is missing; run scripts/run_quote_measurement.py "
        "(it spends model calls)"
    )
    recording = json.loads(HISTORY_RESULTS.read_text(encoding="utf-8"))
    quote_runner = RecordedQuoteRunner.from_records(
        recording["notes"], recording["rows_asked"], model=recording["model"]
    )
    verifier = RecordedVerifierRunner.from_records(
        json.loads(VERIFIER_RESULTS.read_text(encoding="utf-8"))["claims"]
    )
    knowledge = LocalKnowledgeStore()
    patient = LocalPatientStore()
    policy = LocalPolicyStore()
    rows = knowledge.get_medication_effect_rows()
    products = {
        row.ingredient.code: knowledge.get_ingredient_products(row.ingredient.code)
        for row in rows
    }

    def run_for(patient_id: str, procedure_code: str) -> history.HistoryRun:
        tree = policy.get_tree(
            policy.resolve(
                procedure_code, patient.get_jurisdiction_state(patient_id)
            ).policy_version_id
        )
        sets = {}
        for criterion in tree.criteria:
            constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
            if constant is not None:
                sets[str(constant.value)] = policy.get_value_set(str(constant.value))
        return history.run_review(
            quote_runner=quote_runner,
            patient_id=patient_id,
            policy_version_id=tree.policy_version_id,
            rows=rows,
            products=products,
            medications=patient.get_medications(patient_id),
            conditions=patient.get_conditions(patient_id),
            observations=patient.get_observations(patient_id),
            criteria=tree.criteria,
            value_sets=sets,
            notes=patient.get_notes(patient_id),
            verifier=verifier,
        )

    return run_for


def test_h4s_chart_reads_red_with_both_notes_consulted_and_every_turn_counted(corpus_run):
    """T-98's row (D122): the recorded runner is consulted on both of the
    chart's notes for every table condition, none anchors for renal
    impairment, and the candidate is red — proved by a recording, not
    assumed. No yellow, so the recorded verifier is never reached."""
    run_ = corpus_run(H4_PATIENT, "43775")
    assert run_.review.policy_version_id == "ncd-100.1-jf-v1"
    assert run_.review.by_colour == {"green": 0, "yellow": 0, "red": 1}
    (suggestion,) = run_.review.suggestions
    assert (suggestion.row_id, suggestion.icd10_code) == ("lisinopril-renal-impairment", "N28.9")
    assert suggestion.citations == () and suggestion.verifier_rejected is False
    assert suggestion.would_affect == ()
    assert run_.review.withheld == ()
    assert run_.notes_consulted == 2
    assert run_.model_calls == sum(len(t.metrics) for t in run_.traces) >= 2
    assert run_.verifications == [] and run_.rejections == ()
    assert {t.document_id for t in run_.traces} == {
        f"{H4_PATIENT}/chart_note_1.txt", f"{H4_PATIENT}/chart_note_2.txt",
    }


def test_the_other_labeled_charts_reach_the_same_review_through_run_review(
    corpus_review, corpus_run
):
    for patient_id, code in (
        (H1_PATIENT, "93975"),
        ("b1bbb34a-aa4b-5c18-a550-8bd26e0da528", "93975"),
        ("42a430ab-b7ca-87a5-279f-ee115f49fd6e", "J1745"),
    ):
        run_ = corpus_run(patient_id, code)
        assert run_.review == corpus_review(patient_id, code)
        assert run_.notes_consulted == 0, "note-free: nothing to consult"


def test_every_suggestion_the_corpus_produces_slices_back(corpus_run):
    """Article III over the review's own citations, on both corpora: a chart span
    into the bundle and an effect span into a hashed label."""
    from pa_agent.index import DocumentIndex
    from pa_agent.spans import validate

    knowledge, patient = LocalKnowledgeStore(), LocalPatientStore()
    index = DocumentIndex()
    checked = 0
    for patient_id, code in (
        (H1_PATIENT, "93975"),
        ("b1bbb34a-aa4b-5c18-a550-8bd26e0da528", "93975"),
        ("42a430ab-b7ca-87a5-279f-ee115f49fd6e", "J1745"),
        (H4_PATIENT, "43775"),
    ):
        review = corpus_run(patient_id, code).review
        for item in (*review.suggestions, *review.withheld):
            spans = list(item.citations)
            if getattr(item, "effect", None) is not None:
                spans.append(item.effect)
            for span in spans:
                if span.document_id not in index:
                    try:
                        index.add(patient.get_document(span.document_id))
                    except KeyError:
                        index.add(knowledge.get_document(span.document_id))
                validate(span, index)
                checked += 1
    assert checked >= 8, f"only {checked} span(s) checked; the corpus produces more"


def test_the_declared_constant_every_criterion_names_its_value_set_in_is_the_trees():
    """`history.VALUE_SET_CONSTANT` must be the name the committed trees use, or
    `would_affect` silently finds no criterion to compare against."""
    policy = LocalPolicyStore()
    named = 0
    for path in sorted((REPO_ROOT / "data" / "policies").glob("*.json")):
        tree = policy.get_tree(json.loads(path.read_text(encoding="utf-8"))["policy_version_id"])
        for criterion in tree.criteria:
            if history.VALUE_SET_CONSTANT in criterion.constants:
                named += 1
    assert named >= 4, (
        f"only {named} committed criteria name {history.VALUE_SET_CONSTANT!r}; "
        "would_affect compares against nothing"
    )
