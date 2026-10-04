"""T-110 — the round's committed charts, the measured yellow and the widened
differential (D155).

`tests/test_palmetto_workup.py` and `tests/test_rheumatoid_workup.py` hold
the arithmetic on charts written there. This file holds what was committed:

- five declared clones, each carrying notes its source never had, added in
  this round and never apart from it (D120);
- their rows, replayed through every recording the gates read, answering what
  the rows label;
- the measured yellow (`H13`) and its `(candidate, quotes)` claim, held by the
  history verifier recording on both tiers;
- the differential, widened to every note-bearing chart's request.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import Determination
from pa_agent.determination import determine
from pa_agent.model_pin import VERIFIER_MODEL
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.verifier import HISTORY_PROMPT_VERSION, RecordedVerifierRunner, claim_digest

REPO_ROOT = Path(__file__).resolve().parent.parent
POPULATION = REPO_ROOT / "data" / "patients" / "manifest.json"
MANIFESTS = REPO_ROOT / "eval" / "manifests"
NOTES = REPO_ROOT / "data" / "patients" / "notes"
CASES = REPO_ROOT / "eval" / "cases.json"
VERIFIER = REPO_ROOT / "eval" / "verifier"
AGENTIC = REPO_ROOT / "eval" / "agentic"
AS_OF = date(2026, 9, 1)

#: The round's five patients: row -> (patient, source chart). Written out, so a
#: chart swapped underneath the rows fails here first.
CLONES = {
    "J2": ("519ab9cd-e076-5113-8acc-9aa1b8bdad6d", "afdcee59-dfdd-4bc5-37f1-cf7f909ede3d"),
    "J3": ("0d2a9302-c054-58ca-a292-e3703314dcd4", "afdcee59-dfdd-4bc5-37f1-cf7f909ede3d"),
    "RA4": ("e41a0d08-e747-5e48-a468-e6deb4159d8e", "42a430ab-b7ca-87a5-279f-ee115f49fd6e"),
    "RA5": ("25622028-814a-56c6-8173-a818d05014ce", "42a430ab-b7ca-87a5-279f-ee115f49fd6e"),
    "RA6": ("2d5c55a6-7d64-5e2c-8c10-d4e8a0c240f3", "42a430ab-b7ca-87a5-279f-ee115f49fd6e"),
}
ROWS = ("J1", "J2", "J3", "RA1", "RA4", "RA5", "RA6")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases() -> dict[str, dict]:
    return {c["case_id"]: c for c in _load(CASES)["cases"]}


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("t110_run_eval", REPO_ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# The patients
# --------------------------------------------------------------------------


def test_the_five_patients_are_declared_clones_of_this_round():
    declared = {r["patient_id"]: r for r in _load(POPULATION)["synthetic_patients"]}
    for row, (patient_id, source) in CLONES.items():
        record = declared[patient_id]
        assert record["cloned_from"] == source, row
        assert (record["task"], record["decision"]) == ("T-110", "D155"), row
    states = {declared[CLONES[r][0]].get("address", {}).get("state") for r in ("J2", "J3")}
    assert states == {"GA", "TN"}, "both bariatric clones are in Palmetto's territory"


@pytest.mark.parametrize("row", sorted(CLONES))
def test_each_clone_carries_notes_of_its_own(row):
    """`declared_clone_of`, never `cloned_from`: the second means the clone's
    notes are its source's bytes and replay by content (D102), and these are
    new documents, rendered from the clone's own fact manifest."""
    patient_id, source = CLONES[row]
    manifest = _load(MANIFESTS / f"{patient_id}.json")
    assert manifest["declared_clone_of"] == source
    assert "cloned_from" not in manifest
    mine = {p.name: p.read_bytes() for p in (NOTES / patient_id).glob("*.txt")}
    assert len(mine) == 2
    theirs = {p.name: p.read_bytes() for p in (NOTES / source).glob("*.txt")}
    assert not set(mine.values()) & set(theirs.values())


# --------------------------------------------------------------------------
# The rows, replayed through every recording the gates read
# --------------------------------------------------------------------------


@pytest.mark.parametrize("row", ROWS)
def test_each_row_answers_what_it_labels(row, cases, harness):
    case = cases[row]
    result = determine(
        LocalPolicyStore(), case["procedure_code"], case["patient_id"],
        LocalPatientStore(), date.fromisoformat(case.get("as_of") or "2026-09-01"),
        harness._recorded_runner(), harness._recorded_verifier(), payer="medicare",
    )
    expect = case["expect"]
    assert result.outcome.value == expect["outcome"]
    assert result.policy_version_id == expect["policy_version_id"]
    by_id = {r.criterion_id: r for r in result.criterion_results}
    for criterion_id, label in expect["criteria"].items():
        assert by_id[criterion_id].verdict.value == label["verdict"], (row, criterion_id)
        if "gap_reason" in label:
            assert by_id[criterion_id].gap_reason.value == label["gap_reason"], (row, criterion_id)
    assert result.model_calls <= expect["max_model_calls"]


def test_no_criterion_of_the_two_trees_t110_claimed_is_unclaimed_any_more():
    store = LocalPolicyStore()
    for tree_id in ("ncd-100.1-jjm-v1", "infliximab-ra-jjm-v1"):
        assert all(c.evaluation == "deterministic" for c in store.get_tree(tree_id).criteria)
    ultrasound = store.get_tree("us-abdominal-visceral-j5-j8-v1")
    assert [c.id for c in ultrasound.criteria if c.evaluation == "unclaimed"] == ["c", "d", "e"], (
        "the ultrasound tree's three judgments stay unclaimed (D107)"
    )


# --------------------------------------------------------------------------
# The measured yellow
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name,tier", [("history_results.json", "ai_studio"),
                                       ("history_results_vertex.json", "vertex")])
def test_the_history_recording_holds_the_yellows_claim_accepted(name, tier):
    recording = _load(VERIFIER / name)
    assert (recording["task"], recording["decision"]) == ("T-110", "D155")
    assert recording["prompt_version"] == HISTORY_PROMPT_VERSION
    assert recording["model"] == VERIFIER_MODEL
    assert recording["tier"] == tier
    (claim,) = recording["claims"]
    assert claim_digest(claim["payload"]) == claim["digest"]
    assert claim["accept"] is True
    assert claim["payload"]["candidate"] == {
        "effect": "neutropenia", "icd10_title": "Other drug-induced agranulocytosis",
    }
    assert set(claim["payload"]) == {"candidate", "quotes"}, (
        "no drug, no code, no colour reaches the verifier (D122)"
    )


def test_the_criterion_and_history_recordings_share_no_digest():
    criteria = {c["digest"] for c in _load(VERIFIER / "results.json")["claims"]}
    history = {c["digest"] for c in _load(VERIFIER / "history_results.json")["claims"]}
    assert criteria and history and not criteria & history
    RecordedVerifierRunner.from_records(
        _load(VERIFIER / "results.json")["claims"] + _load(VERIFIER / "history_results.json")["claims"]
    )


def test_h13_is_yellow_through_the_gates_own_replay(cases, harness):
    case = cases["H13"]
    determination = determine(
        LocalPolicyStore(), case["procedure_code"], case["patient_id"], LocalPatientStore(),
        AS_OF, harness._recorded_runner(), harness._recorded_verifier(), payer="medicare",
    )
    run = harness._review(
        case, determination, LocalPolicyStore(), LocalPatientStore(),
        __import__("pa_agent.stores.knowledge", fromlist=["LocalKnowledgeStore"]).LocalKnowledgeStore(),
        harness._recorded_quote_runner(), harness._recorded_verifier(),
    )
    (suggestion,) = run.review.suggestions
    assert suggestion.colour.value == "yellow"
    assert [s.quote for s in suggestion.citations] == ["consistent with neutropenia"]
    assert run.rejections == ()
    assert len(run.verifications) == 1, "the yellow's verification is counted beside the quotes"


# --------------------------------------------------------------------------
# The differential, widened
# --------------------------------------------------------------------------


def _agentic(name: str) -> dict:
    return _load(AGENTIC / name)


@pytest.mark.parametrize("name", ["results.json", "results_vertex.json"])
def test_the_differential_covers_every_note_bearing_charts_request(name):
    recording = _agentic(name)
    note_bearing = {r["patient_id"] for r in _load(NOTES / "manifest.json")["notes"]}
    rows = recording["patients"]
    assert {r["patient_id"] for r in rows} == note_bearing
    assert len(rows) == len(note_bearing) == 25
    assert {r["procedure_code"] for r in rows} == {"43775", "E0601", "J7325", "J1745"}
    assert all(r.get("as_of") for r in rows)


@pytest.mark.parametrize("name", ["results.json", "results_vertex.json"])
def test_the_differential_agrees_everywhere_with_zero_errors_on_both_tiers(name):
    """A14's clause, read on both tiers since D156 and held since T-147
    (D160): every outcome and criterion agrees, every span slices back, and no
    run errored. v1.6 closed on this."""
    aggregate = _agentic(name)["aggregate"]
    assert (aggregate["scored"], aggregate["errors"]) == (25, 0)
    assert aggregate["outcome_disagreements"] == aggregate["criterion_disagreements"] == 0
    assert aggregate["criteria_compared"] == 137
    assert aggregate["spans_valid"] == aggregate["spans_total"]
