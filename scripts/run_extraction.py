"""T-15 — run extraction over both note corpora and record the measurement (D45).

    python scripts/run_extraction.py            # spends one model call per note, two on a re-ask
    python scripts/run_extraction.py --rescore  # re-anchors recorded payloads, no calls

Since T-89 (D103) a note whose first turn returned a quote Python could not
locate costs a second call — the verbatim re-ask — and every turn is on the
record: per note as `trace`, and in the aggregate through `turn_metrics()`,
which is D71's rule (count every model turn, not just the first) applied to
the one measurement script it had not reached.

`pytest tests/test_extraction.py` verifies what this writes and spends
nothing. That split is D17's, and D45 keeps it for D17's reasons: a gate that
calls a model is slow, rate-limited, and answers differently on two
invocations. The staleness it invites is answered by re-hashing every note
here and re-validating every span there.

`--rescore` exists for D18's reason, proven by D46: changing the anchoring
rule should not cost a re-measurement. Each record keeps the raw model payload,
so a new rule replays against the quotes already returned — sound only because
the notes are re-hashed first, since re-anchoring against a note that moved
would be scoring a quote against a document it never came from.

Labels come from `spike/spike_001/labels.json` for the spike corpus and from
`eval/manifests/` for the synthesized one — one source of truth per note, so a
second label file cannot disagree and make a corpus defect look like a model
error (D42, D45).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import CallMetrics, RunTrace  # noqa: E402
from pa_agent.extraction import (  # noqa: E402
    EXTRACTION_TEMPERATURE,
    PROMPT_VERSION,
    REASK_ROUNDS,
    build_result,
    extract,
    reask_targets,
)
from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL, SECOND_TIER  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

SPIKE_DIR = REPO_ROOT / "spike" / "spike_001"
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
OUT_DIR = REPO_ROOT / "eval" / "extraction"
OUT_PATH = OUT_DIR / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

#: Which task and entry own each tier's recording. A Vertex run is its own
#: measurement, not a re-run of T-81's, so it carries its own provenance
#: (D45, D106) — and a `--rescore` carries forward whatever the file already
#: says rather than restamping it.
PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-81", "decision": "D104", "supersedes": "T-89 (D103)"},
    SECOND_TIER: {"task": "T-90", "decision": "D106", "supersedes": None},
}


def _named_tier(argv: list[str]) -> str:
    """`--tier X` or `--tier=X`, defaulting to the development tier.

    An unrecognised tier exits rather than falling back: the whole point of the
    flag is that a recording says which tier produced it, and a silent default
    would let a typo stamp the wrong one (D106).
    """
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
    """One recording per tier, side by side. The AI Studio path is unchanged,
    so every existing invocation and every gate reads the same bytes (D106)."""
    return OUT_DIR / ("results.json" if tier == MEASURED_TIER else f"results_{tier}.json")

RETRIES = 4  # the free tier's 429/503 behavior under load (D5, D20)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_env() -> None:
    """Read the gitignored .env. No credential is ever written to a tracked
    file (working rule 10); this only moves one into the process."""
    if not ENV_PATH.exists():
        sys.exit(f"no {ENV_PATH}; a live run needs its tier's credentials (D5)")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def spike_cases() -> list[dict]:
    labels = json.loads((SPIKE_DIR / "labels.json").read_text(encoding="utf-8"))
    cases = []
    for note in labels["notes"]:
        text = (SPIKE_DIR / note["file"]).read_text(encoding="utf-8")
        cases.append({
            "note_id": note["note_id"],
            "corpus": "spike_001",
            "document_id": note["note_id"],
            "text": text,
            "sha256": sha256(text),
            "events": [
                {"date": e["date"], "bmi_documented": e["bmi_documented"],
                 "diet_documented": e["diet_documented"],
                 "activity_documented": e["activity_documented"]}
                for e in note["events"]
            ],
            "traps": [{"date": t["date"], "reason": t["reason"]} for t in note["traps"]],
            "assertion_required": note["program_assertion_required"],
        })
    return cases


def synthesized_cases() -> list[dict]:
    """Labels derived from T-06's manifests — the same facts T-07 rendered."""
    store = LocalPatientStore()
    cases = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("note") is False:
            # E12's chart is declared note-free (D73): nothing to extract,
            # so the measurement never spends a call on it.
            continue
        if manifest.get("cloned_from"):
            # A declared clone's note is its source's bytes (T-88, D102): the
            # source's row measures it, and `RecordedExtractionRunner` replays
            # by content. Skipped by declaration rather than by duplicate
            # hash, so which row "owns" the bytes never depends on sort order.
            continue
        # T-81 (D104): a chart is the documents its manifest declares, and
        # every fact names the one it is rendered into. One recording row per
        # document, labeled with that document's facts only, keyed by the
        # case list and the document's ordinal so no two rows share an id.
        served = {
            d.document_id: d for d in store.get_notes(manifest["patient_id"])
        }
        for ordinal, basename in enumerate(manifest["documents"], 1):
            document = served[f"{manifest['patient_id']}/{basename}"]
            events = [
                {"date": e["date"],
                 "bmi_documented": bool(e.get("bmi_documented")),
                 "diet_documented": bool(e.get("diet_documented")),
                 "activity_documented": bool(e.get("activity_documented"))}
                for program in manifest["wm_programs"]
                for e in program["encounters"]
                if e["document"] == basename
            ]
            cases.append({
                "note_id": f"{'+'.join(manifest['cases'])}/{ordinal}",
                "corpus": "synthesized",
                "document_id": document.document_id,
                "document": basename,
                "text": document.text,
                "sha256": document.sha256,
                "cases": manifest["cases"],
                "events": sorted(events, key=lambda e: e["date"]),
                "traps": [
                    {"date": t["date"], "reason": t["type"]}
                    for t in manifest["traps"] if t["document"] == basename
                ],
                "assertion_required": any(
                    a["document"] == basename for a in manifest["program_assertions"]
                ),
            })
    return cases


def score(case: dict, result) -> dict:
    """D17's rule: an extracted event matches a labeled one on the ISO date.

    Matching on span overlap would credit the model for citing the right
    passage while reading the wrong date out of it, and c3 buckets by month —
    a right span with a wrong date is the worst failure this system has,
    wearing a citation that makes it look correct.
    """
    labeled = {e["date"] for e in case["events"]}
    extracted = {e.event_date.isoformat() for e in result.events}
    matched = labeled & extracted

    trap_dates = {t["date"] for t in case["traps"]}
    req9_traps = {
        t["date"] for t in case["traps"]
        if t["reason"] in ("missed_visit", "unsuccessful_contact", "unsupervised_attempt")
    }
    trapped = trap_dates & extracted

    by_date = {e["date"]: e for e in case["events"]}
    field_agreements = 0
    field_total = 0
    field_disagreements = []
    for event in result.events:
        iso = event.event_date.isoformat()
        if iso not in by_date:
            continue
        expected = by_date[iso]
        for name, actual in (
            ("bmi_documented", event.bmi is not None),
            ("diet_documented", event.diet_documented),
            ("activity_documented", event.activity_documented),
        ):
            field_total += 1
            if expected[name] == actual:
                field_agreements += 1
            else:
                field_disagreements.append(
                    {"date": iso, "field": name,
                     "expected": expected[name], "got": actual}
                )

    anchored = [s for s in result.anchored_spans if s.anchored]
    return {
        "labeled_events": len(labeled),
        "extracted_events": len(extracted),
        "matched_events": len(matched),
        "precision": round(len(matched) / len(extracted), 4) if extracted else None,
        "recall": round(len(matched) / len(labeled), 4) if labeled else None,
        "missed": sorted(labeled - extracted),
        "spurious": sorted(extracted - labeled),
        "traps_total": len(trap_dates),
        "traps_req9": len(req9_traps),
        "traps_extracted": sorted(trapped),
        "req9_traps_extracted": sorted(trapped & req9_traps),
        "assertions": len(result.assertions),
        "assertion_required": case["assertion_required"],
        "spans_emitted": len(result.anchored_spans),
        "spans_anchored": len(anchored),
        "spans_normalized": sum(1 for s in anchored if s.anchor_mode == "normalized"),
        "spans_unescaped": sum(1 for s in anchored if s.anchor_mode == "unescaped"),
        "spans_multi_occurrence": sum(1 for s in anchored if s.occurrences > 1),
        "spans_disambiguated": sum(1 for s in anchored if s.disambiguated),
        "model_offsets_usable": sum(
            1 for s in result.anchored_spans if s.model_offsets_yield_quote
        ),
        "dropped": result.dropped,
        "field_agreements": field_agreements,
        "field_total": field_total,
        "field_disagreements": field_disagreements,
    }


def turn_metrics(records: list[dict]) -> list[dict]:
    """Every model turn's metrics, not just the first one of each note (D71).

    A record carries a singular `metrics` — the `ExtractionResult`'s, which is
    turn one — and a `trace` whose `metrics` list holds **every** turn. Since
    T-89 a direct note can cost two (the re-ask), and summing the singular field
    would drop the second: the 12.1x undercount D71 measured on the tool-fetch
    path, one script over. Article X says measured, never estimated. The
    fallback to the singular field is for a recording written before traces
    existed, which aggregates the only thing it has.
    """
    out: list[dict] = []
    for record in records:
        turns = (record.get("trace") or {}).get("metrics") or []
        if turns:
            out.extend(turns)
        elif record.get("metrics"):
            out.append(record["metrics"])
    return out


def reask_figures(records: list[dict]) -> dict:
    """What T-89's re-ask did across a recording (REQ-56, D103), from the
    per-note `reask` blocks: notes re-asked, quotes asked about, quotes
    recovered, and the re-ask turns spent."""
    blocks = [r["reask"] for r in records if r.get("reask")]
    return {
        "reask_notes": len(blocks),
        "reask_targets": sum(len(b["targets"]) for b in blocks),
        "reask_recovered": sum(len(b["recovered"]) for b in blocks),
        "reask_calls": sum(
            1 for m in turn_metrics(records) if m.get("purpose") == "extraction_reask"
        ),
    }


def _rederive_reask(result, prior: dict, document_id: str, text: str) -> None:
    """`--rescore`'s half of the re-ask: the recovered set re-derived by
    anchoring both payloads, never copied from the record (D18's rule applied
    to T-89's audit). The answers, the error and the trace are the measured
    facts and carry through unchanged."""
    result.trace = RunTrace.model_validate(prior["trace"]) if prior.get("trace") else None
    if not prior.get("reask"):
        return
    first = build_result(document_id, text, prior["raw_first_turn"])
    targets = reask_targets(first)
    still = {drop.get("path") for drop in result.dropped}
    result.raw_first_turn = prior["raw_first_turn"]
    result.reask = {
        **prior["reask"],
        "targets": targets,
        "recovered": [t["path"] for t in targets if t["path"] not in still],
        "unrecovered": [t["path"] for t in targets if t["path"] in still],
    }


def aggregate(records: list[dict]) -> dict:
    """The recording's headline figures over every scored record. Token
    and call totals sum **every** turn through `turn_metrics` (D71, D103);
    everything else sums the per-note `score` blocks."""
    matched = sum(r["score"]["matched_events"] for r in records)
    extracted = sum(r["score"]["extracted_events"] for r in records)
    labeled = sum(r["score"]["labeled_events"] for r in records)
    req9_total = sum(r["score"]["traps_req9"] for r in records)
    req9_taken = sum(len(r["score"]["req9_traps_extracted"]) for r in records)
    spans_emitted = sum(r["score"]["spans_emitted"] for r in records)
    spans_anchored = sum(r["score"]["spans_anchored"] for r in records)
    offsets_usable = sum(r["score"]["model_offsets_usable"] for r in records)
    field_ok = sum(r["score"]["field_agreements"] for r in records)
    field_total = sum(r["score"]["field_total"] for r in records)
    turns = turn_metrics(records)

    return {
        "notes": len(records),
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
        "spans_emitted": spans_emitted,
        "spans_anchored": spans_anchored,
        "spans_normalized": sum(r["score"]["spans_normalized"] for r in records),
        "spans_unescaped": sum(r["score"]["spans_unescaped"] for r in records),
        "spans_multi_occurrence": sum(r["score"]["spans_multi_occurrence"] for r in records),
        "spans_disambiguated": sum(r["score"]["spans_disambiguated"] for r in records),
        "model_offsets_usable": offsets_usable,
        "field_agreement": round(field_ok / field_total, 4) if field_total else None,
        "field_total": field_total,
        **reask_figures(records),
        # Every turn, not just the first (D71, D103).
        "model_calls": len(turns),
        "total_input_tokens": sum(m["input_tokens"] for m in turns),
        "total_output_tokens": sum(m["output_tokens"] for m in turns),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in turns), 1),
    }


def main() -> int:
    rescore = "--rescore" in sys.argv
    tier = _named_tier(sys.argv)
    out_path = out_path_for(tier)
    cases = spike_cases() + synthesized_cases()

    if rescore:
        # Routed by tier: unrouted, `--tier vertex --rescore` would re-anchor
        # and overwrite the AI Studio recording every gate reads (D106).
        if not out_path.exists():
            sys.exit(f"nothing to rescore at {out_path}; run without --rescore first")
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        recorded = {r["note_id"]: r for r in previous["notes"]}
        for case in cases:
            prior = recorded.get(case["note_id"])
            if prior is None or prior.get("raw") is None:
                sys.exit(f"{case['note_id']}: no recorded payload to re-anchor")
            if prior["note_sha256"] != case["sha256"]:
                sys.exit(
                    f"{case['note_id']}: the note changed since it was measured. "
                    "Re-anchoring against it would score a quote against a "
                    "document it never came from (D18)."
                )
        client = None
        print(
            f"re-anchoring {len(cases)} recorded payloads from "
            f"{out_path.name}, no model call"
        )
    else:
        load_env()
        from pa_agent.tiers import client_for

        client = client_for(tier)
        from pa_agent.tiers import tier_of

        print(
            f"{len(cases)} notes, model {PINNED_MODEL}, "
            f"tier {tier_of(client)}, temperature {EXTRACTION_TEMPERATURE}"
        )

    records = []
    for case in cases:
        if rescore:
            prior = recorded[case["note_id"]]
            result = build_result(
                case["document_id"], case["text"], prior["raw"],
                CallMetrics.model_validate(prior["metrics"]) if prior["metrics"] else None,
            )
            _rederive_reask(result, prior, case["document_id"], case["text"])
        else:
            for attempt in range(RETRIES):
                try:
                    result = extract(case["document_id"], case["text"], client)
                except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                    if attempt == RETRIES - 1:
                        raise
                    wait = 2 ** attempt * 5
                    print(f"  {case['note_id']}: {type(exc).__name__}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                reask_error = (result.reask or {}).get("error") or ""
                if reask_error.startswith("CALL_FAILED") and attempt < RETRIES - 1:
                    # A re-ask that failed on transport is not a measurement of
                    # the re-ask; the note is re-run whole (D103). A re-ask the
                    # model answered badly is a finding and stays (D71).
                    wait = 2 ** attempt * 5
                    print(f"  {case['note_id']}: re-ask {reask_error[:60]}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                break

        scored = score(case, result)
        records.append({
            "note_id": case["note_id"],
            "corpus": case["corpus"],
            "document_id": case["document_id"],
            "document": case.get("document"),
            "note_sha256": case["sha256"],
            "cases": case.get("cases", []),
            "labels": {"events": case["events"], "traps": case["traps"],
                       "assertion_required": case["assertion_required"]},
            "events": [
                {"date": e.event_date.isoformat(),
                 "span": e.span.model_dump(mode="json"),
                 "bmi": e.bmi,
                 "bmi_span": e.bmi_span.model_dump(mode="json") if e.bmi_span else None,
                 "diet_documented": e.diet_documented,
                 "diet_span": e.diet_span.model_dump(mode="json") if e.diet_span else None,
                 "activity_documented": e.activity_documented,
                 "activity_span": (
                     e.activity_span.model_dump(mode="json") if e.activity_span else None
                 )}
                for e in result.events
            ],
            "assertions": [
                {"span": a.span.model_dump(mode="json"), "text": a.text}
                for a in result.assertions
            ],
            # T-60: the note-level BMI, which belongs to no encounter (D50).
            "current_bmi": result.current_bmi,
            "current_bmi_span": (
                result.current_bmi_span.model_dump(mode="json")
                if result.current_bmi_span else None
            ),
            "metrics": result.metrics.model_dump(mode="json") if result.metrics else None,
            # Every turn (D71, D103): the re-ask is the second entry when there
            # was one. `RecordedExtractionRunner` replays this whole.
            "trace": result.trace.model_dump(mode="json") if result.trace else None,
            # The payload the result was built from — patched after a re-ask —
            # kept so a future anchoring rule can be replayed without spending
            # the corpus again (D18, D46), and the model's first answer beside
            # it so the re-ask's effect is re-derivable (T-89).
            "raw": result.raw,
            "raw_first_turn": result.raw_first_turn,
            "reask": result.reask,
            "score": scored,
        })
        reask_note = ""
        if result.reask:
            reask_note = (
                f" reask {len(result.reask['recovered'])}/"
                f"{len(result.reask['targets'])} recovered"
                + (f" ({result.reask['error'][:40]})" if result.reask["error"] else "")
            )
        print(
            f"  {case['note_id']:<24} events {scored['matched_events']}/"
            f"{scored['labeled_events']} extracted {scored['extracted_events']} "
            f"traps_taken {len(scored['traps_extracted'])} "
            f"assertions {scored['assertions']} "
            f"spans {scored['spans_anchored']}/{scored['spans_emitted']}{reask_note}"
        )

    aggregate_figures = aggregate(records)

    if rescore:
        # A rescore re-derives figures, never provenance: the recording keeps
        # saying which task measured it.
        provenance = {k: previous.get(k) for k in ("task", "decision", "supersedes")}
        recorded_tier = previous["tier"]
    else:
        from pa_agent.tiers import tier_of

        provenance = PROVENANCE[tier]
        # Read off the client, never the flag: a mis-built client cannot launder
        # itself into the artifact's provenance (D106).
        recorded_tier = tier_of(client)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({
            **provenance,
            "measured_at": (
                previous["measured_at"] if rescore
                else datetime.now(timezone.utc).isoformat()
            ),
            "rescored_at": datetime.now(timezone.utc).isoformat() if rescore else None,
            "model": PINNED_MODEL,
            "tier": recorded_tier,
            "prompt_version": PROMPT_VERSION,
            "reask_rounds": REASK_ROUNDS,
            "temperature": EXTRACTION_TEMPERATURE,
            "schema_note": (
                "Wider than spike 001's: REQ-38 per-field diet/activity spans and "
                "the BMI as a value with its own span. D19's numbers belong to the "
                "narrow schema and are not re-run here (D45)."
            ),
            "aggregate": aggregate_figures,
            "notes": records,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate_figures, indent=2))
    print(f"written: {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
