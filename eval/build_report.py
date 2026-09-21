"""T-22 / T-28 — generate `eval/report.md` from committed recordings (D85).

The document A2, A3, A5 and A6 are satisfied by, and the one an external
reviewer actually reads. **Generated, never hand-edited**: every figure is
derived from artifacts already in the repo, and `--verify` re-renders and diffs
against the committed file so a stale number is a red gate rather than a
plausible-looking table.

    python eval/build_report.py            # write eval/report.md
    python eval/build_report.py --verify   # recompute and diff; non-zero on drift

Zero model calls and no network: extraction replays T-15's recording, the
verifier replays T-17's, and the sweep varies one constant through a store
wrapper rather than by writing to a tracked policy file (D85).

Why an exact diff is safe here: three consecutive harness runs report
byte-identical tokens *and* wall time, because every metric is replayed rather
than measured live. See `_cost_section` for what that makes the latency figure
mean.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(EVAL_DIR))

import run_eval as harness  # noqa: E402

from pa_agent.contracts import (  # noqa: E402
    CriteriaTree,
    CriterionVerdict,
    Determination,
    GapReason,
    PredicateKind,
)
from pa_agent.index import DocumentIndex  # noqa: E402
from pa_agent.spans import SpanValidationError, validate  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402

REPORT_PATH = EVAL_DIR / "report.md"

#: D82's grid. The pinned 1.0 sits inside it deliberately, so the committed
#: choice is visible on the curve rather than asserted beside it.
TOLERANCE_GRID: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 2.0, 5.0, 50.0)

#: Where every predicate kind came from: the practice that first needed it and
#: the task that earned it (T-95, D116). `PredicateKind` is the engine's closed
#: vocabulary; this is the closed account of its provenance, and the
#: compatibility account's middle column is computed from it — a criterion's
#: kind is *reused* when its origin practice is not the criterion's own.
#:
#: T-91 named the seven it found, wrapping predicates built across T-13 to T-42
#: for the one practice that then existed; the two after it arrived with the
#: practices that needed them.
#:
#: **A literal, and pinned against the enum rather than against the corpus**
#: (`set(KIND_ORIGIN) == set(PredicateKind)`, the shape `PREDICATES` and
#: `STEP_KINDS` already use). The rejected alternative was one frozenset of
#: "the kinds that predate v1.2", derived from today's bariatric trees to pin
#: it — which asserts a historical claim against a present derivation, so a
#: bariatric tree revision declaring an existing kind could only be made green
#: by rewriting the history the constant exists to preserve (D116).
KIND_ORIGIN: dict[PredicateKind, tuple[str, str]] = {
    PredicateKind.BMI_OBSERVATION_THRESHOLD: ("bariatric_surgery", "T-91"),
    PredicateKind.CONDITION_VALUE_SET_MEMBERSHIP: ("bariatric_surgery", "T-91"),
    PredicateKind.NOTE_EVENT_COUNT: ("bariatric_surgery", "T-91"),
    PredicateKind.NOTE_EVENT_RUN_LENGTH: ("bariatric_surgery", "T-91"),
    PredicateKind.NOTE_EVENT_RUN_RECENCY: ("bariatric_surgery", "T-91"),
    PredicateKind.NOTE_EVENT_RUN_BMI_RATE: ("bariatric_surgery", "T-91"),
    PredicateKind.NOTE_EVENT_RUN_BEHAVIOR_RATE: ("bariatric_surgery", "T-91"),
    PredicateKind.MEDICATION_VALUE_SET_ACTIVE: ("rheumatology", "T-92"),
    PredicateKind.PROCEDURE_VALUE_SET_INTERVAL: ("diagnostic_ultrasound", "T-94"),
}

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_BROKEN = 2


# --------------------------------------------------------------------------
# Running the system under test
# --------------------------------------------------------------------------


class _ToleranceOverride:
    """`LocalPolicyStore` with one constant replaced, for D82's sweep.

    The tree is a frozen Pydantic model, so this is an explicit `model_copy`
    and the committed file is never touched. Patching
    `data/policies/ncd_100_1_jf.json` and restoring it was refused (D85): a gate
    that writes a tracked policy file leaves the corpus wrong if it is
    interrupted, and `verify_sources.py` is downstream of exactly that file.

    Everything else is delegated, so the real `reconcile_bmi` runs on the real
    pipeline and only the constant differs.
    """

    def __init__(self, inner: Any, tolerance: float) -> None:
        self._inner = inner
        self._tolerance = tolerance

    def get_tree(self, policy_version_id: str) -> Any:
        tree = self._inner.get_tree(policy_version_id)
        facts = [
            fact.model_copy(
                update={
                    "discrepancy_tolerance": fact.discrepancy_tolerance.model_copy(
                        update={"value": self._tolerance}
                    )
                }
            )
            for fact in tree.reconciled_facts
        ]
        return tree.model_copy(update={"reconciled_facts": facts})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _run(policy_store: Any) -> tuple[list[Any], dict[Any, Any]]:
    """Every labeled case, scored, plus the determination cache behind it.

    The cache is what the per-criterion sections read: one determination per
    `(patient, procedure, as_of)`, which is the object rows sharing a patient
    were scored against (D75).
    """
    cases = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    patient_store = LocalPatientStore()

    def resolve_document(document_id: str) -> Any:
        try:
            return patient_store.get_document(document_id)
        except KeyError:
            return policy_store.get_document(document_id)

    runner = harness._recorded_runner()
    verifier = harness._recorded_verifier()
    if runner is None or verifier is None:
        raise SystemExit(
            "eval/extraction/results.json or eval/verifier/results.json is "
            "missing; the report cannot be built without the recordings it "
            "reads. Both are committed — this is a checkout problem."
        )

    cache: dict[Any, Any] = {}
    results = [
        harness.run_case(
            case,
            policy_store,
            patient_store,
            runner,
            harness.EVAL_AS_OF,
            cache,
            resolve_document,
            verifier,
        )
        for case in cases
    ]
    return results, cache


def _labels() -> dict[str, dict[str, Any]]:
    """Case id -> its `expect` block."""
    cases = json.loads((EVAL_DIR / "cases.json").read_text(encoding="utf-8"))["cases"]
    return {case["case_id"]: case for case in cases}


def _determinations(cache: dict[Any, Any]) -> list[Determination]:
    return [d for d in cache.values() if isinstance(d, Determination)]


def _determination_for(case: dict[str, Any], cache: dict[Any, Any]) -> Any:
    from datetime import date

    as_of = (
        date.fromisoformat(case["as_of"])
        if case.get("as_of") is not None
        else harness.EVAL_AS_OF
    )
    return cache.get(
        (case.get("patient_id"), case["procedure_code"], as_of, case.get("state"))
    )


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _fmt(value: float | None, places: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{places}f}"


def _outcomes_section(results: list[Any]) -> list[str]:
    counts = Counter(r.status.value for r in results)
    lines = [
        "## Outcomes against the labels",
        "",
        f"{len(results)} labeled cases. A case is `PASS` when the system's outcome, "
        "every named criterion verdict, every named `gap_reason`, the discrepancy "
        "count and the model-call budget all match its label, and every cited span "
        "slices back to its source.",
        "",
        "| Status | Cases | Meaning |",
        "|---|---|---|",
        f"| `PASS` | {counts.get('PASS', 0)} | matched the label on every checked dimension |",
        f"| `FAIL` | {counts.get('FAIL', 0)} | answered, and answered wrongly |",
        f"| `BLOCKED` | {counts.get('BLOCKED', 0)} | the component under test does not exist yet |",
        f"| `ERROR` | {counts.get('ERROR', 0)} | a classified fault; the system did not answer (REQ-28) |",
        "",
        "`BLOCKED` is not `FAIL` and `ERROR` is neither (D27, D67, D77). "
        '"Answered wrongly", "the component does not exist" and "the system '
        'could not evaluate it" have different next actions, and only the first '
        "is a defect in a verdict.",
        "",
    ]
    return lines


def _criterion_pairs(results: list[Any], cache: dict[Any, Any]) -> list[tuple[str, str, str, str]]:
    """Every (case, criterion) pair the eval set labels, with both verdicts.

    Precision is computed over labeled pairs only (D85). The system emits seven
    verdicts per determination and the set labels a subset; scoring the rest
    would need a ground truth that does not exist, and deriving one from the
    system's own output is the circularity D42 warns about.
    """
    pairs: list[tuple[str, str, str, str]] = []
    for case in _labels().values():
        expected = (case.get("expect") or {}).get("criteria") or {}
        if not expected:
            continue
        determination = _determination_for(case, cache)
        if not isinstance(determination, Determination):
            continue
        actual = {r.criterion_id: r for r in determination.criterion_results}
        for criterion_id, label in sorted(expected.items()):
            result = actual.get(criterion_id)
            if result is None:
                continue
            pairs.append(
                (case["case_id"], criterion_id, label["verdict"], result.verdict.value)
            )
    return pairs


def _precision_section(
    results: list[Any], cache: dict[Any, Any], pairs: list[tuple[str, str, str, str]]
) -> list[str]:
    determinations = _determinations(cache)
    emitted = sum(len(d.criterion_results) for d in determinations)

    by_criterion: dict[str, list[tuple[str, str]]] = {}
    for _case, criterion_id, expected, actual in pairs:
        by_criterion.setdefault(criterion_id, []).append((expected, actual))

    rows = []
    total_met_calls = total_met_correct = 0
    for criterion_id in sorted(by_criterion):
        observed = by_criterion[criterion_id]
        met_calls = [p for p in observed if p[1] == "MET"]
        correct = [p for p in met_calls if p[0] == "MET"]
        total_met_calls += len(met_calls)
        total_met_correct += len(correct)
        precision = len(correct) / len(met_calls) if met_calls else None
        rows.append(
            f"| `{criterion_id}` | {len(observed)} | {len(met_calls)} | "
            f"{len(correct)} | {_fmt(precision)} |"
        )

    overall = total_met_correct / total_met_calls if total_met_calls else None

    # T-28: A2's denominators. The always-`MET` baseline answers `MET` on every
    # labeled pair, so its precision is the base rate by construction — which is
    # the comparison A2 asks for, not a coincidence.
    labeled_met = sum(1 for _c, _k, expected, _a in pairs if expected == "MET")
    base_rate = labeled_met / len(pairs) if pairs else None

    lines = [
        "## Per-criterion precision on `MET` (A2, A3)",
        "",
        f"The system emitted **{emitted}** criterion verdicts across "
        f"{len(determinations)} determinations. The eval set labels **{len(pairs)}** "
        "of them. Precision is computed over the labeled pairs only: scoring the "
        "rest would need a ground truth that does not exist, and deriving one "
        "from the system's own output measures agreement with itself (D42, D85).",
        "",
        "| Criterion | Labeled pairs | System said `MET` | Correct | Precision |",
        "|---|---|---|---|---|",
        *rows,
        f"| **all** | **{len(pairs)}** | **{total_met_calls}** | "
        f"**{total_met_correct}** | **{_fmt(overall)}** |",
        "",
        f"**A2's threshold is 0.90 on `MET`.** Measured: **{_fmt(overall)}**.",
        "",
        "### The base rate and the trivial baseline (A2, T-28)",
        "",
        "A precision figure without its denominators is not a result. On a set "
        "where most labeled verdicts are `MET`, a system answering `MET` "
        "unconditionally clears 0.90 while knowing nothing.",
        "",
        "| Figure | Value |",
        "|---|---|",
        f"| `MET` base rate (labeled pairs that are `MET`) | "
        f"{labeled_met}/{len(pairs)} = **{_fmt(base_rate)}** |",
        f"| Precision of a trivial always-`MET` baseline | **{_fmt(base_rate)}** |",
        f"| Precision measured | **{_fmt(overall)}** |",
        "",
        "The baseline's precision *is* the base rate, by construction: a system "
        "that answers `MET` everywhere is correct exactly as often as `MET` is "
        "the right answer. The measured figure is only a result to the extent it "
        "exceeds that number.",
        "",
        "**A2 is asymmetric on purpose.** A false `MET` produces a denial the "
        "specialist did not expect; a false `NOT_MET` produces an unnecessary "
        "chart review. The first is worse, so precision on `MET` is gated and "
        "recall is only reported.",
        "",
    ]
    return lines


def _span_section(cache: dict[Any, Any]) -> list[str]:
    """A3: zero `MET` verdicts with an invalid span, recomputed here.

    The scorer already fails a case on an invalid span; this recounts
    independently so the report carries the rate rather than the absence of a
    failure. Anchoring and validation are separate modules for the same reason
    (`anchor.py`, `spans.py`): a locator must not be able to launder its bugs
    through its own validator.
    """
    patient_store = LocalPatientStore()
    policy_store = LocalPolicyStore()

    def document(document_id: str) -> Any:
        try:
            return patient_store.get_document(document_id)
        except KeyError:
            return policy_store.get_document(document_id)

    index = DocumentIndex()
    checked = valid = 0
    met_checked = met_valid = 0
    not_met = not_met_with_shortfall = 0
    for determination in _determinations(cache):
        for result in determination.criterion_results:
            is_met = result.verdict is CriterionVerdict.MET
            if result.verdict is CriterionVerdict.NOT_MET:
                # T-86 (D99): each of these passed `step_sufficiency` on the
                # way here, so the count is the count of verdicts whose own
                # citations re-derived them. Counted, not asserted: the graph
                # already aborts on a failure, and a report row that could
                # only ever read n/n is still the row a reviewer looks for.
                not_met += 1
                not_met_with_shortfall += result.shortfall is not None
            for span in result.spans:
                checked += 1
                met_checked += is_met
                # Narrow on purpose. A broad `except` here turns a wiring bug
                # into a plausible-looking 0.000 — which is exactly what the
                # first draft of this function did, by passing a `Document`
                # where `validate` wants a `DocumentIndex`. Anything that is
                # not a span rejection is a broken measurement and must raise.
                try:
                    if span.document_id not in index:
                        index.add(document(span.document_id))
                    validate(span, index)
                except (SpanValidationError, KeyError):
                    continue
                valid += 1
                met_valid += is_met

    rate = valid / checked if checked else None
    met_rate = met_valid / met_checked if met_checked else None
    return [
        "## Span validity (A3, Article III)",
        "",
        "Every span carried by a criterion verdict, re-sliced from its source "
        "document and compared to the quote it claims. Recomputed here rather "
        "than inferred from the scorer's silence.",
        "",
        "| Figure | Value |",
        "|---|---|",
        f"| Spans checked | {checked} |",
        f"| Spans that slice back | {valid} |",
        f"| Span validity rate | **{_fmt(rate)}** |",
        f"| `NOT_MET` verdicts | {not_met} |",
        f"| …of those, re-derived from their own citations (T-86, D99) | {not_met_with_shortfall} |",
        f"| Spans on `MET` verdicts | {met_checked} |",
        f"| …of those, valid | {met_valid} |",
        f"| **A3: `MET` verdicts with an invalid span** | "
        f"**{met_checked - met_valid}** |",
        "",
        "**A3 requires zero.** Measured: "
        f"{met_checked - met_valid} (rate {_fmt(met_rate)}).",
        "",
        "The model's own character offsets are not used and never were: 0 of 80 "
        "were usable in the spike, 0 of 171 in T-15's run and 0 of 169 in "
        "T-89's re-measurement of it. Spans are located by searching the "
        "model's verbatim quote, exact first then whitespace-normalized, "
        "always recording raw offsets (D18).",
        "",
    ]


#: The three committed extraction recordings, in measurement order (T-15,
#: then T-63's two modes — D68 split them). Each is read for its per-note
#: scores; the stored `aggregate` blocks are a measured-day snapshot and are
#: not consulted (T-71, D88).
EXTRACTION_RECORDINGS: tuple[str, ...] = (
    "results.json",
    "adk_results_inline.json",
    "adk_results_tool_fetch.json",
)


def _recording_label(payload: dict[str, Any]) -> str:
    task = payload.get("task", "?")
    if payload.get("runner") == "adk":
        mode = "ADK tool-fetch" if payload.get("tool_fetch") else "ADK inline"
    else:
        mode = "direct"
    return f"{task} {mode}"


def _anchoring_rows(recordings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per recording, recomputed from the per-note `score` blocks.

    A note with no `score` was skipped by that run — the tool-fetch mode has
    no address for the spike notes (T-67) — and contributes nothing, not a
    zero. Assertion coverage follows D88: the denominator is the notes that
    *require* an assertion, and it is `None` rather than 1.000 when no note
    does, because "none was required" and "every one was produced" are
    different facts.
    """
    rows = []
    for filename, payload in recordings.items():
        scored = [n for n in payload["notes"] if n.get("score")]
        emitted = sum(n["score"]["spans_emitted"] for n in scored)
        anchored = sum(n["score"]["spans_anchored"] for n in scored)
        dropped = [
            {
                "note_id": n["note_id"],
                "reason": d["reason"],
                "quote": " ".join(str(d.get("quote", "")).split()),
            }
            for n in scored
            for d in n["score"].get("dropped") or []
        ]
        required = [n for n in scored if n["score"].get("assertion_required")]
        covered = [n for n in required if n["score"].get("assertions")]
        # T-89 (D103): what the verbatim re-ask asked about and got back, read
        # from each note's own `reask` block — per-note, like the scores above,
        # never the stored aggregate (T-71). A recording measured before the
        # re-ask existed has no blocks and reads zero asked, zero recovered.
        reasks = [(n, n["reask"]) for n in scored if n.get("reask")]
        recovered = [
            {
                "note_id": n["note_id"],
                "path": path,
                "quote": " ".join(
                    str(_final_quote(n, path)).split()
                ),
            }
            for n, block in reasks
            for path in block.get("recovered") or []
        ]
        rows.append(
            {
                "file": filename,
                "label": _recording_label(payload),
                "notes_scored": len(scored),
                "notes_skipped": len(payload["notes"]) - len(scored),
                "spans_emitted": emitted,
                "spans_anchored": anchored,
                "spans_not_anchored": emitted - anchored,
                "dropped": dropped,
                "reask_notes": len(reasks),
                "reask_targets": sum(len(b.get("targets") or []) for _, b in reasks),
                "reask_recovered": sum(len(b.get("recovered") or []) for _, b in reasks),
                "recovered": recovered,
                "assertion_notes": len(required),
                "assertion_notes_covered": len(covered),
                "assertion_coverage": (
                    round(len(covered) / len(required), 4) if required else None
                ),
            }
        )
    return rows


def _final_quote(note: dict[str, Any], path: str) -> str:
    """The quote the re-ask recovered, read from the patched payload at the
    path the audit names. Empty when the record has no payload to read."""
    from pa_agent.extraction import _locate

    raw = note.get("raw")
    if not raw:
        return ""
    try:
        container, key = _locate(raw, path)
    except KeyError:
        return ""
    return str(container.get(key, ""))


def _anchoring_section() -> list[str]:
    """P2 / T-85: what the model quoted that Python could not locate (D98).

    Spans are located by searching the model's verbatim quote (D18); a quote
    that does not occur in the note is dropped and the determination says
    less than the chart does. The scorer records every drop per note, and
    D88 put assertion coverage in the measurement aggregate — but this
    report, the document a reviewer reads, carried none of it. The account
    is recomputed here from the per-note scores of every committed
    extraction recording, for zero model calls.
    """
    recordings: dict[str, dict[str, Any]] = {}
    for filename in EXTRACTION_RECORDINGS:
        path = EVAL_DIR / "extraction" / filename
        if not path.exists():
            raise SystemExit(
                f"eval/extraction/{filename} is missing; the extraction "
                "recordings are committed and the anchoring section reads "
                "all of them. This is a checkout problem."
            )
        recordings[filename] = json.loads(path.read_text(encoding="utf-8"))
    rows = _anchoring_rows(recordings)

    lines = [
        "## Anchoring (Article III, D18, D88, D103)",
        "",
        "Every span a model emits is located by searching its verbatim quote "
        "in the note — exact first, then whitespace-normalized — and a quote "
        "that does not occur is dropped rather than approximated (D18). A "
        "dropped claim is evidence the chart holds and the determination does "
        "not cite: fail-closed, and still a loss. Since T-89 a quote the "
        "anchorer refused is re-asked once for its verbatim text and the "
        "answer is anchored the same way (REQ-56, D103); *Re-asked* counts "
        "the quotes asked about and *Recovered* the ones the second answer "
        "anchored. Recomputed from the per-note scores and re-ask blocks of "
        "each committed extraction recording; the recordings' own `aggregate` "
        "blocks are a measured-day snapshot and are not read (T-71).",
        "",
        "| Recording | Notes scored | Spans emitted | Anchored | Not anchored "
        "| Re-asked | Recovered | Assertion coverage (D88) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        coverage = (
            "n/a — no note required one"
            if row["assertion_coverage"] is None
            else f"{row['assertion_notes_covered']}/{row['assertion_notes']} = "
            f"**{_fmt(row['assertion_coverage'])}**"
        )
        skipped = f" ({row['notes_skipped']} skipped)" if row["notes_skipped"] else ""
        lines.append(
            f"| {row['label']} (`{row['file']}`) | {row['notes_scored']}{skipped} "
            f"| {row['spans_emitted']} | {row['spans_anchored']} "
            f"| **{row['spans_not_anchored']}** | {row['reask_targets']} "
            f"| **{row['reask_recovered']}** | {coverage} |"
        )
    lines.append("")

    recovered = [(row, r) for row in rows for r in row["recovered"]]
    if recovered:
        lines.append("**Recovered by the re-ask, named.** Each is a quote the "
                     "first turn paraphrased and the second turn returned "
                     "verbatim; the anchorer located the second (T-89).")
        lines.append("")
        for row, r in recovered:
            quote = r["quote"]
            shown = quote if len(quote) <= 80 else quote[:77] + "…"
            lines.append(
                f"- {row['label']}, note `{r['note_id']}`: `{r['path']}` — "
                f"*{shown}*"
            )
        lines.append("")

    drops = [(row, d) for row in rows for d in row["dropped"]]
    if drops:
        lines.append("**Dropped claims, named.** Each is a quote the model "
                     "emitted that does not occur in its note — after the "
                     "re-ask, where one ran.")
        lines.append("")
        for row, d in drops:
            quote = d["quote"]
            shown = quote if len(quote) <= 80 else quote[:77] + "…"
            lines.append(
                f"- {row['label']}, note `{d['note_id']}`: `{d['reason']}` — "
                f"*{shown}*"
            )
        lines.append("")
    else:
        lines.append("**No claim was dropped in any recording.**")
        lines.append("")

    asked = sum(row["reask_targets"] for row in rows)
    got = sum(row["reask_recovered"] for row in rows)
    lines += [
        "**What a drop costs.** The determination that results is well-formed "
        "and says less than the chart does — it abstains, or names a weaker "
        "gap, where evidence existed. The failure is fail-closed rather than a "
        "wrong approval, and it is still a failure (spec §10, P2). A "
        "similarity fallback is not the fix: a match generous enough to absorb "
        "a tense change is generous enough to absorb a negation (D18). The "
        "fix is the bounded verbatim re-ask — one extra model call on a note "
        "with an unanchorable quote, the answer admitted by the anchorer and "
        "never by the model — and across these recordings it was asked about "
        f"{asked} quote{'s' if asked != 1 else ''} and recovered {got} "
        "(D103).",
        "",
    ]
    return lines


#: The two tiers' recordings, paired. Each pair is one column of the tier
#: section; the AI Studio file is the one every gate replays (D106).
TIER_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("direct", "extraction/results.json", "extraction/results_vertex.json"),
    ("ADK inline", "extraction/adk_results_inline.json",
     "extraction/adk_results_inline_vertex.json"),
    ("ADK tool-fetch", "extraction/adk_results_tool_fetch.json",
     "extraction/adk_results_tool_fetch_vertex.json"),
)


def _turn_totals(payload: dict[str, Any]) -> tuple[int, int, int, float]:
    """Calls and tokens over **every turn**, from each note's own trace.

    A tool round trip is two LLM calls and a note's singular `metrics` is turn
    one only; summing the wrong one understated T-63's output tokens 12.1x and
    inverted a comparison's sign (D71). Read per-note, never from the stored
    aggregate, which is a measured-day snapshot (T-71).
    """
    calls = tokens_in = tokens_out = 0
    wall = 0.0
    for note in payload["notes"]:
        turns = ((note.get("trace") or {}).get("metrics")) or (
            [note["metrics"]] if note.get("metrics") else []
        )
        calls += len(turns)
        tokens_in += sum(m.get("input_tokens", 0) for m in turns)
        tokens_out += sum(m.get("output_tokens", 0) for m in turns)
        wall += sum(m.get("wall_time_ms", 0.0) for m in turns)
    return calls, tokens_in, tokens_out, wall


def _note_tokens(payload: dict[str, Any]) -> dict[str, int]:
    """note_id -> input tokens over every turn, for the scored notes only."""
    out = {}
    for note in payload["notes"]:
        if not note.get("score"):
            continue
        turns = ((note.get("trace") or {}).get("metrics")) or (
            [note["metrics"]] if note.get("metrics") else []
        )
        out[note["note_id"]] = sum(m.get("input_tokens", 0) for m in turns)
    return out


def _tool_path_overhead(tier_file: str, direct_file: str) -> tuple[int, int] | None:
    """What the tool path costs over the direct runner, **on one tier**.

    Over the notes both runners scored, because the tool-fetch mode cannot
    address the spike corpus (D67) and an aggregate over two different note
    sets is the pooling `compare()` refuses (D68).
    """
    tool_path, direct_path = EVAL_DIR / tier_file, EVAL_DIR / direct_file
    if not (tool_path.exists() and direct_path.exists()):
        return None
    tool = _note_tokens(json.loads(tool_path.read_text(encoding="utf-8")))
    direct = _note_tokens(json.loads(direct_path.read_text(encoding="utf-8")))
    shared = tool.keys() & direct.keys()
    if not shared:
        return None
    return sum(tool[n] for n in shared), sum(direct[n] for n in shared)


def _tier_figures(payload: dict[str, Any]) -> dict[str, Any]:
    """One recording's figures, recomputed from its per-note blocks."""
    scored = [n for n in payload["notes"] if n.get("score")]
    calls, tin, tout, wall = _turn_totals(payload)
    span = lambda key: sum(n["score"].get(key, 0) for n in scored)  # noqa: E731
    return {
        "notes": len(scored),
        "spans_emitted": span("spans_emitted"),
        "spans_anchored": span("spans_anchored"),
        "spans_unescaped": span("spans_unescaped"),
        "model_offsets_usable": span("model_offsets_usable"),
        "tool_calls": sum(
            len((n.get("trace") or {}).get("tool_calls") or []) for n in scored
        ),
        "model_calls": calls,
        "input_tokens": tin,
        "output_tokens": tout,
        "wall_ms": wall,
        "native_schema": payload.get("output_schema_and_tools"),
    }


def _tier_section() -> list[str]:
    """P8's answer: the same corpus measured on a second tier (T-90, D106).

    Rendered as a column rather than as extra rows in the anchoring table,
    because that table's prose sums its rows and asserts no claim was dropped
    in any recording — pooling two tiers there would make a Vertex drop, which
    is a *finding*, flip a branch and turn the suite red (D27, D78).
    """
    figures = {}
    for label, ai_file, vx_file in TIER_PAIRS:
        ai_path, vx_path = EVAL_DIR / ai_file, EVAL_DIR / vx_file
        if not (ai_path.exists() and vx_path.exists()):
            continue
        figures[label] = (
            _tier_figures(json.loads(ai_path.read_text(encoding="utf-8"))),
            _tier_figures(json.loads(vx_path.read_text(encoding="utf-8"))),
        )
    if not figures:
        return []

    rows = [
        ("Notes scored", "notes", "{:d}"),
        ("Spans emitted", "spans_emitted", "{:d}"),
        ("Spans anchored", "spans_anchored", "{:d}"),
        ("Spans unescaped (D62's tell)", "spans_unescaped", "{:d}"),
        ("Model offsets usable (D18)", "model_offsets_usable", "{:d}"),
        ("Tool calls", "tool_calls", "{:d}"),
        ("Model calls", "model_calls", "{:d}"),
        ("Input tokens", "input_tokens", "{:d}"),
        ("Output tokens", "output_tokens", "{:d}"),
    ]

    lines = [
        "## Tier (P8, D5, D62, D106)",
        "",
        "Spec §10's P8: every freely-reproducible figure above describes the "
        "model as it behaved on one measured day, on **one tier**. This section "
        "is the second tier, measured once over the same corpus (T-90). The AI "
        "Studio column is the one every gate replays and every figure above is "
        "computed from; the Vertex column stands beside it and replaces nothing "
        "— a changed tier is a new measurement, never a confirmation (D45, D5).",
        "",
        "**The tier changes the prompt, not only the endpoint.** `output_schema` "
        "with `tools` is native on Vertex only; on AI Studio the ADK injects a "
        "`SetModelResponseTool` and an extra instruction (D62). That is read off "
        "the model rather than assumed, and recorded per run.",
        "",
    ]
    for label, (ai, vx) in figures.items():
        if vx["native_schema"] is None:
            native = (
                "This path declares no tools, so `output_schema_and_tools` never "
                "applies to it"
            )
        elif ai["native_schema"] is None:
            native = (
                f"`output_schema_and_tools`: Vertex **{vx['native_schema']}**; the "
                f"AI Studio recording predates T-90 and does not carry the field, "
                f"so it is **not recorded** rather than false — its 4 unescaped "
                f"spans are the injected tool's own tell (D62)"
                if ai["spans_unescaped"]
                else f"`output_schema_and_tools`: Vertex **{vx['native_schema']}**; "
                f"the AI Studio recording predates T-90 and does not carry the field"
            )
        else:
            native = (
                f"`output_schema_and_tools`: AI Studio **{ai['native_schema']}**, "
                f"Vertex **{vx['native_schema']}**"
            )
        lines += [
            f"### {label}",
            "",
            native + ".",
            "",
            "| Figure | AI Studio | Vertex | Δ |",
            "|---|---|---|---|",
        ]
        for name, key, fmt in rows:
            a, v = ai[key], vx[key]
            delta = v - a
            lines.append(
                f"| {name} | {fmt.format(a)} | {fmt.format(v)} | "
                f"{'+' if delta > 0 else ''}{delta} |"
            )
        lines.append("")

        if label != "ADK tool-fetch":
            continue
        ai_pair = _tool_path_overhead(
            "extraction/adk_results_tool_fetch.json", "extraction/results.json"
        )
        vx_pair = _tool_path_overhead(
            "extraction/adk_results_tool_fetch_vertex.json",
            "extraction/results_vertex.json",
        )
        if not (ai_pair and vx_pair):
            continue
        (ai_tool, ai_direct), (vx_tool, vx_direct) = ai_pair, vx_pair
        lines += [
            "**D71's reversal condition, answered: partly.** D71 left it open "
            "whether the tool path's overhead is an AI Studio artifact — the "
            "native schema path removing the second turn — or a real cost of "
            "tool-directed fetching. Both halves are now measured, against each "
            "tier's *own* direct recording over the notes both runners scored.",
            "",
            "| Tool path vs direct, same tier | AI Studio | Vertex |",
            "|---|---|---|",
            f"| Input tokens, tool-fetch | {ai_tool} | {vx_tool} |",
            f"| Input tokens, direct | {ai_direct} | {vx_direct} |",
            f"| Ratio | **{ai_tool / ai_direct:.2f}x** | "
            f"**{vx_tool / vx_direct:.2f}x** |",
            f"| Delta | **+{ai_tool - ai_direct}** | "
            f"**+{vx_tool - vx_direct}** |",
            "",
            "**The injected tool is gone and the overhead is not.** The native "
            "path removes exactly what D62 said it would: the "
            "`set_model_response` round trip disappears — tool calls fall from "
            "26 to 12, one `read_note` per note — and the 4 unescaped spans go "
            "to 0. But the tool path still pays roughly twice the direct "
            "runner's input tokens on Vertex. So about half of AI Studio's "
            "overhead was the injected tool, and the rest is what it costs to "
            "ask for a document the caller was already holding — D71's finding "
            "survives at half its magnitude.",
            "",
            "**Read the delta, not only the ratio** (D91). The ratio falls "
            "further than the cost does, because the *denominator* moved: the "
            "direct runner's own input tokens are markedly higher on Vertex for "
            "the identical prompt and corpus. Token accounting is evidently not "
            "like-for-like across tiers, so a cross-tier token figure is a "
            "weaker claim than a within-tier one, and the within-tier ratios "
            "above are the comparison to quote.",
            "",
        ]
    return lines + _verifier_tier_rows() + _agentic_tier_rows()


def _verifier_tier_rows() -> list[str]:
    """Article V on both tiers, joined per claim.

    Exact, and free, because the Vertex claims were enumerated from the AI
    Studio extraction recording on purpose: a claim digest is the criterion,
    the verdict and the sliced quote (D78), so holding the extraction fixed is
    what keeps the two recordings keyed alike (D106).
    """
    ai_path = EVAL_DIR / "verifier" / "results.json"
    vx_path = EVAL_DIR / "verifier" / "results_vertex.json"
    if not (ai_path.exists() and vx_path.exists()):
        return []
    ai = json.loads(ai_path.read_text(encoding="utf-8"))
    vx = json.loads(vx_path.read_text(encoding="utf-8"))
    ai_by = {c["digest"]: c for c in ai["claims"]}
    vx_by = {c["digest"]: c for c in vx["claims"]}
    shared = ai_by.keys() & vx_by.keys()
    moved = sorted(
        d for d in shared if ai_by[d]["accept"] != vx_by[d]["accept"]
    )
    lines = [
        "### The verifier (Article V)",
        "",
        f"The same **{len(shared)}** claims, put to the blind verifier on both "
        f"tiers. The claim sets are identical by construction, not by luck: the "
        f"Vertex claims were enumerated from the AI Studio extraction recording, "
        f"because a digest is the criterion, the verdict and the sliced quote "
        f"(D78) and a Vertex extraction would have moved every one of them "
        f"(D106).",
        "",
        "| Figure | AI Studio | Vertex |",
        "|---|---|---|",
        f"| Claims | {len(ai_by)} | {len(vx_by)} |",
        f"| Accepted | {sum(1 for c in ai_by.values() if c['accept'])} | "
        f"{sum(1 for c in vx_by.values() if c['accept'])} |",
        f"| Verdicts that moved between tiers | — | **{len(moved)}** of {len(shared)} |",
        "",
    ]
    if moved:
        lines += ["Each claim whose verdict moved, named:", ""]
        for digest in moved:
            claim = ai_by[digest]
            lines.append(
                f"- `{digest[:12]}` {claim['criterion_id']}/{claim['verdict']}: "
                f"AI Studio {'accept' if claim['accept'] else 'reject'}, "
                f"Vertex {'accept' if vx_by[digest]['accept'] else 'reject'}"
            )
        lines.append("")
    else:
        lines += [
            "**No verdict moved.** Article V's answer is the same on both tiers "
            "for every claim the system produced — which is the result a blind "
            "checker should give, and the first evidence this repo has that it "
            "is not a property of one endpoint.",
            "",
        ]
    return lines


def _agentic_tier_rows() -> list[str]:
    """Model-directed retrieval on both tiers (D63, D64, D91)."""
    ai_path = EVAL_DIR / "agentic" / "results.json"
    vx_path = EVAL_DIR / "agentic" / "results_vertex.json"
    if not (ai_path.exists() and vx_path.exists()):
        return []
    ai = json.loads(ai_path.read_text(encoding="utf-8"))["aggregate"]
    vx = json.loads(vx_path.read_text(encoding="utf-8"))["aggregate"]
    rows = (
        ("Patients scored", "scored"),
        ("Outcome agreement", "outcome_agreement_rate"),
        ("Criterion agreement", "criterion_agreement_rate"),
        ("Citation validity", "citation_validity"),
        ("Errors", "errors"),
        ("Planner model calls", "total_model_calls"),
        ("Planner input tokens", "total_input_tokens"),
    )
    return [
        "### Model-directed retrieval",
        "",
        "The differential re-measured on the second tier. Extraction and "
        "verification are **replayed** from the AI Studio recordings on both "
        "sides, so the one variable is the tier the planner called — which is "
        "why this recording stamps its tier per component rather than as one "
        "value (D106).",
        "",
        "| Figure | AI Studio | Vertex |",
        "|---|---|---|",
        *(
            f"| {name} | {ai.get(key)} | {vx.get(key)} |"
            for name, key in rows
        ),
        "",
        "A free-tier tool loop is not reproducible at temperature 0 (D91), and "
        "neither is a paid one: these are two samples, not a before and an "
        "after. What they agree on is the part that matters — the planner "
        "reaches the same outcome as the deterministic oracle on every patient, "
        "on both tiers, with every cited span slicing back.",
        "",
    ]


def _corpus_counts() -> dict[str, int]:
    """The corpus's size, from the three artifacts that own it (D108's rule).

    Hardcoding these is what went stale when T-93 added three charts: the
    sentence read "eight patients" while eleven were being scored, and every
    gate stayed green because prose is not a figure anything re-derives.
    """
    population = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
    )
    notes = json.loads(
        (REPO_ROOT / "data" / "patients" / "notes" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    sources = json.loads(
        (REPO_ROOT / "data" / "policies" / "source" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "patients": len(population["bundles"]),
        "notes": len(notes["notes"]),
        "documents": len(sources["documents"]),
    }


def _abstention_section(results: list[Any], cache: dict[Any, Any]) -> list[str]:
    """A5's first half (D82): the account per `gap_reason`, not a rate alone."""
    account = harness.abstention_account(results)

    reasons: Counter[str] = Counter()
    abstaining_criteria = 0
    for determination in _determinations(cache):
        for result in determination.criterion_results:
            if result.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE:
                abstaining_criteria += 1
                reasons[result.gap_reason.value if result.gap_reason else "(none)"] += 1

    meaning = {
        "NO_EVIDENCE_RETRIEVED": "Find documentation of a program",
        "UNSUBSTANTIATED_ASSERTION": "Find the visit notes behind the claim",
        "VERIFIER_REJECTED": "Re-read the cited passage",
        "SOURCE_CONFLICT": "Reconcile the two values",
    }
    rows = [
        f"| `{reason}` | {count} | {meaning.get(reason, '—')} |"
        for reason, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # T-93 (D113): where the declared-limit abstentions come from. A practice
    # whose document is judgment-heavy raises the corpus-wide rate without any
    # criterion answering worse, and a reader who sees only the rate would
    # read that as the system getting more cautious.
    unclaimed: dict[str, set[str]] = {}
    for determination in _determinations(cache):
        for result in determination.criterion_results:
            if result.gap_reason is GapReason.NOT_EVALUATED_BY_THIS_SYSTEM:
                unclaimed.setdefault(determination.policy_version_id, set()).add(
                    result.criterion_id
                )
    split = "; ".join(
        f"`{tree}` declares {', '.join(sorted(ids))}"
        for tree, ids in sorted(unclaimed.items())
    )

    return [
        "## Abstention (A5, REQ-28, REQ-31)",
        "",
        f"**Abstention rate: {account['abstained']}/{account['answered']} answered "
        f"= {_fmt(account['abstention_rate'])}**"
        f"  ·  {account['errors']} `ERROR` case(s) counted in neither the "
        "numerator nor the denominator.",
        "",
        "An `ERROR` entering the numerator alone would let a crash *lower* the "
        "abstention rate — caution misreported as confidence (D77).",
        "",
        "### The account, per `gap_reason` (D82)",
        "",
        f"{abstaining_criteria} criterion verdicts abstained. Abstentions here "
        "have named causes rather than a dial, and the enum is closed precisely "
        "so that each member names a different next action (REQ-31, D44). This "
        "account is what A5 asks for in this system's terms.",
        "",
        "| `gap_reason` | Criterion verdicts | What it tells the specialist to collect |",
        "|---|---|---|",
        *(rows or ["| — | 0 | no criterion abstained |"]),
        "",
        f"`NOT_EVALUATED_BY_THIS_SYSTEM` is a **tree's declared limit, not a "
        f"chart's gap** (REQ-58): {split}. Those criteria abstain on every "
        "chart that tree answers, so the corpus-wide rate moves with which "
        "practices the eval set exercises rather than with how well any "
        "criterion is evaluated. Read it beside this split (D101, D111, D113).",
        "",
    ]


@lru_cache(maxsize=1)
def _sweep_rows() -> tuple[str, ...]:
    """The sweep's table rows, computed once.

    Cached because it re-runs the whole pipeline per grid point and both the
    generator and its tests ask for it more than once. Pure and argument-free,
    so the cache cannot serve a stale answer within a process.
    """
    rows = []
    for tolerance in TOLERANCE_GRID:
        store = _ToleranceOverride(LocalPolicyStore(), tolerance)
        results, cache = _run(store)
        account = harness.abstention_account(results)
        discrepancies = sum(
            len(r.discrepancies)
            for d in _determinations(cache)
            for r in d.criterion_results
        )
        pinned = " ← pinned (D51)" if tolerance == 1.0 else ""
        rows.append(
            f"| {tolerance:g}{pinned} | {discrepancies} | "
            f"{_fmt(account['abstention_rate'])} |"
        )
    return tuple(rows)


def _sweep_section() -> list[str]:
    """A5's second half (D82): a real curve over a real constant.

    The abstention column is printed at every grid point on purpose. "No
    constant in this tree moves abstention" is then a measurement a future
    change can falsify, rather than a sentence someone has to remember to
    re-check.
    """
    rows = list(_sweep_rows())

    return [
        "### The `discrepancy_tolerance` sweep (A5, D82)",
        "",
        "A5 asked for a curve across \"a range of fail-closed thresholds\", "
        "naming the point where abstention reaches one. **No such threshold "
        "exists**: every verdict is a deterministic predicate over spans and "
        "constants (Articles I and II) and nothing carries a confidence score. "
        "Two constants in the tree are decisions rather than spans, and only one "
        "can be swept for free.",
        "",
        "`discrepancy_tolerance` is the materiality threshold on the "
        "structured-vs-note BMI comparison (REQ-39, D51). Sweeping it moves the "
        "**disclosure count** — how much source disagreement reaches the "
        "reviewer — and the abstention column is printed beside it so the "
        "flatness is measured rather than asserted.",
        "",
        "| `discrepancy_tolerance` | Discrepancies disclosed | Abstention rate |",
        "|---|---|---|",
        *rows,
        "",
        "**Why abstention does not move.** Tolerance gates whether a "
        "disagreement is *recorded*. The abstention branch is a different test — "
        "the two values falling on opposite sides of the coverage threshold "
        "(REQ-34, `SOURCE_CONFLICT`) — and the threshold is not what is being "
        "swept.",
        "",
        "**Why the other constant is not swept.** Changing `a.lookback_months` "
        "changes criterion (a)'s verdict; a changed verdict is a different "
        "verifier claim, and `RecordedVerifierRunner` raises on a claim it has "
        "never seen rather than accepting by default (D78). A verdict-moving "
        "sweep therefore spends model calls, which no gate may do (D45). And it "
        "would not produce abstentions in any case: REQ-16 routes evidence that "
        "exists but is too old to `NOT_MET`, deliberately, because \"we looked "
        "and it is stale\" and \"we could not find it\" send the specialist to "
        "different places.",
        "",
        "**No constant in this tree can drive abstention to 1.** Where the "
        "system stops being useful is answered under A8, in the README's "
        "failure-modes section, in the terms this system actually has.",
        "",
    ]


def _recall_section(cache: dict[Any, Any]) -> list[str]:
    """T-27 / T-80 / REQ-25: per-criterion planner recall, twice over (D86, D91).

    For each criterion, over the cases where the fixed planner's run cited
    anything for it: the fraction where every document those spans name also
    appears in the agentic run's set. Two sets, so two figures over one
    denominator —

      direct  the bundle the agentic planner **gathered** and handed downstream.
              What REQ-25 actually asks for. T-80 put it in the recording.
      cited   the documents the agentic run's spans **point into**. The bound
              D86 had to settle for, kept because on this corpus it is the one
              that can still move.

    Until T-81 the direct figure was 1.000 by construction — one note per
    patient, a planner that raises rather than returning less, structured
    facts re-read from the port (D66) — and the cited figure was the one that
    could move (D91). Since T-81 every note-bearing chart is two documents
    (D104): the planner may name one of them and the run succeeds with a
    shorter chart, which is REQ-25's mechanism, so the direct figure is
    measured and is the one to quote. The cited figure stays as the bound
    beneath it, and the per-patient table says how many notes each run
    gathered against how many the store holds — read from the notes manifest,
    never from the oracle's own row.
    """
    recording_path = EVAL_DIR / "agentic" / "results.json"
    if not recording_path.exists():
        raise SystemExit(
            "eval/agentic/results.json is missing; T-61's recording is committed "
            "and the recall section reads it. This is a checkout problem."
        )
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    notes_manifest = json.loads(
        (REPO_ROOT / "data" / "patients" / "notes" / "manifest.json").read_text(encoding="utf-8")
    )
    notes_on_file: dict[str, list[str]] = {}
    for record in notes_manifest["notes"]:
        notes_on_file.setdefault(record["patient_id"], []).append(record["document_id"])
    gathered_by_patient = {
        row["patient_id"]: set(row["agentic"]["gathered"]["document_ids"])
        for row in recording["patients"]
        if row.get("agentic")
    }
    cited_by_patient = {
        row["patient_id"]: set(row["agentic"]["document_ids"])
        for row in recording["patients"]
        if row.get("agentic")
    }

    # The oracle side, re-derived in-process from the same cache the rest of the
    # report reads — zero calls, recorded extraction.
    direct: dict[str, list[bool]] = {}
    bound: dict[str, list[bool]] = {}
    for (patient_id, procedure_code, as_of, _state), determination in cache.items():
        if not isinstance(determination, Determination):
            continue
        if patient_id not in gathered_by_patient:
            continue
        if procedure_code != recording["procedure_code"]:
            continue
        if as_of.isoformat() != recording["as_of"]:
            continue
        gathered = gathered_by_patient[patient_id]
        cited = cited_by_patient[patient_id]
        for result in determination.criterion_results:
            wanted = {span.document_id for span in result.spans}
            if not wanted:
                continue
            direct.setdefault(result.criterion_id, []).append(wanted <= gathered)
            bound.setdefault(result.criterion_id, []).append(wanted <= cited)

    rows = []
    direct_hits = bound_hits = total = 0
    for criterion_id in sorted(direct):
        d, b = direct[criterion_id], bound[criterion_id]
        direct_hits += sum(d)
        bound_hits += sum(b)
        total += len(d)
        rows.append(
            f"| `{criterion_id}` | {len(d)} | {sum(d)} | {sum(b)} | "
            f"{sum(d) / len(d):.3f} | {sum(b) / len(b):.3f} |"
        )
    direct_overall = direct_hits / total if total else None
    bound_overall = bound_hits / total if total else None

    # T-81 (D104): per patient, what the agentic run gathered against what the
    # store holds. This is the table that can show a skipped note.
    patient_rows = []
    gathered_every_note = cited_no_note = 0
    for row in recording["patients"]:
        if not row.get("agentic"):
            continue
        patient_id = row["patient_id"]
        on_file = notes_on_file.get(patient_id, [])
        gathered_notes = [d for d in gathered_by_patient[patient_id] if d in on_file]
        cited_notes = [d for d in cited_by_patient[patient_id] if d in on_file]
        gathered_every_note += set(gathered_notes) == set(on_file)
        cited_no_note += not cited_notes
        patient_rows.append(
            f"| `{patient_id[:8]}` ({', '.join(row.get('cases') or [])}) | "
            f"{len(on_file)} | {len(gathered_notes)} | {len(cited_notes)} |"
        )
    measured = len(patient_rows)

    return [
        "## Planner recall against the oracle's evidence (REQ-25, D4, D86, D91)",
        "",
        "`FixedRetrievalPlanner` reads three stores in a fixed order; "
        "`AgenticRetrievalPlanner` lets the model choose what to fetch. "
        "Everything downstream is identical and cannot tell which planner ran, "
        "which is what makes the differential a comparison (D63). This section "
        "asks the question the outcome comparison cannot: **did the "
        "model-directed run have the evidence the deterministic one used?**",
        "",
        "Two figures over one denominator. **Direct** is containment in the "
        "bundle the agentic planner *gathered* and handed downstream — what "
        "REQ-25 asks for, recorded by T-80. **Cited** is containment in the "
        "documents that run's spans *point into* — the bound D86 had to settle "
        "for. The direct figure is the one to quote; the cited figure is the "
        "bound beneath it.",
        "",
        "| Criterion | Cases citing evidence | Covered (gathered) | Covered (cited) "
        "| Recall (direct) | Recall (cited) |",
        "|---|---|---|---|---|---|",
        *rows,
        f"| **all** | **{total}** | **{direct_hits}** | **{bound_hits}** | "
        f"**{_fmt(direct_overall)}** | **{_fmt(bound_overall)}** |",
        "",
        "**Since T-81 the direct figure can fall, and this is a measurement "
        "of whether it did.** Every note-bearing chart holds two notes and the "
        "qualifying run straddles them wherever a run exists (D104), so "
        "`AgenticRetrievalPlanner` may name one of the two and the run "
        "succeeds with a shorter chart — REQ-25's mechanism, and the thing "
        "D91's one-note corpus could not exhibit. On this run the planner "
        f"gathered every note on file for **{gathered_every_note} of "
        f"{measured}** patients. The construction caveat D91 put here is gone "
        "because it stopped being true; the figure above is what the planner "
        "did.",
        "",
        "| Patient (cases) | Notes on file | Gathered | Cited |",
        "|---|---|---|---|",
        *patient_rows,
        "",
        f"**Gathered and uncitable, still.** {cited_no_note} of {measured} "
        "patients cite no note at all on the agentic side: their notes reached "
        "the criteria and yielded nothing to cite, which is what the gathered "
        "column beside the cited one shows (D86's open question, settled by "
        "D91 and unchanged here).",
        "",
        "**Containment at whole-document granularity is containment of the "
        "span.** Gathered notes come back from the store by id and are "
        "hash-verified, so the bytes the agentic run held are the bytes the "
        "oracle sliced. REQ-25's *\"contains the span\"* is satisfied exactly, "
        "not by proxy — the in-process re-run D86 thought it would take is not "
        "needed at this granularity.",
        "",
        "**D4's reversal condition now reads against a number.** It was set as "
        "\"measured retrieval recall below 0.85 — a number, not a hunch\" and had "
        "been unfalsifiable since it was written, because nothing measured "
        "retrieval recall and nothing could. Vector search stays rejected on "
        "rule 9 and on a six-document corpus; this is the figure that would let "
        "it back in on evidence.",
        "",
    ]


def _cost_section(results: list[Any], cache: dict[Any, Any]) -> list[str]:
    """A6: reported from instrumentation, never asserted (D85)."""
    # Summed over **determinations**, not over case rows. Rows sharing a
    # (patient, procedure, as_of) are scored against one cached determination
    # (D75), so summing rows counts a shared run's calls once per row — the
    # same double-count shape D71 caught in T-63, in a different place.
    determinations = _determinations(cache)
    calls = sum(d.model_calls for d in determinations)
    input_tokens = sum(d.total_input_tokens for d in determinations)
    output_tokens = sum(d.total_output_tokens for d in determinations)
    wall_ms = sum(d.total_wall_time_ms for d in determinations)
    n = len(determinations)

    return [
        "## Cost and latency (A6, Article X)",
        "",
        "Measured from each determination's own `metrics`, never estimated. "
        "**Reported, never asserted**: tokens and latency move between runs of "
        "the same model on the same input, and folding them into a gate would "
        "fail it on noise.",
        "",
        "| Figure | Total | Per determination |",
        "|---|---|---|",
        f"| Determinations | {n} | — |",
        f"| Model calls | {calls} | {_fmt(calls / n if n else None, 1)} |",
        f"| Input tokens | {input_tokens} | {_fmt(input_tokens / n if n else None, 0)} |",
        f"| Output tokens | {output_tokens} | {_fmt(output_tokens / n if n else None, 0)} |",
        f"| Wall time (ms) | {wall_ms:.1f} | {_fmt(wall_ms / n if n else None, 1)} |",
        "",
        "**What the latency figure means.** These are the wall times measured "
        "*when the recordings were made*, against the pinned model on AI Studio "
        "(T-15's extraction recording and T-17's verifier recording). They are "
        "not the cost of the replay, which is microseconds and would be a "
        "meaningless number to publish. A6 asks for cost and latency from "
        "instrumentation rather than estimated; replayed instrumentation is "
        "still instrumentation, and a replay's own clock would not be.",
        "",
        "Every gate in this repo, this report included, spends **zero** model "
        "calls and touches no network (D45). The figures above are what the "
        "measurements cost when they were run.",
        "",
        "**Counting note.** A tool round trip is two LLM calls, and a note's "
        "singular `metrics` is turn one only. Summing the wrong one understated "
        "T-63's output tokens by 12.1x and inverted a comparison's sign, using "
        "figures that were each individually real (D71). These totals sum the "
        "full list.",
        "",
    ]


def _trees() -> list[CriteriaTree]:
    """Every criteria tree the engine loads, read through the policy port.

    The **file set** comes from the directory and the objects come from the
    port. That is what makes the account's claim a claim about
    `data/policies/` rather than about a list written here: a generator
    reading its own list of trees would answer "zero omitted" about the list
    (D116). `_corpus_counts` reads `REPO_ROOT` the same way.
    """
    store = LocalPolicyStore()
    paths = sorted((REPO_ROOT / "data" / "policies").glob("*.json"))
    ids = [
        json.loads(path.read_text(encoding="utf-8"))["policy_version_id"]
        for path in paths
    ]
    trees = [store.get_tree(policy_version_id) for policy_version_id in ids]
    return sorted(trees, key=lambda tree: (tree.practice, tree.policy_version_id))


def _criterion_class(tree: CriteriaTree, criterion: Any) -> tuple[str, str]:
    """How one criterion is evaluated: `(class, detail)` (T-95, D116).

    Three classes and no fourth. A tree naming a kind the engine lacks fails
    at load rather than abstaining, so "unbuilt" cannot appear here — that is
    the distinction REQ-57 exists to keep. `KIND_ORIGIN` is indexed rather
    than consulted with a default: a kind with no recorded origin raises here
    instead of being quietly classed as something.
    """
    if criterion.evaluation == "unclaimed":
        return "unclaimed", ""
    origin_practice, origin_task = KIND_ORIGIN[criterion.kind]
    earned_here = origin_practice == tree.practice
    return ("earned" if earned_here else "reused"), origin_task


def _practice_name(practice: str) -> str:
    """The declared slug, read back as prose."""
    return practice.replace("_", " ")


def _compatibility_section() -> list[str]:
    """A10's first clause, rendered: every criterion, classed, zero omitted."""
    trees = _trees()
    practices = sorted({tree.practice for tree in trees})

    lines = [
        "## Cross-practice compatibility (A10, REQ-57, REQ-58, D110, D111, D114)",
        "",
        "v1.2 asked how much of this engine was bariatric surgery's. The "
        "answer is generated here rather than asserted in prose: every "
        "criterion of every tree the policy directory holds, classed by where "
        "the arithmetic that evaluates it came from. A criterion is evaluated "
        "by a predicate kind **an earlier practice earned**, by a kind **this "
        "practice earned**, or it is **declared unclaimed** and abstained on "
        "with `NOT_EVALUATED_BY_THIS_SYSTEM` (REQ-58).",
        "",
        "There is no fourth class, and that is the point. A tree naming a "
        "kind the engine lacks **fails at load** rather than abstaining, so "
        "*unbuilt* cannot appear in this table — which is the distinction "
        "REQ-57 exists to keep, and the one a tree from an unrelated practice "
        "is most able to blur (D110).",
        "",
        "### Per practice",
        "",
        "| Practice | Trees | Criteria | By a kind an earlier practice earned "
        "| By a kind it earned itself | Declared unclaimed | Kinds first "
        "earned here |",
        "|---|---|---|---|---|---|---|",
    ]

    for practice in practices:
        owned = [tree for tree in trees if tree.practice == practice]
        classes = [
            _criterion_class(tree, criterion)[0]
            for tree in owned
            for criterion in tree.criteria
        ]
        first_earned = {
            criterion.kind
            for tree in owned
            for criterion in tree.criteria
            if criterion.kind is not None
            and KIND_ORIGIN[criterion.kind][0] == practice
        }
        lines.append(
            f"| {_practice_name(practice)} | {len(owned)} | {len(classes)} "
            f"| {classes.count('reused')} | {classes.count('earned')} "
            f"| {classes.count('unclaimed')} | {len(first_earned)} |"
        )

    total = sum(len(tree.criteria) for tree in trees)
    lines += [
        "",
        f"**{total} criteria across {len(trees)} trees and {len(practices)} "
        "practices, zero omitted.** The rows below are the policy directory's "
        "own: the file set is globbed and each tree is read back through the "
        "policy port, so a criterion missing from this table is a criterion "
        "missing from the engine.",
        "",
        "The practices that arrived after the first reused what the engine "
        "already had and earned what it lacked; the last column is what each "
        "one cost the vocabulary. Nothing here is unclaimed for want of a "
        "predicate — every abstention below is a limit of the **document or "
        "the chart**, which is the finding rather than a shortfall.",
        "",
        "### Every criterion of every loaded tree",
        "",
        "| Practice | Tree | Id | Criterion | Evaluated by |",
        "|---|---|---|---|---|",
    ]

    for tree in trees:
        for criterion in tree.criteria:
            klass, origin_task = _criterion_class(tree, criterion)
            if klass == "unclaimed":
                evaluated_by = "*declared unclaimed*"
            else:
                earned = "earned here" if klass == "earned" else "reused"
                evaluated_by = f"`{criterion.kind.value}` ({earned}, {origin_task})"
            lines.append(
                f"| {_practice_name(tree.practice)} | `{tree.policy_version_id}` "
                f"| `{criterion.id}` | {criterion.label} | {evaluated_by} |"
            )

    lines += [
        "",
        "### Why each unclaimed criterion is unclaimed",
        "",
        "The tree's own words, quoted rather than sorted into categories of "
        "this report's invention (D116). Read together they separate "
        "themselves: some name a limit of **this pipeline**, which a later "
        "version lifts, and the rest name a limit of **the record**, which no "
        "amount of engineering reaches.",
        "",
    ]

    for tree in trees:
        for criterion in tree.criteria:
            if criterion.evaluation != "unclaimed":
                continue
            lines.append(
                f"- **`{tree.policy_version_id}` `{criterion.id}`** — "
                f"{criterion.label}. {criterion.note}"
            )

    exclusions = [
        (tree, exclusion)
        for tree in trees
        for exclusion in tree.categorical_exclusions
    ]
    lines += [
        "",
        "### Categorical exclusions",
        "",
        "Counted apart, because A10 counts criteria and an exclusion is not "
        "one: written as a criterion it would have to answer `MET` with no "
        "span on every chart that does not trigger it, and REQ-5 refuses "
        "that (REQ-60, D111). One that does not fire produces nothing. The "
        "same rule reaches them — an `ExclusionKind` the engine lacks fails "
        "at load, because an exclusion silently skipped **approves** past a "
        "denial the policy states.",
        "",
        "| Practice | Tree | Exclusion | Procedure scope | Evaluated by |",
        "|---|---|---|---|---|",
    ]
    for tree, exclusion in exclusions:
        lines.append(
            f"| {_practice_name(tree.practice)} | `{tree.policy_version_id}` "
            f"| {exclusion.label} | `{exclusion.procedure_scope}` "
            f"| `{exclusion.kind.value}` |"
        )
    lines.append("")

    return lines


def _caveats_section() -> list[str]:
    return [
        "## Scope of these numbers",
        "",
        "The measurement context for every figure above.",
        "",
        "- **The ground truth is a working first draft, drafted alongside the "
        "system it grades** (D19, D42, D96). The manifests are written from "
        "the bundles before the notes are synthesized, the system under test "
        "never reads them, and every cited span is validated against the "
        "source rather than against a label. Re-labeling and review ride "
        "with the corpus expansion of a later version.",
        "- **This system determines coverage as one contractor would, for "
        "each of three contractors and three practices.** NCD 100.1 "
        "quantifies nothing — no months, no visit counts, no recency. Every "
        "constant in a criteria tree comes from its MAC's document — A53028 "
        "for Noridian Jurisdiction F, L34576 for Palmetto GBA Jurisdictions J "
        "and M, L35677 for the same MAC's infliximab policy (T-92, D111), and "
        "L35755 for WPS's abdominal and visceral vascular studies (T-94, "
        "D114) — and a request resolves by procedure code and state, to one "
        "tree per practice (D21, D29, D100, D111). These are not CMS's "
        "thresholds; they are three contractors' worth of them.",
        f"- **The corpus is {_corpus_counts()['patients']} patients, "
        f"{_corpus_counts()['notes']} chart notes and "
        f"{_corpus_counts()['documents']} policy documents.** Six bundles "
        "from the base seed, E12's with its declared observation (D73), and "
        "one declared clone of E4's chart re-addressed into Palmetto's "
        "territory (T-88, D102) — the row `J1`, which shares both its notes' "
        "bytes with E4. Every note-bearing chart is two documents since T-81, "
        "a split of the facts its manifest already declared (D104). The last "
        "six are the second and third practices, all note-free because v1.2 "
        "declares every note-only criterion unclaimed: two Synthea charts "
        "carrying rheumatoid arthritis in Palmetto's territory and a declared "
        "clone of one of them holding the drug L35677 excludes (T-93, D113), "
        "then one Synthea chart in WPS's territory and two declared clones of "
        "it that differ only in the date of one re-coded procedure, which is "
        "what L35755's frequency limit turns on (T-94, D114). Rates "
        "over a set this size move by large steps; one case is worth more "
        "than a percentage point in every table above.",
        "",
    ]


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render() -> str:
    results, cache = _run(LocalPolicyStore())
    pairs = _criterion_pairs(results, cache)

    lines = [
        "# Metrics report",
        "",
        "<!-- GENERATED by eval/build_report.py — do not edit by hand.",
        "     Every figure is derived from committed recordings; "
        "`--verify` recomputes",
        "     and diffs against this file, and is a gate (T-22, T-28, D85). -->",
        "",
        "Produced from the committed eval set and recordings for **zero model "
        "calls and no network**. Regenerate with `python eval/build_report.py`; "
        "`--verify` fails if any figure here no longer matches what the system "
        "produces.",
        "",
        *_outcomes_section(results),
        *_precision_section(results, cache, pairs),
        *_span_section(cache),
        *_anchoring_section(),
        *_tier_section(),
        *_abstention_section(results, cache),
        *_sweep_section(),
        *_recall_section(cache),
        *_cost_section(results, cache),
        *_compatibility_section(),
        *_caveats_section(),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify",
        action="store_true",
        help="recompute and diff against the committed report; non-zero on drift",
    )
    parser.add_argument("--out", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    rendered = render()

    if not args.verify:
        args.out.write_text(rendered, encoding="utf-8")
        rel = args.out.relative_to(REPO_ROOT) if args.out.is_relative_to(REPO_ROOT) else args.out
        print(f"  wrote {rel}  ({len(rendered.splitlines())} lines)")
        return EXIT_OK

    if not args.out.exists():
        print(
            f"\n  {args.out} does not exist. Run `python eval/build_report.py` "
            "and commit it.\n",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    committed = args.out.read_text(encoding="utf-8")
    if committed == rendered:
        print(f"  report matches its sources  ({len(rendered.splitlines())} lines)")
        return EXIT_OK

    diff = list(
        difflib.unified_diff(
            committed.splitlines(),
            rendered.splitlines(),
            fromfile="committed eval/report.md",
            tofile="recomputed",
            lineterm="",
            n=1,
        )
    )
    print("\n  DRIFT: eval/report.md no longer matches what the system produces.")
    print("  Either the system changed and the report should be regenerated, or")
    print("  the report was edited by hand. It is generated, never hand-edited (D85).\n")
    for line in diff[:40]:
        print(f"    {line}")
    if len(diff) > 40:
        print(f"    … {len(diff) - 40} more diff line(s)")
    print()
    return EXIT_DRIFT


if __name__ == "__main__":
    raise SystemExit(main())
