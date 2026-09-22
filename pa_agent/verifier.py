"""T-17 — Article V's verifier: one claim at a time, and no reasoning. *(D78)*

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
package is the silent skip Article V would not survive (D78).
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
#: explicitly while keeping threshold contradictions rejectable (D78).
#: v3: v2 measured 25/27, both rejections false and both shortfall-type
#: NOT_MET claims — structurally unverifiable blind, because the shortfall is
#: code's arithmetic over a record the verifier must not see. v3 states the
#: asymmetry: MET is judged from the quote; a NOT_MET is rejected only on a
#: direct, arithmetic-free contradiction. Measured 27/27.
#: v4: v3's instruction unchanged; the payload dropped `as_of` (D78). v3's
#: asymmetry bars the verifier from date arithmetic — the only use the field
#: ever had — and carrying it date-bound every claim digest, which broke the
#: CLI's default (as-of today) against a recording measured at the harness
#: clock. A changed model input is a new measurement (D45).
#: v5: v4 measured 38/38 on AI Studio and 36/38 on Vertex, both rejections
#: false and both on a value-set membership criterion — the verifier decided
#: whether quoted conditions belong to a set the payload names and never
#: shows it. That is v2's and v3's failure one category over: Article II's
#: arithmetic over data Article V hides. v5 bars it, keeping v4's asymmetry
#: and every rejection that does not need the set (D115).
#: v6: v5 measured 37/38 on AI Studio with one false rejection — a
#: shortfall-type `NOT_MET` rejected with a reason that *agreed* with it
#: ("the quote is from June 2025, outside the 12-month window"), which is
#: what a NOT_MET cites. v3's asymmetry already forbids that and stopped
#: being the last thing read when v5's paragraph landed after it, so v6
#: states the accepting case outright instead of only the forbidden one
#: (D115). Both rounds are recorded; neither is a re-run of the other.
PROMPT_VERSION = "verifier-v6"

VERIFIER_TEMPERATURE = 0.0

#: The medical-history review's claim (T-98, D122): a candidate condition and
#: the note passages quoted for it. Its own version because it is its own
#: instruction and its own recording — `eval/verifier/history_results.json`,
#: which T-110 measures; nothing produces a yellow on the committed corpus, so
#: v1 is built, wired and unmeasured, and `RecordedVerifierRunner` raises on
#: any digest it has not got rather than defaulting a yellow to accepted.
HISTORY_PROMPT_VERSION = "history-verifier-v1"

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
    "missed. Evidence that is stale, short, or otherwise consistent with the "
    "criterion failing is exactly what a NOT_MET is expected to cite: that "
    "is an accept, not a rejection.\n\n"
    "A criterion may name a value set in its constants. That set was "
    "compiled from the policy and is not shown to you, and membership was "
    "computed by code against it in a declared code system. So never reject "
    "a claim because a quoted diagnosis, medication or procedure does not "
    "look to you like a member of the named set, and never substitute your "
    "own idea of what that set contains: that is the same re-adjudication as "
    "doing the arithmetic yourself. Reject such a claim only if the quote is "
    "not evidence about this criterion's subject at all — a prescription "
    "quoted for a criterion about diagnoses, or a passage of prose quoted "
    "where the criterion is about the coded record.\n\n"
    "Respond with JSON: {\"accept\": true|false, \"reason\": \"one sentence\"}."
)

#: What the model is asked about a history candidate, verbatim. One condition,
#: its passages, and nothing else — no drug, no code, no colour, no chart —
#: because the question is whether the passages document the patient having
#: the condition, and every other fact would be reasoning by another door.
HISTORY_INSTRUCTION = (
    "You are a blind verifier for a prior-authorization system. You will be "
    "given exactly one claim: a clinical condition (its name and the title of "
    "the diagnosis code it would be recorded under), and the verbatim text "
    "quoted from a clinical note as documentation that the patient has that "
    "condition. You see nothing else: no medication list, no laboratory "
    "values, no other conditions, no reasoning, no chart.\n\n"
    "Your job is citation fidelity, not diagnosis: accept the claim if every "
    "quoted passage documents that this patient has this condition — a "
    "finding, an assessment, a diagnosis, or a result the note itself calls "
    "abnormal. Reject it if any passage is about something else: a different "
    "condition, a medication that can cause the condition, a plan to monitor "
    "or screen for it, a family history of it, a normal result, or the "
    "condition named as a risk, a possibility, or something to rule out. "
    "Never infer the condition from a number, never apply a threshold, and "
    "never judge whether the condition is correctly coded: the code was "
    "chosen by a reviewed table and is not yours to re-adjudicate.\n\n"
    "Respond with JSON: {\"accept\": true|false, \"reason\": \"one sentence\"}."
)


# --------------------------------------------------------------------------
# Failure classification — REQ-18a's loop, kept distinct from REQ-18's
# --------------------------------------------------------------------------


class VerifierFailure(str, Enum):
    """Why no verifier answer could be produced. Mirrors `ExtractionFailure`;
    mapped onto `ErrorCode` at the workflow boundary (D76, D78)."""

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
    which broke replay for any request at another one (D78). The blindness
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


def build_history_claim_payload(candidate, quotes: list[str]) -> dict:
    """One medical-history claim, and everything the verifier will ever see
    about it (T-98, D122): the candidate's condition by display and the title
    of its ICD-10 code, and the passages sliced mechanically from the note.

    `candidate` is an `IcdSuggestion` or a `MedicationEffectRow` — both carry
    the two fields read here. Excludes, deliberately: the row id (it embeds the
    drug), the medication and ingredient (a prescription is never evidence of
    the condition, D119), the colour (the verifier does not grade evidence),
    the signal and any measurement (a threshold is Python's), the ICD-10 code
    itself (a code invites re-adjudication of coding), `would_affect`, and
    every chart fact. The top-level key set differs from a criterion claim's,
    so no history digest can collide with one of the criterion recording's.
    """
    return {
        "candidate": {
            "effect": candidate.effect_display,
            "icd10_title": candidate.icd10_title,
        },
        "quotes": list(quotes),
    }


def _describe(payload: dict) -> str:
    """What a miss is about, for the message: a criterion claim names its
    criterion and verdict; a history claim names its candidate (D122)."""
    if "criterion" in payload:
        return (
            f"criterion {payload.get('criterion', {}).get('id', '?')}, "
            f"verdict {payload.get('verdict', '?')}"
        )
    if "candidate" in payload:
        return f"candidate {payload.get('candidate', {}).get('effect', '?')}"
    return "a payload of unknown shape"


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
    zero tokens would understate what the answer cost (Art. X, D78)."""

    accept: bool
    reason: str
    metrics: CallMetrics | None = None
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


@runtime_checkable
class VerifierRunner(Protocol):
    """One claim payload in, accept-or-reject out (Article V, D78).

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

    def __init__(
        self, client, model: str = VERIFIER_MODEL, *, instruction: str = INSTRUCTION
    ) -> None:
        self._client = client
        self._model = model
        # The criterion instruction by default, so the measured path is
        # byte-identical; `HISTORY_INSTRUCTION` for a history claim (D122).
        self._instruction = instruction

    @property
    def model(self) -> str:
        return self._model

    @property
    def instruction(self) -> str:
        return self._instruction

    def run(self, payload: dict) -> VerifierAnswer:
        contents = (
            f"{self._instruction}\n\nCLAIM:\n"
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
            # response (D8, D76's classification).
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
                f"({_describe(payload)}); run "
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
