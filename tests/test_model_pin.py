"""T-34 — the model a measurement runs against cannot drift from the record.

Two properties, per D20:

1. the model a bare `python spike/spike_001/run.py` would measure on is the
   model `spike/spike_001/results.json` records, so a re-run cannot silently
   replace D19's finding with one measured somewhere else;
2. no model identifier appears as a string literal in tracked Python outside
   `pa_agent/model_pin.py`.

The first is the guard. The second is what keeps the guard meaningful: a check
comparing two values is worth nothing if a third copy can be written anywhere
else, which is the state this task found the repo in.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import pytest

from pa_agent.model_pin import PINNED_MODEL, find_model_literals

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_PY = REPO_ROOT / "spike" / "spike_001" / "run.py"
RESULTS_PATH = REPO_ROOT / "spike" / "spike_001" / "results.json"

# The one file allowed to write a model identifier, because it is the file that
# defines what one looks like.
PIN_MODULE = Path("pa_agent/model_pin.py")


def _load_run_module():
    """Import `spike/spike_001/run.py` by path.

    The spike is not a package -- no `__init__.py` anywhere under `spike/` --
    and giving it one to make this import prettier would change the layout T-00
    closed on.
    """
    spec = importlib.util.spec_from_file_location("spike_001_run", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_python_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(p) for p in proc.stdout.split("\0") if p]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """`id()` of every node that is a docstring rather than a value.

    Skipped by the scan on purpose (D20): a docstring cannot be handed to a
    model call, so a stale model name in prose is a documentation defect, not a
    provenance one. Scanning them would tax every honest sentence about D19 in
    this repo.
    """
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def _string_constants(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip:
                yield node.lineno, node.value


# --------------------------------------------------------------------------
# 1. A bare re-run measures on the model the record names.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bare_run_model() -> str:
    """What `python spike/spike_001/run.py` with no arguments would use.

    Read off the parser rather than off `DEFAULT_MODEL`, because the constant
    matching the value actually passed is precisely the assumption that failed
    here once already.
    """
    return _load_run_module().build_parser().parse_args([]).model


@pytest.fixture(scope="module")
def recorded_model() -> str:
    assert RESULTS_PATH.exists(), (
        f"{RESULTS_PATH.relative_to(REPO_ROOT)} is absent; T-00 is closed on it "
        "and there is no recorded measurement to guard."
    )
    model = json.loads(RESULTS_PATH.read_text()).get("model")
    assert model, "results.json records no model; the measurement has no provenance"
    return model


def test_bare_run_measures_on_the_recorded_model(bare_run_model, recorded_model):
    assert bare_run_model == recorded_model, (
        f"a bare run would measure on {bare_run_model!r} while results.json "
        f"records {recorded_model!r}. Re-running would overwrite that finding "
        "with numbers from a different model and every other gate would still "
        "return zero. Re-measure deliberately and move both together, or fix "
        "the pin."
    )


def test_the_pin_is_what_a_bare_run_uses(bare_run_model):
    assert bare_run_model == PINNED_MODEL, (
        f"the spike would run against {bare_run_model!r}, which is not the "
        f"pinned model {PINNED_MODEL!r}. The pin is only a guard while every "
        "model call resolves through it."
    )


def test_the_pinned_model_is_the_one_the_record_names(recorded_model):
    assert PINNED_MODEL == recorded_model, (
        f"the pin names {PINNED_MODEL!r} but the recorded measurement was made "
        f"on {recorded_model!r}. D20: the pin follows the measurement, so this "
        "means either the pin moved without a re-measurement or D19 needs one."
    )


# --------------------------------------------------------------------------
# 2. Exactly one file writes a model identifier.
# --------------------------------------------------------------------------


def test_no_model_identifier_outside_the_pin_module():
    offenders: list[str] = []
    scanned = 0

    for rel in _tracked_python_files():
        if rel == PIN_MODULE:
            continue
        scanned += 1
        for lineno, value in _string_constants(REPO_ROOT / rel):
            for literal in find_model_literals(value):
                offenders.append(f"{rel}:{lineno}  {literal!r}")

    assert scanned, "no tracked Python files were scanned; the check is vacuous"
    assert not offenders, (
        "model identifiers written as string literals outside "
        f"{PIN_MODULE}:\n  " + "\n  ".join(offenders) + "\n"
        "Import the pin instead. A second copy is how three identifiers in two "
        "files came to disagree with the recorded measurement and with each "
        "other, without failing anything (D20)."
    )


def test_the_scan_would_catch_a_stray_literal(tmp_path):
    """The scan is worth nothing if it cannot fail.

    Its regex lives in the module it exempts, so nothing else in the suite
    exercises it against a positive case.
    """
    stray = tmp_path / "stray.py"
    stray.write_text(f'MODEL = "{PINNED_MODEL}"\n')

    hits = [
        literal
        for _, value in _string_constants(stray)
        for literal in find_model_literals(value)
    ]
    assert hits, "the scan does not recognize the pinned model as a model identifier"


def test_the_scan_ignores_a_docstring(tmp_path):
    documented = tmp_path / "documented.py"
    documented.write_text(f'"""Measured on {PINNED_MODEL}."""\n')

    hits = [
        literal
        for _, value in _string_constants(documented)
        for literal in find_model_literals(value)
    ]
    assert not hits, "a docstring naming a model was treated as a stray literal"
