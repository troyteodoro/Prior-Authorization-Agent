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

**There is no stub quote source class.** The review takes the anchored quotes as
a mapping, so yellow is produced here by passing one; nothing under `pa_agent/`
implements a quote port at this close, and T-98 is the row that adds one. D78's
rule — an accept-all implementation lives in `tests/` and never in the package —
is satisfied by there being nothing to place.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent import history
from pa_agent.contracts import (
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
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

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


def test_no_quote_source_on_a_chart_with_notes_raises_and_names_the_next_row():
    """D90, on a second wire. Answering red here would report *the chart does not
    say* for a chart nobody read."""
    row = a_row()
    with pytest.raises(history.QuoteSourceNotConsulted) as caught:
        run(row, notes=[a_note()], quotes=None)
    assert "T-98" in str(caught.value)


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


def test_the_one_note_bearing_chart_with_a_candidate_raises_until_t98(corpus_review):
    """`bc6748d3` carries an active lisinopril, no creatinine, and notes that
    mention nothing renal. It is why the review is computed only for rows that
    label one, and it is T-98's yellow host."""
    with pytest.raises(history.QuoteSourceNotConsulted):
        corpus_review("bc6748d3-3a0f-9734-7730-4518f5b268fb", "43775")


def test_every_suggestion_the_corpus_produces_slices_back(corpus_review):
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
    ):
        review = corpus_review(patient_id, code)
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
