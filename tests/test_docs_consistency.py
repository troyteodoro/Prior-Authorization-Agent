"""D108 — the figures documents copy are checked against the artifact that owns them.

**Spends no model call and touches no network.**

Working rule 4 has always said a task closes on a command returning zero, and
every close honoured it while `CLAUDE.md` carried A2 at a 0.591 base rate, A5
at 0.250 and A6 at 38 calls — wrong for two whole tasks, through two closes,
with all ten gates green the entire time. `eval/report.md` is generated and
`--verify`'d; every prose copy of its figures was a copy nothing compared.

So the rule ships with the check. The principle it encodes is the one working
rule 12 states: **the generated artifact owns the figure and prose is a copy.**
`eval/report.md` owns every measured number, `docs/tasks.md` owns the counts,
and the suite owns its own size. Each test below re-derives from the owner and
requires the copy to agree — never the reverse.

Two things are deliberately **not** checked, and both matter:

- Figures inside *closed task records* and inside `docs/decisions.md`. They
  record what was true at that close; the log is append-only and a reversal is
  a new entry, never an edit. A test that "fixed" them would erase the history
  this repo keeps on purpose.
- Every other number in every document. A pin on all of them would be friction
  that gets suppressed rather than satisfied, which is worse than no pin
  because it looks like coverage (D108). These are the ones that actually
  drifted.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"
REPORT = REPO_ROOT / "eval" / "report.md"
TASKS = REPO_ROOT / "docs" / "tasks.md"
SPEC = REPO_ROOT / "docs" / "spec.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def claude() -> str:
    return CLAUDE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The suite owns its own size
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def collected() -> int:
    """How many tests the suite actually collects.

    Collection, not execution: `--collect-only` does not run test bodies, so
    this cannot re-enter the suite the way D69's recursion guard is about.
    `check_gates.py` refuses to run under pytest because it *runs* the suite;
    counting it is a different act, and it costs 0.6s.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "--color=no"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    assert match, f"could not read a collection count:\n{proc.stdout[-500:]}"
    return int(match.group(1))


def test_the_documented_suite_size_is_the_suite_s_size(readme, collected):
    """It read 653 in one document and 833 in another against an actual 876.

    The number now appears twice on purpose — once in each document a reader
    starts from — and both are checked. Everywhere else it was deleted rather
    than pinned, because a copy that earns nothing is drift surface (D108).
    """
    match = re.search(r"collects\s+\*?\*?(\d[\d,]*)\*?\*?\s+tests", readme)
    assert match, "README no longer states the suite size"
    documented = int(match.group(1).replace(",", ""))
    assert documented == collected, (
        f"README says {documented} tests, the suite collects {collected}. "
        "The suite owns this number (working rule 12)."
    )


def test_claude_md_agrees_with_the_suite_too(claude, collected):
    match = re.search(r"collects\s+(\d[\d,]*)\s+tests", claude)
    assert match, "CLAUDE.md no longer states the suite size"
    assert int(match.group(1).replace(",", "")) == collected


def test_the_documented_file_count_is_the_file_count(claude):
    actual = len(list((REPO_ROOT / "tests").glob("test_*.py")))
    match = re.search(r"across (\d+) files", claude)
    assert match, "CLAUDE.md no longer states the test-file count"
    assert int(match.group(1)) == actual


# --------------------------------------------------------------------------
# The board owns the counts
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def closed_tasks() -> int:
    """Closed task records on the board, counted from their headings."""
    board = TASKS.read_text(encoding="utf-8")
    return len(re.findall(r"^### `\[x\] T-\d+`", board, re.MULTILINE))


def test_both_documents_report_the_board_s_own_task_count(readme, claude, closed_tasks):
    """It read 69 in the README after the board said 70.

    Counted from the board's closed-record headings rather than from its prose,
    so the prose is checked too.
    """
    for name, text in (("README.md", readme), ("CLAUDE.md", claude)):
        found = {int(n) for n in re.findall(r"(\d+) of \1 tasks closed", text)}
        assert found, f"{name} no longer states a task count"
        assert found == {closed_tasks}, (
            f"{name} says {found} tasks closed; the board carries "
            f"{closed_tasks} closed records (working rule 12)."
        )


# --------------------------------------------------------------------------
# The report owns every measured figure
# --------------------------------------------------------------------------


def test_the_acceptance_figures_in_claude_md_come_from_the_report(claude, report):
    """A2, A5 and A6 — the three that were wrong for two tasks.

    Each is re-read from `eval/report.md`, which `build_report.py --verify`
    recomputes from the committed recordings, and required to appear in
    `CLAUDE.md`'s prose. The report is the source; this file holds a copy.
    """
    base_rate = re.search(r"base rate \(labeled pairs that are `MET`\) \| [\d/]+ = \*\*([\d.]+)\*\*", report)
    abstention = re.search(r"Abstention rate: [\d/]+ answered = ([\d.]+)", report)
    assert base_rate and abstention, "the report's shape moved; re-read it here"

    assert f"{base_rate.group(1)} base rate" in claude, (
        f"CLAUDE.md does not carry the report's A2 base rate "
        f"({base_rate.group(1)})"
    )
    assert f"A5 abstention {abstention.group(1)}" in claude, (
        f"CLAUDE.md does not carry the report's A5 abstention rate "
        f"({abstention.group(1)})"
    )

    # Scoped to A6's own section: the tier comparison above it has rows with
    # the same labels, and an unscoped search reads the wrong table.
    cost_section = report[report.index("## Cost and latency"):]
    cost = re.search(
        r"\| Model calls \| (\d+) \|.*?\| Input tokens \| (\d+) \|.*?"
        r"\| Output tokens \| (\d+) \|",
        cost_section, re.DOTALL,
    )
    assert cost, "the report's cost table moved"
    calls, tin, tout = cost.groups()
    assert f"A6 {calls} model calls" in claude, f"CLAUDE.md's A6 call count is not {calls}"
    for label, value in (("input", tin), ("output", tout)):
        assert f"{int(value):,}" in claude, (
            f"CLAUDE.md does not carry the report's A6 {label} tokens ({int(value):,})"
        )


# --------------------------------------------------------------------------
# The spec owns the roadmap
# --------------------------------------------------------------------------


def _roadmap(text: str) -> list[tuple[str, str]]:
    """(version, story) for each row of a roadmap table, in order."""
    rows = []
    for line in text.splitlines():
        match = re.match(r"\| (v\d[\d.]*) \| .*?\| (US-[\d–—-]+|—) \|", line.strip())
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def test_the_readme_roadmap_agrees_with_the_spec(readme):
    """The README's roadmap is a third copy of spec §11's, so it is pinned.

    Adding a copy of a table without a check is exactly how the figures above
    went stale (D108). Versions the README states as complete are its own to
    carry; the ones the spec schedules have to match it, in order.
    """
    spec_text = SPEC.read_text(encoding="utf-8")
    section = spec_text[spec_text.index("## 11."):]
    spec_rows = _roadmap(section)
    readme_rows = _roadmap(readme)

    assert spec_rows, "spec §11's roadmap table no longer parses"
    assert readme_rows, "the README no longer carries a roadmap"

    scheduled = [row for row in readme_rows if row[0] not in ("v1",)]
    assert scheduled == spec_rows, (
        "the README's roadmap disagrees with spec §11's.\n"
        f"  README: {scheduled}\n"
        f"  spec  : {spec_rows}\n"
        "Spec §11 is the source; reordering versions is a decision entry (D105)."
    )
