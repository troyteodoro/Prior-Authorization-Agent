"""T-98 — the quote port (D122), spending no model call.

The one model turn the medical-history review gets: asked for verbatim
passages documenting each knowledge-table condition, anchored by
`pa_agent.anchor`, dropped when unanchorable, re-asked once (T-89's core on a
second payload shape). This file pins the shape, never a measured number —
the recordings are `tests/test_quote_recording.py`'s.

1. **The model is asked about conditions, and nothing else.** No drug, no
   row id, no code, no colour. Every asked row is keyed in the answer.
2. **Python anchors; the model's offsets are recorded and never trusted.**
   A paraphrase is dropped with the path the re-ask reads it back from.
3. **The re-ask fires on a Python predicate, once, and never raises.**
4. **The recorded runner replays by content, refuses moved bytes, an
   unmeasured note, and a row it was never asked.**

The direct client is a sequenced fake with the shape `google-genai` exposes,
copied from `tests/test_reask.py` for the same reason it was written there.
"""

from __future__ import annotations

import copy
import json
import re
from types import SimpleNamespace

import pytest

from pa_agent.contracts import (
    CallMetrics,
    CodedConcept,
    EffectSignal,
    EvidenceSpan,
    MedicationEffectRow,
    RunTrace,
)
from pa_agent.extraction import PROMPT_VERSION, REASK_INSTRUCTION, REASK_ROUNDS
from pa_agent.quotes import (
    DROP_REASONS,
    EFFECTS_HEADING,
    QUOTE_INSTRUCTION,
    QUOTE_PROMPT_VERSION,
    DirectQuoteRunner,
    NullQuoteRunner,
    QuoteAnswers,
    QuoteFailure,
    QuoteOutputError,
    QuoteRunner,
    RecordedQuoteRunner,
    _locate_quote,
    build_quote_result,
    consult,
    format_effects,
    quote_contents,
    rows_asked,
)
from pa_agent.stores.knowledge import LocalKnowledgeStore

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"

NOTE = (
    "ASSESSMENT\n"
    "Chronic kidney disease stage 2 noted on labs.\n"
    "Blood pressure 118/76, unremarkable.\n"
    "On lisinopril 10 mg daily.\n"
)
RENAL_VERBATIM = "Chronic kidney disease stage 2 noted on labs."
RENAL_PARAPHRASE = "Chronic kidney disease stage two noted on labs."
FIRST_PATH = "effects[0].quotes[0].quote"
DOC = "src/chart_note_1.txt"


# --------------------------------------------------------------------------
# Builders and the sequenced fake client
# --------------------------------------------------------------------------


def a_row(row_id: str, display: str, ingredient: str = "1") -> MedicationEffectRow:
    return MedicationEffectRow(
        row_id=row_id,
        ingredient=CodedConcept(system=RXNORM, code=ingredient, display="drug"),
        ingredient_source={"origin": "a test"},
        effect_display=display,
        effect=EvidenceSpan(document_id="spl_drug", char_start=0, char_end=5, quote="an ef"),
        icd10_code="X00.0",
        icd10_title=f"{display.capitalize()}, unspecified",
        icd10_source={"origin": "a test"},
        already_coded=(CodedConcept(system=SNOMED, code="111"),),
        signal=EffectSignal(
            system=LOINC, code="2160-0", comparator="gt", threshold=1.3,
            unit="mg/dL", constant_name="a_threshold",
        ),
    )


RENAL = a_row("drug-renal", "renal impairment", "1")
HYPO = a_row("drug-hypo", "hypotension", "2")
ROWS = [RENAL, HYPO]


def _payload(*blocks: tuple[str, list[tuple[str, int, int]]]) -> dict:
    return {
        "effects": [
            {"effect": effect, "quotes": [
                {"quote": q, "char_start": s, "char_end": e} for q, s, e in quotes
            ]}
            for effect, quotes in blocks
        ]
    }


class _SequencedClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        index = len(self.requests)
        self.requests.append({"model": model, "contents": contents, "config": config})
        item = self._responses[index]
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(
            text=item,
            usage_metadata=SimpleNamespace(
                prompt_token_count=100 + index, candidates_token_count=10 + index,
            ),
        )


def _answers(*pairs: tuple[str, str]) -> str:
    return json.dumps({"quotes": [{"path": p, "verbatim": v} for p, v in pairs]})


def _metric(purpose: str = "quotes") -> CallMetrics:
    return CallMetrics(
        model="fake-under-test", purpose=purpose, input_tokens=1,
        output_tokens=1, wall_time_ms=1.0,
    )


# --------------------------------------------------------------------------
# 1. Conditions, and nothing else
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def table_rows() -> list[MedicationEffectRow]:
    return LocalKnowledgeStore().get_medication_effect_rows()


def test_the_request_names_no_drug_row_id_code_or_colour(table_rows) -> None:
    """D122: every row id embeds the ingredient, and a model told *lisinopril*
    beside *renal impairment* is invited to quote the medication line as
    evidence of the condition — the one thing D119 says a prescription may
    never be. The ICD-10 title can say *drug-induced*. Neither is sent."""
    request = quote_contents("a note", table_rows).lower()
    for row in table_rows:
        assert row.row_id.lower() not in request, row.row_id
        assert row.ingredient.display.lower() not in request, row.ingredient.display
        assert row.icd10_code.lower() not in request, row.icd10_code
        assert row.icd10_title.lower() not in request, row.icd10_title
    for word in ("green", "yellow", "red", "icd", "rxnorm", "threshold"):
        assert not re.search(rf"\b{word}\b", request), word
    for row in table_rows:
        assert f"- {row.effect_display}" in request


def test_format_effects_lists_the_displays_in_the_order_given(table_rows) -> None:
    rendered = format_effects(table_rows)
    assert rendered.splitlines()[0] == EFFECTS_HEADING
    assert rendered.splitlines()[1:] == [f"- {r.effect_display}" for r in table_rows]
    assert rows_asked(table_rows) == tuple((r.row_id, r.effect_display) for r in table_rows)


def test_two_rows_sharing_a_display_are_refused() -> None:
    twin = a_row("other-renal", "renal impairment", "3")
    with pytest.raises(ValueError, match="share the display"):
        format_effects([RENAL, twin])


def test_the_version_names_both_halves_and_shares_the_reask_half() -> None:
    first, second = QUOTE_PROMPT_VERSION.split("/")
    assert first.startswith("t98-quotes-")
    assert second == PROMPT_VERSION.split("/")[1], "the re-ask is T-89's, verbatim"


def test_the_response_schema_requires_the_effects_list() -> None:
    """`VerbatimAnswers.quotes`' rule: another shape is a schema failure,
    never read as 'nothing found'. An empty list *is* nothing found."""
    with pytest.raises(Exception):
        QuoteAnswers.model_validate({})
    assert QuoteAnswers.model_validate({"effects": []}).effects == []


# --------------------------------------------------------------------------
# 2. Python anchors; offsets are recorded and never trusted
# --------------------------------------------------------------------------


def test_an_anchored_quote_lands_under_its_row_by_search_not_by_offset() -> None:
    payload = _payload(("renal impairment", [(RENAL_VERBATIM, 999, 1000)]))
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    (span,) = result.spans["drug-renal"]
    assert span.document_id == DOC
    assert NOTE[span.char_start:span.char_end] == RENAL_VERBATIM
    assert result.spans["drug-hypo"] == ()
    assert result.dropped == []
    (located,) = result.anchored_spans
    assert located.anchored and not located.model_offsets_yield_quote
    assert result.raw is payload


def test_a_paraphrase_is_dropped_with_the_path_the_reask_reads() -> None:
    payload = _payload(("renal impairment", [(RENAL_PARAPHRASE, 11, 58)]))
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    assert result.spans == {"drug-renal": (), "drug-hypo": ()}
    assert result.dropped == [
        {"reason": "effect_quote_unanchorable", "quote": RENAL_PARAPHRASE, "path": FIRST_PATH}
    ]
    assert result.quotes_anchored == 0


def test_a_blank_quote_is_dropped_and_never_re_asked() -> None:
    payload = _payload(("renal impairment", [("   ", 0, 3)]))
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    (drop,) = result.dropped
    assert drop["reason"] == "blank_quote" and "path" not in drop
    assert result.anchored_spans == []


def test_a_condition_nobody_asked_about_is_dropped_and_never_re_asked() -> None:
    payload = _payload(("obesity", [("ASSESSMENT", 0, 10)]))
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    (drop,) = result.dropped
    assert drop["reason"] == "unknown_effect" and "path" not in drop
    assert result.spans == {"drug-renal": (), "drug-hypo": ()}


def test_a_passage_quoted_twice_for_one_condition_cites_once() -> None:
    payload = _payload(
        ("renal impairment", [(RENAL_VERBATIM, 0, 1), (RENAL_VERBATIM, 0, 1)]),
    )
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    assert len(result.spans["drug-renal"]) == 1
    assert len(result.anchored_spans) == 2, "both were located; one cites"


def test_every_asked_row_is_keyed_even_when_the_model_omits_it() -> None:
    result = build_quote_result(DOC, NOTE, {"effects": []}, rows=ROWS)
    assert result.spans == {"drug-renal": (), "drug-hypo": ()}
    assert result.rows_asked == (("drug-renal", "renal impairment"), ("drug-hypo", "hypotension"))


def test_every_drop_reason_is_in_the_closed_set() -> None:
    payload = _payload(
        ("renal impairment", [(RENAL_PARAPHRASE, 0, 1), ("", 0, 0)]),
        ("obesity", [("ASSESSMENT", 0, 10)]),
    )
    result = build_quote_result(DOC, NOTE, payload, rows=ROWS)
    assert {d["reason"] for d in result.dropped} == DROP_REASONS


def test_the_locator_names_a_quote_field_and_nothing_else() -> None:
    payload = _payload(("renal impairment", [(RENAL_PARAPHRASE, 0, 1)]))
    container, key = _locate_quote(payload, FIRST_PATH)
    assert key == "quote" and container["quote"] == RENAL_PARAPHRASE
    for bad in ("effects[0].quote", "effects[0].quotes[0].effect", "wm_events[0].quote",
                "effects[1].quotes[0].quote", "effects[0].quotes[3].quote", ""):
        with pytest.raises(KeyError):
            _locate_quote(payload, bad)


# --------------------------------------------------------------------------
# 3. The re-ask: Python decides, once, and never raises
# --------------------------------------------------------------------------


def test_a_note_whose_quotes_anchor_makes_one_call() -> None:
    client = _SequencedClient([json.dumps(_payload(("renal impairment", [(RENAL_VERBATIM, 0, 1)])))])
    result = consult(DOC, NOTE, ROWS, client, model="fake-under-test")
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["contents"] == f"{QUOTE_INSTRUCTION}\n\n{format_effects(ROWS)}\n\nNOTE:\n{NOTE}"
    assert request["config"]["temperature"] == 0.0
    assert request["config"]["response_schema"] is QuoteAnswers
    assert result.metrics is not None and result.metrics.purpose == "quotes"
    assert result.reask is None and result.raw_first_turn is None
    assert result.trace is not None
    assert result.trace.runner_name == "direct"
    assert result.trace.prompt_version == QUOTE_PROMPT_VERSION
    assert result.trace.steps == ["quotes"]
    assert result.trace.metrics == [result.metrics]


def test_an_unanchorable_quote_is_re_asked_once_and_the_answer_is_anchored() -> None:
    first = _payload(("renal impairment", [(RENAL_PARAPHRASE, 0, 1)]))
    client = _SequencedClient([json.dumps(first), _answers((FIRST_PATH, RENAL_VERBATIM))])
    result = consult(DOC, NOTE, ROWS, client, model="fake-under-test")
    assert len(client.requests) == 2
    second = client.requests[1]["contents"]
    assert second.startswith(REASK_INSTRUCTION)
    assert f"NOTE:\n{NOTE}" in second and FIRST_PATH in second
    assert "CONDITIONS:" not in second, "the re-ask asks for text, not for conditions"
    (span,) = result.spans["drug-renal"]
    assert NOTE[span.char_start:span.char_end] == RENAL_VERBATIM
    assert result.reask["recovered"] == [FIRST_PATH] and result.reask["unrecovered"] == []
    assert result.raw_first_turn == first
    assert result.raw["effects"][0]["quotes"][0]["quote"] == RENAL_VERBATIM
    assert [m.purpose for m in result.trace.metrics] == ["quotes", "quotes_reask"]
    assert result.metrics == result.trace.metrics[0], "`metrics` is turn one"
    assert result.trace.steps == ["quotes", "quotes_reask"]


def test_a_repeated_paraphrase_stays_dropped_and_is_asked_only_once() -> None:
    first = _payload(("renal impairment", [(RENAL_PARAPHRASE, 0, 1)]))
    client = _SequencedClient([json.dumps(first), _answers((FIRST_PATH, "still not verbatim"))])
    result = consult(DOC, NOTE, ROWS, client, model="fake-under-test")
    assert len(client.requests) == 1 + REASK_ROUNDS
    assert result.spans["drug-renal"] == ()
    assert result.reask["unrecovered"] == [FIRST_PATH]
    assert result.dropped[0]["reason"] == "effect_quote_unanchorable"


def test_a_failed_re_ask_is_recorded_and_never_raised() -> None:
    first = _payload(("renal impairment", [(RENAL_PARAPHRASE, 0, 1)]))
    client = _SequencedClient([json.dumps(first), RuntimeError("503 from the fake")])
    result = consult(DOC, NOTE, ROWS, client, model="fake-under-test")
    assert result.spans["drug-renal"] == ()
    assert result.reask["error"].startswith("CALL_FAILED")
    assert result.reask["unrecovered"] == [FIRST_PATH]
    assert "CALL_FAILED" in (result.trace.termination_reason or "")
    assert len(result.trace.metrics) == 1, "a turn that produced no response measures nothing"


# --------------------------------------------------------------------------
# 4. The runners
# --------------------------------------------------------------------------


def test_the_direct_runner_classifies_non_json_as_unparseable() -> None:
    runner = DirectQuoteRunner(_SequencedClient(["not json"]), model="fake-under-test")
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(DOC, NOTE, ROWS)
    assert caught.value.reason is QuoteFailure.UNPARSEABLE


def test_the_direct_runner_classifies_a_wrong_shape_as_schema_invalid() -> None:
    runner = DirectQuoteRunner(_SequencedClient([json.dumps({"quotes": []})]), model="x")
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(DOC, NOTE, ROWS)
    assert caught.value.reason is QuoteFailure.SCHEMA_INVALID


def test_the_direct_runner_classifies_a_transport_failure_as_call_failed() -> None:
    runner = DirectQuoteRunner(_SequencedClient([RuntimeError("boom")]), model="x")
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(DOC, NOTE, ROWS)
    assert caught.value.reason is QuoteFailure.CALL_FAILED


def _records() -> list[dict]:
    import hashlib

    trace = RunTrace(
        runner_name="direct", model="fake-under-test", prompt_version=QUOTE_PROMPT_VERSION,
        document_id=DOC, steps=["quotes"], metrics=[_metric()],
    )
    return [{
        "document_id": DOC,
        "note_sha256": hashlib.sha256(NOTE.encode("utf-8")).hexdigest(),
        "raw": _payload(("renal impairment", [(RENAL_VERBATIM, 0, 1)])),
        "metrics": _metric().model_dump(mode="json"),
        "trace": trace.model_dump(mode="json"),
    }, {
        "document_id": "scored-only/chart_note_2.txt", "note_sha256": "0" * 64, "raw": None,
    }]


ASKED = [{"row_id": "drug-renal", "effect_display": "renal impairment"},
         {"row_id": "drug-hypo", "effect_display": "hypotension"}]


def test_the_recorded_runner_replays_by_content_under_another_id() -> None:
    runner = RecordedQuoteRunner.from_records(_records(), ASKED, model="fake-under-test")
    assert DOC in runner and "scored-only/chart_note_2.txt" not in runner
    result = runner.run("clone/chart_note_1.txt", NOTE, ROWS)
    (span,) = result.spans["drug-renal"]
    assert span.document_id == "clone/chart_note_1.txt"
    assert result.trace.document_id == "clone/chart_note_1.txt"
    assert result.metrics == _metric(), "recorded metrics carried through (Art. X)"
    assert result.trace.metrics == [_metric()]


def test_the_recorded_runner_refuses_moved_bytes() -> None:
    runner = RecordedQuoteRunner.from_records(_records(), ASKED)
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(DOC, NOTE + "an edit\n", ROWS)
    assert caught.value.reason is QuoteFailure.DOCUMENT_CHANGED
    assert "D18" in str(caught.value)


def test_the_recorded_runner_refuses_an_unmeasured_note_and_names_the_script() -> None:
    runner = RecordedQuoteRunner.from_records(_records(), ASKED)
    with pytest.raises(QuoteOutputError) as caught:
        runner.run("nobody/chart_note_1.txt", "never measured", ROWS)
    assert caught.value.reason is QuoteFailure.NOT_RECORDED
    assert "run_quote_measurement.py" in str(caught.value)


def test_the_recorded_runner_refuses_a_row_it_was_never_asked() -> None:
    """Adding a table row is a new measurement (D45): the recording answers
    only the pairs it was asked, and a request naming another is a miss."""
    runner = RecordedQuoteRunner.from_records(_records(), ASKED)
    new_row = a_row("drug-new", "a new effect", "9")
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(DOC, NOTE, [*ROWS, new_row])
    assert caught.value.reason is QuoteFailure.NOT_RECORDED
    assert "D45" in str(caught.value) and "a new effect" in str(caught.value)
    renamed = a_row("drug-renal", "kidney trouble", "1")
    with pytest.raises(QuoteOutputError):
        runner.run(DOC, NOTE, [renamed])


def test_the_recorded_runner_answers_a_subset_of_the_asked_rows() -> None:
    runner = RecordedQuoteRunner.from_records(_records(), [tuple(a.values()) for a in ASKED])
    result = runner.run(DOC, NOTE, [HYPO])
    assert result.spans == {"drug-hypo": ()}
    assert result.rows_asked == (("drug-hypo", "hypotension"),)


def test_the_null_runner_raises() -> None:
    with pytest.raises(AssertionError, match="consult no note"):
        NullQuoteRunner("the chart is note-free").run(DOC, NOTE, ROWS)


def test_every_runner_satisfies_the_port() -> None:
    assert isinstance(DirectQuoteRunner(_SequencedClient([])), QuoteRunner)
    assert isinstance(RecordedQuoteRunner.from_records([], ASKED), QuoteRunner)
    assert isinstance(NullQuoteRunner(), QuoteRunner)
