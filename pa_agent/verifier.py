"""T-17 — Article V's verifier: one claim at a time, and no reasoning. *(D77)*

The verifier receives the criterion, the cited span, and the claimed verdict —
and nothing else (REQ-17). `build_claim_payload` is the blindness boundary: its
output *is* the whole model input, so the property Article V legislates is a
property of a pure function a test can assert on, not of a prompt a review has
to read. The quotes in the payload are recovered by slicing each span from its
source document (`DocumentIndex.slice`), never taken from model output — D18's
lesson applied forward.

Three implementations behind one port, mirroring `runners.py` (REQ-52's
pattern):

- `LiveVerifierRunner` — raw `google-genai`, injected client, so nothing here
  reads a credential or names a tier (D5: the caller chooses).
- `RecordedVerifierRunner` — replays `eval/verifier/results.json`, keyed by
  claim digest. A miss raises: an accept is a well-formed answer every
  downstream test agrees with, and a reject is a manufactured gap, so neither
  is an acceptable default (D31, D39).
- `NullVerifierRunner` — raises on any call; the structural proof that a path
  was never reached (A4's pattern).

The accept-all fake that unit tests of *other* components need lives under
`tests/`, deliberately not importable from here: an accept-all verifier in the
package is the silent skip Article V would not survive (D77).
"""

from __future__ import annotations

import hashlib
import json
import time

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from pa_agent.contracts import CallMetrics, Criterion
from pa_agent.model_pin import VERIFIER_MODEL

#: Versioned because a changed prompt is a new measurement (D45's rule). The
#: recording carries this, and the replay refuses a payload built by another.
#: v2: v1 measured 26/27 with one false rejection — the model re-did c2's
#: months-between arithmetic backwards — so v2 barred date arithmetic
#: explicitly while keeping threshold contradictions rejectable (D77).
#: v3: v2 measured 25/27, both rejections false and both shortfall-type
#: NOT_MET claims — structurally unverifiable blind, because the shortfall is
#: code's arithmetic over a record the verifier must not see. v3 states the
#: asymmetry: MET is judged from the quote; a NOT_MET is rejected only on a
#: direct, arithmetic-free contradiction. Measured 27/27.
#: v4: v3's instruction unchanged; the payload dropped `as_of` (D77). v3's
#: asymmetry bars the verifier from date arithmetic — the only use the field
#: ever had — and carrying it date-bound every claim digest, which broke the
#: CLI's default (as-of today) against a recording measured at the harness
#: clock. A changed model input is a new measurement (D45).
PROMPT_VERSION = "verifier-v4"

VERIFIER_TEMPERATURE = 0.0

#: What the model is asked, verbatim. The payload follows as JSON; the model
#: never sees more than `build_claim_payload` put there.
INSTRUCTION = (
    "You are a blind verifier for a prior-authorization system. You will be "
    "given exactly one claim: a policy criterion (its label and its "
    "operational constants), a claimed verdict (MET or NOT_MET), and the "
    "verbatim text quoted from source documents as the evidence for that "
    "verdict. You see nothing else: no other criteria, no reasoning, no "
    "chart, no dates beyond what the quotes themselves contain.\n\n"
    "Your job is citation fidelity, not re-adjudication: counting, "
    "thresholds and date arithmetic were computed deterministically by code, "
    "and a criterion that aggregates over several quotes (a count of months, "
    "a documentation rate) will show you the instances, not the arithmetic. "
    "The two verdicts are not symmetric. A MET claim says the quotes show "
    "the criterion satisfied: accept it if every quote is evidence about "
    "this criterion's subject and is consistent with satisfaction; reject "
    "it if a quote is about something else or states a value on the wrong "
    "side of a threshold named in the constants (a BMI of 32 quoted for MET "
    "against a threshold of 35 or more). A NOT_MET claim is different: it "
    "cites the best evidence that was found, and the shortfall itself — "
    "months counted, recency windows measured — was computed by code "
    "over the full record, which you cannot see. Never reject a NOT_MET "
    "because the quotes look sufficient to you, and never perform date or "
    "count arithmetic yourself. Reject a NOT_MET only if a quote is about "
    "something else entirely, or by itself — with no arithmetic — directly "
    "shows the criterion satisfied, such as a value on the satisfying side "
    "of a named threshold quoted as evidence that the threshold was "
    "missed.\n\n"
    "Respond with JSON: {\"accept\": true|false, \"reason\": \"one sentence\"}."
)


# --------------------------------------------------------------------------
# Failure classification — REQ-18a's loop, kept distinct from REQ-18's
# --------------------------------------------------------------------------


class VerifierFailure(str, Enum):
    """Why no verifier answer could be produced. Mirrors `ExtractionFailure`;
    mapped onto `ErrorCode` at the workflow boundary (D75, D77)."""

    CALL_FAILED = "call_failed"          # network/API — a second attempt may differ
    UNPARSEABLE = "unparseable"          # response is not JSON — terminal (D8)
    SCHEMA_INVALID = "schema_invalid"    # JSON but not our schema — terminal
    NOT_RECORDED = "not_recorded"        # replay asked for a claim never measured
    RECORD_TAMPERED = "record_tampered"  # a record's payload no longer hashes to its key


class VerifierOutputError(ValueError):
    """No verifier answer, classified. A rejection is NOT this — a rejection
    is an answer (REQ-18); this is the inability to get one (REQ-18a)."""

    def __init__(self, reason: VerifierFailure, message: str) -> None:
        super().__init__(f"[{reason.value}] {message}")
        self.reason = reason
        self.message = message


# --------------------------------------------------------------------------
# The claim payload — the blindness boundary (Article V, REQ-17)
# --------------------------------------------------------------------------


def build_claim_payload(
    criterion: Criterion, verdict: str, quotes: list[str]
) -> dict:
    """One claim, and everything the verifier will ever see about it.

    Includes: the criterion's id, label, and its constants' names, values and
    comparisons — the compiled criterion is the one object Article VI already
    lets cross, and without the constants "BMI at or above the coverage
    threshold" is unjudgeable against "BMI 36.2".

    Excludes, deliberately: constant `note`s and `source`s (a note may name an
    eval case or an expected verdict — reasoning by another door), the
    criterion's `scoped_to` (names another criterion), the national floor, the
    reasoning trace, every other criterion, the rest of the chart — and the
    request's `as_of`, which rode along from v1 to v3 and left with the v3
    asymmetry rule: a verifier barred from date arithmetic has no use for a
    date, and a date in the payload binds every claim digest to one as-of,
    which broke replay for any request at another one (D77). The blindness
    test asserts on this function's output, so a leak is a failing test
    rather than a prompt-review finding.
    """
    return {
        "criterion": {
            "id": criterion.id,
            "label": criterion.label,
            "constants": {
                name: (
                    {"value": constant.value, "comparison": constant.comparison}
                    if constant.comparison is not None
                    else {"value": constant.value}
                )
                for name, constant in sorted(criterion.constants.items())
            },
        },
        "verdict": verdict,
        "quotes": list(quotes),
    }


def claim_digest(payload: dict) -> str:
    """The recording key: sha256 over the canonical JSON of the payload.

    The digest covers the whole claim, so a drifted document, a relabeled
    criterion, or a changed constant produces a *miss* — which raises — rather
    than replaying an answer measured against text the verifier never saw
    (D18's rule, applied to this recording)."""
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# The answer
# --------------------------------------------------------------------------


class VerifierResponse(BaseModel):
    """The live model's response, validated. Closed: accept and one sentence.

    `extra="forbid"` is the local check — an extra field (a confidence score
    nobody measured) fails validation rather than riding along. The schema
    sent to the API is `RESPONSE_SCHEMA` below, written by hand, because the
    schema pydantic generates for a closed model carries
    `additionalProperties: false`, which the Gemini `response_schema` field
    rejects with a 400 (measured, 2026-09-11)."""

    model_config = ConfigDict(extra="forbid")

    accept: bool
    reason: str


#: What the API is asked to shape the response to. Field-for-field the model
#: above; the strictness lives in local validation, not in the wire schema.
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "accept": {"type": "BOOLEAN"},
        "reason": {"type": "STRING"},
    },
    "required": ["accept", "reason"],
}


@dataclass(frozen=True)
class VerifierAnswer:
    """One claim, answered. `metrics` measures the call that produced it —
    carried through by the replay unchanged, because a replay that reported
    zero tokens would understate what the answer cost (Art. X, D77)."""

    accept: bool
    reason: str
    metrics: CallMetrics | None = None
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


@runtime_checkable
class VerifierRunner(Protocol):
    """One claim payload in, accept-or-reject out (Article V, D77).

    `name` is recorded on the run trace, so a determination's provenance says
    which implementation checked its citations.
    """

    name: str

    def run(self, payload: dict) -> VerifierAnswer:
        """Verify one claim.

        Raises `VerifierOutputError` when no answer could be produced. It must
        never map a failure onto accept or reject — a rejection is an answer
        about the claim, and a fault is not (REQ-18 vs REQ-18a).
        """
        ...


# --------------------------------------------------------------------------
# Live: the configuration the measurement records
# --------------------------------------------------------------------------


class LiveVerifierRunner:
    """`google-genai` with a native response schema, one call per claim.

    The client is injected, so nothing here reads a credential or names a tier
    (D5). The model defaults to the pin; passing another is a deliberate act
    the recording will name.
    """

    name = "live"

    def __init__(self, client, model: str = VERIFIER_MODEL) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def run(self, payload: dict) -> VerifierAnswer:
        contents = (
            f"{INSTRUCTION}\n\nCLAIM:\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=1)}"
        )
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": RESPONSE_SCHEMA,
                    "temperature": VERIFIER_TEMPERATURE,
                },
            )
        except Exception as exc:  # re-raised classified, never swallowed (REQ-27)
            raise VerifierOutputError(
                VerifierFailure.CALL_FAILED, f"{type(exc).__name__}: {exc}"
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        try:
            raw = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            # Terminal, one attempt: the same prompt returns the same invalid
            # response (D8, D75's classification).
            raise VerifierOutputError(
                VerifierFailure.UNPARSEABLE, f"response is not JSON: {exc}"
            ) from exc
        try:
            answer = VerifierResponse.model_validate(raw)
        except ValidationError as exc:
            raise VerifierOutputError(
                VerifierFailure.SCHEMA_INVALID,
                f"response failed schema validation: {exc}",
            ) from exc

        usage = getattr(response, "usage_metadata", None)
        metrics = CallMetrics(
            model=self._model,
            purpose="verification",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            wall_time_ms=elapsed_ms,
        )
        return VerifierAnswer(
            accept=answer.accept, reason=answer.reason, metrics=metrics, raw=raw
        )


# --------------------------------------------------------------------------
# Recorded: every gate verifies for nothing
# --------------------------------------------------------------------------


class RecordedVerifierRunner:
    """Replays the committed measurement. No model call.

    Takes records, **not a path** (REQ-41's rule, `RecordedExtractionRunner`'s
    precedent): the recording is read by the CLI, the eval harness and the
    tests, all of which already read files; `from_records()` turns a loaded
    recording into one of these.

    Keyed by claim digest. A miss raises `NOT_RECORDED` naming the measurement
    script — never a default answer in either direction (D31). Each record's
    stored payload is re-hashed against its key on replay, so a tampered
    recording fails on the claim it lies about rather than replaying the lie.
    """

    name = "recorded"

    def __init__(self, records: dict[str, dict], model: str | None = None) -> None:
        self._records = dict(records)
        self._model = model

    @classmethod
    def from_records(cls, claims: list[dict], model: str | None = None):
        """Build one from a recording's `claims[]`, as written by
        `scripts/run_verifier_measurement.py`."""
        records: dict[str, dict] = {}
        for record in claims:
            digest = record.get("digest")
            if digest:
                records[digest] = record
        return cls(records, model=model)

    @property
    def model(self) -> str | None:
        return self._model

    def __contains__(self, digest: str) -> bool:
        return digest in self._records

    def __len__(self) -> int:
        return len(self._records)

    def run(self, payload: dict) -> VerifierAnswer:
        digest = claim_digest(payload)
        record = self._records.get(digest)
        if record is None:
            raise VerifierOutputError(
                VerifierFailure.NOT_RECORDED,
                f"no recorded verification for claim {digest[:12]} "
                f"(criterion {payload.get('criterion', {}).get('id', '?')}, "
                f"verdict {payload.get('verdict', '?')}); run "
                "`python scripts/run_verifier_measurement.py` (it spends model "
                "calls) or pass a live runner",
            )
        recorded = claim_digest(record["payload"])
        if recorded != digest:
            raise VerifierOutputError(
                VerifierFailure.RECORD_TAMPERED,
                f"record {digest[:12]} holds a payload hashing to "
                f"{recorded[:12]}; the recording no longer says what was "
                "measured",
            )
        metrics = (
            CallMetrics.model_validate(record["metrics"])
            if record.get("metrics")
            else None
        )
        return VerifierAnswer(
            accept=bool(record["accept"]),
            reason=record.get("reason", ""),
            metrics=metrics,
            raw=record.get("raw", {}),
        )


class NullVerifierRunner:
    """Raises on any call. The runner a zero-verification proof hands in.

    Handing the workflow a verifier that cannot be called and getting an
    answer anyway proves no cited verdict was produced — the structural form
    of the claim, per `NullExtractionRunner`'s pattern."""

    name = "null"

    def __init__(self, note: str = "") -> None:
        self._note = note

    def run(self, payload: dict) -> VerifierAnswer:
        raise AssertionError(
            "NullVerifierRunner was reached: this path was asserted to verify "
            f"nothing and it verified. {self._note}".strip()
        )
