"""T-30 — REQ-28: an `ERROR` is neither a pass nor an abstention (D76).

The harness gained its fourth status at T-30. `PASS`/`FAIL` say the system
answered, `BLOCKED` says the component does not exist, and `ERROR` says it
exists and aborted with a classified fault — T-29's `DeterminationAborted`
reaching the grader. REQ-28 is about the arithmetic downstream of that
classification: the reported abstention rate never counts an `ERROR`, in the
numerator *or* the denominator. Excluding it from the numerator alone would
let a crash lower the rate — caution misreported as confidence — which is why
the exit condition is phrased as "a seeded `ERROR` leaves the reported
abstention rate unchanged."

Scoring itself is T-10's and T-21's; this file owns only the accounting and
the abort's classification.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import (  # noqa: E402
    CriterionResult,
    CriterionVerdict,
    DeterminationAborted,
    ErrorCode,
)


def _harness():
    """Load `eval/run_eval.py` as a module.

    `eval/` is a directory of harnesses, not a package, so it goes through
    `importlib` — the same route `tests/test_agentic_workflow.py` uses.
    """
    path = REPO_ROOT / "eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` resolves field types through
    # `sys.modules[cls.__module__]`, which must exist while the body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HARNESS = _harness()


def _answered(case_id: str, status, outcome: str):
    return HARNESS.CaseResult(
        case_id, status, outcome=outcome, model_calls=0
    )


def _seeded_error(case_id: str = "SEED"):
    """An ERROR the way `run_case` produces one: through the abort itself,
    not a hand-built `CaseResult` that could drift from the real path."""

    class _AbortingStore:
        def resolve(self, procedure_code: str):
            raise DeterminationAborted(
                [
                    CriterionResult(
                        criterion_id="c1",
                        verdict=CriterionVerdict.ERROR,
                        error_code=ErrorCode.MODEL_CALL_FAILED,
                        error_detail="seeded",
                    )
                ],
                attempts=3,
            )

    case = {
        "case_id": case_id,
        "procedure_code": "00000",
        "expect": {"outcome": "NOT_COVERED"},
    }
    return HARNESS.run_case(case, _AbortingStore())


def _fixture_results():
    """Four answered cases, one an abstention, mixing PASS and FAIL — the
    denominator is *answered*, not *correct* (D76)."""
    S = HARNESS.CaseStatus
    return [
        _answered("A1", S.PASS, "NOT_COVERED"),
        _answered("A2", S.PASS, "INSUFFICIENT_EVIDENCE"),
        _answered("A3", S.FAIL, "NOT_MET"),
        _answered("A4", S.PASS, "NO_POLICY_FOUND"),
    ]


# --------------------------------------------------------------------------
# The exit condition: a seeded ERROR leaves the abstention rate unchanged
# --------------------------------------------------------------------------


def test_a_seeded_error_leaves_the_reported_abstention_rate_unchanged():
    results = _fixture_results()
    before = HARNESS.abstention_account(results)
    assert before["abstention_rate"] == 0.25

    after = HARNESS.abstention_account(results + [_seeded_error()])
    assert after["abstention_rate"] == before["abstention_rate"]
    # Neither side moved: the numerator exclusion alone would keep the rate's
    # numerator at 1 but grow the denominator to 5 — 0.2, a crash reported as
    # extra confidence (REQ-28, D76).
    assert after["abstained"] == before["abstained"] == 1
    assert after["answered"] == before["answered"] == 4
    # Counted separately, as REQ-28's first sentence asks.
    assert after["errors"] == 1 and before["errors"] == 0


def test_error_never_enters_an_insufficient_evidence_count():
    seeded = _seeded_error()
    assert seeded.outcome is None, (
        "an ERROR case must carry no outcome — an outcome is what would let "
        "a rate count it as an abstention"
    )
    account = HARNESS.abstention_account([seeded])
    assert account["abstained"] == 0
    assert account["answered"] == 0
    assert account["abstention_rate"] is None


# --------------------------------------------------------------------------
# Classification: the abort is ERROR, not FAIL, not BLOCKED
# --------------------------------------------------------------------------


def test_determination_aborted_classifies_as_error_with_the_codes_in_the_reason():
    result = _seeded_error()
    assert result.status is HARNESS.CaseStatus.ERROR
    assert result.reason_class is HARNESS.ReasonClass.ERROR
    # The classified fault survives into the reason text — the next action is
    # reading the code, not rerunning the label.
    assert "c1" in result.reason and "MODEL_CALL_FAILED" in result.reason
    # And it diffs against the baseline as its own pair, so a case that starts
    # crashing drifts as loudly as one that starts failing.
    assert result.key == ("ERROR", "ERROR")


def test_blocked_still_means_unbuilt_and_never_absorbs_the_abort():
    class _UnbuiltStore:
        def resolve(self, procedure_code: str):
            raise NotImplementedError("T-99")

    case = {
        "case_id": "B1",
        "procedure_code": "00000",
        "expect": {"outcome": "NOT_COVERED"},
    }
    result = HARNESS.run_case(case, _UnbuiltStore())
    assert result.status is HARNESS.CaseStatus.BLOCKED
    assert result.status is not HARNESS.CaseStatus.ERROR


# --------------------------------------------------------------------------
# The number the report prints is the account's, not a private recomputation
# --------------------------------------------------------------------------


def test_the_report_prints_the_account_and_names_the_exclusion(capsys):
    results = _fixture_results() + [_seeded_error()]
    HARNESS.print_report(results, [], REPO_ROOT / "eval" / "baseline.json")
    out = capsys.readouterr().out
    # The full summary line, not a substring a different line could satisfy —
    # "1 ERROR" alone also matches the exclusion note (the T-70 lesson).
    assert "5 case(s): 3 PASS, 1 FAIL, 0 BLOCKED, 1 ERROR" in out
    assert "abstention rate: 1/4 answered = 0.250" in out
    assert "1 ERROR case(s) excluded (REQ-28)" in out


def test_an_unanswered_set_reports_no_rate_rather_than_a_zero(capsys):
    HARNESS.print_report([_seeded_error()], [], REPO_ROOT / "eval" / "baseline.json")
    out = capsys.readouterr().out
    assert "abstention rate: n/a" in out


# --------------------------------------------------------------------------
# The self-check carries the branch (D27's pattern)
# --------------------------------------------------------------------------


def test_self_check_covers_the_abort_classification_and_passes():
    checks = HARNESS.self_check()
    labels = [label for label, _, _ in checks]
    matching = [
        (label, ok, detail)
        for label, ok, detail in checks
        if "DeterminationAborted" in label or "ERROR case" in label
    ]
    assert matching, f"no abort-classification branch in the self-check: {labels}"
    for label, ok, detail in matching:
        assert ok, f"{label}: {detail}"


# --------------------------------------------------------------------------
# The outcome actually rides on the result — a dropped attach blanks the rate
# while every status stays green, which is why it is pinned here
# --------------------------------------------------------------------------


def test_run_case_attaches_the_outcome_on_the_no_policy_path():
    class _UngoverningStore:
        def resolve(self, procedure_code: str):
            return None

    case = {
        "case_id": "N1",
        "procedure_code": "00000",
        "expect": {"outcome": "NO_POLICY_FOUND"},
    }
    result = HARNESS.run_case(case, _UngoverningStore())
    assert result.status is HARNESS.CaseStatus.PASS
    assert result.outcome == "NO_POLICY_FOUND"


def test_run_case_attaches_the_determination_outcome(monkeypatch):
    from pa_agent.contracts import DeterminationOutcome

    det = HARNESS._synthetic_determination(
        DeterminationOutcome.INSUFFICIENT_EVIDENCE
    )
    monkeypatch.setattr(HARNESS, "_determine", lambda *a, **k: det)
    case = {
        "case_id": "D1",
        "procedure_code": "00000",
        "expect": {"outcome": "INSUFFICIENT_EVIDENCE"},
    }
    result = HARNESS.run_case(case, object())
    assert result.status is HARNESS.CaseStatus.PASS
    assert result.outcome == "INSUFFICIENT_EVIDENCE"
    assert HARNESS.abstention_account([result])["abstention_rate"] == 1.0
