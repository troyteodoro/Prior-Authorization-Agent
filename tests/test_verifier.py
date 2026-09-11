"""T-17 — Article V's verifier is blind, and a rejection is an abstention (D77).

The exit condition's four claims, each pinned here:

1. a mismatched span and verdict is rejected — via the port's mechanics, on a
   fake LLM (T-62's no-network pattern), because what a *real* model rejects
   is a measured fact recorded by `scripts/run_verifier_measurement.py`, not a
   unit test;
2. the verifier's input contains no reasoning trace and no other criterion —
   asserted on `build_claim_payload`, whose output *is* the model input;
3. a rejection resolves the criterion to `INSUFFICIENT_EVIDENCE` with
   `gap_reason` `VERIFIER_REJECTED`, spends exactly one verifier call, and
   still emits a `Determination`;
4. the committed recording replays: model equals the pin, every digest
   recomputes, and every gate-reachable claim is recorded.

The fourth block reads `eval/verifier/results.json` unconditionally — a
missing or stale recording is a failure, not a skip, because a skipped check
and a passed one look identical in a green run (D27's lesson).
"""

from __future__ import annotations

import importlib.util
import json

from datetime import date
from pathlib import Path

import pytest

from conftest import AcceptAllVerifier
from pa_agent.contracts import (
    CriterionVerdict,
    DeterminationAborted,
    ErrorCode,
    GapReason,
)
from pa_agent.model_pin import (
    VERIFIER_MEASURED_TIER,
    VERIFIER_MODEL,
)
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.verifier import (
    INSTRUCTION,
    PROMPT_VERSION,
    LiveVerifierRunner,
    NullVerifierRunner,
    RecordedVerifierRunner,
    VerifierAnswer,
    VerifierFailure,
    VerifierOutputError,
    build_claim_payload,
    claim_digest,
)
from pa_agent.workflow import (
    DEFAULT_MAX_ATTEMPTS,
    run_criteria_workflow,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
VERIFIER_RESULTS = REPO_ROOT / "eval" / "verifier" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"
MEASUREMENT_SCRIPT = REPO_ROOT / "scripts" / "run_verifier_measurement.py"

TREE_VERSION = "ncd-100.1-jf-v1"
CONTRACTOR_CODE = "43775"
AS_OF = date(2026, 9, 1)


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def tree(policy_store):
    return policy_store.get_tree(TREE_VERSION)


@pytest.fixture(scope="module")
def runner() -> RecordedExtractionRunner:
    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def e1_patient() -> str:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return next(
        record["patient_id"]
        for record in manifest["notes"]
        if "E1" in record["cases"]
    )


def _run(policy_store, patient_store, runner, patient_id, verifier):
    return run_criteria_workflow(
        policy_store=policy_store,
        patient_store=patient_store,
        extraction_runner=runner,
        policy_ref=policy_store.resolve(CONTRACTOR_CODE),
        patient_id=patient_id,
        as_of=AS_OF,
        verifier=verifier,
    )


class _SelectiveVerifier:
    """Rejects the named criteria, accepts the rest, and counts every call."""

    name = "selective"

    def __init__(self, reject: set[str]) -> None:
        self.reject = reject
        self.calls: list[dict] = []

    def run(self, payload: dict) -> VerifierAnswer:
        self.calls.append(payload)
        criterion_id = payload["criterion"]["id"]
        if criterion_id in self.reject:
            return VerifierAnswer(
                accept=False, reason=f"test rejection of {criterion_id}"
            )
        return VerifierAnswer(accept=True, reason="supported")


class _FailingVerifier:
    """Fails the same way every time, and counts how often it was asked."""

    name = "failing"

    def __init__(self, failure: VerifierFailure) -> None:
        self.failure = failure
        self.attempts = 0

    def run(self, payload: dict) -> VerifierAnswer:
        self.attempts += 1
        raise VerifierOutputError(self.failure, "injected")


# --------------------------------------------------------------------------
# Blindness: the payload is the whole model input (REQ-17, Article V)
# --------------------------------------------------------------------------


def test_the_payload_carries_the_claim_and_nothing_else(tree) -> None:
    """Article V's list is exhaustive: criterion, span, verdict. The payload's
    key sets are asserted exactly, so a field added for convenience — a
    reasoning trace, a neighbour criterion, a chart summary — is a failing
    test rather than a prompt-review finding."""
    criterion = tree.criterion("a")
    payload = build_claim_payload(criterion, "MET", ["Current BMI 36.2."])

    assert sorted(payload) == ["criterion", "quotes", "verdict"], (
        "the payload is the claim and nothing else — not even the request's "
        "as_of, which left with v3's bar on date arithmetic and whose "
        "presence date-bound every claim digest (D77)"
    )
    assert sorted(payload["criterion"]) == ["constants", "id", "label"]
    for constant in payload["criterion"]["constants"].values():
        assert set(constant) <= {"value", "comparison"}, (
            "a constant's note or source leaked into the payload; notes can "
            "name eval cases and expected verdicts, which is reasoning by "
            "another door (D77)"
        )
    assert payload["verdict"] == "MET"
    assert payload["quotes"] == ["Current BMI 36.2."]


def test_no_other_criterion_reaches_the_payload(tree) -> None:
    """Built for c2, the payload must not name c3 — even though c2 is scoped
    to c3's run — nor any other criterion's label. A verifier that sees a
    second criterion is a verifier reading the adjudication."""
    payload = build_claim_payload(tree.criterion("c2"), "MET", ["quote"])
    rendered = json.dumps(payload)

    assert "scoped_to" not in rendered
    for other in tree.criteria:
        if other.id == "c2":
            continue
        assert other.label not in rendered, (
            f"criterion {other.id}'s label leaked into c2's payload"
        )


def test_the_workflow_verifies_cited_verdicts_and_only_cited_verdicts(
    policy_store, patient_store, runner, e1_patient
) -> None:
    """Abstentions cite nothing (REQ-5), so there is no claim to check; the
    verifier sees exactly the criteria that ended `MET` or `NOT_MET`."""
    verifier = AcceptAllVerifier()
    run = _run(policy_store, patient_store, runner, e1_patient, verifier)

    cited = {
        r.criterion_id
        for r in run.determination.criterion_results
        if r.verdict in (CriterionVerdict.MET, CriterionVerdict.NOT_MET)
    }
    assert {p["criterion"]["id"] for p in verifier.calls} == cited
    assert len(verifier.calls) == len(cited), "one claim per criterion"


def test_the_quotes_are_sliced_from_the_documents_not_taken_from_the_model(
    policy_store, patient_store, runner, e1_patient
) -> None:
    """D18 forward: whatever a runner's payload said, what the verifier reads
    is what the span slices to in the hashed source document."""
    verifier = AcceptAllVerifier()
    run = _run(policy_store, patient_store, runner, e1_patient, verifier)

    by_criterion = {p["criterion"]["id"]: p for p in verifier.calls}
    checked = 0
    for result in run.determination.criterion_results:
        if result.criterion_id not in by_criterion:
            continue
        payload = by_criterion[result.criterion_id]
        for span, quote in zip(result.spans, payload["quotes"]):
            document = patient_store.get_document(span.document_id)
            assert quote == document.text[span.char_start:span.char_end]
            checked += 1
    assert checked > 0, "the assertion is vacuous; no quote was compared"


# --------------------------------------------------------------------------
# Rejection: REQ-18 verbatim, and Article IV's non-collapse
# --------------------------------------------------------------------------


def test_a_rejection_resolves_insufficient_evidence_and_still_emits(
    policy_store, patient_store, runner, e1_patient
) -> None:
    verifier = _SelectiveVerifier(reject={"a"})
    run = _run(policy_store, patient_store, runner, e1_patient, verifier)

    rejected = next(
        r
        for r in run.determination.criterion_results
        if r.criterion_id == "a"
    )
    assert rejected.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE, (
        "a rejection is 'could not substantiate', never NOT_MET (Art. IV)"
    )
    assert rejected.gap_reason is GapReason.VERIFIER_REJECTED
    assert rejected.spans == [], (
        "an abstention ships no span — least of all the span just refused"
    )
    assert rejected.detail == "test rejection of a"
    assert any(
        g.criterion_id == "a" for g in run.determination.gap_list
    ), "the rejection reaches the gap list (REQ-31)"


def test_a_rejection_is_answered_once_with_no_retry(
    policy_store, patient_store, runner, e1_patient
) -> None:
    """REQ-18: first rejection, no retry — distinct from REQ-18a's loop. The
    verifier is asked about criterion (a) exactly once even though it said no."""
    verifier = _SelectiveVerifier(reject={"a"})
    _run(policy_store, patient_store, runner, e1_patient, verifier)

    asked_about_a = [
        p for p in verifier.calls if p["criterion"]["id"] == "a"
    ]
    assert len(asked_about_a) == 1


def test_an_accepted_claim_is_untouched(
    policy_store, patient_store, runner, e1_patient
) -> None:
    accepted = _run(
        policy_store, patient_store, runner, e1_patient, AcceptAllVerifier()
    ).determination
    rejected = _run(
        policy_store, patient_store, runner, e1_patient,
        _SelectiveVerifier(reject={"a"}),
    ).determination

    by_id_accepted = {r.criterion_id: r for r in accepted.criterion_results}
    by_id_rejected = {r.criterion_id: r for r in rejected.criterion_results}
    for criterion_id, result in by_id_rejected.items():
        if criterion_id == "a":
            continue
        assert result == by_id_accepted[criterion_id], (
            "verification must not perturb the criteria it accepted"
        )


def test_a_missing_verifier_raises_at_the_first_cited_verdict(
    policy_store, patient_store, runner, e1_patient
) -> None:
    """D31's rule: a determination with cited verdicts and no verifier is an
    unbuilt path that raises naming what to pass — never a silent accept-all."""
    with pytest.raises(NotImplementedError, match="T-17"):
        _run(policy_store, patient_store, runner, e1_patient, None)


def test_the_null_verifier_raises_when_reached(
    policy_store, patient_store, runner, e1_patient
) -> None:
    """The structural zero-verification proof: handing in a verifier that
    cannot be called and completing anyway would prove no cited verdict was
    produced. E1 produces seven, so this run must die on the first."""
    with pytest.raises(AssertionError, match="NullVerifierRunner was reached"):
        _run(
            policy_store, patient_store, runner, e1_patient,
            NullVerifierRunner(),
        )


# --------------------------------------------------------------------------
# Faults: REQ-18a's loop, mapped at the workflow boundary (D75's shape)
# --------------------------------------------------------------------------


def test_a_retryable_fault_consumes_the_budget_then_errors(
    policy_store, patient_store, runner, e1_patient
) -> None:
    failing = _FailingVerifier(VerifierFailure.CALL_FAILED)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, runner, e1_patient, failing)

    assert failing.attempts == DEFAULT_MAX_ATTEMPTS
    assert caught.value.attempts == DEFAULT_MAX_ATTEMPTS
    [errored] = caught.value.results
    assert errored.verdict is CriterionVerdict.ERROR
    assert errored.error_code is ErrorCode.MODEL_CALL_FAILED
    assert errored.gap_reason is None, (
        "an ERROR is not an abstention; there is nothing to go collect (Art. IV)"
    )


def test_a_terminal_fault_is_tried_once(
    policy_store, patient_store, runner, e1_patient
) -> None:
    failing = _FailingVerifier(VerifierFailure.SCHEMA_INVALID)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, runner, e1_patient, failing)

    assert failing.attempts == 1
    assert caught.value.attempts == 1
    [errored] = caught.value.results
    assert errored.error_code is ErrorCode.SCHEMA_INVALID


# --------------------------------------------------------------------------
# The live runner's mechanics, on a fake client (T-62: no network, no mock of
# our own code — the fake is the SDK boundary, everything inward is real)
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage_metadata = type(
            "Usage", (), {"prompt_token_count": 120, "candidates_token_count": 20}
        )()


class _FakeClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.requests: list[dict] = []
        outer = self

        class _Models:
            def generate_content(self, **kwargs):
                outer.requests.append(kwargs)
                return _FakeResponse(outer._text)

        self.models = _Models()


def test_the_live_runner_returns_a_rejection_as_an_answer(tree) -> None:
    client = _FakeClient('{"accept": false, "reason": "the quote is a diet log"}')
    answer = LiveVerifierRunner(client).run(
        build_claim_payload(tree.criterion("a"), "MET", ["walked 20 minutes"])
    )
    assert answer.accept is False
    assert answer.reason == "the quote is a diet log"
    assert answer.metrics is not None
    assert answer.metrics.purpose == "verification"
    assert answer.metrics.model == VERIFIER_MODEL
    assert answer.metrics.input_tokens == 120


def test_the_live_runner_sends_the_payload_and_nothing_else(tree) -> None:
    """The request body is the instruction plus the payload's JSON — the
    blindness boundary holds at the wire, not just at the builder."""
    client = _FakeClient('{"accept": true, "reason": "ok"}')
    payload = build_claim_payload(tree.criterion("a"), "MET", ["BMI 36.2"])
    LiveVerifierRunner(client).run(payload)

    [request] = client.requests
    assert request["model"] == VERIFIER_MODEL
    body = request["contents"]
    assert body.startswith(INSTRUCTION)
    remainder = body[len(INSTRUCTION):]
    assert json.loads(remainder.replace("CLAIM:", "", 1)) == payload, (
        "the wire carries exactly the payload; anything else is a leak"
    )


def test_an_unparseable_response_is_terminal(tree) -> None:
    client = _FakeClient("I think this claim is fine.")
    with pytest.raises(VerifierOutputError) as caught:
        LiveVerifierRunner(client).run(
            build_claim_payload(tree.criterion("a"), "MET", ["q"])
        )
    assert caught.value.reason is VerifierFailure.UNPARSEABLE


def test_a_response_off_schema_is_terminal(tree) -> None:
    client = _FakeClient('{"accept": true, "confidence": 0.93, "reason": "ok"}')
    with pytest.raises(VerifierOutputError) as caught:
        LiveVerifierRunner(client).run(
            build_claim_payload(tree.criterion("a"), "MET", ["q"])
        )
    assert caught.value.reason is VerifierFailure.SCHEMA_INVALID, (
        "extra fields are refused: a confidence score nobody measured is a "
        "threshold waiting to be invented (D77)"
    )


# --------------------------------------------------------------------------
# The recorded runner's contract (synthetic recording)
# --------------------------------------------------------------------------


def _record_for(payload: dict, accept: bool = True) -> dict:
    return {
        "digest": claim_digest(payload),
        "payload": payload,
        "accept": accept,
        "reason": "recorded",
        "metrics": {
            "model": VERIFIER_MODEL,
            "purpose": "verification",
            "input_tokens": 100,
            "output_tokens": 10,
            "wall_time_ms": 50.0,
        },
    }


def test_a_replay_returns_the_recorded_answer_with_its_metrics(tree) -> None:
    payload = build_claim_payload(tree.criterion("a"), "MET", ["BMI 36.2"])
    replay = RecordedVerifierRunner.from_records([_record_for(payload, accept=False)])
    answer = replay.run(payload)
    assert answer.accept is False
    assert answer.metrics is not None and answer.metrics.input_tokens == 100, (
        "recorded metrics are carried through; a replay that reported zero "
        "tokens would understate what the answer cost (Art. X)"
    )


def test_a_miss_raises_and_names_the_measurement_script(tree) -> None:
    replay = RecordedVerifierRunner.from_records([])
    payload = build_claim_payload(tree.criterion("a"), "MET", ["never measured"])
    with pytest.raises(VerifierOutputError) as caught:
        replay.run(payload)
    assert caught.value.reason is VerifierFailure.NOT_RECORDED
    assert "run_verifier_measurement" in str(caught.value)


def test_a_tampered_record_fails_on_the_claim_it_lies_about(tree) -> None:
    payload = build_claim_payload(tree.criterion("a"), "MET", ["BMI 36.2"])
    record = _record_for(payload)
    record["payload"] = build_claim_payload(
        tree.criterion("a"), "NOT_MET", ["BMI 36.2"]
    )
    replay = RecordedVerifierRunner({record["digest"]: record})
    with pytest.raises(VerifierOutputError) as caught:
        replay.run(payload)
    assert caught.value.reason is VerifierFailure.RECORD_TAMPERED


# --------------------------------------------------------------------------
# The committed recording (read unconditionally; a missing file is a failure,
# not a skip — D27)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def recording() -> dict:
    assert VERIFIER_RESULTS.exists(), (
        "no committed verifier recording; run "
        "`python scripts/run_verifier_measurement.py` (it spends model calls) "
        "and commit eval/verifier/results.json (T-17, D77)"
    )
    return json.loads(VERIFIER_RESULTS.read_text(encoding="utf-8"))


def test_the_recording_names_the_pinned_model_and_prompt(recording) -> None:
    """D20's rule for the second pin: the pin and the artifact move together
    or the suite fails. The tier is named so nobody quotes AI Studio numbers
    as Vertex numbers (D5)."""
    assert recording["model"] == VERIFIER_MODEL
    assert recording["tier"] == VERIFIER_MEASURED_TIER
    assert recording["prompt_version"] == PROMPT_VERSION


def test_every_recorded_digest_recomputes_from_its_payload(recording) -> None:
    assert recording["claims"], "an empty recording verifies nothing"
    for record in recording["claims"]:
        assert claim_digest(record["payload"]) == record["digest"], (
            f"claim {record['digest'][:12]} no longer says what was measured"
        )


def test_every_gate_reachable_claim_is_recorded(recording) -> None:
    """Coverage, computed rather than assumed: enumerate every claim the
    gates' determinations produce (the measurement script's own enumeration,
    which spends nothing) and require each digest in the recording. A new
    eval row or a drifted document produces a digest the recording lacks,
    and this test names it before a gate dies on it."""
    spec = importlib.util.spec_from_file_location(
        "run_verifier_measurement", MEASUREMENT_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    recorded = {record["digest"] for record in recording["claims"]}
    reachable = module.enumerate_claims()
    assert reachable, "enumeration found no claims; the harness is broken"
    missing = set(reachable) - recorded
    assert not missing, (
        f"{len(missing)} gate-reachable claim(s) missing from the recording; "
        "re-run scripts/run_verifier_measurement.py"
    )
