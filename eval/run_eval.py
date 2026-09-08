#!/usr/bin/env python3
"""T-10 — the eval harness, written before the system it grades (spec §8).

`python eval/run_eval.py` reads the labeled cases in `eval/cases.json`, runs
each one, classifies the result, and compares every classification against
`eval/baseline.json`. **The exit code means "the observed results match the
baseline", not "every case passed."** On the day this is written nothing
downstream of T-09 exists, so no case can pass; a gate that could not return
zero until the system was finished would be a gate nobody ran while building it.
See D27.

Drift fails in both directions. A case that starts passing fails the gate as
loudly as one that stops, and the baseline update is the commit that records the
change. That is what makes US-1's close — "`run_eval.py` reports E3 passing" —
a command rather than a table someone reads.

Three statuses, and the third is the point:

    PASS      the system answered, and answered as labeled
    FAIL      the system answered, and it was wrong
    BLOCKED   the component that would answer does not exist yet

`BLOCKED` never folds into `FAIL`. Both read as "not passing," which is exactly
why they must stay apart — only one of them names a task. This is Article IV's
argument one level above where the article states it, and REQ-28 will force the
same distinction on this file at T-30, when an `ERROR` must not be counted as an
abstention.

Blocking is **discovered, never declared**: the harness catches
`NotImplementedError` and records the message, and those messages already name
their own task. A `blocked_by` field on a case would keep naming a task after it
landed, which is the T-39 defect with different spelling.

No model is called here, and none will be: this file scores a `Determination`,
it does not produce one.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import (  # noqa: E402
    CallMetrics,
    Determination,
    DeterminationOutcome,
)
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "cases.json"
DEFAULT_BASELINE = EVAL_DIR / "baseline.json"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_HARNESS_BROKEN = 2


class CaseStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class ReasonClass(str, Enum):
    """Why a case is not `PASS`, coarsely enough to be diffed.

    The baseline compares a status and one of these, never the free-text
    reason. Diffing prose would fail the gate whenever someone rewords an
    exception, which teaches a reader to update the baseline without looking at
    it — the one habit that makes the whole mechanism worthless (D27).
    """

    WRONG_OUTCOME = "WRONG_OUTCOME"
    MODEL_CALLS_EXCEEDED = "MODEL_CALLS_EXCEEDED"
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"
    CASE_UNSPECIFIED = "CASE_UNSPECIFIED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: CaseStatus
    reason_class: ReasonClass | None = None
    reason: str = ""
    model_calls: int | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        """What the baseline records and diffs."""
        return (
            self.status.value,
            self.reason_class.value if self.reason_class else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status.value,
            "reason_class": self.reason_class.value if self.reason_class else None,
            "reason": self.reason,
            "model_calls": self.model_calls,
        }


# --------------------------------------------------------------------------
# Scoring — pure, no I/O, and the half no real case can exercise until T-25
# --------------------------------------------------------------------------


def score(case: dict[str, Any], determination: Determination) -> CaseResult:
    """Compare a determination against its label.

    Outcome is checked before the model-call budget. When both are wrong the
    outcome is the finding: A4's zero-call assertion is a claim about *how* the
    right answer was reached, and reporting it over a wrong answer would bury
    the more serious defect.
    """
    case_id = case["case_id"]
    expect = case["expect"]
    calls = determination.model_calls

    expected_outcome = DeterminationOutcome(expect["outcome"])
    if determination.outcome is not expected_outcome:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.WRONG_OUTCOME,
            f"expected {expected_outcome.value}, got {determination.outcome.value}",
            calls,
        )

    budget = expect.get("max_model_calls")
    if budget is not None and calls > budget:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.MODEL_CALLS_EXCEEDED,
            f"{calls} model call(s) against a budget of {budget}",
            calls,
        )

    return CaseResult(case_id, CaseStatus.PASS, None, "", calls)


# --------------------------------------------------------------------------
# The system under test
# --------------------------------------------------------------------------


def _determine(case: dict[str, Any], policy_store: Any) -> Determination:
    """The seam T-24 and T-25 fill in. Today it reaches storage and stops.

    `LocalPolicyStore.resolve` raises `NotImplementedError` citing T-38, which
    is the correct answer to "what is the governing policy for this code"
    while the criteria tree carries no procedure sets. The harness records that
    message rather than a status it invented (D26, D27).
    """
    policy_store.resolve(case["procedure_code"])
    raise NotImplementedError(
        "T-25 has not built determination assembly: resolution returned, and "
        "there is nothing yet that turns a PolicyRef into a Determination."
    )


def run_case(case: dict[str, Any], policy_store: Any) -> CaseResult:
    """Run one labeled case and classify the result.

    The broad handler is deliberate and is not the thing REQ-27 forbids: it
    re-raises nothing but maps to a *named* `reason_class` and records the
    traceback, and D7 is explicit that a batch eval should report which case
    failed rather than die on the first. REQ-27's grep is over `pa_agent/`; this
    file is the grader, not the system.
    """
    case_id = case["case_id"]

    if case.get("procedure_code") is None:
        return CaseResult(
            case_id,
            CaseStatus.BLOCKED,
            ReasonClass.CASE_UNSPECIFIED,
            case.get("unspecified_reason", "the case carries no procedure code"),
        )

    try:
        determination = _determine(case, policy_store)
    except NotImplementedError as exc:
        return CaseResult(
            case_id, CaseStatus.BLOCKED, ReasonClass.NOT_IMPLEMENTED, str(exc)
        )
    except Exception as exc:  # noqa: BLE001 — mapped to a named class, see above
        detail = "".join(
            traceback.format_exception_only(type(exc), exc)
        ).strip()
        return CaseResult(
            case_id, CaseStatus.FAIL, ReasonClass.UNEXPECTED_EXCEPTION, detail
        )

    return score(case, determination)


# --------------------------------------------------------------------------
# Scorer self-check
# --------------------------------------------------------------------------


def _synthetic_case(**overrides: Any) -> dict[str, Any]:
    case = {
        "case_id": "SELF",
        "procedure_code": "00000",
        "expect": {"outcome": "NOT_COVERED", "max_model_calls": 0},
    }
    case.update(overrides)
    return case


def _synthetic_determination(
    outcome: DeterminationOutcome, metrics: list[CallMetrics] | None = None
) -> Determination:
    return Determination(
        patient_id="self-check",
        procedure_code="00000",
        policy_version_id="self-check-v0",
        outcome=outcome,
        metrics=metrics or [],
    )


def self_check() -> list[tuple[str, bool, str]]:
    """Score six synthetic cases whose answers are known.

    Every branch below is one no real case can reach until T-25 produces a
    determination. Without this the code that decides whether US-1 passed would
    sit unrun until the moment it decides whether US-1 passed.
    """
    metric = CallMetrics(
        model="self-check",
        purpose="self-check",
        input_tokens=0,
        output_tokens=0,
        wall_time_ms=0.0,
    )

    class _RaisingStore:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def resolve(self, procedure_code: str) -> None:
            raise self._exc

    checks: list[tuple[str, tuple[str, str | None], tuple[str, str | None]]] = []

    def record(label: str, observed: CaseResult, expected: tuple[str, str | None]):
        checks.append((label, observed.key, expected))

    record(
        "a matching determination scores PASS",
        score(_synthetic_case(), _synthetic_determination(DeterminationOutcome.NOT_COVERED)),
        ("PASS", None),
    )
    record(
        "a mismatched outcome scores FAIL/WRONG_OUTCOME",
        score(_synthetic_case(), _synthetic_determination(DeterminationOutcome.MET)),
        ("FAIL", "WRONG_OUTCOME"),
    )
    record(
        "a spent model call against a zero budget scores FAIL/MODEL_CALLS_EXCEEDED",
        score(
            _synthetic_case(),
            _synthetic_determination(DeterminationOutcome.NOT_COVERED, [metric]),
        ),
        ("FAIL", "MODEL_CALLS_EXCEEDED"),
    )
    record(
        "a wrong outcome outranks a blown call budget",
        score(
            _synthetic_case(),
            _synthetic_determination(DeterminationOutcome.MET, [metric]),
        ),
        ("FAIL", "WRONG_OUTCOME"),
    )
    record(
        "a case with no procedure code is BLOCKED/CASE_UNSPECIFIED",
        run_case(_synthetic_case(procedure_code=None), _RaisingStore(AssertionError())),
        ("BLOCKED", "CASE_UNSPECIFIED"),
    )
    record(
        "NotImplementedError is BLOCKED/NOT_IMPLEMENTED, never FAIL",
        run_case(_synthetic_case(), _RaisingStore(NotImplementedError("T-38"))),
        ("BLOCKED", "NOT_IMPLEMENTED"),
    )
    record(
        "any other exception is FAIL/UNEXPECTED_EXCEPTION",
        run_case(_synthetic_case(), _RaisingStore(ValueError("boom"))),
        ("FAIL", "UNEXPECTED_EXCEPTION"),
    )

    return [
        (label, observed == expected, f"expected {expected}, got {observed}")
        for label, observed, expected in checks
    ]


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def diff_against_baseline(
    results: list[CaseResult], baseline: dict[str, dict[str, Any]]
) -> list[str]:
    """Every way the observed set can differ from what was recorded."""
    drift: list[str] = []
    observed = {r.case_id: r for r in results}

    for case_id, result in observed.items():
        if case_id not in baseline:
            drift.append(
                f"{case_id}: ran but is absent from the baseline "
                f"(observed {result.key[0]}/{result.key[1]}). A case nothing "
                "records is a case nothing grades."
            )
            continue
        recorded = (baseline[case_id]["status"], baseline[case_id].get("reason_class"))
        if result.key != recorded:
            drift.append(
                f"{case_id}: baseline says {recorded[0]}/{recorded[1]}, "
                f"observed {result.key[0]}/{result.key[1]}"
            )

    for case_id in baseline:
        if case_id not in observed:
            drift.append(f"{case_id}: in the baseline and not in the case set")

    return drift


def baseline_from(results: list[CaseResult], note: str) -> dict[str, Any]:
    return {
        "recorded_on": date.today().isoformat(),
        "note": note,
        "cases": {
            r.case_id: {
                "status": r.status.value,
                "reason_class": r.reason_class.value if r.reason_class else None,
            }
            for r in sorted(results, key=lambda r: r.case_id)
        },
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def print_report(results: list[CaseResult], drift: list[str], baseline_path: Path) -> None:
    print()
    width = max((len(r.case_id) for r in results), default=4)
    for result in sorted(results, key=lambda r: r.case_id):
        reason_class = result.reason_class.value if result.reason_class else "-"
        print(f"  {result.case_id:<{width}}  {result.status.value:<7}  {reason_class}")
        if result.reason:
            for line in _wrap(result.reason, 76):
                print(f"  {'':<{width}}           {line}")

    counts = {status: 0 for status in CaseStatus}
    for result in results:
        counts[result.status] += 1
    calls = sum(r.model_calls or 0 for r in results)

    print()
    print(
        f"  {len(results)} case(s): {counts[CaseStatus.PASS]} PASS, "
        f"{counts[CaseStatus.FAIL]} FAIL, {counts[CaseStatus.BLOCKED]} BLOCKED"
    )
    print(f"  model calls spent: {calls}")
    print()

    rel = baseline_path.relative_to(REPO_ROOT) if baseline_path.is_relative_to(REPO_ROOT) else baseline_path
    if drift:
        print(f"  DRIFT against {rel}:")
        for line in drift:
            print(f"    - {line}")
        print()
        print("  Either the change was intended — re-run with --update-baseline and")
        print("  commit the diff — or something regressed. The gate does not guess.")
    else:
        print(f"  no drift against {rel}")
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", type=Path, default=None, help="write results here")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite the baseline from what was observed, for review as a diff",
    )
    parser.add_argument("--no-self-check", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_self_check:
        checks = self_check()
        failed = [(label, detail) for label, ok, detail in checks if not ok]
        if failed:
            print(f"\n  scorer self-check FAILED: {len(failed)} of {len(checks)} checks")
            for label, detail in failed:
                print(f"    - {label}: {detail}")
            print("\n  The instrument is broken. Nothing below it is worth reading.\n")
            return EXIT_HARNESS_BROKEN
        print(f"\n  scorer self-check: {len(checks)}/{len(checks)}")

    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n  cannot read cases from {args.cases}: {exc}\n", file=sys.stderr)
        return EXIT_HARNESS_BROKEN

    policy_store = LocalPolicyStore()
    results = [run_case(case, policy_store) for case in cases]

    if args.update_baseline:
        note = (
            "Observed status per case. Updated by --update-baseline; the change "
            "is meant to be read as a diff (D27)."
        )
        args.baseline.write_text(
            json.dumps(baseline_from(results, note), indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n  baseline rewritten: {args.baseline}")

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n  cannot read the baseline at {args.baseline}: {exc}\n", file=sys.stderr)
        return EXIT_HARNESS_BROKEN

    drift = diff_against_baseline(results, baseline)
    print_report(results, drift, args.baseline)

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "eval_set": str(args.cases.name),
                    "results": [r.as_dict() for r in results],
                    "drift": drift,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
