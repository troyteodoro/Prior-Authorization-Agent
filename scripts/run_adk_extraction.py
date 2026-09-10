"""T-63 — measure the ADK extraction runner against the direct one (D62).

    python scripts/run_adk_extraction.py                  measure. Spends calls.
    python scripts/run_adk_extraction.py --tool-fetch     ditto, model fetches
                                                          the note through its tool
    python scripts/run_adk_extraction.py --compare        compare two recordings.
                                                          Spends nothing.

**Spends model calls, so it is in no gate.** `pytest` and `eval/run_eval.py` read
recordings; this is the thing that makes one.

### Why this exists at all

`pa_agent/extraction.py` calls `google-genai` directly with a native
`response_schema`. `pa_agent/agent/extraction_agent.py` calls the same model
through `google-adk`. **Different call configuration, therefore a different
measurement** — D45's own rule for itself, and the reason nothing may quote D45's
numbers for the ADK path until this script has run.

### Why the tier is printed in bold

`output_schema` and `tools` work together in 2.8.0, but natively only on Vertex:
`flows/llm_flows/basic.py` sets a native response schema only when
`model.capabilities.output_schema_and_tools`, and `models/_capabilities.py` gates
that on the Vertex variant. On AI Studio with tools present, ADK instead injects a
`SetModelResponseTool` and appends an instruction telling the model to answer
through it.

D5 develops on AI Studio and evals on Vertex. So under `--tool-fetch` **the two
tiers run different prompts**, and a number from one is not a number for the
other. `MEASURED_TIER` records which produced a result; this is the first case
where the tier changes the request and not just the endpoint.

### The corpus and the scorer are `scripts/run_extraction.py`'s, imported

Not reimplemented. Two scorers agreeing is a weaker claim than one scorer applied
twice, and the whole point of this script is a comparison — so the only thing that
differs between the two recordings is the runner.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL  # noqa: E402
from pa_agent.runners import ExtractionOutputError  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

OUT_DIR = REPO_ROOT / "eval" / "extraction"
DIRECT_PATH = OUT_DIR / "results.json"
ADK_PATH = OUT_DIR / "adk_results.json"

#: The headline figures the comparison reports, in the order a reader wants them.
COMPARED = (
    "notes",
    "precision",
    "recall",
    "req9_exclusion_recall",
    "field_agreement",
    "spans_anchored",
    "spans_emitted",
    "model_offsets_usable",
    "total_input_tokens",
    "total_output_tokens",
    "total_wall_time_ms",
)


def _load_run_extraction():
    """Import `scripts/run_extraction.py` as a module.

    A script, not a package, so it goes through `importlib` — the same pattern
    `tests/test_model_pin.py` uses for the spike. Reused rather than copied because
    the corpus builder and the scorer must be *identical* for a comparison to mean
    anything, and identical is what one import guarantees.
    """
    path = Path(__file__).resolve().parent / "run_extraction.py"
    spec = importlib.util.spec_from_file_location("run_extraction", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_extraction"] = module
    spec.loader.exec_module(module)
    return module


def _client():
    base = _load_run_extraction()
    base.load_env()
    from google import genai

    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


def measure(tool_fetch: bool, limit: int | None = None) -> int:
    base = _load_run_extraction()
    from pa_agent.agent.extraction_agent import (
        DEFAULT_MAX_LLM_CALLS,
        PROMPT_VERSION,
        AdkExtractionRunner,
    )

    cases = base.spike_cases() + base.synthesized_cases()
    if limit is not None:
        cases = cases[:limit]

    runner = AdkExtractionRunner(
        client=_client(),
        patient_store=LocalPatientStore(),
        tool_fetch=tool_fetch,
        model=PINNED_MODEL,
    )

    print(
        f"{len(cases)} notes · adk · model {PINNED_MODEL} · tier {MEASURED_TIER} · "
        f"tool_fetch={tool_fetch} · max_llm_calls={DEFAULT_MAX_LLM_CALLS}"
    )
    if tool_fetch and MEASURED_TIER != "vertex":
        print(
            "  NOTE: output_schema + tools is native on Vertex only. On this tier "
            "ADK injects a set_model_response tool and an extra instruction, so "
            "this run's prompt differs from a Vertex run's (D62)."
        )

    records: list[dict] = []
    for index, case in enumerate(cases, start=1):
        print(f"  [{index}/{len(cases)}] {case['note_id']}", flush=True)
        try:
            result = runner.run(case["document_id"], case["text"])
        except ExtractionOutputError as exc:
            # Recorded, never swallowed, and never scored: a note that produced no
            # extraction has nothing to score, and scoring it anyway is the trap
            # `spike/spike_001/run.py` documents — it would leak no traps and
            # contribute no false positives, reading as flawless extraction.
            print(f"      FAILED: {exc}")
            records.append(
                {
                    "note_id": case["note_id"],
                    "corpus": case["corpus"],
                    "document_id": case["document_id"],
                    "note_sha256": case["sha256"],
                    "cases": case.get("cases", []),
                    "labels": case["labels"],
                    "error": str(exc),
                    "raw": None,
                    "score": None,
                }
            )
            continue

        scored = base.score(case, result)
        trace = result.trace
        records.append(
            {
                "note_id": case["note_id"],
                "corpus": case["corpus"],
                "document_id": case["document_id"],
                "note_sha256": case["sha256"],
                "cases": case.get("cases", []),
                "labels": case["labels"],
                "events": [e.model_dump(mode="json") for e in result.events],
                "assertions": [a.model_dump(mode="json") for a in result.assertions],
                "current_bmi": result.current_bmi,
                "current_bmi_span": (
                    result.current_bmi_span.model_dump(mode="json")
                    if result.current_bmi_span
                    else None
                ),
                "metrics": (
                    result.metrics.model_dump(mode="json") if result.metrics else None
                ),
                # REQ-49: the tool-call sequence, attempts and termination reason.
                # The direct recording has no equivalent, which is itself part of
                # what the two runners differ by.
                "trace": trace.model_dump(mode="json") if trace else None,
                "raw": result.raw,
                "score": scored,
            }
        )

    scored_records = [r for r in records if r.get("score")]
    payload = {
        "task": "T-63",
        "decision": "D62",
        "runner": "adk",
        "tool_fetch": tool_fetch,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "model": PINNED_MODEL,
        "tier": MEASURED_TIER,
        "prompt_version": PROMPT_VERSION,
        "temperature": 0.0,
        "adk_version": _adk_version(),
        "schema_note": (
            "Same corpus, same labels and the same scorer as "
            "eval/extraction/results.json — scripts/run_extraction.py's, imported "
            "rather than reimplemented. The runner is the only difference (D62)."
        ),
        "aggregate": _aggregate(scored_records, records),
        "notes": records,
    }
    ADK_PATH.parent.mkdir(parents=True, exist_ok=True)
    ADK_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print()
    print(json.dumps(payload["aggregate"], indent=2))
    print(f"\nwrote {ADK_PATH.relative_to(REPO_ROOT)}")
    return 0


def _adk_version() -> str:
    import google.adk

    return google.adk.__version__


def _aggregate(scored: list[dict], every: list[dict]) -> dict:
    """The same figures `run_extraction.py` reports, plus the failures.

    `notes` counts what was scored; `failed` counts what could not be. Keeping them
    separate is REQ-28's rule — a fault is never folded into a finding — and the
    reason it matters here is that a run where three notes errored and eight scored
    1.000 is not a run that scored 1.000.
    """
    if not scored:
        return {"notes": 0, "failed": len(every)}

    def total(key: str) -> int:
        return sum(r["score"][key] for r in scored)

    labeled = total("labeled_events")
    extracted = total("extracted_events")
    matched = total("matched_events")
    req9_total = total("traps_req9")
    req9_taken = sum(len(r["score"]["req9_traps_extracted"]) for r in scored)
    field_total = total("field_total")
    field_disagreements = sum(
        len(r["score"]["field_disagreements"]) for r in scored
    )
    metrics = [r["metrics"] for r in scored if r.get("metrics")]
    tool_calls = sum(
        len((r.get("trace") or {}).get("tool_calls") or []) for r in scored
    )

    return {
        "notes": len(scored),
        "failed": len(every) - len(scored),
        "labeled_events": labeled,
        "extracted_events": extracted,
        "matched_events": matched,
        "precision": round(matched / extracted, 4) if extracted else None,
        "recall": round(matched / labeled, 4) if labeled else None,
        "req9_traps": req9_total,
        "req9_traps_excluded": req9_total - req9_taken,
        "req9_exclusion_recall": (
            round((req9_total - req9_taken) / req9_total, 4) if req9_total else None
        ),
        "spans_emitted": total("spans_emitted"),
        "spans_anchored": total("spans_anchored"),
        "spans_normalized": total("spans_normalized"),
        "spans_unescaped": total("spans_unescaped"),
        "spans_disambiguated": total("spans_disambiguated"),
        "model_offsets_usable": total("model_offsets_usable"),
        "field_total": field_total,
        "field_agreement": (
            round((field_total - field_disagreements) / field_total, 4)
            if field_total
            else None
        ),
        "tool_calls": tool_calls,
        "total_input_tokens": sum(m["input_tokens"] for m in metrics),
        "total_output_tokens": sum(m["output_tokens"] for m in metrics),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in metrics), 1),
    }


def compare() -> int:
    """Print the two recordings' aggregates side by side. Spends nothing."""
    missing = [p for p in (DIRECT_PATH, ADK_PATH) if not p.exists()]
    if missing:
        print(
            "missing: "
            + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing)
            + "\nrun `python scripts/run_extraction.py` and "
            "`python scripts/run_adk_extraction.py` (both spend model calls)",
            file=sys.stderr,
        )
        return 2

    direct = json.loads(DIRECT_PATH.read_text(encoding="utf-8"))
    adk = json.loads(ADK_PATH.read_text(encoding="utf-8"))

    print(f"  direct : {direct['model']} · tier {direct.get('tier')} · google-genai")
    print(
        f"  adk    : {adk['model']} · tier {adk.get('tier')} · google-adk "
        f"{adk.get('adk_version')} · tool_fetch={adk.get('tool_fetch')}"
    )
    if direct["model"] != adk["model"]:
        print("  !! different models: this is not a comparison of two runners")
    if direct.get("tier") != adk.get("tier"):
        print("  !! different tiers: see D62 on the set_model_response fallback")
    print()

    width = max(len(k) for k in COMPARED)
    print(f"  {'':<{width}}  {'direct':>14}  {'adk':>14}")
    for key in COMPARED:
        left = direct["aggregate"].get(key)
        right = adk["aggregate"].get(key)
        flag = "" if left == right else "   <- differs"
        print(f"  {key:<{width}}  {str(left):>14}  {str(right):>14}{flag}")

    if adk["aggregate"].get("failed"):
        print(
            f"\n  {adk['aggregate']['failed']} note(s) produced no extraction and "
            "are excluded from every figure above (REQ-28)."
        )
    print(
        "\n  Neither column may be quoted for the other runner. D45's rule: a "
        "changed call configuration is a new measurement."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/run_adk_extraction.py", description=__doc__
    )
    parser.add_argument(
        "--tool-fetch",
        action="store_true",
        help="the model fetches the note through get_patient_document",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="compare the two recordings and spend nothing",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="measure only the first N notes"
    )
    args = parser.parse_args(argv)

    if args.compare:
        return compare()
    return measure(args.tool_fetch, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
