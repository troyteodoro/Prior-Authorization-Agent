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

from pa_agent.contracts import Determination, DeterminationAborted
from pa_agent.determination import NoPolicyResult, determine
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.verifier import RecordedVerifierRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECORDING = REPO_ROOT / "eval" / "extraction" / "results.json"
DEFAULT_VERIFIER_RECORDING = REPO_ROOT / "eval" / "verifier" / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"


def _render(result: Determination | NoPolicyResult) -> dict:
    if isinstance(result, NoPolicyResult):
        return {
            "result": "NO_POLICY_FOUND",
            "procedure_code": result.procedure_code,
            "note": "no policy in the store governs this code; this is not a "
                    "denial (REQ-1, D26)",
        }
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


def _build_runner(mode: str, recording: Path, tool_fetch: bool, patient_store):
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
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

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



def _build_verifier(mode: str, recording: Path):
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
    from google import genai

    from pa_agent.verifier import LiveVerifierRunner

    return LiveVerifierRunner(genai.Client(api_key=os.environ["GOOGLE_API_KEY"]))


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
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="the date windows are measured from (default: today). Pin it for a "
             "reproducible answer — every recency verdict moves with this.",
    )
    args = parser.parse_args(argv)

    store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    runner = _build_runner(
        args.extraction, args.recording, args.tool_fetch, patient_store
    )
    verifier = _build_verifier(args.extraction, DEFAULT_VERIFIER_RECORDING)

    try:
        result = determine(
            store,
            args.procedure,
            patient_id=args.patient,
            patient_store=patient_store,
            as_of=args.as_of or date.today(),
            extraction_runner=runner,
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

    print(json.dumps(_render(result), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
