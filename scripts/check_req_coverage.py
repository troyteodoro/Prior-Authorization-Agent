"""Audit the REQ → check mapping for completeness (A7, T-23, D87).

Every requirement in `docs/spec.md` §5 either maps to a check in this repo or
appears in §5's *Unclaimed in v1* table with a stated reason. A requirement in
neither fails. An unclaimed row with no `docs/decisions.md` entry naming it
fails. A mapping pointing at a check that does not exist fails. A requirement in
both fails.

**This gate audits the mapping, not the checks.** Running them is
`check_gates.py`'s job. "This requirement is covered" and "this check passes"
are different claims with different evidence, and merging them into one script
is the merge D28 refused (D87).

The mapping is written down by hand, deliberately. Deriving it by searching test
files for REQ numbers grades comment discipline instead of coverage: a REQ
number in a docstring is not a check, a check can test a requirement without
naming it, and the gate would be satisfiable by editing a comment. Seven of the
requirements below are never named by the file that checks them, which is the
measurement that settled it.

    python scripts/check_req_coverage.py

Spends no model call, touches no network.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "docs" / "spec.md"
DECISIONS = REPO_ROOT / "docs" / "decisions.md"

#: REQ -> the check that covers it. A path under `tests/` is run by the suite,
#: which is the first gate; anything else must appear in `check_gates.GATES`.
#: One entry per requirement, so a requirement whose check disappears fails here
#: rather than quietly becoming unclaimed.
MAPPING: dict[str, str] = {
    # Policy resolution
    "REQ-1": "tests/test_resolver.py",
    "REQ-2": "tests/test_resolver.py",
    "REQ-3": "tests/test_short_circuits.py",
    "REQ-4": "tests/test_determination.py",
    "REQ-42": "tests/test_resolver.py",
    "REQ-55": "tests/test_resolver.py",
    "REQ-33": "tests/test_planes.py",
    "REQ-41": "tests/test_planes.py",
    # Evidence and citation
    "REQ-5": "tests/test_schemas.py",
    "REQ-6": "tests/test_spans.py",
    "REQ-7": "tests/test_index.py",
    # Extraction
    "REQ-8": "tests/test_extraction.py",
    "REQ-9": "tests/test_extraction.py",
    "REQ-10": "tests/test_extraction.py",
    "REQ-35": "tests/test_extraction.py",
    "REQ-38": "tests/test_extraction.py",
    "REQ-52": "tests/test_workflow.py",
    "REQ-53": "tests/test_adk_agent.py",
    "REQ-56": "tests/test_reask.py",
    # Agentic orchestration
    "REQ-43": "tests/test_agentic_workflow.py",
    "REQ-45": "tests/test_agentic_workflow.py",
    "REQ-46": "tests/test_valueset_port.py",
    "REQ-48": "tests/test_agentic_workflow.py",
    "REQ-49": "tests/test_adk_agent.py",
    "REQ-50": "eval/run_agentic_eval.py",
    "REQ-51": "tests/test_agentic_workflow.py",
    "REQ-54": "tests/test_agentic_workflow.py",
    # Adjudication
    "REQ-11": "tests/test_criteria_ab.py",
    "REQ-12": "tests/test_criteria_ab.py",
    "REQ-13": "tests/test_criteria_c.py",
    "REQ-36": "tests/test_criteria_c.py",
    "REQ-32": "tests/test_criteria_c.py",
    "REQ-14": "tests/test_criteria_c.py",
    "REQ-40": "tests/test_criteria_c.py",
    "REQ-37": "tests/test_criteria_c.py",
    "REQ-15": "tests/test_criteria_c.py",
    "REQ-16": "tests/test_criteria_ab.py",
    "REQ-34": "tests/test_reconciliation.py",
    # Verification
    "REQ-17": "tests/test_verifier.py",
    "REQ-18": "tests/test_verifier.py",
    "REQ-18a": "tests/test_verifier.py",
    "REQ-31": "tests/test_gap_reason.py",
    "REQ-23": "tests/test_fault_injection.py",
    "REQ-24": "tests/test_fault_injection.py",
    "REQ-26": "tests/test_error_state.py",
    "REQ-27": "tests/test_fault_injection.py",
    "REQ-29": "tests/test_fault_injection.py",
    "REQ-30": "tests/test_error_state.py",
    # Aggregation
    "REQ-19": "tests/test_determination.py",
    "REQ-20": "tests/test_determination.py",
    "REQ-21": "tests/test_determination.py",
    "REQ-39": "tests/test_reconciliation.py",
    # Instrumentation
    "REQ-22": "tests/test_workflow.py",
    "REQ-25": "tests/test_build_report.py",
    "REQ-28": "tests/test_metrics_error_accounting.py",
}

REQ_RE = re.compile(r"^\*\*(REQ-[0-9]+[a-z]?)\*\*", re.MULTILINE)
UNCLAIMED_ROW_RE = re.compile(r"^\|\s*(REQ-[0-9]+[a-z]?)\s*\|(.+?)\|(.+?)\|\s*$", re.MULTILINE)


class CheckFailed(Exception):
    """The message is the reason the gate is red."""


def spec_requirements(spec: str) -> list[str]:
    ids = REQ_RE.findall(spec)
    if not ids:
        raise CheckFailed(
            "no requirements parsed from docs/spec.md; the parser is reading the "
            "wrong thing, and an empty universe makes every check below vacuous"
        )
    duplicates = {r for r in ids if ids.count(r) > 1}
    if duplicates:
        raise CheckFailed(f"docs/spec.md declares {sorted(duplicates)} more than once")
    return ids


def unclaimed_table(spec: str) -> dict[str, tuple[str, str]]:
    """§5's *Unclaimed in v1* rows: REQ -> (why, what would claim it)."""
    heading = "#### Unclaimed in v1"
    if heading not in spec:
        raise CheckFailed(
            "docs/spec.md has no 'Unclaimed in v1' section. A7 admits a list and "
            "this gate reads it rather than assuming it empty (D70)"
        )
    section = spec[spec.index(heading) :]
    nxt = re.search(r"^#{1,4} ", section[len(heading) :], re.MULTILINE)
    if nxt:
        section = section[: len(heading) + nxt.start()]

    rows: dict[str, tuple[str, str]] = {}
    for req, why, claims in UNCLAIMED_ROW_RE.findall(section):
        rows[req] = (why.strip(), claims.strip())
    return rows


def check_mapping_targets(root: Path, gate_commands: set[str]) -> None:
    """Failure 3: a mapping is a claim about the repo.

    A claim about a missing file is how a coverage report reaches 100% while
    covering nothing. Anything outside `tests/` must also be named by a gate,
    since the suite only runs `tests/`.
    """
    for req, target in sorted(MAPPING.items()):
        if not (root / target).exists():
            raise CheckFailed(f"{req} maps to {target}, which does not exist")
        if target.startswith("tests/"):
            continue
        if target not in gate_commands:
            raise CheckFailed(
                f"{req} maps to {target}, which is outside tests/ and is not a "
                "gate; nothing runs it, so the mapping claims a check that never "
                "executes"
            )


def check_completeness(requirements: list[str], unclaimed: dict[str, tuple[str, str]]) -> None:
    """Failures 1 and 4: every REQ is spoken for, and by exactly one side."""
    mapped = set(MAPPING)
    declared = set(unclaimed)

    both = sorted(mapped & declared)
    if both:
        raise CheckFailed(
            f"{both} are both mapped to a check and declared unclaimed. Two "
            "answers to 'is this claimed' is worse than none, and the table is "
            "where a broken check would go to retire (D87)"
        )

    missing = [r for r in requirements if r not in mapped and r not in declared]
    if missing:
        raise CheckFailed(
            f"{len(missing)} requirement(s) map to no check and are not declared "
            f"unclaimed: {', '.join(missing)}. A7 requires one or the other"
        )

    stale = sorted((mapped | declared) - set(requirements))
    if stale:
        raise CheckFailed(
            f"{stale} are mapped or declared but appear in no §5 requirement; "
            "the mapping is describing a spec that moved"
        )


def check_unclaimed_are_justified(unclaimed: dict[str, tuple[str, str]], decisions: str) -> None:
    """Failure 2: A7's closing sentence, made enforceable.

    "The list does not grow without a decision entry." A list that absorbs
    whatever is inconvenient makes the gate meaningless instead of satisfiable.
    """
    for req, (why, claims) in sorted(unclaimed.items()):
        if len(why.split()) < 5:
            raise CheckFailed(f"{req} is declared unclaimed with no stated reason")
        if len(claims.split()) < 3:
            raise CheckFailed(
                f"{req} is declared unclaimed without the condition that would claim it"
            )
        if not re.search(rf"\b{re.escape(req)}\b", decisions):
            raise CheckFailed(
                f"{req} is declared unclaimed and no entry in docs/decisions.md "
                "names it. A7's list does not grow without a decision entry"
            )


def _gate_commands() -> set[str]:
    spec = importlib.util.spec_from_file_location(
        "check_gates", REPO_ROOT / "scripts" / "check_gates.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_gates", module)
    spec.loader.exec_module(module)
    return {argv[0] for _label, argv in module.GATES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    spec = SPEC.read_text(encoding="utf-8")
    decisions = DECISIONS.read_text(encoding="utf-8")

    try:
        requirements = spec_requirements(spec)
        unclaimed = unclaimed_table(spec)
        check_completeness(requirements, unclaimed)
        check_mapping_targets(REPO_ROOT, _gate_commands())
        check_unclaimed_are_justified(unclaimed, decisions)
    except CheckFailed as exc:
        print(f"\n  FAIL  {exc}\n", file=sys.stderr)
        return 1

    print(f"ok    the requirements  ({len(requirements)} in docs/spec.md §5)")
    print(f"ok    the mapping  ({len(MAPPING)} mapped to a check that exists)")
    print(
        f"ok    the unclaimed list  ({len(unclaimed)} declared, each with a "
        "reason and a decision entry)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
