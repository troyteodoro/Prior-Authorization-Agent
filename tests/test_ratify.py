"""The recording tool refuses what rule 11 and D75 say it must refuse.

``scripts/ratify.py`` is the owner's pen (D75): explicit ids only, one-directional
statuses, task links checked at recording time. These tests drive a tmp copy
of the ledger — the real ``docs/ratifications.json`` is never written.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ratify.py"
LEDGER = REPO_ROOT / "docs" / "ratifications.json"

TODAY = "2026-09-11"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("ratify", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ratify"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def universe(script):
    return script._co.enumerate_ids(REPO_ROOT)


@pytest.fixture()
def tmp_ledger(tmp_path):
    # A copy normalized to T-73's seed state, so these tests keep meaning the
    # same thing as the owner's real ratifications accumulate in the live ledger.
    target = tmp_path / "ratifications.json"
    ledger = json.loads(LEDGER.read_text())
    ledger["required_tiers"] = []
    for entry in ledger["entries"].values():
        entry.clear()
        entry.update(status="proposed", by="agent", date="2026-09-10")
    target.write_text(json.dumps(ledger, indent=2) + "\n")
    return target


def _record(script, universe, tmp_ledger, ids, status="ratified", by="human", task=None):
    return script.record(ids, status, by, task, tmp_ledger, universe, TODAY)


def test_unknown_id_is_refused_and_nothing_is_written(script, universe, tmp_ledger):
    before = tmp_ledger.read_bytes()
    with pytest.raises(script.RecordRefused, match="no document"):
        _record(script, universe, tmp_ledger, ["Article I", "Article XIV"])
    assert tmp_ledger.read_bytes() == before


def test_proposed_is_not_recordable(script, universe, tmp_ledger):
    with pytest.raises(script.RecordRefused, match="not recordable"):
        _record(script, universe, tmp_ledger, ["Article I"], status="proposed")


def test_amended_requires_a_resolving_board_task(script, universe, tmp_ledger):
    with pytest.raises(script.RecordRefused, match="must link a board task"):
        _record(script, universe, tmp_ledger, ["REQ-1"], status="amended")
    with pytest.raises(script.RecordRefused, match="must link a board task"):
        _record(script, universe, tmp_ledger, ["REQ-1"], status="amended", task="T-999")


def test_task_link_only_accompanies_linked_statuses(script, universe, tmp_ledger):
    with pytest.raises(script.RecordRefused, match="--task only"):
        _record(script, universe, tmp_ledger, ["REQ-1"], task="T-21")


def test_a_valid_write_passes_the_gate_validator(script, universe, tmp_ledger):
    changes = _record(script, universe, tmp_ledger, ["Article I", "US-4"])
    assert changes == [("Article I", "proposed"), ("US-4", "proposed")]
    entries = json.loads(tmp_ledger.read_text())["entries"]
    board = {i for i, tiers in universe.items() if tiers == ("tasks",)}
    for i in ("Article I", "US-4"):
        assert entries[i] == {"status": "ratified", "by": "human", "date": TODAY}
        script._co.check_entry(i, entries[i], board)


def test_an_amended_write_carries_its_task_and_validates(script, universe, tmp_ledger):
    _record(script, universe, tmp_ledger, ["REQ-1"], status="amended", task="T-21")
    entry = json.loads(tmp_ledger.read_text())["entries"]["REQ-1"]
    assert entry == {"status": "amended", "by": "human", "date": TODAY, "task": "T-21"}
    board = {i for i, tiers in universe.items() if tiers == ("tasks",)}
    script._co.check_entry("REQ-1", entry, board)


def test_no_tier_wildcard_exists(script):
    # D75: each invocation names what was read. A --tier/--all flag is the
    # rejected rubber stamp, so argparse must not know it.
    with pytest.raises(SystemExit) as exc:
        script.main(["--tier", "constitution"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        script.main(["--all"])
    assert exc.value.code == 2


def test_pending_reports_tier_progress(script, universe, tmp_ledger):
    report = script.pending_report(tmp_ledger, universe)
    lines = report.splitlines()
    assert lines[0].split() == ["constitution", "0/11"]
    _record(script, universe, tmp_ledger, ["Article I"])
    report = script.pending_report(tmp_ledger, universe)
    assert report.splitlines()[0].split() == ["constitution", "1/11"]
    assert "    proposed  Article I" not in report.splitlines()


def test_the_real_ledger_was_not_touched_by_this_file(script):
    entries = json.loads(LEDGER.read_text())["entries"]
    # Statuses beyond 'proposed' in the real ledger are the owner's edits (rule 11);
    # this suite must never be the thing that wrote one.
    assert all(
        e["status"] == "proposed" or e["by"] != "agent" for e in entries.values()
    )
