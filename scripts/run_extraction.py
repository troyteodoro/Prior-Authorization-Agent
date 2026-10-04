"""T-15 — run extraction over both note corpora and record the measurement (D45).

    python scripts/run_extraction.py            # spends one model call per note, two on a re-ask
    python scripts/run_extraction.py --rescore  # re-anchors recorded payloads, no calls
    python scripts/run_extraction.py --extend   # measures only the notes the recording
                                                # lacks, keeping the rest (T-110, D155)

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

**One recording per fact kind** (T-108, D150). The bare invocation measures
`weight_management` into `results.json`, exactly as before.
`--kind sleep_apnea_workup` measures the sleep charts' notes under the second
kind into `sleep_apnea_workup.json` (`_vertex` beside it), with its own scorer:
the two kinds are different questions, and one aggregate over both would be a
number about neither. A chart is measured under the kind its manifest's facts
describe, so the weight-management corpus skips the sleep charts.
`--kind knee_osteoarthritis_workup` is the third, T-109's, into
`knee_osteoarthritis_workup.json` (D154), with its own corpus and scorer.
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

from pa_agent.contracts import (  # noqa: E402
    CallMetrics,
    ClinicalEvaluation,
    ConservativeTherapy,
    DiseaseActivityAssessment,
    DocumentedFinding,
    DocumentedWeight,
    EvaluationComponent,
    FactKind,
    HeartFailureAssessment,
    KneeRadiograph,
    KneeSymptom,
    RunTrace,
    SleepTest,
    TuberculosisScreen,
    TuberculosisTreatment,
)
from pa_agent.extraction import (  # noqa: E402
    EXTRACTION_TEMPERATURE,
    FACT_SCHEMAS,
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
#: The second kind's recordings, both tiers measured by T-108 (D150).
SLEEP_PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-108", "decision": "D150", "supersedes": None},
    SECOND_TIER: {"task": "T-108", "decision": "D150", "supersedes": None},
}
#: The third kind's recordings, both tiers measured by T-109 (D154).
KNEE_PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-109", "decision": "D154", "supersedes": None},
    SECOND_TIER: {"task": "T-109", "decision": "D154", "supersedes": None},
}
#: The fourth and fifth kinds' recordings, both tiers measured by T-110 (D155).
T110_PROVENANCE: dict[str, dict[str, str | None]] = {
    MEASURED_TIER: {"task": "T-110", "decision": "D155", "supersedes": None},
    SECOND_TIER: {"task": "T-110", "decision": "D155", "supersedes": None},
}
CASES_PATH = REPO_ROOT / "eval" / "cases.json"


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


def out_path_for(tier: str, kind: FactKind = FactKind.WEIGHT_MANAGEMENT) -> Path:
    """One recording per tier, side by side. The AI Studio path is unchanged,
    so every existing invocation and every gate reads the same bytes (D106).
    A kind other than `weight_management` is its own file, named for the kind
    (T-108, D150)."""
    stem = "results" if kind is FactKind.WEIGHT_MANAGEMENT else kind.value
    return OUT_DIR / (f"{stem}.json" if tier == MEASURED_TIER else f"{stem}_{tier}.json")


def _named_kind(argv: list[str]) -> FactKind:
    """`--kind X` or `--kind=X`, defaulting to `weight_management`. An unknown
    kind exits rather than falling back, for `_named_tier`'s reason (D150)."""
    named = None
    for i, arg in enumerate(argv):
        if arg == "--kind" and i + 1 < len(argv):
            named = argv[i + 1]
        elif arg.startswith("--kind="):
            named = arg.split("=", 1)[1]
    if named is None:
        return FactKind.WEIGHT_MANAGEMENT
    try:
        return FactKind(named)
    except ValueError:
        sys.exit(f"unknown fact kind {named!r}; one of {[k.value for k in FactKind]}")

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
        if manifest.get("sleep_tests") or manifest.get("sleep_evaluations"):
            # T-108 (D150): a sleep chart is measured under the sleep kind,
            # into its own recording. Asking it for weight-management
            # encounters would be a measurement of nothing.
            continue
        if manifest.get("knee_visits"):
            # T-109 (D154): a knee chart likewise, under the third kind.
            continue
        if manifest.get("rheumatology_visits"):
            # T-110 (D155): a rheumatology chart likewise, under the fifth.
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


# --------------------------------------------------------------------------
# The sleep apnea workup (T-108, D150): its corpus, scorer and record shape
# --------------------------------------------------------------------------


def sleep_cases() -> list[dict]:
    """The sleep charts' documents, labeled with each document's own facts."""
    store = LocalPatientStore()
    cases = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not (manifest.get("sleep_tests") or manifest.get("sleep_evaluations")):
            continue
        served = {d.document_id: d for d in store.get_notes(manifest["patient_id"])}
        for ordinal, basename in enumerate(manifest["documents"], 1):
            document = served[f"{manifest['patient_id']}/{basename}"]

            def mine(key: str) -> list[dict]:
                return [f for f in manifest.get(key, []) if f["document"] == basename]

            cases.append({
                "note_id": f"{'+'.join(manifest['cases'])}/{ordinal}",
                "corpus": "synthesized",
                "document_id": document.document_id,
                "document": basename,
                "text": document.text,
                "sha256": document.sha256,
                "cases": manifest["cases"],
                "labels": {
                    "evaluations": [e["date"] for e in mine("sleep_evaluations")],
                    "tests": [
                        {"date": t["date"], "index": t["index"],
                         "recording_hours": t["recording_hours"]}
                        for t in mine("sleep_tests")
                    ],
                    "findings": sorted(f["category"] for f in mine("sleep_findings")),
                    "denied_findings": sorted(f["category"] for f in mine("denied_findings")),
                    "traps": [{"date": t["date"], "reason": t["type"]} for t in mine("traps")],
                },
            })
    return cases


def score_sleep(case: dict, result) -> dict:
    """D17's rule, carried to the second kind: a fact matches its label on the
    ISO date, and a value is scored only on a matched fact. A right span with
    a wrong date is the worst failure this system has (D17)."""
    labels = case["labels"]
    evaluations = [f for f in result.facts if isinstance(f, ClinicalEvaluation)]
    tests = [f for f in result.facts if isinstance(f, SleepTest)]
    findings = [f for f in result.facts if isinstance(f, DocumentedFinding)]

    labeled_eval = set(labels["evaluations"])
    got_eval = {e.evaluation_date.isoformat() for e in evaluations}
    labeled_tests = {t["date"]: t for t in labels["tests"]}
    got_tests = {t.test_date.isoformat(): t for t in tests}
    trap_dates = {t["date"] for t in labels["traps"]}
    value_checks = []
    for when, label in labeled_tests.items():
        test = got_tests.get(when)
        if test is None:
            continue
        value_checks.append(("index", label["index"], test.index))
        value_checks.append(("recording_hours", label["recording_hours"], test.recording_hours))
    got_findings = sorted(f.category.value for f in findings)
    anchored = [s for s in result.anchored_spans if s.anchored]
    return {
        "labeled_evaluations": len(labeled_eval),
        "extracted_evaluations": len(got_eval),
        "matched_evaluations": len(labeled_eval & got_eval),
        "labeled_tests": len(labeled_tests),
        "extracted_tests": len(got_tests),
        "matched_tests": len(set(labeled_tests) & set(got_tests)),
        "value_agreements": sum(1 for _, want, got in value_checks if want == got),
        "value_total": len(value_checks),
        "value_disagreements": [
            {"field": name, "expected": want, "got": got}
            for name, want, got in value_checks if want != got
        ],
        "labeled_findings": labels["findings"],
        "extracted_findings": got_findings,
        "findings_matched": sum(
            min(labels["findings"].count(c), got_findings.count(c))
            for c in set(labels["findings"])
        ),
        "denied_findings_extracted": sorted(
            set(labels["denied_findings"]) & set(got_findings)
        ),
        "traps_extracted": sorted(trap_dates & (got_eval | set(got_tests))),
        "spans_emitted": len(result.anchored_spans),
        "spans_anchored": len(anchored),
        "spans_normalized": sum(1 for s in anchored if s.anchor_mode == "normalized"),
        "model_offsets_usable": sum(
            1 for s in result.anchored_spans if s.model_offsets_yield_quote
        ),
        "dropped": result.dropped,
    }


def aggregate_sleep(records: list[dict]) -> dict:
    """The sleep recording's headline figures. Every turn counted (D71)."""
    def total(key: str) -> int:
        return sum(r["score"][key] for r in records)

    turns = turn_metrics(records)
    labeled_findings = sum(len(r["score"]["labeled_findings"]) for r in records)
    extracted_findings = sum(len(r["score"]["extracted_findings"]) for r in records)
    return {
        "notes": len(records),
        "labeled_evaluations": total("labeled_evaluations"),
        "extracted_evaluations": total("extracted_evaluations"),
        "matched_evaluations": total("matched_evaluations"),
        "labeled_tests": total("labeled_tests"),
        "extracted_tests": total("extracted_tests"),
        "matched_tests": total("matched_tests"),
        "value_agreements": total("value_agreements"),
        "value_total": total("value_total"),
        "labeled_findings": labeled_findings,
        "extracted_findings": extracted_findings,
        "findings_matched": total("findings_matched"),
        "denied_findings_extracted": sum(
            len(r["score"]["denied_findings_extracted"]) for r in records
        ),
        "traps": sum(len(r["labels"]["traps"]) for r in records),
        "traps_extracted": sum(len(r["score"]["traps_extracted"]) for r in records),
        "spans_emitted": total("spans_emitted"),
        "spans_anchored": total("spans_anchored"),
        "spans_normalized": total("spans_normalized"),
        "model_offsets_usable": total("model_offsets_usable"),
        **reask_figures(records),
        "model_calls": len(turns),
        "total_input_tokens": sum(m["input_tokens"] for m in turns),
        "total_output_tokens": sum(m["output_tokens"] for m in turns),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in turns), 1),
    }


def _span(span) -> dict | None:
    return span.model_dump(mode="json") if span is not None else None


def sleep_facts(result) -> dict:
    """The result's facts in the recording's shape: what the replay will
    rebuild, written down so a reader of the file sees it without running it."""
    return {
        "evaluations": [
            {"date": f.evaluation_date.isoformat(), "span": _span(f.span)}
            for f in result.facts if isinstance(f, ClinicalEvaluation)
        ],
        "tests": [
            {"date": f.test_date.isoformat(), "span": _span(f.span),
             "index": f.index, "index_span": _span(f.index_span),
             "recording_hours": f.recording_hours, "hours_span": _span(f.hours_span)}
            for f in result.facts if isinstance(f, SleepTest)
        ],
        "findings": [
            {"category": f.category.value, "span": _span(f.span)}
            for f in result.facts if isinstance(f, DocumentedFinding)
        ],
    }


# --------------------------------------------------------------------------
# The knee osteoarthritis workup (T-109, D154): its corpus, scorer and record
# shape, in the sleep kind's shape
# --------------------------------------------------------------------------


def knee_cases() -> list[dict]:
    """The knee charts' documents, labeled with each document's own facts."""
    store = LocalPatientStore()
    cases = []
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not manifest.get("knee_visits"):
            continue
        served = {d.document_id: d for d in store.get_notes(manifest["patient_id"])}
        for ordinal, basename in enumerate(manifest["documents"], 1):
            document = served[f"{manifest['patient_id']}/{basename}"]

            def mine(key: str) -> list[dict]:
                return [f for f in manifest.get(key, []) if f["document"] == basename]

            cases.append({
                "note_id": f"{'+'.join(manifest['cases'])}/{ordinal}",
                "corpus": "synthesized",
                "document_id": document.document_id,
                "document": basename,
                "text": document.text,
                "sha256": document.sha256,
                "cases": manifest["cases"],
                "labels": {
                    "symptoms": sorted(s["category"] for s in mine("knee_symptoms")),
                    "denied_symptoms": sorted(s["category"] for s in mine("denied_symptoms")),
                    "radiographs": [
                        {"date": r["date"], "findings": sorted(r["findings"])}
                        for r in mine("knee_radiographs")
                    ],
                    "therapies": sorted(
                        ({"date": t["date"], "category": t["category"]}
                         for t in mine("conservative_therapies")),
                        key=lambda t: (t["date"], t["category"]),
                    ),
                    "traps": [{"date": t["date"], "reason": t["type"]} for t in mine("traps")],
                },
            })
    return cases


def score_knee(case: dict, result) -> dict:
    """D17's rule on the third kind: a radiograph matches its label on the ISO
    date, and its findings are scored only on a matched radiograph; a therapy
    matches on its (start date, category). A right category on a wrong date is
    a wrong fact (D17)."""
    labels = case["labels"]
    symptoms = sorted(f.category.value for f in result.facts if isinstance(f, KneeSymptom))
    radiographs = {
        f.radiograph_date.isoformat(): f for f in result.facts if isinstance(f, KneeRadiograph)
    }
    therapies = {
        (f.start_date.isoformat(), f.category.value)
        for f in result.facts if isinstance(f, ConservativeTherapy)
    }
    labeled_rad = {r["date"]: r for r in labels["radiographs"]}
    finding_checks = []
    for when, label in labeled_rad.items():
        got = radiographs.get(when)
        if got is None:
            continue
        finding_checks.append(
            (when, label["findings"], sorted(f.category.value for f in got.findings))
        )
    labeled_ther = {(t["date"], t["category"]) for t in labels["therapies"]}
    trap_dates = {t["date"] for t in labels["traps"]}
    anchored = [s for s in result.anchored_spans if s.anchored]
    return {
        "labeled_symptoms": labels["symptoms"],
        "extracted_symptoms": symptoms,
        "symptoms_matched": sum(
            min(labels["symptoms"].count(c), symptoms.count(c)) for c in set(labels["symptoms"])
        ),
        "denied_symptoms_extracted": sorted(set(labels["denied_symptoms"]) & set(symptoms)),
        "labeled_radiographs": len(labeled_rad),
        "extracted_radiographs": len(radiographs),
        "matched_radiographs": len(set(labeled_rad) & set(radiographs)),
        "finding_agreements": sum(1 for _, want, got in finding_checks if want == got),
        "finding_total": len(finding_checks),
        "finding_disagreements": [
            {"date": when, "expected": want, "got": got}
            for when, want, got in finding_checks if want != got
        ],
        "labeled_therapies": len(labeled_ther),
        "extracted_therapies": len(therapies),
        "matched_therapies": len(labeled_ther & therapies),
        "unlabeled_therapies": sorted(list(t) for t in therapies - labeled_ther),
        "traps_extracted": sorted(trap_dates & set(radiographs)),
        "spans_emitted": len(result.anchored_spans),
        "spans_anchored": len(anchored),
        "spans_normalized": sum(1 for s in anchored if s.anchor_mode == "normalized"),
        "model_offsets_usable": sum(
            1 for s in result.anchored_spans if s.model_offsets_yield_quote
        ),
        "dropped": result.dropped,
    }


def aggregate_knee(records: list[dict]) -> dict:
    """The knee recording's headline figures. Every turn counted (D71)."""
    def total(key: str) -> int:
        return sum(r["score"][key] for r in records)

    turns = turn_metrics(records)
    return {
        "notes": len(records),
        "labeled_symptoms": sum(len(r["score"]["labeled_symptoms"]) for r in records),
        "extracted_symptoms": sum(len(r["score"]["extracted_symptoms"]) for r in records),
        "symptoms_matched": total("symptoms_matched"),
        "denied_symptoms_extracted": sum(
            len(r["score"]["denied_symptoms_extracted"]) for r in records
        ),
        "labeled_radiographs": total("labeled_radiographs"),
        "extracted_radiographs": total("extracted_radiographs"),
        "matched_radiographs": total("matched_radiographs"),
        "finding_agreements": total("finding_agreements"),
        "finding_total": total("finding_total"),
        "labeled_therapies": total("labeled_therapies"),
        "extracted_therapies": total("extracted_therapies"),
        "matched_therapies": total("matched_therapies"),
        "traps": sum(len(r["labels"]["traps"]) for r in records),
        "traps_extracted": sum(len(r["score"]["traps_extracted"]) for r in records),
        "spans_emitted": total("spans_emitted"),
        "spans_anchored": total("spans_anchored"),
        "spans_normalized": total("spans_normalized"),
        "model_offsets_usable": total("model_offsets_usable"),
        **reask_figures(records),
        "model_calls": len(turns),
        "total_input_tokens": sum(m["input_tokens"] for m in turns),
        "total_output_tokens": sum(m["output_tokens"] for m in turns),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in turns), 1),
    }


def knee_facts(result) -> dict:
    """The result's facts in the recording's shape, for a reader of the file."""
    return {
        "symptoms": [
            {"category": f.category.value, "span": _span(f.span)}
            for f in result.facts if isinstance(f, KneeSymptom)
        ],
        "radiographs": [
            {"date": f.radiograph_date.isoformat(), "span": _span(f.span),
             "findings": [
                 {"category": g.category.value, "span": _span(g.span)} for g in f.findings
             ]}
            for f in result.facts if isinstance(f, KneeRadiograph)
        ],
        "therapies": [
            {"category": f.category.value, "start_date": f.start_date.isoformat(),
             "span": _span(f.span)}
            for f in result.facts if isinstance(f, ConservativeTherapy)
        ],
    }


def _print_knee(note_id: str, scored: dict) -> None:
    print(
        f"  {note_id:<10} symptoms {scored['extracted_symptoms']} "
        f"(want {scored['labeled_symptoms']}) radiographs "
        f"{scored['matched_radiographs']}/{scored['labeled_radiographs']} "
        f"(got {scored['extracted_radiographs']}) findings "
        f"{scored['finding_agreements']}/{scored['finding_total']} therapies "
        f"{scored['matched_therapies']}/{scored['labeled_therapies']} "
        f"(got {scored['extracted_therapies']}) spans "
        f"{scored['spans_anchored']}/{scored['spans_emitted']}"
    )


def _print_sleep(note_id: str, scored: dict) -> None:
    print(
        f"  {note_id:<10} evaluations {scored['matched_evaluations']}/"
        f"{scored['labeled_evaluations']} (got {scored['extracted_evaluations']}) "
        f"tests {scored['matched_tests']}/{scored['labeled_tests']} "
        f"values {scored['value_agreements']}/{scored['value_total']} "
        f"findings {scored['extracted_findings']} "
        f"spans {scored['spans_anchored']}/{scored['spans_emitted']}"
    )


# --------------------------------------------------------------------------
# The bariatric surgical workup and the rheumatoid arthritis workup (T-110,
# D155): one corpus rule, one scorer and one record shape for both, because
# every item either kind extracts is a flat, dated, labelled fact.
# --------------------------------------------------------------------------


def _charts_declaring(kind: FactKind) -> list[str]:
    """The note-bearing charts whose eval rows resolve to a tree declaring
    `kind`, in manifest order. Read from the system rather than from a
    manifest field, so a chart is measured under exactly the kinds a request
    on it reads (REQ-78): `J1`'s notes are `E4`'s bytes, and they are read
    under Palmetto's second kind because `J1` resolves there."""
    from pa_agent.stores.policy import LocalPolicyStore

    store, policies = LocalPatientStore(), LocalPolicyStore()
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    wanted: list[str] = []
    for case in cases:
        patient_id = case.get("patient_id")
        if not patient_id or patient_id in wanted:
            continue
        state = case.get("state") or store.get_jurisdiction_state(patient_id)
        ref = policies.resolve(case["procedure_code"], state)
        if ref is None:
            continue
        tree = policies.get_tree(ref.policy_version_id)
        if kind in tree.fact_kinds and store.get_notes(patient_id):
            wanted.append(patient_id)
    return wanted


def _flat_labels(kind: FactKind, manifest: dict, basename: str) -> dict:
    """One document's labelled facts under `kind`, from its fact manifest."""
    def mine(key: str) -> list[dict]:
        return [f for f in manifest.get(key, []) if f["document"] == basename]

    if kind is FactKind.BARIATRIC_SURGICAL_WORKUP:
        weights = [
            {"date": e["date"]}
            for program in manifest["wm_programs"]
            for e in program["encounters"]
            if e["document"] == basename and e.get("weight_documented")
        ]
        return {
            "weights": sorted(weights, key=lambda w: w["date"]),
            "evaluations": sorted(
                ({"date": e["date"], "label": e["component"]}
                 for e in mine("multidisciplinary_evaluations")),
                key=lambda e: (e["date"], e["label"]),
            ),
            "traps": [{"date": t["date"], "reason": t["type"]} for t in mine("traps")],
        }
    return {
        "heart_failure_assessments": [
            {"date": f["date"], "label": f["status"]} for f in mine("heart_failure_assessments")
        ],
        "tuberculosis_screens": [
            {"date": f["date"], "label": f["result"]} for f in mine("tuberculosis_screens")
        ],
        "tuberculosis_treatments": [{"date": f["date"]} for f in mine("tuberculosis_treatments")],
        "disease_activity_assessments": [
            {"date": f["date"], "label": f["level"]} for f in mine("disease_activity_assessments")
        ],
        "traps": [{"date": t["date"], "reason": t["type"]} for t in mine("traps")],
    }


def flat_cases(kind: FactKind) -> list[dict]:
    """The documents of every chart `_charts_declaring(kind)` names, labelled
    with each document's own facts under that kind."""
    store = LocalPatientStore()
    manifests = {
        m["patient_id"]: m
        for m in (
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(MANIFEST_DIR.glob("*.json"))
        )
    }
    cases = []
    for patient_id in _charts_declaring(kind):
        manifest = manifests[patient_id]
        served = {d.document_id: d for d in store.get_notes(patient_id)}
        for ordinal, basename in enumerate(manifest["documents"], 1):
            document = served[f"{patient_id}/{basename}"]
            cases.append({
                "note_id": f"{'+'.join(manifest['cases'])}/{ordinal}",
                "corpus": "synthesized",
                "document_id": document.document_id,
                "document": basename,
                "text": document.text,
                "sha256": document.sha256,
                "cases": manifest["cases"],
                "labels": _flat_labels(kind, manifest, basename),
            })
    return cases


#: kind -> collection -> (contract type, date attribute, label attribute).
FLAT_COLLECTIONS: dict[FactKind, dict[str, tuple[type, str, str | None]]] = {
    FactKind.BARIATRIC_SURGICAL_WORKUP: {
        "weights": (DocumentedWeight, "weight_date", None),
        "evaluations": (EvaluationComponent, "evaluation_date", "category"),
    },
    FactKind.RHEUMATOID_ARTHRITIS_WORKUP: {
        "heart_failure_assessments": (HeartFailureAssessment, "assessment_date", "status"),
        "tuberculosis_screens": (TuberculosisScreen, "screen_date", "result"),
        "tuberculosis_treatments": (TuberculosisTreatment, "start_date", None),
        "disease_activity_assessments": (DiseaseActivityAssessment, "assessment_date", "level"),
    },
}


#: trap type -> the one collection it imitates (T-110, D155).
TRAP_COLLECTION: dict[str, str] = {
    "tuberculosis_treatment_deferred": "tuberculosis_treatments",
    "disease_activity_not_scored": "disease_activity_assessments",
}


def _flat_items(kind: FactKind, result) -> dict[str, list[tuple[str, str | None]]]:
    out: dict[str, list[tuple[str, str | None]]] = {}
    for collection, (fact_type, when, label) in FLAT_COLLECTIONS[kind].items():
        out[collection] = sorted(
            (
                getattr(f, when).isoformat(),
                getattr(f, label).value if label else None,
            )
            for f in result.facts if isinstance(f, fact_type)
        )
    return out


def score_flat(kind: FactKind, case: dict, result) -> dict:
    """D17's rule on flat dated facts: an item matches its label on the ISO
    date **and** the label, so a right category on a wrong date, or a right
    date read as the wrong class, is a wrong fact."""
    labels = case["labels"]
    got = _flat_items(kind, result)
    scored: dict = {}
    for collection in FLAT_COLLECTIONS[kind]:
        want = sorted((item["date"], item.get("label")) for item in labels[collection])
        have = got[collection]
        scored[collection] = {
            "labeled": len(want),
            "extracted": len(have),
            "matched": sum(min(want.count(i), have.count(i)) for i in set(want)),
            "unlabeled": [list(i) for i in have if i not in want],
            "missed": [list(i) for i in want if i not in have],
        }
    anchored = [s for s in result.anchored_spans if s.anchored]
    taken = []
    for trap in labels["traps"]:
        # A trap is read as a fact when the collection it imitates holds an
        # item on its date: a deferred treatment as a treatment, an unscored
        # examination as a stated level. A program-series trap (a missed
        # visit) imitates nothing in particular, so every collection is read.
        named = TRAP_COLLECTION.get(trap["reason"])
        collections = [named] if named else list(got)
        if any(d == trap["date"] for c in collections for d, _ in got[c]):
            taken.append(trap["date"])
    scored.update({
        "traps_extracted": sorted(set(taken)),
        "spans_emitted": len(result.anchored_spans),
        "spans_anchored": len(anchored),
        "spans_normalized": sum(1 for s in anchored if s.anchor_mode == "normalized"),
        "model_offsets_usable": sum(
            1 for s in result.anchored_spans if s.model_offsets_yield_quote
        ),
        "dropped": result.dropped,
    })
    return scored


def aggregate_flat(kind: FactKind, records: list[dict]) -> dict:
    """A flat kind's headline figures. Every turn counted (D71)."""
    turns = turn_metrics(records)
    figures: dict = {"notes": len(records)}
    for collection in FLAT_COLLECTIONS[kind]:
        for key in ("labeled", "extracted", "matched"):
            figures[f"{collection}_{key}"] = sum(
                r["score"][collection][key] for r in records
            )
    figures.update({
        "traps": sum(len(r["labels"]["traps"]) for r in records),
        "traps_extracted": sum(len(r["score"]["traps_extracted"]) for r in records),
        "spans_emitted": sum(r["score"]["spans_emitted"] for r in records),
        "spans_anchored": sum(r["score"]["spans_anchored"] for r in records),
        "spans_normalized": sum(r["score"]["spans_normalized"] for r in records),
        "model_offsets_usable": sum(r["score"]["model_offsets_usable"] for r in records),
        **reask_figures(records),
        "model_calls": len(turns),
        "total_input_tokens": sum(m["input_tokens"] for m in turns),
        "total_output_tokens": sum(m["output_tokens"] for m in turns),
        "total_wall_time_ms": round(sum(m["wall_time_ms"] for m in turns), 1),
    })
    return figures


def flat_facts(kind: FactKind, result) -> dict:
    """The result's facts in the recording's shape, for a reader of the file."""
    out: dict = {}
    for collection, (fact_type, when, label) in FLAT_COLLECTIONS[kind].items():
        out[collection] = [
            {"date": getattr(f, when).isoformat(),
             **({"label": getattr(f, label).value} if label else {}),
             "span": _span(f.span)}
            for f in result.facts if isinstance(f, fact_type)
        ]
    return out


def _print_flat(note_id: str, scored: dict) -> None:
    parts = [
        f"{name} {v['matched']}/{v['labeled']} (got {v['extracted']})"
        for name, v in scored.items() if isinstance(v, dict) and "matched" in v
    ]
    print(
        f"  {note_id:<12} " + " ".join(parts)
        + f" spans {scored['spans_anchored']}/{scored['spans_emitted']}"
    )


#: kind -> (corpus, scorer, facts, aggregate, printer, provenance, schema note).
#: One entry per kind measured on its own corpus (T-108, T-109).
KIND_MEASUREMENTS = {
    FactKind.SLEEP_APNEA_WORKUP: (
        lambda: sleep_cases(), lambda c, r: score_sleep(c, r), lambda r: sleep_facts(r),
        lambda rs: aggregate_sleep(rs), _print_sleep, SLEEP_PROVENANCE,
        "The second fact kind's recording: in-person evaluations, "
        "diagnostic sleep tests with their index and recording time, and "
        "documented findings. Its own file and its own scorer, because "
        "an aggregate over two kinds is a number about neither (D150).",
    ),
    FactKind.KNEE_OSTEOARTHRITIS_WORKUP: (
        lambda: knee_cases(), lambda c, r: score_knee(c, r), lambda r: knee_facts(r),
        lambda rs: aggregate_knee(rs), _print_knee, KNEE_PROVENANCE,
        "The third fact kind's recording: documented knee symptoms, knee "
        "radiographs with their findings, and conservative therapies with "
        "their start dates. Its own file and its own scorer, for the second "
        "kind's reason (D150, D154).",
    ),
    FactKind.BARIATRIC_SURGICAL_WORKUP: (
        lambda: flat_cases(FactKind.BARIATRIC_SURGICAL_WORKUP),
        lambda c, r: score_flat(FactKind.BARIATRIC_SURGICAL_WORKUP, c, r),
        lambda r: flat_facts(FactKind.BARIATRIC_SURGICAL_WORKUP, r),
        lambda rs: aggregate_flat(FactKind.BARIATRIC_SURGICAL_WORKUP, rs),
        _print_flat, T110_PROVENANCE,
        "The fourth fact kind's recording: documented weights with their "
        "dates, and the multidisciplinary evaluation's components with theirs. "
        "Read on every chart whose request resolves to a tree declaring it -- "
        "Palmetto's bariatric tree -- including J1's notes, which are E4's "
        "bytes (D155).",
    ),
    FactKind.RHEUMATOID_ARTHRITIS_WORKUP: (
        lambda: flat_cases(FactKind.RHEUMATOID_ARTHRITIS_WORKUP),
        lambda c, r: score_flat(FactKind.RHEUMATOID_ARTHRITIS_WORKUP, c, r),
        lambda r: flat_facts(FactKind.RHEUMATOID_ARTHRITIS_WORKUP, r),
        lambda rs: aggregate_flat(FactKind.RHEUMATOID_ARTHRITIS_WORKUP, rs),
        _print_flat, T110_PROVENANCE,
        "The fifth fact kind's recording: heart-failure assessments, "
        "tuberculosis screens and treatments, and disease-activity levels "
        "stated in words, each dated and labelled from a closed list (D155).",
    ),
}


def main() -> int:
    if _named_kind(sys.argv) is not FactKind.WEIGHT_MANAGEMENT:
        return main_kind(_named_kind(sys.argv))
    rescore = "--rescore" in sys.argv
    extend = "--extend" in sys.argv
    if rescore and extend:
        sys.exit("--rescore and --extend are two different runs; name one")
    tier = _named_tier(sys.argv)
    out_path = out_path_for(tier)
    cases = spike_cases() + synthesized_cases()
    kept: list[dict] = []

    if extend:
        # T-110 (D155): measure only the notes the recording lacks, under the
        # configuration it was measured with, and keep every recorded note's
        # record byte for byte -- D152's and D154's extension, on the first
        # kind. A changed prompt, model or tier is a new measurement (D45),
        # so it refuses rather than mixing two configurations in one file.
        if not out_path.exists():
            sys.exit(f"nothing to extend at {out_path}; run a full measurement first")
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        if previous.get("prompt_version") != PROMPT_VERSION:
            sys.exit(
                f"{out_path.name} was measured under {previous.get('prompt_version')!r}; "
                f"the code is {PROMPT_VERSION!r}. That is a new measurement (D45), not an extension"
            )
        if previous.get("model") != PINNED_MODEL:
            sys.exit(f"{out_path.name} was measured on {previous.get('model')!r}; not an extension")
        recorded = {r["note_id"]: r for r in previous["notes"]}
        by_id = {c["note_id"]: c for c in cases}
        for note_id, record in recorded.items():
            case = by_id.get(note_id)
            if case is None or case["sha256"] != record["note_sha256"]:
                sys.exit(
                    f"{note_id}: recorded, and no longer in the corpus as measured. "
                    "An extension adds notes; it never re-measures or drops one (D45)"
                )
        kept = list(previous["notes"])
        cases = [c for c in cases if c["note_id"] not in recorded]
        if not cases:
            sys.exit("every note in the corpus is already recorded; nothing to extend")

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

        if extend and previous.get("tier") != tier_of(client):
            sys.exit(f"{out_path.name} was measured on {previous.get('tier')!r}; not an extension")
        print(
            f"{len(cases)} notes, model {PINNED_MODEL}, "
            f"tier {tier_of(client)}, temperature {EXTRACTION_TEMPERATURE}"
            + (f", extending {out_path.name}" if extend else "")
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
                    result = extract(
                        case["document_id"], case["text"], client,
                        kind=FactKind.WEIGHT_MANAGEMENT,
                    )
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

    measured_now = [r["note_id"] for r in records]
    records = kept + records
    aggregate_figures = aggregate(records)

    extensions = list(previous.get("extensions", [])) if (rescore or extend) else []
    if extend:
        extensions.append({
            "task": "T-110",
            "decision": "D155",
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "notes": measured_now,
        })
    if rescore or extend:
        # A rescore re-derives figures, never provenance: the recording keeps
        # saying which task measured it. An extension says which task added
        # which notes, beside it.
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
                previous["measured_at"] if (rescore or extend)
                else datetime.now(timezone.utc).isoformat()
            ),
            "rescored_at": datetime.now(timezone.utc).isoformat() if rescore else None,
            **({"extensions": extensions} if extensions else {}),
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


def main_kind(kind: FactKind) -> int:
    """A kind other than `weight_management`: its corpus, its scorer, its own
    recording per tier. The two-turn walk and the anchoring are the same core
    every kind passes through (REQ-79); only the questions differ (D150)."""
    if kind not in KIND_MEASUREMENTS:
        sys.exit(f"no measurement corpus is declared for {kind.value!r}")
    corpus, scorer, facts_of, aggregate_of, printer, kind_provenance, schema_note = (
        KIND_MEASUREMENTS[kind]
    )
    schema = FACT_SCHEMAS[kind]
    rescore = "--rescore" in sys.argv
    tier = _named_tier(sys.argv)
    out_path = out_path_for(tier, kind)
    cases = corpus()

    if rescore:
        if not out_path.exists():
            sys.exit(f"nothing to rescore at {out_path}; run without --rescore first")
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        recorded = {r["note_id"]: r for r in previous["notes"]}
        for case in cases:
            prior = recorded.get(case["note_id"])
            if prior is None or prior.get("raw") is None:
                sys.exit(f"{case['note_id']}: no recorded payload to re-anchor")
            if prior["note_sha256"] != case["sha256"]:
                sys.exit(f"{case['note_id']}: the note changed since it was measured (D18)")
        client = None
        print(f"re-anchoring {len(cases)} recorded payloads from {out_path.name}, no model call")
    else:
        load_env()
        from pa_agent.tiers import client_for, tier_of

        client = client_for(tier)
        print(
            f"{len(cases)} notes, kind {kind.value}, model {PINNED_MODEL}, "
            f"tier {tier_of(client)}, temperature {EXTRACTION_TEMPERATURE}"
        )

    records = []
    for case in cases:
        if rescore:
            prior = recorded[case["note_id"]]
            result = schema.build(
                case["document_id"], case["text"], prior["raw"],
                CallMetrics.model_validate(prior["metrics"]) if prior["metrics"] else None,
            )
            result.trace = RunTrace.model_validate(prior["trace"]) if prior.get("trace") else None
            if prior.get("reask"):
                first = schema.build(case["document_id"], case["text"], prior["raw_first_turn"])
                targets = reask_targets(first, schema.locate)
                still = {drop.get("path") for drop in result.dropped}
                result.raw_first_turn = prior["raw_first_turn"]
                result.reask = {
                    **prior["reask"],
                    "targets": targets,
                    "recovered": [t["path"] for t in targets if t["path"] not in still],
                    "unrecovered": [t["path"] for t in targets if t["path"] in still],
                }
        else:
            for attempt in range(RETRIES):
                try:
                    result = extract(case["document_id"], case["text"], client, kind=kind)
                except Exception as exc:  # noqa: BLE001 — retried, then re-raised
                    if attempt == RETRIES - 1:
                        raise
                    wait = 2 ** attempt * 5
                    print(f"  {case['note_id']}: {type(exc).__name__}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                reask_error = (result.reask or {}).get("error") or ""
                if reask_error.startswith("CALL_FAILED") and attempt < RETRIES - 1:
                    wait = 2 ** attempt * 5
                    print(f"  {case['note_id']}: re-ask {reask_error[:60]}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                break

        scored = scorer(case, result)
        records.append({
            "note_id": case["note_id"],
            "corpus": case["corpus"],
            "document_id": case["document_id"],
            "document": case["document"],
            "note_sha256": case["sha256"],
            "cases": case["cases"],
            "kind": kind.value,
            "labels": case["labels"],
            "facts": facts_of(result),
            "metrics": result.metrics.model_dump(mode="json") if result.metrics else None,
            "trace": result.trace.model_dump(mode="json") if result.trace else None,
            "raw": result.raw,
            "raw_first_turn": result.raw_first_turn,
            "reask": result.reask,
            "score": scored,
        })
        printer(case["note_id"], scored)

    figures = aggregate_of(records)
    if rescore:
        provenance = {k: previous.get(k) for k in ("task", "decision", "supersedes")}
        recorded_tier = previous["tier"]
    else:
        from pa_agent.tiers import tier_of

        provenance = kind_provenance[tier]
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
            "kind": kind.value,
            "prompt_version": schema.prompt_version,
            "reask_rounds": REASK_ROUNDS,
            "temperature": EXTRACTION_TEMPERATURE,
            "schema_note": schema_note,
            "aggregate": figures,
            "notes": records,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(figures, indent=2))
    print(f"written: {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
