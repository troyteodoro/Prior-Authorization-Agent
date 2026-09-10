"""T-69 — the close ritual is one command, and its list cannot rot quietly (D69).

**Spends nothing, and never runs the real gates.** `scripts/check_gates.py` runs
`pytest`, and `pytest` collects this file; a test that drove it end to end would
recurse until something ran out. So `run_gates()` is driven with stub commands
here, and the recursion guard itself is asserted rather than exercised — the same
split D67 made between a measurement and the bookkeeping around it.

Three claims:

1. **A failing gate fails the command.** The reason working rule 4 gained a
   second half is that T-67 closed with `pytest` red; a runner that reports a
   failure and exits zero would reproduce that exactly.
2. **Every tracked script is classified**, as a gate or as an exclusion with a
   reason, so adding one and forgetting the list fails here instead of silently
   shrinking the ritual.
3. **The gate list is auditable against the board**: every entry names a real
   file, and `pytest` bare leads it.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_gates.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("check_gates", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_gates"] = module
    spec.loader.exec_module(module)
    return module


def _stub(results: dict[tuple[str, ...], tuple[int, str]]):
    """A runner that answers from a table and spawns nothing."""

    def run(argv: tuple[str, ...]) -> tuple[int, str]:
        return results[argv]

    return run


# --------------------------------------------------------------------------
# 1. A failing gate fails the command
# --------------------------------------------------------------------------


def test_every_gate_passing_returns_zero(script):
    gates = (("one", ("a.py",)), ("two", ("b.py",)))
    out = StringIO()
    failed = script.run_gates(
        gates, runner=_stub({("a.py",): (0, ""), ("b.py",): (0, "")}), stream=out
    )
    assert failed == 0
    assert out.getvalue().count("  ok    ") == 2


def test_one_failing_gate_is_counted_and_its_output_printed(script):
    """The defect this whole task is about, in miniature: a gate that reports a
    failure and lets the command exit zero is a gate nobody is answerable to."""
    gates = (("one", ("a.py",)), ("two", ("b.py",)))
    out = StringIO()
    failed = script.run_gates(
        gates,
        runner=_stub({("a.py",): (0, "quiet"), ("b.py",): (1, "AssertionError: boom")}),
        stream=out,
    )
    printed = out.getvalue()

    assert failed == 1
    assert "FAIL  two" in printed
    assert "AssertionError: boom" in printed, "a failure prints what it said"
    assert "quiet" not in printed, "a passing gate's output is noise"
    assert "no task closes" in printed


def test_a_failure_count_becomes_a_nonzero_exit(script, monkeypatch):
    """`main()` returns 1 on any failure — the exit code is what working rule 4
    actually reads."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(script, "run_gates", lambda: 2)
    assert script.main([]) == 1
    monkeypatch.setattr(script, "run_gates", lambda: 0)
    assert script.main([]) == 0


def test_it_refuses_to_run_inside_pytest(script, monkeypatch, capsys):
    """Not a stylistic objection: it runs the suite, and the suite collects this
    file. `PYTEST_CURRENT_TEST` is set for us right now, by pytest."""
    monkeypatch.setattr(
        script, "run_gates", lambda: pytest.fail("the real gates were run")
    )
    assert script.main([]) == 2
    assert "refusing to run inside pytest" in capsys.readouterr().err


def test_the_guard_is_read_before_anything_is_run(script):
    """The structural half, and the reason it is structural is worth reading.

    The obvious test here is to spawn `python scripts/check_gates.py` from inside
    pytest and assert it exits 2. **That test is a fork bomb**, and it was written
    and run before this one replaced it: with the guard removed the child runs the
    suite, the suite reaches this test, and it spawns another child. The mutation
    pass hung for two minutes and had to be killed. A check whose failure mode is
    an unbounded process tree is worse than the defect it looks for.

    So the guard is asserted by parsing `main`: the environment read comes before
    any call to `run_gates`, which is the ordering the whole thing depends on.
    """
    source = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    guard_line = next(
        (
            node.lineno
            for node in ast.walk(main)
            if isinstance(node, ast.Constant) and node.value == "PYTEST_CURRENT_TEST"
        ),
        None,
    )
    assert guard_line is not None, (
        "main() no longer reads PYTEST_CURRENT_TEST; it runs the suite, and the "
        "suite collects this file (D69)"
    )
    ran = [
        node.lineno
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_gates"
    ]
    assert ran, "main() no longer runs the gates"
    assert guard_line < min(ran), "the guard must be read before anything is run"


# --------------------------------------------------------------------------
# 2. Every tracked script is classified
# --------------------------------------------------------------------------


def _tracked_scripts() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "scripts/*.py", "eval/*.py", "spike/**/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [p for p in proc.stdout.split("\0") if p]


def test_every_tracked_script_is_a_gate_or_an_excluded_one_with_a_reason(script):
    """The drift protection. A new script under `scripts/`, `eval/` or `spike/`
    is either part of the close ritual or explicitly not, with the reason next to
    it — the "adding a provider is one entry here" pattern `model_pin.py` uses.

    Without this the list rots exactly the way the unwritten rule did.
    """
    tracked = _tracked_scripts()
    assert tracked, "no tracked scripts were found; the check is vacuous"

    gated = {argv[0] for _, argv in script.GATES if argv[0] != "-m"}
    unclassified = [
        path for path in tracked if path not in gated and path not in script.EXCLUDED
    ]
    assert not unclassified, (
        "tracked scripts in neither GATES nor EXCLUDED:\n  "
        + "\n  ".join(unclassified)
        + "\nClassify it: a zero-cost check named by a task's exit is a gate, "
        "anything else is an exclusion with a reason (D69)."
    )
    for path, reason in script.EXCLUDED.items():
        assert reason.strip(), f"{path} is excluded without a reason"


def test_an_unclassified_script_is_caught(script, monkeypatch):
    """The mutation this file exists to catch, run as a test: emptying the
    classification must fail, or the check above is decorative."""
    monkeypatch.setattr(script, "GATES", ())
    monkeypatch.setattr(script, "EXCLUDED", {})
    with pytest.raises(AssertionError, match="neither GATES nor EXCLUDED"):
        test_every_tracked_script_is_a_gate_or_an_excluded_one_with_a_reason(script)


# --------------------------------------------------------------------------
# 3. The list is auditable
# --------------------------------------------------------------------------


def test_every_gate_names_a_file_that_exists(script):
    """`scripts/check_req_coverage.py` is named by an exit condition on the board
    and has never been written (A7, unclaimed). A gate list that can name a
    missing file fails at close time with a `No such file` nobody expects."""
    for _, argv in script.GATES:
        if argv[0] == "-m":
            assert argv[1] == "pytest", "the only module-form gate is the suite"
            continue
        assert (REPO_ROOT / argv[0]).exists(), f"{argv[0]} does not exist"


def test_the_suite_is_the_first_gate(script):
    """Ordering is a claim: the suite subsumes the thirty-odd per-file `pytest`
    exits on the board, so it is the gate that fails first and loudest."""
    name, argv = script.GATES[0]
    assert argv[:3] == ("-m", "pytest", "-q")
    assert "suite" in name


def test_no_gate_spends_a_model_call_or_reaches_the_network(script):
    """The membership rule's second half, pinned against the commands the board
    itself marks as costly. `verify_sources.py` is in the list *only* with
    `--offline`; without it, it re-downloads (T-02)."""
    costly = {
        "scripts/run_extraction.py",
        "scripts/run_adk_extraction.py",
    }
    for _, argv in script.GATES:
        assert argv[0] not in costly, f"{argv[0]} spends model calls"
        if argv[0] == "scripts/verify_sources.py":
            assert "--offline" in argv, "the online form re-downloads the corpus"
        if argv[0] in ("scripts/select_patients.py", "spike/spike_001/run.py"):
            assert "--verify" in argv, "the bare form regenerates or measures"
