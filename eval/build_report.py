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
    CriterionVerdict,
    Determination,
)
from pa_agent.index import DocumentIndex  # noqa: E402
from pa_agent.spans import SpanValidationError, validate  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402

REPORT_PATH = EVAL_DIR / "report.md"

#: D82's grid. The pinned 1.0 sits inside it deliberately, so the committed
#: choice is visible on the curve rather than asserted beside it.
TOLERANCE_GRID: tuple[float, ...] = (0.0, 0.25, 0.5, 1.0, 2.0, 5.0, 50.0)

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
    return cache.get((case.get("patient_id"), case["procedure_code"], as_of))


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
    for determination in _determinations(cache):
        for result in determination.criterion_results:
            is_met = result.verdict is CriterionVerdict.MET
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
        f"| Spans on `MET` verdicts | {met_checked} |",
        f"| …of those, valid | {met_valid} |",
        f"| **A3: `MET` verdicts with an invalid span** | "
        f"**{met_checked - met_valid}** |",
        "",
        "**A3 requires zero.** Measured: "
        f"{met_checked - met_valid} (rate {_fmt(met_rate)}).",
        "",
        "The model's own character offsets are not used and never were: 0 of 80 "
        "were usable in the spike and 0 of 171 in T-15. Spans are located by "
        "searching the model's verbatim quote, exact first then "
        "whitespace-normalized, always recording raw offsets (D18).",
        "",
    ]


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


def _caveats_section() -> list[str]:
    return [
        "## What these numbers do not establish",
        "",
        "Read this before quoting any figure above.",
        "",
        "- **The ground truth was authored by the agent building the system it "
        "grades** (D19, D42). Structural mitigations are in place — the "
        "manifests are written from the bundles before the notes are "
        "synthesized, and the system under test never reads them — and a "
        "perfect score still means only that the approach does not obviously "
        "fail.",
        "- **The pass that would have retired that caveat is deferred, not "
        "done** (D79, D81). T-78 is the owner's adjudication of every label and "
        "manifest; it sits in the board's *Deferred — under review* section, so "
        "every figure here is measured against proposals rather than against "
        "adjudicated ground truth.",
        "- **This system determines coverage as one contractor would.** NCD "
        "100.1 quantifies nothing — no months, no visit counts, no recency. "
        "Every constant in the criteria tree comes from A53028, a Noridian "
        "Jurisdiction F article. A different MAC is a different tree over the "
        "same NCD (D21, D29). These are not CMS's thresholds.",
        "- **The corpus is six patients and three policy documents.** Rates over "
        "a set this size move by large steps; one case is worth more than a "
        "percentage point in every table above.",
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
        *_abstention_section(results, cache),
        *_sweep_section(),
        *_cost_section(results, cache),
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
