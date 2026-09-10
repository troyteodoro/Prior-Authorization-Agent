"""T-26 — the `ERROR` state in the data contracts.

REQ-18a, REQ-23, REQ-24, REQ-26, REQ-30 · gates A9 · D62

Article IV names three states and forbids collapsing them. Two were built in
T-09; this is the third, and the reason it waited is that `ERROR` is the one
that is *not* a claim about the chart. `NOT_MET` says the evidence fell short.
`INSUFFICIENT_EVIDENCE` says the evidence is missing and names what to go
collect. `ERROR` says the system could not evaluate — nobody failed to document
anything, and there is nothing for Sam to go get.

All three read as "not approved" to a casual reader, which is exactly why the
shapes below are mutually exclusive rather than conventional.

**Contracts only.** *Mapping* a fault onto an `ERROR` — an unanchorable quote, a
raising predicate, a model call that failed past its retries — is T-29's, and so
is REQ-24's abort. This file asserts the vocabulary exists and cannot be misused;
it does not assert anything about who produces one.

Spends no model call and reads no recording.
"""

from __future__ import annotations

import ast
from datetime import date
from enum import Enum
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    ErrorClass,
    ErrorCode,
    EvidenceSpan,
    GapReason,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SPAN = EvidenceSpan(document_id="d1", char_start=0, char_end=8, quote="anything")

# REQ-30's table, transcribed. Written out rather than derived from the enum, so
# that flipping a classification in the contracts fails here instead of agreeing
# with itself.
EXPECTED_CLASSIFICATION = {
    "MODEL_CALL_FAILED": ErrorClass.RETRYABLE,
    "SOURCE_UNAVAILABLE": ErrorClass.RETRYABLE,
    "SCHEMA_INVALID": ErrorClass.TERMINAL,
    "SPAN_VALIDATION_FAILED": ErrorClass.TERMINAL,
    "PREDICATE_EXCEPTION": ErrorClass.TERMINAL,
}


def _errored(**overrides) -> CriterionResult:
    fields = {
        "criterion_id": "c1",
        "verdict": CriterionVerdict.ERROR,
        "error_code": ErrorCode.SCHEMA_INVALID,
        "error_detail": "ValidationError: 1 validation error for Extraction",
    }
    fields.update(overrides)
    return CriterionResult(**fields)


# --------------------------------------------------------------------------
# The state itself
# --------------------------------------------------------------------------


def test_error_is_the_third_verdict_and_the_set_is_closed() -> None:
    assert CriterionVerdict.ERROR.value == "ERROR"
    assert {v.value for v in CriterionVerdict} == {
        "MET",
        "NOT_MET",
        "INSUFFICIENT_EVIDENCE",
        "ERROR",
    }


def test_error_is_not_a_determination_outcome() -> None:
    """There is no such thing as an errored determination.

    There is a determination, or there is a fault and no determination — which
    is what REQ-26 enforces below. A fourth `DeterminationOutcome` would make
    "we could not compute this" into a reportable answer, and spec §6 labels no
    such case.
    """
    assert not hasattr(DeterminationOutcome, "ERROR")
    assert {o.value for o in DeterminationOutcome} == {
        "MET",
        "NOT_MET",
        "INSUFFICIENT_EVIDENCE",
        "NOT_COVERED",
    }


# --------------------------------------------------------------------------
# REQ-30: every code declares retryable or terminal
# --------------------------------------------------------------------------


def test_every_error_code_declares_a_classification() -> None:
    for code in ErrorCode:
        assert code.classification in (ErrorClass.RETRYABLE, ErrorClass.TERMINAL)
        assert code.retryable is (code.classification is ErrorClass.RETRYABLE)
        assert code.terminal is not code.retryable


def test_the_classifications_are_the_ones_req30_tabulates() -> None:
    assert {c.value for c in ErrorCode} == set(EXPECTED_CLASSIFICATION), (
        "REQ-30's enum is closed; a new member needs a row in the spec table "
        "and a row in this test"
    )
    for code in ErrorCode:
        assert code.classification is EXPECTED_CLASSIFICATION[code.value], (
            f"{code.value} is classified {code.classification.value}; REQ-30 "
            f"says {EXPECTED_CLASSIFICATION[code.value].value}"
        )


def test_only_a_transport_failure_is_retryable() -> None:
    """REQ-18a's asymmetry, stated as a claim about which codes retry.

    A call that failed and a store that was unavailable may succeed on a second
    attempt. A response that did not validate, a span that did not slice back,
    and a predicate that raised will all do the same thing again — retrying them
    spends money to reach the same answer.
    """
    assert [c.value for c in ErrorCode if c.retryable] == [
        "MODEL_CALL_FAILED",
        "SOURCE_UNAVAILABLE",
    ]


def test_a_member_added_without_a_classification_defaults_to_terminal() -> None:
    """REQ-30's last sentence, exercised on a throwaway enum of the same shape.

    The default lives in `ErrorCode.__new__`, so it cannot be tested by adding a
    member to the real enum. It can be tested by building one the same way — and
    the point is which direction the default falls: forgetting a classification
    must mean "we did not retry", never "we retried a fault that cannot succeed".
    """

    class Probe(str, Enum):
        def __new__(cls, value: str, retryable: bool = False) -> "Probe":
            obj = str.__new__(cls, value)
            obj._value_ = value
            obj._retryable = retryable
            return obj

        UNCLASSIFIED = ("UNCLASSIFIED",)
        CLASSIFIED = ("CLASSIFIED", True)

        @property
        def retryable(self) -> bool:
            return self._retryable

    assert Probe.UNCLASSIFIED.retryable is False
    assert Probe.CLASSIFIED.retryable is True

    # And the real enum uses that same defaulting form for its terminal members,
    # rather than spelling out `False` — asserted on the source so a refactor to
    # an external lookup table, which would lose the default, is visible here.
    source = (REPO_ROOT / "pa_agent" / "contracts.py").read_text(encoding="utf-8")
    assert 'def __new__(cls, value: str, retryable: bool = False)' in source
    assert 'SCHEMA_INVALID = ("SCHEMA_INVALID",)' in source


# --------------------------------------------------------------------------
# The three shapes are mutually exclusive
# --------------------------------------------------------------------------


def test_an_error_carries_its_code_and_the_exception_text() -> None:
    result = _errored()
    assert result.verdict is CriterionVerdict.ERROR
    assert result.error_code is ErrorCode.SCHEMA_INVALID
    assert "ValidationError" in (result.error_detail or "")
    assert result.spans == []
    assert result.gap_reason is None


def test_an_error_without_a_code_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError, match="ERROR without an error_code"):
        _errored(error_code=None)


def test_an_error_without_the_exception_text_cannot_be_constructed() -> None:
    """REQ-24: the underlying exception is surfaced, not swallowed."""
    with pytest.raises(ValidationError, match="ERROR without error_detail"):
        _errored(error_detail=None)
    with pytest.raises(ValidationError, match="ERROR without error_detail"):
        _errored(error_detail="   ")


@pytest.mark.parametrize(
    "verdict,extra",
    [
        (CriterionVerdict.MET, {"spans": [SPAN]}),
        (CriterionVerdict.NOT_MET, {"spans": [SPAN]}),
        (
            CriterionVerdict.INSUFFICIENT_EVIDENCE,
            {"gap_reason": GapReason.NO_EVIDENCE_RETRIEVED},
        ),
    ],
)
def test_no_other_verdict_may_carry_an_error_code(verdict, extra) -> None:
    with pytest.raises(ValidationError, match="carries error_code"):
        CriterionResult(
            criterion_id="c1",
            verdict=verdict,
            error_code=ErrorCode.PREDICATE_EXCEPTION,
            **extra,
        )


def test_no_other_verdict_may_carry_error_detail() -> None:
    """`detail` is for a finding; `error_detail` is the text behind a fault."""
    with pytest.raises(ValidationError, match="carries\\s+error_detail"):
        CriterionResult(
            criterion_id="c1",
            verdict=CriterionVerdict.MET,
            spans=[SPAN],
            error_detail="not a fault",
        )


def test_an_error_carries_no_span() -> None:
    """REQ-5's second half reaches `ERROR` without a new branch: a criterion
    that could not be evaluated has no evidence, and a span on one would be a
    citation supporting nothing."""
    with pytest.raises(ValidationError, match="ERROR carries 1 span"):
        _errored(spans=[SPAN])


def test_an_error_carries_no_gap_reason() -> None:
    """The sharpest edge in Article IV.

    A gap reason says what to go collect. An `ERROR` means the system could not
    evaluate: nobody failed to document anything. Offering a next action for a
    fault is exactly the collapse the article forbids between `ERROR` and
    `INSUFFICIENT_EVIDENCE` (D9).
    """
    with pytest.raises(ValidationError, match="ERROR carries gap_reason"):
        _errored(gap_reason=GapReason.NO_EVIDENCE_RETRIEVED)


def test_the_three_not_approved_states_have_three_different_shapes() -> None:
    """Stated as one assertion because the collapse D9 warns about is a reader
    treating them as one thing. Three verdicts, three disjoint payloads."""
    not_met = CriterionResult(
        criterion_id="c1", verdict=CriterionVerdict.NOT_MET, spans=[SPAN]
    )
    abstained = CriterionResult(
        criterion_id="c1",
        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
        gap_reason=GapReason.UNSUBSTANTIATED_ASSERTION,
    )
    errored = _errored()

    shapes = {
        r.verdict: (bool(r.spans), r.gap_reason is not None, r.error_code is not None)
        for r in (not_met, abstained, errored)
    }
    assert shapes == {
        CriterionVerdict.NOT_MET: (True, False, False),
        CriterionVerdict.INSUFFICIENT_EVIDENCE: (False, True, False),
        CriterionVerdict.ERROR: (False, False, True),
    }
    assert len(set(shapes.values())) == 3


# --------------------------------------------------------------------------
# REQ-26 / A9: no determination is presented over an ERROR
# --------------------------------------------------------------------------


def test_a_determination_over_an_error_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError, match="criterion\\(s\\) in ERROR"):
        Determination(
            patient_id="p1",
            procedure_code="43775",
            policy_version_id="ncd-100.1-jf-v1",
            outcome=DeterminationOutcome.NOT_MET,
            criterion_results=[
                CriterionResult(
                    criterion_id="a", verdict=CriterionVerdict.MET, spans=[SPAN]
                ),
                _errored(),
            ],
        )


def test_the_refusal_names_the_criterion_and_its_code() -> None:
    """A9 is a gate somebody has to debug. "One criterion errored" is not
    actionable; "c1=SCHEMA_INVALID" is."""
    with pytest.raises(ValidationError) as caught:
        Determination(
            patient_id="p1",
            procedure_code="43775",
            policy_version_id="ncd-100.1-jf-v1",
            outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
            criterion_results=[_errored(criterion_id="c4")],
        )
    message = str(caught.value)
    assert "'c4'" in message
    assert "c4=SCHEMA_INVALID" in message


@pytest.mark.parametrize(
    "outcome",
    [
        DeterminationOutcome.MET,
        DeterminationOutcome.NOT_MET,
        DeterminationOutcome.INSUFFICIENT_EVIDENCE,
    ],
)
def test_no_outcome_makes_an_errored_determination_constructible(outcome) -> None:
    """The refusal is unconditional. There is no outcome under which a fault
    becomes reportable — that is the whole of A9."""
    with pytest.raises(ValidationError):
        Determination(
            patient_id="p1",
            procedure_code="43775",
            policy_version_id="ncd-100.1-jf-v1",
            outcome=outcome,
            criterion_results=[_errored()],
        )


def test_a_determination_with_no_errored_criterion_is_unaffected() -> None:
    """The counterexample the validator needs, so the gate is not vacuous."""
    determination = Determination(
        patient_id="p1",
        procedure_code="43775",
        policy_version_id="ncd-100.1-jf-v1",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
        criterion_results=[
            CriterionResult(
                criterion_id="a", verdict=CriterionVerdict.MET, spans=[SPAN]
            ),
            CriterionResult(
                criterion_id="c1",
                verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
                gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
            ),
        ],
    )
    assert [g.criterion_id for g in determination.gap_list] == ["c1"]


# --------------------------------------------------------------------------
# The state is enforced by a validator, not by a convention
# --------------------------------------------------------------------------


def test_the_error_rules_live_in_validators_and_not_in_a_helper() -> None:
    """REQ-26 says "enforced by a model validator, not by convention", and the
    difference is whether a caller can route around it. Asserted on the AST so a
    later refactor into a module-level `check_error(...)` that somebody forgets
    to call is visible here."""
    source = (REPO_ROOT / "pa_agent" / "contracts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    validators: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            decorated = any(
                "model_validator" in ast.unparse(d) for d in item.decorator_list
            )
            if decorated:
                validators.setdefault(node.name, set()).add(item.name)

    assert "_an_error_names_its_fault_and_nothing_else" in validators["CriterionResult"]
    assert "_no_determination_is_presented_over_an_error" in validators["Determination"]


def test_the_contracts_still_import_no_model_and_no_store() -> None:
    """T-26 widened the vocabulary; it did not widen the module's reach.

    Checked in a **fresh interpreter**, not with a `sys.modules` lookup in this
    process. `tests/test_adk_agent.py` imports `google.adk` legitimately — that is
    what it is for — so a `sys.modules` check here passes or fails by test
    ordering, and what it would really be asserting is "nothing in this process
    loaded the ADK" rather than "this module does not". The subprocess form is the
    one `tests/test_resolver.py` already uses, for exactly this reason (D62).
    """
    import subprocess
    import sys

    probe = (
        "import sys; import pa_agent.contracts; "
        "assert 'google.adk' not in sys.modules, 'the contracts pulled in the ADK'; "
        "assert 'google.genai' not in sys.modules, 'the contracts pulled in genai'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr

    source = (REPO_ROOT / "pa_agent" / "contracts.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"__future__", "hashlib", "json", "datetime", "enum", "typing", "pydantic"}, (
        f"contracts.py imports {sorted(imported)}; a store or a model client "
        "here would put storage and a credential in the vocabulary layer "
        "(REQ-41)"
    )


def test_the_date_import_is_still_used_so_the_import_set_is_honest() -> None:
    """Guards the assertion above against passing for the wrong reason."""
    from pa_agent.contracts import Observation

    assert Observation(
        code="39156-5", value=36.2, effective_date=date(2026, 3, 1)
    ).effective_date.year == 2026
