"""Check the ratification ledger against the repo's load-bearing IDs (D74, T-73).

Ownership, not coverage: this gate proves every Article, REQ, edge case,
acceptance criterion, story, task and decision entry carries a human
ratification status in ``docs/ratifications.json`` — it does not prove any
requirement maps to a passing check, which is ``check_req_coverage.py``'s
claim (T-23) and a different one (D74, by D28's reasoning).

Statuses: an agent may write ``proposed`` and nothing else; ``ratified``,
``amended`` and ``overruled`` are the owner's edits (CLAUDE.md working rule 11).
An ``amended``/``overruled`` entry must link a task that resolves on the
board. A tier listed in the ledger's ``required_tiers`` — or passed via
``--require`` — fails on any entry still ``proposed``; ``amended`` and
``overruled`` pass it, because each already carries a judgment and scheduled
work. Once the ``eval`` tier is required, every eval ground-truth artifact
must also carry a well-formed ``adjudicated`` record.

The gate spends no model call and touches no network. It proves coverage of
the ledger, not comprehension of the reading.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = REPO_ROOT / "docs" / "ratifications.json"

CONSTITUTION = REPO_ROOT / "docs" / "constitution.md"
SPEC = REPO_ROOT / "docs" / "spec.md"
STORIES = REPO_ROOT / "docs" / "stories.md"
TASKS = REPO_ROOT / "docs" / "tasks.md"
DECISIONS = REPO_ROOT / "docs" / "decisions.md"

STATUSES = ("proposed", "ratified", "amended", "overruled")
LINKED_STATUSES = ("amended", "overruled")
TIERS = (
    "constitution",
    "spec",
    "stories",
    "tasks",
    "decisions-core",
    "decisions-all",
    "eval",
)

# D74 froze this set: the decisions CLAUDE.md's "Invariants a fresh session
# will break silently" and "Domain facts" sections cite. Growing it is a
# decision entry, not an edit here.
DECISIONS_CORE = frozenset(
    f"D{n}"
    for n in (
        7, 9, 18, 19, 20, 21, 22, 24, 25, 27, 28, 29, 30, 31,
        39, 40, 42, 45, 50, 51, 62, 63, 64, 66, 67, 71, 73,
    )
)

# Eval ground-truth artifacts adjudicated at T-21 (D74). Manifests are
# globbed so a new patient cannot land unadjudicated in silence.
EVAL_FIXED_ARTIFACTS = ("eval/cases.json", "spike/spike_001/labels.json")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CheckFailed(Exception):
    """The message is the reason the gate is red."""


def _section(text: str, heading_re: str) -> str:
    """Slice one ``## `` section: from its heading to the next ``## ``.

    Scoped rather than grepped from the whole file, per the pattern
    tests/test_criteria_tree.py uses for spec §9.
    """
    match = re.search(heading_re, text, re.MULTILINE)
    if match is None:
        raise CheckFailed(f"no section matching {heading_re!r}")
    rest = text[match.end():]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest if nxt is None else rest[: nxt.start()]


def constitution_ids(text: str) -> list[str]:
    ids = re.findall(r"^## (Article [IVX]+|Amendment \d+) — ", text, re.MULTILINE)
    if not ids:
        raise CheckFailed("no Article headings found in docs/constitution.md")
    return ids


def spec_ids(text: str) -> list[str]:
    reqs = re.findall(r"\*\*(REQ-\d+[a-z]?)\*\*", _section(text, r"^## 5\."))
    edges = re.findall(r"^\| (E\d+[a-z]?) \|", _section(text, r"^## 6\."), re.MULTILINE)
    accepts = re.findall(r"^\| (A\d+) \|", _section(text, r"^## 7\."), re.MULTILINE)
    for name, found in (("REQ", reqs), ("edge case", edges), ("acceptance", accepts)):
        if not found:
            raise CheckFailed(f"no {name} ids found in docs/spec.md")
    seen: dict[str, None] = {}
    for i in (*reqs, *edges, *accepts):
        seen.setdefault(i, None)
    return list(seen)


def story_ids(text: str) -> list[str]:
    ids = re.findall(r"^### `(US-\d+)` ", text, re.MULTILINE)
    if not ids:
        raise CheckFailed("no story headings found in docs/stories.md")
    return ids


def task_ids(text: str) -> list[str]:
    ids = re.findall(r"^### `\[[ ~x!]\] (T-\d+)` ", text, re.MULTILINE)
    if not ids:
        raise CheckFailed("no task headings found in docs/tasks.md")
    return ids


def decision_ids(text: str) -> list[str]:
    # Iterate every heading rather than anchoring on the first match —
    # spike/spike_001/run.py records the bug the other way. Two non-D
    # headings (Kill criteria, Open questions) sit mid-file; skip them.
    ids = re.findall(r"^## (D\d+) — ", text, re.MULTILINE)
    if not ids:
        raise CheckFailed("no decision headings found in docs/decisions.md")
    return ids


def eval_artifact_ids(root: Path = REPO_ROOT) -> list[str]:
    manifests = sorted(
        p.relative_to(root).as_posix() for p in (root / "eval" / "manifests").glob("*.json")
    )
    if not manifests:
        raise CheckFailed("no manifests found under eval/manifests/")
    return [*EVAL_FIXED_ARTIFACTS, *manifests]


def enumerate_ids(root: Path = REPO_ROOT) -> dict[str, tuple[str, ...]]:
    """Every load-bearing ID mapped to the tiers it belongs to."""
    ids: dict[str, tuple[str, ...]] = {}
    for i in constitution_ids((root / "docs" / "constitution.md").read_text()):
        ids[i] = ("constitution",)
    for i in spec_ids((root / "docs" / "spec.md").read_text()):
        ids[i] = ("spec",)
    for i in story_ids((root / "docs" / "stories.md").read_text()):
        ids[i] = ("stories",)
    for i in task_ids((root / "docs" / "tasks.md").read_text()):
        ids[i] = ("tasks",)
    for i in decision_ids((root / "docs" / "decisions.md").read_text()):
        tiers = ("decisions-core", "decisions-all") if i in DECISIONS_CORE else ("decisions-all",)
        ids[i] = tiers
    for i in eval_artifact_ids(root):
        ids[i] = ("eval",)
    return ids


def check_entry(entry_id: str, entry: object, board: set[str]) -> None:
    if not isinstance(entry, dict):
        raise CheckFailed(f"{entry_id}: entry is not an object")
    status = entry.get("status")
    if status not in STATUSES:
        raise CheckFailed(f"{entry_id}: status {status!r} is not one of {STATUSES}")
    by = entry.get("by")
    if not isinstance(by, str) or not by.strip():
        raise CheckFailed(f"{entry_id}: 'by' must be a non-empty string")
    date = entry.get("date")
    if not isinstance(date, str) or not DATE_RE.match(date):
        raise CheckFailed(f"{entry_id}: 'date' must be YYYY-MM-DD, got {date!r}")
    if status in LINKED_STATUSES:
        task = entry.get("task")
        if not isinstance(task, str) or task not in board:
            raise CheckFailed(
                f"{entry_id}: status {status!r} must link a task on the board "
                f"(working rule 6, D74); got {task!r}"
            )


def check_ledger(
    ledger: dict, ids: dict[str, tuple[str, ...]], required: set[str]
) -> None:
    entries = ledger.get("entries")
    if not isinstance(entries, dict):
        raise CheckFailed("ledger has no 'entries' object")
    declared_required = ledger.get("required_tiers", [])
    if not isinstance(declared_required, list) or any(
        t not in TIERS for t in declared_required
    ):
        raise CheckFailed(f"'required_tiers' must be a list drawn from {TIERS}")
    required = required | set(declared_required)

    missing = [i for i in ids if i not in entries]
    if missing:
        raise CheckFailed(
            f"{len(missing)} ids missing from the ledger: {', '.join(missing[:5])} …"
            if len(missing) > 5
            else f"ids missing from the ledger: {', '.join(missing)}"
        )
    unknown = [i for i in entries if i not in ids]
    if unknown:
        raise CheckFailed(f"ledger ids resolving in no document: {', '.join(unknown)}")

    board = {i for i, tiers in ids.items() if tiers == ("tasks",)}
    for entry_id, entry in entries.items():
        check_entry(entry_id, entry, board)

    still_proposed = [
        i
        for i, tiers in ids.items()
        if any(t in required for t in tiers) and entries[i]["status"] == "proposed"
    ]
    if still_proposed:
        raise CheckFailed(
            f"{len(still_proposed)} entries still 'proposed' in a required tier "
            f"({', '.join(sorted(required))}): {', '.join(still_proposed[:5])} …"
            if len(still_proposed) > 5
            else "entries still 'proposed' in a required tier "
            f"({', '.join(sorted(required))}): {', '.join(still_proposed)}"
        )


def check_adjudications(root: Path, artifact_ids: list[str]) -> None:
    """Once the eval tier is required, every artifact adjudicates (D74, T-21)."""
    for rel in artifact_ids:
        payload = json.loads((root / rel).read_text())
        if rel.endswith("cases.json"):
            records = [
                (f"{rel}#{case.get('case_id', '?')}", case.get("adjudicated"))
                for case in payload.get("cases", [])
            ]
        else:
            records = [(rel, payload.get("adjudicated"))]
        for name, record in records:
            if not isinstance(record, dict):
                raise CheckFailed(f"{name}: no 'adjudicated' record")
            by, date = record.get("by"), record.get("date")
            if not isinstance(by, str) or not by.strip():
                raise CheckFailed(f"{name}: adjudicated 'by' must be a non-empty string")
            if not isinstance(date, str) or not DATE_RE.match(date):
                raise CheckFailed(f"{name}: adjudicated 'date' must be YYYY-MM-DD")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        choices=TIERS,
        metavar="TIER",
        help="require this tier for one run, ahead of it entering required_tiers",
    )
    args = parser.parse_args(argv)

    try:
        ledger = json.loads(LEDGER_PATH.read_text())
    except FileNotFoundError:
        print("FAIL  the ledger\n      docs/ratifications.json does not exist")
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL  the ledger\n      docs/ratifications.json is not JSON: {exc}")
        return 1

    try:
        ids = enumerate_ids()
        print(f"ok    the documents' ids  ({len(ids)} enumerated)")
        requested = set(args.require)
        check_ledger(ledger, ids, requested)
        effective = sorted(requested | set(ledger.get("required_tiers", [])))
        print(
            "ok    the ledger  "
            f"({len(ledger['entries'])} entries; required: {', '.join(effective) or 'none'})"
        )
        if "eval" in effective:
            artifact_ids = [i for i, tiers in ids.items() if tiers == ("eval",)]
            check_adjudications(REPO_ROOT, artifact_ids)
            print(f"ok    the adjudications  ({len(artifact_ids)} artifacts)")
    except CheckFailed as exc:
        print(f"FAIL  ownership\n      {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
