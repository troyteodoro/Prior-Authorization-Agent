"""T-62 — the ADK extraction runner and its declared tools (REQ-41, 52, 53; D62).

**Spends no model call and needs no credential**, and it still tests the real
thing. ADK's `LlmAgent` accepts a `BaseLlm` instance as its `model`, so a ~20-line
fake drives the whole flow: real `FunctionTool` declarations built from real
signatures, real tool dispatch, the real `output_schema` handling, real plugin
hooks, real `usage_metadata`. What is faked is the network, and nothing else.

The pattern is copied rather than imported. ADK ships a `MockModel` in
`google/adk/cli/agent_test_runner.py`, which is a CLI conformance helper: nothing
in it is public API and importing it drags in CLI dependencies. Twenty lines here
is cheaper than a dependency on a private module.

Three things this file is really about:

1. **`build_result()` is the trust boundary.** ADK output goes through the same
   function the direct runner's does, and the assertion is equality with what that
   function produces from the same payload — so the ADK path cannot acquire a
   private way to construct a `WmEvent`.
2. **The allowlist is an object.** The extraction agent is given two tools and not
   four, and the two it is denied are the ones that would collapse T-33's two
   independent BMI readings into one.
3. **Article I is structural.** `SingleFlow`, no `transfer_to_agent`, a literal
   tool list, `include_contents="none"`, a `max_llm_calls` ceiling — each asserted
   on the constructed agent rather than promised in a docstring.
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from pa_agent.contracts import Condition, Document, Observation, ToolCall
from pa_agent.extraction import Extraction, build_result
from pa_agent.model_pin import PINNED_MODEL
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    ExtractionRunner,
    RecordedExtractionRunner,
)
from pa_agent.spans import validate
from pa_agent.index import DocumentIndex
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

from pa_agent.agent.extraction_agent import (
    AGENT_NAME,
    DEFAULT_MAX_LLM_CALLS,
    EXTRACTION_ALLOWLIST,
    OUTPUT_KEY,
    PROMPT_VERSION,
    AdkExtractionRunner,
    build_extraction_agent,
)
from pa_agent.agent.patient_tools import build_patient_tools
from pa_agent.agent.policy_tools import build_policy_tools

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"

TREE_VERSION = "ncd-100.1-jf-v1"


# --------------------------------------------------------------------------
# The fake model. Twenty lines, no network.
# --------------------------------------------------------------------------


def _fake_llm(responses: list[Any], model_name: str = "fake-under-test"):
    """A `BaseLlm` that yields prepared responses in order.

    `responses` entries are either a JSON string (a final answer) or a
    `types.Part` carrying a function call (a tool turn). Built inside a function so
    importing this module does not require ADK at collection time.
    """
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class FakeLlm(BaseLlm):
        # Named `model_name` in the signature: `model: str = model` would make the
        # name local to the class body and shadow the parameter.
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
            part = (
                types.Part(text=item) if isinstance(item, str) else item
            )
            yield LlmResponse(
                content=types.Content(role="model", parts=[part]),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=11,
                    candidates_token_count=7,
                    total_token_count=18,
                ),
            )

    return FakeLlm(prepared=list(responses))


class _RecordingPatientStore:
    """Every call recorded, nothing read from disk.

    A stub rather than the real adapter, because the claim under test is "the tool
    reached the port", and the only way to see that is to be the port.
    """

    def __init__(self, notes: list[Document] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._notes = notes or []

    def get_notes(self, patient_id: str) -> list[Document]:
        self.calls.append(("get_notes", patient_id))
        return list(self._notes)

    def get_observations(self, patient_id: str) -> list[Observation]:
        self.calls.append(("get_observations", patient_id))
        return [
            Observation(code="39156-5", value=41.3, unit="kg/m2",
                        effective_date=date(2026, 3, 1))
        ]

    def get_conditions(self, patient_id: str) -> list[Condition]:
        self.calls.append(("get_conditions", patient_id))
        return [Condition(code="44054006", system="http://snomed.info/sct",
                          clinical_status="active")]

    def get_document(self, document_id: str) -> Document:
        # T-64's widened patient-plane namespace, stubbed: the scoped note reader
        # resolves through here rather than through `get_notes`, because it holds
        # no patient id (T-66).
        self.calls.append(("get_document", document_id))
        for note in self._notes:
            if note.document_id == document_id:
                return note
        raise KeyError(document_id)


class _RaisingPatientStore:
    """Every method raises. A tool with another route to an answer will show it."""

    def get_notes(self, patient_id: str):
        raise RuntimeError("the port refused")

    def get_observations(self, patient_id: str):
        raise RuntimeError("the port refused")

    def get_conditions(self, patient_id: str):
        raise RuntimeError("the port refused")

    def get_document(self, document_id: str):
        raise RuntimeError("the port refused")


@pytest.fixture(scope="module")
def recording() -> dict:
    assert EXTRACTION_RESULTS.exists()
    return json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def e1_note() -> Document:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    patient_id = next(
        r["patient_id"] for r in manifest["notes"] if "E1" in r["cases"]
    )
    return LocalPatientStore().get_notes(patient_id)[0]


@pytest.fixture(scope="module")
def e1_payload(recording, e1_note) -> dict:
    return next(
        r["raw"] for r in recording["notes"]
        if r["document_id"] == e1_note.document_id
    )


# --------------------------------------------------------------------------
# 1. The tools reach their data through the injected port
# --------------------------------------------------------------------------


def test_each_tool_calls_the_injected_store_exactly_once() -> None:
    store = _RecordingPatientStore()
    toolset = build_patient_tools(store)

    toolset.tools["get_patient_notes"]("p1")
    toolset.tools["get_patient_observations"]("p1")
    toolset.tools["get_patient_conditions"]("p1")

    assert store.calls == [
        ("get_notes", "p1"),
        ("get_observations", "p1"),
        ("get_conditions", "p1"),
    ], "arguments must be forwarded verbatim and each tool must make one call"


def test_the_policy_tools_call_the_injected_policy_store() -> None:
    store = LocalPolicyStore()
    toolset = build_policy_tools(store)

    context = toolset.tools["get_policy_context"](TREE_VERSION)
    assert context["policy_version_id"] == TREE_VERSION
    assert context["decision_expression"] == store.get_tree(
        TREE_VERSION
    ).decision_expression
    assert [c["id"] for c in context["criteria"]] == ["a", "b", "c1", "c2", "c3", "c4", "c5"]

    value_set_id = store.get_tree(TREE_VERSION).criterion("b").require("value_set_id")
    codes = toolset.tools["get_policy_value_set"](value_set_id)
    assert codes["codes"] == sorted(store.get_value_set(value_set_id))


def test_a_tool_has_no_route_to_an_answer_when_the_port_refuses() -> None:
    """The counterexample that makes the claim above mean something. If a tool
    could reach a file, a raising store would not stop it."""
    toolset = build_patient_tools(_RaisingPatientStore())
    for name in ("get_patient_notes", "get_patient_observations", "get_patient_conditions"):
        with pytest.raises(RuntimeError, match="the port refused"):
            toolset.tools[name]("p1")


def test_a_missing_document_raises_rather_than_returning_empty_text() -> None:
    """D31's lesson at tool scope: an empty string is a note that documents
    nothing, which is a clinical claim. A lookup failure must stay one."""
    store = _RecordingPatientStore(notes=[])
    toolset = build_patient_tools(store)
    with pytest.raises(KeyError):
        toolset.tools["get_patient_document"]("p1", "p1/chart_note.txt")


def test_the_notes_tool_returns_ids_and_not_text(e1_note) -> None:
    """Listing is not reading. A model that can enumerate a patient's notes does
    not thereby get every note in one response, and the workflow stays the thing
    that decides which documents are in scope."""
    toolset = build_patient_tools(_RecordingPatientStore(notes=[e1_note]))
    listed = toolset.tools["get_patient_notes"]("p1")
    assert listed == {
        "notes": [
            {"document_id": e1_note.document_id, "characters": len(e1_note.text)}
        ],
        "total": 1,
    }
    body = json.dumps(listed)
    assert e1_note.text[:60] not in body


def test_the_structured_tools_select_nothing_and_hide_no_field() -> None:
    """D39's rule survives T-65's cap, and the distinction matters.

    D39 forbids a *semantic* filter in a tool — a BMI-only or active-only read is
    a filter applied twice, free to disagree with the one criterion (a) applies.
    T-65 caps the row *count*, which is not a predicate: it drops the oldest rows
    and says so, and nothing downstream reads the result. So no code is filtered
    out here and `clinical_status` is reported rather than used.
    """
    store = _RecordingPatientStore()
    toolset = build_patient_tools(store)
    observations = toolset.tools["get_patient_observations"]("p1")["observations"]
    conditions = toolset.tools["get_patient_conditions"]("p1")["conditions"]
    assert len(observations) == 1 and observations[0]["code"] == "39156-5"
    assert conditions[0]["clinical_status"] == "active", (
        "clinical_status must be reported, not used to filter here"
    )


# --------------------------------------------------------------------------
# 2. The tools open no file and name no path
# --------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


@pytest.mark.parametrize("module", ["patient_tools.py", "policy_tools.py"])
def test_a_tool_module_opens_no_file_and_names_no_path(module) -> None:
    """REQ-41, on the AST and on the calls. The ports are what make a production
    database a second adapter rather than a rewrite (D25), and a tool that reaches
    around them is the thing that would break that."""
    path = REPO_ROOT / "pa_agent" / "agent" / module
    imported = _imports(path)
    for forbidden in ("pathlib", "os", "sqlite3", "psycopg", "io", "shutil"):
        assert forbidden not in imported, f"{module} imports {forbidden}"

    tree = ast.parse(path.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "open" not in called, f"{module} calls open()"

    source = path.read_text(encoding="utf-8")
    for forbidden in ("read_text(", "json.load", "Path("):
        assert forbidden not in source, f"{module} contains {forbidden}"


#: The one module allowed to hold both toolsets, named so a second is a visible
#: diff rather than a silent addition (T-61, D63).
_BOTH_PLANES = {"pa_agent/agent/retrieval_agent.py"}


def test_only_the_declared_module_holds_both_planes_toolsets() -> None:
    """Article VI, and REQ-41's closing line: a module able to read both planes
    would have to hold both handles. Two modules is what makes holding both a
    **visible act** rather than an accident — and the count is pinned at one.

    T-61's gatherer is that one, and it is legal for the reason Article VI states
    itself: *"The criterion text does cross. The policy corpus and its index do
    not."* Its policy tools return compiled criteria — ids, labels, constants, the
    decision expression — and no document, no span and no corpus text. The test
    below asserts that, and it is the assertion that carries the article's actual
    content. This one only counts handles.

    `pa_agent/workflow.py` holds both *store* handles for the same reason and
    T-18's gate says so explicitly. Holding both is permitted; holding both by
    accident is what these two tests prevent.
    """
    both = []
    for path in (REPO_ROOT / "pa_agent").rglob("*.py"):
        names = {n.rsplit(".", 1)[-1] for n in _imports(path)}
        if {"patient_tools", "policy_tools"} <= names:
            both.append(str(path.relative_to(REPO_ROOT)))
    assert set(both) == _BOTH_PLANES, (
        f"modules holding both toolsets: {sorted(both)}; expected exactly "
        f"{sorted(_BOTH_PLANES)}. A new one needs an Article VI argument in a "
        "decisions entry, not a passing suite."
    )


def test_no_model_facing_policy_tool_can_reach_the_corpus() -> None:
    """**This is Article VI's actual content**, and the reason the exception above
    is an exception rather than a breach.

    The article's smaller, true claim is that the compiled criterion crosses the
    boundary and the policy corpus does not. So the test is not "does a module
    hold both handles" — it is "can a model holding both reach the source
    documents". It cannot: `PolicyStore.get_document` is not wrapped as a tool,
    and nothing in the policy toolset returns document text or a span.

    Without this, a later commit could add `get_policy_document` to the toolset,
    the count above would still read one, and the corpus would be in a context
    window alongside patient data.
    """
    from pa_agent.agent.policy_tools import build_policy_tools

    toolset = build_policy_tools(LocalPolicyStore())
    assert set(toolset.names) == {"get_policy_context", "get_policy_value_set"}

    source = (REPO_ROOT / "pa_agent" / "agent" / "policy_tools.py").read_text(
        encoding="utf-8"
    )
    for corpus_reach in ("get_document", "document_id", "char_start", ".text"):
        assert corpus_reach not in source, (
            f"policy_tools.py mentions {corpus_reach!r}; the corpus does not "
            "cross the plane boundary (Art. VI)"
        )

    # And what it does return is the compiled criterion, which the article
    # explicitly permits crossing.
    context = toolset.tools["get_policy_context"](TREE_VERSION)
    assert set(context) == {
        "policy_version_id", "title", "jurisdiction", "decision_expression",
        "criteria", "criteria_total", "criteria_truncated",
    }
    assert context["criteria_truncated"] is False
    for criterion in context["criteria"]:
        assert set(criterion) == {"id", "label", "scoped_to", "constants"}


def test_the_patient_tools_import_no_policy_and_the_policy_tools_no_patient() -> None:
    patient = _imports(REPO_ROOT / "pa_agent" / "agent" / "patient_tools.py")
    policy = _imports(REPO_ROOT / "pa_agent" / "agent" / "policy_tools.py")
    assert not any("policy" in name for name in patient), patient
    assert not any("patient" in name for name in policy), policy


# --------------------------------------------------------------------------
# 3. REQ-53: the allowlist is narrower than the toolset, on purpose
# --------------------------------------------------------------------------


def test_the_extraction_agent_reaches_one_document_and_nothing_else(e1_note) -> None:
    """The single most consequential line in this task, narrowed by T-66.

    Handing extraction `get_patient_observations` would give the model the
    structured BMI while asking it for the note's. T-33 and T-60 exist because
    those are two independent readings that can disagree; E10b is the case where
    they disagree by 1.6 across 35.0. A model shown both has no reason to
    disagree — and the system would keep passing every test it has, because the
    tests compare the two values and would now find them equal.

    T-62 stated that as a list of names withheld. T-66 makes it the absence of a
    reachable second document: the one declared tool is scoped in Python to the
    note under review (D66).
    """
    store = _RecordingPatientStore(notes=[e1_note])
    agent = build_extraction_agent(
        patient_store=store, tool_fetch=True, document_id=e1_note.document_id
    )
    names = {tool.__name__ for tool in agent.tools}
    assert names == set(EXTRACTION_ALLOWLIST) == {"read_note"}
    for withheld in (
        "get_patient_observations",
        "get_patient_conditions",
        "get_patient_document",
        "get_patient_notes",
    ):
        assert withheld not in names


def test_the_patient_toolset_declares_far_more_than_the_extractor_may_see() -> None:
    """So the previous assertion is about an allowlist and not about a toolset
    that happens to be small. The full patient plane is four tools; the extractor
    sees a fifth that reaches exactly one document."""
    declared = set(build_patient_tools(_RecordingPatientStore()).names)
    assert declared == {
        "get_patient_notes",
        "get_patient_document",
        "get_patient_observations",
        "get_patient_conditions",
    }
    assert not declared & set(EXTRACTION_ALLOWLIST), (
        "the extractor's tool must not be one of the patient-plane tools; if it "
        "is, someone re-widened it and the bundle route is back (T-66)"
    )


def test_the_extraction_agent_is_given_no_policy_tool(e1_note) -> None:
    """A note extractor that can read the policy's thresholds is a threshold
    leaking into the model's judgment."""
    store = _RecordingPatientStore(notes=[e1_note])
    agent = build_extraction_agent(
        patient_store=store, tool_fetch=True, document_id=e1_note.document_id
    )
    names = {tool.__name__ for tool in agent.tools}
    assert not names & {"get_policy_context", "get_policy_value_set"}


def test_an_allowlist_naming_a_tool_that_does_not_exist_raises() -> None:
    """REQ-45 says the model may not invent tools. Neither may a caller: a typo
    that silently produced an agent with fewer tools than intended would be a
    capability quietly removed."""
    toolset = build_patient_tools(_RecordingPatientStore())
    with pytest.raises(KeyError, match="no such tool"):
        toolset.allowlist("get_patient_notes", "get_patient_horoscope")


def test_without_tool_fetch_the_agent_has_no_tools_at_all() -> None:
    agent = build_extraction_agent(tool_fetch=False)
    assert agent.tools == []


def test_tool_fetch_without_a_store_refuses_to_build() -> None:
    with pytest.raises(ValueError, match="needs a PatientStore"):
        build_extraction_agent(patient_store=None, tool_fetch=True)


def test_tool_fetch_without_a_document_id_refuses_to_build() -> None:
    """T-66. The reader's scope is fixed before the run; there is no fallback
    that derives it from whatever id the model asks for, because that fallback
    was `_patient_of` (D66)."""
    with pytest.raises(ValueError, match="needs the document_id"):
        build_extraction_agent(
            patient_store=_RecordingPatientStore(), tool_fetch=True
        )


# --------------------------------------------------------------------------
# 4. Article I holds by construction
# --------------------------------------------------------------------------


def test_the_agent_uses_single_flow_so_it_cannot_transfer_control() -> None:
    """Stronger than "the model did not transfer": with both
    `disallow_transfer_to_*` set and no `sub_agents`, ADK's `_llm_flow` selects
    `SingleFlow` and the `transfer_to_agent` tool is never injected. There is no
    mechanism to misuse."""
    from google.adk.flows.llm_flows.single_flow import SingleFlow

    agent = build_extraction_agent(tool_fetch=False)
    assert isinstance(agent._llm_flow, SingleFlow)
    assert agent.disallow_transfer_to_parent is True
    assert agent.disallow_transfer_to_peers is True
    assert agent.sub_agents == []

    resolved = asyncio.run(agent.canonical_tools())
    assert "transfer_to_agent" not in {tool.name for tool in resolved}


def test_the_agent_carries_no_history_between_notes() -> None:
    """D17's finding: a shared session carries note N-1 into note N. One shot per
    note, and nothing to steer with."""
    agent = build_extraction_agent(tool_fetch=False)
    assert agent.include_contents == "none"


def test_the_agent_runs_at_temperature_zero() -> None:
    """Article II's test: if the same input could produce a different verdict on a
    second run, the computation is in the wrong place."""
    agent = build_extraction_agent(tool_fetch=False)
    assert agent.generate_content_config.temperature == 0.0


def test_the_agent_names_the_pinned_model_and_never_a_literal() -> None:
    agent = build_extraction_agent(tool_fetch=False)
    assert agent.model == PINNED_MODEL


def test_the_agent_carries_the_measured_instruction_verbatim() -> None:
    """D19 and D45 were measured on this exact formulation with no tuning spent.
    Paraphrasing it for the ADK would restart the prompt's history at zero."""
    from pa_agent.extraction import INSTRUCTION

    assert build_extraction_agent(tool_fetch=False).instruction == INSTRUCTION


def test_the_tool_fetch_variant_only_prepends_a_retrieval_step(e1_note) -> None:
    """The extraction half of the prompt is still byte-identical, so T-63's
    comparison isolates the retrieval change rather than a rewrite."""
    from pa_agent.extraction import INSTRUCTION

    agent = build_extraction_agent(
        patient_store=_RecordingPatientStore(notes=[e1_note]),
        tool_fetch=True,
        document_id=e1_note.document_id,
    )
    assert agent.instruction.endswith(INSTRUCTION)
    assert "read_note" in agent.instruction


def test_the_run_is_bounded_by_a_call_ceiling() -> None:
    """REQ-46. `max_llm_calls` is ADK's only budget knob and it counts LLM calls,
    so one tool round trip costs two — the ceiling has to leave room for a tool
    turn and no room for a loop."""
    assert DEFAULT_MAX_LLM_CALLS >= 2
    assert DEFAULT_MAX_LLM_CALLS <= 10, (
        "a ceiling this high stops bounding anything a loop would do"
    )
    runner = AdkExtractionRunner(max_llm_calls=DEFAULT_MAX_LLM_CALLS)
    assert runner._max_llm_calls == DEFAULT_MAX_LLM_CALLS


def test_the_declaration_the_model_sees_hides_the_injected_store(e1_note) -> None:
    """The closure is the boundary. ADK builds a declaration from
    `inspect.signature`, and a closure's captured cells never appear in one — so
    the model sees `read_note(document_id)` and has no way to name, reach, or
    substitute the store behind it.

    T-66 leans harder on this than T-62 did: the *scope* is a captured cell too,
    so the permitted document id is not in the declaration either. The model can
    echo the id it was given and cannot discover another one."""
    store = _RecordingPatientStore(notes=[e1_note])
    agent = build_extraction_agent(
        patient_store=store, tool_fetch=True, document_id=e1_note.document_id
    )
    resolved = asyncio.run(agent.canonical_tools())
    by_name = {tool.name: tool for tool in resolved}
    declaration = by_name["read_note"]._get_declaration()
    rendered = str(declaration)
    assert "document_id" in rendered
    for leaked in ("store", "patient_store", "_RecordingPatientStore", "toolset",
                   "permitted"):
        assert leaked not in rendered, f"the declaration leaks {leaked!r}"


# --------------------------------------------------------------------------
# 5. ADK output goes through build_result(), which is the trust boundary
# --------------------------------------------------------------------------


def _run_with_fake(payload_or_parts, note: Document, **kwargs) -> Any:
    runner = AdkExtractionRunner(llm=_fake_llm(payload_or_parts), **kwargs)
    return runner.run(note.document_id, note.text)


def test_adk_output_produces_exactly_what_build_result_produces(
    e1_note, e1_payload
) -> None:
    """REQ-52's assertion, and the one that makes "ADK output is untrusted" true
    rather than aspirational: the ADK path has no private route to a `WmEvent`."""
    through_adk = _run_with_fake([json.dumps(e1_payload)], e1_note)
    directly = build_result(e1_note.document_id, e1_note.text, e1_payload)

    assert len(through_adk.events) == len(directly.events) > 0
    assert [e.model_dump(mode="json") for e in through_adk.events] == [
        e.model_dump(mode="json") for e in directly.events
    ]
    assert through_adk.current_bmi == directly.current_bmi
    assert through_adk.dropped == directly.dropped == []


def test_every_span_the_adk_path_produced_still_slices_back(
    e1_note, e1_payload
) -> None:
    """Article III on the ADK path specifically. Spans are located by
    `pa_agent.anchor` from the model's verbatim quote, never taken from the
    model's offsets — D19 found zero of eighty offset pairs usable."""
    result = _run_with_fake([json.dumps(e1_payload)], e1_note)
    index = DocumentIndex()
    index.add(e1_note)
    checked = 0
    for event in result.events:
        for span in (event.span, event.bmi_span, event.diet_span, event.activity_span):
            if span is None:
                continue
            assert validate(span, index), "an ADK-produced span does not slice back"
            checked += 1
    assert checked > 0, "the gate is vacuous"


def test_a_fabricated_quote_never_becomes_evidence(e1_note) -> None:
    """The model returns a quote that is not in the note. It is counted in
    `dropped[]` and produces no event — which is the failure Article III exists to
    catch, caught on string comparison rather than on judgment."""
    payload = {
        "wm_events": [
            {
                "date": "2025-01-09",
                "quote": "The patient completed a nine-month supervised program.",
                "char_start": 0,
                "char_end": 53,
            }
        ],
        "program_assertions": [],
    }
    result = _run_with_fake([json.dumps(payload)], e1_note)
    assert result.events == []
    assert len(result.dropped) == 1
    assert result.dropped[0]["reason"] == "event_quote_unanchorable"


def test_a_fabricated_bmi_quote_demotes_the_bmi_and_keeps_the_event(
    e1_note, e1_payload
) -> None:
    """D15's rule, reached through the ADK path: a BMI nobody can cite is not a
    documented BMI. Dropping the value rather than the event keeps c4 honest and
    c3 intact."""
    payload = json.loads(json.dumps(e1_payload))
    payload["wm_events"][0]["bmi"] = 99.9
    payload["wm_events"][0]["bmi_quote"] = "BMI 99.9"

    result = _run_with_fake([json.dumps(payload)], e1_note)
    assert len(result.events) == len(e1_payload["wm_events"])
    first = result.events[0]
    assert first.bmi is None, "an uncitable BMI must not survive"
    assert first.bmi_span is None
    assert any(d["reason"] == "bmi_quote_unanchorable" for d in result.dropped)


# --------------------------------------------------------------------------
# 6. Malformed output raises and never becomes an empty extraction
# --------------------------------------------------------------------------


def test_unparseable_output_raises(e1_note) -> None:
    with pytest.raises(ExtractionOutputError) as caught:
        _run_with_fake(["this is not JSON at all"], e1_note)
    assert caught.value.reason in (
        ExtractionFailure.UNPARSEABLE,
        ExtractionFailure.NO_PAYLOAD,
        ExtractionFailure.CALL_FAILED,
    )


def test_a_payload_the_schema_rejects_raises(e1_note) -> None:
    with pytest.raises(ExtractionOutputError) as caught:
        _run_with_fake(['{"wm_events": "not a list"}'], e1_note)
    assert caught.value.reason in (
        ExtractionFailure.SCHEMA_INVALID,
        ExtractionFailure.NO_PAYLOAD,
        ExtractionFailure.CALL_FAILED,
    )


def test_no_failure_mode_returns_a_zero_event_extraction(e1_note) -> None:
    """The trap `spike/spike_001/run.py` documents in writing: a note that
    extracts nothing leaks no REQ-9 traps, so it scores perfect exclusion, and
    contributes no false positives, so it scores perfect precision. A transport
    failure would read as flawless extraction.

    Every malformed shape must raise. An empty *and valid* payload is the one
    thing that may come back as a result, because a model that read the note and
    found no encounter is E7, which is an answer.
    """
    for broken in ("", "null", "[]", '{"wm_events": 3}', "not json"):
        with pytest.raises(ExtractionOutputError):
            _run_with_fake([broken], e1_note)

    honest = _run_with_fake(
        ['{"wm_events": [], "program_assertions": []}'], e1_note
    )
    assert honest.events == [] and honest.assertions == []


def test_an_absent_optional_field_still_round_trips(e1_note) -> None:
    """ADK writes session state via `model_dump(exclude_none=True)`, so a `None`
    field is *absent* rather than null. Pydantic's defaults restore it — asserted
    because ten of the eleven recorded notes have `current_bmi: None` and a
    KeyError here would look like a model failure."""
    result = _run_with_fake(
        ['{"wm_events": [], "program_assertions": []}'], e1_note
    )
    assert result.current_bmi is None
    assert result.current_bmi_span is None


# --------------------------------------------------------------------------
# 7. The runner is a drop-in for the port, and metrics are captured
# --------------------------------------------------------------------------


def test_the_adk_runner_satisfies_the_extraction_port() -> None:
    assert isinstance(AdkExtractionRunner(), ExtractionRunner)
    assert isinstance(RecordedExtractionRunner({}), ExtractionRunner)
    assert AdkExtractionRunner().name == "adk"


def test_the_adk_runner_and_the_recorded_runner_agree_on_the_same_payload(
    e1_note, e1_payload, recording
) -> None:
    """Point 8 of T-62's exit, and the property that makes the port worth having:
    two implementations, one payload, one answer. If these diverged, every
    downstream verdict would depend on which runner was wired in."""
    recorded = RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )
    from_recorded = recorded.run(e1_note.document_id, e1_note.text)
    from_adk = _run_with_fake([json.dumps(e1_payload)], e1_note)

    assert [e.model_dump(mode="json") for e in from_adk.events] == [
        e.model_dump(mode="json") for e in from_recorded.events
    ]


def test_the_run_records_tokens_and_a_termination_reason(e1_note, e1_payload) -> None:
    """Article X: recorded from the first model call, never estimated."""
    result = _run_with_fake([json.dumps(e1_payload)], e1_note)
    trace = result.trace
    assert trace is not None
    assert trace.runner_name == "adk"
    assert trace.document_id == e1_note.document_id
    assert trace.prompt_version == PROMPT_VERSION
    assert trace.termination_reason and "ok" in trace.termination_reason
    assert trace.attempts == 1

    assert trace.metrics, "no CallMetrics recorded; the plugin hook did not fire"
    metric = trace.metrics[0]
    assert metric.purpose == "extraction"
    assert metric.input_tokens == 11 and metric.output_tokens == 7
    assert result.metrics is metric, (
        "the determination's counter reads the same measurement (REQ-22)"
    )


def test_the_tool_call_trace_is_recorded_in_order(e1_note, e1_payload) -> None:
    """REQ-49. The model calls `read_note`, the tool answers, and the model
    returns the payload — two LLM turns and one tool call, in that order."""
    from google.genai import types

    store = _RecordingPatientStore(notes=[e1_note])
    fetch = types.Part(
        function_call=types.FunctionCall(
            name="read_note",
            args={"document_id": e1_note.document_id},
        )
    )
    runner = AdkExtractionRunner(
        llm=_fake_llm([fetch, json.dumps(e1_payload)]),
        patient_store=store,
        tool_fetch=True,
    )
    result = runner.run(e1_note.document_id, e1_note.text)

    names = [call.name for call in result.trace.tool_calls]
    assert names == ["read_note"], names
    assert result.trace.tool_calls[0].ok is True
    assert ("get_document", e1_note.document_id) in store.calls, (
        "the tool reached the injected port, not a file"
    )
    assert len(result.events) > 0, "the payload still came back through build_result"


def test_a_tool_call_trace_records_a_digest_and_never_the_arguments(
    e1_note, e1_payload
) -> None:
    """Article VI. A trace holding `patient_id` verbatim is patient data sitting in
    an instrumentation record that will end up in a report. A digest still proves
    two calls differed, which is the question a trace answers."""
    from google.genai import types

    store = _RecordingPatientStore(notes=[e1_note])
    fetch = types.Part(
        function_call=types.FunctionCall(
            name="read_note",
            args={"document_id": e1_note.document_id},
        )
    )
    runner = AdkExtractionRunner(
        llm=_fake_llm([fetch, json.dumps(e1_payload)]),
        patient_store=store,
        tool_fetch=True,
    )
    result = runner.run(e1_note.document_id, e1_note.text)
    call = result.trace.tool_calls[0]

    assert e1_note.document_id not in call.arguments_digest
    assert "/" not in call.arguments_digest, "the digest still looks like a path"
    assert len(call.arguments_digest) == 16
    assert call.arguments_digest == ToolCall.digest(
        {"document_id": e1_note.document_id}
    ), "the digest must be reproducible, or it proves nothing about two calls"


def test_a_fresh_session_per_note_so_one_note_cannot_see_another(
    e1_note, e1_payload
) -> None:
    """D17's finding, asserted structurally: the session id differs per call, so
    `run_debug`'s shared default cannot carry note N-1 into note N."""
    runner = AdkExtractionRunner(
        llm=_fake_llm([json.dumps(e1_payload), json.dumps(e1_payload)])
    )
    runner.run(e1_note.document_id, e1_note.text)
    first = runner._counter
    runner.run(e1_note.document_id, e1_note.text)
    assert runner._counter == first + 1, (
        "the session counter did not advance; two notes would share a session"
    )


def test_the_recorded_runner_carries_no_trace(recording, e1_note) -> None:
    """`None` rather than an empty trace, because a replay made no calls to trace
    and an empty one would imply a run that recorded nothing."""
    recorded = RecordedExtractionRunner.from_records(recording["notes"])
    assert recorded.run(e1_note.document_id, e1_note.text).trace is None


# --------------------------------------------------------------------------
# 8. The boundary: the ADK stays in pa_agent/agent/
# --------------------------------------------------------------------------


def test_no_deterministic_module_imports_the_adk() -> None:
    """The boundary `pa_agent/__init__.py` exists to hold, checked across the
    package rather than module by module. `pa_agent/agent/` is the declared
    exception — that is the whole point of it being a directory (D16)."""
    offenders = []
    for path in (REPO_ROOT / "pa_agent").rglob("*.py"):
        if path.parent.name == "agent":
            continue
        for name in _imports(path):
            if name.startswith("google."):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
    assert offenders == [], offenders


def test_importing_the_cli_does_not_pull_in_the_adk() -> None:
    """`--extraction adk` imports the runner inside the branch that needs it, so a
    request that answers E3 with no model at all does not load the ADK to do it."""
    import subprocess
    import sys

    probe = (
        "import sys; import pa_agent.cli; "
        "assert 'google.adk' not in sys.modules, 'the CLI pulled in the ADK'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr


def test_the_output_key_and_agent_name_are_stable_identifiers() -> None:
    """Both are read back out of session state and out of `/list-apps`, so a
    rename is a breaking change to `scripts/check_skeleton.py` and to every
    recorded trace."""
    assert AGENT_NAME.isidentifier(), "ADK's BaseNode validates str.isidentifier()"
    assert OUTPUT_KEY == "extraction"
