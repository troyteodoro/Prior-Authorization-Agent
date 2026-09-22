"""T-98 — consult every note-bearing chart's notes for the knowledge table's
conditions, and record it (D122).

    python scripts/run_quote_measurement.py                 spends one call per note,
                                                            two on a re-ask
                                     -> eval/history/results.json
    python scripts/run_quote_measurement.py --tier vertex   -> eval/history/results_vertex.json
    python scripts/run_quote_measurement.py --rescore       re-anchors recorded payloads,
                                                            no calls

**Spends model calls, so it is in no gate.** `pytest tests/test_quote_recording.py`
verifies what this writes and spends nothing — D45's split, for D45's reasons.

### What is measured

One call per note asks the model, through `pa_agent.quotes.DirectQuoteRunner`,
for verbatim passages documenting **each** of the knowledge table's conditions,
by display and nothing else (D122). The chart's medication list never reaches
the prompt, so the request is one fixed configuration a chart cannot vary, and
the recording pins it twice: `prompt_version` for the instruction and the
schema, `rows_asked` for the condition list. `RecordedQuoteRunner` refuses a
row outside `rows_asked` — adding a table row is a new measurement (D45).

No committed note documents any of the five conditions, so the figure this
recording yields is a **fabrication rate**: `pairs_fabricated` counts the
`(note, condition)` pairs for which the model returned a passage that Python
could not anchor after the re-ask. A passage that anchors for a condition the
note does not document would be worse — a yellow the corpus was not supposed
to be able to produce — and the gate reports it as a non-zero `pairs_anchored`.

### The corpus

Every chart `eval/manifests/` declares notes for, except a declared clone
(`cloned_from`), whose notes are its source's bytes and replay by content
(T-88, D102): twelve notes over six charts, identified exactly as
`scripts/run_extraction.py` identifies them so the two recordings join.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import CallMetrics, MedicationEffectRow, RunTrace  # noqa: E402
from pa_agent.extraction import REASK_ROUNDS, reask_targets  # noqa: E402
from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL, SECOND_TIER  # noqa: E402
from pa_agent.quotes import (  # noqa: E402
    DROP_REASONS,
    QUOTE_PROMPT_VERSION,
    QUOTE_TEMPERATURE,
    DirectQuoteRunner,
    QuoteFailure,
    QuoteOutputError,
    _locate_quote,
    build_quote_result,
    rows_asked,
)
from pa_agent.stores.knowledge import LocalKnowledgeStore  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
KNOWLEDGE_TABLE = REPO_ROOT / "data" / "knowledge" / "medication_effects.json"
OUT_DIR = REPO_ROOT / "eval" / "history"

#: Which task and entry own each tier's recording (D45, D106). Both tiers are
#: T-98's: the round measures both, side by side, and neither supersedes anything.
PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-98", "decision": "D122", "supersedes": None},
    SECOND_TIER: {"task": "T-98", "decision": "D122", "supersedes": None},
}

RETRIES = 4  # the free tier's 429/503 behaviour under load (D5, D20)


def _load_run_extraction():
    """`scripts/run_extraction.py` as a module, for `load_env` and
    `turn_metrics` — one definition of "every turn counted" (D71), reused
    rather than copied, `run_adk_extraction.py`'s pattern."""
    cached = sys.modules.get("run_extraction")
    if cached is not None and getattr(cached, "turn_metrics", None) is not None:
        return cached
    path = Path(__file__).resolve().parent / "run_extraction.py"
    spec = importlib.util.spec_from_file_location("run_extraction", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_extraction"] = module
    spec.loader.exec_module(module)
    return module


def _named_tier(argv: list[str]) -> str:
    """`--tier X` or `--tier=X`, defaulting to the development tier; an
    unrecognised tier exits rather than stamping the wrong one (D106)."""
    named = None
    for i, arg in enumerate(argv):
        if arg == "--tier" and i + 1 < len(argv):
            named = argv[i + 1]
        elif arg.startswith("--tier="):
            named = arg.split("=", 1)[1]
    if named is None:
        return MEASURED_TIER
    if named not in PROVENANCE:
        sys.exit(f"unknown tier {named!r}; one of {sorted(PROVENANCE)}")
    return named


def out_path_for(tier: str) -> Path:
    """One recording per tier, side by side; `OUT_DIR` read at call time so a
    test can redirect it."""
    return OUT_DIR / ("results.json" if tier == MEASURED_TIER else f"results_{tier}.json")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise — a test
    redirects the output under a tmp directory."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _client(tier: str):
    _load_run_extraction().load_env()
    from pa_agent.tiers import client_for

    return client_for(tier)


# --------------------------------------------------------------------------
# The corpus and the table
# --------------------------------------------------------------------------


def history_cases() -> list[dict]:
    """Every declared note of every note-bearing, non-clone chart, identified
    as `run_extraction.synthesized_cases` identifies them."""
    store = LocalPatientStore()
    cases: list[dict] = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("note") is False:
            continue
        if manifest.get("cloned_from"):
            # The source's row measures these bytes; the recorded runner
            # replays them by content (T-88, D102, D122).
            continue
        served = {d.document_id: d for d in store.get_notes(manifest["patient_id"])}
        for ordinal, basename in enumerate(manifest["documents"], 1):
            document = served[f"{manifest['patient_id']}/{basename}"]
            cases.append({
                "note_id": f"{'+'.join(manifest['cases'])}/{ordinal}",
                "corpus": "synthesized",
                "document_id": document.document_id,
                "document": basename,
                "text": document.text,
                "sha256": document.sha256,
                "cases": manifest["cases"],
            })
    return cases


def table_rows() -> list[MedicationEffectRow]:
    return LocalKnowledgeStore().get_medication_effect_rows()


def table_id() -> str:
    return json.loads(KNOWLEDGE_TABLE.read_text(encoding="utf-8"))["table_id"]


def rows_asked_record(rows: list[MedicationEffectRow]) -> list[dict]:
    return [{"row_id": row_id, "effect_display": display} for row_id, display in rows_asked(rows)]


# --------------------------------------------------------------------------
# Scoring — per note, from the result; per recording, from the per-note scores
# --------------------------------------------------------------------------


def score(case: dict, result, rows: list[MedicationEffectRow]) -> dict:
    """What the model returned for each condition and what Python kept.

    A `(note, condition)` pair is *fabricated* when the model returned at
    least one passage for it and none anchored after the re-ask: the note
    holds no such text (D18). A pair that anchored is reported, never judged —
    whether the passage documents the condition is the verifier's question,
    and on this corpus the expected count is zero.
    """
    by_display = {row.effect_display: row.row_id for row in rows}
    returned: dict[str, int] = {row.row_id: 0 for row in rows}
    for block in (result.raw or {}).get("effects", []):
        row_id = by_display.get(block.get("effect"))
        if row_id is not None:
            returned[row_id] += len(block.get("quotes") or [])
    per_effect = {
        row.row_id: {"returned": returned[row.row_id], "anchored": len(result.spans[row.row_id])}
        for row in rows
    }
    anchored = [s for s in result.anchored_spans if s.anchored]
    reasons = [d["reason"] for d in result.dropped]
    return {
        "quotes_returned": sum(returned.values()),
        "quotes_anchored": result.quotes_anchored,
        "quotes_refused": reasons.count("effect_quote_unanchorable"),
        "quotes_blank": reasons.count("blank_quote"),
        "unknown_effects": reasons.count("unknown_effect"),
        "pairs_with_quote": sum(1 for p in per_effect.values() if p["returned"]),
        "pairs_anchored": sum(1 for p in per_effect.values() if p["anchored"]),
        "pairs_fabricated": sum(
            1 for p in per_effect.values() if p["returned"] and not p["anchored"]
        ),
        "spans_emitted": len(result.anchored_spans),
        "spans_anchored": len(anchored),
        "spans_normalized": sum(1 for s in anchored if s.anchor_mode == "normalized"),
        "spans_unescaped": sum(1 for s in anchored if s.anchor_mode == "unescaped"),
        "model_offsets_usable": sum(
            1 for s in result.anchored_spans if s.model_offsets_yield_quote
        ),
        "per_effect": per_effect,
        "dropped": result.dropped,
    }


def reask_figures(records: list[dict]) -> dict:
    """T-89's re-ask across a recording, from the per-note `reask` blocks and
    the turns whose purpose is the quote re-ask."""
    turn_metrics = _load_run_extraction().turn_metrics
    blocks = [r["reask"] for r in records if r.get("reask")]
    return {
        "reask_notes": len(blocks),
        "reask_targets": sum(len(b["targets"]) for b in blocks),
        "reask_recovered": sum(len(b["recovered"]) for b in blocks),
        "reask_calls": sum(
            1 for m in turn_metrics(records) if m.get("purpose") == "quotes_reask"
        ),
    }


def aggregate(records: list[dict], effects_asked: int) -> dict:
    """The recording's headline figures. Token and call totals sum **every**
    turn through `turn_metrics` (D71); everything else sums the per-note
    `score` blocks, and a note with none was asked and produced nothing."""
    turn_metrics = _load_run_extraction().turn_metrics
    scored = [r for r in records if r.get("score")]

    def total(key: str) -> int:
        return sum(r["score"][key] for r in scored)

    turns = turn_metrics(records)
    return {
        "notes": len(scored),
        "failed": len(records) - len(scored),
        "effects_asked": effects_asked,
        "pairs": len(scored) * effects_asked,
        "pairs_with_quote": total("pairs_with_quote"),
        "pairs_anchored": total("pairs_anchored"),
        "pairs_fabricated": total("pairs_fabricated"),
        "quotes_returned": total("quotes_returned"),
        "quotes_anchored": total("quotes_anchored"),
        "quotes_refused": total("quotes_refused"),
        "quotes_blank": total("quotes_blank"),
        "unknown_effects": total("unknown_effects"),
        "spans_emitted": total("spans_emitted"),
        "spans_anchored": total("spans_anchored"),
        "spans_normalized": total("spans_normalized"),
        "spans_unescaped": total("spans_unescaped"),
        "model_offsets_usable": total("model_offsets_usable"),
        **reask_figures(records),
        "model_calls": len(turns),
        "total_input_tokens": sum(m["input_tokens"] for m in turns),
        "total_output_tokens": sum(m["output_tokens"] for m in turns),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in turns), 1),
    }


def _rederive_reask(result, prior: dict, document_id: str, text: str, rows) -> None:
    """`--rescore`'s half of the re-ask: the recovered set re-derived by
    anchoring both payloads, never copied (D18's rule on T-89's audit)."""
    result.trace = RunTrace.model_validate(prior["trace"]) if prior.get("trace") else None
    if not prior.get("reask"):
        return
    first = build_quote_result(document_id, text, prior["raw_first_turn"], rows=rows)
    targets = reask_targets(first, _locate_quote)
    still = {drop.get("path") for drop in result.dropped}
    result.raw_first_turn = prior["raw_first_turn"]
    result.reask = {
        **prior["reask"],
        "targets": targets,
        "recovered": [t["path"] for t in targets if t["path"] not in still],
        "unrecovered": [t["path"] for t in targets if t["path"] in still],
    }


def record_for(case: dict, result, scored: dict | None, error: str | None = None) -> dict:
    """One per-note record. `raw` is the payload the result was built from —
    patched after a re-ask — so `--rescore` and `RecordedQuoteRunner` reproduce
    the result exactly; `raw_first_turn` sits beside it (T-89)."""
    return {
        "note_id": case["note_id"],
        "corpus": case["corpus"],
        "document_id": case["document_id"],
        "document": case.get("document"),
        "note_sha256": case["sha256"],
        "cases": case.get("cases", []),
        "error": error,
        "quotes": (
            {row_id: [s.model_dump(mode="json") for s in spans] for row_id, spans in result.spans.items()}
            if result is not None else None
        ),
        "refused": (
            [d for d in result.dropped if d.get("path")] if result is not None else None
        ),
        "metrics": (
            result.metrics.model_dump(mode="json") if result is not None and result.metrics else None
        ),
        "trace": result.trace.model_dump(mode="json") if result is not None and result.trace else None,
        "raw": result.raw if result is not None else None,
        "raw_first_turn": result.raw_first_turn if result is not None else None,
        "reask": result.reask if result is not None else None,
        "score": scored,
    }


def run_with_retries(runner, case: dict, rows) -> tuple[object | None, str | None]:
    """A transport fault is retried, on the first turn and on the re-ask (a
    re-ask that failed on transport is not a measurement of the re-ask, D103);
    any other failure is a finding and is recorded, never retried (D71)."""
    for attempt in range(RETRIES):
        try:
            result = runner.run(case["document_id"], case["text"], rows)
        except QuoteOutputError as exc:
            if exc.reason is QuoteFailure.CALL_FAILED and attempt < RETRIES - 1:
                wait = 2 ** attempt * 5
                print(f"  {case['note_id']}: {exc.reason.value}, retry in {wait}s")
                time.sleep(wait)
                continue
            return None, str(exc)
        reask_error = (result.reask or {}).get("error") or ""
        if reask_error.startswith("CALL_FAILED") and attempt < RETRIES - 1:
            wait = 2 ** attempt * 5
            print(f"  {case['note_id']}: re-ask {reask_error[:60]}, retry in {wait}s")
            time.sleep(wait)
            continue
        return result, None
    return None, "retries exhausted"  # pragma: no cover - the loop returns before this


def _print_note(case: dict, scored: dict | None, result, error: str | None) -> None:
    if scored is None:
        print(f"  {case['note_id']:<24} FAILED: {error}")
        return
    reask_note = ""
    if result.reask:
        reask_note = (
            f" reask {len(result.reask['recovered'])}/{len(result.reask['targets'])} recovered"
            + (f" ({result.reask['error'][:40]})" if result.reask["error"] else "")
        )
    print(
        f"  {case['note_id']:<24} returned {scored['quotes_returned']} "
        f"anchored {scored['quotes_anchored']} refused {scored['quotes_refused']} "
        f"fabricated_pairs {scored['pairs_fabricated']}{reask_note}"
    )


def write_recording(out_path: Path, payload: dict) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Measure and rescore
# --------------------------------------------------------------------------


def measure(tier: str = MEASURED_TIER) -> int:
    cases = history_cases()
    rows = table_rows()
    client = _client(tier)
    from pa_agent.tiers import tier_of

    runner = DirectQuoteRunner(client, PINNED_MODEL)
    print(
        f"{len(cases)} notes × {len(rows)} conditions · direct · model {PINNED_MODEL} · "
        f"tier {tier_of(client)} · temperature {QUOTE_TEMPERATURE}"
    )
    records: list[dict] = []
    for case in cases:
        result, error = run_with_retries(runner, case, rows)
        scored = score(case, result, rows) if result is not None else None
        records.append(record_for(case, result, scored, error))
        _print_note(case, scored, result, error)

    figures = aggregate(records, len(rows))
    payload = {
        **PROVENANCE[tier],
        "runner": "direct",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "rescored_at": None,
        "model": PINNED_MODEL,
        # Read off the client, never the flag (D106).
        "tier": tier_of(client),
        "prompt_version": QUOTE_PROMPT_VERSION,
        "reask_rounds": REASK_ROUNDS,
        "temperature": QUOTE_TEMPERATURE,
        "table_id": table_id(),
        "rows_asked": rows_asked_record(rows),
        "schema_note": (
            "One call per note asks for verbatim passages documenting each of the "
            "knowledge table's conditions, by display only (D122). No committed "
            "note documents any, so pairs_fabricated is the figure."
        ),
        "aggregate": figures,
        "notes": records,
    }
    out_path = out_path_for(tier)
    write_recording(out_path, payload)
    print(json.dumps(figures, indent=2))
    print(f"written: {_display(out_path)}")
    return 0


def rescore(tier: str = MEASURED_TIER) -> int:
    """Re-anchor every recorded payload for zero calls. Refuses a note whose
    bytes moved (D18) and a table whose rows differ from the ones asked (D45)."""
    out_path = out_path_for(tier)
    if not out_path.exists():
        sys.exit(f"nothing to rescore at {out_path}; run without --rescore first")
    previous = json.loads(out_path.read_text(encoding="utf-8"))
    cases = history_cases()
    rows = table_rows()
    if previous.get("rows_asked") != rows_asked_record(rows):
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
            _print_note(case, None, None, prior.get("error"))
            continue
        result = build_quote_result(
            case["document_id"], case["text"], prior["raw"],
            CallMetrics.model_validate(prior["metrics"]) if prior.get("metrics") else None,
            rows=rows,
        )
        _rederive_reask(result, prior, case["document_id"], case["text"], rows)
        scored = score(case, result, rows)
        records.append(record_for(case, result, scored, None))
        _print_note(case, scored, result, None)

    figures = aggregate(records, len(rows))
    payload = {
        **{k: previous.get(k) for k in ("task", "decision", "supersedes")},
        "runner": previous.get("runner", "direct"),
        "measured_at": previous["measured_at"],
        "rescored_at": datetime.now(timezone.utc).isoformat(),
        "model": PINNED_MODEL,
        "tier": previous["tier"],
        "prompt_version": QUOTE_PROMPT_VERSION,
        "reask_rounds": REASK_ROUNDS,
        "temperature": QUOTE_TEMPERATURE,
        "table_id": previous.get("table_id"),
        "rows_asked": previous["rows_asked"],
        "schema_note": previous.get("schema_note"),
        "aggregate": figures,
        "notes": records,
    }
    write_recording(out_path, payload)
    print(json.dumps(figures, indent=2))
    print(f"written: {_display(out_path)}")
    return 0


def main() -> int:
    tier = _named_tier(sys.argv)
    if "--rescore" in sys.argv:
        return rescore(tier)
    return measure(tier)


if __name__ == "__main__":
    sys.exit(main())
