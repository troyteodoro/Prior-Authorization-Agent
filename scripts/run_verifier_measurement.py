"""T-17 — run the live verifier over every gate-reachable claim and record it (D78).

    python scripts/run_verifier_measurement.py            # spends one model call per unique claim
    python scripts/run_verifier_measurement.py --rescore  # re-checks the recording, no calls

`pytest tests/test_verifier.py` verifies what this writes and spends nothing —
T-15's split (D17, D45): a gate that calls a model is slow, rate-limited, and
answers differently on two invocations.

**Enumeration is free.** Every determination any zero-cost gate can run is a
row in `eval/cases.json` — the agentic differential's oracle side reads the
same six patients at the same procedure and date — so this script replays
those determinations with a collecting verifier that records each unique claim
payload and accepts. The collector is the enumeration device and lives here,
in the one script already classified as spending model calls; it is not
importable from `pa_agent/`, where an accept-all verifier would be the silent
skip D78 refuses.

**The live pass is the spend**: one call per unique claim digest, on the tier
named in the recording (D5). A rejection is a finding, not an answer (D78):
the recording is still written so the claim can be reviewed, but the exit code
says rejections are present and nothing should be committed until each one is
resolved.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pa_agent.determination import determine  # noqa: E402
from pa_agent.model_pin import (  # noqa: E402
    VERIFIER_MEASURED_TIER,
    VERIFIER_MODEL,
)
from pa_agent.runners import RecordedExtractionRunner  # noqa: E402
from pa_agent.stores.patient import LocalPatientStore  # noqa: E402
from pa_agent.stores.policy import LocalPolicyStore  # noqa: E402
from pa_agent.verifier import (  # noqa: E402
    PROMPT_VERSION,
    LiveVerifierRunner,
    VerifierAnswer,
    VerifierFailure,
    VerifierOutputError,
    claim_digest,
)

CASES_PATH = REPO_ROOT / "eval" / "cases.json"
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
OUT_DIR = REPO_ROOT / "eval" / "verifier"
OUT_PATH = OUT_DIR / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

#: The harness clock (eval/run_eval.py's EVAL_AS_OF and the agentic
#: differential's AS_OF are this same date). A case row may override it.
DEFAULT_AS_OF = date(2026, 9, 1)

RETRIES = 4  # the free tier's 429/503 behavior under load (D5, D20)

EXIT_OK = 0
EXIT_REJECTIONS = 4


def load_env() -> None:
    """Read the gitignored .env unless the credential is already exported. No
    credential is ever written to a tracked file (working rule 10)."""
    if os.environ.get("GOOGLE_API_KEY"):
        return
    if not ENV_PATH.exists():
        sys.exit(
            f"no GOOGLE_API_KEY in the environment and no "
            f"{ENV_PATH.relative_to(REPO_ROOT)}; this run needs one. "
            "--rescore re-checks the committed recording for nothing."
        )
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class CollectingVerifier:
    """Accepts everything and remembers what it was shown.

    The enumeration device, deliberately confined to this script (see the
    module docstring). Order of first sight is preserved so the recording
    reads in determination order.
    """

    name = "collecting"

    def __init__(self) -> None:
        self.claims: dict[str, dict] = {}

    def run(self, payload: dict) -> VerifierAnswer:
        digest = claim_digest(payload)
        if digest not in self.claims:
            self.claims[digest] = payload
        return VerifierAnswer(accept=True, reason="enumeration pass")


def enumerate_claims() -> dict[str, dict]:
    """Every unique claim payload the gates' determinations produce. Free."""
    policy_store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    runner = RecordedExtractionRunner.from_records(
        recording["notes"], model=recording.get("model")
    )
    collector = CollectingVerifier()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    seen: set[tuple] = set()
    for case in cases:
        if case.get("procedure_code") is None:
            continue
        as_of = (
            date.fromisoformat(case["as_of"])
            if case.get("as_of")
            else DEFAULT_AS_OF
        )
        key = (case.get("patient_id"), case["procedure_code"], as_of)
        if key in seen:
            continue
        seen.add(key)
        determine(
            policy_store,
            case["procedure_code"],
            patient_id=case.get("patient_id"),
            patient_store=patient_store,
            as_of=as_of,
            extraction_runner=runner,
            verifier=collector,
        )
    return collector.claims


def measure() -> int:
    load_env()
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    live = LiveVerifierRunner(client, model=VERIFIER_MODEL)

    claims = enumerate_claims()
    print(
        f"{len(claims)} unique claims · model {VERIFIER_MODEL} · "
        f"tier {VERIFIER_MEASURED_TIER} · prompt {PROMPT_VERSION}"
    )

    records: list[dict] = []
    rejections: list[dict] = []
    for index, (digest, payload) in enumerate(claims.items(), start=1):
        criterion_id = payload["criterion"]["id"]
        label = f"{criterion_id}/{payload['verdict']}"
        answer = None
        for attempt in range(1, RETRIES + 1):
            try:
                answer = live.run(payload)
                break
            except VerifierOutputError as exc:
                if exc.reason is not VerifierFailure.CALL_FAILED or attempt == RETRIES:
                    raise
                wait = 15 * attempt
                print(f"  [{index}] {label}: {exc.reason.value}, retry in {wait}s")
                time.sleep(wait)
        assert answer is not None
        record = {
            "digest": digest,
            "criterion_id": criterion_id,
            "verdict": payload["verdict"],
            "payload": payload,
            "accept": answer.accept,
            "reason": answer.reason,
            "raw": answer.raw,
            "metrics": answer.metrics.model_dump(mode="json")
            if answer.metrics
            else None,
        }
        records.append(record)
        marker = "accept" if answer.accept else "REJECT"
        print(f"  [{index}/{len(claims)}] {label}: {marker}")
        if not answer.accept:
            rejections.append(record)

    payload_out = {
        "task": "T-17",
        "model": VERIFIER_MODEL,
        "tier": VERIFIER_MEASURED_TIER,
        "prompt_version": PROMPT_VERSION,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "claims": records,
        "aggregate": aggregate(records),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload_out, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {OUT_PATH.relative_to(REPO_ROOT)}")
    report(payload_out)

    if rejections:
        # D78: a rejection is a finding. The recording exists so the claim can
        # be reviewed; the exit code says do not commit until it is resolved.
        print(f"\n{len(rejections)} REJECTION(S) — review before committing:")
        for record in rejections:
            print(
                f"  {record['criterion_id']}/{record['verdict']}: "
                f"{record['reason']}"
            )
        return EXIT_REJECTIONS
    return EXIT_OK


def aggregate(records: list[dict]) -> dict:
    metrics = [r["metrics"] for r in records if r.get("metrics")]
    return {
        "claims": len(records),
        "accepted": sum(1 for r in records if r["accept"]),
        "rejected": sum(1 for r in records if not r["accept"]),
        "model_calls": len(metrics),
        "input_tokens": sum(m["input_tokens"] for m in metrics),
        "output_tokens": sum(m["output_tokens"] for m in metrics),
        "wall_time_ms": round(sum(m["wall_time_ms"] for m in metrics), 1),
    }


def report(payload: dict) -> None:
    agg = payload["aggregate"]
    print(
        f"  {agg['claims']} claims · {agg['accepted']} accepted · "
        f"{agg['rejected']} rejected · {agg['input_tokens']} in / "
        f"{agg['output_tokens']} out tokens"
    )


def rescore() -> int:
    """Re-derive every number from the recording. **Spends nothing.**

    Re-hashes every stored payload against its digest key (a tampered record
    fails on the claim it lies about), recomputes the aggregate, and checks
    the recorded model is the pin — the same three guarantees the suite
    asserts, runnable standalone.
    """
    if not OUT_PATH.exists():
        sys.exit(f"nothing to rescore; no recording at {OUT_PATH}")
    payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))

    problems: list[str] = []
    if payload.get("model") != VERIFIER_MODEL:
        problems.append(
            f"recording names {payload.get('model')!r}; the pin is "
            f"{VERIFIER_MODEL!r} (D20: the two move together)"
        )
    if payload.get("prompt_version") != PROMPT_VERSION:
        problems.append(
            f"recording was measured on prompt {payload.get('prompt_version')!r}; "
            f"the code builds {PROMPT_VERSION!r} — a changed prompt is a new "
            "measurement (D45)"
        )
    for record in payload.get("claims", []):
        actual = claim_digest(record["payload"])
        if actual != record["digest"]:
            problems.append(
                f"claim {record['digest'][:12]}: payload hashes to "
                f"{actual[:12]}; the record no longer says what was measured"
            )

    payload["aggregate"] = aggregate(payload.get("claims", []))
    OUT_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report(payload)

    if problems:
        for problem in problems:
            print(f"  PROBLEM: {problem}", file=sys.stderr)
        return 1
    print("  recording internally coherent; model matches the pin")
    return EXIT_OK


def main() -> int:
    if "--rescore" in sys.argv:
        return rescore()
    return measure()


if __name__ == "__main__":
    raise SystemExit(main())
