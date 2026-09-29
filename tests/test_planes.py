"""T-32 — the global plane-separation walk (Article VI, REQ-33, REQ-41, D83).

Four tests already parse one module's AST for its own import restriction, and
two more name this task where a combined handle would be caught. Every one of
them is a per-module assertion that a new module joins *by someone remembering
to add it*. This file is the walk none of them gave.

Three assertions, the first two in opposite directions (D83):

1. **Downward from each plane's roots** — following imports down from a policy
   module never reaches a patient module, and the reverse. This needs no
   whitelist, because a composition holder sits *above* the roots and is never
   reached by walking down from one. It is Article VI's claim proper.
2. **Both-planes reachability, as an exact set** — the modules whose closure
   reaches both planes are exactly the five declared here, each with its reason.
   Set equality in both directions: a new module that quietly grows a second
   import fails, and a whitelist entry that stops reaching both fails too, so an
   entry cannot go stale.
3. **All-three-corpora reachability, as an exact set** (T-134, D133) — the
   modules that reach the policy, patient *and* knowledge corpora are exactly
   the one declared here. `BOTH_PLANES` cannot express this claim: it is
   satisfied by a module that knows nothing of `data/knowledge/`, which is how
   the composition root came to build a packet's document index from two ports
   while a packet's citations point into three.

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

#: The third corpus (T-97, D119). `data/knowledge/` is what a *drug* is known to
#: do — neither what a payer covers nor what one chart says — so its adapter is
#: a root of its own and must reach neither plane. It is declared here rather
#: than folded into one of the two above precisely because folding it in would
#: make "the policy plane cannot read a chart" true of a module that reads
#: neither, which is a weaker claim wearing the same words.
KNOWLEDGE_ROOTS = ("pa_agent.stores.knowledge",)

#: The fourth port, and the first that **writes** (T-100, D127). `data/sessions/`
#: is not a corpus the repository ships — it is the system's own record of having
#: answered a question — so it is a root of its own for the same reason the
#: knowledge corpus is: folding it into either plane would make "the policy plane
#: cannot read a chart" true of a module that reads neither.
SESSION_ROOTS = ("pa_agent.stores.session", "pa_agent.session")

#: The fifth and sixth ports (T-105, D134). Two roots and **not one**, because
#: `payer` is a corpus the repository ships and `outbox` is output the system
#: writes — the split D134 declined to put behind a single protocol. Each is a
#: root of its own for `KNOWLEDGE_ROOTS`' reason: folding either into a plane
#: would make "the policy plane cannot read a chart" true of a module that reads
#: neither.
#:
#: **Neither is in `ALL_READ_PLANES` below, and the set's own definition is why.**
#: That set is the modules reaching all three corpora a *citation* can address,
#: derived in `tests/test_packet_index.py` from the ports declaring
#: `get_document`. `PayerStore` declares none — a contact is not a document a span
#: points into — and the outbox is a **write** plane, so it is not a read corpus
#: at all. A packet's spans never point into either.
PAYER_ROOTS = ("pa_agent.stores.payer",)
OUTBOX_ROOTS = ("pa_agent.stores.outbox",)

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

#: Modules that reach **all three read corpora**, and why (T-134, D133).
#:
#: `BOTH_PLANES` above is Article VI's policy-and-patient pair and must keep
#: meaning exactly that, so this is a second exact set rather than a widening of
#: the first. It exists because nothing in this repository ever wrote down that a
#: module reaches every corpus: the composition root has done so since `T-97`,
#: and the only assertion about the knowledge plane was that nothing *else*
#: reaches it — which is how `cli._packet_index` came to be built from two ports
#: while a packet's citations point into three.
#:
#: The **session** plane is deliberately not a member of this claim. A session
#: store serves no `get_document` and holds no citable document, so it is not a
#: corpus a span can point into; `tests/test_packet_index.py` draws the same line
#: from the same fact, derived from the store package.
ALL_READ_PLANES: dict[str, str] = {
    "pa_agent.cli": (
        "the composition root; REQ-41 makes it the one place a store is "
        "constructed, and a packet's citations slice back through all three "
        "corpora — the chart, the policy corpus and an FDA label (REQ-74, D133)"
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
        "violation the two planes exist to prevent (REQ-33)"
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


def test_exactly_the_declared_modules_reach_all_three_read_corpora(graph):
    """Set equality again, over the three corpora a citation can address (D133).

    `BOTH_PLANES` cannot express this: it is satisfied by a module that reaches
    the policy and patient planes and knows nothing of `data/knowledge/`, which
    described `cli.py` at `T-96` and stopped being the whole truth at `T-97`. A
    module that can read every corpus is the composition point a packet's
    document index is built at, and it needs a stated reason for the same purpose
    the other whitelist serves.

    Equality in both directions, so a second module growing the third import
    fails here, and an entry that stops reaching all three cannot linger.
    """
    actual = {
        module
        for module in graph
        if _reaches(graph, module, POLICY_ROOTS)
        and _reaches(graph, module, PATIENT_ROOTS)
        and _reaches(graph, module, KNOWLEDGE_ROOTS)
    }
    declared = set(ALL_READ_PLANES)
    undeclared = actual - declared
    assert not undeclared, (
        f"{sorted(undeclared)} reach all three read corpora and are not "
        "declared. A module that can read what a payer covers, what one chart "
        "says and what a drug is known to do is a composition point and needs a "
        "stated reason in ALL_READ_PLANES (Article VI, D119, D133)"
    )
    stale = declared - actual
    assert not stale, (
        f"{sorted(stale)} are declared as three-corpus modules and no longer "
        "reach all three; drop them, or the whitelist documents a shape the code "
        "left behind"
    )
    assert declared <= set(BOTH_PLANES), (
        f"{sorted(declared - set(BOTH_PLANES))} reach all three corpora and are "
        "not declared in BOTH_PLANES, which reaching two of them requires"
    )


def test_every_three_corpus_entry_states_a_reason():
    """`test_every_whitelist_entry_states_a_reason`'s rule on the second list —
    written out rather than folded into it, because a loop over both would pass
    if one of the two dictionaries were emptied."""
    assert ALL_READ_PLANES, "the three-corpus whitelist is empty; cli.py reaches all three"
    for module, reason in ALL_READ_PLANES.items():
        assert len(reason.split()) >= 5, f"{module}: reason is too thin to audit"


@pytest.mark.parametrize("root", KNOWLEDGE_ROOTS)
def test_the_knowledge_plane_reaches_neither_of_the_other_two(graph, root):
    """The knowledge corpus holds no patient data and no policy (D119).

    Both directions matter and both are asserted: a knowledge adapter that could
    read a chart would be a third route around Article VI, and one that could
    read the policy corpus would be the coupling D118 split two manifests to
    avoid.
    """
    assert not _reaches(graph, root, POLICY_ROOTS), (
        f"{root} reaches the policy plane"
    )
    assert not _reaches(graph, root, PATIENT_ROOTS), (
        f"{root} reaches the patient plane"
    )


@pytest.mark.parametrize("root", POLICY_ROOTS + PATIENT_ROOTS)
def test_no_plane_root_reaches_the_knowledge_corpus(graph, root):
    """And neither plane reaches it. A criteria tree that could read what a drug
    is known to do would be a policy artifact with a second source of truth."""
    assert not _reaches(graph, root, KNOWLEDGE_ROOTS), (
        f"{root} reaches the knowledge corpus"
    )


@pytest.mark.parametrize("root", SESSION_ROOTS)
def test_the_session_plane_reaches_no_other_plane(graph, root):
    """The session plane holds ids and snapshots, not corpora (T-100, REQ-70).

    Both directions, as for the knowledge corpus: a session adapter that could
    read a chart or a policy would be able to store one, and REQ-70's claim is
    that it stores neither. `pa_agent.session` is in the set because the state
    machine is pure — a store import there would put a write path one directory
    above the only place the storage scan tolerates one (D127).
    """
    assert not _reaches(graph, root, POLICY_ROOTS), f"{root} reaches the policy plane"
    assert not _reaches(graph, root, PATIENT_ROOTS), f"{root} reaches the patient plane"
    assert not _reaches(graph, root, KNOWLEDGE_ROOTS), (
        f"{root} reaches the knowledge corpus"
    )


@pytest.mark.parametrize("root", POLICY_ROOTS + PATIENT_ROOTS + KNOWLEDGE_ROOTS)
def test_no_other_plane_reaches_the_session_corpus(graph, root):
    """And nothing reaches it. A criteria tree or a chart adapter that could read
    what the system has already answered would be a corpus with a second source
    of truth — and a policy plane that could read a determination is a rule
    deciding by precedent (Article VII)."""
    assert not _reaches(graph, root, SESSION_ROOTS), (
        f"{root} reaches the session corpus"
    )


@pytest.mark.parametrize("root", PAYER_ROOTS + OUTBOX_ROOTS)
def test_the_transmission_planes_reach_no_other_plane(graph, root):
    """The payer directory holds contacts and the outbox holds one rendered
    document; neither holds a corpus, a chart or a session (T-105, REQ-70).

    Both directions, as for the knowledge and session planes. A payer adapter that
    could read a chart would be a third route around Article VI, and an outbox that
    could read the policy corpus would be able to store one — while what it is
    *for* is text somebody already read.
    """
    for other in (POLICY_ROOTS, PATIENT_ROOTS, KNOWLEDGE_ROOTS, SESSION_ROOTS):
        assert not _reaches(graph, root, other), f"{root} reaches {other}"


@pytest.mark.parametrize(
    "root", POLICY_ROOTS + PATIENT_ROOTS + KNOWLEDGE_ROOTS + SESSION_ROOTS
)
def test_no_other_plane_reaches_the_transmission_planes(graph, root):
    """And nothing reaches them. A criteria tree that could read who a packet was
    sent to would be a rule deciding by precedent (Article VII), and a chart
    adapter that could read the outbox would be a second copy of what left."""
    assert not _reaches(graph, root, PAYER_ROOTS), f"{root} reaches the payer directory"
    assert not _reaches(graph, root, OUTBOX_ROOTS), f"{root} reaches the outbox"


def test_the_transmission_planes_are_not_three_corpus_modules(graph):
    """The classification D134 had to make, asserted rather than assumed.

    `ALL_READ_PLANES` is *reaches all three read corpora*, and a write plane is
    not a read corpus. If either of these ever satisfied that predicate it would
    mean the adapter had grown an import into a corpus, so the check is that they
    do not — stated here because a set nobody may join is a set whose membership
    rule is invisible.
    """
    for root in PAYER_ROOTS + OUTBOX_ROOTS:
        assert root not in ALL_READ_PLANES
        assert root not in BOTH_PLANES
        assert not _reaches(graph, root, POLICY_ROOTS + PATIENT_ROOTS)


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
