"""Record Troy's ratification statuses in ``docs/ratifications.json`` (T-74, D75).

This tool is Troy's pen, not a check. Working rule 11 scopes who decides —
an agent writes ``proposed`` and nothing else — and D74 rejected a git-author
check because the rule binds sessions, not mechanisms. Troy invoking this
tool is Troy's edit; the ``by`` field and the diff remain the record. **An
agent never runs this tool**: its entire output is statuses an agent may not
write.

Explicit ids only, by design (D75): there is no tier-wide wildcard, so each
invocation names what was read. ``--pending`` prints the checklist view.
Validation is borrowed from ``check_ownership.py`` — the same
``enumerate_ids`` supplies the id universe, so the pen and the gate cannot
disagree about what an id is.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date as _date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_ownership", REPO_ROOT / "scripts" / "check_ownership.py"
)
_co = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_ownership", _co)
_spec.loader.exec_module(_co)

# One-directional on purpose: the seed state is T-73's, never re-written here,
# and an agent-writable status has no business in a status-writing tool (D75).
WRITABLE_STATUSES = ("ratified", "amended", "overruled")


class RecordRefused(Exception):
    """The message says why nothing was written."""


def record(
    ids: list[str],
    status: str,
    by: str,
    task: str | None,
    ledger_path: Path,
    universe: dict[str, tuple[str, ...]],
    today: str,
) -> list[tuple[str, str]]:
    """Stamp ``{status, by, date[, task]}`` onto each id; all-or-nothing."""
    if status not in WRITABLE_STATUSES:
        raise RecordRefused(
            f"status {status!r} is not recordable here; one of {WRITABLE_STATUSES}"
        )
    unknown = [i for i in ids if i not in universe]
    if unknown:
        raise RecordRefused(
            f"ids resolving in no document: {', '.join(unknown)} — "
            "spelling is exact, e.g. 'Article I', 'REQ-18a', 'E10b', 'US-4'"
        )
    board = {i for i, tiers in universe.items() if tiers == ("tasks",)}
    if status in _co.LINKED_STATUSES:
        if task is None or task not in board:
            raise RecordRefused(
                f"status {status!r} must link a board task via --task "
                f"(working rule 6, D74); got {task!r}"
            )
    elif task is not None:
        raise RecordRefused(f"--task only accompanies {_co.LINKED_STATUSES}")

    ledger = json.loads(ledger_path.read_text())
    entries = ledger["entries"]
    missing = [i for i in ids if i not in entries]
    if missing:
        raise RecordRefused(f"ids not in the ledger: {', '.join(missing)}")

    changes = []
    for i in ids:
        previous = entries[i].get("status", "?")
        entry = {"status": status, "by": by, "date": today}
        if task is not None:
            entry["task"] = task
        entries[i] = entry
        changes.append((i, previous))
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    return changes


def pending_report(ledger_path: Path, universe: dict[str, tuple[str, ...]]) -> str:
    """The checklist view: per-tier progress and the ids still ``proposed``."""
    entries = json.loads(ledger_path.read_text())["entries"]
    lines = []
    for tier in _co.TIERS:
        tier_ids = [i for i, tiers in universe.items() if tier in tiers]
        still = [i for i in tier_ids if entries.get(i, {}).get("status") == "proposed"]
        done = len(tier_ids) - len(still)
        lines.append(f"{tier:16s} {done}/{len(tier_ids)}")
        for i in still:
            lines.append(f"    proposed  {i}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ids", nargs="*", metavar="ID", help="exact ids as the docs spell them")
    parser.add_argument("--status", choices=WRITABLE_STATUSES, default="ratified")
    parser.add_argument("--by", default="troy")
    parser.add_argument("--task", default=None, metavar="T-nn",
                        help="board task an amended/overruled entry links (working rule 6)")
    parser.add_argument("--pending", action="store_true",
                        help="print per-tier progress and ids still 'proposed'")
    args = parser.parse_args(argv)

    universe = _co.enumerate_ids(REPO_ROOT)
    ledger_path = _co.LEDGER_PATH

    if args.pending:
        if args.ids:
            parser.error("--pending takes no ids")
        print(pending_report(ledger_path, universe))
        return 0
    if not args.ids:
        parser.error("no ids given (and no --pending)")

    try:
        changes = record(
            args.ids, args.status, args.by, args.task,
            ledger_path, universe, _date.today().isoformat(),
        )
    except RecordRefused as exc:
        print(f"refused: {exc}")
        return 1

    for i, previous in changes:
        print(f"{i}: {previous} -> {args.status}  (by {args.by})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
