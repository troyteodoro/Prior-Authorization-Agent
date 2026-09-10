"""T-69 — every zero-cost gate in the repo, in one command (D69).

    python scripts/check_gates.py

Working rule 4: a task closes on its own exit condition **and** on this. The rule
existed without the second half until T-67 closed with `pytest` red and T-68
found it a commit later — a task's exit names one file, and nothing said the rest
of the repo had to still be green.

### What is in the list, and why that is not a taste call

A command is a gate iff **some task's exit condition in `docs/tasks.md` names it**
and it **spends no model call and touches no network**. Both halves are auditable
against the board, so every entry has an argument and a reader can check it.

The second half alone would be too weak: `scripts/run_extraction.py --rescore`
costs nothing and no task's exit names it, because it is the bookkeeping half of
a measurement rather than a check on the repo.

`pytest` bare comes first and subsumes the thirty-odd per-file `pytest` exits on
the board. The per-file command stays the task's exit; the suite is what the task
is *additionally* answerable to.

### Why this file refuses to run under pytest

It runs `pytest`, and `pytest` collects `tests/test_check_gates.py`. A test that
drove this end to end would recurse until something ran out, so `main` stops when
it sees `PYTEST_CURRENT_TEST`. The test drives `run_gates()` with stub commands
instead — the same split D67 made between a measurement and its bookkeeping.

### Limits, stated because a green run invites trust

This says the zero-cost checks pass. It says nothing about the checks that spend
money or hit the network — `verify_sources.py` without `--offline`,
`run_extraction.py`, `run_adk_extraction.py` — and those are where the model
provenance in `docs/decisions.md` actually rests. It also cannot make anyone run
it (D69).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Every zero-cost gate, in the order a reader wants them: the suite first,
#: because it is the one that subsumes the others, then the artifact checks.
#: Each entry is argv after the interpreter, and each is named by some task's
#: exit condition — the task is in the comment so the claim is checkable.
GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("the whole suite", ("-m", "pytest", "-q", "--color=no")),  # every pytest exit
    ("the declared environment", ("scripts/check_env.py",)),  # T-43
    ("the skeleton and adk web", ("scripts/check_skeleton.py",)),  # T-03
    # T-02's exit is this without `--offline`, which re-downloads. The offline
    # form re-hashes what is committed and deliberately **does not close T-02**.
    ("the policy corpus hashes", ("scripts/verify_sources.py", "--offline")),  # T-02
    ("the patient bundles", ("scripts/select_patients.py", "--verify")),  # T-04
    ("spike 001's recording", ("spike/spike_001/run.py", "--verify")),  # T-00
    ("the eval baseline", ("eval/run_eval.py",)),  # T-10
    ("the agentic differential", ("eval/run_agentic_eval.py",)),  # T-61
    ("the ratification ledger", ("scripts/check_ownership.py",)),  # T-73
)

#: Tracked scripts that are **not** gates, and why. `tests/test_check_gates.py`
#: asserts every tracked script under `scripts/`, `eval/` and `spike/` appears
#: here or in `GATES`, so a new script has to be classified rather than silently
#: falling outside the ritual (D69).
EXCLUDED: dict[str, str] = {
    "scripts/run_extraction.py": "spends model calls (T-15's measurement)",
    "scripts/run_adk_extraction.py": "spends model calls (T-63's measurement)",
    "scripts/synthesize_notes.py": (
        "regenerates a committed corpus; T-07 closes on tests/test_notes.py, "
        "which the suite already runs"
    ),
    "scripts/check_gates.py": (
        "this file; running it inside itself is the recursion the pytest guard "
        "refuses"
    ),
}


def _display(argv: tuple[str, ...]) -> str:
    """The command as a reader would type it, so a FAIL line can be copied."""
    return "python " + " ".join(argv)


def run_gates(
    gates: tuple[tuple[str, tuple[str, ...]], ...] = GATES,
    runner=None,
    stream=None,
) -> int:
    """Run every gate, report each, and return the number that failed.

    `runner` is injected so the test can drive this with stubs. It takes the
    argv tuple and returns `(returncode, output)`; the default spawns the
    running interpreter, which is how a venv stays the environment under test
    (`scripts/check_env.py` makes the same assumption about `sys.executable`).
    """
    runner = runner or _subprocess_runner
    out = stream or sys.stdout
    failures: list[tuple[str, str, str]] = []

    print(f"  interpreter: {sys.executable}", file=out)
    for name, argv in gates:
        started = time.monotonic()
        code, output = runner(argv)
        elapsed = time.monotonic() - started
        if code == 0:
            print(f"  ok    {name} — {_display(argv)} ({elapsed:.1f}s)", file=out)
        else:
            print(f"  FAIL  {name} — {_display(argv)} exited {code}", file=out)
            failures.append((name, _display(argv), output))

    for name, command, output in failures:
        # Only a failure's output is printed. Eight passing gates print eight
        # lines; a failing one prints everything it said, because that is the
        # moment someone needs it.
        print(f"\n  ---- {name}: {command}\n", file=out)
        print(output.rstrip() or "  (no output)", file=out)

    if failures:
        print(
            f"\n  {len(failures)} of {len(gates)} gate(s) failed — the repo is "
            "not green, so no task closes (working rule 4, D69)",
            file=out,
        )
    else:
        print(f"\n  {len(gates)} gates pass; no model call, no network", file=out)
    return len(failures)


def _subprocess_runner(argv: tuple[str, ...]) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, *argv],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/check_gates.py",
        description="Every zero-cost gate in the repo, in one command (T-69).",
    )
    parser.parse_args(argv)

    if os.environ.get("PYTEST_CURRENT_TEST"):
        # Not a stylistic objection: this runs `pytest`, and `pytest` collects
        # the test that would be calling it (D69).
        print(
            "  refusing to run inside pytest: this command runs the suite, and "
            "the suite collects tests/test_check_gates.py.\n"
            "  Drive run_gates() with stub commands instead.",
            file=sys.stderr,
        )
        return 2

    return 1 if run_gates() else 0


if __name__ == "__main__":
    raise SystemExit(main())
