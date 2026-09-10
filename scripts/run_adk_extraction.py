"""T-63 — measure the ADK extraction runner against the direct one (D62).

    python scripts/run_adk_extraction.py                  measure. Spends calls.
                                     -> eval/extraction/adk_results_inline.json
    python scripts/run_adk_extraction.py --tool-fetch     ditto, model fetches
                                                          the note through its tool
                                     -> eval/extraction/adk_results_tool_fetch.json
    python scripts/run_adk_extraction.py --compare        compare a recording with
    python scripts/run_adk_extraction.py --compare --tool-fetch    the direct one.
                                                          Spends nothing.

**One recording per mode (T-68, D68).** The path is derived from `--tool-fetch`
rather than chosen by the caller, so no invocation of either mode can land on the
other's file. `--compare` takes the same flag, prints the path it read, and
refuses when a recording's own `tool_fetch` disagrees with the mode requested.

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

### The corpus is two corpora, and only one of them has an address (T-67, D67)

`--tool-fetch` hands the model a `document_id` and nothing else; `read_note`
resolves it through `PatientStore.get_document`. Spike 001's five notes have no
patient — no bundle, no structured observation, nothing for REQ-34 to reconcile
against — so the port raises on their ids before the model is asked anything. That
is not a defect to repair: extraction is the one thing here that needs no patient,
which is why `ExtractionRunner.run(document_id, text)` carries no patient id.
**They are skipped under `--tool-fetch`, and D67 argues against giving them a
patient-plane address to fix it.**

Three consequences, all of them visible in the output:

- **Addressability is asked of the port**, never inferred from `case["corpus"]`.
  A corpus-name test answers identically today and is the read-structure-out-of-
  an-id shape D65 and T-66 deleted twice. Asking the port means the split closes
  by itself if the corpus ever changes.
- **`skipped` is not `failed`.** `failed` means the model was asked and produced
  nothing; `skipped` means it was never asked. D27's `BLOCKED`-is-not-`FAIL` line,
  at a third site.
- **`by_corpus` is reported in every mode**, and `--compare` recomputes both
  aggregates over the *intersection* of the note ids the two recordings cover. An
  eleven-note column beside a six-note column is two columns of numbers that look
  like a comparison and are not one.
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

#: One recording per mode, and the mode names the file (T-68, D68). `inline` is
#: what the mode does — the note text travels inline in the message — rather than
#: what it lacks; a file named for the absence of a flag describes the command
#: that produced it instead of the measurement it holds.
ADK_INLINE_PATH = OUT_DIR / "adk_results_inline.json"
ADK_TOOL_FETCH_PATH = OUT_DIR / "adk_results_tool_fetch.json"


def adk_path(tool_fetch: bool) -> Path:
    """The recording this mode writes and `--compare` reads.

    Derived, never passed in. An `--out` argument would keep one default, so the
    run that forgets it clobbers exactly as before — avoidable rather than
    impossible — and it would put a measurement's identity in shell history
    instead of in the repo (D68).

    The globals are read at call time so a test can point either mode at a tmp
    directory.
    """
    return ADK_TOOL_FETCH_PATH if tool_fetch else ADK_INLINE_PATH

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


def _record_base(case: dict) -> dict:
    """The fields every record carries, whatever the note's outcome (T-67).

    One builder for all three outcomes, because there is no version of this where
    a scored note and a skipped one should be identified differently — and because
    the three copies this replaces had all inlined `case["labels"]`, a key
    `spike_cases()` and `synthesized_cases()` do not produce. The script raised
    `KeyError` on note one of every run and nothing caught it, since T-63 spends
    model calls and is in no gate. `tests/test_adk_measurement.py` builds a record
    from a real case for that reason.
    """
    return {
        "note_id": case["note_id"],
        "corpus": case["corpus"],
        "document_id": case["document_id"],
        "note_sha256": case["sha256"],
        "cases": case.get("cases", []),
        # Assembled here exactly as `run_extraction.py` assembles it, so the two
        # recordings stay diffable record for record.
        "labels": {
            "events": case["events"],
            "traps": case["traps"],
            "assertion_required": case["assertion_required"],
        },
    }


# --------------------------------------------------------------------------
# Which notes the tool path can reach, asked of the port (T-67, D67)
# --------------------------------------------------------------------------


def addressable(store, case: dict) -> str | None:
    """`None` if the tool path can reach this note, else why it cannot.

    **The port is asked; the corpus label is never consulted.** Today
    `case["corpus"] == "spike_001"` would answer identically on all eleven notes,
    which is exactly what makes it the wrong test: it is D65's read-structure-out-
    of-an-identifier written into a script, where it would keep answering
    correctly right up until the corpus changed and then answer confidently and
    wrongly. Asking `get_document` costs one dict lookup and one file read, and it
    is the same call `read_note` will make.
    """
    try:
        store.get_document(case["document_id"])
    except Exception as exc:
        return f"{type(exc).__name__}: no address on the patient plane"
    return None


def partition(cases: list[dict], store, tool_fetch: bool) -> tuple[list, list]:
    """Split the corpus into what this mode can measure and what it must skip.

    Without `tool_fetch` the note text travels in the message and every case is
    measurable — the `document_id` is a label there, not a lookup key. With it,
    the id *is* the lookup key, so a note the port cannot resolve is one the model
    can never be asked about.
    """
    if not tool_fetch:
        return list(cases), []
    measurable: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    for case in cases:
        reason = addressable(store, case)
        if reason is None:
            measurable.append(case)
        else:
            skipped.append((case, reason))
    return measurable, skipped


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

    store = LocalPatientStore()
    # The corpus order every recording is written in, so a skipped note still
    # appears where a reader diffing against results.json expects to find it.
    order = {case["note_id"]: i for i, case in enumerate(cases)}
    cases, skipped = partition(cases, store, tool_fetch)

    runner = AdkExtractionRunner(
        client=_client() if cases else None,
        patient_store=store,
        tool_fetch=tool_fetch,
        model=PINNED_MODEL,
    )

    print(
        f"{len(cases)} notes · adk · model {PINNED_MODEL} · tier {MEASURED_TIER} · "
        f"tool_fetch={tool_fetch} · max_llm_calls={DEFAULT_MAX_LLM_CALLS}"
    )
    for case, reason in skipped:
        # Named, not silently dropped. A corpus this mode cannot address is a fact
        # about the corpus, and it belongs in the run's own output rather than only
        # in D67 (T-67).
        print(f"  SKIP {case['note_id']} ({case['corpus']}): {reason}")
    if tool_fetch and MEASURED_TIER != "vertex":
        print(
            "  NOTE: output_schema + tools is native on Vertex only. On this tier "
            "ADK injects a set_model_response tool and an extra instruction, so "
            "this run's prompt differs from a Vertex run's (D62)."
        )

    records: list[dict] = [
        {
            **_record_base(case),
            # A third outcome, deliberately not `error`. The model was never
            # asked, so nothing about the runner is being reported here (D67).
            "skipped": reason,
            "raw": None,
            "score": None,
        }
        for case, reason in skipped
    ]
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
                    **_record_base(case),
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
                **_record_base(case),
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
        "aggregate": _aggregate(records),
        "notes": sorted(records, key=lambda r: order[r["note_id"]]),
    }
    out_path = adk_path(tool_fetch)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print()
    print(json.dumps(payload["aggregate"], indent=2))
    print(f"\nwrote {_display(out_path)}")
    return 0


def _display(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _adk_version() -> str:
    import google.adk

    return google.adk.__version__


def _aggregate(every: list[dict], nest: bool = True) -> dict:
    """The same figures `run_extraction.py` reports, over three outcomes.

    `notes` counts what was scored; `failed` counts what was asked and produced
    nothing; `skipped` counts what was never asked, because this mode has no
    address for it (T-67). Keeping the first two apart is REQ-28's rule — a fault
    is never folded into a finding — and a run where three notes errored and eight
    scored 1.000 is not a run that scored 1.000.

    Keeping the **third** apart is D67's, and it is the same rule read once more:
    a skip says nothing about the runner. Counting spike 001's five unaddressable
    notes as failures would report the ADK runner failing five of eleven notes it
    was never given.

    `by_corpus` is reported in every mode rather than only when something was
    skipped. A figure that appears when there is a problem and vanishes when there
    is not is a figure nobody learns to read — and the synthesized six should be
    comparable across all four runner × `tool_fetch` combinations without the
    spike five diluting one side.
    """
    scored = [r for r in every if r.get("score")]
    skipped = [r for r in every if r.get("skipped")]
    by_corpus = {}
    if nest:
        for corpus in sorted({r["corpus"] for r in every}):
            subset = [r for r in every if r["corpus"] == corpus]
            by_corpus[corpus] = _aggregate(subset, nest=False)

    if not scored:
        return {
            "notes": 0,
            "failed": len(every) - len(skipped),
            "skipped": len(skipped),
            **({"by_corpus": by_corpus} if nest else {}),
        }

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
        "failed": len(every) - len(scored) - len(skipped),
        "skipped": len(skipped),
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
        **({"by_corpus": by_corpus} if nest else {}),
    }


def _covered(recording: dict) -> dict[str, dict]:
    """The notes a recording actually scored, by `note_id`.

    A failed note and a skipped note are both uncovered, for the same reason and
    with different meanings: neither produced a score, so neither can be one side
    of a comparison. Which of the two it was is reported, never averaged away.
    """
    return {r["note_id"]: r for r in recording["notes"] if r.get("score")}


def _uncovered(recording: dict) -> list[tuple[str, str]]:
    """Every note a recording did not score, paired with why."""
    out = []
    for record in recording["notes"]:
        if record.get("score"):
            continue
        if record.get("skipped"):
            out.append((record["note_id"], f"skipped: {record['skipped']}"))
        else:
            out.append((record["note_id"], f"failed: {record.get('error', 'no score')}"))
    return out


def _column(left: dict, right: dict, title: str, right_head: str = "adk") -> None:
    width = max(len(k) for k in COMPARED)
    print(f"  {title:<{width}}  {'direct':>14}  {right_head:>14}")
    for key in COMPARED:
        a, b = left.get(key), right.get(key)
        flag = "" if a == b else "   <- differs"
        print(f"  {key:<{width}}  {str(a):>14}  {str(b):>14}{flag}")


def _mode(tool_fetch: bool) -> str:
    """The name this mode goes by in output. Not a filename, and not the flag."""
    return "tool_fetch" if tool_fetch else "inline"


def compare(tool_fetch: bool = False) -> int:
    """Compare one ADK recording with the direct one, over the notes both scored.

    **Recomputed, not read off.** Each file's own `aggregate` covers that file's
    corpus, and under `--tool-fetch` the ADK recording covers six notes where the
    direct one covers eleven. Printing those two aggregates beside each other
    produces twelve rows of numbers that look like a comparison and are not one —
    the difference in every row would be five notes, not the runner (T-67, D67).
    So the two columns are rebuilt from the per-note records over the intersection,
    with the same `_aggregate` that produced the file-level figures, and everything
    dropped from either side is named.

    **One mode per invocation (T-68, D68).** `--tool-fetch` selects which ADK
    recording is read, the same flag that selected which one was written, and the
    path is printed so the reader is never guessing which file a column came from.

    Spends nothing.
    """
    adk_source = adk_path(tool_fetch)
    wanted = (
        (DIRECT_PATH, "python scripts/run_extraction.py"),
        (
            adk_source,
            "python scripts/run_adk_extraction.py"
            + (" --tool-fetch" if tool_fetch else ""),
        ),
    )
    missing = [(path, command) for path, command in wanted if not path.exists()]
    if missing:
        # The command is named per file, `--tool-fetch` included, because the two
        # ADK recordings are made by two different invocations and telling a
        # reader to "run the script" is telling them to guess which one.
        print(
            "missing:\n"
            + "\n".join(
                f"  {_display(path)} — run `{command}` (spends model calls)"
                for path, command in missing
            ),
            file=sys.stderr,
        )
        return 2

    direct = json.loads(DIRECT_PATH.read_text(encoding="utf-8"))
    adk = json.loads(adk_source.read_text(encoding="utf-8"))

    # The mode is read off the payload, and the guard below is what makes that
    # worth doing: without it a caption taken from the request would be true by
    # construction and would report nothing. With it the two are provably equal
    # by the time the caption is built — so the refusal is the load-bearing half,
    # and the payload-sourced label is what keeps it that way (D68).
    recorded_mode = _mode(bool(adk.get("tool_fetch")))
    if recorded_mode != _mode(tool_fetch):
        print(
            f"  !! {_display(adk_source)} records tool_fetch="
            f"{adk.get('tool_fetch')}, which is the {recorded_mode} mode, but "
            f"{_mode(tool_fetch)} was asked for.\n"
            "  Refusing: a comparison that mislabels its own column is not one "
            "(D68).",
            file=sys.stderr,
        )
        return 2

    print(f"  direct : {_display(DIRECT_PATH)}")
    print(f"           {direct['model']} · tier {direct.get('tier')} · google-genai")
    print(f"  adk    : {_display(adk_source)}")
    print(
        f"           {adk['model']} · tier {adk.get('tier')} · google-adk "
        f"{adk.get('adk_version')} · tool_fetch={adk.get('tool_fetch')}"
    )
    if direct["model"] != adk["model"]:
        print("  !! different models: this is not a comparison of two runners")
    if direct.get("tier") != adk.get("tier"):
        print("  !! different tiers: see D62 on the set_model_response fallback")

    left_covered, right_covered = _covered(direct), _covered(adk)
    shared = sorted(set(left_covered) & set(right_covered))
    if not shared:
        print(
            "\n  the two recordings share no scored note; there is nothing to "
            "compare",
            file=sys.stderr,
        )
        return 2

    left = [left_covered[n] for n in shared]
    right = [right_covered[n] for n in shared]
    corpora = sorted({r["corpus"] for r in left})
    print(f"\n  comparing {len(shared)} note(s) both runners scored · "
          f"corpus: {', '.join(corpora)}")

    for side, recording in (("direct", direct), ("adk", adk)):
        dropped = _uncovered(recording) + [
            (note_id, "scored here, not scored by the other runner")
            for note_id in sorted(set(_covered(recording)) - set(shared))
        ]
        for note_id, why in sorted(dropped):
            print(f"    - {side}: {note_id} excluded ({why})")
    print()

    head = f"adk·{recorded_mode}"
    _column(_aggregate(left, nest=False), _aggregate(right, nest=False), "", head)

    if len(corpora) > 1:
        # Per corpus as well as overall, because the two halves are different
        # measurement corpora — one Troy wrote for the spike, one T-07 rendered
        # from T-06's manifests — and an aggregate over both hides which moved.
        for corpus in corpora:
            print()
            _column(
                _aggregate([r for r in left if r["corpus"] == corpus], nest=False),
                _aggregate([r for r in right if r["corpus"] == corpus], nest=False),
                corpus,
                head,
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
        help=(
            "the model reads the note through its scoped read_note tool; selects "
            "the mode in both verbs, so it also picks which recording --compare "
            "reads"
        ),
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="compare this mode's recording with the direct one; spends nothing",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="measure only the first N notes"
    )
    args = parser.parse_args(argv)

    if args.compare:
        return compare(args.tool_fetch)
    return measure(args.tool_fetch, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
