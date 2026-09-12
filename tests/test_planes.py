"""T-32 — the global plane-separation walk (Article VI, REQ-33, REQ-41, D83).

Four tests already parse one module's AST for its own import restriction, and
two more name this task where a combined handle would be caught. Every one of
them is a per-module assertion that a new module joins *by someone remembering
to add it*. This file is the walk none of them gave.

Two assertions, in opposite directions (D83):

1. **Downward from each plane's roots** — following imports down from a policy
   module never reaches a patient module, and the reverse. This needs no
   whitelist, because a composition holder sits *above* the roots and is never
   reached by walking down from one. It is Article VI's claim proper.
2. **Both-planes reachability, as an exact set** — the modules whose closure
   reaches both planes are exactly the five declared here, each with its reason.
   Set equality in both directions: a new module that quietly grows a second
   import fails, and a whitelist entry that stops reaching both fails too, so an
   entry cannot go stale.

Plus D25's scan, scoped to `pa_agent/` with `cli.py` exempt as the composition
root (REQ-41). Scoping matters: `tests/` opens fixtures and `eval/run_eval.py`
opens its own labels, and a tree-wide scan would fail on the grader and get
relaxed until it asserted nothing (D27).
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "pa_agent"

#: The two planes, named by the modules that *are* the plane rather than by the
#: modules that use one. `resolver` is policy-plane because REQ-2 resolves a
#: procedure code against the corpus and nothing else.
POLICY_ROOTS = ("pa_agent.stores.policy", "pa_agent.resolver", "pa_agent.agent.policy_tools")
PATIENT_ROOTS = ("pa_agent.stores.patient", "pa_agent.agent.patient_tools")

#: Modules that legitimately reach both planes, and why. Asserted as an exact
#: set (D83) — a subset check would have hidden `retrieval`, which nobody's list
#: had until the graph was parsed.
BOTH_PLANES: dict[str, str] = {
    "pa_agent.cli": (
        "the composition root; REQ-41 makes it the one place a store is "
        "constructed"
    ),
    "pa_agent.determination": (
        "composes the two short circuits and the workflow over both ports"
    ),
    "pa_agent.workflow": "the fixed graph; its steps read both ports",
    "pa_agent.retrieval": (
        "gather() re-reads observations and conditions from the patient port "
        "*and* the comorbidity value set from the policy port (T-46, REQ-46), "
        "because the tool payload is never the evidence path (D66)"
    ),
    "pa_agent.agent.retrieval_agent": (
        "the agentic planner; same bundle as retrieval.py, chosen by the model"
    ),
}

#: `cli.py` names storage locations because composing a store requires it. One
#: module wide, and the test says which (D25, D83).
STORAGE_SCAN_EXEMPT = ("pa_agent.cli",)

#: What "reaches storage" means, as names rather than a substring sweep.
STORAGE_NAMES = frozenset(
    {"open", "Path", "PurePath", "pathlib", "sqlite3", "connect", "glob", "listdir"}
)


# --------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path) -> set[str]:
    """Every `pa_agent.*` module this file imports, resolved to module names.

    `from pa_agent.stores import policy` and `import pa_agent.stores.policy`
    are the same edge, so a `from X import Y` whose `X.Y` is a real module
    contributes that edge too — otherwise the submodule form would be a hole
    wide enough to walk a whole plane through.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith("pa_agent"))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or not node.module.startswith("pa_agent"):
                continue
            found.add(node.module)
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if (REPO_ROOT / Path(*candidate.split("."))).with_suffix(".py").exists():
                    found.add(candidate)
    return found


@pytest.fixture(scope="module")
def graph() -> dict[str, set[str]]:
    return {
        _module_name(f): _imports(f)
        for f in sorted(PACKAGE.rglob("*.py"))
    }


def _closure(graph: dict[str, set[str]], start: str) -> set[str]:
    """Every module reachable by following imports down from `start`."""
    seen, queue = {start}, deque([start])
    while queue:
        for dep in graph.get(queue.popleft(), ()):
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return seen


def _reaches(graph: dict[str, set[str]], module: str, roots: tuple[str, ...]) -> bool:
    closure = _closure(graph, module)
    return any(root in closure for root in roots)


# --------------------------------------------------------------------------
# The graph is real before anything is asserted over it
# --------------------------------------------------------------------------


def test_the_graph_covers_every_module_and_has_edges(graph):
    """A parser that silently returns nothing makes every assertion below pass.

    This is the mutation that matters most here: the walk asserts *absence*, and
    absence is what an empty graph reports for free.
    """
    assert len(graph) >= 25, f"only {len(graph)} modules parsed; the walk is reading a subset"
    for root in POLICY_ROOTS + PATIENT_ROOTS:
        assert root in graph, f"{root} is named as a plane root and was not parsed"
    assert sum(len(v) for v in graph.values()) >= 40, "the graph has almost no edges"
    # The known-good edge, so an `_imports` that drops submodule forms is caught.
    assert "pa_agent.stores.policy" in graph["pa_agent.resolver"]


# --------------------------------------------------------------------------
# 1. Downward from each plane's roots (Article VI, REQ-33)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("root", POLICY_ROOTS)
def test_no_policy_module_reaches_the_patient_plane(graph, root):
    reached = _closure(graph, root) & set(PATIENT_ROOTS)
    assert not reached, (
        f"{root} reaches {sorted(reached)}. Article VI keeps the corpus and the "
        "chart on two planes; a policy module that can read patient data is the "
        "violation the two ports exist to prevent (REQ-33)"
    )


@pytest.mark.parametrize("root", PATIENT_ROOTS)
def test_no_patient_module_reaches_the_policy_plane(graph, root):
    reached = _closure(graph, root) & set(POLICY_ROOTS)
    assert not reached, (
        f"{root} reaches {sorted(reached)}. The direction matters as much as the "
        "other one: a patient module that can read the corpus is a second path "
        "to the criteria tree that Article VII's git-is-the-source rule does not "
        "cover (REQ-33)"
    )


# --------------------------------------------------------------------------
# 2. Both-planes reachability, as an exact set (D83)
# --------------------------------------------------------------------------


def test_exactly_the_declared_modules_reach_both_planes(graph):
    """Set equality, deliberately, and in both directions.

    A subset check ("these *may* reach both") would have passed without anyone
    noticing `retrieval` is a fifth member — which is how it was found. Equality
    also means a stale entry cannot linger: a module that stops reaching both
    fails here and has to be removed.
    """
    actual = {
        module
        for module in graph
        if _reaches(graph, module, POLICY_ROOTS) and _reaches(graph, module, PATIENT_ROOTS)
    }
    declared = set(BOTH_PLANES)
    undeclared = actual - declared
    assert not undeclared, (
        f"{sorted(undeclared)} reach both planes and are not declared. A module "
        "that touches both is a composition point and needs a stated reason in "
        "BOTH_PLANES, or it is Article VI leaking (D83)"
    )
    stale = declared - actual
    assert not stale, (
        f"{sorted(stale)} are declared as both-planes modules and no longer "
        "reach both; drop them, or the whitelist is documenting a shape the "
        "code left behind"
    )


def test_every_whitelist_entry_states_a_reason():
    """A whitelist whose entries carry no reason is a list of exceptions nobody
    can audit — T-69's `EXCLUDED` mapping is the shape being copied."""
    for module, reason in BOTH_PLANES.items():
        assert len(reason.split()) >= 5, f"{module}: reason is too thin to audit"


def test_the_plane_roots_do_not_themselves_reach_both(graph):
    """The roots define the planes. A root in BOTH_PLANES would make the first
    pair of assertions vacuous by definition rather than by evidence."""
    for root in POLICY_ROOTS + PATIENT_ROOTS:
        assert root not in BOTH_PLANES, f"{root} is a plane root and a whitelist entry"


# --------------------------------------------------------------------------
# D25's scan: storage lives in stores/ and nowhere else
# --------------------------------------------------------------------------


def _storage_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.update(
                a.name.split(".")[0]
                for a in node.names
                if a.name.split(".")[0] in STORAGE_NAMES
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in STORAGE_NAMES:
                hits.add(node.module.split(".")[0])
            hits.update(a.name for a in node.names if a.name in STORAGE_NAMES)
        elif isinstance(node, ast.Name) and node.id in STORAGE_NAMES:
            hits.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in STORAGE_NAMES:
            hits.add(node.attr)
    return hits


def test_no_module_outside_stores_names_a_storage_location():
    """D25, scoped to `pa_agent/` with `cli.py` exempt (D83).

    The exemption is one module wide and named here rather than implied: REQ-41
    makes the CLI the one place a store is constructed, and a composition root
    that cannot name a location cannot compose anything.
    """
    offenders: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        module = _module_name(path)
        if module.startswith("pa_agent.stores") or module in STORAGE_SCAN_EXEMPT:
            continue
        hits = _storage_names(path)
        if hits:
            offenders[module] = hits
    assert not offenders, (
        f"storage reach outside pa_agent/stores/: {offenders}. Article VI puts "
        "every storage location behind a port; a module that opens its own path "
        "is a second adapter nobody declared (D25, REQ-41)"
    )


def test_the_storage_scan_would_catch_something(tmp_path):
    """The scan asserts absence over a tree that is currently clean, so without
    this it passes identically with an emptied `_storage_names` (D27's lesson
    about an assertion that cannot fail)."""
    sample = tmp_path / "leaky.py"
    sample.write_text(
        "from pathlib import Path\n\nDATA = Path('/var/patients')\n", encoding="utf-8"
    )
    assert _storage_names(sample), "the scan finds nothing in an obviously leaky module"


def test_the_composition_root_is_why_the_exemption_exists():
    """If `cli.py` ever stops naming a storage location the exemption is dead
    weight and should go — asserted so it cannot quietly become one."""
    cli = PACKAGE / "cli.py"
    assert _storage_names(cli), (
        "cli.py names no storage location, so its scan exemption documents "
        "nothing; remove it from STORAGE_SCAN_EXEMPT"
    )
