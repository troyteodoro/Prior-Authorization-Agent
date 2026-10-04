"""T-110 — Palmetto's `c4` and `d`, read through the fourth fact kind (D155).

`ncd-100.1-jjm-v1` declared both criteria unclaimed from T-87 (D101) because
the weight-management kind reads a BMI and nothing read an evaluation. Since
T-110 the tree declares `bariatric_surgical_workup` beside
`weight_management`, and two predicate kinds read it:

- `note_weight_run_rate` (`c4`): a documented weight in every month of the
  qualifying run, `evaluate_c4`'s arithmetic on the field L34576 names;
- `note_multidisciplinary_evaluation` (`d`): all four components L34576 lists,
  each documented fewer than six whole months before the clock.

This file holds the arithmetic on charts written **here**, both directions of
each, every `NOT_MET` re-derived from its citations (D99), the kind's trust
boundary through the real anchorer, and its two recordings.
`tests/test_t110_corpus.py` holds the committed charts.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import (
    CriteriaTree,
    CriterionVerdict,
    DocumentedWeight,
    EvaluationComponent,
    EvaluationComponentCategory,
    EvidenceSpan,
    FactKind,
    GapReason,
    PredicateKind,
    WmEvent,
)
from pa_agent.criteria import (
    CitationInsufficient,
    NARROWERS,
    PredicateInputs,
    QualifyingRun,
    check_citation_sufficiency,
    evaluate,
)
from pa_agent.extraction import (
    BARIATRIC_PROMPT_VERSION,
    FACT_SCHEMAS,
    build_bariatric_workup_result,
)
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.workflow import NOTE_EVENT_KINDS, RUN_KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION = REPO_ROOT / "eval" / "extraction"
TREE_ID = "ncd-100.1-jjm-v1"
AS_OF = date(2026, 9, 1)
KIND = FactKind.BARIATRIC_SURGICAL_WORKUP
C = EvaluationComponentCategory

#: The kind's digest beside its version (D149). **If this fails, the prompt
#: changed**: a new version and a new measurement of both recordings, never a
#: new literal.
BARIATRIC_SURGICAL_WORKUP_DIGEST = (
    "847c85fb6eb75baeed78bbf33d463a02d9751803f384f07002aad540aa65ca60"
)
BARIATRIC_SURGICAL_WORKUP_VERSION = "t110-bariatric-surgical-workup-v1/t89-reask-v1"


@pytest.fixture(scope="module")
def tree() -> CriteriaTree:
    return LocalPolicyStore().get_tree(TREE_ID)


_counter = iter(range(10_000))


def _span(name: str = "note") -> EvidenceSpan:
    start = next(_counter) * 10
    return EvidenceSpan(document_id=f"note/{name}", char_start=start, char_end=start + 5, quote=None)


def _event(when: str) -> WmEvent:
    return WmEvent(event_date=date.fromisoformat(when), span=_span("event"))


def _run_of(*dates: str) -> QualifyingRun:
    events = tuple(_event(d) for d in dates)
    months = tuple(sorted({(e.event_date.year, e.event_date.month) for e in events}))
    return QualifyingRun(months=months, events=events)


def _weight(when: str) -> DocumentedWeight:
    return DocumentedWeight(weight_date=date.fromisoformat(when), span=_span("weight"))


def _component(category: EvaluationComponentCategory, when: str) -> EvaluationComponent:
    return EvaluationComponent(
        category=category, evaluation_date=date.fromisoformat(when), span=_span("eval")
    )


def _inputs(run: QualifyingRun, *facts, established: bool = True) -> PredicateInputs:
    return PredicateInputs(
        as_of=AS_OF, events=run.events, run=run, run_established=established,
        facts={KIND: tuple(facts)},
    )


def _rederive(tree, result, run, *facts) -> None:
    check_citation_sufficiency(
        tree.criterion(result.criterion_id), result,
        observations=[], procedures=[], value_sets={}, run=run, as_of=AS_OF,
        c3_met=True, facts={KIND: tuple(facts)},
    )


ALL_FOUR_IN_WINDOW = (
    _component(C.PRIMARY_CARE_REFERRAL, "2026-03-24"),
    _component(C.BARIATRIC_SURGEON, "2026-04-28"),
    _component(C.MENTAL_HEALTH, "2026-05-26"),
    _component(C.NUTRITION, "2026-06-23"),
)


# --------------------------------------------------------------------------
# The tree
# --------------------------------------------------------------------------


def test_the_tree_declares_both_kinds_and_claims_c4_and_d(tree):
    assert tree.fact_kinds == (FactKind.WEIGHT_MANAGEMENT, KIND)
    assert tree.criterion("c4").evaluation == "deterministic"
    assert tree.criterion("c4").kind is PredicateKind.NOTE_WEIGHT_RUN_RATE
    assert tree.criterion("d").evaluation == "deterministic"
    assert tree.criterion("d").kind is PredicateKind.NOTE_MULTIDISCIPLINARY_EVALUATION
    assert [c.id for c in tree.criteria if c.evaluation == "unclaimed"] == []


def test_d_requires_the_four_components_l34576_lists_and_its_window(tree):
    d = tree.criterion("d")
    assert d.require("required_components") == [c.value for c in C]
    assert d.require("evaluation_window_months") == 6
    source = d.constants["required_components"].source
    text = (REPO_ROOT / "data" / "policies" / "source" / "l34576.txt").read_text(encoding="utf-8")
    assert text[source.char_start:source.char_end] == source.quote
    assert "ALL of the following" in source.quote


def test_the_weight_rate_is_a_run_kind():
    """It is scoped to the qualifying run, so the run is computed whenever a
    tree declares it -- a kind missing from `RUN_KINDS` would read a run no
    step had selected."""
    assert PredicateKind.NOTE_WEIGHT_RUN_RATE in RUN_KINDS
    assert PredicateKind.NOTE_WEIGHT_RUN_RATE in NOTE_EVENT_KINDS


def test_the_kind_is_registered_with_its_digest_pinned():
    schema = FACT_SCHEMAS[KIND]
    assert schema.build is build_bariatric_workup_result
    assert schema.prompt_version == BARIATRIC_PROMPT_VERSION == BARIATRIC_SURGICAL_WORKUP_VERSION
    assert schema.digest == BARIATRIC_SURGICAL_WORKUP_DIGEST


# --------------------------------------------------------------------------
# c4: a weight in every month of the run
# --------------------------------------------------------------------------


def test_a_weight_in_every_run_month_is_met(tree):
    run = _run_of("2026-03-10", "2026-04-14", "2026-05-12")
    weights = (_weight("2026-03-10"), _weight("2026-04-14"), _weight("2026-05-12"))
    result = evaluate(tree.criterion("c4"), _inputs(run, *weights))
    assert result.verdict is CriterionVerdict.MET
    assert set(result.spans) == {w.span for w in weights}


def test_a_weight_outside_the_run_does_not_count(tree):
    run = _run_of("2026-03-10", "2026-04-14")
    result = evaluate(
        tree.criterion("c4"), _inputs(run, _weight("2026-03-10"), _weight("2026-06-01"))
    )
    assert result.verdict is CriterionVerdict.NOT_MET
    assert (result.shortfall.observed, result.shortfall.unit) == (1, "months_without_weight")


def test_a_month_without_a_weight_is_not_met_and_re_derives(tree):
    run = _run_of("2026-03-10", "2026-04-14", "2026-05-12")
    weights = (_weight("2026-03-10"), _weight("2026-05-12"))
    result = evaluate(tree.criterion("c4"), _inputs(run, *weights))
    assert result.verdict is CriterionVerdict.NOT_MET
    assert result.spans == [run.events_in((2026, 4))[0].span]
    _rederive(tree, result, run, *weights)


def test_a_c4_not_met_citing_a_weighed_month_does_not_re_derive(tree):
    """The narrower narrows the run and the weights together: citing April's
    encounter **and** May's, where May was weighed, re-derives one month
    short over two and must refuse the claimed shortfall."""
    run = _run_of("2026-03-10", "2026-04-14", "2026-05-12")
    weights = (_weight("2026-03-10"), _weight("2026-05-12"))
    result = evaluate(tree.criterion("c4"), _inputs(run, *weights))
    padded = result.model_copy(update={"spans": result.spans + [run.events_in((2026, 5))[0].span]})
    with pytest.raises(CitationInsufficient):
        _rederive(tree, padded, run, *weights)


def test_a_c4_not_met_citing_only_a_weighed_month_does_not_re_derive(tree):
    """The narrower narrows the run and keeps every weight: a `NOT_MET` that
    cites only May's encounter, where May was weighed, must re-derive `MET`
    over May and be refused. Narrowing the weights to the cited spans too --
    the obvious symmetric choice -- would drop May's weight and let the claim
    stand (T-110's mutation pass, D155)."""
    run = _run_of("2026-03-10", "2026-04-14", "2026-05-12")
    weights = (_weight("2026-03-10"), _weight("2026-05-12"))
    result = evaluate(tree.criterion("c4"), _inputs(run, *weights))
    wrong = result.model_copy(update={"spans": [run.events_in((2026, 5))[0].span]})
    with pytest.raises(CitationInsufficient):
        _rederive(tree, wrong, run, *weights)


def test_c4_abstains_with_no_run(tree):
    run = _run_of()
    result = evaluate(tree.criterion("c4"), _inputs(run, established=False))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED


def test_c4_has_a_narrower_over_the_run_and_the_facts():
    assert PredicateKind.NOTE_WEIGHT_RUN_RATE in NARROWERS
    assert PredicateKind.NOTE_MULTIDISCIPLINARY_EVALUATION in NARROWERS


# --------------------------------------------------------------------------
# d: four components, each inside six months
# --------------------------------------------------------------------------


def test_all_four_in_window_is_met_citing_each(tree):
    result = evaluate(tree.criterion("d"), _inputs(_run_of(), *ALL_FOUR_IN_WINDOW))
    assert result.verdict is CriterionVerdict.MET
    assert set(result.spans) == {c.span for c in ALL_FOUR_IN_WINDOW}


@pytest.mark.parametrize(
    "when,verdict",
    [
        ("2026-03-02", CriterionVerdict.MET),  # five whole months: inside
        ("2026-03-01", CriterionVerdict.NOT_MET),  # six: outside (c2's comparison)
    ],
)
def test_the_window_boundary_is_c2s_comparison(tree, when, verdict):
    components = ALL_FOUR_IN_WINDOW[1:] + (_component(C.PRIMARY_CARE_REFERRAL, when),)
    assert evaluate(tree.criterion("d"), _inputs(_run_of(), *components)).verdict is verdict


def test_a_stale_component_is_not_met_citing_all_four_and_re_derives(tree):
    components = ALL_FOUR_IN_WINDOW[:2] + (
        _component(C.MENTAL_HEALTH, "2025-11-04"),
        ALL_FOUR_IN_WINDOW[3],
    )
    result = evaluate(tree.criterion("d"), _inputs(_run_of(), *components))
    assert result.verdict is CriterionVerdict.NOT_MET
    assert (result.shortfall.observed, result.shortfall.required) == (9, 6)
    assert len(result.spans) == 4
    _rederive(tree, result, _run_of(), *components)


def test_a_later_documentation_of_a_stale_component_supersedes_it(tree):
    components = ALL_FOUR_IN_WINDOW + (_component(C.MENTAL_HEALTH, "2025-11-04"),)
    assert evaluate(tree.criterion("d"), _inputs(_run_of(), *components)).verdict is (
        CriterionVerdict.MET
    )


def test_an_undocumented_component_abstains_never_denies(tree):
    result = evaluate(tree.criterion("d"), _inputs(_run_of(), *ALL_FOUR_IN_WINDOW[:3]))
    assert result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    assert result.gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert "nutrition" in result.detail


def test_a_component_after_the_clock_is_not_documented_by_it(tree):
    components = ALL_FOUR_IN_WINDOW[:3] + (_component(C.NUTRITION, "2026-09-15"),)
    assert evaluate(tree.criterion("d"), _inputs(_run_of(), *components)).verdict is (
        CriterionVerdict.INSUFFICIENT_EVIDENCE
    )


# --------------------------------------------------------------------------
# The trust boundary, through the real anchorer
# --------------------------------------------------------------------------


NOTE = (
    "03/10/2026 - Program visit. Weight 112.1 kg, BMI 37.6.\n"
    "04/28/2026 - Seen by D. Okafor, MD, bariatric surgeon, who recommends\n"
    "laparoscopic sleeve gastrectomy.\n"
)


def test_the_builder_anchors_every_quote_and_drops_one_it_cannot_find():
    payload = {
        "weights": [
            {"date": "2026-03-10", "quote": "03/10/2026 - Program visit. Weight 112.1 kg",
             "char_start": 0, "char_end": 0},
            {"date": "2026-04-14", "quote": "04/14/2026 - Weight 110 kg",
             "char_start": 0, "char_end": 0},
        ],
        "evaluations": [
            {"category": "bariatric_surgeon", "date": "2026-04-28",
             "quote": "04/28/2026 - Seen by D. Okafor, MD, bariatric surgeon",
             "char_start": 0, "char_end": 0},
        ],
    }
    result = build_bariatric_workup_result("n", NOTE, payload)
    weights = [f for f in result.facts if isinstance(f, DocumentedWeight)]
    components = [f for f in result.facts if isinstance(f, EvaluationComponent)]
    assert [w.weight_date.isoformat() for w in weights] == ["2026-03-10"]
    assert [c.category for c in components] == [C.BARIATRIC_SURGEON]
    assert [d["path"] for d in result.dropped] == ["weights[1].quote"]
    assert result.events == [] and result.assertions == []
    for fact in result.facts:
        assert NOTE[fact.span.char_start:fact.span.char_end] == fact.span.quote


# --------------------------------------------------------------------------
# The recordings
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["bariatric_surgical_workup.json", "bariatric_surgical_workup_vertex.json"])
def test_each_recording_read_every_labelled_fact_and_nothing_else(name):
    recording = json.loads((EXTRACTION / name).read_text(encoding="utf-8"))
    assert recording["prompt_version"] == BARIATRIC_PROMPT_VERSION
    assert (recording["task"], recording["decision"]) == ("T-110", "D155")
    assert {n["note_id"].split("/")[0] for n in recording["notes"]} == {"J1", "J2", "J3"}
    for note in recording["notes"]:
        score = note["score"]
        for collection in ("weights", "evaluations"):
            block = score[collection]
            assert block["matched"] == block["labeled"] == block["extracted"], (
                note["note_id"], collection, block,
            )
        assert score["traps_extracted"] == [], note["note_id"]
        assert score["spans_anchored"] == score["spans_emitted"]
        assert note["trace"]["prompt_version"] == BARIATRIC_PROMPT_VERSION
