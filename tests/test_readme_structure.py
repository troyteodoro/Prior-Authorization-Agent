"""T-128 — the README's structure, compared to the engine it describes (D117, D121).

**Spends no model call and touches no network.**

`T-126` replaced two prose architecture sections with one mermaid flowchart
and pinned the README's heading order, and D117 said what made that
legitimate under Article VIII: a test that parses the diagram and compares it
to `workflow.STEPS`, so a renamed step is a red suite rather than a picture
that quietly describes last month's graph. That test was named in the exit
and never written; `T-127` found `pytest` returning 4 on the path (D120).
This is the file.

Every comparand here is read from the engine at test time — the step names,
the resolver's result types, the ports at the model boundary, the store
adapters — and the one literal is the heading sequence, which D117 chose to
be a literal so that re-ordering the document is a deliberate diff. And every
check is a function over text that a hand-written mutant must fail, because
a parser that matches nothing passes for free: a regex that finds no subgraph
compares an empty chain to nothing, and "no edge joins two planes" holds on a
diagram with no planes (D65's shape).
"""

from __future__ import annotations

import re
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest

from pa_agent import resolver, retrieval, runners, verifier
from pa_agent.workflow import STEPS

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

#: The `##` sequence, written out (D117). Re-ordering the README is a
#: deliberate diff to this tuple, never an accident of where a section was
#: appended.
EXPECTED_SECTIONS = (
    "Where the project stands",
    "What it measures, and what that means",
    "Where this system degrades",
    "The road from here",
    "The problem this design answers",
    "What a determination looks like",
    "How it works",
    "The policy corpus, and what it took to get right",
    "A full prior-auth form, mapped to these lanes",
    "Using it",
    "How the project is governed",
    "What the spike taught, and where it landed",
    "Repository layout",
)

#: The two sections the diagram replaced. They may survive as subsections
#: of *How it works*; they may not come back as top-level sections, because
#: that is the duplication the diagram removed (D117).
REPLACED_SECTIONS = (
    "How the policy file drives the engine",
    "Two implementations, one oracle",
)


# --------------------------------------------------------------------------
# A small flowchart parser — enough for this diagram, and it raises on more
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    src: str
    label: str | None
    dst: str
    dotted: bool


_NODE = re.compile(
    r"(?P<id>[A-Za-z_]\w*)"
    r'(?:\(\["(?P<stadium>[^"]*)"\]\)'
    r'|\[\("(?P<cylinder>[^"]*)"\)\]'
    r'|\["(?P<box>[^"]*)"\]'
    r'|\{"(?P<diamond>[^"]*)"\})?'
)
_ARROW = re.compile(r"\s*(?P<arrow>-->|-\.->)\s*(?:\|(?P<label>[^|]*)\|)?\s*")
_SKIP = ("flowchart", "subgraph", "end", "direction", "classDef", "class ", "%%")


def _without_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _mermaid(text: str) -> str:
    blocks = re.findall(r"```mermaid\n(.*?)```", text, flags=re.S)
    assert len(blocks) == 1, f"expected exactly one mermaid block, found {len(blocks)}"
    return blocks[0]


def _lines(block: str) -> list[tuple[str | None, str]]:
    """Each statement line with the subgraph it sits in, or None."""
    out: list[tuple[str | None, str]] = []
    current: str | None = None
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        opened = re.match(r"subgraph\s+([A-Za-z_]\w*)", line)
        if opened:
            assert current is None, "nested subgraphs are not something this parser reads"
            current = opened.group(1)
            continue
        if line == "end":
            assert current is not None, "'end' outside a subgraph"
            current = None
            continue
        out.append((current, line))
    assert current is None, "a subgraph was opened and never closed"
    return out


def _parse(block: str) -> tuple[list[tuple[str | None, Edge]], dict[str, str]]:
    """Every edge with its subgraph, and every label the diagram gives a node."""
    edges: list[tuple[str | None, Edge]] = []
    labels: dict[str, str] = {}
    for scope, line in _lines(block):
        if line.startswith(_SKIP):
            continue
        pos = 0
        node = _NODE.match(line, pos)
        assert node, f"could not read a node at the start of: {line!r}"
        prev = node.group("id")
        _record_label(labels, node)
        pos = node.end()
        while pos < len(line):
            arrow = _ARROW.match(line, pos)
            assert arrow and arrow.group("arrow"), f"could not read an arrow in: {line!r}"
            pos = arrow.end()
            node = _NODE.match(line, pos)
            assert node, f"an arrow with no node after it in: {line!r}"
            label = arrow.group("label")
            edges.append(
                (scope, Edge(prev, label.strip() if label else None, node.group("id"),
                             arrow.group("arrow") == "-.->"))
            )
            prev = node.group("id")
            _record_label(labels, node)
            pos = node.end()
    return edges, labels


def _record_label(labels: dict[str, str], node: re.Match[str]) -> None:
    for key in ("stadium", "cylinder", "box", "diamond"):
        value = node.group(key)
        if value is not None:
            labels[node.group("id")] = value


def _edges(block: str) -> list[Edge]:
    return [edge for _scope, edge in _parse(block)[0]]


def _labels(block: str) -> dict[str, str]:
    return _parse(block)[1]


def _chain(block: str, subgraph: str) -> list[str]:
    """The nodes of one subgraph, in arrow order; raises unless it is a chain."""
    scoped = [edge for scope, edge in _parse(block)[0] if scope == subgraph]
    assert scoped, f"no edges inside a subgraph named {subgraph!r}"
    chain = [scoped[0].src]
    for edge in scoped:
        assert edge.src == chain[-1], (
            f"subgraph {subgraph!r} is not a single chain: {edge.src} follows {chain[-1]}"
        )
        chain.append(edge.dst)
    return chain


def _class_members(block: str, cls: str) -> set[str]:
    found: set[str] = set()
    for members in re.findall(rf"^\s*class\s+([\w,]+)\s+{cls}\s*;", block, flags=re.M):
        found.update(members.split(","))
    return found


def _sinks(edges: list[Edge]) -> set[str]:
    return {e.dst for e in edges} - {e.src for e in edges}


# --------------------------------------------------------------------------
# The checks — each a function over text, each refused by a mutant below
# --------------------------------------------------------------------------


def check_steps(block: str, steps: list[str]) -> None:
    chain = _chain(block, "STEPS")
    assert chain == steps, f"the diagram walks {chain}; workflow.STEPS is {steps}"


#: The sentence in *What a determination looks like* that describes `STEPS`.
PROSE_STEPS = re.compile(
    r"module-level tuple of ([a-z]+) named steps walked by a plain-Python "
    r"driver:\s*\n?\s*\*([^*]+)\*"
)

SPELLED = {
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def check_prose_steps(prose: str, steps: list[str]) -> None:
    """The README's *sentence* about the graph, held to `workflow.STEPS`.

    The diagram was checked from `T-128` on; the prose above it was not, and it
    had drifted — *eight named steps*, listing eight, while the picture below
    drew ten. Every gate stayed green, because nothing compared the two
    *(T-99, D123)*. Both halves of the sentence are compared: the written-out
    count and the arrow chain, because either alone admits the other's error.
    """
    written = PROSE_STEPS.search(prose)
    assert written is not None, (
        "the prose description of `workflow.STEPS` is gone or reworded; if "
        "that was deliberate, re-point PROSE_STEPS at the new sentence"
    )
    assert SPELLED.get(written.group(1)) == len(steps), (
        f"the README says {written.group(1)!r} named steps; workflow.STEPS "
        f"has {len(steps)}"
    )
    chain = [re.sub(r"\s+", " ", name).strip() for name in written.group(2).split("→")]
    chain = [name for name in chain if name]
    assert chain == steps, (
        f"the README's prose walks {chain}; workflow.STEPS is {steps}"
    )


def check_short_circuit(block: str, results: dict[str, bool]) -> None:
    """Every result type is on an SC1 edge, routed by whether it carries a tree."""
    parsed, _labels_unused = _parse(block)
    edges = [edge for _scope, edge in parsed]
    # The determination's flow is the solid arrows outside any subgraph:
    # dotted arrows are reads across the model boundary and into the planes,
    # each ending at the thing read, and a subgraph's inner chain ends at its
    # last step while the subgraph node carries the flow on. The one sink of
    # that flow is the determination.
    flow = [edge for scope, edge in parsed if scope is None and not edge.dotted]
    sinks = _sinks(flow)
    assert len(sinks) == 1, f"expected one sink of the top-level solid flow, found {sorted(sinks)}"
    sink = next(iter(sinks))
    routed: dict[str, str] = {}
    for edge in edges:
        if edge.src != "SC1":
            continue
        assert edge.label, f"an unlabeled edge leaves SC1 for {edge.dst}"
        for name in (part.strip() for part in edge.label.split("·")):
            assert name not in routed, f"{name} is named on two SC1 edges"
            routed[name] = edge.dst
    assert set(routed) == set(results), (
        f"SC1's labels name {sorted(routed)}; resolve_sc1 returns {sorted(results)}"
    )
    for name, continues in results.items():
        if continues:
            assert routed[name] != sink, f"{name} carries a tree and must continue, not end"
        else:
            assert routed[name] == sink, f"{name} carries no tree and must end at {sink}"


def check_ports(block: str, protocols: set[str], steps: list[str]) -> None:
    """Every port is drawn, labelled with its Protocol, and reached from a step."""
    ports = _class_members(block, "port")
    assert ports, "no node is classed as a port"
    labels = _labels(block)
    edges = _edges(block)
    named: set[str] = set()
    for port in sorted(ports):
        label = labels.get(port, "")
        mine = {p for p in protocols if p in label}
        assert len(mine) == 1, f"port {port} is labelled {label!r}, which names {sorted(mine)}"
        named |= mine
        sources = {e.src for e in edges if e.dst == port}
        assert sources & set(steps), f"port {port} is reached from {sorted(sources)}, none a step"
    assert named == protocols, f"ports name {sorted(named)}; the engine declares {sorted(protocols)}"


def check_planes(block: str, adapters: set[str]) -> None:
    """Every drawn plane is a real adapter, and no edge joins two planes."""
    planes = _class_members(block, "plane")
    assert planes, "no node is classed as a plane"
    labels = _labels(block)
    for plane in sorted(planes):
        module = re.search(r"stores/(\w+)\.py", labels.get(plane, ""))
        assert module, f"plane {plane} names no stores module in {labels.get(plane)!r}"
        assert module.group(1) in adapters, (
            f"plane {plane} names stores/{module.group(1)}.py; the adapters are {sorted(adapters)}"
        )
    for edge in _edges(block):
        assert not (edge.src in planes and edge.dst in planes), (
            f"{edge.src} -> {edge.dst} joins two storage planes (Article VI)"
        )


# --------------------------------------------------------------------------
# What the engine declares — read, not written
# --------------------------------------------------------------------------


def _step_names() -> list[str]:
    return [name for name, _ in STEPS]


def _result_types() -> dict[str, bool]:
    """resolve_sc1's declared results, and whether each continues to sc2."""
    hints = typing.get_type_hints(resolver.resolve_sc1)
    classes = typing.get_args(hints["return"])
    assert len(classes) >= 2, "resolve_sc1 no longer returns a union; re-read this test"
    return {cls.__name__: "policy_ref" in cls.model_fields for cls in classes}


def _protocols() -> set[str]:
    found: set[str] = set()
    for module in (runners, retrieval, verifier):
        for value in vars(module).values():
            if (isinstance(value, type) and value.__module__ == module.__name__
                    and getattr(value, "_is_protocol", False)):
                found.add(value.__name__)
    return found


def _adapters() -> set[str]:
    return {
        path.stem for path in (REPO_ROOT / "pa_agent" / "stores").glob("*.py")
        if path.stem != "__init__"
    }


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def diagram(readme: str) -> str:
    return _mermaid(readme)


# --------------------------------------------------------------------------
# The README
# --------------------------------------------------------------------------


def test_the_heading_order_is_the_literal(readme):
    headings = re.findall(r"^## (.+)$", _without_fences(readme), flags=re.M)
    assert tuple(headings) == EXPECTED_SECTIONS, (
        "the README's `##` sequence moved; if that was deliberate, change "
        "EXPECTED_SECTIONS in the same commit (D117)"
    )


def test_the_prose_step_list_is_the_engines_too(readme):
    check_prose_steps(_without_fences(readme), _step_names())


def test_the_replaced_sections_are_not_top_level_again(readme):
    headings = re.findall(r"^## (.+)$", _without_fences(readme), flags=re.M)
    back = [name for name in REPLACED_SECTIONS if name in headings]
    assert not back, f"{back} came back as top-level sections; the diagram replaced them"


def test_the_readme_carries_exactly_one_diagram(readme):
    assert readme.count("```mermaid") == 1


def test_the_diagram_walks_workflow_steps_in_order(diagram):
    check_steps(diagram, _step_names())


def test_short_circuit_one_names_every_resolver_result_and_routes_it(diagram):
    results = _result_types()
    assert len(results) == 5, f"resolve_sc1 declares {len(results)} results; re-read the diagram"
    assert set(results.values()) == {True, False}, "some results continue and some end"
    check_short_circuit(diagram, results)


def test_every_model_boundary_port_is_drawn_from_a_step(diagram):
    protocols = _protocols()
    assert len(protocols) == 3, f"the engine declares {sorted(protocols)}; three ports expected"
    check_ports(diagram, protocols, _step_names())


def test_no_edge_joins_two_storage_planes_and_every_plane_is_an_adapter(diagram):
    adapters = _adapters()
    assert len(adapters) >= 2, f"stores holds {sorted(adapters)}"
    check_planes(diagram, adapters)


# --------------------------------------------------------------------------
# The mutants — each check must refuse a diagram that is wrong in one place
# --------------------------------------------------------------------------


def _mutated(diagram: str, old: str, new: str) -> str:
    assert diagram.count(old) == 1, f"the mutant's anchor {old!r} is not unique in the diagram"
    return diagram.replace(old, new)


def test_prose_with_a_dropped_step_is_refused(readme):
    """The real drift: a step missing and the count adjusted to match, which
    reads perfectly *(T-99, D123)*."""
    mutant = _without_fences(readme).replace(
        "→ criteria_c → unclaimed → sufficiency → verify*",
        "→ criteria_c → verify*",
    ).replace("tuple of ten named steps", "tuple of eight named steps")
    with pytest.raises(AssertionError):
        check_prose_steps(mutant, _step_names())


def test_prose_with_a_renamed_step_is_refused(readme):
    """A count that still adds up, so only comparing the chain catches it."""
    mutant = _without_fences(readme).replace("→ sufficiency →", "→ sufficient →")
    with pytest.raises(AssertionError):
        check_prose_steps(mutant, _step_names())


def test_prose_with_a_reordered_step_is_refused(readme):
    """Same names, same count, wrong order — the case a set comparison or a
    length check would both wave through."""
    mutant = _without_fences(readme).replace(
        "*gather → extract → criterion_a →", "*extract → gather → criterion_a →"
    )
    with pytest.raises(AssertionError):
        check_prose_steps(mutant, _step_names())


def test_prose_with_a_wrong_count_is_refused(readme):
    """The chain right and the number wrong — the half a chain-only check
    would miss."""
    mutant = _without_fences(readme).replace(
        "tuple of ten named steps", "tuple of nine named steps"
    )
    with pytest.raises(AssertionError):
        check_prose_steps(mutant, _step_names())


def test_a_renamed_step_is_refused(diagram):
    mutant = _mutated(diagram, "sufficiency --> verify", "sufficient --> verify")
    with pytest.raises(AssertionError):
        check_steps(mutant, _step_names())


def test_a_reordered_step_is_refused(diagram):
    mutant = _mutated(diagram, "extract --> criterion_a", "criterion_a --> extract")
    with pytest.raises(AssertionError):
        check_steps(mutant, _step_names())


def test_a_diagram_with_no_steps_subgraph_is_refused(diagram):
    mutant = _mutated(diagram, "subgraph STEPS[", "subgraph STEPZ[")
    with pytest.raises(AssertionError, match="no edges inside a subgraph"):
        check_steps(mutant, _step_names())


def test_a_dropped_result_type_is_refused(diagram):
    mutant = _mutated(
        diagram,
        "NotCovered · NoPolicyFound · NoJurisdictionTree",
        "NotCovered · NoJurisdictionTree",
    )
    with pytest.raises(AssertionError, match="SC1's labels name"):
        check_short_circuit(mutant, _result_types())


def test_a_result_routed_the_wrong_way_is_refused(diagram):
    mutant = _mutated(diagram, "|Resolved · ResolvedByContractor| SC2", "|Resolved · ResolvedByContractor| OUT")
    with pytest.raises(AssertionError, match="must continue"):
        check_short_circuit(mutant, _result_types())


def test_a_port_reached_from_no_step_is_refused(diagram):
    mutant = _mutated(diagram, "verify -.-> VERI", "AGG -.-> VERI")
    with pytest.raises(AssertionError, match="none a step"):
        check_ports(mutant, _protocols(), _step_names())


def test_a_port_that_is_not_drawn_is_refused(diagram):
    mutant = _mutated(diagram, "class PLAN,EXTR,VERI port;", "class PLAN,EXTR port;")
    with pytest.raises(AssertionError, match="ports name"):
        check_ports(mutant, _protocols(), _step_names())


def test_an_edge_between_the_planes_is_refused(diagram):
    mutant = diagram + "\n    PAT --> POL\n"
    with pytest.raises(AssertionError, match="joins two storage planes"):
        check_planes(mutant, _adapters())


def test_a_plane_naming_no_adapter_is_refused(diagram):
    mutant = _mutated(diagram, "stores/policy.py", "stores/ledger.py")
    with pytest.raises(AssertionError, match="the adapters are"):
        check_planes(mutant, _adapters())


def test_a_diagram_with_no_planes_is_refused(diagram):
    mutant = _mutated(diagram, "class PAT,POL plane;", "")
    with pytest.raises(AssertionError, match="no node is classed as a plane"):
        check_planes(mutant, _adapters())
