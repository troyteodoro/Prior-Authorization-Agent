"""T-109 — the fifth practice's tree and its three predicates (REQ-79, D154).

`hyaluronan-knee-oa-j5-j8-v1` is compiled from WPS's L39529 and its billing
article A56157, for A/B MAC Jurisdictions 5 and 8. No NCD stands over it. This
file holds the tree and the arithmetic on charts written **here**;
`tests/test_knee_oa_corpus.py` holds the committed charts. D114's division: the
corpus has seven charts and cannot reach every branch, so each predicate is
exercised in both directions on facts built below, and the one `NOT_MET` kind
is put through `check_citation_sufficiency`, which re-derives it from what it
cites (D99).
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent import criteria as criteria_module
from pa_agent.contracts import (
    ConservativeTherapy,
    ConservativeTherapyCategory,
    CoverageStatus,
    CriteriaTree,
    CriterionVerdict,
    EvidenceSpan,
    FactKind,
    GapReason,
    KneeRadiograph,
    KneeSymptom,
    KneeSymptomCategory,
    PredicateKind,
    RadiographicFinding,
    RadiographicFindingCategory,
)
from pa_agent.criteria import (
    NARROWERS,
    PredicateInputs,
    QualifyingRun,
    check_citation_sufficiency,
    evaluate,
)
from pa_agent.extraction import build_knee_result, extract_with_reask
from pa_agent.stores.policy import LocalPolicyStore, UnknownJurisdiction

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = REPO_ROOT / "data" / "policies" / "hyaluronan_knee_oa_j5_j8.json"
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
TREE_ID = "hyaluronan-knee-oa-j5-j8-v1"
AS_OF = date(2026, 9, 1)
KNEE = FactKind.KNEE_OSTEOARTHRITIS_WORKUP
NONPHARM = ConservativeTherapyCategory.NONPHARMACOLOGIC
PHARM = ConservativeTherapyCategory.SIMPLE_ANALGESIC_OR_NSAID


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(store) -> CriteriaTree:
    return store.get_tree(TREE_ID)


_counter = iter(range(10_000))


def _span(name: str) -> EvidenceSpan:
    start = next(_counter) * 10
    return EvidenceSpan(document_id=f"note/{name}", char_start=start, char_end=start + 5, quote=None)


def _symptom(category: KneeSymptomCategory) -> KneeSymptom:
    return KneeSymptom(category=category, span=_span("symptom"))


def _radiograph(when: str, *findings: RadiographicFindingCategory) -> KneeRadiograph:
    return KneeRadiograph(
        radiograph_date=date.fromisoformat(when),
        span=_span("radiograph"),
        findings=tuple(RadiographicFinding(category=f, span=_span("finding")) for f in findings),
    )


def _therapy(category: ConservativeTherapyCategory, when: str) -> ConservativeTherapy:
    return ConservativeTherapy(category=category, start_date=date.fromisoformat(when), span=_span("therapy"))


def _inputs(*facts, as_of: date = AS_OF) -> PredicateInputs:
    return PredicateInputs(as_of=as_of, facts={KNEE: tuple(facts)})


def _run(tree, criterion_id: str, *facts, as_of: date = AS_OF):
    return evaluate(tree.criterion(criterion_id), _inputs(*facts, as_of=as_of))


def _rederives(tree, result, *facts, as_of: date = AS_OF) -> None:
    """D99: a `NOT_MET` re-derives from its citations alone, or this raises."""
    check_citation_sufficiency(
        tree.criterion(result.criterion_id),
        result,
        observations=[],
        procedures=[],
        value_sets={},
        run=QualifyingRun(months=(), events=()),
        as_of=as_of,
        c3_met=False,
        facts={KNEE: tuple(facts)},
    )


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def test_the_tree_declares_its_practice_kinds_letters_and_jurisdiction(tree):
    assert tree.practice == "orthopedics"
    assert tree.fact_kinds == (KNEE,)
    assert [c.id for c in tree.criteria] == ["a", "b", "c", "d", "e"]
    assert [c.evaluation for c in tree.criteria] == [
        "deterministic", "deterministic", "unclaimed", "deterministic", "unclaimed",
    ]
    assert tree.decision_expression == "a AND b AND c AND d AND e"
    # J-5's and J-8's A/B states only: contract 05901's forty-eight are not a
    # by-state jurisdiction (D154), and Texas stays unserved.
    assert sorted(tree.jurisdiction.states) == ["IA", "IN", "KS", "MI", "MO", "NE"]
    assert all(c.floors() == () for c in tree.criteria), "no NCD stands over L39529"


def test_every_hyaluronan_code_resolves_here_and_texas_is_unserved(store, tree):
    (entry,) = tree.procedure_sets.contractor_determined
    codes = [b.code for b in entry.codes]
    assert codes == [
        "J7318", "J7320", "J7321", "J7322", "J7323", "J7324", "J7325",
        "J7326", "J7327", "J7328", "J7329", "J7331", "J7332",
    ]
    for code in codes:
        ref = store.resolve(code, "IA")
        assert ref is not None and ref.policy_version_id == TREE_ID, code
        assert ref.coverage is CoverageStatus.CONTRACTOR_DETERMINED
    # The injection's own CPT code is not bound (D154), and the sleep tree
    # still answers E0601 in the same state: nothing collides.
    assert store.resolve("20610", "IA") is None
    assert store.resolve("E0601", "IA").policy_version_id == "pap-osa-dme-jd-v1"
    with pytest.raises(UnknownJurisdiction):
        store.resolve("J7325", "TX")


def test_every_binding_and_the_coverage_claim_slice_back(tree):
    (entry,) = tree.procedure_sets.contractor_determined
    lcd = (SOURCE_DIR / "l39529.txt").read_text(encoding="utf-8")
    article = (SOURCE_DIR / "a56157.txt").read_text(encoding="utf-8")
    claim = entry.coverage_claim
    assert lcd[claim.char_start:claim.char_end] == claim.quote
    for binding in entry.codes:
        assert binding.system == "HCPCS" and binding.identity
        assert article[binding.char_start:binding.char_end] == binding.quote
        assert binding.quote.startswith(binding.code + "\n"), binding.code


def test_every_constant_slices_back_from_l39529(tree):
    lcd = (SOURCE_DIR / "l39529.txt").read_text(encoding="utf-8")
    for criterion in tree.criteria:
        for name, constant in criterion.constants.items():
            source = constant.source
            assert source is not None and source.document_id == "l39529", (criterion.id, name)
            assert lcd[source.char_start:source.char_end] == source.quote, (criterion.id, name)
    assert tree.criterion("d").constant("min_months").value == 3


# --------------------------------------------------------------------------
# a: the patient is symptomatic
# --------------------------------------------------------------------------


def test_a_documented_symptom_is_met_citing_every_qualifying_one(tree):
    pain = _symptom(KneeSymptomCategory.PAIN_LIMITING_DAILY_ACTIVITIES)
    stiffness = _symptom(KneeSymptomCategory.KNEE_STIFFNESS)
    result = _run(tree, "a", pain, stiffness)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [pain.span, stiffness.span]


def test_no_documented_symptom_abstains_and_never_denies(tree):
    result = _run(tree, "a", _radiograph("2026-01-01", RadiographicFindingCategory.OSTEOPHYTES))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_a_symptom_qualifies_by_the_trees_list_and_not_by_its_label(tree):
    raw = json.loads(TREE_PATH.read_text(encoding="utf-8"))
    raw["criteria"][0]["constants"]["qualifying_symptoms"]["value"] = ["crepitus"]
    narrowed = CriteriaTree.model_validate(raw)
    stiffness = _symptom(KneeSymptomCategory.KNEE_STIFFNESS)
    assert _run(narrowed, "a", stiffness).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert _run(tree, "a", stiffness).verdict is CriterionVerdict.MET


# --------------------------------------------------------------------------
# b: radiologic evidence
# --------------------------------------------------------------------------


def test_a_radiograph_with_a_listed_finding_is_met_citing_it_and_its_findings(tree):
    radiograph = _radiograph(
        "2026-01-01",
        RadiographicFindingCategory.JOINT_SPACE_NARROWING,
        RadiographicFindingCategory.OSTEOPHYTES,
    )
    result = _run(tree, "b", radiograph)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [radiograph.span] + [f.span for f in radiograph.findings]


def test_a_radiograph_reporting_none_of_the_four_abstains_because_the_list_is_open(tree):
    result = _run(tree, "b", _radiograph("2026-01-01"))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert "such as" in result.detail


def test_no_radiograph_or_only_a_later_one_abstains(tree):
    assert _run(tree, "b").verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    later = _radiograph("2026-10-01", RadiographicFindingCategory.OSTEOPHYTES)
    assert _run(tree, "b", later).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_the_latest_supporting_radiograph_is_the_one_cited(tree):
    older = _radiograph("2023-06-22", RadiographicFindingCategory.SUBCHONDRAL_CYSTS)
    normal = _radiograph("2025-01-01")
    newer = _radiograph("2026-07-09", RadiographicFindingCategory.OSTEOPHYTES)
    result = _run(tree, "b", older, normal, newer)
    assert result.verdict is CriterionVerdict.MET and result.spans[0] == newer.span
    # A later radiograph with none of the four does not undo an earlier one.
    assert _run(tree, "b", older, normal).spans[0] == older.span


# --------------------------------------------------------------------------
# d: at least three months of conservative therapy
# --------------------------------------------------------------------------


def test_both_legs_at_three_months_are_met_citing_each_earliest_start(tree):
    early = _therapy(PHARM, "2026-01-15")
    later_dup = _therapy(PHARM, "2026-03-01")
    exercise = _therapy(NONPHARM, "2026-06-01")
    result = _run(tree, "d", early, later_dup, exercise)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [exercise.span, early.span]


def test_the_boundary_is_whole_calendar_months_and_inclusive(tree):
    """2026-06-01 to 2026-09-01 is three months, met; to 2026-08-31 is two."""
    facts = (_therapy(PHARM, "2026-06-01"), _therapy(NONPHARM, "2026-06-01"))
    assert _run(tree, "d", *facts).verdict is CriterionVerdict.MET
    short = _run(tree, "d", *facts, as_of=date(2026, 8, 31))
    assert short.verdict is CriterionVerdict.NOT_MET
    assert (short.shortfall.observed, short.shortfall.required) == (2, 3)
    assert short.shortfall.unit == "months_of_conservative_therapy"


def test_the_shorter_leg_decides_and_the_not_met_rederives(tree):
    nsaid = _therapy(PHARM, "2026-01-22")
    therapy = _therapy(NONPHARM, "2026-07-26")
    result = _run(tree, "d", nsaid, therapy)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.shortfall.observed == 1
    assert set(result.spans) == {nsaid.span, therapy.span}
    _rederives(tree, result, nsaid, therapy)


@pytest.mark.parametrize("missing", [NONPHARM, PHARM], ids=["no-nonpharm", "no-pharm"])
def test_an_undocumented_leg_abstains_rather_than_denying(tree, missing):
    present = PHARM if missing is NONPHARM else NONPHARM
    result = _run(tree, "d", _therapy(present, "2020-01-01"))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert missing.value in result.detail


def test_a_therapy_begun_after_the_clock_is_not_counted(tree):
    facts = (_therapy(PHARM, "2020-01-01"), _therapy(NONPHARM, "2026-10-01"))
    assert _run(tree, "d", *facts).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_the_narrower_keeps_only_what_a_verdict_cites(tree):
    """`check_citation_sufficiency` re-derives over what the narrower keeps; on
    a correct predicate an identity narrower re-derives the same answer, so
    this holds it directly (D99, D150's lesson)."""
    cited, uncited = _therapy(PHARM, "2026-01-22"), _therapy(NONPHARM, "2020-01-01")
    narrowed = NARROWERS[PredicateKind.NOTE_CONSERVATIVE_THERAPY_DURATION](
        _inputs(cited, uncited), [cited.span]
    )
    assert narrowed.facts[KNEE] == (cited,)


def test_no_knee_predicate_reads_another_kinds_facts(tree):
    """A knee binder asks for the knee kind by name; filed under the sleep kind,
    the same facts are invisible to it (REQ-79)."""
    facts = (_therapy(PHARM, "2020-01-01"), _therapy(NONPHARM, "2020-01-01"))
    misfiled = PredicateInputs(as_of=AS_OF, facts={FactKind.SLEEP_APNEA_WORKUP: facts})
    assert evaluate(tree.criterion("d"), misfiled).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------
# The trust boundary, through the real anchorer on a note written here
# --------------------------------------------------------------------------

NOTE = (
    "FOLLOW-UP VISIT\n04/26/2026 - Seen in person.\n"
    "Reports morning stiffness of the right knee.\n"
    "01/22/2026 - Weight-bearing radiographs of the right knee: Marginal "
    "osteophytes at the tibiofemoral joint.\n"
    "Naproxen sodium 220 mg by mouth twice daily, started 01/22/2026.\n"
)


def test_the_builder_anchors_every_quote_and_drops_a_finding_it_cannot_locate():
    payload = {
        "symptoms": [{"category": "knee_stiffness",
                      "quote": "Reports morning stiffness of the right knee.",
                      "char_start": 0, "char_end": 0}],
        "radiographs": [{
            "date": "2026-01-22", "quote": "01/22/2026 - Weight-bearing radiographs",
            "char_start": 0, "char_end": 0,
            "findings": [
                {"category": "osteophytes", "quote": "Marginal osteophytes",
                 "char_start": 0, "char_end": 0},
                {"category": "subchondral_cysts", "quote": "Subchondral cysts in the condyle",
                 "char_start": 0, "char_end": 0},
            ],
        }],
        "conservative_therapies": [{"category": "simple_analgesic_or_nsaid",
                                    "start_date": "2026-01-22",
                                    "quote": "Naproxen sodium 220 mg by mouth twice daily, started 01/22/2026.",
                                    "char_start": 0, "char_end": 0}],
    }
    result = build_knee_result("note/doc.txt", NOTE, payload)
    assert result.kind is KNEE and not result.events and not result.assertions
    (radiograph,) = [f for f in result.facts if isinstance(f, KneeRadiograph)]
    assert [f.category for f in radiograph.findings] == [RadiographicFindingCategory.OSTEOPHYTES]
    assert result.dropped == [{
        "reason": "finding_quote_unanchorable",
        "quote": "Subchondral cysts in the condyle",
        "path": "radiographs[0].findings[1].quote",
    }]
    for fact in result.facts:
        assert NOTE[fact.span.char_start:fact.span.char_end] == fact.span.quote


def test_the_knee_builder_walks_the_shared_reask_core():
    """The live runners walk `extract_with_reask` with each schema's builder and
    locator; the knee locator resolves the nested finding path the builder
    names, which is what lets the re-ask patch it (T-89, REQ-79)."""
    from pa_agent.extraction import FACT_SCHEMAS

    schema = FACT_SCHEMAS[KNEE]
    payload = {"radiographs": [{"quote": "x", "findings": [{"quote": "y"}]}]}
    container, key = schema.locate(payload, "radiographs[0].findings[0].quote")
    assert container[key] == "y"
    with pytest.raises(KeyError):
        schema.locate(payload, "radiographs[0].findings[1].quote")
    with pytest.raises(KeyError):
        schema.locate(payload, "radiographs[0].date")
    assert "locate" in inspect.signature(extract_with_reask).parameters


def test_the_knee_predicates_name_no_criterion_id():
    """Kind-keyed dispatch (REQ-57, D110): the three predicates read their
    constants by name and never branch on a criterion's letter."""
    for function in (
        criteria_module.evaluate_knee_symptoms,
        criteria_module.evaluate_knee_radiographic_findings,
        criteria_module.evaluate_conservative_therapy_duration,
    ):
        tree_ = ast.parse(inspect.getsource(function).lstrip())
        compared = [
            n for n in ast.walk(tree_)
            if isinstance(n, ast.Compare)
            and any(isinstance(c, ast.Constant) and c.value in {"a", "b", "c", "d", "e"}
                    for c in n.comparators)
        ]
        assert not compared, function.__name__
