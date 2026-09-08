"""T-25 — the CLI: request in, determination out.

    python -m pa_agent.cli --patient X --procedure 43842

Prints one JSON document to stdout. For a determination it carries the outcome,
the `policy_version_id` (REQ-4), the coverage claim it cites (Art. III), the
gap list (REQ-21) and the model-call counter — a computed property, added
explicitly because `model_dump` only serializes fields. For a code no policy
governs it prints a `NO_POLICY_FOUND` result and still exits zero: a
deterministic answer is not an error (D32).

Exit codes: 0 for an answer, 2 for a path the system has not built yet — the
`NotImplementedError` message, which names its task, goes to stderr.

This module is where the one `LocalPolicyStore` is constructed. Everything
below it reaches data through the port and holds no path (REQ-41).
"""

from __future__ import annotations

import argparse
import json
import sys

from pa_agent.contracts import Determination
from pa_agent.determination import NoPolicyResult, determine
from pa_agent.stores.policy import LocalPolicyStore


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
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pa_agent.cli",
        description="Prior authorization determination for one request.",
    )
    parser.add_argument("--patient", required=True, help="patient identifier")
    parser.add_argument("--procedure", required=True, help="procedure code")
    args = parser.parse_args(argv)

    store = LocalPolicyStore()
    try:
        result = determine(store, args.procedure, patient_id=args.patient)
    except NotImplementedError as exc:
        # The message names the task that unblocks this path (D27's pattern).
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(_render(result), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
