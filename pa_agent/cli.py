"""T-25 — the CLI: request in, determination out. *Extended by T-18/T-62 (D62).*

    python -m pa_agent.cli --patient X --procedure 43842
    python -m pa_agent.cli --patient <uuid> --procedure 43775
    python -m pa_agent.cli --patient <uuid> --procedure 43775 --extraction adk

Prints one JSON document to stdout. For a determination it carries the outcome,
the `policy_version_id` (REQ-4), the coverage claim it cites where there is one
(Art. III), the gap list (REQ-21), REQ-39's discrepancies, and the model-call
counter — computed properties, added explicitly because `model_dump` only
serializes fields. For a code no policy governs it prints a `NO_POLICY_FOUND`
result and still exits zero: a deterministic answer is not an error (D32).

**This module is where every path is named and every port is constructed** — the
two stores, and the extraction runner (REQ-41, REQ-52). Nothing below it holds a
path or a credential, which is what makes the production adapters D25 wants a
second implementation rather than a rewrite.

`--extraction` picks the model leaf, and the default is the honest one:

    recorded  replay `eval/extraction/results.json`. Zero model calls, so the
              same request answers the same way twice, which is what makes this
              a demo path rather than a bill.
    direct    `google-genai` with a native response schema — D45's measured
              configuration.
    adk       `google-adk` 2.8.0, with declared tools. A different call
              configuration and therefore a different measurement (T-63).

Exit codes: 0 for an answer, 1 for a request the stores cannot resolve (an
unknown patient), 2 for a path the system has not built yet — the
`NotImplementedError` message, which names what is missing, goes to stderr —
and 3 for a determination aborted over a criterion in `ERROR` (REQ-24, T-29,
D76): one stderr line per errored criterion carrying the criterion id and its
`error_code` (REQ-29), and nothing on stdout.

*Exit 2 currently has no reachable route from this entry point*: T-18 and T-19
built the criteria path, and T-17 built the verifier this module now always
supplies beside the extraction runner. The handler is kept because it is how
the next unbuilt path reports itself instead of crashing.
`tests/test_determination.py` asserts the mapping directly rather than through
a subprocess that can no longer trigger it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from datetime import date
from pathlib import Path

from pa_agent import history
from pa_agent.contracts import Determination, DeterminationAborted
from pa_agent.determination import NoJurisdictionResult, NoPolicyResult, determine
from pa_agent.quotes import RecordedQuoteRunner
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.verifier import RecordedVerifierRunner
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.model_pin import MEASURED_TIER
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.tiers import TIERS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECORDING = REPO_ROOT / "eval" / "extraction" / "results.json"
DEFAULT_VERIFIER_RECORDING = REPO_ROOT / "eval" / "verifier" / "results.json"
DEFAULT_HISTORY_RECORDING = REPO_ROOT / "eval" / "history" / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"


def _render(
    result: Determination | NoPolicyResult | NoJurisdictionResult,
    suggestions: dict | None = None,
) -> dict:
    """The printed document. `suggestions` is the `--suggest` block or `None`.

    The block rides **beside** the determination's own keys and never inside
    them: spec §11's *no verdict changes in v1.3* is structural, and the one
    place that could have blurred it is the renderer (REQ-65, T-99, D123).
    Without the flag nothing here changes, which is what the CLI tests assert.
    """
    if isinstance(result, NoJurisdictionResult):
        rendered = {
            "result": "NO_JURISDICTION_TREE",
            "procedure_code": result.procedure_code,
            "state": result.state,
            "known_states": list(result.known_states),
            "note": "no criteria tree in the store governs this state; this is "
                    "not a denial and not a bad request (REQ-55, D100)",
        }
        return _with_suggestions(rendered, suggestions)
    if isinstance(result, NoPolicyResult):
        rendered = {
            "result": "NO_POLICY_FOUND",
            "procedure_code": result.procedure_code,
            "note": "no policy in the store governs this code; this is not a "
                    "denial (REQ-1, D26)",
        }
        return _with_suggestions(rendered, suggestions)
    rendered = result.model_dump(mode="json")
    rendered["model_calls"] = result.model_calls
    rendered["gap_list"] = [g.model_dump(mode="json") for g in result.gap_list]
    # REQ-39: advisory, and a separate list from the gap list on purpose — a
    # disagreement between two recorded values is not something to go collect,
    # so it must not read as a gap (D14).
    rendered["discrepancies"] = [
        d.model_dump(mode="json") for d in result.discrepancies
    ]
    rendered["total_input_tokens"] = result.total_input_tokens
    rendered["total_output_tokens"] = result.total_output_tokens
    rendered["total_wall_time_ms"] = round(result.total_wall_time_ms, 1)
    return _with_suggestions(rendered, suggestions)


def _with_suggestions(rendered: dict, suggestions: dict | None) -> dict:
    """Attach the review under its own key, or leave the document untouched."""
    if suggestions is not None:
        rendered["icd_suggestions"] = suggestions
    return rendered


def _load_env() -> None:
    """Read `pa_agent/agent/.env` for a live run. Gitignored, placeholder-only
    in the tracked tree (working rule 10)."""
    if not ENV_PATH.exists():
        raise SystemExit(
            f"no {ENV_PATH.relative_to(REPO_ROOT)}; a live extraction run needs "
            "GOOGLE_API_KEY. Use --extraction recorded to answer from T-15's "
            "recording for nothing."
        )
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_runner(
    mode: str, recording: Path, tool_fetch: bool, patient_store, tier: str
):
    """Construct the model leaf. The only place a runner is chosen (REQ-52)."""
    if mode == "recorded":
        if not recording.exists():
            raise SystemExit(
                f"no recording at {recording}; run "
                "`python scripts/run_extraction.py` (it spends model calls) or "
                "pass --extraction direct"
            )
        payload = json.loads(recording.read_text(encoding="utf-8"))
        return RecordedExtractionRunner.from_records(
            payload["notes"], model=payload.get("model")
        )

    _load_env()
    from pa_agent.tiers import client_for

    client = client_for(tier)

    if mode == "direct":
        from pa_agent.runners import DirectExtractionRunner

        return DirectExtractionRunner(client)

    # Imported here, not at module scope: `pa_agent.agent` is the subpackage that
    # holds the ADK, and pulling it into every CLI invocation would put
    # `google.adk` on the import path of a request that answers E3 with no model
    # at all (D16's boundary, D62).
    from pa_agent.agent.extraction_agent import AdkExtractionRunner

    return AdkExtractionRunner(
        client=client, patient_store=patient_store, tool_fetch=tool_fetch
    )



def _build_verifier(mode: str, recording: Path, tier: str):
    """Construct Article V's checker beside the model leaf (T-17, D78).

    It follows `--extraction`: the recorded leaf gets the recorded verifier —
    zero calls, same answer twice — and a live leaf gets a live verifier,
    because live extraction produces claims no recording has seen and a replay
    would refuse them (D31: a miss raises, never defaults).
    """
    if mode == "recorded":
        if not recording.exists():
            raise SystemExit(
                f"no verifier recording at {recording}; run "
                "`python scripts/run_verifier_measurement.py` (it spends model "
                "calls) or pass --extraction direct"
            )
        payload = json.loads(recording.read_text(encoding="utf-8"))
        return RecordedVerifierRunner.from_records(
            payload["claims"], model=payload.get("model")
        )

    _load_env()
    from pa_agent.tiers import client_for

    from pa_agent.verifier import LiveVerifierRunner

    return LiveVerifierRunner(client_for(tier))


def _build_quote_runner(
    mode: str, recording: Path, tool_fetch: bool, patient_store, tier: str
):
    """Construct the review's model leaf, beside the other two (T-98, D122).

    It follows `--extraction` for the reason `_build_verifier` does: a recorded
    leaf gets the recorded quote runner — zero calls, the same answer twice —
    and a live leaf gets a live one, because a live run asks about notes no
    recording has seen. A third word for this would make representable a
    configuration no recording holds, live quotes over replayed extraction,
    which is `T-110`'s measurement and not a demo path *(D123)*.

    A missing recording raises rather than returning `None`: a review with no
    quote source on a note-bearing chart is "the system did not look", and the
    one thing it must never become is red (D31, D90).
    """
    if mode == "recorded":
        if not recording.exists():
            raise SystemExit(
                f"no quote recording at {recording}; run "
                "`python scripts/run_quote_measurement.py` (it spends model "
                "calls) or drop --suggest"
            )
        payload = json.loads(recording.read_text(encoding="utf-8"))
        return RecordedQuoteRunner.from_records(
            payload["notes"], payload["rows_asked"], model=payload.get("model")
        )

    _load_env()
    from pa_agent.tiers import client_for

    client = client_for(tier)

    if mode == "direct":
        from pa_agent.quotes import DirectQuoteRunner

        return DirectQuoteRunner(client)

    # Late, for D16's boundary: `google.adk` stays off the import path of a
    # request that answers with no model at all.
    from pa_agent.agent.quote_agent import AdkQuoteRunner

    return AdkQuoteRunner(
        client=client, patient_store=patient_store, tool_fetch=tool_fetch
    )


def _review_block(
    *,
    result: Determination | NoPolicyResult | NoJurisdictionResult,
    patient_id: str,
    policy_store,
    patient_store,
    knowledge_store,
    quote_runner,
    verifier,
) -> dict:
    """The `icd_suggestions` block for one request (REQ-63 through REQ-68).

    The assembly lives here because `history.py` takes its value sets and its
    ingredient expansion as **arguments** — that is what keeps the review off
    both planes — and `cli.py` is the composition root REQ-41 names, already
    the only declared module that reaches both (D123). The eval harness
    assembles the same facts for the rows that label a review.

    A request that short-circuits has no governing tree, so `criteria` is empty
    and `policy_version_id` is `None`: the review is a report about the chart
    rather than about the policy, and an uncovered code is the case most worth
    answering. Every `would_affect` is then empty carrying that as its reason,
    because REQ-66 forbids an empty list alone.
    """
    tree = (
        policy_store.get_tree(result.policy_version_id)
        if isinstance(result, Determination)
        else None
    )
    criteria = tuple(tree.criteria) if tree is not None else ()
    value_sets = {}
    for criterion in criteria:
        constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
        if constant is not None:
            value_sets[str(constant.value)] = policy_store.get_value_set(
                str(constant.value)
            )

    rows = knowledge_store.get_medication_effect_rows()
    run = history.run_review(
        quote_runner=quote_runner,
        verifier=verifier,
        patient_id=patient_id,
        policy_version_id=tree.policy_version_id if tree is not None else None,
        rows=rows,
        products={
            row.ingredient.code: knowledge_store.get_ingredient_products(
                row.ingredient.code
            )
            for row in rows
        },
        medications=patient_store.get_medications(patient_id),
        conditions=patient_store.get_conditions(patient_id),
        observations=patient_store.get_observations(patient_id),
        criteria=criteria,
        value_sets=value_sets,
        notes=patient_store.get_notes(patient_id),
    )

    review = run.review
    return {
        "policy_version_id": review.policy_version_id,
        "suggestions": [s.model_dump(mode="json") for s in review.suggestions],
        "withheld": [w.model_dump(mode="json") for w in review.withheld],
        "by_colour": review.by_colour,
        "verifier_rejections": [
            {"row_id": row_id, "reason": reason} for row_id, reason in run.rejections
        ],
        # Article X on the review's own turns, and REQ-68's rule that they are
        # reported *apart* from determination cost: these never enter the
        # document's own `model_calls`, because the review sits beside the
        # determination and not inside it (D119, D122).
        "notes_consulted": run.notes_consulted,
        "model_calls": run.model_calls,
        "total_input_tokens": run.total_input_tokens,
        "total_output_tokens": run.total_output_tokens,
        "total_wall_time_ms": round(run.total_wall_time_ms, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pa_agent.cli",
        description="Prior authorization determination for one request.",
    )
    parser.add_argument("--patient", required=True, help="patient identifier")
    parser.add_argument("--procedure", required=True, help="procedure code")
    parser.add_argument(
        "--extraction",
        choices=("recorded", "direct", "adk"),
        default="recorded",
        help="which model leaf reads the notes (default: recorded, zero calls)",
    )
    parser.add_argument(
        "--recording",
        type=Path,
        default=DEFAULT_RECORDING,
        help="recording to replay under --extraction recorded",
    )
    parser.add_argument(
        "--tool-fetch",
        action="store_true",
        help="under --extraction adk, let the agent fetch the note through its "
             "declared tool instead of receiving it in the message",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="two-letter state the request is resolved under (REQ-55). Default: "
             "read from the patient's bundle. An explicit value wins, so the same "
             "chart can be adjudicated under another MAC's tree (D100).",
    )
    parser.add_argument(
        "--tier",
        choices=TIERS,
        default=MEASURED_TIER,
        help="which tier a live leaf calls (default: the development tier). "
             "Ignored by --extraction recorded, which spends nothing. D5 runs "
             "any demo on the tier that does not train on submitted data.",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="the date windows are measured from (default: today). Pin it for a "
             "reproducible answer — every recency verdict moves with this.",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="also emit the medical-history review as an `icd_suggestions` "
             "block beside the determination (REQ-63 to REQ-68). Changes no "
             "verdict, span or gap entry. Its quote leaf follows --extraction, "
             "so the default replays the committed recording for zero calls.",
    )
    parser.add_argument(
        "--history-recording",
        type=Path,
        default=DEFAULT_HISTORY_RECORDING,
        help="quote recording replayed under --suggest with --extraction "
             "recorded",
    )
    args = parser.parse_args(argv)

    store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    runner = _build_runner(
        args.extraction, args.recording, args.tool_fetch, patient_store, args.tier
    )
    verifier = _build_verifier(
        args.extraction, DEFAULT_VERIFIER_RECORDING, args.tier
    )

    suggestions: dict | None = None
    try:
        result = determine(
            store,
            args.procedure,
            patient_id=args.patient,
            patient_store=patient_store,
            as_of=args.as_of or date.today(),
            extraction_runner=runner,
            verifier=verifier,
            state=args.state,
        )
        if args.suggest:
            # Inside the same `try`, so a review over a patient the stores
            # cannot resolve is the bad request it is (exit 1) rather than a
            # traceback after a printed determination.
            suggestions = _review_block(
                result=result,
                patient_id=args.patient,
                policy_store=store,
                patient_store=patient_store,
                knowledge_store=LocalKnowledgeStore(),
                quote_runner=_build_quote_runner(
                    args.extraction,
                    args.history_recording,
                    args.tool_fetch,
                    patient_store,
                    args.tier,
                ),
                verifier=verifier,
            )
    except NotImplementedError as exc:
        # The message names what is missing (D27's pattern).
        print(str(exc), file=sys.stderr)
        return 2
    except DeterminationAborted as exc:
        # REQ-29 (T-29, D76): the criterion id and its `error_code` go to
        # stderr, one line per errored criterion, and nothing goes to stdout —
        # a partial answer printed anyway would be a determination emitted
        # over an `ERROR` with extra steps (REQ-24, REQ-26).
        for errored in exc.results:
            code = (
                errored.error_code.value if errored.error_code else "UNCLASSIFIED"
            )
            print(
                f"criterion {errored.criterion_id}: {code}: "
                f"{errored.error_detail}",
                file=sys.stderr,
            )
        if exc.attempts is not None:
            print(f"attempts: {exc.attempts}", file=sys.stderr)
        return 3
    except KeyError as exc:
        # A request the stores cannot resolve — an unknown patient, most
        # likely. A bad request is not an answer and not an unbuilt path.
        print(f"bad request: {exc.args[0]}", file=sys.stderr)
        return 1

    print(json.dumps(_render(result, suggestions), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
