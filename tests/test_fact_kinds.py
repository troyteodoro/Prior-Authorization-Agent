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

Since T-108 (REQ-79, D150) there are two kinds, and the claims extend to the
second: its digest is pinned beside its version, its two recordings replay
under it and under nothing else, a note can hold one payload per kind, and
the trust boundary is the same one — every kind anchors through
`_anchor_or_drop`, folds through `workflow.FACT_FOLDS` (a partition of
`FactKind`), and no kind but `weight_management` writes a `WmEvent`-shaped
field, which is held by parsing because no behavioural test on this corpus
can tell a builder that does from one that does not.
"""

from __future__ import annotations

import ast
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
    KNEE_INSTRUCTION,
    KNEE_PROMPT_VERSION,
    PROMPT_VERSION,
    SLEEP_INSTRUCTION,
    SLEEP_PROMPT_VERSION,
    Extraction,
    KneeOsteoarthritisWorkupExtraction,
    SleepApneaWorkupExtraction,
    _locate,
    _locate_knee,
    _locate_sleep,
    build_knee_result,
    build_result,
    build_sleep_result,
)
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    RecordedExtractionRunner,
)
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.workflow import ERROR_CODE_FOR, FACT_FOLDS

from conftest import AcceptAllVerifier

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICIES = REPO_ROOT / "data" / "policies"
EXTRACTION = REPO_ROOT / "eval" / "extraction"
SPIKE_NOTES = REPO_ROOT / "spike" / "spike_001" / "notes"
CASES = REPO_ROOT / "eval" / "cases.json"

WM = FactKind.WEIGHT_MANAGEMENT
SLEEP = FactKind.SLEEP_APNEA_WORKUP
KNEE = FactKind.KNEE_OSTEOARTHRITIS_WORKUP

#: What each committed tree declares. A literal, so a tree that drops or gains a
#: kind is a visible diff here as well as a load-time check (D51's move).
EXPECTED_FACT_KINDS = {
    "ncd_100_1_jf.json": (WM,),
    "ncd_100_1_jjm.json": (WM,),
    "infliximab_ra_jjm.json": (),
    "us_abdominal_visceral_j5_j8.json": (),
    "pap_osa_dme_jd.json": (SLEEP,),
    "hyaluronan_knee_oa_j5_j8.json": (KNEE,),
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

#: The sleep workup's digest and version (T-108, D150), pinned for the same
#: reason as the weight-management pair above: a failure here is a new version
#: and a new measurement of both sleep recordings, never a new literal.
SLEEP_APNEA_WORKUP_DIGEST = (
    "e32b1431855c479fcdb83a4d5ec9a74c2712d358045022fae781c96dace65cdc"
)
SLEEP_APNEA_WORKUP_VERSION = "t108-sleep-workup-v1/t89-reask-v1"

#: The knee workup's digest and version (T-109, D154), pinned for the same
#: reason: a failure here is a new version and a new measurement of both knee
#: recordings, never a new literal.
KNEE_OSTEOARTHRITIS_WORKUP_DIGEST = (
    "265d703dd962af4587abe2f0013944bf3205b26ba7b85ebab1b9a16235dfbba6"
)
KNEE_OSTEOARTHRITIS_WORKUP_VERSION = "t109-knee-oa-workup-v1/t89-reask-v1"

#: One recording per tier per runner for `weight_management` (T-81, T-90), and
#: one per tier for `sleep_apnea_workup` on the direct runner (T-108, D150).
SLEEP_RECORDINGS = sorted(EXTRACTION.glob("sleep_apnea_workup*.json"))
#: And one per tier for `knee_osteoarthritis_workup` (T-109, D154).
KNEE_RECORDINGS = sorted(EXTRACTION.glob("knee_osteoarthritis_workup*.json"))
RECORDINGS = sorted(
    p for p in EXTRACTION.glob("*.json")
    if p not in SLEEP_RECORDINGS and p not in KNEE_RECORDINGS
)


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


def test_there_are_two_sleep_recordings_one_per_tier():
    assert [p.name for p in SLEEP_RECORDINGS] == [
        "sleep_apnea_workup.json",
        "sleep_apnea_workup_vertex.json",
    ]
    tiers = {json.loads(p.read_text(encoding="utf-8"))["tier"] for p in SLEEP_RECORDINGS}
    assert tiers == {"ai_studio", "vertex"}


@pytest.mark.parametrize("path", SLEEP_RECORDINGS, ids=lambda p: p.name)
def test_every_sleep_recording_replays_under_its_kind_and_no_other(path):
    """The replay key in both directions: every sleep note rebuilds under the
    sleep kind, and asking it for weight-management facts is
    `SCHEMA_MISMATCH` — the request D147 was written to refuse."""
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["prompt_version"] == SLEEP_APNEA_WORKUP_VERSION
    runner = RecordedExtractionRunner.from_records(recording["notes"])
    store = LocalPatientStore()
    for record in recording["notes"]:
        text = store.get_document(record["document_id"]).text
        result = runner.run(record["document_id"], text, SLEEP)
        assert result.kind is SLEEP and result.facts
        assert not result.events and not result.assertions
        with pytest.raises(ExtractionOutputError) as caught:
            runner.run(record["document_id"], text, WM)
        assert caught.value.reason is ExtractionFailure.SCHEMA_MISMATCH
    assert len(recording["notes"]) == 12


def test_one_note_holds_one_payload_per_kind():
    """A note read under two kinds is two measurements, and the replay holds
    both: each request gets its own kind's payload (D150 clause 8)."""
    wm = next(r for r in _results_records() if r["corpus"] == "synthesized")
    sleep = copy.deepcopy(
        json.loads((EXTRACTION / "sleep_apnea_workup.json").read_text(encoding="utf-8"))[
            "notes"
        ][0]
    )
    # The sleep payload, re-addressed onto the weight-management note's bytes.
    sleep["document_id"] = wm["document_id"]
    sleep["note_sha256"] = wm["note_sha256"]
    sleep["raw"] = {"clinical_evaluations": [], "sleep_tests": [], "findings": []}
    runner = RecordedExtractionRunner.from_records([wm, sleep])
    text = _text(wm, LocalPatientStore())
    assert runner.run(wm["document_id"], text, WM).kind is WM
    assert runner.run(wm["document_id"], text, SLEEP).kind is SLEEP
    with pytest.raises(ValueError):
        RecordedExtractionRunner.from_records([wm, copy.deepcopy(wm)])


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


# --------------------------------------------------------------------------
# REQ-79: the trust boundary is generic over declared kinds (T-108, D150)
# --------------------------------------------------------------------------


def test_the_sleep_schema_is_its_registered_configuration_and_its_digest_is_pinned():
    schema = FACT_SCHEMAS[SLEEP]
    assert schema.response_model is SleepApneaWorkupExtraction
    assert schema.instruction is SLEEP_INSTRUCTION
    assert schema.build is build_sleep_result
    assert schema.locate is _locate_sleep
    assert schema.prompt_version == SLEEP_PROMPT_VERSION == SLEEP_APNEA_WORKUP_VERSION
    assert schema.digest == SLEEP_APNEA_WORKUP_DIGEST
    assert FACT_SCHEMAS[SLEEP].digest != FACT_SCHEMAS[WM].digest


def test_the_knee_schema_is_its_registered_configuration_and_its_digest_is_pinned():
    schema = FACT_SCHEMAS[KNEE]
    assert schema.response_model is KneeOsteoarthritisWorkupExtraction
    assert schema.instruction is KNEE_INSTRUCTION
    assert schema.build is build_knee_result
    assert schema.locate is _locate_knee
    assert schema.prompt_version == KNEE_PROMPT_VERSION == KNEE_OSTEOARTHRITIS_WORKUP_VERSION
    assert schema.digest == KNEE_OSTEOARTHRITIS_WORKUP_DIGEST
    assert len({FACT_SCHEMAS[k].digest for k in FactKind}) == len(FactKind)


def test_there_are_two_knee_recordings_one_per_tier():
    assert [p.name for p in KNEE_RECORDINGS] == [
        "knee_osteoarthritis_workup.json",
        "knee_osteoarthritis_workup_vertex.json",
    ]
    tiers = {json.loads(p.read_text(encoding="utf-8"))["tier"] for p in KNEE_RECORDINGS}
    assert tiers == {"ai_studio", "vertex"}


@pytest.mark.parametrize("path", KNEE_RECORDINGS, ids=lambda p: p.name)
def test_every_knee_recording_replays_under_its_kind_and_no_other(path):
    """The sleep test's shape on the third kind: every knee note rebuilds
    under the knee kind and refuses both others as `SCHEMA_MISMATCH` (D154)."""
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["prompt_version"] == KNEE_OSTEOARTHRITIS_WORKUP_VERSION
    runner = RecordedExtractionRunner.from_records(recording["notes"])
    store = LocalPatientStore()
    for record in recording["notes"]:
        text = store.get_document(record["document_id"]).text
        result = runner.run(record["document_id"], text, KNEE)
        assert result.kind is KNEE
        assert not result.events and not result.assertions
        for other in (WM, SLEEP):
            with pytest.raises(ExtractionOutputError) as caught:
                runner.run(record["document_id"], text, other)
            assert caught.value.reason is ExtractionFailure.SCHEMA_MISMATCH
    assert len(recording["notes"]) == 14


def test_every_kind_folds_through_the_registry():
    """`FACT_FOLDS` partitions `FactKind`: a kind with no fold would extract
    every note and keep nothing, and the step names no kind of its own."""
    assert set(FACT_FOLDS) == set(FactKind)
    tree = ast.parse((REPO_ROOT / "pa_agent" / "workflow.py").read_text(encoding="utf-8"))
    step = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "step_extract"
    )
    named = {
        n.attr for n in ast.walk(step)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
        and n.value.id == "FactKind"
    }
    assert named == set(), f"step_extract names fact kinds {named}; it folds through FACT_FOLDS"
    calls = [
        ast.unparse(n.func) for n in ast.walk(step) if isinstance(n, ast.Call)
    ]
    assert "FACT_FOLDS[kind]" in calls


_WM_FIELDS = {"events", "assertions", "current_bmi", "current_bmi_span"}


def _builder(name: str) -> ast.FunctionDef:
    tree = ast.parse((REPO_ROOT / "pa_agent" / "extraction.py").read_text(encoding="utf-8"))
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_no_kind_but_weight_management_reaches_a_wmevent_shaped_route():
    """Parsed, because the committed sleep corpus cannot tell a builder that
    writes `result.events` from one that does not: a sleep payload never
    carries a `WmEvent`'s fields to write. Every registered builder other than
    `build_result` constructs no `WmEvent` or `ProgramAssertion` and touches no
    weight-management field of the result (REQ-79)."""
    builders = {
        schema.build.__name__ for kind, schema in FACT_SCHEMAS.items() if kind is not WM
    }
    assert builders == {"build_sleep_result", "build_knee_result"}
    for name in builders:
        node = _builder(name)
        constructed = {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert not constructed & {"WmEvent", "ProgramAssertion", "NoteBmi"}, name
        touched = {
            n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr in _WM_FIELDS
        }
        assert not touched, f"{name} touches {touched}"


def test_every_builder_anchors_through_the_one_anchorer():
    """The trust boundary is one function, called by every kind's builder:
    a builder that sliced the model's offsets or searched the note itself
    would be a second, unvalidated route (Art. III, D18)."""
    for schema in FACT_SCHEMAS.values():
        node = _builder(schema.build.__name__)
        called = {
            n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_anchor_or_drop" in called, schema.build.__name__
        assert "anchor" not in called, schema.build.__name__


def test_a_sleep_quote_the_note_does_not_contain_is_dropped_with_its_path():
    """Through the real anchorer, on a note written here: an unanchorable
    index phrase demotes the index to unstated rather than keeping a number
    without a span, and the drop names the payload path the re-ask needs."""
    note = (
        "SLEEP STUDY REPORT\nStudy date: 03/16/2026. Home sleep test.\n"
        "Total recording time: 6.5 hours.\nRDI: 8 events per hour.\n"
    )
    payload = {
        "clinical_evaluations": [],
        "sleep_tests": [{
            "date": "2026-03-16",
            "quote": "Study date: 03/16/2026. Home sleep test.",
            "char_start": 0, "char_end": 0,
            "index": 8.0, "index_quote": "RDI of eight per hour",
            "recording_hours": 6.5, "recording_hours_quote": "6.5 hours",
        }],
        "findings": [{
            "category": "insomnia", "quote": "Reports insomnia.",
            "char_start": 0, "char_end": 0,
        }],
    }
    result = build_sleep_result("doc", note, payload)
    (test,) = result.facts
    assert test.index is None and test.index_span is None
    assert test.recording_hours == 6.5 and test.hours_span is not None
    assert {d["path"] for d in result.dropped} == {
        "sleep_tests[0].index_quote",
        "findings[0].quote",
    }
    assert _locate_sleep(payload, "sleep_tests[0].index_quote") == (
        payload["sleep_tests"][0], "index_quote"
    )
    with pytest.raises(KeyError):
        _locate_sleep(payload, "sleep_tests[0].date")
    with pytest.raises(KeyError):
        _locate_sleep(payload, "findings[0].index_quote")
