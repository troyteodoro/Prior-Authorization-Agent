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

import importlib.util
import json
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
DECISIONS = REPO_ROOT / "docs" / "decisions.md"
KNOWLEDGE = REPO_ROOT / "data" / "knowledge"


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


#: Small counts are written as words in this project's prose, so a check on
#: them has to read both forms.
NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "twenty-one": 21, "twenty-two": 22, "twenty-three": 23,
    "twenty-four": 24, "twenty-five": 25, "twenty-six": 26,
    "twenty-seven": 27, "twenty-eight": 28, "twenty-nine": 29, "thirty": 30,
}


def _as_count(token: str) -> int | None:
    """`token` as a number, or None when it is not a count at all.

    Returning None rather than raising is deliberate: *restate FDA label* is
    a sentence about Part B drug LCDs, not a miscount, and a check that
    cannot tell the two apart is a check that gets suppressed.
    """
    cleaned = token.strip("*`_,.;:()").lower()
    if cleaned.isdigit():
        return int(cleaned)
    return NUMBER_WORDS.get(cleaned)


def _counted_phrases(text: str, pattern: str) -> list[tuple[int, str]]:
    """Every match of `pattern` whose leading group reads as a number."""
    found = []
    for match in re.finditer(pattern, text):
        value = _as_count(match.group(1))
        if value is not None:
            found.append((value, match.group(0)))
    return found


def _every_range_claim(text: str, pattern: str, expected: int, what: str) -> None:
    """Every stated ID range in `text` ends where the document actually ends."""
    matches = list(re.finditer(pattern, text))
    assert matches, f"CLAUDE.md no longer states the {what} range"
    for match in matches:
        assert int(match.group(1)) == expected, (
            f"CLAUDE.md says {match.group(0)!r}; the highest {what} is {expected}"
        )


def _suite_size_claims(text: str) -> list[tuple[int, int | None, int]]:
    """Every claim in `text` about the size of the **whole** suite.

    Two phrasings are in use — `collects N tests` and `N tests across M
    files` — and both are collected, with the file count where the sentence
    carries one. Per-file counts like `` `tests/x.py` (23 tests) `` are a
    different claim and are deliberately not matched here; they have their
    own check below.
    """
    claims: list[tuple[int, int | None, int]] = []
    for match in re.finditer(r"collects\s+\*{0,2}(\d[\d,]*)\*{0,2}\s+tests", text):
        claims.append((int(match.group(1).replace(",", "")), None, match.start()))
    for match in re.finditer(r"(\d[\d,]*)\s+tests\s+across\s+(\d+)\s+files", text):
        claims.append(
            (int(match.group(1).replace(",", "")), int(match.group(2)), match.start())
        )
    return claims


def test_every_stated_suite_size_is_the_suites_size(readme, claude, collected):
    """**Every** occurrence, not the first — which is how 1120 survived.

    It read 653 in one document and 833 in another against an actual 876, so
    the number is stated once in each document a reader starts from and both
    are checked. But the check used `re.search`, so it compared the *first*
    match and stopped: when T-126 rewrote the README it left `1120` further
    down the same file, and nothing looked at it. A second copy of a checked
    figure was invisible to the check that exists for copies.

    Scanning every occurrence also means a new copy is covered the moment
    someone writes it, rather than the moment someone remembers to extend a
    regex. Elsewhere copies are deleted rather than pinned, because a copy
    that earns nothing is drift surface (D108).
    """
    files = len(list((REPO_ROOT / "tests").glob("test_*.py")))
    for name, text in (("README.md", readme), ("CLAUDE.md", claude)):
        claims = _suite_size_claims(text)
        assert claims, f"{name} no longer states the suite size"
        for size, file_count, offset in claims:
            line = text.count("\n", 0, offset) + 1
            assert size == collected, (
                f"{name}:{line} says {size} tests, the suite collects "
                f"{collected}. The suite owns this number (working rule 12)."
            )
            if file_count is not None:
                assert file_count == files, (
                    f"{name}:{line} says {file_count} test files, there are "
                    f"{files}"
                )


def test_every_per_file_test_count_is_that_files_count(readme):
    """The README quotes six per-file counts. Each is that file's own.

    These were all correct when this check was written, which is the point:
    the six figures that drifted were also correct once. Collecting a single
    file costs about a fifth of a second, and a number nobody re-derives is a
    number that goes stale between the close that wrote it and the close that
    reads it.
    """
    claims = re.findall(r"`tests/(test_[a-z0-9_]+)\.py`\s*\((\d+) tests\)", readme)
    assert claims, "the README no longer quotes any per-file test count"
    for stem, stated in claims:
        path = REPO_ROOT / "tests" / f"{stem}.py"
        assert path.is_file(), f"README names {stem}.py, which does not exist"
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--collect-only", "-q",
             "-p", "no:cacheprovider", "--color=no"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        match = re.search(r"(\d+) tests? collected", proc.stdout)
        assert match, f"could not collect {stem}.py:\n{proc.stdout[-400:]}"
        assert int(stated) == int(match.group(1)), (
            f"README says {stem}.py has {stated} tests; it collects "
            f"{match.group(1)}"
        )


def test_neither_document_states_a_wall_clock_figure(readme, claude):
    """No `~Ns` in prose. There is no decision entry for this; here is why.

    These figures cannot be pinned the way the counts above are. Timing the
    suite would mean the suite timing itself, which is the recursion D69's
    guard refuses, and a tolerance wide enough not to be flaky on a loaded
    machine is wide enough to be worthless.

    Left unpinned they drifted badly: `check_gates.py` was described as
    `~12s`, `~25s` and `~50s` **in three places in one file**, against a
    measured 68s, and the suite as `~40s` and `~55s` against 53s. So they are
    deleted rather than pinned — D108's own move, applied to the one class of
    figure that has no owner to re-derive from. `check_gates.py` prints its
    elapsed time per gate on every run, so the command reports what the prose
    used to guess.

    A measured duration that some artifact *does* own is a different thing
    and is not matched here: A6's `52.4s` comes from `eval/report.md` and is
    checked below. That is why this keys on the tilde.
    """
    for name, text in (("README.md", readme), ("CLAUDE.md", claude)):
        offenders = [
            (text.count("\n", 0, m.start()) + 1, m.group(0))
            for m in re.finditer(r"~\s?\d+(\.\d+)?\s?s\b", text)
        ]
        assert not offenders, (
            f"{name} states a wall-clock figure at {offenders}. These are "
            "deleted, not pinned — see this test's docstring."
        )


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


def _gate_row(readme: str, gate: str) -> str:
    """The README's acceptance-gate table row for `gate`, without its cells."""
    match = re.search(rf"^\| {gate} \| (.*) \|$", readme, re.M)
    assert match, f"README's gate table no longer has an {gate} row"
    return match.group(1)


def _determinations(report: str) -> int:
    """A6's denominator, from the cost section's own row."""
    cost_start = report.index("## Cost and latency")
    cost_end = report.index("\n## ", cost_start + 1)
    match = re.search(r"\| Determinations \| (\d+) \|", report[cost_start:cost_end])
    assert match, "the report's cost table no longer states a determination count"
    return int(match.group(1))


def test_the_acceptance_figures_in_both_documents_come_from_the_report(readme, claude, report):
    """A2, A5, A6 and A6's denominator — in **both** documents.

    CLAUDE.md's three were the ones wrong for two tasks (D108), so they were
    pinned and stayed right. README's gate table, twenty lines below a
    sentence saying every figure in it is re-derived from the report, was
    not pinned — and it carried A2 at a 0.467 base rate, A5 at 0.333 and A6
    over sixteen determinations two closes after the report moved to 0.420,
    0.407 and seventeen. A pin on one copy is a pin on one copy.

    Each figure is re-read from `eval/report.md`, which `build_report.py
    --verify` recomputes from the committed recordings, and required in both
    documents' prose. The report is the source; each file holds a copy.
    """
    base_rate = re.search(r"base rate \(labeled pairs that are `MET`\) \| [\d/]+ = \*\*([\d.]+)\*\*", report)
    abstention = re.search(r"Abstention rate: [\d/]+ answered = ([\d.]+)", report)
    assert base_rate and abstention, "the report's shape moved; re-read it here"
    rate, abstained = base_rate.group(1), abstention.group(1)

    assert f"{rate} base rate" in claude, (
        f"CLAUDE.md does not carry the report's A2 base rate ({rate})"
    )
    assert f"A5 abstention {abstained}" in claude, (
        f"CLAUDE.md does not carry the report's A5 abstention rate ({abstained})"
    )
    assert f"**{rate}** base rate" in _gate_row(readme, "A2"), (
        f"README's A2 row does not carry the report's base rate ({rate})"
    )
    assert f"abstention **{abstained}**" in _gate_row(readme, "A5"), (
        f"README's A5 row does not carry the report's abstention rate ({abstained})"
    )

    # Scoped to A6's own section: the tier comparison above it has rows with
    # the same labels, and an unscoped search reads the wrong table.
    #
    # Bounded at the **end** of that section too, since T-95 (D116). It used
    # to run to the end of the file, which was safe only because `## Cost and
    # latency` was the last section with a table; a section after it lands
    # inside the slice, and a non-greedy match that starts in the cost table
    # and finishes in a later one would pair figures across the boundary.
    cost_start = report.index("## Cost and latency")
    cost_end = report.index("\n## ", cost_start + 1)
    cost_section = report[cost_start:cost_end]
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
    a6 = f"{calls} model calls / {int(tin):,} in / {int(tout):,} out"
    assert a6 in _gate_row(readme, "A6"), f"README's A6 row does not read {a6!r}"

    # The denominator is written as a number word in both documents; every
    # occurrence is checked, and a stale one is the one that drifted.
    determinations = _determinations(report)
    for name, text in (("README.md", readme), ("CLAUDE.md", claude)):
        counted = _counted_phrases(" ".join(text.split()), r"across (\S+) determinations")
        assert counted, f"{name} no longer states A6's determination count"
        for stated, phrase in counted:
            assert stated == determinations, (
                f"{name} says {phrase!r}; the report counts {determinations} "
                "determinations (working rule 12)"
            )


@pytest.fixture(scope="module")
def coverage():
    """`scripts/check_req_coverage.py`, loaded rather than run: its counts
    are the owner of README's A7 row, and importing it spends nothing."""
    spec = importlib.util.spec_from_file_location(
        "check_req_coverage", REPO_ROOT / "scripts" / "check_req_coverage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_readmes_a7_row_comes_from_the_coverage_gate(readme, coverage):
    """A7's three counts are the coverage gate's own, re-derived here.

    README said *63 requirements: 61 mapped* while the gate printed 68 and
    66 — T-96 and T-97 minted five requirements between them and nothing
    compared the row to the script that counts them. The gate owns these
    figures the way the report owns A2's.
    """
    spec_text = SPEC.read_text(encoding="utf-8")
    requirements = len(coverage.spec_requirements(spec_text))
    mapped = len(coverage.MAPPING)
    unclaimed = len(coverage.unclaimed_table(spec_text))
    expected = (
        f"{requirements} requirements: {mapped} mapped to a check, "
        f"{unclaimed} declared unclaimed"
    )
    assert expected in _gate_row(readme, "A7"), (
        f"README's A7 row does not read {expected!r}; check_req_coverage.py "
        "owns these counts (working rule 12)"
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


def test_the_readmes_corpus_figures_come_from_the_report(readme, report):
    """P3's counts, which were stale for two tasks (T-95, D116).

    `Where this system degrades` said *eleven patients … seven policy
    documents, twenty cases* while the corpus was fourteen, nine and
    twenty-four, and the README contradicted its own A1 row twenty lines
    later. The report has rendered the corrected sentence since T-93 — it is
    computed by `_corpus_counts` from the three manifests that own it — so
    this was a copy nothing compared, which is exactly what D108 is about.
    """
    corpus = re.search(
        r"The corpus is (\d+) patients, (\d+) chart notes and (\d+) policy documents\.",
        report,
    )
    assert corpus, "the report's corpus sentence moved; re-read it here"
    patients, notes, documents = corpus.groups()

    cases = re.search(r"(\d+) labeled cases\.", report)
    assert cases, "the report's case count moved"

    degrades = readme[readme.index("## Where this system degrades") :]
    degrades = degrades[: degrades.index("\n## ", 1)]
    problem = degrades[degrades.index("- **P3") :]
    problem = problem[: problem.index("\n- **P4")]

    for label, value in (
        ("patients", patients),
        ("chart notes", notes),
        ("policy documents", documents),
        ("cases", cases.group(1)),
    ):
        assert value in problem, (
            f"P3 does not carry the report's {label} figure ({value}); the "
            "report owns it and this bullet is a copy (rule 12)"
        )


def test_the_readmes_per_practice_table_comes_from_the_report(readme, report):
    """The compatibility account owns the classification; README copies it.

    T-95 generated the figures README had been stating by hand, so rule 12
    applies to them from that moment: the copy is re-derived from the owner,
    never the reverse. Each rendered row is required verbatim, which is the
    cheapest form the pin can take and the one that cannot drift by a digit.
    """
    section = report[report.index("## Cross-practice compatibility") :]
    per_practice = section[section.index("### Per practice") :]
    per_practice = per_practice[: per_practice.index("**")]

    rows = [
        line.strip()
        for line in per_practice.splitlines()
        if line.startswith("| ") and "---" not in line and not line.startswith("| Practice")
    ]
    assert len(rows) == 3, "the report renders one row per practice"

    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        practice, trees, criteria = cells[0], cells[1], cells[2]
        reused, earned, unclaimed = cells[3], cells[4], cells[5]
        copy = f"| {practice} | {trees} | {criteria} | {reused} | {earned} | {unclaimed} |"
        assert copy in readme, (
            f"README's per-practice table does not carry the report's row for "
            f"{practice!r}: expected {copy!r} (rule 12, D116)"
        )


# --------------------------------------------------------------------------
# The documents own their own ID ranges
# --------------------------------------------------------------------------


def test_the_precedence_table_states_the_real_id_ranges(claude):
    """The four that went stale inside the close that moved them.

    `CLAUDE.md`'s precedence table names the ID range of every document it
    ranks. T-96 minted REQ-62, wrote a task record and appended D118 — and
    left the table reading `REQ-61`, `T-95`, `D117` and *T-96 through T-125
    are reserved* for a row that was no longer reserved. Every gate was
    green, because the table was prose nothing re-derived.

    Each range is now read from the document that owns it. No magic
    constants: the reserved block is checked by asserting no record exists
    inside it, which is what "reserved" means.
    """
    requirements = [int(n) for n in re.findall(r"^\*\*REQ-(\d+)", SPEC.read_text(encoding="utf-8"), re.M)]
    decisions = [int(n) for n in re.findall(r"^## D(\d+)", DECISIONS.read_text(encoding="utf-8"), re.M)]
    records = {int(n) for n in re.findall(r"^### `\[.\] T-(\d+)`", TASKS.read_text(encoding="utf-8"), re.M)}
    assert requirements and decisions and records, "a document's IDs stopped parsing"

    # Every occurrence, not the first — a right copy must not be able to hide
    # a wrong one. That is the weakness this file was rebuilt around.
    _every_range_claim(claude, r"REQ-1 through REQ-(\d+)", max(requirements), "REQ")
    _every_range_claim(claude, r"D1\u2013D(\d+)", max(decisions), "decision")

    cell = re.search(
        r"Task records T-00 through T-(\d+) plus ([^,]+?), each with a runnable "
        r"exit condition; T-(\d+) through T-(\d+) are reserved",
        claude,
    )
    assert cell, "the precedence table's task sentence moved; re-read it here"
    through, extras_text, reserved_from, reserved_to = cell.groups()
    extras = {int(n) for n in re.findall(r"T-(\d+)", extras_text)}
    covered = set(range(0, int(through) + 1)) | extras

    uncovered = sorted(records - covered)
    assert not uncovered, (
        f"the precedence table covers T-00 through T-{through} plus "
        f"{sorted(extras)}, but records exist for {uncovered}"
    )
    assert extras <= records, (
        f"the table names {sorted(extras - records)} as a written record, and none exists"
    )
    assert int(through) in records, (
        f"the table reads 'through T-{through}', and no such record exists"
    )
    occupied = sorted(n for n in records if int(reserved_from) <= n <= int(reserved_to))
    assert not occupied, (
        f"T-{reserved_from} through T-{reserved_to} are called reserved, but "
        f"{occupied} already have records"
    )


# --------------------------------------------------------------------------
# The knowledge corpus owns its own counts
# --------------------------------------------------------------------------


def test_the_knowledge_corpus_figures_come_from_the_corpus(readme, claude):
    """Five labels and five rows, re-derived from the files that hold them.

    Both documents state these in prose, and both were written by hand in
    the same close that created the files. The corpus is the owner (working
    rule 12); growing it must move the prose or turn the suite red.
    """
    labels = len(json.loads((KNOWLEDGE / "sources.json").read_text(encoding="utf-8"))["documents"])
    table = json.loads((KNOWLEDGE / "medication_effects.json").read_text(encoding="utf-8"))
    rows = len(table["rows"])
    on_disk = len(list((KNOWLEDGE / "source").glob("*.txt")))

    assert labels == on_disk, (
        f"the knowledge manifest records {labels} documents and {on_disk} are on disk"
    )
    assert table["declared_constants"] == rows, (
        "every row declares one threshold constant; the table's count disagrees"
    )

    # Prose wraps. A phrase check that a line break defeats goes quiet exactly
    # when someone reflows a paragraph, so both documents are flattened first.
    flat = {"README.md": " ".join(readme.split()), "CLAUDE.md": " ".join(claude.split())}

    for name, text in flat.items():
        counted = _counted_phrases(text, r"(\S+)\s+FDA\s+(?:drug\s+)?labels?\b")
        assert counted, f"{name} states no count of the label corpus"
        for stated, phrase in counted:
            assert stated == labels, (
                f"{name} says {phrase!r}; the knowledge corpus holds {labels} labels"
            )

    counted = _counted_phrases(
        flat["CLAUDE.md"], r"medication_effects\.json[^.]{0,120}?(\S+)\s+rows\b"
    )
    assert counted, "CLAUDE.md states no row count for the knowledge table"
    for stated, phrase in counted:
        assert stated == rows, (
            f"CLAUDE.md says {phrase!r}; the table has {rows} rows"
        )


# --------------------------------------------------------------------------
# The board owns each version's state
# --------------------------------------------------------------------------


def _board_states(board: str) -> dict[str, str]:
    """version -> complete / in progress / planned, from the board's roadmap.

    The board marks a version's state inside its *Delivers* cell — `· **closed**`
    or `· **in progress**` — and an unmarked row is a version not yet opened.
    """
    section = board[board.index("## Roadmap after v1.1") :]
    section = section[: section.index("\n### ")]
    states: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"\| (v\d[\d.]*) \| (.*?) \| (US-[\d–—-]+|—) \|", line.strip())
        if not match:
            continue
        delivers = match.group(2)
        if "**closed**" in delivers:
            states[match.group(1)] = "complete"
        elif "**in progress**" in delivers:
            states[match.group(1)] = "in progress"
        else:
            states[match.group(1)] = "planned"
    return states


def _readme_states(readme: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in readme.splitlines():
        match = re.match(
            r"\| (v\d[\d.]*) \| .*? \| (US-[\d–—-]+|—) \| ([^|]+) \|", line.strip()
        )
        if match:
            states[match.group(1)] = match.group(3).strip().strip("*").strip()
    return states


def test_the_readme_roadmap_states_agree_with_the_board(readme):
    """The roadmap pin above compares versions and stories; the *State*
    column drifted underneath it. README carried v1.3 as `planned` for two
    closed tasks while its own *Where the project stands* section, forty
    lines up, said in progress. The board is the owner (working rule 12)."""
    board = _board_states(TASKS.read_text(encoding="utf-8"))
    stated = _readme_states(readme)
    assert board, "the board's roadmap table no longer parses"
    for version, state in board.items():
        assert version in stated, f"README's roadmap has no row for {version}"
        assert stated[version] == state, (
            f"README's roadmap says {version} is {stated[version]!r}; the "
            f"board says {state!r}"
        )


# --------------------------------------------------------------------------
# The store package owns the port count
# --------------------------------------------------------------------------


def test_the_stated_port_count_is_the_adapter_count(readme, claude):
    """Three ports since T-97, and two documents plus the spec said two.

    REQ-41 was cited by D119 for the third port and still read *two storage
    ports*; README's introduction and its repository layout said the same.
    The package is the owner: one adapter module per port, `__init__`
    excluded because it imports none of them on purpose (Article VI).
    """
    stores = REPO_ROOT / "pa_agent" / "stores"
    ports = len([p for p in stores.glob("*.py") if p.name != "__init__.py"])

    spec_text = SPEC.read_text(encoding="utf-8")
    req41 = re.search(r"\*\*REQ-41\*\*(.*?)\n\n", spec_text, re.DOTALL)
    assert req41, "REQ-41 moved; re-read it here"

    sources = {
        "docs/spec.md REQ-41": req41.group(1),
        "README.md": readme,
        "CLAUDE.md": claude,
    }
    for name, text in sources.items():
        counted = _counted_phrases(" ".join(text.split()), r"(\S+)\s+storage\s+ports\b")
        assert counted, f"{name} states no storage-port count"
        for stated, phrase in counted:
            assert stated == ports, (
                f"{name} says {phrase!r}; pa_agent/stores/ holds {ports} adapters"
            )

    for name, text in (("README.md", readme), ("CLAUDE.md", claude)):
        layout = re.search(r"^\s*stores/\s+(.+)$", text, re.M)
        assert layout, f"{name}'s repository layout no longer lists stores/"
        counted = _counted_phrases(layout.group(1), r"(\S+)\s+ports\b")
        assert counted, f"{name}'s stores/ line states no port count"
        for stated, phrase in counted:
            assert stated == ports, (
                f"{name}'s stores/ line says {phrase!r}; there are {ports} ports"
            )


# --------------------------------------------------------------------------
# The eval set owns its own families
# --------------------------------------------------------------------------


def test_claude_md_names_every_eval_row_family(claude):
    """The count and the enumeration behind it must agree.

    CLAUDE.md said *twenty-seven labeled rows — spec §6's fifteen plus NP1,
    J1, RA1–RA3 and US1–US4*, which sums to twenty-four: T-97's `H1`–`H3`
    were added to the count and not to the list. So every id-prefix family
    in `eval/cases.json` has to be named in that sentence, and the count has
    to be the file's. §6's rows are the `E` family and are named as §6's.
    """
    cases = json.loads((REPO_ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]
    families = sorted({re.match(r"[A-Z]+", case["case_id"]).group(0) for case in cases})

    flat = " ".join(claude.split())
    sentence = re.search(r"holds (\S+) labeled rows — (.*?) — all `PASS`", flat)
    assert sentence, "CLAUDE.md's eval-set sentence moved; re-read it here"
    assert _as_count(sentence.group(1)) == len(cases), (
        f"CLAUDE.md says the eval set holds {sentence.group(1)} rows; "
        f"eval/cases.json holds {len(cases)}"
    )
    enumeration = sentence.group(2)
    for family in families:
        marker = r"§6" if family == "E" else rf"`{family}\d"
        assert re.search(marker, enumeration), (
            f"CLAUDE.md's enumeration of the eval set names no {family!r} row: "
            f"{enumeration!r}"
        )


# --------------------------------------------------------------------------
# The report owns spec §10's P3 as well
# --------------------------------------------------------------------------


def test_spec_p3_carries_the_reports_corpus_figures(report):
    """The same pin README's P3 has, over the spec's own P3.

    README's copy was pinned at T-95 (D116) and moved with the corpus; the
    spec's — the section README's is a summary of — was not, and it stopped
    at T-93's *eleven patients, seven documents, twenty cases* through two
    corpus rounds. Its heading and its precision sentence are checked here
    against the report's corpus sentence and precision table.
    """
    spec_text = SPEC.read_text(encoding="utf-8")

    corpus = re.search(
        r"The corpus is (\d+) patients, (\d+) chart notes and (\d+) policy documents\.",
        report,
    )
    cases = re.search(r"(\d+) labeled cases\.", report)
    precision_section = report[report.index("## Per-criterion precision") :]
    precision_section = precision_section[: precision_section.index("### The base rate")]
    said_met = re.search(r"\| \*\*all\*\* \| \*\*\d+\*\* \| \*\*(\d+)\*\* \|", precision_section)
    measured = re.search(r"\*\*A2's threshold is [\d.]+ on `MET`\.\*\* Measured: \*\*([\d.]+)\*\*", report)
    base_rate = re.search(r"base rate \(labeled pairs that are `MET`\) \| [\d/]+ = \*\*([\d.]+)\*\*", report)
    assert corpus and cases and said_met and measured and base_rate, (
        "the report's shape moved; re-read it here"
    )
    patients, _notes, documents = (int(v) for v in corpus.groups())

    heading = re.search(r"^### P3 — (\S+) patients, (\S+) documents, (\S+) cases", spec_text, re.M)
    assert heading, "spec §10's P3 heading moved; re-read it here"
    for label, word, expected in (
        ("patients", heading.group(1), patients),
        ("documents", heading.group(2), documents),
        ("cases", heading.group(3), int(cases.group(1))),
    ):
        assert _as_count(word) == expected, (
            f"P3's heading says {word!r} {label}; the report says {expected} "
            "(working rule 12)"
        )

    flat = " ".join(spec_text.split())
    sentence = re.search(
        r"A precision of ([\d.]+) over (\S+) `MET` calls against a base rate of ([\d.]+)",
        flat,
    )
    assert sentence, "P3's precision sentence moved; re-read it here"
    assert sentence.group(1) == measured.group(1), (
        f"P3 states precision {sentence.group(1)}; the report measures {measured.group(1)}"
    )
    assert _as_count(sentence.group(2)) == int(said_met.group(1)), (
        f"P3 says {sentence.group(2)!r} `MET` calls; the report counts {said_met.group(1)}"
    )
    assert sentence.group(3) == base_rate.group(1), (
        f"P3 states a base rate of {sentence.group(3)}; the report's is {base_rate.group(1)}"
    )
