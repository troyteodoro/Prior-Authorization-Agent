"""T-107 — a tree declares the fact kinds it extracts (REQ-78, D147, D149).

Four claims, in the order they can break:

- **The declaration.** Every committed tree states `fact_kinds`, and the
  statement equals what its deterministic criteria and reconciled facts
  consume, in both directions. Each way that can go wrong is refused at load:
  a missing field, an unknown kind, a duplicate, a kind nothing consumes (D113,
  reading every note for nothing), a consumed kind nobody declared, and a
  reconciled fact naming a note source no kind produces.
- **The registry.** Every `FactKind` has a `FactSchema`, and the one that
  exists is today's extraction configuration, unchanged. Its digest is pinned
  beside its prompt version, so the prompt cannot move while the version
  stays the same.
- **The replay key.** All six extraction recordings replay under
  `weight_management`. A payload asked for under a version it was not measured
  with is `SCHEMA_MISMATCH`, which is terminal, and a determination over it is
  an `ERROR` on every note criterion, never an abstention (D90). A payload
  with no recorded version is not replayable.
- **Reads no note** is proved in `test_rheumatology_corpus.py`, on the one
  committed input that can prove it: a note-bearing chart under a tree that
  declares no kind, with a runner that raises.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    NOTE_SOURCE_FACT_KIND,
    PREDICATE_FACT_KIND,
    CriteriaTree,
    CriterionVerdict,
    DeterminationAborted,
    ErrorCode,
    FactKind,
)
from pa_agent.determination import determine
from pa_agent.extraction import (
    FACT_SCHEMAS,
    INSTRUCTION,
    PROMPT_VERSION,
    Extraction,
    _locate,
    build_result,
)
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    RecordedExtractionRunner,
)
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.workflow import ERROR_CODE_FOR

from conftest import AcceptAllVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICIES = REPO_ROOT / "data" / "policies"
EXTRACTION = REPO_ROOT / "eval" / "extraction"
SPIKE_NOTES = REPO_ROOT / "spike" / "spike_001" / "notes"
CASES = REPO_ROOT / "eval" / "cases.json"

WM = FactKind.WEIGHT_MANAGEMENT

#: What each committed tree declares. A literal, so a tree that drops or gains a
#: kind is a visible diff here as well as a load-time check (D51's move).
EXPECTED_FACT_KINDS = {
    "ncd_100_1_jf.json": (WM,),
    "ncd_100_1_jjm.json": (WM,),
    "infliximab_ra_jjm.json": (),
    "us_abdominal_visceral_j5_j8.json": (),
}

#: `FACT_SCHEMAS[weight_management].digest` — the schema, the instruction, the
#: re-ask instruction and the prompt version hashed together. **If this fails,
#: the prompt changed.** That is a new `PROMPT_VERSION` and a new measurement
#: of every extraction recording (D45), never an update to this literal alone:
#: a recording is keyed by its version (D149), so a prompt that moved under an
#: unchanged version would replay as though it had been measured.
WEIGHT_MANAGEMENT_DIGEST = (
    "519641a2e6a5a68c1293bdde05cb7d61bc2cef86fd880f4b694cce490a353389"
)
WEIGHT_MANAGEMENT_VERSION = "t15-instruction-v1/t89-reask-v1"

RECORDINGS = sorted(EXTRACTION.glob("*.json"))


def _raw(filename: str) -> dict:
    return json.loads((POLICIES / filename).read_text(encoding="utf-8"))


def _unclaim(criterion: dict) -> None:
    """An unclaimed criterion declares no kind, and no scope onto a criterion
    nobody evaluates — the contract refuses both (REQ-58, D110)."""
    criterion["evaluation"] = "unclaimed"
    criterion["note"] = "unclaimed for this test"
    for key in ("kind", "scoped_to"):
        criterion.pop(key, None)


# --------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------


def test_every_committed_tree_declares_what_it_consumes():
    assert {p.name for p in POLICIES.glob("*.json")} == set(EXPECTED_FACT_KINDS)
    for filename, expected in EXPECTED_FACT_KINDS.items():
        tree = CriteriaTree.model_validate(_raw(filename))
        assert tree.fact_kinds == expected, filename
        consumed = {
            PREDICATE_FACT_KIND[c.kind]
            for c in tree.criteria
            if c.evaluation == "deterministic" and c.kind in PREDICATE_FACT_KIND
        } | {NOTE_SOURCE_FACT_KIND[r.note_source] for r in tree.reconciled_facts}
        assert set(tree.fact_kinds) == consumed, filename


def test_a_tree_that_omits_the_declaration_does_not_load():
    raw = _raw("ncd_100_1_jf.json")
    del raw["fact_kinds"]
    with pytest.raises(ValidationError, match="fact_kinds"):
        CriteriaTree.model_validate(raw)


def test_an_unknown_fact_kind_does_not_load():
    raw = _raw("ncd_100_1_jf.json")
    raw["fact_kinds"] = ["weight_management", "sleep_study"]
    with pytest.raises(ValidationError, match="fact_kinds"):
        CriteriaTree.model_validate(raw)


def test_a_kind_declared_twice_does_not_load():
    raw = _raw("ncd_100_1_jf.json")
    raw["fact_kinds"] = ["weight_management", "weight_management"]
    with pytest.raises(ValidationError, match="twice"):
        CriteriaTree.model_validate(raw)


def test_a_kind_nothing_consumes_does_not_load():
    """D113's case: the rheumatology tree reading every note for nothing."""
    raw = _raw("infliximab_ra_jjm.json")
    raw["fact_kinds"] = ["weight_management"]
    with pytest.raises(ValidationError, match="no criterion or reconciled fact consumes"):
        CriteriaTree.model_validate(raw)


def test_a_consumed_kind_nobody_declared_does_not_load():
    raw = _raw("ncd_100_1_jf.json")
    raw["fact_kinds"] = []
    with pytest.raises(ValidationError, match="it does not declare"):
        CriteriaTree.model_validate(raw)


def test_a_reconciled_fact_alone_requires_its_kind():
    """With every note criterion unclaimed, the BMI reconciliation still reads
    the note — so the kind is still consumed, and dropping it is refused."""
    raw = _raw("ncd_100_1_jf.json")
    for criterion in raw["criteria"]:
        if criterion.get("kind") in {k.value for k in PREDICATE_FACT_KIND}:
            _unclaim(criterion)
    raw["fact_kinds"] = []
    with pytest.raises(ValidationError, match="it does not declare"):
        CriteriaTree.model_validate(raw)


def test_an_unclaimed_criterion_consumes_nothing():
    """The same tree with its reconciliation removed too consumes nothing, so
    it loads declaring nothing: an unclaimed criterion declares no kind."""
    raw = _raw("ncd_100_1_jf.json")
    for criterion in raw["criteria"]:
        if criterion.get("kind") in {k.value for k in PREDICATE_FACT_KIND}:
            _unclaim(criterion)
    raw["reconciled_facts"] = []
    raw["fact_kinds"] = []
    assert CriteriaTree.model_validate(raw).fact_kinds == ()


def test_an_unknown_note_source_does_not_load():
    raw = _raw("ncd_100_1_jf.json")
    raw["reconciled_facts"][0]["note_source"] = "wm_event.weight"
    with pytest.raises(ValidationError, match="no fact kind produces"):
        CriteriaTree.model_validate(raw)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------


def test_every_fact_kind_has_a_schema_and_every_map_lands_in_the_registry():
    assert set(FACT_SCHEMAS) == set(FactKind)
    assert set(PREDICATE_FACT_KIND.values()) <= set(FACT_SCHEMAS)
    assert set(NOTE_SOURCE_FACT_KIND.values()) <= set(FACT_SCHEMAS)
    for kind, schema in FACT_SCHEMAS.items():
        assert schema.kind is kind


def test_the_weight_management_schema_is_the_measured_configuration():
    """Every extraction recording was measured under these five objects. A
    registry entry that pointed anywhere else would replay them under a schema
    they never saw."""
    schema = FACT_SCHEMAS[WM]
    assert schema.response_model is Extraction
    assert schema.instruction is INSTRUCTION
    assert schema.build is build_result
    assert schema.locate is _locate
    assert schema.prompt_version == PROMPT_VERSION == WEIGHT_MANAGEMENT_VERSION


def test_the_weight_management_digest_is_pinned_to_its_version():
    """See `WEIGHT_MANAGEMENT_DIGEST`: a failure here means a new version and a
    new measurement, not a new literal."""
    assert FACT_SCHEMAS[WM].digest == WEIGHT_MANAGEMENT_DIGEST
    assert FACT_SCHEMAS[WM].prompt_version == WEIGHT_MANAGEMENT_VERSION


# --------------------------------------------------------------------------
# The replay key
# --------------------------------------------------------------------------


def _text(record: dict, store: LocalPatientStore) -> str:
    if record["corpus"] == "spike_001":
        return (SPIKE_NOTES / f"{record['document_id']}.txt").read_text(encoding="utf-8")
    return store.get_document(record["document_id"]).text


def test_there_are_six_extraction_recordings():
    assert len(RECORDINGS) == 6, [p.name for p in RECORDINGS]


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.name)
def test_every_recording_replays_under_weight_management(path):
    recording = json.loads(path.read_text(encoding="utf-8"))
    runner = RecordedExtractionRunner.from_records(recording["notes"])
    store = LocalPatientStore()
    replayed = 0
    for record in recording["notes"]:
        if record.get("raw") is None:
            continue
        result = runner.run(record["document_id"], _text(record, store), WM)
        assert result.document_id == record["document_id"]
        replayed += 1
    assert replayed >= 12, path.name


def _results_records() -> list[dict]:
    return json.loads((EXTRACTION / "results.json").read_text(encoding="utf-8"))["notes"]


def _with_version(records: list[dict], version: str) -> list[dict]:
    moved = copy.deepcopy(records)
    for record in moved:
        if record.get("trace"):
            record["trace"]["prompt_version"] = version
    return moved


def test_a_payload_measured_under_another_version_is_refused_before_it_is_built():
    records = _with_version(_results_records(), "t15-instruction-v2/t89-reask-v1")
    record = next(r for r in records if r["corpus"] == "synthesized")
    runner = RecordedExtractionRunner.from_records(records)
    with pytest.raises(ExtractionOutputError) as caught:
        runner.run(record["document_id"], _text(record, LocalPatientStore()), WM)
    assert caught.value.reason is ExtractionFailure.SCHEMA_MISMATCH


def test_a_payload_with_no_recorded_version_is_not_replayable():
    records = copy.deepcopy(_results_records())
    record = next(r for r in records if r["corpus"] == "synthesized")
    record["trace"] = None
    record.pop("prompt_version", None)
    runner = RecordedExtractionRunner.from_records([record])
    with pytest.raises(ExtractionOutputError) as caught:
        runner.run(record["document_id"], _text(record, LocalPatientStore()), WM)
    assert caught.value.reason is ExtractionFailure.NOT_RECORDED


def test_a_schema_mismatch_is_terminal():
    code = ERROR_CODE_FOR[ExtractionFailure.SCHEMA_MISMATCH]
    assert code is ErrorCode.SCHEMA_INVALID
    assert not code.retryable


def test_a_determination_over_a_mismatched_recording_is_an_error_never_an_abstention():
    """D90's rule at the replay key: "the recording does not answer this
    request" is a fault, and reporting it as an abstention would say the chart
    is silent about a program the note documents."""
    case = next(
        c for c in json.loads(CASES.read_text(encoding="utf-8"))["cases"]
        if c["case_id"] == "E1"
    )
    records = _with_version(_results_records(), "t15-instruction-v2/t89-reask-v1")
    with pytest.raises(DeterminationAborted) as caught:
        determine(
            LocalPolicyStore(),
            case["procedure_code"],
            patient_id=case["patient_id"],
            patient_store=LocalPatientStore(),
            as_of=date.fromisoformat(case.get("as_of", "2026-09-01")),
            extraction_runner=RecordedExtractionRunner.from_records(records),
            verifier=AcceptAllVerifier(),
        )
    results = caught.value.results
    assert results, "an abort must name the criteria it errored"
    assert {r.verdict for r in results} == {CriterionVerdict.ERROR}
    assert {r.error_code for r in results} == {ErrorCode.SCHEMA_INVALID}
    assert caught.value.attempts == 1
