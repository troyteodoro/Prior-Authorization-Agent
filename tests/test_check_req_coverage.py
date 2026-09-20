"""T-23 — the REQ coverage gate's four failures (A7, D87).

The gate asserts that every requirement is spoken for. Every assertion it makes
is about *absence* — nothing unmapped, nothing undeclared, nothing pointing at a
missing file — and an assertion about absence passes for free if the parser
returns nothing. So the parser is pinned before anything else.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_req_coverage.py"
SPEC = REPO_ROOT / "docs" / "spec.md"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("check_req_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_req_coverage"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def spec_text():
    return SPEC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# The repo, and the parser the repo's result depends on
# --------------------------------------------------------------------------


def test_the_committed_repo_passes(script):
    assert script.main([]) == 0


def test_the_parser_finds_every_requirement(script, spec_text):
    """The universe, pinned. An emptied parser makes every check below vacuous
    and the gate green — which is the failure mode of asserting absence."""
    requirements = script.spec_requirements(spec_text)
    assert len(requirements) == 58, f"parsed {len(requirements)}; §5 declares 58"
    assert "REQ-1" in requirements and "REQ-56" in requirements
    assert "REQ-18a" in requirements, "the lettered id is load-bearing and parses"
    assert "REQ-34a" in requirements, "T-81's split of REQ-34 parses (D104)"


def test_an_empty_spec_fails_rather_than_passing(script):
    with pytest.raises(script.CheckFailed, match="no requirements parsed"):
        script.spec_requirements("# a spec with no requirements\n")


def test_the_unclaimed_table_is_read_not_assumed_empty(script, spec_text):
    """A7 admits a list, and a gate that assumed it empty would fail forever or
    skip two requirements silently (D70)."""
    unclaimed = script.unclaimed_table(spec_text)
    assert set(unclaimed) == {"REQ-44", "REQ-47"}
    for _why, claims in unclaimed.values():
        assert claims


def test_a_spec_without_the_section_fails(script):
    with pytest.raises(script.CheckFailed, match="Unclaimed in v1"):
        script.unclaimed_table("**REQ-1** something\n")


# --------------------------------------------------------------------------
# Failure 1 — a requirement in neither
# --------------------------------------------------------------------------


def test_an_unmapped_requirement_fails(script):
    with pytest.raises(script.CheckFailed, match="map to no check"):
        script.check_completeness(["REQ-1", "REQ-999"], {})


def test_a_requirement_deleted_from_the_mapping_fails(script, spec_text, monkeypatch):
    """T-23's mutation: a REQ silently dropped from the mapping."""
    thinned = dict(script.MAPPING)
    thinned.pop("REQ-13")
    monkeypatch.setattr(script, "MAPPING", thinned)
    with pytest.raises(script.CheckFailed, match="REQ-13"):
        script.check_completeness(
            script.spec_requirements(spec_text), script.unclaimed_table(spec_text)
        )


# --------------------------------------------------------------------------
# Failure 2 — an unclaimed row nobody justified
# --------------------------------------------------------------------------


def test_an_unclaimed_row_without_a_decision_entry_fails(script):
    """A7's closing sentence. A list that absorbs whatever is inconvenient makes
    the gate meaningless rather than satisfiable."""
    rows = {"REQ-99": ("because it is hard to check right now", "a later amendment")}
    with pytest.raises(script.CheckFailed, match="names it"):
        script.check_unclaimed_are_justified(rows, "D1 — an entry about nothing\n")


def test_an_unclaimed_row_without_a_reason_fails(script):
    rows = {"REQ-99": ("later", "a later amendment")}
    with pytest.raises(script.CheckFailed, match="no stated reason"):
        script.check_unclaimed_are_justified(rows, "REQ-99 is discussed here\n")


def test_an_unclaimed_row_without_a_claiming_condition_fails(script):
    rows = {"REQ-99": ("a genuinely long and stated reason goes here", "never")}
    with pytest.raises(script.CheckFailed, match="condition that would claim"):
        script.check_unclaimed_are_justified(rows, "REQ-99 is discussed here\n")


def test_the_committed_unclaimed_rows_are_justified(script, spec_text):
    decisions = (REPO_ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    script.check_unclaimed_are_justified(script.unclaimed_table(spec_text), decisions)


# --------------------------------------------------------------------------
# Failure 3 — a mapping pointing at nothing
# --------------------------------------------------------------------------


def test_a_mapping_to_a_missing_file_fails(script, monkeypatch):
    monkeypatch.setattr(script, "MAPPING", {"REQ-1": "tests/test_does_not_exist.py"})
    with pytest.raises(script.CheckFailed, match="does not exist"):
        script.check_mapping_targets(REPO_ROOT, set())


def test_a_mapping_outside_tests_must_name_a_gate(script, monkeypatch):
    """The suite only runs `tests/`. A mapping to a script nothing executes
    claims a check that never runs — 100% coverage over nothing."""
    monkeypatch.setattr(script, "MAPPING", {"REQ-1": "scripts/synthesize_notes.py"})
    with pytest.raises(script.CheckFailed, match="not a gate"):
        script.check_mapping_targets(REPO_ROOT, {"eval/run_eval.py"})


def test_every_committed_mapping_target_exists(script):
    script.check_mapping_targets(REPO_ROOT, script._gate_commands())


# --------------------------------------------------------------------------
# Failure 4 — a requirement claimed twice
# --------------------------------------------------------------------------


def test_a_requirement_both_mapped_and_unclaimed_fails(script, monkeypatch):
    """Two answers to "is this claimed" is worse than none, and the table is
    where a broken check would go to retire (D87)."""
    monkeypatch.setattr(script, "MAPPING", {"REQ-44": "tests/test_spans.py"})
    with pytest.raises(script.CheckFailed, match="both mapped"):
        script.check_completeness(["REQ-44"], {"REQ-44": ("a reason here", "later")})


def test_a_mapping_for_a_requirement_the_spec_dropped_fails(script):
    with pytest.raises(script.CheckFailed, match="spec that moved"):
        script.check_completeness(["REQ-1"], {"REQ-404": ("a reason", "later")})


# --------------------------------------------------------------------------
# The mapping is a real mapping
# --------------------------------------------------------------------------


def test_the_mapping_is_not_a_catch_all(script):
    """D87's reversal condition, made visible: a mapping that points whole
    groups at one file passes while meaning less. Asserted as a spread, so the
    collapse is a failure rather than a judgement call in review."""
    from collections import Counter

    counts = Counter(script.MAPPING.values())
    assert len(counts) >= 15, f"{len(counts)} distinct checks for {len(script.MAPPING)} REQs"
    worst, share = counts.most_common(1)[0]
    assert share <= len(script.MAPPING) // 4, (
        f"{worst} covers {share} of {len(script.MAPPING)} requirements; the "
        "mapping is collapsing into a catch-all (D87)"
    )


def test_seven_requirements_are_checked_by_a_file_that_never_names_them(script):
    """The measurement that settled D87's rejected alternative.

    Deriving the mapping by searching test files for REQ numbers grades comment
    discipline rather than coverage. These are covered and unnamed, and a
    search-based gate would have called them uncovered.
    """
    unnamed = []
    for req, target in script.MAPPING.items():
        text = (REPO_ROOT / target).read_text(encoding="utf-8")
        if not re.search(rf"\b{re.escape(req)}\b", text):
            unnamed.append(req)
    assert unnamed, (
        "every mapped REQ is now named by its own check, so the search-based "
        "alternative D87 rejected would pass; re-read that entry before relying "
        "on this"
    )
