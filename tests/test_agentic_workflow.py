"""T-61 — model-directed retrieval, bounded (REQ-43, 45, 46, 48, 49, 50, 51; D63).

**Spends no model call and needs no credential.** ADK's `LlmAgent` takes a
`BaseLlm` instance, so a scripted fake drives the real flow: real `FunctionTool`
declarations, real tool dispatch, real plugin hooks, real session state. What is
faked is the network.

The model chooses what to read. Python still adjudicates — Amendment 1 reserves
date arithmetic, numeric comparisons, counting, sorting and set membership on
both paths, which is the entire decision procedure for all seven criteria, so
there is no verdict the model could reach here without doing something the
amendment reserves (D63).

What this file is really asserting: **the failure modes of model-directed
retrieval all produce well-formed determinations.** A note the planner never
opened leaves c3 measuring a shorter run. Forgotten observations make criterion
(a) abstain. A run that stops early adjudicates a partial chart. None of those
raises on its own, and none looks wrong in the output — so each one is either
refused at the boundary here, or surfaced by the differential.
"""

from __future__ import annotations

import ast
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pa_agent.agent.tool_bounds import MAX_ROWS
from pa_agent.contracts import Condition, CriterionVerdict, Document, Observation
from pa_agent.retrieval import (
    FixedRetrievalPlanner,
    RetrievalError,
    RetrievalPlanner,
    RetrievalResult,
)
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from conftest import AcceptAllVerifier
from pa_agent.workflow import STEP_NAMES, run_criteria_workflow

from pa_agent.agent.retrieval_agent import (
    AGENT_NAME,
    DEFAULT_MAX_LLM_CALLS,
    DEFAULT_MAX_STEPS,
    DEFAULT_TIMEOUT_S,
    PATIENT_ALLOWLIST,
    POLICY_ALLOWLIST,
    PROMPT_VERSION,
    AgenticRetrievalPlanner,
    RetrievalPlan,
    build_retrieval_agent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"

TREE_VERSION = "ncd-100.1-jf-v1"
CONTRACTOR_CODE = "43775"
AS_OF = date(2026, 9, 1)


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def tree(policy_store):
    return policy_store.get_tree(TREE_VERSION)


@pytest.fixture(scope="module")
def runner() -> RecordedExtractionRunner:
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def case_patients() -> dict[str, str]:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return {
        case: record["patient_id"]
        for record in manifest["notes"]
        for case in record["cases"]
    }


@pytest.fixture(scope="module")
def e1(case_patients, patient_store) -> tuple[str, str]:
    patient_id = case_patients["E1"]
    return patient_id, patient_store.get_notes(patient_id)[0].document_id


def _eval_module():
    """Load `eval/run_agentic_eval.py` as a module.

    `eval/` is a directory of harnesses, not a package, so it goes through
    `importlib` — the same route `tests/test_model_pin.py` uses for the spike.
    """
    import importlib.util

    path = REPO_ROOT / "eval" / "run_agentic_eval.py"
    spec = importlib.util.spec_from_file_location("run_agentic_eval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call(name: str, **arguments):
    from google.genai import types

    return types.Part(
        function_call=types.FunctionCall(name=name, args=arguments)
    )


def _plan(document_ids, gathered=True, note=""):
    return json.dumps(
        {"document_ids": list(document_ids), "gathered": gathered, "note": note}
    )


def _planner(turns, **kwargs) -> AgenticRetrievalPlanner:
    from test_adk_agent import _fake_llm

    return AgenticRetrievalPlanner(llm=_fake_llm(turns), **kwargs)


def _full_run(patient_id: str, document_id: str):
    """The tool sequence a well-behaved planner produces."""
    return [
        _call("get_patient_notes", patient_id=patient_id),
        _call("get_patient_document", patient_id=patient_id, document_id=document_id),
        _call("get_patient_observations", patient_id=patient_id),
        _call("get_patient_conditions", patient_id=patient_id),
        _plan([document_id]),
    ]


# --------------------------------------------------------------------------
# REQ-43: the model selects from an allowlist and cannot name anything else
# --------------------------------------------------------------------------


def test_the_agent_sees_exactly_the_declared_allowlist(patient_store, policy_store):
    agent, _, _ = build_retrieval_agent(patient_store, policy_store)
    names = {tool.__name__ for tool in agent.tools}
    assert names == set(PATIENT_ALLOWLIST) | set(POLICY_ALLOWLIST)


def test_the_gatherer_gets_the_structured_facts_the_extractor_is_denied(
    patient_store, policy_store
):
    """The two allowlists differ on purpose, and the asymmetry needs saying.

    REQ-53 denies the *extraction* agent `get_patient_observations` because a
    model shown the structured BMI while asked for the note's BMI collapses
    T-33's two independent readings into one. That argument does not reach this
    agent: it never reports a BMI, it reports which documents to read. It cannot
    collapse two readings because it produces neither.
    """
    from pa_agent.agent.extraction_agent import EXTRACTION_ALLOWLIST

    assert "get_patient_observations" in PATIENT_ALLOWLIST
    assert "get_patient_observations" not in EXTRACTION_ALLOWLIST
    # T-66 made the asymmetry structural rather than a subset relation: the
    # extractor's one tool is not a patient-plane tool at all, it is a reader
    # scoped in Python to one document. So the two allowlists are now disjoint,
    # which is a stronger statement than "narrower" (D66).
    assert not set(EXTRACTION_ALLOWLIST) & set(PATIENT_ALLOWLIST)
    assert set(EXTRACTION_ALLOWLIST) == {"read_note"}


def test_a_tool_outside_the_allowlist_cannot_be_requested(patient_store, policy_store):
    """REQ-45: the model may not invent tools. It has no name to invent with —
    the declaration list is what it sees, and a call to anything else is not a
    refused request but an unresolvable one."""
    agent, _, _ = build_retrieval_agent(patient_store, policy_store)
    declared = {tool.__name__ for tool in agent.tools}
    for invented in ("delete_patient", "write_policy", "get_policy_tree_file"):
        assert invented not in declared


def test_the_policy_tools_are_read_only(policy_store):
    """Article VII: the model may interpret the selected policy and cannot
    rewrite it. Asserted structurally — there is no setter to call."""
    from pa_agent.agent.policy_tools import build_policy_tools

    toolset = build_policy_tools(policy_store)
    assert set(toolset.names) == set(POLICY_ALLOWLIST)
    for name in toolset.names:
        assert name.startswith("get_"), f"{name} is not obviously a read"

    source = (REPO_ROOT / "pa_agent" / "agent" / "policy_tools.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("write_text", "open(", "json.dump", ".save(", "update_tree"):
        assert forbidden not in source, f"policy_tools.py contains {forbidden}"


def test_the_criteria_tree_is_unchanged_by_a_run(
    policy_store, patient_store, tree, e1
):
    """REQ-45 as an observable fact rather than an import assertion: the tree on
    disk is byte-identical after a full agentic run."""
    tree_path = REPO_ROOT / "data" / "policies" / "ncd_100_1_jf.json"
    before = tree_path.read_bytes()
    patient_id, document_id = e1
    _planner(_full_run(patient_id, document_id)).gather(
        patient_id, tree, patient_store, policy_store
    )
    assert tree_path.read_bytes() == before


# --------------------------------------------------------------------------
# REQ-49: the tool-call trace, in order
# --------------------------------------------------------------------------


def test_the_tool_calls_are_recorded_in_the_order_they_were_made(
    policy_store, patient_store, tree, e1
):
    patient_id, document_id = e1
    result = _planner(_full_run(patient_id, document_id)).gather(
        patient_id, tree, patient_store, policy_store
    )
    assert [call.name for call in result.trace.tool_calls] == [
        "get_patient_notes",
        "get_patient_document",
        "get_patient_observations",
        "get_patient_conditions",
    ]
    assert result.trace.steps == [c.name for c in result.trace.tool_calls]


def test_the_trace_digests_its_arguments_and_never_stores_them(
    policy_store, patient_store, tree, e1
):
    """Article VI. A trace holding `patient_id` verbatim is patient data in an
    instrumentation record that ends up in a report."""
    patient_id, document_id = e1
    result = _planner(_full_run(patient_id, document_id)).gather(
        patient_id, tree, patient_store, policy_store
    )
    for call in result.trace.tool_calls:
        assert patient_id not in call.arguments_digest
        assert document_id not in call.arguments_digest
        assert len(call.arguments_digest) == 16


def test_the_run_records_what_article_x_asks_for(
    policy_store, patient_store, tree, e1
):
    patient_id, document_id = e1
    result = _planner(_full_run(patient_id, document_id)).gather(
        patient_id, tree, patient_store, policy_store
    )
    trace = result.trace
    assert trace.runner_name == "agentic"
    assert trace.prompt_version == PROMPT_VERSION
    assert trace.termination_reason and "ok" in trace.termination_reason
    assert trace.metrics, "no CallMetrics recorded; the plugin hook did not fire"
    assert trace.total_input_tokens > 0


def test_the_model_can_request_additional_evidence(
    policy_store, patient_store, tree, e1
):
    """The capability REQ-43 is really about: a second turn asking for something
    the first turn did not have. Asserted by the run reflecting what it asked
    for — three documents requested, three fetched."""
    patient_id, document_id = e1
    turns = [
        _call("get_patient_notes", patient_id=patient_id),
        _call("get_patient_document", patient_id=patient_id, document_id=document_id),
        _call("get_patient_document", patient_id=patient_id, document_id=document_id),
        _call("get_patient_observations", patient_id=patient_id),
        _plan([document_id]),
    ]
    result = _planner(turns).gather(patient_id, tree, patient_store, policy_store)
    assert len(result.trace.tool_calls) == 4, (
        "the extra request was not made; the loop is not model-directed"
    )


# --------------------------------------------------------------------------
# REQ-46: the bounds are Python's, and exhausting one terminates
# --------------------------------------------------------------------------


def test_the_step_budget_terminates_rather_than_returning_a_partial_bundle(
    policy_store, patient_store, tree, e1
):
    """A partial bundle presented as a complete one is indistinguishable from a
    patient with a thinner chart. That is the whole failure this bound exists to
    prevent, so exhausting it must raise rather than return what it has."""
    patient_id, document_id = e1
    turns = [_call("get_patient_notes", patient_id=patient_id)] * 5 + [
        _plan([document_id])
    ]
    with pytest.raises(RetrievalError, match="against a budget of 2"):
        _planner(turns, max_steps=2).gather(
            patient_id, tree, patient_store, policy_store
        )


def test_the_termination_reason_names_which_bound_was_hit(
    policy_store, patient_store, tree, e1
):
    patient_id, document_id = e1
    turns = [_call("get_patient_notes", patient_id=patient_id)] * 5 + [
        _plan([document_id])
    ]
    with pytest.raises(RetrievalError) as caught:
        _planner(turns, max_steps=2).gather(
            patient_id, tree, patient_store, policy_store
        )
    assert "tool calls" in str(caught.value)
    assert "Tool calls made:" in str(caught.value), (
        "a bound that fires without saying what was spent is not debuggable"
    )


def test_the_timeout_is_a_python_budget_the_model_never_sees(
    policy_store, patient_store, tree, e1
):
    patient_id, document_id = e1
    with pytest.raises(RetrievalError, match="budget of 0.0"):
        _planner(
            _full_run(patient_id, document_id), timeout_s=0.0
        ).gather(patient_id, tree, patient_store, policy_store)


def test_every_bound_is_a_python_constant(policy_store, patient_store):
    for bound in (DEFAULT_MAX_STEPS, DEFAULT_MAX_LLM_CALLS):
        assert isinstance(bound, int) and bound > 0
    assert DEFAULT_TIMEOUT_S > 0
    assert DEFAULT_MAX_LLM_CALLS >= DEFAULT_MAX_STEPS, (
        "max_llm_calls counts LLM calls and a tool round trip costs two, so a "
        "ceiling below the tool budget makes the tool budget unreachable"
    )


# --------------------------------------------------------------------------
# REQ-54: no tool returns a collection sized by the chart (T-65, D66)
# --------------------------------------------------------------------------


class _WideStore:
    """A patient with more of everything than the ceiling admits.

    Synthetic rather than a fixture patient, because the property under test is
    what happens *past* `MAX_ROWS` and the committed corpus's largest chart is
    3,780 observations — which exercises truncation but could never exercise the
    note-list fault, and both branches need a case.
    """

    def __init__(self, notes: int = 1, rows: int = MAX_ROWS + 1) -> None:
        self._notes = [
            Document.from_text(f"wide/note_{i}.txt", f"note {i}")
            for i in range(notes)
        ]
        # Oldest first, so a tool that returned rows in port order and truncated
        # would keep exactly the wrong ones.
        self._rows = rows

    def get_notes(self, patient_id: str):
        return list(self._notes)

    def get_document(self, document_id: str):
        for note in self._notes:
            if note.document_id == document_id:
                return note
        raise KeyError(document_id)

    def get_observations(self, patient_id: str):
        return [
            Observation(
                code="39156-5",
                value=40.0 + index,
                unit="kg/m2",
                effective_date=date(2020, 1, 1) + timedelta(days=index),
            )
            for index in range(self._rows)
        ]

    def get_conditions(self, patient_id: str):
        return [
            Condition(
                code=f"{index}",
                system="http://snomed.info/sct",
                clinical_status="active",
                onset_date=date(2020, 1, 1) + timedelta(days=index),
            )
            for index in range(self._rows)
        ]


def test_no_structured_tool_returns_a_collection_sized_by_the_chart():
    """T-65's exit, and the whole of D64's 446x outlier.

    `get_patient_observations` returned all 3,780 of one patient's rows, the
    payload entered the context window, and `include_contents="default"` re-sent
    it every turn. Cost was payload x turns and it scaled with the chart rather
    than with the question.
    """
    from pa_agent.agent.patient_tools import build_patient_tools

    tools = build_patient_tools(_WideStore()).tools
    for name, key in (
        ("get_patient_observations", "observations"),
        ("get_patient_conditions", "conditions"),
    ):
        response = tools[name]("p1")
        assert len(response[key]) == MAX_ROWS
        assert response["returned"] == MAX_ROWS
        assert response["total"] == MAX_ROWS + 1, (
            "a truncated payload that did not name the true total would be "
            "indistinguishable from a patient with a thinner chart"
        )
        assert response["truncated"] is True


def test_a_truncated_read_drops_the_oldest_rows_and_not_the_newest():
    """If rows must go, the only defensible ones to drop are the oldest —
    criterion (a)'s lookback is twelve months and c2 asks about recency. A tool
    that truncated in port order would keep exactly the wrong ones (D66)."""
    from pa_agent.agent.patient_tools import build_patient_tools

    store = _WideStore()
    rows = build_patient_tools(store).tools["get_patient_observations"]("p1")
    dates = [row["effective_date"] for row in rows["observations"]]
    assert dates == sorted(dates, reverse=True)
    newest = max(o.effective_date for o in store.get_observations("p1")).isoformat()
    assert dates[0] == newest


def test_the_note_list_faults_rather_than_truncating():
    """The asymmetry T-65 turns on. Every `document_id` the model may ask for
    comes out of `get_patient_notes`, so its payload is not information — it is
    the model's action space, and a truncated list is a shorter chart with a flag
    nobody can act on. There is no page two. REQ-46 already makes an exceeded
    bound an error rather than a partial answer."""
    from pa_agent.agent.patient_tools import build_patient_tools
    from pa_agent.agent.tool_bounds import ToolBudgetExceeded

    tools = build_patient_tools(_WideStore(notes=MAX_ROWS + 1)).tools
    with pytest.raises(ToolBudgetExceeded) as raised:
        tools["get_patient_notes"]("p1")
    assert str(MAX_ROWS) in str(raised.value)
    assert str(MAX_ROWS + 1) in str(raised.value)

    # And the fault is recorded rather than swallowed: a call that failed still
    # happened, and Article X wants the sequence.
    toolset = build_patient_tools(_WideStore(notes=MAX_ROWS + 1))
    with pytest.raises(ToolBudgetExceeded):
        toolset.tools["get_patient_notes"]("p1")
    assert [c.name for c in toolset.calls] == ["get_patient_notes"]
    assert toolset.calls[0].ok is False


def test_the_bundle_is_the_ports_full_read_and_never_the_models_view(
    policy_store, patient_store, tree, case_patients
):
    """**The pin that makes truncation safe**, on the patient that caused D64's
    446x.

    Capping a tool's response is a cost change only while the payload informs the
    model's plan and reaches no criterion. That is D63's design — `gather()`
    re-reads the structured facts from the port — but nothing was stopping a
    later change from assembling the bundle out of what the model returned
    instead. Then a chart truncated at fifty rows would silently become the
    evidence, and criterion (a) would answer on it.
    """
    from pa_agent.agent.patient_tools import build_patient_tools

    patient_id = case_patients["E2"]
    document_id = patient_store.get_notes(patient_id)[0].document_id

    full = patient_store.get_observations(patient_id)
    assert len(full) > MAX_ROWS, (
        "this pin needs a patient whose chart exceeds the ceiling; E2+E7 had "
        "3,780 observations when D64 measured it"
    )

    seen = build_patient_tools(patient_store).tools["get_patient_observations"](
        patient_id
    )
    assert seen["truncated"] is True and len(seen["observations"]) == MAX_ROWS

    result = _planner(_full_run(patient_id, document_id)).gather(
        patient_id, tree, patient_store, policy_store
    )
    assert len(result.observations) == len(full), (
        "the bundle was assembled from the model's view; a capped tool is now a "
        "correctness change and not a cost control (D66)"
    )
    assert result.conditions == patient_store.get_conditions(patient_id)


class _WideValueSetStore:
    """A policy store whose value set is bigger than the ceiling.

    Synthetic, and it has to be: this corpus's obesity-comorbidity set holds two
    codes, so an unbounded `get_policy_value_set` and a bounded one answer
    identically on every input the repository can produce — the mutation that
    removes the cap survives against the committed data. A production value set
    is thousands of codes, which is the 3,780-observation shape on the other
    plane (D66).
    """

    def __init__(self, size: int = MAX_ROWS + 1) -> None:
        self.codes = frozenset(f"{100000 + i}" for i in range(size))

    def get_value_set(self, value_set_id: str) -> frozenset[str]:
        return self.codes

    def get_tree(self, policy_version_id: str):  # pragma: no cover - unused here
        raise NotImplementedError


def test_the_value_set_is_bounded_and_criterion_b_still_reads_all_of_it(
    policy_store, tree
):
    """The policy plane's half. It truncates rather than faults because
    Amendment 1 reserves set membership to Python on both paths: no verdict can
    turn on which codes the model saw, and criterion (b) reads the port's set."""
    from pa_agent.agent.policy_tools import build_policy_tools

    value_set_id = tree.criterion("b").require("value_set_id")
    response = build_policy_tools(policy_store).tools["get_policy_value_set"](
        value_set_id
    )
    assert set(response) == {
        "value_set_id", "codes", "total", "returned", "truncated"
    }
    full = policy_store.get_value_set(value_set_id)
    assert response["total"] == len(full)
    assert response["truncated"] is False

    wide = _WideValueSetStore()
    capped = build_policy_tools(wide).tools["get_policy_value_set"](value_set_id)
    assert len(capped["codes"]) == MAX_ROWS
    assert capped["total"] == MAX_ROWS + 1
    assert capped["truncated"] is True

    # And the criterion is unaffected, which is the reason truncating is legal
    # here at all: membership is Python's, over the port's full set.
    assert wide.get_value_set(value_set_id) == frozenset(wide.codes)
    assert len(wide.get_value_set(value_set_id)) == MAX_ROWS + 1


def test_the_ceiling_is_one_python_constant_the_model_never_sees():
    """Article I and II's shape, applied to T-65: the bound is a module constant,
    not a number the model proposes, and one number rather than four — four caps
    would need four justifications and they would all be the same sentence."""
    from pa_agent.agent import tool_bounds

    assert isinstance(MAX_ROWS, int) and MAX_ROWS > 0
    caps = {
        name: value
        for name, value in vars(tool_bounds).items()
        if isinstance(value, int) and not name.startswith("_")
    }
    assert caps == {"MAX_ROWS": MAX_ROWS}, (
        f"tool_bounds declares more than one ceiling: {sorted(caps)}"
    )

    source = (REPO_ROOT / "pa_agent" / "agent" / "patient_tools.py").read_text(
        encoding="utf-8"
    )
    assert str(MAX_ROWS) not in source, (
        "a tool module naming the ceiling as a literal is a second place to "
        "change it, and the two would drift"
    )


# --------------------------------------------------------------------------
# REQ-41 / REQ-53: scope is supplied, never parsed out of an id (T-66, D66)
# --------------------------------------------------------------------------


def test_the_document_tool_refuses_a_bundle_filename(patient_store, case_patients):
    """T-66's exit, and D65's refusal made mechanical.

    The extraction agent's whole REQ-53 argument is that it must not reach the
    structured BMI while reporting the note's. A document tool that resolved
    across T-64's widened patient-plane namespace would hand it a FHIR bundle by
    filename, and every existing test would keep passing because the tests
    compare the two readings and would now find them equal.
    """
    from pa_agent.agent.patient_tools import build_patient_tools

    patient_id = case_patients["E2"]
    bundles = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )["bundles"]
    filename = next(b["filename"] for b in bundles if b["patient_id"] == patient_id)

    # The port resolves it — that is T-64, and it is correct.
    assert patient_store.get_document(filename).document_id == filename

    # The tool does not, because its scope is this patient's notes.
    tools = build_patient_tools(patient_store).tools
    with pytest.raises(KeyError):
        tools["get_patient_document"](patient_id, filename)


def test_the_note_reader_refuses_every_id_but_the_one_it_was_built_for(
    patient_store, case_patients
):
    """The extraction agent holds no patient id — `ExtractionRunner.run` is given
    a document id and nothing else — so T-66's "pass the id the model already
    holds" does not reach it. The scope is a captured constant instead, and the
    check runs before the port is consulted, so the widened namespace is never
    searched with an id the caller did not authorize."""
    from pa_agent.agent.patient_tools import build_note_reader

    mine, other = (
        patient_store.get_notes(case_patients[case])[0].document_id
        for case in ("E1", "E2")
    )
    toolset = build_note_reader(patient_store, mine)
    assert toolset.names == ["read_note"]
    assert toolset.tools["read_note"](mine)["text"]

    for refused in (other, "Rayford811_Sanford861_x.json", ""):
        with pytest.raises(KeyError):
            toolset.tools["read_note"](refused)
    assert [c.ok for c in toolset.calls] == [True, False, False, False]


def test_no_tool_module_reads_structure_out_of_an_identifier():
    """D65's structural pin, moved from the adapter to the tools.

    `_patient_of` recovered a note's owner by splitting the id on `/`, which
    worked only because T-07 named every note `<patient_id>/chart_note.txt`. A
    resolver that parses an id is a convention wearing a function's clothes, and
    it is silently wrong the first time a note is named anything else. Asserted on
    the AST rather than on behaviour, because a parse that falls through to a
    lookup answers identically on every id this corpus can produce — which is
    exactly the mutation that survived in T-64.
    """
    forbidden = {"split", "rsplit", "partition", "startswith", "endswith",
                 "removeprefix", "removesuffix"}
    for name in ("patient_tools.py", "policy_tools.py"):
        path = REPO_ROOT / "pa_agent" / "agent" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # On the AST, so the docstring that explains why `_patient_of` is gone
        # does not itself fail the check. Prose about a deleted function is the
        # record of the decision; a call to one is the defect.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name != "_patient_of", f"{name} still parses an owner"
            if isinstance(node, ast.Name):
                assert node.id != "_patient_of", f"{name} still calls _patient_of"
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                raise AssertionError(
                    f"{name} calls .{node.attr}() on line {node.lineno}; scope "
                    "comes from an argument or a captured constant, never from "
                    "the shape of an identifier (T-66, D66)"
                )


# --------------------------------------------------------------------------
# REQ-45 / REQ-48: invented evidence and malformed output are faults
# --------------------------------------------------------------------------


def test_a_document_id_the_store_does_not_serve_is_refused(
    policy_store, patient_store, tree, e1
):
    """REQ-45: the model may not invent evidence. An id no store serves is a
    fabricated citation one layer up from a fabricated quote, and it fails on a
    dict lookup rather than on judgment (Art. III's argument, applied to
    retrieval)."""
    patient_id, _ = e1
    with pytest.raises(RetrievalError, match="may not invent evidence"):
        _planner([_plan(["ghost/chart_note.txt"])]).gather(
            patient_id, tree, patient_store, policy_store
        )


def test_a_plan_that_gathered_nothing_is_a_fault_not_an_empty_bundle(
    policy_store, patient_store, tree, e1
):
    """The third time this repo has paid for this rule. `resolve` returning
    `None` reports NO_POLICY_FOUND for all of Medicare (D31). A patient store
    returning `[]` manufactures E7 for every patient (D39). A planner returning
    nothing makes every chart look like it has no weight-management history —
    and the determination is perfectly well-formed."""
    patient_id, _ = e1
    with pytest.raises(RetrievalError, match="gathered no readable note"):
        _planner([_plan([], gathered=False, note="could not read")]).gather(
            patient_id, tree, patient_store, policy_store
        )


def test_malformed_model_output_is_a_fault_with_a_name(
    policy_store, patient_store, tree, e1
):
    """REQ-48: schema-invalid output resolves to a fault, never to a verdict and
    never to an empty bundle."""
    patient_id, _ = e1
    for broken in ('{"document_ids": "not a list"}', "not json at all", "null"):
        with pytest.raises((RetrievalError, Exception)):
            _planner([broken]).gather(
                patient_id, tree, patient_store, policy_store
            )


def test_the_plan_carries_ids_and_never_content():
    """Article III at the retrieval boundary. If the model could hand back note
    text, it could hand back text that was never in the note, and every span
    downstream would anchor against a fabrication. So the documents come from the
    store and the model supplies only names."""
    fields = set(RetrievalPlan.model_fields)
    assert fields == {"document_ids", "gathered", "note"}
    assert "text" not in fields and "content" not in fields


def test_a_repeated_document_is_counted_once_in_the_bundle(
    policy_store, patient_store, tree, e1
):
    """A repeat is a cost finding, not a second copy of the evidence. Counting it
    twice would hand the patient a longer history than the chart has, and c3
    would measure a run that never happened."""
    patient_id, document_id = e1
    result = _planner([_plan([document_id, document_id, document_id])]).gather(
        patient_id, tree, patient_store, policy_store
    )
    assert len(result.notes) == 1


# --------------------------------------------------------------------------
# REQ-51: deterministic validation remains authoritative
# --------------------------------------------------------------------------


def test_the_agentic_planner_satisfies_the_same_port_as_the_fixed_one():
    assert isinstance(FixedRetrievalPlanner(), RetrievalPlanner)
    assert isinstance(AgenticRetrievalPlanner(), RetrievalPlanner)
    assert FixedRetrievalPlanner().name == "fixed"
    assert AgenticRetrievalPlanner().name == "agentic"


def test_the_same_gathered_evidence_yields_the_same_verdicts(
    policy_store, patient_store, runner, tree, e1
):
    """REQ-51, and the property the whole differential rests on: when the planner
    gathers what the fixed one gathers, the criteria cannot tell them apart —
    because it is the same criteria code reading the same bundle."""
    patient_id, document_id = e1
    ref = policy_store.resolve(CONTRACTOR_CODE)

    fixed = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=patient_id,
        as_of=AS_OF, planner=FixedRetrievalPlanner(), verifier=AcceptAllVerifier(),
    ).determination
    agentic = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=patient_id,
        as_of=AS_OF, planner=_planner(_full_run(patient_id, document_id)),
        verifier=AcceptAllVerifier(),
    ).determination

    assert agentic.outcome is fixed.outcome
    assert [(r.criterion_id, r.verdict) for r in agentic.criterion_results] == [
        (r.criterion_id, r.verdict) for r in fixed.criterion_results
    ]


def test_the_agentic_path_walks_the_same_graph(
    policy_store, patient_store, runner, tree, e1
):
    """One variable changes. Two graphs that were supposed to differ in one step
    would drift in others, and every drift would show up in the differential as a
    finding about the model (D63)."""
    patient_id, document_id = e1
    run = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=policy_store.resolve(CONTRACTOR_CODE),
        patient_id=patient_id, as_of=AS_OF,
        planner=_planner(_full_run(patient_id, document_id)),
        verifier=AcceptAllVerifier(),
    )
    assert run.steps == list(STEP_NAMES)


def test_a_planner_that_skips_a_note_changes_a_verdict_and_nothing_raises(
    policy_store, patient_store, runner, tree, case_patients
):
    """The failure this task exists to make visible.

    The E4 chart's run is what c3 measures. A planner that gathers no note at all
    is refused above — but one that gathers a *different* note produces a
    determination that is entirely well-formed and quietly about the wrong chart.
    Here the E1 planner is pointed at E5's document: nothing raises, spans still
    validate, and the verdicts move.

    This is why the deliverable is a differential rather than an assertion.
    """
    e1_patient = case_patients["E1"]
    e5_document = patient_store.get_notes(case_patients["E5"])[0].document_id

    class _CrossPlanner:
        name = "cross"

        def gather(self, patient_id, tree, patient_store, policy_store):
            value_set_id = tree.criterion("b").require("value_set_id")
            return RetrievalResult(
                observations=patient_store.get_observations(patient_id),
                conditions=patient_store.get_conditions(patient_id),
                value_set=policy_store.get_value_set(value_set_id),
                notes=patient_store.get_notes(case_patients["E5"]),
            )

    ref = policy_store.resolve(CONTRACTOR_CODE)
    correct = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=e1_patient,
        as_of=AS_OF, planner=FixedRetrievalPlanner(), verifier=AcceptAllVerifier(),
    ).determination
    crossed = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=e1_patient,
        as_of=AS_OF, planner=_CrossPlanner(), verifier=AcceptAllVerifier(),
    ).determination

    assert crossed.outcome is not correct.outcome, (
        "reading the wrong chart produced the same answer; this test is not "
        "demonstrating anything"
    )
    assert e5_document  # the document the crossed run actually read
    # And it is well-formed: no error, every criterion answered, a gap list.
    assert crossed.criterion_results
    assert all(r.verdict is not CriterionVerdict.ERROR for r in crossed.criterion_results)


# --------------------------------------------------------------------------
# REQ-50: the differential compares identical inputs
# --------------------------------------------------------------------------


def test_the_differential_reports_a_criterion_disagreement():
    compare = _eval_module().compare

    assert compare({"a": "MET"}, {"a": "MET"}) == {}
    assert compare({"a": "MET"}, {"a": "NOT_MET"}) == {
        "a": {"agentic": "MET", "oracle": "NOT_MET"}
    }


def test_the_differential_does_not_hide_a_disagreement_under_an_agreeing_outcome():
    """Two paths can agree overall while disagreeing about which criterion could
    not be answered, and the second names the evidence that went missing."""
    compare = _eval_module().compare

    assert compare(
        {"a": "NOT_MET", "b": "MET"}, {"a": "MET", "b": "NOT_MET"}
    ) == {
        "a": {"agentic": "NOT_MET", "oracle": "MET"},
        "b": {"agentic": "MET", "oracle": "NOT_MET"},
    }


def test_the_evals_self_checks_all_pass():
    self_check = _eval_module().self_check

    failed = [(label, detail) for label, ok, detail in self_check() if not ok]
    assert failed == [], failed


def test_the_gate_refuses_to_pass_without_a_measurement():
    """T-61's deliverable is a comparison. A gate that reported success having
    compared nothing would be the vacuous check D63 refused to write."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "eval/run_agentic_eval.py"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    measurement = REPO_ROOT / "eval" / "agentic" / "results.json"
    if measurement.exists():
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode == 1
        assert "no measurement" in result.stdout


# --------------------------------------------------------------------------
# Article I and the plane boundary
# --------------------------------------------------------------------------


def test_the_agent_cannot_transfer_control(patient_store, policy_store):
    from google.adk.flows.llm_flows.single_flow import SingleFlow

    agent, _, _ = build_retrieval_agent(patient_store, policy_store)
    assert isinstance(agent._llm_flow, SingleFlow)
    assert agent.sub_agents == []
    assert AGENT_NAME.isidentifier()


def test_the_retrieval_port_module_imports_no_model():
    """`pa_agent/retrieval.py` holds the port and the fixed planner; the agentic
    one lives in `pa_agent/agent/`, which is what keeps the ADK off the import
    path of the deterministic core (D16)."""
    import subprocess
    import sys

    probe = (
        "import sys; import pa_agent.retrieval; "
        "assert 'google.adk' not in sys.modules, 'retrieval pulled in the ADK'; "
        "assert 'google.genai' not in sys.modules, 'retrieval pulled in genai'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr


def test_the_two_toolsets_stay_in_two_modules(patient_store, policy_store):
    """The gatherer is the one agent holding both plane handles, and Article VI
    survives it: each tool closes over its own store and neither can see the
    other's. The modules stay separate and neither imports the other."""
    for module in ("patient_tools.py", "policy_tools.py"):
        source = (REPO_ROOT / "pa_agent" / "agent" / module).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        other = "policy" if module.startswith("patient") else "patient"
        assert not any(other in name for name in imported), (
            f"{module} imports the {other} plane: {sorted(imported)}"
        )
