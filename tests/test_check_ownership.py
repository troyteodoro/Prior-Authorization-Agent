"""The ownership gate can fail, and each failure class is caught (T-73, D74).

Every drift-check here is paired with a mutation that proves the check is not
decorative: an emptied parser, a dropped ID class, a severed task link each
turn the gate red rather than silently narrowing what it protects.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_ownership.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("check_ownership", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_ownership"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def committed(script):
    ids = script.enumerate_ids(REPO_ROOT)
    ledger = json.loads((REPO_ROOT / "docs" / "ratifications.json").read_text())
    return ids, ledger


def _entry(status="proposed", by="agent", date="2026-09-10", **extra):
    return {"status": status, "by": by, "date": date, **extra}


# ---------------------------------------------------------------- the repo


def test_the_committed_repo_passes(script):
    assert script.main([]) == 0


def test_the_ledger_covers_exactly_the_documented_ids(committed):
    ids, ledger = committed
    assert set(ledger["entries"]) == set(ids)


def test_decisions_core_matches_the_set_d74_froze(script):
    """The frozen list lives in two places — D74's text and the script — and
    this is the check that they cannot drift apart in silence."""
    text = (REPO_ROOT / "docs" / "decisions.md").read_text()
    match = re.search(r"^## D74 — ", text, re.MULTILINE)
    assert match, "D74 is not in the log"
    entry = text[match.start():]
    nxt = re.search(r"^## D7[5-9] — ", entry, re.MULTILINE)
    if nxt:
        entry = entry[: nxt.start()]
    frozen = re.search(r"frozen here[^*]*\*\*([^*]+)\*\*", entry, re.S)
    assert frozen, "D74 no longer states the frozen decisions-core list"
    stated = set(re.findall(r"D\d+", frozen.group(1)))
    assert stated == set(script.DECISIONS_CORE)


# ------------------------------------------------------------ failure modes


def test_an_id_missing_from_the_ledger_fails(script):
    ids = {"D1": ("decisions-all",)}
    with pytest.raises(script.CheckFailed, match="missing from the ledger"):
        script.check_ledger({"entries": {}, "required_tiers": []}, ids, set())


def test_a_ledger_id_resolving_in_no_document_fails(script):
    ids = {"D1": ("decisions-all",)}
    entries = {"D1": _entry(), "D999": _entry()}
    with pytest.raises(script.CheckFailed, match="resolving in no document"):
        script.check_ledger({"entries": entries, "required_tiers": []}, ids, set())


def test_an_overrule_without_a_resolving_task_fails(script):
    ids = {"D1": ("decisions-all",), "T-01": ("tasks",)}
    entries = {
        "D1": _entry(status="overruled", by="troy", task="T-999"),
        "T-01": _entry(),
    }
    with pytest.raises(script.CheckFailed, match="must link a task"):
        script.check_ledger({"entries": entries, "required_tiers": []}, ids, set())


def test_an_overrule_linking_a_board_task_passes(script):
    ids = {"D1": ("decisions-all",), "T-01": ("tasks",)}
    entries = {
        "D1": _entry(status="overruled", by="troy", task="T-01"),
        "T-01": _entry(),
    }
    script.check_ledger({"entries": entries, "required_tiers": []}, ids, set())


@pytest.mark.parametrize(
    "bad",
    [
        _entry(status="approved"),
        _entry(by=""),
        _entry(date="yesterday"),
        "not-an-object",
    ],
)
def test_a_malformed_entry_fails(script, bad):
    ids = {"D1": ("decisions-all",)}
    with pytest.raises(script.CheckFailed):
        script.check_ledger({"entries": {"D1": bad}, "required_tiers": []}, ids, set())


def test_a_required_tier_fails_on_a_proposed_entry(script):
    ids = {"Article I": ("constitution",)}
    ledger = {"entries": {"Article I": _entry()}, "required_tiers": []}
    with pytest.raises(script.CheckFailed, match="still 'proposed'"):
        script.check_ledger(ledger, ids, {"constitution"})


def test_a_required_tier_passes_ratified_and_linked_statuses(script):
    """`amended`/`overruled` pass a required tier — failing them would hold
    every gate red until the linked task closed, deadlocking working rule 4
    (D74)."""
    ids = {"Article I": ("constitution",), "REQ-1": ("spec",), "T-01": ("tasks",)}
    entries = {
        "Article I": _entry(status="ratified", by="troy"),
        "REQ-1": _entry(status="amended", by="troy", task="T-01"),
        "T-01": _entry(status="ratified", by="troy"),
    }
    ledger = {"entries": entries, "required_tiers": []}
    script.check_ledger(ledger, ids, {"constitution", "spec", "tasks"})


def test_required_tiers_in_the_ledger_bind_the_bare_invocation(script):
    ids = {"Article I": ("constitution",)}
    ledger = {"entries": {"Article I": _entry()}, "required_tiers": ["constitution"]}
    with pytest.raises(script.CheckFailed, match="still 'proposed'"):
        script.check_ledger(ledger, ids, set())


# ----------------------------------------------------------- adjudications


def test_an_unadjudicated_manifest_fails(script, tmp_path):
    (tmp_path / "eval" / "manifests").mkdir(parents=True)
    manifest = tmp_path / "eval" / "manifests" / "p1.json"
    manifest.write_text(json.dumps({"patient_id": "p1"}))
    with pytest.raises(script.CheckFailed, match="no 'adjudicated' record"):
        script.check_adjudications(tmp_path, ["eval/manifests/p1.json"])


def test_every_case_in_the_eval_set_needs_its_own_adjudication(script, tmp_path):
    (tmp_path / "eval").mkdir()
    cases = tmp_path / "eval" / "cases.json"
    good = {"case_id": "E1", "adjudicated": {"by": "troy", "date": "2026-09-10"}}
    bare = {"case_id": "E2"}
    cases.write_text(json.dumps({"cases": [good, bare]}))
    with pytest.raises(script.CheckFailed, match="E2"):
        script.check_adjudications(tmp_path, ["eval/cases.json"])


def test_a_well_formed_adjudication_passes(script, tmp_path):
    (tmp_path / "eval" / "manifests").mkdir(parents=True)
    manifest = tmp_path / "eval" / "manifests" / "p1.json"
    manifest.write_text(
        json.dumps({"patient_id": "p1", "adjudicated": {"by": "troy", "date": "2026-09-10"}})
    )
    script.check_adjudications(tmp_path, ["eval/manifests/p1.json"])


# ------------------------------------------------- the check is not decorative


def test_an_emptied_parser_is_caught(script):
    """Deleting any parser's match (the mutation) raises rather than returning
    an empty enumeration the ledger check would then agree with."""
    for fn in (
        script.constitution_ids,
        script.story_ids,
        script.task_ids,
        script.decision_ids,
    ):
        with pytest.raises(script.CheckFailed):
            fn("no headings here\n")
    with pytest.raises(script.CheckFailed):
        script.spec_ids("## 5.\n\n## 6.\n\n## 7.\n\nnothing\n")


def test_a_dropped_id_class_turns_the_gate_red(script, committed):
    """If a parser regression stopped enumerating a class (stories, say), the
    committed ledger's entries for that class would stop resolving — loud,
    not a silent narrowing of what the gate protects."""
    ids, ledger = committed
    without_stories = {i: t for i, t in ids.items() if t != ("stories",)}
    with pytest.raises(script.CheckFailed, match="resolving in no document"):
        script.check_ledger(ledger, without_stories, set())


def test_a_missing_ledger_is_a_failure_not_a_pass(script, monkeypatch, capsys):
    monkeypatch.setattr(script, "LEDGER_PATH", REPO_ROOT / "docs" / "no_such_ledger.json")
    assert script.main([]) == 1
    assert "does not exist" in capsys.readouterr().out
