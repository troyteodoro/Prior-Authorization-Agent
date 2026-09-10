"""T-15 — run extraction over both note corpora and record the measurement (D45).

    python scripts/run_extraction.py            # spends one model call per note
    python scripts/run_extraction.py --rescore  # re-anchors recorded payloads, no calls

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

from pa_agent.contracts import CallMetrics  # noqa: E402
from pa_agent.extraction import (  # noqa: E402
    EXTRACTION_TEMPERATURE,
    build_result,
    extract,
)
from pa_agent.model_pin import PINNED_MODEL  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402

SPIKE_DIR = REPO_ROOT / "spike" / "spike_001"
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
OUT_DIR = REPO_ROOT / "eval" / "extraction"
OUT_PATH = OUT_DIR / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

RETRIES = 4  # the free tier's 429/503 behavior under load (D5, D20)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_env() -> None:
    """Read the gitignored .env. No credential is ever written to a tracked
    file (working rule 10); this only moves one into the process."""
    if not ENV_PATH.exists():
        sys.exit(f"no {ENV_PATH}; extraction needs an AI Studio key (D5)")
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
        documents = store.get_notes(manifest["patient_id"])
        assert len(documents) == 1, manifest["patient_id"]
        document = documents[0]
        events = [
            {"date": e["date"],
             "bmi_documented": bool(e.get("bmi_documented")),
             "diet_documented": bool(e.get("diet_documented")),
             "activity_documented": bool(e.get("activity_documented"))}
            for program in manifest["wm_programs"]
            for e in program["encounters"]
        ]
        cases.append({
            "note_id": "+".join(manifest["cases"]),
            "corpus": "synthesized",
            "document_id": document.document_id,
            "text": document.text,
            "sha256": document.sha256,
            "cases": manifest["cases"],
            "events": sorted(events, key=lambda e: e["date"]),
            "traps": [{"date": t["date"], "reason": t["type"]} for t in manifest["traps"]],
            "assertion_required": bool(manifest["program_assertions"]),
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


def main() -> int:
    rescore = "--rescore" in sys.argv
    cases = spike_cases() + synthesized_cases()

    if rescore:
        if not OUT_PATH.exists():
            sys.exit("nothing to rescore; run without --rescore first")
        previous = json.loads(OUT_PATH.read_text(encoding="utf-8"))
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
        print(f"re-anchoring {len(cases)} recorded payloads, no model call")
    else:
        load_env()
        from google import genai

        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        print(
            f"{len(cases)} notes, model {PINNED_MODEL}, "
            f"temperature {EXTRACTION_TEMPERATURE}"
        )

    records = []
    for case in cases:
        if rescore:
            prior = recorded[case["note_id"]]
            result = build_result(
                case["document_id"], case["text"], prior["raw"],
                CallMetrics.model_validate(prior["metrics"]) if prior["metrics"] else None,
            )
        else:
            for attempt in range(RETRIES):
                try:
                    result = extract(case["document_id"], case["text"], client)
                    break
                except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                    if attempt == RETRIES - 1:
                        raise
                    wait = 2 ** attempt * 5
                    print(f"  {case['note_id']}: {type(exc).__name__}, retry in {wait}s")
                    time.sleep(wait)

        scored = score(case, result)
        records.append({
            "note_id": case["note_id"],
            "corpus": case["corpus"],
            "document_id": case["document_id"],
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
            # The model's own output, kept so a future anchoring rule can be
            # replayed without spending the corpus again (D18, D46).
            "raw": result.raw,
            "score": scored,
        })
        print(
            f"  {case['note_id']:<24} events {scored['matched_events']}/"
            f"{scored['labeled_events']} extracted {scored['extracted_events']} "
            f"traps_taken {len(scored['traps_extracted'])} "
            f"assertions {scored['assertions']} "
            f"spans {scored['spans_anchored']}/{scored['spans_emitted']}"
        )

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

    aggregate = {
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
        "total_input_tokens": sum(
            r["metrics"]["input_tokens"] for r in records if r["metrics"]
        ),
        "total_output_tokens": sum(
            r["metrics"]["output_tokens"] for r in records if r["metrics"]
        ),
        "total_wall_time_ms": round(
            sum(r["metrics"]["wall_time_ms"] for r in records if r["metrics"]), 1
        ),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({
            "task": "T-15",
            "decision": "D45",
            "measured_at": (
                previous["measured_at"] if rescore
                else datetime.now(timezone.utc).isoformat()
            ),
            "rescored_at": datetime.now(timezone.utc).isoformat() if rescore else None,
            "model": PINNED_MODEL,
            "tier": "ai_studio",
            "temperature": EXTRACTION_TEMPERATURE,
            "schema_note": (
                "Wider than spike 001's: REQ-38 per-field diet/activity spans and "
                "the BMI as a value with its own span. D19's numbers belong to the "
                "narrow schema and are not re-run here (D45)."
            ),
            "aggregate": aggregate,
            "notes": records,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(aggregate, indent=2))
    print(f"written: {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
