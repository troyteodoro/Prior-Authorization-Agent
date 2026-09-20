"""T-89 — the bounded verbatim re-ask (REQ-56, D103), spending no model call.

Spans are located by searching the model's verbatim quote (D18), so a
paraphrased quote is unanchorable and `build_result` drops the claim. T-89
re-asks once, for the verbatim text of every quote Python could not locate,
and anchors the answer exactly as it anchored the first. This file pins the
mechanism's shape rather than any measured number:

1. **Python decides, the anchorer admits.** The re-ask fires only when a
   quote did not anchor, exactly once, and the second answer is admitted by
   the anchorer or dropped — never by the model's say-so.
2. **The patch is narrow.** Only quote fields at the paths Python asked about
   change; an answer cannot add, remove or re-date a claim, and a blank
   answer is ignored because an empty quote would anchor at offset zero.
3. **A failed re-ask never raises.** The first turn's result stands and the
   trace says what happened.
4. **Every turn is counted**, on the live path and on the replay.

The direct client is a sequenced fake with the shape `google-genai` exposes —
`client.models.generate_content(...)` returning `.text` and `.usage_metadata`
— because the direct runner had no no-network test before this task, and the
re-ask lives on it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pa_agent.contracts import CallMetrics, Document, RunTrace
from pa_agent.extraction import (
    PROMPT_VERSION,
    REASK_INSTRUCTION,
    REASK_ROUNDS,
    Turn,
    VerbatimAnswers,
    apply_verbatim,
    build_result,
    extract,
    extract_with_reask,
    reask_targets,
)
from pa_agent.runners import (
    DirectExtractionRunner,
    ExtractionFailure,
    ExtractionOutputError,
    RecordedExtractionRunner,
)
from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"

# The loss on record (D71, D98): T-63's tool-fetch run wrote *completed* where
# E8's note says *completing*, with 62 verbatim characters after it.
E8_PARAPHRASE = "completed a six-month\nmedically supervised weight-loss program last year"
E8_VERBATIM = "completing a six-month\nmedically supervised weight-loss program last year"
E8_PATH = "program_assertions[0].quote"


# --------------------------------------------------------------------------
# A sequenced google-genai client. No network.
# --------------------------------------------------------------------------


class _SequencedClient:
    """`client.models.generate_content` answering from a list, in order.

    Each entry is the response text, or an exception instance to raise for
    that call. Every request is kept — model, contents, config — so a test can
    assert what the second turn was asked, not only what it answered.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.models = self  # the SDK's `client.models` namespace

    def generate_content(self, *, model, contents, config):
        index = len(self.requests)
        self.requests.append({"model": model, "contents": contents, "config": config})
        item = self._responses[index]
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(
            text=item,
            usage_metadata=SimpleNamespace(
                prompt_token_count=100 + index,
                candidates_token_count=10 + index,
                total_token_count=110 + 2 * index,
            ),
        )


def _answers(*pairs: tuple[str, str]) -> str:
    return json.dumps({"quotes": [{"path": p, "verbatim": v} for p, v in pairs]})


def _metric(purpose: str) -> CallMetrics:
    return CallMetrics(
        model="fake-under-test", purpose=purpose, input_tokens=1,
        output_tokens=1, wall_time_ms=1.0,
    )


@pytest.fixture(scope="module")
def recording() -> dict:
    assert EXTRACTION_RESULTS.exists()
    return json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))


def _note_for(case: str) -> Document:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    patient_id = next(r["patient_id"] for r in manifest["notes"] if case in r["cases"])
    return LocalPatientStore().get_notes(patient_id)[0]


@pytest.fixture(scope="module")
def e1_note() -> Document:
    return _note_for("E1")


@pytest.fixture(scope="module")
def e1_payload(recording, e1_note) -> dict:
    return next(
        r["raw"] for r in recording["notes"] if r["document_id"] == e1_note.document_id
    )


@pytest.fixture(scope="module")
def e8_note() -> Document:
    note = _note_for("E8")
    assert E8_VERBATIM in note.text, "the fixture depends on E8's exact bytes"
    assert E8_PARAPHRASE not in note.text
    return note


@pytest.fixture
def e8_paraphrased() -> dict:
    """What T-63's tool-fetch run returned for E8, in shape: no events, one
    assertion whose quote is one word off the note."""
    return {
        "wm_events": [],
        "program_assertions": [
            {
                "quote": E8_PARAPHRASE,
                "char_start": 105,
                "char_end": 182,
                "claim": "completed six-month medically supervised program last year",
            }
        ],
    }


# --------------------------------------------------------------------------
# 1. Python decides; the anchorer admits
# --------------------------------------------------------------------------


def test_a_note_with_no_unanchorable_quote_makes_one_call(e1_note, e1_payload) -> None:
    client = _SequencedClient([json.dumps(e1_payload)])
    result = extract(e1_note.document_id, e1_note.text, client)

    assert len(client.requests) == 1
    assert result.reask is None
    assert result.raw_first_turn is None
    assert result.trace is not None
    assert result.trace.steps == ["extract"]
    assert len(result.trace.metrics) == 1
    assert result.metrics is result.trace.metrics[0]
    assert result.trace.prompt_version == PROMPT_VERSION


def test_an_unanchorable_quote_is_re_asked_with_the_note_and_the_failed_quote(
    e8_note, e8_paraphrased
) -> None:
    """The second request is the re-ask instruction, the note, and the quotes
    the anchorer refused, under their paths — and it asks for text, not for
    offsets (D17, D19) and not for a shorter phrase (D18, D98)."""
    client = _SequencedClient([json.dumps(e8_paraphrased), _answers((E8_PATH, E8_VERBATIM))])
    extract(e8_note.document_id, e8_note.text, client)

    assert len(client.requests) == 2
    second = client.requests[1]
    assert second["contents"].startswith(REASK_INSTRUCTION)
    assert e8_note.text in second["contents"]
    assert E8_PATH in second["contents"]
    assert json.dumps(E8_PARAPHRASE, ensure_ascii=False) in second["contents"]
    assert second["config"]["response_schema"] is VerbatimAnswers
    assert second["config"]["temperature"] == 0.0
    assert "char_start" not in REASK_INSTRUCTION and "offset" not in REASK_INSTRUCTION


def test_a_verbatim_answer_recovers_the_claim(e8_note, e8_paraphrased) -> None:
    """P2's loss, recovered: the assertion anchors, the audit names the path,
    the patched payload replays to the same result, and both turns are on the
    trace while `metrics` stays turn one (Art. X, D71)."""
    client = _SequencedClient([json.dumps(e8_paraphrased), _answers((E8_PATH, E8_VERBATIM))])
    result = extract(e8_note.document_id, e8_note.text, client)

    assert len(result.assertions) == 1
    assert result.dropped == []
    assert e8_note.text[result.assertions[0].span.char_start:result.assertions[0].span.char_end] == E8_VERBATIM
    assert result.reask["recovered"] == [E8_PATH]
    assert result.reask["unrecovered"] == []
    assert result.reask["error"] is None
    assert result.reask["targets"][0]["reason"] == "assertion_quote_unanchorable"

    assert result.raw_first_turn == e8_paraphrased
    assert result.raw["program_assertions"][0]["quote"] == E8_VERBATIM
    replayed = build_result(e8_note.document_id, e8_note.text, result.raw)
    assert [a.model_dump(mode="json") for a in replayed.assertions] == [
        a.model_dump(mode="json") for a in result.assertions
    ]

    trace = result.trace
    assert trace.steps == ["extract", "reask"]
    assert [m.purpose for m in trace.metrics] == ["extraction", "extraction_reask"]
    assert result.metrics is trace.metrics[0]
    assert trace.model_calls == 2
    assert "reask ok" in trace.termination_reason


def test_a_repeated_paraphrase_stays_dropped_and_is_not_asked_again(
    e8_note, e8_paraphrased
) -> None:
    """The bound, on the live path: a second answer that still does not occur
    in the note is dropped exactly as the first was, and there is no third
    call. The anchorer refused it; nobody asked the model whether to retry."""
    client = _SequencedClient(
        [json.dumps(e8_paraphrased), _answers((E8_PATH, "completed a six-month program"))]
    )
    result = extract(e8_note.document_id, e8_note.text, client)

    assert len(client.requests) == 2
    assert result.assertions == []
    assert [d["reason"] for d in result.dropped] == ["assertion_quote_unanchorable"]
    assert result.reask["recovered"] == []
    assert result.reask["unrecovered"] == [E8_PATH]
    assert result.reask["error"] is None


def test_the_re_ask_is_bounded_by_reask_rounds(e8_note, e8_paraphrased) -> None:
    """`REASK_ROUNDS` is the bound, asserted on the orchestration with counting
    callables so the constant, not the fake's length, is what stops it."""
    asked: list[list[dict]] = []

    def first_turn() -> Turn:
        return Turn(payload=e8_paraphrased, metrics=[_metric("extraction")])

    def reask_turn(targets: list[dict]) -> Turn:
        asked.append(targets)
        return Turn(
            payload={"quotes": [{"path": E8_PATH, "verbatim": "never in the note"}]},
            metrics=[_metric("extraction_reask")],
        )

    result = extract_with_reask(
        e8_note.document_id, e8_note.text, first_turn, reask_turn,
        runner_name="test", model="fake-under-test",
    )
    assert REASK_ROUNDS == 1
    assert len(asked) == REASK_ROUNDS
    assert len(result.trace.metrics) == 1 + REASK_ROUNDS
    assert result.reask["unrecovered"] == [E8_PATH]


def test_a_drop_that_is_not_a_quote_is_never_re_asked(e1_note, e1_payload) -> None:
    """An unparseable date is not a citation problem; no verbatim text repairs
    it, so it is not a target and costs no call."""
    payload = copy.deepcopy(e1_payload)
    payload["wm_events"][0]["date"] = "January 9th"
    client = _SequencedClient([json.dumps(payload)])
    result = extract(e1_note.document_id, e1_note.text, client)

    assert len(client.requests) == 1
    assert [d["reason"] for d in result.dropped] == ["unparseable_date"]
    assert "path" not in result.dropped[0]
    assert result.reask is None


def test_a_recovered_bmi_quote_un_demotes_the_bmi(e1_note, e1_payload) -> None:
    """D15 demotes a BMI nobody can cite; a recovered citation restores it —
    through `build_result`, not through a patch of the result."""
    payload = copy.deepcopy(e1_payload)
    real_quote = payload["wm_events"][0]["bmi_quote"]
    assert payload["wm_events"][0]["bmi"] is not None and real_quote
    payload["wm_events"][0]["bmi_quote"] = "BMI 99.9 (not in the note)"

    client = _SequencedClient(
        [json.dumps(payload), _answers(("wm_events[0].bmi_quote", real_quote))]
    )
    result = extract(e1_note.document_id, e1_note.text, client)

    assert result.events[0].bmi == payload["wm_events"][0]["bmi"]
    assert result.events[0].bmi_span is not None
    assert result.reask["recovered"] == ["wm_events[0].bmi_quote"]


def test_a_recovered_event_quote_exposes_field_drops_that_were_never_asked(
    e1_note, e1_payload
) -> None:
    """D103's eighth point. Turn one skips an event's field quotes when the
    event quote itself fails, so recovering the event quote anchors them for
    the first time; one that fails then is dropped, and it is neither a
    target nor counted as unrecovered — bounded, and honest about it."""
    payload = copy.deepcopy(e1_payload)
    event = payload["wm_events"][0]
    real_event_quote = event["quote"]
    event["quote"] = "a sentence that is not in the note at all"
    event["bmi_quote"] = "BMI 99.9 (not in the note either)"

    client = _SequencedClient(
        [json.dumps(payload), _answers(("wm_events[0].quote", real_event_quote))]
    )
    result = extract(e1_note.document_id, e1_note.text, client)

    assert len(result.events) == len(e1_payload["wm_events"])
    assert result.events[0].bmi is None, "the field quote still does not anchor"
    assert [t["path"] for t in result.reask["targets"]] == ["wm_events[0].quote"]
    assert result.reask["recovered"] == ["wm_events[0].quote"]
    assert result.reask["unrecovered"] == []
    assert [d["path"] for d in result.dropped] == ["wm_events[0].bmi_quote"]
    assert len(client.requests) == 2, "no third call for a drop the re-ask exposed"


# --------------------------------------------------------------------------
# 2. The patch is narrow
# --------------------------------------------------------------------------


def test_the_re_ask_can_neither_add_nor_remove_a_claim(e8_note, e8_paraphrased) -> None:
    """Answers for paths that were not asked about, for paths that do not
    exist, and with extra keys change nothing; the count of events and
    assertions and every non-quote field are the first turn's."""
    answers = json.dumps(
        {
            "quotes": [
                {"path": E8_PATH, "verbatim": E8_VERBATIM},
                {"path": "program_assertions[1].quote", "verbatim": "an invented claim"},
                {"path": "wm_events[0].quote", "verbatim": "Seen 07/02/2026 for evaluation."},
                {"path": "program_assertions[0].claim", "verbatim": "a rewritten claim"},
                {"path": "current_bmi", "verbatim": "41.0"},
            ],
            "wm_events": [{"date": "2026-01-01", "quote": "Seen 07/02/2026", "char_start": 0, "char_end": 1}],
        }
    )
    client = _SequencedClient([json.dumps(e8_paraphrased), answers])
    result = extract(e8_note.document_id, e8_note.text, client)

    assert result.events == []
    assert len(result.assertions) == 1
    assert result.assertions[0].text == e8_paraphrased["program_assertions"][0]["claim"]
    assert result.raw["wm_events"] == []
    assert len(result.raw["program_assertions"]) == 1
    assert result.raw.get("current_bmi") is None


def test_apply_verbatim_writes_only_targeted_quote_fields() -> None:
    payload = {
        "wm_events": [
            {"date": "2026-01-09", "quote": "old", "char_start": 0, "char_end": 3,
             "bmi": 41.3, "bmi_quote": "b-old"},
        ],
        "program_assertions": [],
        "current_bmi": 40.0,
        "current_bmi_quote": "c-old",
    }
    targets = [{"path": "wm_events[0].bmi_quote", "reason": "bmi_quote_unanchorable", "quote": "b-old"}]
    answers = VerbatimAnswers.model_validate(
        {
            "quotes": [
                {"path": "wm_events[0].bmi_quote", "verbatim": "b-new"},
                {"path": "wm_events[0].quote", "verbatim": "not asked"},
                {"path": "current_bmi_quote", "verbatim": "not asked"},
                {"path": "wm_events[0].date", "verbatim": "2020-01-01"},
                {"path": "wm_events[7].bmi_quote", "verbatim": "no such event"},
            ]
        }
    )
    before = copy.deepcopy(payload)
    patched = apply_verbatim(payload, targets, answers)

    assert payload == before, "the first turn's payload is never mutated"
    assert patched["wm_events"][0]["bmi_quote"] == "b-new"
    assert patched["wm_events"][0]["quote"] == "old"
    assert patched["current_bmi_quote"] == "c-old"
    assert patched["wm_events"][0]["date"] == "2026-01-09"
    assert len(patched["wm_events"]) == 1


def test_a_blank_verbatim_is_ignored(e8_note, e8_paraphrased) -> None:
    """An empty quote would anchor at offset zero and cite nothing. The model
    said "no such passage", which the anchorer's drop already records."""
    client = _SequencedClient([json.dumps(e8_paraphrased), _answers((E8_PATH, "   "))])
    result = extract(e8_note.document_id, e8_note.text, client)

    assert result.assertions == []
    assert result.raw["program_assertions"][0]["quote"] == E8_PARAPHRASE
    assert result.reask["unrecovered"] == [E8_PATH]


def test_reask_targets_carry_the_full_quote_not_the_truncated_one(e1_note) -> None:
    """`dropped[]` truncates the quote at 120 characters for the record; the
    re-ask must show the model what it actually said."""
    long_quote = "x" * 130
    payload = {
        "wm_events": [{"date": "2026-01-09", "quote": long_quote, "char_start": 0, "char_end": 130}],
        "program_assertions": [],
    }
    result = build_result(e1_note.document_id, e1_note.text, payload)
    assert len(result.dropped[0]["quote"]) == 120
    targets = reask_targets(result)
    assert targets == [{"path": "wm_events[0].quote", "reason": "event_quote_unanchorable", "quote": long_quote}]


def test_every_quote_drop_reason_is_a_target_and_nothing_else_is(e1_note, e1_payload) -> None:
    """Six quote fields, six reasons, six paths — and the seventh reason,
    `unparseable_date`, is not one of them."""
    payload = copy.deepcopy(e1_payload)
    event = payload["wm_events"][0]
    event.update(
        {"bmi": 41.3, "bmi_quote": "nope-b", "diet_documented": True, "diet_quote": "nope-d",
         "activity_documented": True, "activity_quote": "nope-a"}
    )
    payload["current_bmi"] = 40.0
    payload["current_bmi_quote"] = "nope-c"
    payload["program_assertions"] = [
        {"quote": "nope-p", "char_start": 0, "char_end": 1, "claim": "x"}
    ]
    payload["wm_events"][1]["quote"] = "nope-e"
    payload["wm_events"][2]["date"] = "not a date"

    result = build_result(e1_note.document_id, e1_note.text, payload)
    reasons = {t["reason"] for t in reask_targets(result)}
    assert reasons == {
        "bmi_quote_unanchorable", "diet_quote_unanchorable", "activity_quote_unanchorable",
        "current_bmi_quote_unanchorable", "assertion_quote_unanchorable",
        "event_quote_unanchorable",
    }
    assert {d["reason"] for d in result.dropped} == reasons | {"unparseable_date"}


# --------------------------------------------------------------------------
# 3. A failed re-ask never raises
# --------------------------------------------------------------------------


def test_a_failed_re_ask_keeps_the_first_turn_result_and_says_so(
    e8_note, e8_paraphrased
) -> None:
    """A transport fault on the second call is recorded, classified, on the
    result and the trace — never raised, because raising would send a
    successful extraction back through the attempt budget and report "the
    system did not look" for a note it read (D90, D103)."""
    client = _SequencedClient([json.dumps(e8_paraphrased), RuntimeError("503 overloaded")])
    runner = DirectExtractionRunner(client)
    result = runner.run(e8_note.document_id, e8_note.text)

    assert result.assertions == []
    assert [d["reason"] for d in result.dropped] == ["assertion_quote_unanchorable"]
    assert result.reask["error"].startswith("CALL_FAILED: RuntimeError")
    assert result.reask["recovered"] == [] and result.reask["unrecovered"] == [E8_PATH]
    assert len(result.trace.metrics) == 1, "a call that failed measured nothing"
    assert result.trace.steps == ["extract", "reask"]
    assert "CALL_FAILED" in result.trace.termination_reason


def test_a_non_json_re_ask_answer_is_unparseable_with_zero_recoveries(
    e8_note, e8_paraphrased
) -> None:
    client = _SequencedClient([json.dumps(e8_paraphrased), "this is not JSON"])
    result = extract(e8_note.document_id, e8_note.text, client)

    assert result.reask["error"].startswith("UNPARSEABLE")
    assert result.reask["unrecovered"] == [E8_PATH]
    assert len(result.trace.metrics) == 2, "the call was made and measured"


def test_a_re_ask_answer_of_the_wrong_shape_is_schema_invalid(
    e8_note, e8_paraphrased
) -> None:
    """`quotes` is required. An `Extraction` payload arriving where
    `VerbatimAnswers` was asked for is a schema failure, recorded as one —
    not read as "no answers"."""
    client = _SequencedClient([json.dumps(e8_paraphrased), json.dumps(e8_paraphrased)])
    result = extract(e8_note.document_id, e8_note.text, client)

    assert result.reask["error"].startswith("SCHEMA_INVALID")
    assert result.reask["answers"] == []
    assert result.reask["unrecovered"] == [E8_PATH]


def test_a_first_turn_failure_still_raises_classified(e8_note) -> None:
    """The contract has two halves: the first turn raises, the re-ask records.
    The direct runner's classification of the first is unchanged."""
    runner = DirectExtractionRunner(_SequencedClient([RuntimeError("503")]))
    with pytest.raises(ExtractionOutputError) as caught:
        runner.run(e8_note.document_id, e8_note.text)
    assert caught.value.reason is ExtractionFailure.CALL_FAILED

    runner = DirectExtractionRunner(_SequencedClient(["not json"]))
    with pytest.raises(ExtractionOutputError) as caught:
        runner.run(e8_note.document_id, e8_note.text)
    assert caught.value.reason is ExtractionFailure.UNPARSEABLE


# --------------------------------------------------------------------------
# 4. Every turn counted, on the replay too
# --------------------------------------------------------------------------


def _two_turn_record(document_id: str, note: Document, payload: dict) -> dict:
    turns = [
        _metric("extraction").model_dump(mode="json"),
        _metric("extraction_reask").model_dump(mode="json"),
    ]
    return {
        "document_id": document_id,
        "note_sha256": note.sha256,
        "raw": payload,
        "metrics": turns[0],
        "trace": {
            "runner_name": "direct",
            "model": "fake-under-test",
            "prompt_version": PROMPT_VERSION,
            "document_id": document_id,
            "steps": ["extract", "reask"],
            "tool_calls": [],
            "attempts": 1,
            "termination_reason": "ok (1ms); reask ok (1ms)",
            "metrics": turns,
        },
    }


def test_the_recorded_runner_replays_every_recorded_turn(e1_note, e1_payload) -> None:
    """A replay that carried one turn for a note measured at two would
    understate what the answer cost — D71's defect on the replay path, which
    is where A6's figures come from."""
    record = _two_turn_record(e1_note.document_id, e1_note, e1_payload)
    runner = RecordedExtractionRunner.from_records([record])
    result = runner.run(e1_note.document_id, e1_note.text)

    assert result.trace is not None
    assert result.trace.model_calls == 2
    assert [m.purpose for m in result.trace.metrics] == ["extraction", "extraction_reask"]
    assert result.metrics == result.trace.metrics[0]


def test_the_replayed_trace_is_re_addressed_to_the_requesting_document(
    e1_note, e1_payload
) -> None:
    """D102's content-keyed replay, applied to the trace: a clone's replay
    names the clone."""
    record = _two_turn_record(e1_note.document_id, e1_note, e1_payload)
    runner = RecordedExtractionRunner.from_records([record])
    clone = Document.from_text("clone/chart_note.txt", e1_note.text)
    result = runner.run(clone.document_id, clone.text)

    assert result.document_id == clone.document_id
    assert result.trace.document_id == clone.document_id
    assert result.trace.model_calls == 2


def test_the_recorded_runner_carries_no_trace_when_the_record_has_none(
    e1_note, e1_payload
) -> None:
    """`None` rather than an empty trace, because a replay invents nothing: a
    record written before traces existed replays with its one measurement and
    no trace, as it always did (T-62)."""
    record = {
        "document_id": e1_note.document_id,
        "note_sha256": e1_note.sha256,
        "raw": e1_payload,
        "metrics": _metric("extraction").model_dump(mode="json"),
    }
    result = RecordedExtractionRunner.from_records([record]).run(
        e1_note.document_id, e1_note.text
    )
    assert result.trace is None
    assert result.metrics is not None


# --------------------------------------------------------------------------
# 5. The configuration is identifiable
# --------------------------------------------------------------------------


def test_the_prompt_version_names_both_turns() -> None:
    """One identifier for the whole call configuration: the instruction the
    first turn carries, unchanged since T-15, and the re-ask T-89 added. A
    recording stamped with only the first half would claim T-15's
    configuration for a run that did not use it (D45, D64, D103)."""
    from pa_agent.agent import extraction_agent

    assert "t15-instruction-v1" in PROMPT_VERSION
    assert "t89-reask" in PROMPT_VERSION
    assert extraction_agent.PROMPT_VERSION is PROMPT_VERSION


def test_a_trace_is_built_with_the_configuration_version(e1_note, e1_payload) -> None:
    result = extract(e1_note.document_id, e1_note.text, _SequencedClient([json.dumps(e1_payload)]))
    assert isinstance(result.trace, RunTrace)
    assert result.trace.prompt_version == PROMPT_VERSION
    assert result.trace.runner_name == "direct"


# --------------------------------------------------------------------------
# 6. `--rescore` re-derives the recovered set; it never copies it
# --------------------------------------------------------------------------


def _run_extraction_module():
    import importlib.util
    import sys

    path = REPO_ROOT / "scripts" / "run_extraction.py"
    spec = importlib.util.spec_from_file_location("run_extraction", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_extraction"] = module
    spec.loader.exec_module(module)
    return module


def test_rescore_re_derives_the_recovered_set_from_both_payloads(e8_note, e8_paraphrased) -> None:
    """D18's rule applied to T-89's audit: the recovered set is what anchoring
    the two payloads says today, not what the record claims. A tampered
    `recovered` list is overwritten; the answers, the error and the trace —
    measured facts — carry through."""
    script = _run_extraction_module()
    patched = copy.deepcopy(e8_paraphrased)
    patched["program_assertions"][0]["quote"] = E8_VERBATIM
    prior = {
        "raw": patched,
        "raw_first_turn": e8_paraphrased,
        "trace": {
            "runner_name": "direct", "model": "fake-under-test",
            "prompt_version": PROMPT_VERSION, "document_id": e8_note.document_id,
            "steps": ["extract", "reask"], "tool_calls": [], "attempts": 1,
            "termination_reason": "ok; reask ok",
            "metrics": [_metric("extraction").model_dump(mode="json"),
                        _metric("extraction_reask").model_dump(mode="json")],
        },
        "reask": {
            "targets": [], "answers": [{"path": E8_PATH, "verbatim": E8_VERBATIM}],
            "recovered": ["tampered"], "unrecovered": ["tampered too"], "error": None,
        },
    }
    result = build_result(e8_note.document_id, e8_note.text, prior["raw"])
    script._rederive_reask(result, prior, e8_note.document_id, e8_note.text)

    assert result.reask["recovered"] == [E8_PATH]
    assert result.reask["unrecovered"] == []
    assert result.reask["targets"][0]["path"] == E8_PATH
    assert result.reask["answers"] == prior["reask"]["answers"]
    assert result.raw_first_turn == e8_paraphrased
    assert result.trace is not None and result.trace.model_calls == 2


def test_rescore_of_a_note_that_was_not_re_asked_carries_no_block(e1_note, e1_payload) -> None:
    script = _run_extraction_module()
    prior = {"raw": e1_payload, "raw_first_turn": None, "reask": None, "trace": None}
    result = build_result(e1_note.document_id, e1_note.text, prior["raw"])
    script._rederive_reask(result, prior, e1_note.document_id, e1_note.text)
    assert result.reask is None and result.raw_first_turn is None and result.trace is None


def test_the_direct_aggregate_sums_every_turn(e8_note, e8_paraphrased) -> None:
    """D71's rule reaching the direct measurement script: a re-asked note
    costs two turns, and the aggregate reports both — from the trace, not
    from the singular `metrics` that is turn one only."""
    script = _run_extraction_module()
    one = _metric("extraction").model_dump(mode="json")
    two = {**_metric("extraction_reask").model_dump(mode="json"), "input_tokens": 40, "output_tokens": 4}
    score = {
        "labeled_events": 0, "extracted_events": 0, "matched_events": 0, "traps_req9": 0,
        "req9_traps_extracted": [], "spans_emitted": 1, "spans_anchored": 1,
        "spans_normalized": 0, "spans_unescaped": 0, "spans_multi_occurrence": 0,
        "spans_disambiguated": 0, "model_offsets_usable": 0, "field_agreements": 0,
        "field_total": 0, "dropped": [], "traps_extracted": [], "assertions": 1,
        "assertion_required": True,
    }
    record = {
        "note_id": "x", "score": score, "metrics": one,
        "trace": {"metrics": [one, two], "tool_calls": []},
        "reask": {"targets": [{"path": E8_PATH}], "answers": [], "recovered": [E8_PATH],
                  "unrecovered": [], "error": None},
    }
    figures = script.aggregate([record])
    assert figures["model_calls"] == 2
    assert figures["total_input_tokens"] == 1 + 40
    assert figures["total_output_tokens"] == 1 + 4
    assert (figures["reask_notes"], figures["reask_targets"], figures["reask_recovered"],
            figures["reask_calls"]) == (1, 1, 1, 1)
