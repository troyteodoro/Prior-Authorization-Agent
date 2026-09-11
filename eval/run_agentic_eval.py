"""T-61 — the differential: model-directed retrieval against the fixed oracle.

    python eval/run_agentic_eval.py            verify the recording. Spends none.
                                               **This is T-61's gate.**
    python eval/run_agentic_eval.py --measure  run both paths. Spends model calls.
    python eval/run_agentic_eval.py --report   print the comparison. Spends none.

### Why the gate verifies rather than measures

T-61's exit was written as a bare `python eval/run_agentic_eval.py` that compares
the two paths — which would spend model calls on every invocation, and **a gate
that costs money is a gate that gets skipped** (Art. VIII, D63).

T-00 already solved this for a task whose deliverable is a measurement: its gate
is `--verify`, and it *"asserts a number was measured, not that it cleared a
bar"*. Same shape here. `--measure` produces `eval/agentic/results.json`; the
default re-checks that artifact and reports what it found, for free and
reproducibly.

So this returns non-zero until a measurement exists. That is the honest state of
a task that has not been run, and it is why T-61 stays in progress until it has.

### What is held constant, and why that is the whole design

**Extraction is the same recorded payload on both sides.** The differential's one
variable is *which evidence reached the criteria*, so holding the model's reading
of that evidence fixed is what makes a disagreement attributable. If both the
retrieval and the extraction varied, a divergence would tell you nothing about
either.

It also means `--measure` spends calls on the retrieval loop only.

### What a disagreement means

Nothing here is scored against ground truth (D63). REQ-50 compares the agentic
path to the deterministic one, and every figure below is label-free:
disagreement, unsupported-outcome rate, citation validity, error rate, tokens,
latency, tool calls, termination reason. So this runs over the six committed
patients and does not wait for T-21's labelled set.

The failure modes it exists to catch all produce well-formed determinations: a
note the planner never opened leaves c3 measuring a shorter run; forgotten
observations make criterion (a) abstain; a document fetched twice costs twice for
one answer. None of those looks like an error, which is why the comparison is the
instrument rather than an assertion.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import CriterionVerdict, Determination  # noqa: E402
from pa_agent.retrieval import FixedRetrievalPlanner, RetrievalError  # noqa: E402
from pa_agent.runners import RecordedExtractionRunner  # noqa: E402
from pa_agent.verifier import RecordedVerifierRunner  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402
from pa_agent.workflow import run_criteria_workflow  # noqa: E402

OUT_DIR = REPO_ROOT / "eval" / "agentic"
OUT_PATH = OUT_DIR / "results.json"
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
VERIFIER_RESULTS = REPO_ROOT / "eval" / "verifier" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

TREE_VERSION = "ncd-100.1-jf-v1"
PROCEDURE = "43775"
#: T-06 pinned the ground truth here and every recency verdict moves with it.
AS_OF = date(2026, 9, 1)

EXIT_OK = 0
EXIT_NO_MEASUREMENT = 1
EXIT_HARNESS_BROKEN = 2


# --------------------------------------------------------------------------
# Scoring — pure, and the half no real run can exercise until --measure
# --------------------------------------------------------------------------


def compare(agentic: dict, oracle: dict) -> dict:
    """One patient's two determinations, differenced.

    Criterion-level *and* overall, because they fail independently: two paths can
    agree on `INSUFFICIENT_EVIDENCE` overall while disagreeing about which
    criterion could not be answered, and the second is the more useful finding —
    it names the evidence that went missing.
    """
    per_criterion = {}
    for criterion_id in sorted(set(agentic) | set(oracle)):
        left, right = agentic.get(criterion_id), oracle.get(criterion_id)
        if left != right:
            per_criterion[criterion_id] = {"agentic": left, "oracle": right}
    return per_criterion


def _verdicts(determination: Determination) -> dict[str, str]:
    return {r.criterion_id: r.verdict.value for r in determination.criterion_results}


def _unsupported(determination: Determination) -> list[str]:
    """Criteria resolved `INSUFFICIENT_EVIDENCE`. REQ-47's rate, per run."""
    return [
        r.criterion_id
        for r in determination.criterion_results
        if r.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
    ]


def _citation_validity(determination: Determination, patient_store) -> tuple[int, int]:
    """(valid, total) spans on the determination, checked through T-11.

    Article III on the assembled artifact. A span that survived construction and
    points at nothing is exactly what the mechanical check exists to catch, and a
    retrieval path that gathered a different document set is where one would
    plausibly arise.
    """
    from pa_agent.index import DocumentIndex
    from pa_agent.spans import validate

    wanted = {
        span.document_id
        for result in determination.criterion_results
        for span in result.spans
    }
    if not wanted:
        # No spans, no store read. A determination with nothing to cite is a
        # short circuit or an all-abstention, and hitting the patient plane to
        # validate zero spans would make this fail on an unknown patient rather
        # than answer the question it was asked.
        return 0, 0

    # One read per cited document. T-64 made `get_document` resolve the whole
    # patient plane, so the note pre-load this used to do — and the "whatever is
    # left must be a bundle" fallback behind it — are the port's job now (D65).
    index = DocumentIndex()
    for document_id in wanted:
        try:
            index.add(patient_store.get_document(document_id))
        except Exception:
            pass

    valid = total = 0
    for result in determination.criterion_results:
        for span in result.spans:
            total += 1
            try:
                if validate(span, index):
                    valid += 1
            except Exception:
                pass
    return valid, total


def self_check() -> list[tuple[str, bool, str]]:
    """Six checks on the scorer, run before anything is reported.

    Every branch below is unreachable by a real run until `--measure` has spent
    calls, so without these the report could be produced by a scorer that never
    worked. `eval/run_eval.py` made the same argument for the same reason (D27).
    """
    checks: list[tuple[str, bool, str]] = []

    def record(label: str, observed: Any, expected: Any) -> None:
        checks.append((label, observed == expected, f"expected {expected}, got {observed}"))

    record(
        "identical verdicts produce no disagreement",
        compare({"a": "MET", "c1": "MET"}, {"a": "MET", "c1": "MET"}),
        {},
    )
    record(
        "a differing verdict is reported per criterion",
        compare({"a": "MET"}, {"a": "NOT_MET"}),
        {"a": {"agentic": "MET", "oracle": "NOT_MET"}},
    )
    record(
        "a criterion missing from one side is a disagreement, not a skip",
        compare({"a": "MET"}, {"a": "MET", "c1": "MET"}),
        {"c1": {"agentic": None, "oracle": "MET"}},
    )
    record(
        "an agreeing overall outcome does not hide a criterion disagreement",
        bool(compare({"a": "NOT_MET", "b": "MET"}, {"a": "MET", "b": "NOT_MET"})),
        True,
    )

    span_free = Determination(
        patient_id="self-check",
        procedure_code="00000",
        policy_version_id="self-check-v0",
        outcome=__import__(
            "pa_agent.contracts", fromlist=["DeterminationOutcome"]
        ).DeterminationOutcome.NOT_COVERED,
    )
    record("a determination with no criteria reports no unsupported", _unsupported(span_free), [])
    record(
        "citation validity over no spans is (0, 0), never a division",
        _citation_validity(span_free, LocalPatientStore()),
        (0, 0),
    )
    return checks


# --------------------------------------------------------------------------
# Measuring — the half that spends model calls
# --------------------------------------------------------------------------


def _patients() -> list[dict]:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return [
        {"patient_id": r["patient_id"], "cases": r["cases"]} for r in manifest["notes"]
    ]


def _load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(
            f"no {ENV_PATH.relative_to(REPO_ROOT)}; --measure needs GOOGLE_API_KEY"
        )
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _recorded_verifier():
    """T-17's recording as a `VerifierRunner` (D77). Held constant on both
    sides of the differential, like the extraction replay: the one variable is
    which evidence reached the criteria."""
    recording = json.loads(VERIFIER_RESULTS.read_text(encoding="utf-8"))
    return RecordedVerifierRunner.from_records(
        recording["claims"], model=recording.get("model")
    )


def _run_one(policy_store, patient_store, runner, planner, patient_id, verifier) -> Determination:
    return run_criteria_workflow(
        policy_store=policy_store,
        patient_store=patient_store,
        extraction_runner=runner,
        policy_ref=policy_store.resolve(PROCEDURE),
        patient_id=patient_id,
        as_of=AS_OF,
        planner=planner,
        verifier=verifier,
    ).determination


def measure(limit: int | None = None) -> int:
    from pa_agent.agent.retrieval_agent import (
        DEFAULT_MAX_LLM_CALLS,
        DEFAULT_MAX_STEPS,
        PROMPT_VERSION,
        AgenticRetrievalPlanner,
    )
    from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL

    _load_env()
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    policy_store, patient_store = LocalPolicyStore(), LocalPatientStore()
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    # Held constant on both sides: the one variable is which evidence reached the
    # criteria, so the model's *reading* of that evidence must not also move.
    runner = RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )
    verifier = _recorded_verifier()

    patients = _patients()[: limit if limit is not None else None]
    print(
        f"{len(patients)} patients · model {PINNED_MODEL} · tier {MEASURED_TIER} · "
        f"max_steps={DEFAULT_MAX_STEPS} max_llm_calls={DEFAULT_MAX_LLM_CALLS}"
    )
    print("extraction is the committed recording on both sides; calls are retrieval only\n")

    records = []
    for index, entry in enumerate(patients, start=1):
        patient_id = entry["patient_id"]
        label = "+".join(entry["cases"]) or patient_id[:8]
        print(f"  [{index}/{len(patients)}] {label}", flush=True)

        oracle = _run_one(
            policy_store, patient_store, runner, FixedRetrievalPlanner(),
            patient_id, verifier,
        )
        planner = AgenticRetrievalPlanner(client=client)

        row: dict[str, Any] = {
            "patient_id": patient_id,
            "cases": entry["cases"],
            "oracle": {
                "outcome": oracle.outcome.value,
                "verdicts": _verdicts(oracle),
                "unsupported": _unsupported(oracle),
                "document_ids": sorted(
                    {s.document_id for r in oracle.criterion_results for s in r.spans}
                ),
                # The oracle's cost, for the comparison that turned out to be the
                # finding. Free to compute — its only model call is the replayed
                # extraction — and recomputable by --rescore without spending.
                "model_calls": oracle.model_calls,
                "input_tokens": oracle.total_input_tokens,
                "output_tokens": oracle.total_output_tokens,
                "wall_time_ms": round(oracle.total_wall_time_ms, 1),
            },
        }
        try:
            agentic = _run_one(
                policy_store, patient_store, runner, planner, patient_id, verifier
            )
        except RetrievalError as exc:
            # REQ-28: a fault is counted separately and never folded into a
            # finding. A run that errored has no verdicts to compare.
            row["agentic"] = None
            row["error"] = f"RetrievalError: {exc}"
            row["disagreement"] = None
            print(f"      ERROR: {str(exc)[:90]}")
            records.append(row)
            continue
        except Exception as exc:  # noqa: BLE001 — recorded with a named class
            row["agentic"] = None
            row["error"] = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            row["disagreement"] = None
            print(f"      ERROR: {row['error'][:90]}")
            records.append(row)
            continue

        valid, total = _citation_validity(agentic, patient_store)
        row["agentic"] = {
            "outcome": agentic.outcome.value,
            "verdicts": _verdicts(agentic),
            "unsupported": _unsupported(agentic),
            "document_ids": sorted(
                {s.document_id for r in agentic.criterion_results for s in r.spans}
            ),
            "spans_valid": valid,
            "spans_total": total,
            "model_calls": agentic.model_calls,
            "input_tokens": agentic.total_input_tokens,
            "output_tokens": agentic.total_output_tokens,
            "wall_time_ms": round(agentic.total_wall_time_ms, 1),
        }
        row["disagreement"] = compare(
            row["agentic"]["verdicts"], row["oracle"]["verdicts"]
        )
        row["outcome_agrees"] = (
            row["agentic"]["outcome"] == row["oracle"]["outcome"]
        )
        print(
            f"      {row['oracle']['outcome']} / {row['agentic']['outcome']}"
            f"  disagreements={len(row['disagreement'])}"
            f"  calls={agentic.model_calls}"
        )
        records.append(row)

    payload = {
        "task": "T-61",
        "decision": "D63",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "model": PINNED_MODEL,
        "tier": MEASURED_TIER,
        "prompt_version": PROMPT_VERSION,
        "procedure_code": PROCEDURE,
        "as_of": AS_OF.isoformat(),
        "bounds": {
            "max_steps": DEFAULT_MAX_STEPS,
            "max_llm_calls": DEFAULT_MAX_LLM_CALLS,
        },
        "note": (
            "Extraction is eval/extraction/results.json on both sides, so the one "
            "variable is which evidence reached the criteria (D63)."
        ),
        "aggregate": aggregate(records),
        "patients": records,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print()
    print(json.dumps(payload["aggregate"], indent=2))
    print(f"\nwrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return EXIT_OK


def aggregate(records: list[dict]) -> dict:
    """The figures T-61's exit names. Faults counted apart from findings (REQ-28)."""
    scored = [r for r in records if r.get("agentic")]
    errored = [r for r in records if r.get("error")]
    if not scored:
        return {"patients": len(records), "scored": 0, "errors": len(errored)}

    criterion_disagreements = sum(len(r["disagreement"]) for r in scored)
    outcome_disagreements = sum(1 for r in scored if not r["outcome_agrees"])
    unsupported = sum(len(r["agentic"]["unsupported"]) for r in scored)
    criteria_total = sum(len(r["agentic"]["verdicts"]) for r in scored)
    spans_valid = sum(r["agentic"]["spans_valid"] for r in scored)
    spans_total = sum(r["agentic"]["spans_total"] for r in scored)

    return {
        "patients": len(records),
        "scored": len(scored),
        "errors": len(errored),
        "error_rate": round(len(errored) / len(records), 4) if records else None,
        "outcome_disagreements": outcome_disagreements,
        "outcome_agreement_rate": round(
            (len(scored) - outcome_disagreements) / len(scored), 4
        ),
        "criterion_disagreements": criterion_disagreements,
        "criteria_compared": criteria_total,
        "criterion_agreement_rate": round(
            (criteria_total - criterion_disagreements) / criteria_total, 4
        )
        if criteria_total
        else None,
        "unsupported_outcomes": unsupported,
        "unsupported_rate": round(unsupported / criteria_total, 4)
        if criteria_total
        else None,
        "spans_valid": spans_valid,
        "spans_total": spans_total,
        "citation_validity": round(spans_valid / spans_total, 4) if spans_total else None,
        "total_model_calls": sum(r["agentic"]["model_calls"] for r in scored),
        "total_input_tokens": sum(r["agentic"]["input_tokens"] for r in scored),
        "total_output_tokens": sum(r["agentic"]["output_tokens"] for r in scored),
        "total_wall_time_ms": round(
            sum(r["agentic"]["wall_time_ms"] for r in scored), 1
        ),
        # What the same answers cost the deterministic path. Reported beside the
        # agentic figures because a comparison of outcomes alone would say the two
        # paths are equivalent, and on cost they are not remotely (Art. X, A6).
        "oracle_model_calls": sum(
            r["oracle"].get("model_calls", 0) for r in scored
        ),
        "oracle_input_tokens": sum(
            r["oracle"].get("input_tokens", 0) for r in scored
        ),
        "oracle_output_tokens": sum(
            r["oracle"].get("output_tokens", 0) for r in scored
        ),
        "input_token_ratio": _ratio(
            sum(r["agentic"]["input_tokens"] for r in scored),
            sum(r["oracle"].get("input_tokens", 0) for r in scored),
        ),
        "model_call_ratio": _ratio(
            sum(r["agentic"]["model_calls"] for r in scored),
            sum(r["oracle"].get("model_calls", 0) for r in scored),
        ),
    }


def _ratio(agentic: int, oracle: int) -> float | None:
    """Agentic cost as a multiple of the oracle's. `None` when nothing to divide."""
    return round(agentic / oracle, 1) if oracle else None


# --------------------------------------------------------------------------
# Verifying — T-61's gate. Spends nothing.
# --------------------------------------------------------------------------


def verify(report_only: bool = False) -> int:
    checks = self_check()
    failed = [(label, detail) for label, ok, detail in checks if not ok]
    if failed:
        print(f"\n  scorer self-check FAILED: {len(failed)} of {len(checks)} checks")
        for label, detail in failed:
            print(f"    - {label}: {detail}")
        print("\n  The instrument is broken. Nothing below it is worth reading.\n")
        return EXIT_HARNESS_BROKEN
    print(f"\n  scorer self-check: {len(checks)}/{len(checks)}")

    if not OUT_PATH.exists():
        print(
            f"\n  no measurement at {OUT_PATH.relative_to(REPO_ROOT)}.\n"
            "  T-61's deliverable is a comparison, and none has been run.\n"
            "  `python eval/run_agentic_eval.py --measure` spends model calls and\n"
            "  writes it. This gate verifies that artifact; it does not create one.\n"
        )
        return EXIT_NO_MEASUREMENT

    payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    report(payload)

    if report_only:
        return EXIT_OK

    # The gate asserts a measurement was taken and is internally coherent — not
    # that it cleared a bar. T-00's rule: a run answering "the planner diverges"
    # is a successful run, and the bar belongs to a kill criterion (D10).
    problems: list[str] = []
    aggregate_ = payload.get("aggregate") or {}
    if not payload.get("patients"):
        problems.append("the recording covers no patients")
    if aggregate_.get("scored", 0) == 0:
        problems.append("no patient produced a comparable determination")
    if payload.get("model") is None:
        problems.append("the recording does not name the model it measured")
    for row in payload.get("patients", []):
        if row.get("agentic") and row["disagreement"] is None:
            problems.append(f"{row['patient_id']}: scored with no differential computed")
        if row.get("agentic") and row["agentic"]["spans_total"]:
            if row["agentic"]["spans_valid"] != row["agentic"]["spans_total"]:
                problems.append(
                    f"{row['patient_id']}: {row['agentic']['spans_total'] - row['agentic']['spans_valid']} "
                    "span(s) did not slice back (Art. III)"
                )

    if problems:
        print("  recording is not usable as a measurement:")
        for problem in problems:
            print(f"    - {problem}")
        print()
        return EXIT_HARNESS_BROKEN

    print("  measurement present, internally coherent, and every span slices back.\n")
    return EXIT_OK


def rescore() -> int:
    """Recompute the oracle half and the aggregate. **Spends nothing.**

    The agentic side is what cost money and is left exactly as measured. The
    oracle side replays the committed extraction recording, so it is free and
    reproducible — which is what lets a figure be added to the artifact without
    re-running the measurement it belongs to. `scripts/run_extraction.py`
    established this shape and D18 established why: re-deriving what is free is
    not the same act as re-measuring what is not.
    """
    if not OUT_PATH.exists():
        print(f"no recording at {OUT_PATH.relative_to(REPO_ROOT)}", file=sys.stderr)
        return EXIT_NO_MEASUREMENT

    payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    policy_store, patient_store = LocalPolicyStore(), LocalPatientStore()
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    runner = RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )
    verifier = _recorded_verifier()

    for row in payload["patients"]:
        oracle = _run_one(
            policy_store, patient_store, runner, FixedRetrievalPlanner(),
            row["patient_id"], verifier,
        )
        row["oracle"].update(
            {
                "model_calls": oracle.model_calls,
                "input_tokens": oracle.total_input_tokens,
                "output_tokens": oracle.total_output_tokens,
                "wall_time_ms": round(oracle.total_wall_time_ms, 1),
            }
        )
        if row.get("agentic"):
            # The differential is recomputed too, so a change to the oracle can
            # never leave a stale comparison sitting beside a fresh cost figure.
            row["disagreement"] = compare(
                row["agentic"]["verdicts"], row["oracle"]["verdicts"]
            )
            row["outcome_agrees"] = (
                row["agentic"]["outcome"] == row["oracle"]["outcome"]
            )

    payload["rescored_at"] = datetime.now(timezone.utc).isoformat()
    payload["aggregate"] = aggregate(payload["patients"])
    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"rescored {len(payload['patients'])} patients, no model call")
    report(payload)
    return EXIT_OK


def report(payload: dict) -> None:
    print(
        f"\n  measured {payload.get('measured_at', '?')} · model "
        f"{payload.get('model')} · tier {payload.get('tier')} · "
        f"prompt {payload.get('prompt_version')}"
    )
    print(f"  {payload.get('note', '')}\n")

    width = max((len("+".join(r["cases"]) or r["patient_id"][:8]) for r in payload["patients"]), default=8)
    print(f"  {'case':<{width}}  {'oracle':<22}  {'agentic':<22}  disagreements")
    for row in payload["patients"]:
        label = "+".join(row["cases"]) or row["patient_id"][:8]
        oracle = row["oracle"]["outcome"]
        if row.get("agentic") is None:
            print(f"  {label:<{width}}  {oracle:<22}  {'ERROR':<22}  {row.get('error','')[:40]}")
            continue
        agentic = row["agentic"]["outcome"]
        diffs = row["disagreement"]
        detail = ", ".join(
            f"{k}: {v['oracle']}->{v['agentic']}" for k, v in sorted(diffs.items())
        ) or "-"
        print(f"  {label:<{width}}  {oracle:<22}  {agentic:<22}  {detail}")

    print()
    for key, value in (payload.get("aggregate") or {}).items():
        print(f"    {key:<26} {value}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python eval/run_agentic_eval.py",
        description="Differential: model-directed retrieval against the fixed oracle.",
    )
    parser.add_argument(
        "--measure", action="store_true", help="run both paths. Spends model calls."
    )
    parser.add_argument(
        "--report", action="store_true", help="print the comparison and stop."
    )
    parser.add_argument(
        "--rescore",
        action="store_true",
        help="recompute the oracle side and the aggregate from the recording. "
        "Spends nothing: the oracle's only model call is a replayed extraction.",
    )
    parser.add_argument("--limit", type=int, default=None, help="first N patients only")
    args = parser.parse_args(argv)

    if args.measure:
        return measure(args.limit)
    if args.rescore:
        return rescore()
    return verify(report_only=args.report)


if __name__ == "__main__":
    raise SystemExit(main())
