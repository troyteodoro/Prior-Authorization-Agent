"""T-98 — the two quote measurement scripts, driven without a model (D122).

Both scripts spend calls and sit in `check_gates.EXCLUDED`; what the suite can
check is everything around the call: the corpus they walk, the table they ask
about, the per-note score, the aggregate, the record's shape, and `--rescore`
re-deriving the anchoring and the re-ask sets from the recorded payloads
rather than copying them (D18's rule on T-89's audit). The runners are
replaced by stubs whose payloads still go through `build_quote_result`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pa_agent.contracts import CallMetrics, RunTrace
from pa_agent.quotes import QUOTE_PROMPT_VERSION, QuoteFailure, QuoteOutputError, build_quote_result
from pa_agent.stores.knowledge import LocalKnowledgeStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
CLONE = "ee9d79ee-ba2e-5915-b6d5-c7e700066d40"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def direct():
    return _load("run_quote_measurement", SCRIPTS / "run_quote_measurement.py")


@pytest.fixture(scope="module")
def adk(direct):
    return _load("run_adk_quote_measurement", SCRIPTS / "run_adk_quote_measurement.py")


@pytest.fixture(scope="module")
def extraction():
    return _load("run_extraction", SCRIPTS / "run_extraction.py")


@pytest.fixture(scope="module")
def cases(direct) -> list[dict]:
    return direct.history_cases()


@pytest.fixture(scope="module")
def rows():
    return LocalKnowledgeStore().get_medication_effect_rows()


# --------------------------------------------------------------------------
# The corpus and the table
# --------------------------------------------------------------------------


def test_the_corpus_is_the_declared_notes_of_every_non_clone_chart(cases, extraction) -> None:
    """Twelve notes over six charts: every note-bearing chart except the
    declared clone, whose bytes replay by content (T-88, D102, D122). The ids
    are the extraction recording's, so the two recordings join."""
    assert len(cases) == 12
    assert not any(c["document_id"].startswith(CLONE) for c in cases)
    synthesized = {c["note_id"]: c["sha256"] for c in extraction.synthesized_cases()}
    for case in cases:
        assert synthesized[case["note_id"]] == case["sha256"]
    assert {c["corpus"] for c in cases} == {"synthesized"}


def test_rows_asked_is_the_tables_rows_by_id_and_display(direct, rows) -> None:
    asked = direct.rows_asked_record(rows)
    assert asked == [{"row_id": r.row_id, "effect_display": r.effect_display} for r in rows]
    assert direct.table_id()


def test_both_scripts_are_excluded_from_the_gates_with_the_spending_phrase() -> None:
    check = _load("check_gates_for_quotes", SCRIPTS / "check_gates.py")
    for name in ("scripts/run_quote_measurement.py", "scripts/run_adk_quote_measurement.py"):
        assert "spends model calls" in check.EXCLUDED[name]


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


NOTE = "ASSESSMENT\nChronic kidney disease stage 2 noted on labs.\nBP 118/76.\n"
VERBATIM = "Chronic kidney disease stage 2 noted on labs."


def _case(document_id: str = "p/chart_note_1.txt", text: str = NOTE) -> dict:
    import hashlib

    return {
        "note_id": "X/1", "corpus": "synthesized", "document_id": document_id,
        "document": "chart_note_1.txt", "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "cases": ["X"],
    }


def _payload(*blocks: tuple[str, list[str]]) -> dict:
    return {"effects": [
        {"effect": e, "quotes": [{"quote": q, "char_start": 0, "char_end": 1} for q in qs]}
        for e, qs in blocks
    ]}


def _metric(purpose: str, tokens: int = 10) -> CallMetrics:
    return CallMetrics(model="m", purpose=purpose, input_tokens=tokens, output_tokens=1, wall_time_ms=1.0)


def test_score_counts_returned_anchored_refused_and_fabricated_pairs(direct, rows) -> None:
    renal = next(r for r in rows if r.row_id == "lisinopril-renal-impairment")
    hypo = next(r for r in rows if r.row_id == "apixaban-hypotension")
    payload = _payload(
        (renal.effect_display, [VERBATIM, "a passage that is not in the note"]),
        (hypo.effect_display, ["another absent passage"]),
        ("obesity", ["ASSESSMENT"]),
    )
    result = build_quote_result("p/chart_note_1.txt", NOTE, payload, rows=rows)
    scored = direct.score(_case(), result, rows)
    assert scored["quotes_returned"] == 3
    assert scored["quotes_anchored"] == 1
    assert scored["quotes_refused"] == 2
    assert scored["unknown_effects"] == 1
    assert scored["pairs_with_quote"] == 2
    assert scored["pairs_anchored"] == 1
    assert scored["pairs_fabricated"] == 1, "hypotension: returned, nothing anchored"
    assert scored["per_effect"][renal.row_id] == {"returned": 2, "anchored": 1}
    assert scored["per_effect"][hypo.row_id] == {"returned": 1, "anchored": 0}
    assert scored["model_offsets_usable"] == 0


def test_aggregate_sums_every_turn_and_counts_a_failed_note_apart(direct) -> None:
    trace_two = RunTrace(
        runner_name="direct", model="m", prompt_version=QUOTE_PROMPT_VERSION,
        document_id="d", steps=["quotes", "quotes_reask"],
        metrics=[_metric("quotes", 10), _metric("quotes_reask", 20)],
    )
    scored = {
        "quotes_returned": 1, "quotes_anchored": 0, "quotes_refused": 1, "quotes_blank": 0,
        "unknown_effects": 0, "pairs_with_quote": 1, "pairs_anchored": 0,
        "pairs_fabricated": 1, "spans_emitted": 1, "spans_anchored": 0,
        "spans_normalized": 0, "spans_unescaped": 0, "model_offsets_usable": 0,
        "per_effect": {}, "dropped": [],
    }
    records = [
        {"note_id": "A/1", "score": scored, "metrics": _metric("quotes", 10).model_dump(mode="json"),
         "trace": trace_two.model_dump(mode="json"),
         "reask": {"targets": [{"path": "p"}], "recovered": [], "unrecovered": ["p"], "error": None}},
        {"note_id": "B/1", "score": None, "metrics": None, "trace": None, "reask": None, "error": "boom"},
    ]
    figures = direct.aggregate(records, effects_asked=5)
    assert figures["notes"] == 1 and figures["failed"] == 1
    assert figures["pairs"] == 5 and figures["pairs_fabricated"] == 1
    assert figures["model_calls"] == 2, "both turns, not the singular metrics"
    assert figures["total_input_tokens"] == 30
    assert figures["reask_notes"] == 1 and figures["reask_targets"] == 1
    assert figures["reask_recovered"] == 0 and figures["reask_calls"] == 1


# --------------------------------------------------------------------------
# The whole measure / rescore path, with the runner stubbed
# --------------------------------------------------------------------------


class _StubQuoteRunner:
    """Answers every note with a paraphrase, recovers it on the re-ask, and
    fails its first note on transport once so the retry branch runs."""

    name = "stub"

    def __init__(self, *args, **kwargs) -> None:
        assert kwargs.get("client") is None or args == (), "no credential was read"
        self.seen: list[str] = []

    def run(self, document_id, text, rows):
        self.seen.append(document_id)
        if len(self.seen) == 1:
            raise QuoteOutputError(QuoteFailure.CALL_FAILED, "stub transport fault")
        renal = next(r for r in rows if r.row_id == "lisinopril-renal-impairment")
        # A passage present in every synthesized note's header.
        first_line = text.splitlines()[0]
        first_turn = _payload((renal.effect_display, [first_line.lower() + " x"]))
        patched = _payload((renal.effect_display, [first_line]))
        metrics = [_metric("quotes"), _metric("quotes_reask")]
        result = build_quote_result(document_id, text, patched, metrics[0], rows=rows)
        result.raw_first_turn = first_turn
        result.reask = {
            "targets": [{"path": "effects[0].quotes[0].quote", "reason": "effect_quote_unanchorable",
                         "quote": first_line.lower() + " x"}],
            "answers": [{"path": "effects[0].quotes[0].quote", "verbatim": first_line}],
            "recovered": ["effects[0].quotes[0].quote"], "unrecovered": [], "error": None,
        }
        result.trace = RunTrace(
            runner_name=self.name, model="m", prompt_version=QUOTE_PROMPT_VERSION,
            document_id=document_id, steps=["quotes", "quotes_reask"], metrics=metrics,
        )
        return result


def _no_sleep(monkeypatch, module) -> None:
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)


def test_the_direct_measure_path_runs_without_a_model_and_rescores_to_the_same_figures(
    direct, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(direct, "OUT_DIR", tmp_path)
    monkeypatch.setattr(direct, "DirectQuoteRunner", lambda client, model: _StubQuoteRunner(client=client))
    monkeypatch.setattr(direct, "_client", lambda tier: None)
    _no_sleep(monkeypatch, direct)

    class _Tiers:
        @staticmethod
        def tier_of(client):
            return "ai_studio"

    monkeypatch.setitem(sys.modules, "pa_agent.tiers", _Tiers)
    assert direct.measure("ai_studio") == 0
    written = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert written["task"] == "T-98" and written["decision"] == "D122"
    assert written["runner"] == "direct" and written["tier"] == "ai_studio"
    assert written["prompt_version"] == QUOTE_PROMPT_VERSION
    assert written["rows_asked"] == direct.rows_asked_record(direct.table_rows())
    assert written["aggregate"]["notes"] == 12 and written["aggregate"]["failed"] == 0
    assert written["aggregate"]["model_calls"] == 24, "two turns per note, all counted"
    assert written["aggregate"]["reask_recovered"] == 12
    assert written["aggregate"]["pairs_anchored"] == 12
    for record in written["notes"]:
        assert record["raw"] and record["raw_first_turn"] and record["trace"]
        assert record["metrics"] == record["trace"]["metrics"][0]
    out = capsys.readouterr().out
    assert "retry in" in out, "the transport fault was retried"

    # A rescore re-derives the same anchoring and the same re-ask sets.
    monkeypatch.delitem(sys.modules, "pa_agent.tiers")
    assert direct.rescore("ai_studio") == 0
    rescored = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert rescored["aggregate"] == written["aggregate"]
    assert rescored["measured_at"] == written["measured_at"]
    assert rescored["rescored_at"] is not None
    assert [r["quotes"] for r in rescored["notes"]] == [r["quotes"] for r in written["notes"]]
    assert all(r["reask"]["recovered"] == ["effects[0].quotes[0].quote"] for r in rescored["notes"])


def test_rescore_refuses_a_changed_note_and_a_changed_table(direct, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(direct, "OUT_DIR", tmp_path)
    cases = direct.history_cases()
    rows = direct.table_rows()
    records = []
    for case in cases:
        result = build_quote_result(case["document_id"], case["text"], {"effects": []}, rows=rows)
        records.append(direct.record_for(case, result, direct.score(case, result, rows)))
    payload = {
        "task": "T-98", "decision": "D122", "supersedes": None, "runner": "direct",
        "measured_at": "x", "rescored_at": None, "model": "m", "tier": "ai_studio",
        "prompt_version": QUOTE_PROMPT_VERSION, "reask_rounds": 1, "temperature": 0.0,
        "table_id": "t", "rows_asked": direct.rows_asked_record(rows), "schema_note": "",
        "aggregate": direct.aggregate(records, len(rows)), "notes": records,
    }
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert direct.rescore("ai_studio") == 0

    moved = json.loads(path.read_text(encoding="utf-8"))
    moved["notes"][0]["note_sha256"] = "0" * 64
    path.write_text(json.dumps(moved), encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since it was measured"):
        direct.rescore("ai_studio")

    retabled = json.loads(json.dumps(payload))
    retabled["rows_asked"][0]["effect_display"] = "kidney trouble"
    path.write_text(json.dumps(retabled), encoding="utf-8")
    with pytest.raises(SystemExit, match="new measurement"):
        direct.rescore("ai_studio")


def test_rescore_refuses_a_missing_recording(direct, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(direct, "OUT_DIR", tmp_path)
    with pytest.raises(SystemExit, match="nothing to rescore"):
        direct.rescore("ai_studio")


def test_an_unknown_tier_exits_rather_than_defaulting(direct) -> None:
    with pytest.raises(SystemExit, match="unknown tier"):
        direct._named_tier(["x", "--tier", "staging"])
    assert direct._named_tier(["x"]) == "ai_studio"
    assert direct._named_tier(["x", "--tier=vertex"]) == "vertex"


def test_a_non_transport_failure_is_recorded_not_retried(direct, monkeypatch) -> None:
    _no_sleep(monkeypatch, direct)

    class _Bad:
        calls = 0

        def run(self, document_id, text, rows):
            self.calls += 1
            raise QuoteOutputError(QuoteFailure.SCHEMA_INVALID, "the model answered badly")

    runner = _Bad()
    result, error = direct.run_with_retries(runner, _case(), direct.table_rows())
    assert result is None and "SCHEMA_INVALID" in error and runner.calls == 1
    record = direct.record_for(_case(), None, None, error)
    assert record["raw"] is None and record["score"] is None and record["error"] == error


@pytest.mark.parametrize("tool_fetch", [False, True], ids=["inline", "tool_fetch"])
def test_the_adk_measure_path_writes_its_own_recording_and_stamps_the_capability(
    adk, direct, tmp_path, monkeypatch, tool_fetch
) -> None:
    import pa_agent.agent.extraction_agent as extraction_agent
    import pa_agent.agent.quote_agent as quote_agent

    monkeypatch.setattr(adk, "ADK_INLINE_PATH", tmp_path / "adk_results_inline.json")
    monkeypatch.setattr(adk, "ADK_TOOL_FETCH_PATH", tmp_path / "adk_results_tool_fetch.json")
    monkeypatch.setattr(quote_agent, "AdkQuoteRunner", lambda **kw: _StubQuoteRunner(client=kw["client"]))
    monkeypatch.setattr(extraction_agent, "native_schema_enabled", lambda model: "stubbed")
    monkeypatch.setattr(adk, "_client", lambda tier: None)
    monkeypatch.setattr(adk, "_adk_version", lambda: "0.0.0-test")
    _no_sleep(monkeypatch, direct)

    class _Tiers:
        @staticmethod
        def tier_of(client):
            return "ai_studio"

    monkeypatch.setitem(sys.modules, "pa_agent.tiers", _Tiers)
    assert adk.measure(tool_fetch, None, "ai_studio") == 0
    path = adk.adk_path(tool_fetch, "ai_studio")
    assert path.parent == tmp_path
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["runner"] == "adk" and written["tool_fetch"] is tool_fetch
    assert written["output_schema_and_tools"] == "stubbed", "asked of ADK, not inferred"
    assert written["adk_version"] == "0.0.0-test"
    assert written["aggregate"]["notes"] == 12 and written["aggregate"]["model_calls"] == 24
    other = adk.adk_path(not tool_fetch, "ai_studio")
    assert not other.exists(), "the other mode's path is untouched"

    monkeypatch.delitem(sys.modules, "pa_agent.tiers")
    assert adk.rescore(tool_fetch, "ai_studio") == 0
    rescored = json.loads(path.read_text(encoding="utf-8"))
    assert rescored["aggregate"] == written["aggregate"]
    assert rescored["output_schema_and_tools"] == "stubbed"
    # A recording whose own `tool_fetch` contradicts the mode asked for is
    # refused rather than rescored under the wrong name (T-68's rule).
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(SystemExit, match="was asked for"):
        adk.rescore(not tool_fetch, "ai_studio")


def test_the_adk_paths_are_two_per_tier(adk) -> None:
    assert adk.adk_path(False, "ai_studio").name == "adk_results_inline.json"
    assert adk.adk_path(True, "ai_studio").name == "adk_results_tool_fetch.json"
    assert adk.adk_path(False, "vertex").name == "adk_results_inline_vertex.json"
    assert adk.adk_path(True, "vertex").name == "adk_results_tool_fetch_vertex.json"
