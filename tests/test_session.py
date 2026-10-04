"""The session lifecycle (T-100, D127, REQ-71).

Gate A12's first clause — *every transition in the enum has a test and every
illegal one raises* — which on a three-state enum meant all **nine ordered
pairs** and on `T-105`'s five-state enum means all **twenty-five**, not just the
legal ones.

**The legal set here is written out from the prose, not read from the code.**
`LEGAL` below is transcribed from `docs/stories.md`'s US-12 and US-13 and spec
§11's v1.4 entry, sentence by sentence, with the sentence quoted beside each
pair — T-104's row came from US-13's *her edits append*, not from the table. A test
that built its expectation from `TRANSITIONS` would agree with any table at
all, including one that had lost a row — which is the whole failure this file
exists to catch, and the one an adversarial pass at this close went looking
for.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    SUBMITTED_STATES,
    Determination,
    DeterminationOutcome,
    Intake,
    PayerDecision,
    PayerDecisionOutcome,
    Session,
    SessionRun,
    SessionState,
    SubmissionRecord,
)
from pa_agent.session import TRANSITIONS, IllegalTransition, advance, is_terminal, legal

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every move the documents declare, transcribed from their own sentences.
#: Deliberately a literal: derived from `TRANSITIONS` it would prove nothing.
#:
#: - US-12: "a session is created ... in `CREATED`" then "when it is run ... it
#:   holds the determination ... and moves to `DETERMINED`".
#: - US-12: "a second run is a new snapshot, never an edit" -> DETERMINED may
#:   become DETERMINED again.
#: - spec §11 v1.4: "`CREATED -> DETERMINED -> IN_REVIEW`, extended by v1.5".
#: - US-13: "her edits append to a review log beside the determination" ->
#:   IN_REVIEW may become IN_REVIEW again, because *append* is plural and a log
#:   a reviewer may add to once is not a log (T-104, D132).
#: - US-13: "given a reviewed session, when Sam submits it, then ... the session
#:   is `AWAITING_DECISION`" -> IN_REVIEW may become AWAITING_DECISION, and its
#:   own fourth bullet — "given a session **not yet in review**, when submission
#:   is attempted, then ... nothing written" — is why DETERMINED may **not**
#:   (T-105, D134).
#: - US-13: "given a payer's simulated decision, when it is recorded, then the
#:   session **closes** with the outcome and the date" -> AWAITING_DECISION may
#:   become DECIDED, and *closes* is why DECIDED has no row content at all.
LEGAL: set[tuple[SessionState, SessionState]] = {
    (SessionState.CREATED, SessionState.DETERMINED),
    (SessionState.DETERMINED, SessionState.DETERMINED),
    (SessionState.DETERMINED, SessionState.IN_REVIEW),
    (SessionState.IN_REVIEW, SessionState.IN_REVIEW),
    (SessionState.IN_REVIEW, SessionState.AWAITING_DECISION),
    (SessionState.AWAITING_DECISION, SessionState.DECIDED),
}

ALL_PAIRS = list(itertools.product(SessionState, SessionState))


def _determination(policy_version_id: str = "ncd-100.1-jf-v1") -> Determination:
    return Determination(
        patient_id="p",
        procedure_code="43775",
        policy_version_id=policy_version_id,
        payer="medicare",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
    )


def _run(ran_at: str = "2026-09-25T00:00:00Z") -> SessionRun:
    return SessionRun(
        ran_at=ran_at,
        as_of="2026-09-25",
        policy_version_id="ncd-100.1-jf-v1",
        determination=_determination(),
    )


def _submission(run_index: int = 0) -> SubmissionRecord:
    return SubmissionRecord(
        artifact_id=f"s1-run{run_index}",
        sha256="0" * 64,
        payer_id="sim-national-a",
        submitted_at="2026-09-25T01:00:00Z",
        run_index=run_index,
        citation_count=3,
    )


def _decision() -> PayerDecision:
    return PayerDecision(
        outcome=PayerDecisionOutcome.APPROVED,
        decided_on="2026-09-26",
        recorded_at="2026-09-27T00:00:00Z",
        payer_id="sim-national-a",
    )


def _session(state: SessionState = SessionState.CREATED, runs: tuple = ()) -> Session:
    """A session in `state`, carrying exactly what that state requires.

    The two records are supplied **from the state** rather than always, because
    `Session`'s rules are *iff* relations (T-105, D134): a `DETERMINED` session
    carrying a submission is refused, and so is an `AWAITING_DECISION` one with
    none. A helper that always passed both could not build half the enum.
    """
    return Session(
        session_id="s1",
        created_at="2026-09-25T00:00:00Z",
        intake=Intake(patient_id="p", procedure_code="43775"),
        state=state,
        runs=runs,
        submission=_submission() if state in SUBMITTED_STATES else None,
        decision=_decision() if state is SessionState.DECIDED else None,
    )


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def test_the_table_covers_every_state():
    """D110's completeness idiom. A state with no row is one nothing can leave
    and nothing can reach, and no behavioural test would say so."""
    assert set(TRANSITIONS) == set(SessionState)


def test_the_table_is_exactly_what_the_documents_declare():
    """The table against the prose, in both directions.

    This is the check that catches a row added to `TRANSITIONS` that no
    document authorises — a lifecycle that grew in code and not in the spec.
    """
    from_table = {
        (frm, to) for frm, tos in TRANSITIONS.items() for to in tos
    }
    assert from_table == LEGAL, (
        f"TRANSITIONS declares {sorted((f.value, t.value) for f, t in from_table)}; "
        f"the documents declare {sorted((f.value, t.value) for f, t in LEGAL)}"
    )


@pytest.mark.parametrize("frm,to", ALL_PAIRS, ids=lambda s: s.value)
def test_every_ordered_pair_is_legal_or_raises(frm, to):
    """A12: every transition tested, every illegal one raises.

    All nine, parametrized, so a state added to the enum without a decision
    about each of its pairs cannot pass by omission.
    """
    expected = (frm, to) in LEGAL
    assert legal(frm, to) is expected

    runs = () if frm is SessionState.CREATED else (_run(),)
    session = _session(frm, runs)

    if expected:
        moved = advance(session, to, run=_run("2026-09-26T00:00:00Z"))
        assert moved.state is to
    else:
        with pytest.raises(IllegalTransition):
            advance(session, to, run=_run("2026-09-26T00:00:00Z"))


def test_an_illegal_transition_records_nothing():
    """REQ-71's second half, and the reason `advance` cannot write.

    The machine returns a new object or raises; the caller persists. So a
    refused move leaves the caller holding exactly what it passed in — asserted
    on the object, because that is what the adapter would be handed.
    """
    before = _session(SessionState.CREATED)
    with pytest.raises(IllegalTransition):
        advance(before, SessionState.IN_REVIEW, run=_run())
    assert before.state is SessionState.CREATED
    assert before.runs == ()


def test_the_illegal_transition_names_both_ends_and_the_legal_set():
    """The message is what tells a reviewer what they could have done."""
    with pytest.raises(IllegalTransition) as caught:
        advance(_session(SessionState.CREATED), SessionState.IN_REVIEW)
    assert caught.value.frm is SessionState.CREATED
    assert caught.value.to is SessionState.IN_REVIEW
    assert caught.value.legal == ["DETERMINED"]
    assert "CREATED" in str(caught.value) and "IN_REVIEW" in str(caught.value)


def test_exactly_one_state_is_terminal_and_the_table_is_why():
    """D127's argument, paid off twice in two rows — and the lines that did it.

    `IN_REVIEW` was terminal in v1.4 **by the table**, and T-104 gave it the
    append self-edge; terminality followed with no flag moved, no member edited
    and `is_terminal()` untouched, which is precisely what D127 said a literal
    `terminal=True` on the member would have made impossible. **T-105 paid it off
    in the other direction**: `DECIDED` arrived terminal because its row is empty,
    and the diff to `pa_agent/session.py` is three rows of a dict (D134).

    So the message branch for a state with no successors, unreachable at T-104
    and kept for this row, is reachable again — and both branches are asserted
    here, because a refusal that said *terminal* for `IN_REVIEW` and a refusal
    that did not say it for `DECIDED` are the two ways this sentence goes wrong.
    """
    assert [s for s in SessionState if is_terminal(s)] == [SessionState.DECIDED]

    with pytest.raises(IllegalTransition) as caught:
        advance(_session(SessionState.IN_REVIEW, (_run(),)), SessionState.DETERMINED)
    assert caught.value.legal == ["IN_REVIEW", "AWAITING_DECISION"]
    assert "terminal" not in str(caught.value)

    with pytest.raises(IllegalTransition) as closed:
        advance(_session(SessionState.DECIDED, (_run(),)), SessionState.DECIDED)
    assert closed.value.legal == []
    assert "terminal" in str(closed.value)


# --------------------------------------------------------------------------
# `terminal` is derived, not declared (D127)
# --------------------------------------------------------------------------


def test_terminal_is_read_off_the_table():
    """Every state, against its own row. `IN_REVIEW` reads `False` since T-104
    because its row is non-empty, and for no other reason (D132)."""
    for state in SessionState:
        assert is_terminal(state) is (TRANSITIONS[state] == ())
    assert is_terminal(SessionState.IN_REVIEW) is False
    assert is_terminal(SessionState.DECIDED) is True


def test_contracts_does_not_import_the_state_machine():
    """Why `is_terminal` is a function here rather than a property there.

    A property on `SessionState` reads `TRANSITIONS`, so `contracts` imports
    `session` while `session` imports `contracts` — and that cycle makes every
    module in the package reach the session plane. Measured: six plane roots
    went red the first time the walk was run against it (D127).
    """
    source = (REPO_ROOT / "pa_agent" / "contracts.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "pa_agent.session", (
                f"contracts.py imports pa_agent.session at line {node.lineno}; "
                "the vocabulary may not depend on the table that walks it"
            )


def test_no_member_of_the_enum_declares_terminality_as_a_literal():
    """D127's departure from `ErrorCode.__new__`, pinned by parsing.

    A `terminal=True` beside a member is a copy of the table, and v1.5
    contradicts it the moment `AWAITING_DECISION` gives `IN_REVIEW` an outgoing
    edge. Behaviourally the two readings are identical on today's three states,
    so this is checked by reading the source — D65's shape.
    """
    source = (REPO_ROOT / "pa_agent" / "contracts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    enum = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "SessionState"
    )
    for node in enum.body:
        if isinstance(node, ast.Assign):
            value = node.value
            assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
                f"SessionState member at line {node.lineno} carries something "
                "other than a plain string; terminality is the table's (D127)"
            )


# --------------------------------------------------------------------------
# A run is appended, never an edit (US-12)
# --------------------------------------------------------------------------


def test_a_second_run_appends_and_leaves_the_first_alone():
    session = advance(_session(), SessionState.DETERMINED, run=_run("first"))
    again = advance(session, SessionState.DETERMINED, run=_run("second"))

    assert [r.ran_at for r in again.runs] == ["first", "second"]
    assert session.runs[0].ran_at == "first"
    assert len(session.runs) == 1, "the earlier session object was mutated"


def test_advancing_without_a_run_keeps_the_runs_it_had():
    determined = advance(_session(), SessionState.DETERMINED, run=_run())
    reviewed = advance(determined, SessionState.IN_REVIEW)
    assert reviewed.runs == determined.runs


# --------------------------------------------------------------------------
# The state agrees with the runs
# --------------------------------------------------------------------------


def test_a_created_session_carrying_a_run_is_refused():
    with pytest.raises(ValidationError, match="CREATED and carries"):
        _session(SessionState.CREATED, (_run(),))


def test_a_determined_session_carrying_no_run_is_refused():
    with pytest.raises(ValidationError, match="carries\nno run|carries no run"):
        _session(SessionState.DETERMINED, ())


# --------------------------------------------------------------------------
# The submission and the decision exist **exactly when** (T-105, D134, REQ-77)
# --------------------------------------------------------------------------


def _raw(state: SessionState, **extra) -> Session:
    """A session built field by field, bypassing `_session`'s state-driven pairing.

    These four tests are about the rules `_session` exists to satisfy, so they
    have to be able to build the shapes it cannot.
    """
    payload = dict(
        session_id="s1",
        created_at="2026-09-25T00:00:00Z",
        intake=Intake(patient_id="p", procedure_code="43775"),
        state=state,
        runs=() if state is SessionState.CREATED else (_run(),),
    )
    payload.update(extra)
    return Session(**payload)


@pytest.mark.parametrize(
    "state", [SessionState.AWAITING_DECISION, SessionState.DECIDED],
    ids=lambda s: s.value,
)
def test_a_sent_session_with_no_submission_is_refused(state):
    """Half of REQ-77's *exactly when*: a session that claims to have been sent
    names the artifact and the hash, so a gate can open the outbox and compare."""
    with pytest.raises(ValidationError, match="records no submission"):
        _raw(state, decision=_decision() if state is SessionState.DECIDED else None)


@pytest.mark.parametrize(
    "state",
    [SessionState.CREATED, SessionState.DETERMINED, SessionState.IN_REVIEW],
    ids=lambda s: s.value,
)
def test_an_unsent_session_carrying_a_submission_is_refused(state):
    """The other half, and the one a one-way rule loses.

    *`AWAITING_DECISION` needs a submission* is satisfied by a `DETERMINED`
    session that carries one anyway — and *no session in an earlier state has one*
    is half the statement being minted (REQ-77, D134).
    """
    with pytest.raises(ValidationError, match="records a submission"):
        _raw(state, submission=_submission())


def test_a_decided_session_with_no_decision_is_refused():
    """The outcome and the date it was taken are what closing means (US-13)."""
    with pytest.raises(ValidationError, match="records no payer decision"):
        _raw(SessionState.DECIDED, submission=_submission())


@pytest.mark.parametrize(
    "state",
    [SessionState.CREATED, SessionState.DETERMINED, SessionState.IN_REVIEW,
     SessionState.AWAITING_DECISION],
    ids=lambda s: s.value,
)
def test_an_undecided_session_carrying_a_decision_is_refused(state):
    """Both directions again. A session still awaiting an answer it already has
    is a session whose state disagrees with what it holds (D31's shape, on a
    field — the sentence `_runs_match_the_state` was written from)."""
    with pytest.raises(ValidationError, match="records a payer decision"):
        _raw(
            state,
            submission=_submission() if state in SUBMITTED_STATES else None,
            decision=_decision(),
        )


def test_the_two_states_an_artifact_exists_in_are_named_once():
    """`SUBMITTED_STATES` is read by the validator rather than compared twice.

    Two comparisons about one rule eventually disagree, which is `Intake`'s
    normalisation argument (D128) on a validator instead of a field. Pinned as a
    literal transcribed from REQ-77's own sentence — *exactly when it enters
    `AWAITING_DECISION`*, and it keeps the artifact once `DECIDED`.
    """
    assert SUBMITTED_STATES == (
        SessionState.AWAITING_DECISION,
        SessionState.DECIDED,
    )


def test_a_payer_decision_records_two_dates_that_are_not_the_same_field():
    """D134's *two dates, on purpose*, as a field-level claim.

    Collapsing them makes a decision **recorded** a week late indistinguishable
    from one **taken** a week late — D78's category, where an `as_of` rode along
    and date-bound every claim digest, one object over. A behavioural test cannot
    see the difference, because a single-date implementation answers every
    question this suite asks; what separates them is that both fields exist and
    hold different values.
    """
    decision = PayerDecision(
        outcome=PayerDecisionOutcome.DENIED,
        decided_on="2026-09-01",
        recorded_at="2026-09-20T12:00:00Z",
        payer_id="sim-national-a",
    )
    assert {"decided_on", "recorded_at"} <= set(PayerDecision.model_fields)
    assert decision.decided_on.isoformat() == "2026-09-01"
    assert decision.recorded_at.startswith("2026-09-20")
    assert decision.decided_on.isoformat() not in decision.recorded_at


# --------------------------------------------------------------------------
# The machine reaches no plane
# --------------------------------------------------------------------------


def test_the_state_machine_imports_only_contracts():
    """It sits beside the port, not inside it. A store import here would put a
    write path one directory above the only place the plane scan tolerates
    one (D127)."""
    source = (REPO_ROOT / "pa_agent" / "session.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    internal = {name for name in imported if name.startswith("pa_agent")}
    assert internal == {"pa_agent.contracts"}, (
        f"pa_agent/session.py imports {sorted(internal)}; the state machine is "
        "pure and reaches no plane (D127)"
    )
