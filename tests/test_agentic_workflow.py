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
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import CriterionVerdict, Document
from pa_agent.retrieval import (
    FixedRetrievalPlanner,
    RetrievalError,
    RetrievalPlanner,
    RetrievalResult,
)
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
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
        _call("get_patient_document", document_id=document_id),
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
    assert set(EXTRACTION_ALLOWLIST) < set(PATIENT_ALLOWLIST)


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
        _call("get_patient_document", document_id=document_id),
        _call("get_patient_document", document_id=document_id),
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
        as_of=AS_OF, planner=FixedRetrievalPlanner(),
    ).determination
    agentic = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=patient_id,
        as_of=AS_OF, planner=_planner(_full_run(patient_id, document_id)),
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
        as_of=AS_OF, planner=FixedRetrievalPlanner(),
    ).determination
    crossed = run_criteria_workflow(
        policy_store=policy_store, patient_store=patient_store,
        extraction_runner=runner, policy_ref=ref, patient_id=e1_patient,
        as_of=AS_OF, planner=_CrossPlanner(),
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
