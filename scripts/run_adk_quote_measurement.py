"""T-98 — measure the ADK quote runner beside the direct one (D122).

    python scripts/run_adk_quote_measurement.py                 measure. Spends calls.
                                     -> eval/history/adk_results_inline.json
    python scripts/run_adk_quote_measurement.py --tool-fetch    the model reads the note
                                                                through read_note
                                     -> eval/history/adk_results_tool_fetch.json
    python scripts/run_adk_quote_measurement.py --tier vertex   -> ..._vertex.json
    python scripts/run_adk_quote_measurement.py --rescore       re-anchor, no calls

**One recording per mode and tier** (T-68, D68, D106): the path is derived from
the flags, never chosen by the caller. **Spends model calls, so it is in no
gate.**

The corpus, the table, the scorer and the aggregate are
`scripts/run_quote_measurement.py`'s, imported — the only thing that differs
between the recordings is the runner (D62's rule for a comparison). The tier
changes the prompt under `--tool-fetch`: on AI Studio ADK injects a
`SetModelResponseTool` when `output_schema` and a tool are both present, so
`output_schema_and_tools` is stamped from what ADK reports, never from the flag
(D62, D106). Unlike the extraction pair this script has `--rescore`, so every
quote recording re-derives for free.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import CallMetrics  # noqa: E402
from pa_agent.extraction import REASK_ROUNDS  # noqa: E402
from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL, SECOND_TIER  # noqa: E402
from pa_agent.quotes import QUOTE_PROMPT_VERSION, QUOTE_TEMPERATURE, build_quote_result  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

OUT_DIR = REPO_ROOT / "eval" / "history"
ADK_INLINE_PATH = OUT_DIR / "adk_results_inline.json"
ADK_TOOL_FETCH_PATH = OUT_DIR / "adk_results_tool_fetch.json"

PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-98", "decision": "D122", "supersedes": None},
    SECOND_TIER: {"task": "T-98", "decision": "D122", "supersedes": None},
}


def _tier_suffix(tier: str) -> str:
    return "" if tier == MEASURED_TIER else f"_{tier}"


def adk_path(tool_fetch: bool, tier: str = MEASURED_TIER) -> Path:
    """The recording this mode and tier writes and rescores. Derived, never
    passed in; the globals are read at call time so a test can redirect."""
    base = ADK_TOOL_FETCH_PATH if tool_fetch else ADK_INLINE_PATH
    if tier == MEASURED_TIER:
        return base
    return base.with_name(f"{base.stem}{_tier_suffix(tier)}{base.suffix}")


def _load_direct():
    """`scripts/run_quote_measurement.py` as a module: corpus, table, scorer,
    aggregate and the re-ask re-derivation, reused rather than copied."""
    cached = sys.modules.get("run_quote_measurement")
    if cached is not None and getattr(cached, "aggregate", None) is not None:
        return cached
    path = Path(__file__).resolve().parent / "run_quote_measurement.py"
    spec = importlib.util.spec_from_file_location("run_quote_measurement", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_quote_measurement"] = module
    spec.loader.exec_module(module)
    return module


def _client(tier: str):
    return _load_direct()._client(tier)


def _adk_version() -> str:
    import google.adk

    return google.adk.__version__


def _mode(tool_fetch: bool) -> str:
    return "tool_fetch" if tool_fetch else "inline"


def _display(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise — a test
    redirects the output under a tmp directory."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def measure(tool_fetch: bool, limit: int | None = None, tier: str = MEASURED_TIER) -> int:
    base = _load_direct()
    from pa_agent.agent.extraction_agent import DEFAULT_MAX_LLM_CALLS, native_schema_enabled
    from pa_agent.agent.quote_agent import AdkQuoteRunner
    from pa_agent.tiers import tier_of

    cases = base.history_cases()
    if limit is not None:
        cases = cases[:limit]
    rows = base.table_rows()
    store = LocalPatientStore()
    client = _client(tier)
    runner = AdkQuoteRunner(
        client=client, patient_store=store, tool_fetch=tool_fetch, model=PINNED_MODEL,
    )
    # Asked of ADK, not inferred from the flag (D62, D106).
    native_schema = native_schema_enabled(PINNED_MODEL)
    measured_tier = tier_of(client)
    print(
        f"{len(cases)} notes × {len(rows)} conditions · adk · model {PINNED_MODEL} · "
        f"tier {measured_tier} · tool_fetch={tool_fetch} · "
        f"output_schema_and_tools={native_schema} · max_llm_calls={DEFAULT_MAX_LLM_CALLS}"
    )
    if tool_fetch and not native_schema:
        print(
            "  NOTE: output_schema + tools is native on Vertex only. On this run "
            "ADK injects a set_model_response tool and an extra instruction, so "
            "this run's prompt differs from a native one's (D62)."
        )

    records: list[dict] = []
    for index, case in enumerate(cases, start=1):
        print(f"  [{index}/{len(cases)}] {case['note_id']}", flush=True)
        result, error = base.run_with_retries(runner, case, rows)
        scored = base.score(case, result, rows) if result is not None else None
        records.append(base.record_for(case, result, scored, error))
        base._print_note(case, scored, result, error)

    figures = base.aggregate(records, len(rows))
    payload = {
        **PROVENANCE[tier],
        "runner": "adk",
        "tool_fetch": tool_fetch,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "rescored_at": None,
        "model": PINNED_MODEL,
        "tier": measured_tier,
        "output_schema_and_tools": native_schema,
        "prompt_version": QUOTE_PROMPT_VERSION,
        "reask_rounds": REASK_ROUNDS,
        "temperature": QUOTE_TEMPERATURE,
        "adk_version": _adk_version(),
        "table_id": base.table_id(),
        "rows_asked": base.rows_asked_record(rows),
        "schema_note": (
            "Same corpus, same conditions and the same scorer as "
            "eval/history/results.json — scripts/run_quote_measurement.py's, "
            "imported rather than reimplemented. The runner is the only difference."
        ),
        "aggregate": figures,
        "notes": records,
    }
    out_path = adk_path(tool_fetch, tier)
    base.write_recording(out_path, payload)
    print(json.dumps(figures, indent=2))
    print(f"written: {_display(out_path)}")
    return 0


def rescore(tool_fetch: bool, tier: str = MEASURED_TIER) -> int:
    base = _load_direct()
    out_path = adk_path(tool_fetch, tier)
    if not out_path.exists():
        sys.exit(f"nothing to rescore at {out_path}; run without --rescore first")
    previous = json.loads(out_path.read_text(encoding="utf-8"))
    if bool(previous.get("tool_fetch")) is not tool_fetch:
        sys.exit(
            f"{out_path.name} records tool_fetch={previous.get('tool_fetch')}, which is "
            f"the {_mode(bool(previous.get('tool_fetch')))} mode, but {_mode(tool_fetch)} "
            "was asked for"
        )
    cases = base.history_cases()
    rows = base.table_rows()
    if previous.get("rows_asked") != base.rows_asked_record(rows):
        sys.exit(
            "the knowledge table's rows are not the ones this recording was asked "
            "about; a changed table is a new measurement (D45), not a rescore"
        )
    recorded = {r["note_id"]: r for r in previous["notes"]}
    for case in cases:
        prior = recorded.get(case["note_id"])
        if prior is None:
            sys.exit(f"{case['note_id']}: not in the recording; re-measure")
        if prior["note_sha256"] != case["sha256"]:
            sys.exit(
                f"{case['note_id']}: the note changed since it was measured. "
                "Re-anchoring against it would score a quote against a document "
                "it never came from (D18)."
            )
    print(f"re-anchoring {len(cases)} recorded payloads from {out_path.name}, no model call")
    records: list[dict] = []
    for case in cases:
        prior = recorded[case["note_id"]]
        if prior.get("raw") is None:
            records.append({**prior})
            base._print_note(case, None, None, prior.get("error"))
            continue
        result = build_quote_result(
            case["document_id"], case["text"], prior["raw"],
            CallMetrics.model_validate(prior["metrics"]) if prior.get("metrics") else None,
            rows=rows,
        )
        base._rederive_reask(result, prior, case["document_id"], case["text"], rows)
        scored = base.score(case, result, rows)
        records.append(base.record_for(case, result, scored, None))
        base._print_note(case, scored, result, None)

    figures = base.aggregate(records, len(rows))
    payload = {
        **{k: previous.get(k) for k in ("task", "decision", "supersedes")},
        "runner": "adk",
        "tool_fetch": tool_fetch,
        "measured_at": previous["measured_at"],
        "rescored_at": datetime.now(timezone.utc).isoformat(),
        "model": PINNED_MODEL,
        "tier": previous["tier"],
        "output_schema_and_tools": previous.get("output_schema_and_tools"),
        "prompt_version": QUOTE_PROMPT_VERSION,
        "reask_rounds": REASK_ROUNDS,
        "temperature": QUOTE_TEMPERATURE,
        "adk_version": previous.get("adk_version"),
        "table_id": previous.get("table_id"),
        "rows_asked": previous["rows_asked"],
        "schema_note": previous.get("schema_note"),
        "aggregate": figures,
        "notes": records,
    }
    base.write_recording(out_path, payload)
    print(json.dumps(figures, indent=2))
    print(f"written: {_display(out_path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/run_adk_quote_measurement.py", description=__doc__
    )
    parser.add_argument(
        "--tool-fetch", action="store_true",
        help="the model reads the note through its scoped read_note tool; picks the recording",
    )
    parser.add_argument(
        "--rescore", action="store_true",
        help="re-anchor this mode's recording for zero calls",
    )
    parser.add_argument("--limit", type=int, default=None, help="measure only the first N notes")
    parser.add_argument(
        "--tier", choices=tuple(PROVENANCE), default=MEASURED_TIER,
        help="which tier to measure on (default: the development tier)",
    )
    args = parser.parse_args(argv)
    if args.rescore:
        return rescore(args.tool_fetch, args.tier)
    return measure(args.tool_fetch, args.limit, args.tier)


if __name__ == "__main__":
    raise SystemExit(main())
