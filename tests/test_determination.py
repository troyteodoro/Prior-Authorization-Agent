"""T-25 — assembly turns a resolution into the artifact US-1 ships.

This is the file T-19's exit names; T-19 extends it with the aggregator's
tests when the criteria chain exists. Today it gates the minimal assembly:

- E3's code becomes a `NOT_COVERED` determination that records its
  `policy_version_id` (REQ-4), cites its denial with spans that slice back out
  of the hashed corpus (Art. III), carries an empty gap list (REQ-21), and
  reads zero on the model-call counter (REQ-2, A4).
- a code no policy governs becomes `NoPolicyResult` — a type that is not a
  `Determination`, because REQ-4's version id cannot exist for it (D32).
- the unbuilt path raises naming its task: covered and contractor-determined
  codes both need T-19's aggregator, and the contractor raise names the
  citation obligation D33 hands T-19 (delegation plus the MAC's exercise).
- the two D32 validators refuse the combinations they exist to refuse.

No model anywhere (Art. II). The CLI is exercised as a subprocess, because its
exit code is T-25's exit condition and a test that imports `main()` would never
notice a broken `python -m` entry point.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    CoverageClaim,
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
)
from pa_agent.determination import NoPolicyResult, determine
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_VERSION = "ncd-100.1-jf-v1"

E3_CODE = "43842"
FOREIGN_CODE = "99213"
CONTRACTOR_CODE = "43775"
COVERED_CODE = "43644"


@pytest.fixture(scope="module")
def store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def e3_determination(store) -> Determination:
    result = determine(store, E3_CODE, patient_id=None)
    assert isinstance(result, Determination)
    return result


# --------------------------------------------------------------------------
# E3: NOT_COVERED, versioned, cited, zero calls (REQ-2, REQ-4, REQ-21, A4)
# --------------------------------------------------------------------------


def test_e3_is_not_covered_with_the_policy_version(e3_determination):
    assert e3_determination.outcome is DeterminationOutcome.NOT_COVERED
    assert e3_determination.policy_version_id == TREE_VERSION


def test_e3_reads_zero_on_the_model_call_counter(e3_determination):
    """A4's counter, zero by construction: metrics is written empty and
    nothing in the sc1 path can append to it."""
    assert e3_determination.model_calls == 0
    assert e3_determination.metrics == []


def test_e3_cites_its_denial_and_the_citation_slices_back(e3_determination, store):
    claim = e3_determination.coverage_claim
    assert claim is not None, (
        "a NOT_COVERED determination with no coverage_claim asserts a denial "
        "and cites nothing (Art. III, D32)"
    )
    document = store.get_document(claim.document_id)
    assert document.slice(claim) == claim.quote
    assert claim.scoping_quote is not None
    scope_doc = store.get_document(claim.scoping_quote.document_id)
    assert scope_doc.slice(claim.scoping_quote) == claim.scoping_quote.quote


def test_e3_has_an_empty_gap_list(e3_determination):
    """REQ-21: the gap list names every criterion not resolved MET. sc1
    evaluated none, so nothing is missing — an entry here would be inventing a
    criterion nobody adjudicated."""
    assert e3_determination.gap_list == []
    assert e3_determination.criterion_results == []


def test_e3_carries_no_patient_when_none_was_consulted(e3_determination):
    assert e3_determination.patient_id is None


# --------------------------------------------------------------------------
# NO_POLICY_FOUND is not a denial, and not a Determination (REQ-1, D32)
# --------------------------------------------------------------------------


def test_an_ungoverned_code_yields_no_policy_result(store):
    result = determine(store, FOREIGN_CODE)
    assert isinstance(result, NoPolicyResult)
    assert not isinstance(result, Determination)
    assert result.procedure_code == FOREIGN_CODE
    assert not hasattr(result, "policy_version_id"), (
        "NoPolicyResult grew a policy_version_id; REQ-4's impossibility for an "
        "ungoverned code is the reason this type exists (D32)"
    )


# --------------------------------------------------------------------------
# The unbuilt paths raise naming their tasks
# --------------------------------------------------------------------------


def test_a_covered_code_raises_citing_the_aggregator(store):
    with pytest.raises(NotImplementedError, match="aggregator"):
        determine(store, COVERED_CODE)
    with pytest.raises(NotImplementedError) as excinfo:
        determine(store, COVERED_CODE)
    assert "T-19" in str(excinfo.value)


def test_a_contractor_code_raises_citing_the_aggregator(store):
    """T-36 decided: a contractor-determined code proceeds toward the tree
    (REQ-42, D33), so its unbuilt half is T-19's — and the raise must already
    name the obligation T-19 inherits, citing the MAC's exercise alongside the
    delegation, never the NCD alone."""
    with pytest.raises(NotImplementedError) as excinfo:
        determine(store, CONTRACTOR_CODE)
    message = str(excinfo.value)
    assert "T-19" in message and "T-36" not in message
    assert "cite both the delegation and the MAC's exercise" in message, (
        "the raise stopped carrying the obligation D33 hands T-19; the "
        "aggregator would be built against a message that no longer asks"
    )


# --------------------------------------------------------------------------
# The D32 validators refuse what they exist to refuse
# --------------------------------------------------------------------------


def _claim() -> CoverageClaim:
    return CoverageClaim(document_id="d", char_start=0, char_end=4, quote="text")


def test_a_patientless_determination_with_verdicts_is_refused():
    span = EvidenceSpan(document_id="d", char_start=0, char_end=4)
    result = CriterionResult(
        criterion_id="a", verdict=CriterionVerdict.MET, spans=[span]
    )
    with pytest.raises(ValidationError, match="adjudicating nobody"):
        Determination(
            patient_id=None,
            procedure_code=E3_CODE,
            policy_version_id=TREE_VERSION,
            outcome=DeterminationOutcome.MET,
            criterion_results=[result],
        )


def test_a_coverage_claim_on_a_met_determination_is_refused():
    with pytest.raises(ValidationError, match="belongs only to NOT_COVERED"):
        Determination(
            patient_id="p",
            procedure_code=E3_CODE,
            policy_version_id=TREE_VERSION,
            outcome=DeterminationOutcome.MET,
            coverage_claim=_claim(),
        )


# --------------------------------------------------------------------------
# The CLI — T-25's exit condition, exercised the way it is invoked
# --------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_the_cli_prints_e3s_determination_and_exits_zero():
    proc = _run_cli("--patient", "X", "--procedure", E3_CODE)
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["outcome"] == "NOT_COVERED"
    assert printed["policy_version_id"] == TREE_VERSION
    assert printed["model_calls"] == 0
    assert printed["gap_list"] == []
    assert printed["coverage_claim"]["document_id"] == "ncd_100_1"
    assert printed["patient_id"] == "X"


def test_the_cli_reports_no_policy_found_without_denying():
    proc = _run_cli("--patient", "X", "--procedure", FOREIGN_CODE)
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["result"] == "NO_POLICY_FOUND"
    assert "outcome" not in printed


def test_the_cli_exits_nonzero_on_an_unbuilt_path_naming_its_task():
    """A real patient, so the request passes sc2 (which does not fire for this
    chart — no active T2DM below the threshold) and lands on the unbuilt
    criteria chain. Before T-14 wired sc2, any patient string reached the
    raise; now the patient must exist to get that far (D41)."""
    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "data/patients/manifest.json")
        .read_text(encoding="utf-8")
    )
    real_patient = next(
        r for r in manifest["bundles"] if not r["has_active_t2dm"]
    )["patient_id"]
    proc = _run_cli("--patient", real_patient, "--procedure", COVERED_CODE)
    assert proc.returncode == 2
    assert "T-19" in proc.stderr
    assert proc.stdout == "", "an unbuilt path must not print a partial answer"


def test_the_cli_exits_one_on_an_unknown_patient():
    """A bad request is not an answer and not an unbuilt path (D41)."""
    proc = _run_cli("--patient", "nobody-here", "--procedure", COVERED_CODE)
    assert proc.returncode == 1
    assert "bad request" in proc.stderr
    assert proc.stdout == ""
