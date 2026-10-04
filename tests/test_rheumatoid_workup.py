"""T-110 — the rheumatoid tree's `c`, `d` and `e`, read through the fifth fact
kind (D155).

`infliximab-ra-jjm-v1` declared all three unclaimed from T-92 (D111): the NYHA
class, the tuberculosis screen and the disease-activity level are not in the
coded record. Since T-110 the tree declares `rheumatoid_arthritis_workup` and
reads each where a note states it:

- `note_heart_failure_class` (`c`): the latest stated status, against L35677's
  *"Class III or IV"*;
- `note_tuberculosis_screening` (`d`): the latest screen, and whether a
  positive one was treated;
- `note_disease_activity` (`e`): the latest level **stated in words**, against
  *"moderately to severely active"*. No score is ever graded into a level.

Each kind in both directions on facts written here, each `NOT_MET` re-derived
(D99), and the trust boundary and recordings.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriteriaTree,
    CriterionVerdict,
    DiseaseActivityAssessment,
    DiseaseActivityLevel,
    EvidenceSpan,
    FactKind,
    GapReason,
    HeartFailureAssessment,
    HeartFailureClass,
    PredicateKind,
    TuberculosisScreen,
    TuberculosisScreenResult,
    TuberculosisTreatment,
)
from pa_agent.criteria import (
    CitationInsufficient,
    PredicateInputs,
    QualifyingRun,
    check_citation_sufficiency,
    evaluate,
)
from pa_agent.extraction import (
    FACT_SCHEMAS,
    RHEUMATOID_INSTRUCTION,
    RHEUMATOID_PROMPT_VERSION,
    build_rheumatoid_workup_result,
)
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION = REPO_ROOT / "eval" / "extraction"
TREE_ID = "infliximab-ra-jjm-v1"
AS_OF = date(2026, 9, 1)
KIND = FactKind.RHEUMATOID_ARTHRITIS_WORKUP
HF = HeartFailureClass
TB = TuberculosisScreenResult
LEVEL = DiseaseActivityLevel

#: The kind's digest beside its version (D149). **If this fails, the prompt
#: changed**: a new version and a new measurement, never a new literal.
RHEUMATOID_ARTHRITIS_WORKUP_DIGEST = (
    "4e06fcc35f5c876ee74e33d9cc97f2a7e6cc4adfa6df70ae97163236a2e48e40"
)
RHEUMATOID_ARTHRITIS_WORKUP_VERSION = "t110-rheumatoid-arthritis-workup-v1/t89-reask-v1"


@pytest.fixture(scope="module")
def tree() -> CriteriaTree:
    return LocalPolicyStore().get_tree(TREE_ID)


_counter = iter(range(10_000))


def _span() -> EvidenceSpan:
    start = next(_counter) * 10
    return EvidenceSpan(document_id="note/x", char_start=start, char_end=start + 5, quote=None)


def _hf(status: HeartFailureClass, when: str) -> HeartFailureAssessment:
    return HeartFailureAssessment(status=status, assessment_date=date.fromisoformat(when), span=_span())


def _screen(result: TuberculosisScreenResult, when: str) -> TuberculosisScreen:
    return TuberculosisScreen(result=result, screen_date=date.fromisoformat(when), span=_span())


def _treatment(when: str) -> TuberculosisTreatment:
    return TuberculosisTreatment(start_date=date.fromisoformat(when), span=_span())


def _activity(level: DiseaseActivityLevel, when: str) -> DiseaseActivityAssessment:
    return DiseaseActivityAssessment(level=level, assessment_date=date.fromisoformat(when), span=_span())


def _run(tree, criterion_id: str, *facts):
    return evaluate(tree.criterion(criterion_id), PredicateInputs(as_of=AS_OF, facts={KIND: tuple(facts)}))


def _rederive(tree, result, *facts) -> None:
    check_citation_sufficiency(
        tree.criterion(result.criterion_id), result,
        observations=[], procedures=[], value_sets={},
        run=QualifyingRun(months=(), events=()), as_of=AS_OF, c3_met=False,
        facts={KIND: tuple(facts)},
    )


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def test_the_tree_declares_the_kind_and_claims_c_d_and_e(tree):
    assert tree.fact_kinds == (KIND,)
    kinds = {c.id: c.kind for c in tree.criteria}
    assert kinds["c"] is PredicateKind.NOTE_HEART_FAILURE_CLASS
    assert kinds["d"] is PredicateKind.NOTE_TUBERCULOSIS_SCREENING
    assert kinds["e"] is PredicateKind.NOTE_DISEASE_ACTIVITY
    assert [c.id for c in tree.criteria if c.evaluation == "unclaimed"] == []


def test_each_list_constant_slices_back_to_the_lcd(tree):
    text = (REPO_ROOT / "data" / "policies" / "source" / "l35677.txt").read_text(encoding="utf-8")
    for criterion_id, name, value in (
        ("c", "excluded_classes", ["class_iii", "class_iv"]),
        ("e", "qualifying_activity", ["moderate", "high"]),
    ):
        constant = tree.criterion(criterion_id).constants[name]
        assert constant.value == value
        assert text[constant.source.char_start:constant.source.char_end] == constant.source.quote


def test_the_kind_is_registered_with_its_digest_pinned():
    schema = FACT_SCHEMAS[KIND]
    assert schema.build is build_rheumatoid_workup_result
    assert schema.prompt_version == RHEUMATOID_PROMPT_VERSION == RHEUMATOID_ARTHRITIS_WORKUP_VERSION
    assert schema.digest == RHEUMATOID_ARTHRITIS_WORKUP_DIGEST


def test_the_instruction_never_asks_the_model_to_grade_a_score():
    """Article II: a score with no level stated in words is no fact, because
    grading it needs a cut-off no corpus document states (D21, D155)."""
    assert "Never derive a level from a score" in RHEUMATOID_INSTRUCTION
    assert "deferred" in RHEUMATOID_INSTRUCTION


# --------------------------------------------------------------------------
# c: heart failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,verdict",
    [
        (HF.NO_HEART_FAILURE, CriterionVerdict.MET),
        (HF.CLASS_I, CriterionVerdict.MET),
        (HF.CLASS_II, CriterionVerdict.MET),
        (HF.CLASS_III, CriterionVerdict.NOT_MET),
        (HF.CLASS_IV, CriterionVerdict.NOT_MET),
    ],
)
def test_c_reads_the_class_against_the_excluded_ones(tree, status, verdict):
    fact = _hf(status, "2025-12-09")
    result = _run(tree, "c", fact)
    assert result.verdict is verdict
    assert result.spans == [fact.span]
    if verdict is CriterionVerdict.NOT_MET:
        _rederive(tree, result, fact)


def test_c_takes_the_latest_assessment(tree):
    older, newer = _hf(HF.CLASS_III, "2025-01-01"), _hf(HF.CLASS_II, "2025-12-01")
    assert _run(tree, "c", older, newer).verdict is CriterionVerdict.MET
    assert _run(tree, "c", _hf(HF.CLASS_II, "2025-01-01"), _hf(HF.CLASS_III, "2025-12-01")).verdict is (
        CriterionVerdict.NOT_MET
    )


def test_c_ignores_an_assessment_after_the_clock(tree):
    result = _run(tree, "c", _hf(HF.CLASS_IV, "2026-10-01"))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


def test_c_abstains_where_no_note_states_it(tree):
    result = _run(tree, "c")
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_a_c_not_met_citing_an_older_allowed_class_does_not_re_derive(tree):
    """Narrowed to what it cites, a `NOT_MET` that cited an older class II
    alongside the class III re-derives the same verdict over a different span
    set, and the check refuses it (D99)."""
    older, newer = _hf(HF.CLASS_II, "2025-01-01"), _hf(HF.CLASS_III, "2025-12-01")
    result = _run(tree, "c", older, newer)
    padded = result.model_copy(update={"spans": [older.span, newer.span]})
    with pytest.raises(CitationInsufficient):
        _rederive(tree, padded, older, newer)


# --------------------------------------------------------------------------
# d: tuberculosis
# --------------------------------------------------------------------------


def test_a_negative_screen_is_met(tree):
    assert _run(tree, "d", _screen(TB.NEGATIVE, "2026-01-08")).verdict is CriterionVerdict.MET


def test_a_treated_positive_screen_is_met_citing_both(tree):
    screen, treatment = _screen(TB.POSITIVE, "2025-06-11"), _treatment("2025-07-11")
    result = _run(tree, "d", screen, treatment)
    assert result.verdict is CriterionVerdict.MET
    assert result.spans == [screen.span, treatment.span]


def test_an_untreated_positive_screen_is_not_met_and_re_derives(tree):
    screen = _screen(TB.POSITIVE, "2025-12-09")
    result = _run(tree, "d", screen)
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.shortfall.unit == "tuberculosis_treatments_documented"
    _rederive(tree, result, screen)


def test_a_treatment_begun_after_the_clock_is_not_a_treatment_by_it(tree):
    result = _run(tree, "d", _screen(TB.POSITIVE, "2025-12-09"), _treatment("2026-10-01"))
    assert result.verdict is CriterionVerdict.NOT_MET


def test_d_abstains_with_no_screen(tree):
    assert _run(tree, "d", _treatment("2025-07-11")).verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------
# e: disease activity
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level,verdict",
    [
        (LEVEL.HIGH, CriterionVerdict.MET),
        (LEVEL.MODERATE, CriterionVerdict.MET),
        (LEVEL.LOW, CriterionVerdict.NOT_MET),
        (LEVEL.REMISSION, CriterionVerdict.NOT_MET),
    ],
)
def test_e_reads_the_stated_level_against_the_qualifying_ones(tree, level, verdict):
    fact = _activity(level, "2025-12-09")
    result = _run(tree, "e", fact)
    assert result.verdict is verdict
    if verdict is CriterionVerdict.NOT_MET:
        _rederive(tree, result, fact)


def test_e_takes_the_latest_stated_level(tree):
    assert _run(tree, "e", _activity(LEVEL.HIGH, "2025-01-01"), _activity(LEVEL.LOW, "2025-12-01")).verdict is (
        CriterionVerdict.NOT_MET
    )


def test_e_abstains_where_no_level_is_stated(tree):
    result = _run(tree, "e")
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


# --------------------------------------------------------------------------
# The trust boundary and the recordings
# --------------------------------------------------------------------------


NOTE = (
    "12/09/2025 - Disease activity: CDAI 8.0, low disease activity.\n"
    "12/09/2025 - Interferon-gamma release assay drawn; result positive.\n"
)


def test_the_builder_anchors_every_quote_and_drops_one_it_cannot_find():
    payload = {
        "disease_activity_assessments": [
            {"level": "low", "date": "2025-12-09",
             "quote": "CDAI 8.0, low disease activity", "char_start": 0, "char_end": 0},
        ],
        "tuberculosis_screens": [
            {"result": "positive", "date": "2025-12-09",
             "quote": "QuantiFERON positive", "char_start": 0, "char_end": 0},
        ],
    }
    result = build_rheumatoid_workup_result("n", NOTE, payload)
    assert [type(f).__name__ for f in result.facts] == ["DiseaseActivityAssessment"]
    assert [d["path"] for d in result.dropped] == ["tuberculosis_screens[0].quote"]
    assert result.events == [] and result.assertions == []


@pytest.mark.parametrize("name", ["rheumatoid_arthritis_workup.json", "rheumatoid_arthritis_workup_vertex.json"])
def test_each_recording_read_every_labelled_fact_and_no_trap(name):
    recording = json.loads((EXTRACTION / name).read_text(encoding="utf-8"))
    assert recording["prompt_version"] == RHEUMATOID_PROMPT_VERSION
    assert (recording["task"], recording["decision"]) == ("T-110", "D155")
    assert {n["note_id"].split("+")[0] for n in recording["notes"]} == {"RA4", "RA5", "RA6"}
    for note in recording["notes"]:
        score = note["score"]
        for collection in (
            "heart_failure_assessments", "tuberculosis_screens",
            "tuberculosis_treatments", "disease_activity_assessments",
        ):
            block = score[collection]
            assert block["matched"] == block["labeled"] == block["extracted"], (
                note["note_id"], collection, block,
            )
        # The deferred treatment and the unscored examination were read as
        # neither a treatment nor a level.
        assert score["traps_extracted"] == [], note["note_id"]
        assert score["spans_anchored"] == score["spans_emitted"]
