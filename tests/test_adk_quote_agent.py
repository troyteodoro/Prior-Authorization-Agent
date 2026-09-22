"""T-98 — the ADK quote runner (D122), spending no model call.

`tests/test_adk_agent.py`'s pattern: ADK's `LlmAgent` takes a `BaseLlm`
instance, so a twenty-line fake drives the real flow — real tool declarations,
real dispatch, real `output_schema` handling, real plugin hooks — with the
network and nothing else faked. What this file pins:

1. **`build_quote_result()` is the trust boundary.** A fake run's result equals
   what that function produces from the same payload.
2. **The allowlist is the extractor's one tool, or nothing.** Inline, no tools;
   tool-fetch, exactly `read_note` scoped to the document under review.
3. **Article I is structural.** `SingleFlow`, no `transfer_to_agent`,
   `include_contents="none"`, temperature 0, a call ceiling.
4. **The re-ask is a second self-contained invocation**, with its own session,
   mirroring the finder's flags — and the conditions never reach the re-ask.
5. **The ADK stays in `pa_agent/agent/`**: `pa_agent/quotes.py` imports no
   `google.*`, which `test_adk_agent.py`'s package-wide scan already asserts.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import pytest

from pa_agent.contracts import (
    CodedConcept,
    Document,
    EffectSignal,
    EvidenceSpan,
    MedicationEffectRow,
)
from pa_agent.extraction import REASK_INSTRUCTION, REASK_ROUNDS, VerbatimAnswers
from pa_agent.quotes import (
    QUOTE_INSTRUCTION,
    QUOTE_PROMPT_VERSION,
    QuoteAnswers,
    QuoteFailure,
    QuoteOutputError,
    QuoteRunner,
    build_quote_result,
    format_effects,
)

from pa_agent.agent.extraction_agent import DEFAULT_MAX_LLM_CALLS, EXTRACTION_ALLOWLIST
from pa_agent.agent.quote_agent import (
    QUOTE_AGENT_NAME,
    QUOTE_ALLOWLIST,
    QUOTE_OUTPUT_KEY,
    QUOTE_REASK_AGENT_NAME,
    QUOTE_REASK_OUTPUT_KEY,
    AdkQuoteRunner,
    build_quote_agent,
    build_quote_reask_agent,
)

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"

NOTE = (
    "ASSESSMENT\n"
    "Chronic kidney disease stage 2 noted on labs.\n"
    "Blood pressure 118/76, unremarkable.\n"
)
RENAL_VERBATIM = "Chronic kidney disease stage 2 noted on labs."
RENAL_PARAPHRASE = "Chronic kidney disease stage two noted on labs."
FIRST_PATH = "effects[0].quotes[0].quote"
DOC_ID = "p1/chart_note_1.txt"


def a_row(row_id: str, display: str, ingredient: str) -> MedicationEffectRow:
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


ROWS = [a_row("drug-renal", "renal impairment", "1"), a_row("drug-hypo", "hypotension", "2")]


def _payload(quote: str) -> dict:
    return {"effects": [{"effect": "renal impairment",
                         "quotes": [{"quote": quote, "char_start": 0, "char_end": 1}]}]}


def _verbatim_answer() -> str:
    return json.dumps({"quotes": [{"path": FIRST_PATH, "verbatim": RENAL_VERBATIM}]})


@pytest.fixture(scope="module")
def note() -> Document:
    return Document.from_text(DOC_ID, NOTE)


def _fake_llm(responses: list[Any], model_name: str = "fake-under-test"):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class FakeLlm(BaseLlm):
        model: str = model_name
        prepared: list[Any] = []
        cursor: int = -1
        seen: list[Any] = []

        async def generate_content_async(
            self, llm_request, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            self.cursor += 1
            self.seen.append(llm_request)
            item = self.prepared[min(self.cursor, len(self.prepared) - 1)]
            part = types.Part(text=item) if isinstance(item, str) else item
            yield LlmResponse(
                content=types.Content(role="model", parts=[part]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=11, candidates_token_count=7, total_token_count=18,
                ),
            )

    return FakeLlm(prepared=list(responses))


class _RecordingPatientStore:
    def __init__(self, notes: list[Document] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._notes = {n.document_id: n for n in (notes or [])}

    def get_notes(self, patient_id: str) -> list[Document]:
        self.calls.append(("get_notes", patient_id))
        return list(self._notes.values())

    def get_document(self, document_id: str) -> Document:
        self.calls.append(("get_document", document_id))
        return self._notes[document_id]


def _message_text(llm_request) -> str:
    return "\n".join(
        part.text for content in llm_request.contents for part in content.parts if part.text
    )


# --------------------------------------------------------------------------
# 1. The trust boundary
# --------------------------------------------------------------------------


def test_a_fake_run_produces_exactly_what_build_quote_result_produces(note) -> None:
    payload = _payload(RENAL_VERBATIM)
    runner = AdkQuoteRunner(llm=_fake_llm([json.dumps(payload)]))
    result = runner.run(note.document_id, note.text, ROWS)
    expected = build_quote_result(note.document_id, note.text, payload, rows=ROWS)
    assert result.spans == expected.spans
    assert result.dropped == expected.dropped == []
    assert result.rows_asked == expected.rows_asked
    (span,) = result.spans["drug-renal"]
    assert note.text[span.char_start:span.char_end] == RENAL_VERBATIM

    trace = result.trace
    assert trace.runner_name == "adk"
    assert trace.document_id == note.document_id
    assert trace.prompt_version == QUOTE_PROMPT_VERSION
    assert trace.steps == ["adk_quotes"]
    assert trace.metrics and trace.metrics[0].purpose == "quotes"
    assert trace.metrics[0].input_tokens == 11 and trace.metrics[0].output_tokens == 7
    assert result.metrics is trace.metrics[0]


def test_the_conditions_ride_in_the_message_and_the_instruction_is_static(note) -> None:
    fake = _fake_llm([json.dumps({"effects": []})])
    AdkQuoteRunner(llm=fake).run(note.document_id, note.text, ROWS)
    (request,) = fake.seen
    assert format_effects(ROWS) in _message_text(request)
    assert note.text in _message_text(request), "inline: the note travels in the message"
    assert QUOTE_INSTRUCTION.splitlines()[0] in (request.config.system_instruction or "")
    assert "renal impairment" not in (request.config.system_instruction or "").split("CONDITIONS")[0]


def test_no_payload_raises_classified_rather_than_returning_nothing_found(note) -> None:
    from google.genai import types

    # A run that ends without writing the output key: the fake answers with a
    # function call the agent has no tool for, so ADK produces no final answer.
    stray = types.Part(function_call=types.FunctionCall(name="no_such_tool", args={}))
    runner = AdkQuoteRunner(llm=_fake_llm([stray, stray, stray]), max_llm_calls=2)
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(note.document_id, note.text, ROWS)
    assert caught.value.reason in {
        QuoteFailure.NO_PAYLOAD, QuoteFailure.CALL_FAILED, QuoteFailure.SCHEMA_INVALID,
    }


def test_a_payload_the_schema_rejects_raises_schema_invalid(note) -> None:
    runner = AdkQuoteRunner(llm=_fake_llm([json.dumps({"quotes": []})]))
    with pytest.raises(QuoteOutputError) as caught:
        runner.run(note.document_id, note.text, ROWS)
    assert caught.value.reason in {QuoteFailure.SCHEMA_INVALID, QuoteFailure.UNPARSEABLE}


def test_the_runner_satisfies_the_port() -> None:
    assert isinstance(AdkQuoteRunner(llm=_fake_llm([])), QuoteRunner)


# --------------------------------------------------------------------------
# 2. The allowlist
# --------------------------------------------------------------------------


def test_the_allowlist_is_the_extractors_one_tool() -> None:
    """Both leaves read one note and nothing else; the constants are separate
    so each leaf names what it may reach, and this pins them equal (D66)."""
    assert QUOTE_ALLOWLIST == EXTRACTION_ALLOWLIST == ("read_note",)


def test_without_tool_fetch_the_agent_has_no_tools_at_all() -> None:
    assert build_quote_agent(tool_fetch=False).tools == []


def test_under_tool_fetch_the_agent_reaches_exactly_one_scoped_note(note) -> None:
    store = _RecordingPatientStore(notes=[note])
    agent = build_quote_agent(patient_store=store, tool_fetch=True, document_id=note.document_id)
    names = {tool.__name__ for tool in agent.tools}
    assert names == {"read_note"}
    for withheld in ("get_patient_observations", "get_patient_conditions",
                     "get_patient_document", "get_patient_notes",
                     "get_policy_context", "get_policy_value_set"):
        assert withheld not in names


def test_tool_fetch_without_a_store_or_a_document_refuses_to_build() -> None:
    with pytest.raises(ValueError, match="needs a PatientStore"):
        build_quote_agent(patient_store=None, tool_fetch=True)
    with pytest.raises(ValueError, match="needs the document_id"):
        build_quote_agent(patient_store=_RecordingPatientStore(), tool_fetch=True)


def test_the_tool_fetch_run_reads_the_note_through_the_scoped_reader(note) -> None:
    from google.genai import types

    store = _RecordingPatientStore(notes=[note])
    fetch = types.Part(
        function_call=types.FunctionCall(name="read_note", args={"document_id": note.document_id})
    )
    runner = AdkQuoteRunner(
        llm=_fake_llm([fetch, json.dumps(_payload(RENAL_VERBATIM))]),
        patient_store=store, tool_fetch=True,
    )
    result = runner.run(note.document_id, note.text, ROWS)
    assert [call.name for call in result.trace.tool_calls] == ["read_note"]
    assert ("get_document", note.document_id) in store.calls
    assert len(result.trace.metrics) == 2, "a tool round trip is two LLM calls"
    assert len(result.spans["drug-renal"]) == 1


# --------------------------------------------------------------------------
# 3. Article I holds by construction
# --------------------------------------------------------------------------


def test_the_agent_uses_single_flow_carries_no_history_and_runs_cold() -> None:
    from google.adk.flows.llm_flows.single_flow import SingleFlow

    agent = build_quote_agent(tool_fetch=False)
    assert agent.name == QUOTE_AGENT_NAME
    assert isinstance(agent._llm_flow, SingleFlow)
    assert agent.disallow_transfer_to_parent is True
    assert agent.disallow_transfer_to_peers is True
    assert agent.sub_agents == []
    assert agent.include_contents == "none"
    assert agent.generate_content_config.temperature == 0.0
    assert agent.output_schema is QuoteAnswers
    assert agent.output_key == QUOTE_OUTPUT_KEY
    assert agent.instruction == QUOTE_INSTRUCTION
    resolved = asyncio.run(agent.canonical_tools())
    assert "transfer_to_agent" not in {tool.name for tool in resolved}


def test_the_tool_fetch_instruction_is_the_same_text_with_a_read_first_preamble() -> None:
    agent = build_quote_agent(
        patient_store=_RecordingPatientStore(), tool_fetch=True, document_id="p/n.txt"
    )
    assert agent.instruction.endswith(QUOTE_INSTRUCTION)
    assert agent.instruction.startswith("First call read_note")


def test_the_run_is_bounded_by_the_shared_call_ceiling() -> None:
    runner = AdkQuoteRunner(max_llm_calls=DEFAULT_MAX_LLM_CALLS)
    assert runner._max_llm_calls == DEFAULT_MAX_LLM_CALLS
    assert (1 + REASK_ROUNDS) * DEFAULT_MAX_LLM_CALLS <= 20


# --------------------------------------------------------------------------
# 4. The re-ask
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tool_fetch", [False, True])
def test_the_reask_agent_mirrors_the_finders_flags_allowlist_and_temperature(
    note, tool_fetch
) -> None:
    store = _RecordingPatientStore(notes=[note])
    kwargs = dict(patient_store=store, tool_fetch=tool_fetch, document_id=note.document_id)
    finder = build_quote_agent(**kwargs)
    reasker = build_quote_reask_agent(**kwargs)
    assert reasker.name == QUOTE_REASK_AGENT_NAME != finder.name
    assert reasker.include_contents == finder.include_contents == "none"
    assert reasker.disallow_transfer_to_parent and reasker.disallow_transfer_to_peers
    assert reasker.generate_content_config.temperature == 0.0
    assert reasker.output_schema is VerbatimAnswers
    assert reasker.output_key == QUOTE_REASK_OUTPUT_KEY != QUOTE_OUTPUT_KEY
    assert reasker.instruction.endswith(REASK_INSTRUCTION)
    assert ("read_note" in reasker.instruction) is tool_fetch
    names = {tool.name for tool in asyncio.run(reasker.canonical_tools())}
    assert names == (set(QUOTE_ALLOWLIST) if tool_fetch else set())


def test_an_unanchorable_quote_is_re_asked_in_a_second_invocation(note) -> None:
    fake = _fake_llm([json.dumps(_payload(RENAL_PARAPHRASE)), _verbatim_answer()])
    runner = AdkQuoteRunner(llm=fake)
    result = runner.run(note.document_id, note.text, ROWS)

    assert len(fake.seen) == 2
    second = fake.seen[1]
    assert RENAL_PARAPHRASE in _message_text(second) and FIRST_PATH in _message_text(second)
    assert note.text in _message_text(second)
    assert "CONDITIONS:" not in _message_text(second), "the re-ask asks for text, not conditions"
    assert REASK_INSTRUCTION.splitlines()[0] in (second.config.system_instruction or "")

    (span,) = result.spans["drug-renal"]
    assert note.text[span.char_start:span.char_end] == RENAL_VERBATIM
    assert result.reask["recovered"] == [FIRST_PATH]
    assert result.trace.steps == ["adk_quotes", "adk_quotes_reask"]
    assert [m.purpose for m in result.trace.metrics] == ["quotes", "quotes_reask"]
    assert result.metrics is result.trace.metrics[0]
    assert runner._counter == 1


def test_a_still_bad_second_answer_keeps_the_drop_and_asks_no_third_time(note) -> None:
    fake = _fake_llm([
        json.dumps(_payload(RENAL_PARAPHRASE)),
        json.dumps({"quotes": [{"path": FIRST_PATH, "verbatim": "still not there"}]}),
    ])
    result = AdkQuoteRunner(llm=fake).run(note.document_id, note.text, ROWS)
    assert fake.cursor == 1
    assert [d["reason"] for d in result.dropped] == ["effect_quote_unanchorable"]
    assert result.reask["unrecovered"] == [FIRST_PATH]
    assert result.spans["drug-renal"] == ()


def test_the_reask_invocation_has_its_own_session(note, monkeypatch) -> None:
    seen: list[str] = []
    runner = AdkQuoteRunner(llm=_fake_llm([json.dumps(_payload(RENAL_PARAPHRASE)), _verbatim_answer()]))
    original = runner._invoke

    def spy(agent, message, output_key, session_id, purpose):
        seen.append(session_id)
        return original(agent, message, output_key, session_id, purpose)

    monkeypatch.setattr(runner, "_invoke", spy)
    runner.run(note.document_id, note.text, ROWS)
    assert len(seen) == 2 and len(set(seen)) == 2 and seen[1].startswith(seen[0])
    assert "-quotes-" in seen[0]
