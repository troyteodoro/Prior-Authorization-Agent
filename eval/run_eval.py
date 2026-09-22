#!/usr/bin/env python3
"""T-10 — the eval harness, written before the system it grades (spec §8).

`python eval/run_eval.py` reads the labeled cases in `eval/cases.json`, runs
each one, classifies the result, and compares every classification against
`eval/baseline.json`. **The exit code means "the observed results match the
baseline", not "every case passed."** On the day this is written nothing
downstream of T-09 exists, so no case can pass; a gate that could not return
zero until the system was finished would be a gate nobody ran while building it.
See D27.

Drift fails in both directions. A case that starts passing fails the gate as
loudly as one that stops, and the baseline update is the commit that records the
change. That is what makes US-1's close — "`run_eval.py` reports E3 passing" —
a command rather than a table someone reads.

Four statuses, and the last two are the point:

    PASS      the system answered, and answered as labeled
    FAIL      the system answered, and it was wrong
    BLOCKED   the component that would answer does not exist yet
    ERROR     the component exists and aborted with a classified fault

`BLOCKED` never folds into `FAIL`, and `ERROR` folds into neither. All three
read as "not passing," which is exactly why they must stay apart — each names a
different next action, and only `BLOCKED` names a task. This is Article IV's
argument one level above where the article states it, made accounting by REQ-28
(T-30, D77): an `ERROR` is never counted as an abstention — it enters neither
the numerator nor the denominator of the reported abstention rate.

Blocking is **discovered, never declared**: the harness catches
`NotImplementedError` and records the message, and those messages already name
their own task. A `blocked_by` field on a case would keep naming a task after it
landed, which is the T-39 defect with different spelling.

No model is called here, and none will be: this file scores a `Determination`,
it does not produce one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pa_agent.contracts import (  # noqa: E402
    CallMetrics,
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationAborted,
    DeterminationOutcome,
    ErrorCode,
    Document,
    EvidenceSpan,
    GapReason,
)
from pa_agent import history  # noqa: E402
from pa_agent.determination import NoPolicyResult, determine  # noqa: E402
from pa_agent.index import DocumentIndex  # noqa: E402
from pa_agent.runners import RecordedExtractionRunner  # noqa: E402
from pa_agent.verifier import RecordedVerifierRunner  # noqa: E402
from pa_agent.spans import SpanValidationError  # noqa: E402
from pa_agent.spans import validate as validate_span  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402
from pa_agent.stores.knowledge import LocalKnowledgeStore  # noqa: E402
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = EVAL_DIR / "cases.json"
DEFAULT_BASELINE = EVAL_DIR / "baseline.json"

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_HARNESS_BROKEN = 2


class CaseStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    # T-30 (REQ-28, D77): the system aborted with a classified fault. Not FAIL,
    # which means "answered wrongly" about a system that did not answer.
    ERROR = "ERROR"


class ReasonClass(str, Enum):
    """Why a case is not `PASS`, coarsely enough to be diffed.

    The baseline compares a status and one of these, never the free-text
    reason. Diffing prose would fail the gate whenever someone rewords an
    exception, which teaches a reader to update the baseline without looking at
    it — the one habit that makes the whole mechanism worthless (D27).
    """

    WRONG_OUTCOME = "WRONG_OUTCOME"
    # T-88 (D102): the answer came from the wrong tree. Its own class because
    # a right outcome under the wrong jurisdiction is P1's failure exactly.
    WRONG_POLICY_VERSION = "WRONG_POLICY_VERSION"
    WRONG_CRITERION = "WRONG_CRITERION"
    WRONG_GAP_REASON = "WRONG_GAP_REASON"
    # T-81 (D104): a criterion's spans must name as many distinct documents
    # as the label says — E13's claim is that the run was cited from both.
    WRONG_DOCUMENT_COUNT = "WRONG_DOCUMENT_COUNT"
    WRONG_DISCREPANCIES = "WRONG_DISCREPANCIES"
    # T-97 (D119): the medical-history review's advisory channel. One member for
    # one channel, covering both of its claims — the suggestions and the
    # candidates it withheld — because a mislabeled colour and a candidate that
    # should have been withheld have the same next action: read the row.
    WRONG_SUGGESTION = "WRONG_SUGGESTION"
    INVALID_SPAN = "INVALID_SPAN"
    MODEL_CALLS_EXCEEDED = "MODEL_CALLS_EXCEEDED"
    UNEXPECTED_EXCEPTION = "UNEXPECTED_EXCEPTION"
    CASE_UNSPECIFIED = "CASE_UNSPECIFIED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: CaseStatus
    reason_class: ReasonClass | None = None
    reason: str = ""
    model_calls: int | None = None
    # T-20 (Art. X): measured per case, from the determination's own metrics.
    # Deliberately outside `key` below — see the note there.
    input_tokens: int | None = None
    output_tokens: int | None = None
    wall_time_ms: float | None = None
    # T-30 (REQ-28, D77): what the system answered, for the abstention account.
    # None where nothing answered (BLOCKED, ERROR, an unexpected exception).
    # Outside `key` below: the labels already pin every outcome through
    # PASS/FAIL, so the baseline would widen without discriminating more.
    outcome: str | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        """What the baseline records and diffs.

        Status and reason class only. **The T-20 numbers are excluded on
        purpose**: tokens and latency vary between runs of the same model on the
        same input, and folding them in would make D27's exact-match gate fail on
        noise — which is how a gate stops being run. Cost is *reported* every run
        and *asserted* nowhere, which is also why A6 asks for it to be reported
        rather than bounded.
        """
        return (
            self.status.value,
            self.reason_class.value if self.reason_class else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status.value,
            "reason_class": self.reason_class.value if self.reason_class else None,
            "reason": self.reason,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "wall_time_ms": self.wall_time_ms,
            "outcome": self.outcome,
        }


# --------------------------------------------------------------------------
# Scoring — pure, no I/O, and the half no real case can exercise until T-25
# --------------------------------------------------------------------------


#: The one expected outcome that is not a `DeterminationOutcome`: REQ-1's answer
#: is a `NoPolicyResult`, deliberately not a determination (D32), and the scorer
#: matches it by type rather than coercing it into an outcome it does not have.
NO_POLICY_EXPECTATION = "NO_POLICY_FOUND"


def score(
    case: dict[str, Any],
    result: Determination | NoPolicyResult,
    resolve_document: Any = None,
    review: Any = None,
) -> CaseResult:
    """Compare a determination (or a `NoPolicyResult`) against its label.

    Checks run gravest first, and only the gravest finding is reported (D75):
    outcome, then the policy version (T-88), then the named criteria (verdict
    before `gap_reason`) and the criteria the row declares absent, then the
    discrepancy count, then span validity on every cited verdict (A3, Art.
    III), then the model-call budget. A4's zero-call assertion is a claim about *how*
    the right answer was reached, and reporting it over a wrong answer would
    bury the more serious defect.

    `resolve_document` maps a `document_id` to a `Document` for the span check;
    when it is None the span check is skipped — `main()` always supplies one,
    and the self-check pins the branch with a synthetic resolver.

    `review` is the medical-history review for this chart (T-97), supplied only
    for rows that label it. It is checked **after** the determination's own
    claims and before the span walk: a suggestion enters no verdict, so it can
    never be the graver finding, and an Article III failure inside one is graver
    than a mislabeled colour and belongs to the walk that reports it.
    """
    case_id = case["case_id"]
    expect = case["expect"]

    # REQ-1's shape first: either side being `NoPolicyResult` short-circuits,
    # because there is no determination to read metrics or criteria from.
    expects_no_policy = expect["outcome"] == NO_POLICY_EXPECTATION
    if isinstance(result, NoPolicyResult):
        if expects_no_policy:
            return CaseResult(
                case_id, CaseStatus.PASS, None, "",
                model_calls=0, input_tokens=0, output_tokens=0, wall_time_ms=0.0,
            )
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.WRONG_OUTCOME,
            f"expected {expect['outcome']}, but no policy governs "
            f"{result.procedure_code}",
            model_calls=0, input_tokens=0, output_tokens=0, wall_time_ms=0.0,
        )
    determination = result
    calls = determination.model_calls
    # T-20 (Art. X): read off the determination, which sums its own `CallMetrics`.
    # Recorded on every outcome including a failure — a case that answers wrongly
    # still cost what it cost, and dropping the number on the failures would make
    # the reported total quietly optimistic.
    measured = {
        "model_calls": calls,
        "input_tokens": determination.total_input_tokens,
        "output_tokens": determination.total_output_tokens,
        "wall_time_ms": determination.total_wall_time_ms,
    }

    if expects_no_policy:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.WRONG_OUTCOME,
            f"expected {NO_POLICY_EXPECTATION}, got a determination "
            f"({determination.outcome.value} under "
            f"{determination.policy_version_id})",
            **measured,
        )

    expected_outcome = DeterminationOutcome(expect["outcome"])
    if determination.outcome is not expected_outcome:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.WRONG_OUTCOME,
            f"expected {expected_outcome.value}, got {determination.outcome.value}",
            **measured,
        )

    # T-88 (D102): a row in a second jurisdiction pins which tree answered.
    # Checked before the criteria, because every criterion expectation below
    # is a claim about *that* tree's criteria.
    expected_version = expect.get("policy_version_id")
    if expected_version is not None and determination.policy_version_id != expected_version:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.WRONG_POLICY_VERSION,
            f"expected policy_version_id {expected_version}, got "
            f"{determination.policy_version_id}",
            **measured,
        )

    # Criterion-scoped expectations (D75, generalizing D72's E10 ruling). Only
    # the criteria the row names are checked: §6's rows are claims about
    # specific criteria, and pinning the rest here would be labeling from the
    # observed run, which spec §8 forbids.
    results_by_id = {r.criterion_id: r for r in determination.criterion_results}
    for criterion_id, expected in (expect.get("criteria") or {}).items():
        observed = results_by_id.get(criterion_id)
        if observed is None:
            return CaseResult(
                case_id,
                CaseStatus.FAIL,
                ReasonClass.WRONG_CRITERION,
                f"criterion {criterion_id}: expected "
                f"{expected['verdict']}, but the determination carries no "
                "result for it",
                **measured,
            )
        if observed.verdict.value != expected["verdict"]:
            return CaseResult(
                case_id,
                CaseStatus.FAIL,
                ReasonClass.WRONG_CRITERION,
                f"criterion {criterion_id}: expected {expected['verdict']}, "
                f"got {observed.verdict.value}",
                **measured,
            )
        if "gap_reason" in expected:
            observed_reason = (
                observed.gap_reason.value if observed.gap_reason else None
            )
            if observed_reason != expected["gap_reason"]:
                return CaseResult(
                    case_id,
                    CaseStatus.FAIL,
                    ReasonClass.WRONG_GAP_REASON,
                    f"criterion {criterion_id}: expected gap_reason "
                    f"{expected['gap_reason']}, got {observed_reason}",
                    **measured,
                )
        if "distinct_documents" in expected:
            # T-81 (D104): the label says how many documents the criterion's
            # evidence spans, counted exactly — a run cited from one file
            # when the chart holds it in two is E13's failure.
            cited = len({span.document_id for span in observed.spans})
            if cited != expected["distinct_documents"]:
                return CaseResult(
                    case_id,
                    CaseStatus.FAIL,
                    ReasonClass.WRONG_DOCUMENT_COUNT,
                    f"criterion {criterion_id}: spans cite {cited} distinct "
                    f"document(s), expected {expected['distinct_documents']}",
                    **measured,
                )

    # T-88 (D102): a row may declare that a criterion does not exist under its
    # tree — J1's claim is that Palmetto states no run length, so `c3` is
    # absent rather than failed. A determination carrying it answered under
    # the wrong shape, whatever it said.
    for criterion_id in expect.get("absent_criteria") or []:
        if criterion_id in results_by_id:
            return CaseResult(
                case_id,
                CaseStatus.FAIL,
                ReasonClass.WRONG_CRITERION,
                f"criterion {criterion_id}: expected absent from this tree, but "
                f"the determination carries it "
                f"({results_by_id[criterion_id].verdict.value})",
                **measured,
            )

    # REQ-39's advisory channel, counted exactly. E10's single entry and
    # E10c's empty list are both labels, so both directions fail.
    expected_discrepancies = expect.get("discrepancies")
    if expected_discrepancies is not None:
        observed_count = len(determination.discrepancies)
        if observed_count != expected_discrepancies:
            return CaseResult(
                case_id,
                CaseStatus.FAIL,
                ReasonClass.WRONG_DISCREPANCIES,
                f"expected {expected_discrepancies} discrepancy entr"
                f"{'y' if expected_discrepancies == 1 else 'ies'}, "
                f"got {observed_count}",
                **measured,
            )

    # T-97 (D119): the review's two claims, both exact in both directions.
    #
    # `withheld` is not decoration. With a broken ingredient expansion nothing
    # matches, every chart yields no candidates, and a row expecting no
    # suggestions passes while the feature does nothing — which is the mutation
    # D119 puts on the list, measured to produce zero hits in all fourteen
    # bundles. The withheld claim is what makes that row fail.
    expected_suggestions = expect.get("suggestions")
    expected_withheld = expect.get("withheld")
    if expected_suggestions is not None or expected_withheld is not None:
        if review is None:
            return CaseResult(
                case_id,
                CaseStatus.FAIL,
                ReasonClass.WRONG_SUGGESTION,
                "the row labels a medical-history review and none was run; a row "
                "with no chart cannot carry the claim",
                **measured,
            )
        if expected_suggestions is not None:
            observed = [
                {
                    "row_id": s.row_id,
                    "code": s.icd10_code,
                    "colour": s.colour.value,
                    "would_affect": list(s.would_affect),
                }
                for s in review.suggestions
            ]
            wanted = [
                {
                    "row_id": s["row_id"],
                    "code": s["code"],
                    "colour": s["colour"],
                    "would_affect": list(s.get("would_affect") or []),
                }
                for s in expected_suggestions
            ]
            if observed != wanted:
                return CaseResult(
                    case_id,
                    CaseStatus.FAIL,
                    ReasonClass.WRONG_SUGGESTION,
                    f"expected suggestions {wanted}, got {observed}",
                    **measured,
                )
        if expected_withheld is not None:
            observed_withheld = [
                {"row_id": w.row_id, "reason": w.reason.value} for w in review.withheld
            ]
            wanted_withheld = [
                {"row_id": w["row_id"], "reason": w["reason"]}
                for w in expected_withheld
            ]
            if observed_withheld != wanted_withheld:
                return CaseResult(
                    case_id,
                    CaseStatus.FAIL,
                    ReasonClass.WRONG_SUGGESTION,
                    f"expected withheld {wanted_withheld}, got {observed_withheld}",
                    **measured,
                )

    # A3, on every case: every span carried by a cited verdict must validate
    # against the unmodified source (Art. III — not scoped to MET; A3's gate
    # reads the MET subset). The index is built lazily from exactly the
    # documents the spans name.
    if resolve_document is not None:
        index = DocumentIndex()
        cited: list[tuple[str, Any]] = [
            (f"criterion {criterion.criterion_id}", span)
            for criterion in determination.criterion_results
            for span in criterion.spans
        ]
        if review is not None:
            # A suggestion's citations are held to the same rule (Art. III):
            # its `effect` span slices a hashed drug label and its `citations`
            # slice the chart. One walk, one `ReasonClass`, so A3's figure does
            # not split across two.
            cited += [
                (f"suggestion {item.row_id}", span)
                for item in (*review.suggestions, *review.withheld)
                for span in (
                    *(getattr(item, "citations", ()) or ()),
                    *((item.effect,) if getattr(item, "effect", None) else ()),
                )
            ]
        for where, span in cited:
            try:
                if span.document_id not in index:
                    index.add(resolve_document(span.document_id))
                validate_span(span, index)
            except (SpanValidationError, KeyError) as exc:
                return CaseResult(
                    case_id,
                    CaseStatus.FAIL,
                    ReasonClass.INVALID_SPAN,
                    f"{where}: "
                    f"{span.document_id}[{span.char_start}:{span.char_end}] "
                    f"failed validation: {exc}",
                    **measured,
                )

    budget = expect.get("max_model_calls")
    if budget is not None and calls > budget:
        return CaseResult(
            case_id,
            CaseStatus.FAIL,
            ReasonClass.MODEL_CALLS_EXCEEDED,
            f"{calls} model call(s) against a budget of {budget}",
            **measured,
        )

    return CaseResult(case_id, CaseStatus.PASS, None, "", **measured)


# --------------------------------------------------------------------------
# The system under test
# --------------------------------------------------------------------------

#: The date every window is measured from. T-06 pinned the ground truth here and
#: every recency verdict moves with it, so a harness reading `date.today()` would
#: score a different system every morning.
EVAL_AS_OF = date(2026, 9, 1)

EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
VERIFIER_RESULTS = REPO_ROOT / "eval" / "verifier" / "results.json"


def _recorded_verifier() -> Any:
    """T-17's recording as a `VerifierRunner`. Spends nothing (D78).

    `None` when the measurement has not been run — the workflow then raises at
    the first cited verdict, which the harness reports as BLOCKED naming the
    script, rather than this function inventing an answer (D31).
    """
    if not VERIFIER_RESULTS.exists():
        return None
    recording = json.loads(VERIFIER_RESULTS.read_text(encoding="utf-8"))
    return RecordedVerifierRunner.from_records(
        recording["claims"], model=recording.get("model")
    )


def _recorded_runner() -> Any:
    """T-15's recording as an `ExtractionRunner`. Spends nothing.

    The harness reads the file, not the runner: REQ-41 keeps storage locations out
    of `pa_agent/`, and this module is the grader.
    """
    if not EXTRACTION_RESULTS.exists():
        return None
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording.get("model")
    )



def _determine(
    case: dict[str, Any],
    policy_store: Any,
    patient_store: Any = None,
    extraction_runner: Any = None,
    as_of: Any = None,
    cache: dict[tuple[Any, ...], Determination | NoPolicyResult] | None = None,
    verifier: Any = None,
) -> Determination | NoPolicyResult:
    """The seam T-24 and T-25 filled: the system under test, end to end.

    One determination per `(patient_id, procedure_code, as_of)`, cached (D75):
    rows sharing the key are scored against the same object, which is what
    makes contradictory labels on one patient fail loudly instead of each row
    quietly grading its own run. A `NoPolicyResult` is returned as itself —
    the scorer matches it by type (D26's collapse, refused a second time).

    A row may carry its own `as_of` (D75; E2 is why — sc2 refuses evidence
    that is stale at the harness clock, deliberately so on that patient, so
    E2's row runs at a date where its BMI is in-window while E7 reads the same
    chart at `EVAL_AS_OF`).
    """
    if case.get("as_of") is not None:
        as_of = date.fromisoformat(case["as_of"])
    key = (case.get("patient_id"), case["procedure_code"], as_of, case.get("state"))
    if cache is not None and key in cache:
        return cache[key]
    result = determine(
        policy_store,
        case["procedure_code"],
        patient_id=case.get("patient_id"),
        patient_store=patient_store,
        as_of=as_of,
        extraction_runner=extraction_runner,
        verifier=verifier,
        # T-87 (D100): a row names its state, or the patient's bundle does. A
        # patientless row (E3, NP1) carries one explicitly.
        state=case.get("state"),
    )
    if cache is not None:
        cache[key] = result
    return result


#: The claims that make a row a medical-history row. Named once, because
#: `run_case` and `score` must agree about which rows carry a review.
REVIEW_CLAIMS = ("suggestions", "withheld")


def _labels_a_review(case: dict[str, Any]) -> bool:
    return any(case["expect"].get(claim) is not None for claim in REVIEW_CLAIMS)


def _review(
    case: dict[str, Any],
    result: Determination | NoPolicyResult,
    policy_store: Any,
    patient_store: Any,
    knowledge_store: Any,
) -> Any:
    """The medical-history review for one chart, under the tree that answered.

    Computed **only for rows that label it**, and that is load-bearing rather
    than an optimization: `bc6748d3` carries an active lisinopril, no creatinine
    and notes that mention nothing renal, so its candidate can only be separated
    from yellow by reading them — and with no quote source the review raises
    (D90, D119). Two committed rows, E8 and E10b, run through that chart.
    Reviewing every case would make this gate red today, and the row that
    fixes it is T-98.
    """
    tree = policy_store.get_tree(result.policy_version_id)
    value_sets = {}
    for criterion in tree.criteria:
        constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
        if constant is not None:
            value_sets[str(constant.value)] = policy_store.get_value_set(
                str(constant.value)
            )
    rows = knowledge_store.get_medication_effect_rows()
    return history.review(
        patient_id=case["patient_id"],
        policy_version_id=tree.policy_version_id,
        rows=rows,
        products={
            row.ingredient.code: knowledge_store.get_ingredient_products(
                row.ingredient.code
            )
            for row in rows
        },
        medications=patient_store.get_medications(case["patient_id"]),
        conditions=patient_store.get_conditions(case["patient_id"]),
        observations=patient_store.get_observations(case["patient_id"]),
        criteria=tree.criteria,
        value_sets=value_sets,
        notes=patient_store.get_notes(case["patient_id"]),
    )


def run_case(
    case: dict[str, Any],
    policy_store: Any,
    patient_store: Any = None,
    extraction_runner: Any = None,
    as_of: Any = None,
    cache: dict[tuple[Any, ...], Determination | NoPolicyResult] | None = None,
    resolve_document: Any = None,
    verifier: Any = None,
    knowledge_store: Any = None,
) -> CaseResult:
    """Run one labeled case and classify the result.

    The broad handler is deliberate and is not the thing REQ-27 forbids: it
    re-raises nothing but maps to a *named* `reason_class` and records the
    traceback, and D7 is explicit that a batch eval should report which case
    failed rather than die on the first. REQ-27's grep is over `pa_agent/`; this
    file is the grader, not the system.
    """
    case_id = case["case_id"]

    if case.get("procedure_code") is None:
        return CaseResult(
            case_id,
            CaseStatus.BLOCKED,
            ReasonClass.CASE_UNSPECIFIED,
            case.get("unspecified_reason", "the case carries no procedure code"),
        )

    try:
        result = _determine(
            case, policy_store, patient_store, extraction_runner, as_of, cache,
            verifier,
        )
    except NotImplementedError as exc:
        return CaseResult(
            case_id, CaseStatus.BLOCKED, ReasonClass.NOT_IMPLEMENTED, str(exc)
        )
    except DeterminationAborted as exc:
        # T-29's abort under T-30's accounting (REQ-28, D77): the system did
        # not answer, so this is neither FAIL nor an abstention. str(exc)
        # already names each errored criterion and its `error_code`.
        return CaseResult(case_id, CaseStatus.ERROR, ReasonClass.ERROR, str(exc))
    except Exception as exc:  # noqa: BLE001 — mapped to a named class, see above
        detail = "".join(
            traceback.format_exception_only(type(exc), exc)
        ).strip()
        return CaseResult(
            case_id, CaseStatus.FAIL, ReasonClass.UNEXPECTED_EXCEPTION, detail
        )

    review = None
    if (
        knowledge_store is not None
        and case.get("patient_id")
        and not isinstance(result, NoPolicyResult)
        and _labels_a_review(case)
    ):
        try:
            review = _review(
                case, result, policy_store, patient_store, knowledge_store
            )
        except Exception as exc:  # noqa: BLE001 — same rule as the handler above
            detail = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            return CaseResult(
                case_id, CaseStatus.FAIL, ReasonClass.UNEXPECTED_EXCEPTION, detail
            )

    scored = score(case, result, resolve_document, review)
    outcome = (
        NO_POLICY_EXPECTATION
        if isinstance(result, NoPolicyResult)
        else result.outcome.value
    )
    return replace(scored, outcome=outcome)


# --------------------------------------------------------------------------
# Scorer self-check
# --------------------------------------------------------------------------


def _synthetic_case(**overrides: Any) -> dict[str, Any]:
    case = {
        "case_id": "SELF",
        "procedure_code": "00000",
        # T-87 (D100): every request names a state; the self-check's fakes
        # accept any and the harness passes it through like a real row's.
        "state": "WA",
        "expect": {"outcome": "NOT_COVERED", "max_model_calls": 0},
    }
    case.update(overrides)
    return case


def _synthetic_determination(
    outcome: DeterminationOutcome,
    metrics: list[CallMetrics] | None = None,
    criterion_results: list[CriterionResult] | None = None,
) -> Determination:
    return Determination(
        patient_id="self-check",
        procedure_code="00000",
        policy_version_id="self-check-v0",
        outcome=outcome,
        criterion_results=criterion_results or [],
        metrics=metrics or [],
    )


def self_check() -> list[tuple[str, bool, str]]:
    """Score synthetic cases whose answers are known.

    Every branch below is one no real case can reach until T-25 produces a
    determination. Without this the code that decides whether US-1 passed would
    sit unrun until the moment it decides whether US-1 passed.
    """
    metric = CallMetrics(
        model="self-check",
        purpose="self-check",
        input_tokens=0,
        output_tokens=0,
        wall_time_ms=0.0,
    )

    class _RaisingStore:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def resolve(self, procedure_code: str, state: str) -> None:
            raise self._exc

    checks: list[tuple[str, tuple[str, str | None], tuple[str, str | None]]] = []

    def record(label: str, observed: CaseResult, expected: tuple[str, str | None]):
        checks.append((label, observed.key, expected))

    record(
        "a matching determination scores PASS",
        score(_synthetic_case(), _synthetic_determination(DeterminationOutcome.NOT_COVERED)),
        ("PASS", None),
    )
    record(
        "a mismatched outcome scores FAIL/WRONG_OUTCOME",
        score(_synthetic_case(), _synthetic_determination(DeterminationOutcome.MET)),
        ("FAIL", "WRONG_OUTCOME"),
    )
    record(
        "a spent model call against a zero budget scores FAIL/MODEL_CALLS_EXCEEDED",
        score(
            _synthetic_case(),
            _synthetic_determination(DeterminationOutcome.NOT_COVERED, [metric]),
        ),
        ("FAIL", "MODEL_CALLS_EXCEEDED"),
    )
    record(
        "a wrong outcome outranks a blown call budget",
        score(
            _synthetic_case(),
            _synthetic_determination(DeterminationOutcome.MET, [metric]),
        ),
        ("FAIL", "WRONG_OUTCOME"),
    )
    record(
        "a case with no procedure code is BLOCKED/CASE_UNSPECIFIED",
        run_case(_synthetic_case(procedure_code=None), _RaisingStore(AssertionError())),
        ("BLOCKED", "CASE_UNSPECIFIED"),
    )
    record(
        "NotImplementedError is BLOCKED/NOT_IMPLEMENTED, never FAIL",
        run_case(_synthetic_case(), _RaisingStore(NotImplementedError("T-38"))),
        ("BLOCKED", "NOT_IMPLEMENTED"),
    )
    record(
        "any other exception is FAIL/UNEXPECTED_EXCEPTION",
        run_case(_synthetic_case(), _RaisingStore(ValueError("boom"))),
        ("FAIL", "UNEXPECTED_EXCEPTION"),
    )

    # ---- T-30's branch (REQ-28, D77): the abort's classification ----------

    aborted = DeterminationAborted(
        [
            CriterionResult(
                criterion_id="c1",
                verdict=CriterionVerdict.ERROR,
                error_code=ErrorCode.MODEL_CALL_FAILED,
                error_detail="self-check",
            )
        ],
        attempts=3,
    )
    abort_result = run_case(_synthetic_case(), _RaisingStore(aborted))
    record(
        "DeterminationAborted is ERROR/ERROR — neither FAIL nor an abstention",
        abort_result,
        ("ERROR", "ERROR"),
    )
    generic_extra: list[tuple[str, bool, str]] = [
        (
            "an ERROR case carries no outcome, so no rate can count it",
            abort_result.outcome is None,
            f"outcome={abort_result.outcome!r}",
        )
    ]

    class _UngoverningStore:
        """resolve() returns None: the port's documented answer for a code no
        policy governs, which `resolve_sc1` maps to `NoPolicyFound` (REQ-1)."""

        def resolve(self, procedure_code: str, state: str) -> None:
            return None

    # ---- D75's branches: REQ-1's shape, criterion-scoped rows, A3 ----------

    record(
        "an ungoverned code under an outcome expectation is FAIL/WRONG_OUTCOME, "
        "never a coerced denial",
        run_case(_synthetic_case(), _UngoverningStore()),
        ("FAIL", "WRONG_OUTCOME"),
    )
    record(
        "NO_POLICY_FOUND expected and answered scores PASS",
        run_case(
            _synthetic_case(expect={"outcome": NO_POLICY_EXPECTATION}),
            _UngoverningStore(),
        ),
        ("PASS", None),
    )
    record(
        "NO_POLICY_FOUND expected over a determination is FAIL/WRONG_OUTCOME",
        score(
            _synthetic_case(expect={"outcome": NO_POLICY_EXPECTATION}),
            _synthetic_determination(DeterminationOutcome.NOT_COVERED),
        ),
        ("FAIL", "WRONG_OUTCOME"),
    )

    doc_text = "BMI 41.2 documented at the March visit."
    doc = Document(
        document_id="self-doc",
        text=doc_text,
        sha256=hashlib.sha256(doc_text.encode("utf-8")).hexdigest(),
    )
    doc_2 = Document(
        document_id="self-doc-2",
        text=doc_text,
        sha256=doc.sha256,
    )

    def resolve_synthetic(document_id: str) -> Document:
        if document_id == doc.document_id:
            return doc
        if document_id == doc_2.document_id:
            return doc_2
        raise KeyError(document_id)

    good_span = EvidenceSpan(document_id="self-doc", char_start=0, char_end=8)
    second_doc_span = EvidenceSpan(document_id="self-doc-2", char_start=0, char_end=8)
    bad_span = EvidenceSpan(document_id="self-doc", char_start=0, char_end=10_000)

    def _cited(criterion_id: str, verdict: CriterionVerdict, span: EvidenceSpan):
        return CriterionResult(
            criterion_id=criterion_id, verdict=verdict, spans=[span]
        )

    not_met_c3 = _synthetic_determination(
        DeterminationOutcome.NOT_MET,
        criterion_results=[_cited("c3", CriterionVerdict.NOT_MET, good_span)],
    )

    def _criteria_case(criteria: dict, outcome: str = "NOT_MET", **extra: Any):
        return _synthetic_case(
            expect={"outcome": outcome, "criteria": criteria, **extra}
        )

    record(
        "a criterion matching its label scores PASS, its span validated",
        score(
            _criteria_case({"c3": {"verdict": "NOT_MET"}}, discrepancies=0),
            not_met_c3,
            resolve_synthetic,
        ),
        ("PASS", None),
    )
    record(
        "a criterion contradicting its label is FAIL/WRONG_CRITERION",
        score(
            _criteria_case({"c3": {"verdict": "MET"}}), not_met_c3, resolve_synthetic
        ),
        ("FAIL", "WRONG_CRITERION"),
    )
    record(
        "an expectation naming an absent criterion is FAIL/WRONG_CRITERION",
        score(
            _criteria_case({"c4": {"verdict": "NOT_MET"}}),
            not_met_c3,
            resolve_synthetic,
        ),
        ("FAIL", "WRONG_CRITERION"),
    )
    record(
        "a wrong gap_reason is FAIL/WRONG_GAP_REASON",
        score(
            _criteria_case(
                {
                    "c1": {
                        "verdict": "INSUFFICIENT_EVIDENCE",
                        "gap_reason": "NO_EVIDENCE_RETRIEVED",
                    }
                },
                outcome="INSUFFICIENT_EVIDENCE",
            ),
            _synthetic_determination(
                DeterminationOutcome.INSUFFICIENT_EVIDENCE,
                criterion_results=[
                    CriterionResult(
                        criterion_id="c1",
                        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
                        gap_reason=GapReason.UNSUBSTANTIATED_ASSERTION,
                    )
                ],
            ),
        ),
        ("FAIL", "WRONG_GAP_REASON"),
    )
    # T-81 (D104): the document-count expectation, both directions.
    record(
        "a criterion cited from two documents matches distinct_documents 2",
        score(
            _criteria_case({"c3": {"verdict": "MET", "distinct_documents": 2}}, outcome="MET"),
            _synthetic_determination(
                DeterminationOutcome.MET,
                criterion_results=[
                    CriterionResult(
                        criterion_id="c3", verdict=CriterionVerdict.MET,
                        spans=[good_span, second_doc_span],
                    )
                ],
            ),
            resolve_synthetic,
        ),
        ("PASS", None),
    )
    record(
        "a criterion cited from one document against distinct_documents 2 is FAIL/WRONG_DOCUMENT_COUNT",
        score(
            _criteria_case({"c3": {"verdict": "MET", "distinct_documents": 2}}, outcome="MET"),
            _synthetic_determination(
                DeterminationOutcome.MET,
                criterion_results=[
                    CriterionResult(
                        criterion_id="c3", verdict=CriterionVerdict.MET,
                        spans=[good_span, EvidenceSpan(document_id="self-doc", char_start=9, char_end=19)],
                    )
                ],
            ),
            resolve_synthetic,
        ),
        ("FAIL", "WRONG_DOCUMENT_COUNT"),
    )
    record(
        "a missing discrepancy entry is FAIL/WRONG_DISCREPANCIES",
        score(
            _synthetic_case(
                expect={"outcome": "NOT_MET", "discrepancies": 1}
            ),
            not_met_c3,
        ),
        ("FAIL", "WRONG_DISCREPANCIES"),
    )
    record(
        "a cited verdict with an invalid span is FAIL/INVALID_SPAN (A3)",
        score(
            _synthetic_case(expect={"outcome": "NOT_MET"}),
            _synthetic_determination(
                DeterminationOutcome.NOT_MET,
                criterion_results=[
                    _cited("c3", CriterionVerdict.NOT_MET, bad_span)
                ],
            ),
            resolve_synthetic,
        ),
        ("FAIL", "INVALID_SPAN"),
    )
    record(
        "a wrong criterion outranks a blown call budget",
        score(
            _criteria_case({"c3": {"verdict": "MET"}}, max_model_calls=0),
            _synthetic_determination(
                DeterminationOutcome.NOT_MET,
                metrics=[metric],
                criterion_results=[
                    _cited("c3", CriterionVerdict.NOT_MET, good_span)
                ],
            ),
            resolve_synthetic,
        ),
        ("FAIL", "WRONG_CRITERION"),
    )
    # T-88 (D102): the two expectations J1 needs, each in both directions.
    record(
        "a matching policy_version_id scores PASS",
        score(
            _synthetic_case(expect={"outcome": "NOT_MET", "policy_version_id": "self-check-v0"}),
            not_met_c3,
        ),
        ("PASS", None),
    )
    record(
        "the wrong tree is FAIL/WRONG_POLICY_VERSION even with the right outcome",
        score(
            _synthetic_case(expect={"outcome": "NOT_MET", "policy_version_id": "other-v9"}),
            not_met_c3,
        ),
        ("FAIL", "WRONG_POLICY_VERSION"),
    )
    record(
        "a criterion declared absent and absent scores PASS",
        score(
            _synthetic_case(expect={"outcome": "NOT_MET", "absent_criteria": ["c4"]}),
            not_met_c3,
        ),
        ("PASS", None),
    )
    record(
        "a criterion declared absent but present is FAIL/WRONG_CRITERION",
        score(
            _synthetic_case(expect={"outcome": "NOT_MET", "absent_criteria": ["c3"]}),
            not_met_c3,
        ),
        ("FAIL", "WRONG_CRITERION"),
    )
    # Two rows sharing one cached run (D75): the same determination scored
    # against contradictory labels cannot satisfy both.
    record(
        "contradictory rows on one shared run: the agreeing row passes",
        score(_synthetic_case(expect={"outcome": "NOT_MET"}), not_met_c3),
        ("PASS", None),
    )
    record(
        "contradictory rows on one shared run: the contradicting row fails",
        score(_synthetic_case(expect={"outcome": "MET"}), not_met_c3),
        ("FAIL", "WRONG_OUTCOME"),
    )

    generic = [
        (label, observed == expected, f"expected {expected}, got {observed}")
        for label, observed, expected in checks
    ]
    generic.extend(generic_extra)

    # T-20's own self-check, and it is not about a key.
    #
    # Every case in the set today is a zero-call short circuit, so the reported
    # tokens and wall time are legitimately 0 — and a wiring bug that dropped the
    # numbers would report exactly the same 0. This is the check that tells those
    # two apart: a determination carrying metrics must report them (Art. X).
    # Its own metric with non-zero numbers. The shared `metric` above is all
    # zeros — it exists to make `model_calls` non-zero for the budget checks — and
    # reusing it here would make this assertion `0 == 2 * 0`, which is exactly the
    # vacuous check this is supposed to replace.
    costly = CallMetrics(
        model="self-check",
        purpose="self-check",
        input_tokens=13,
        output_tokens=7,
        wall_time_ms=2.5,
    )
    measured = score(
        _synthetic_case(expect={"outcome": "NOT_COVERED", "max_model_calls": None}),
        _synthetic_determination(DeterminationOutcome.NOT_COVERED, [costly, costly]),
    )
    generic.append(
        (
            "a determination with metrics reports its tokens and wall time",
            (
                measured.model_calls == 2
                and measured.input_tokens == 26
                and measured.output_tokens == 14
                and measured.wall_time_ms == 5.0
            ),
            f"model_calls={measured.model_calls} in={measured.input_tokens} "
            f"out={measured.output_tokens} ms={measured.wall_time_ms}",
        )
    )
    return generic


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def diff_against_baseline(
    results: list[CaseResult], baseline: dict[str, dict[str, Any]]
) -> list[str]:
    """Every way the observed set can differ from what was recorded."""
    drift: list[str] = []
    observed = {r.case_id: r for r in results}

    for case_id, result in observed.items():
        if case_id not in baseline:
            drift.append(
                f"{case_id}: ran but is absent from the baseline "
                f"(observed {result.key[0]}/{result.key[1]}). A case nothing "
                "records is a case nothing grades."
            )
            continue
        recorded = (baseline[case_id]["status"], baseline[case_id].get("reason_class"))
        if result.key != recorded:
            drift.append(
                f"{case_id}: baseline says {recorded[0]}/{recorded[1]}, "
                f"observed {result.key[0]}/{result.key[1]}"
            )

    for case_id in baseline:
        if case_id not in observed:
            drift.append(f"{case_id}: in the baseline and not in the case set")

    return drift


def baseline_from(results: list[CaseResult], note: str) -> dict[str, Any]:
    return {
        "recorded_on": date.today().isoformat(),
        "note": note,
        "cases": {
            r.case_id: {
                "status": r.status.value,
                "reason_class": r.reason_class.value if r.reason_class else None,
            }
            for r in sorted(results, key=lambda r: r.case_id)
        },
    }


# --------------------------------------------------------------------------
# The abstention account (T-30, REQ-28, D77)
# --------------------------------------------------------------------------


def abstention_account(results: list[CaseResult]) -> dict[str, Any]:
    """REQ-28's arithmetic, in the one place the report reads it from.

    An abstention is an answered case whose outcome is
    `INSUFFICIENT_EVIDENCE`. The denominator is answered cases only — `PASS`
    and `FAIL` both count, because the rate measures how often the system
    abstained, not how often it was right. An `ERROR` enters neither side:
    excluding it from the numerator alone would let a crash *lower* the rate,
    caution misreported as confidence (D77).
    """
    answered = [r for r in results if r.outcome is not None]
    abstained = [
        r
        for r in answered
        if r.outcome == DeterminationOutcome.INSUFFICIENT_EVIDENCE.value
    ]
    errors = [r for r in results if r.status is CaseStatus.ERROR]
    return {
        "answered": len(answered),
        "abstained": len(abstained),
        "errors": len(errors),
        "abstention_rate": len(abstained) / len(answered) if answered else None,
    }


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def print_report(results: list[CaseResult], drift: list[str], baseline_path: Path) -> None:
    print()
    width = max((len(r.case_id) for r in results), default=4)
    for result in sorted(results, key=lambda r: r.case_id):
        reason_class = result.reason_class.value if result.reason_class else "-"
        print(f"  {result.case_id:<{width}}  {result.status.value:<7}  {reason_class}")
        if result.reason:
            for line in _wrap(result.reason, 76):
                print(f"  {'':<{width}}           {line}")

    counts = {status: 0 for status in CaseStatus}
    for result in results:
        counts[result.status] += 1
    calls = sum(r.model_calls or 0 for r in results)
    input_tokens = sum(r.input_tokens or 0 for r in results)
    output_tokens = sum(r.output_tokens or 0 for r in results)
    wall_ms = sum(r.wall_time_ms or 0.0 for r in results)

    print()
    print(
        f"  {len(results)} case(s): {counts[CaseStatus.PASS]} PASS, "
        f"{counts[CaseStatus.FAIL]} FAIL, {counts[CaseStatus.BLOCKED]} BLOCKED, "
        f"{counts[CaseStatus.ERROR]} ERROR"
    )
    # T-30 / REQ-28. The ERROR exclusion is printed even at zero, because the
    # rate is only readable next to what it deliberately does not count.
    account = abstention_account(results)
    if account["abstention_rate"] is None:
        print("  abstention rate: n/a (no case answered)")
    else:
        print(
            f"  abstention rate: {account['abstained']}/{account['answered']} "
            f"answered = {account['abstention_rate']:.3f}"
            f"  ·  {account['errors']} ERROR case(s) excluded (REQ-28)"
        )
    # T-20 / Article X. Reported, never asserted: tokens and latency move between
    # runs of the same model on the same input, and folding them into the gate
    # would fail it on noise. A6 asks for cost and latency *reported* from
    # instrumentation, and these come off each determination's own metrics.
    print(f"  model calls spent: {calls}")
    print(
        f"  tokens: {input_tokens} in, {output_tokens} out"
        f"  ·  wall time: {wall_ms:.1f}ms"
    )
    if calls:
        print(
            f"  per model call: {input_tokens / calls:.0f} in, "
            f"{output_tokens / calls:.0f} out, {wall_ms / calls:.0f}ms"
        )
    if counts[CaseStatus.BLOCKED]:
        print(
            f"  ({counts[CaseStatus.BLOCKED]} case(s) BLOCKED, so these totals "
            "cover a subset of the eval set)"
        )
    print()

    rel = baseline_path.relative_to(REPO_ROOT) if baseline_path.is_relative_to(REPO_ROOT) else baseline_path
    if drift:
        print(f"  DRIFT against {rel}:")
        for line in drift:
            print(f"    - {line}")
        print()
        print("  Either the change was intended — re-run with --update-baseline and")
        print("  commit the diff — or something regressed. The gate does not guess.")
    else:
        print(f"  no drift against {rel}")
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json", type=Path, default=None, help="write results here")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite the baseline from what was observed, for review as a diff",
    )
    parser.add_argument("--no-self-check", action="store_true")
    args = parser.parse_args(argv)

    if not args.no_self_check:
        checks = self_check()
        failed = [(label, detail) for label, ok, detail in checks if not ok]
        if failed:
            print(f"\n  scorer self-check FAILED: {len(failed)} of {len(checks)} checks")
            for label, detail in failed:
                print(f"    - {label}: {detail}")
            print("\n  The instrument is broken. Nothing below it is worth reading.\n")
            return EXIT_HARNESS_BROKEN
        print(f"\n  scorer self-check: {len(checks)}/{len(checks)}")

    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n  cannot read cases from {args.cases}: {exc}\n", file=sys.stderr)
        return EXIT_HARNESS_BROKEN

    # Two rows under one id collapse silently in every dict keyed by case id —
    # the baseline would grade one of them and pretend it graded both (D75).
    seen: set[str] = set()
    duplicates = sorted(
        {c["case_id"] for c in cases if c["case_id"] in seen or seen.add(c["case_id"])}
    )
    if duplicates:
        print(
            f"\n  duplicate case id(s) in {args.cases}: {', '.join(duplicates)}\n",
            file=sys.stderr,
        )
        return EXIT_HARNESS_BROKEN

    policy_store = LocalPolicyStore()
    patient_store = LocalPatientStore()

    knowledge_store = LocalKnowledgeStore()

    def resolve_document(document_id: str) -> Document:
        """Any corpus's document, for A3's span check. The scorer reads all three
        because spans legitimately point into all three — criterion spans into the
        patient's bundle and notes, coverage spans into the policy corpus, and
        since T-97 a suggestion's effect span into a hashed drug label."""
        try:
            return patient_store.get_document(document_id)
        except KeyError:
            pass
        try:
            return policy_store.get_document(document_id)
        except KeyError:
            return knowledge_store.get_document(document_id)
    # REQ-52: the harness supplies the model leaf, and supplies the free one. A
    # replay of T-15's recording answers every note in the corpus for zero calls,
    # so `run_eval.py` stays a command anyone can run — which is what makes D27's
    # exact-match gate something that gets run rather than something that gets
    # skipped because it costs money.
    #
    # Wired in now although E3 never reaches it: without this, the first covered
    # case T-21 adds would report BLOCKED/NOT_IMPLEMENTED for a path that is built,
    # and the drift would be read as a regression in the system rather than a gap
    # in the harness.
    extraction_runner = _recorded_runner()
    verifier = _recorded_verifier()
    cache: dict[tuple[Any, ...], Determination | NoPolicyResult] = {}
    results = [
        run_case(
            case,
            policy_store,
            patient_store,
            extraction_runner,
            EVAL_AS_OF,
            cache,
            resolve_document,
            verifier,
            knowledge_store,
        )
        for case in cases
    ]

    if args.update_baseline:
        note = (
            "Observed status per case. Updated by --update-baseline; the change "
            "is meant to be read as a diff (D27)."
        )
        args.baseline.write_text(
            json.dumps(baseline_from(results, note), indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n  baseline rewritten: {args.baseline}")

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n  cannot read the baseline at {args.baseline}: {exc}\n", file=sys.stderr)
        return EXIT_HARNESS_BROKEN

    drift = diff_against_baseline(results, baseline)
    print_report(results, drift, args.baseline)

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "eval_set": str(args.cases.name),
                    "results": [r.as_dict() for r in results],
                    "drift": drift,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return EXIT_DRIFT if drift else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
