"""The session lifecycle, as a table and a function (T-100, D127, REQ-71).

Pure Python beside the port, the way `history.py` sits beside the graph. It
imports `contracts` and nothing else — no store, no path, no clock — so it
reaches neither plane and `tests/test_planes.py` has nothing to whitelist.

**The table is the vocabulary.** `TRANSITIONS` maps every `SessionState` to the
states it may move to, and `set(TRANSITIONS) == set(SessionState)` is asserted
at import: a state added to the enum without a row here fails at load rather
than becoming a state nothing can leave and nothing can reach. That is
`PREDICATES` against `PredicateKind`, one structure over (T-91, D110).

`terminal` is read off this table rather than declared on the enum, which is
D127's departure from `ErrorCode`'s idiom: terminality is a fact about the
table, and a flag on the member would be a copy that v1.5 contradicts. **It
did, at T-104** — `IN_REVIEW` gained the append self-edge and stopped being
terminal with no line outside `TRANSITIONS` edited (D132). D127 guessed the
wrong edge and the right mechanism. **And again at T-105**, in the other
direction: `DECIDED` arrived terminal because its row is empty, and the diff to
this module is three rows of a dict (D134).

**Nothing here writes.** `advance()` and `review()` each return a new `Session`
or raise, and the caller persists — so "an illegal transition raises and is
never recorded" (REQ-71) is structural rather than a rule the adapter has to
remember. A machine that wrote would have to be trusted not to write on the
failing path; this one has no way to.
"""

from __future__ import annotations

from pa_agent.contracts import ReviewEntry, Session, SessionRun, SessionState


class IllegalTransition(ValueError):
    """A move the table does not declare (REQ-71).

    Typed and carrying both ends plus the legal set, because the CLI maps it to
    a bad request and the message is what tells the reviewer what they *could*
    have done. A bare `ValueError` would reach stderr saying only that
    something was wrong with the request.
    """

    def __init__(self, frm: SessionState, to: SessionState) -> None:
        legal = [s.value for s in TRANSITIONS[frm]]
        # The `terminal` branch went unreachable at `T-104`, when `IN_REVIEW`
        # acquired the append self-edge and no state had an empty row any more
        # (D132), and was kept rather than deleted because `T-105`'s `DECIDED`
        # would restore it. It did: `DECIDED`'s row is empty, so a move out of a
        # closed session reads *may become nothing — it is terminal* (D134).
        super().__init__(
            f"cannot move a session from {frm.value} to {to.value}; "
            f"{frm.value} may become {legal or 'nothing — it is terminal'}"
        )
        self.frm = frm
        self.to = to
        self.legal = legal


class NoSuchSnapshot(ValueError):
    """A review that names a run the session does not hold (REQ-75).

    Its own type rather than an `IllegalTransition` with a field, because the
    next actions differ: that one says *determine it first*, this one says
    *name a run this session holds*. `resolver.py`'s four result types and
    `form.py`'s three refusals are the precedent.
    """

    def __init__(self, session_id: str, run_index: int, held: int) -> None:
        super().__init__(
            f"session {session_id} holds {held} run(s); a review of run "
            f"{run_index} is a review of nothing. A review binds to the "
            f"snapshot it reviewed (REQ-75)"
        )
        self.session_id = session_id
        self.run_index = run_index
        self.held = held


#: Every state, and what it may become. Derived from US-12's own text:
#:
#: - *a session is created … in `CREATED`* — the entry state.
#: - *when it is run … moves to `DETERMINED`; a second run is a new snapshot,
#:   never an edit* — so `CREATED -> DETERMINED` and `DETERMINED ->
#:   DETERMINED`, the second being what makes re-running append rather than
#:   overwrite.
#: - spec §11: *`CREATED -> DETERMINED -> IN_REVIEW`, extended by v1.5*.
#: - US-13: *her edits append to a review log* — plural, so `IN_REVIEW ->
#:   IN_REVIEW`, the append self-edge (T-104, D132). It is the reason
#:   `IN_REVIEW` is no longer terminal, and **nothing but this row changed** to
#:   make that true: terminality is read off this table, so D127's argument for
#:   deriving it rather than declaring it is paid off here rather than restated.
#:
#: - US-13: *when Sam submits it … the session is `AWAITING_DECISION`* — so
#:   `IN_REVIEW -> AWAITING_DECISION`, and **only** from there (T-105, D134).
#: - US-13: *given a payer's simulated decision, when it is recorded, then the
#:   session closes* — so `AWAITING_DECISION -> DECIDED`, and `DECIDED` has no
#:   row content at all, which is the **only** thing that makes it terminal.
#:
#: `CREATED -> IN_REVIEW` is **absent on purpose**: reviewing a session that has
#: determined nothing is reviewing an empty packet, and `Session`'s own
#: validator already refuses a non-`CREATED` state with no run.
#:
#: Three more absences, each a decision rather than an omission (D134):
#:
#: - `DETERMINED -> AWAITING_DECISION` — submitting without review. US-13's
#:   fourth bullet *is* this refusal: *the system never decides to transmit*, so
#:   an edge here would delete a story bullet rather than add a convenience.
#: - `IN_REVIEW -> DETERMINED` — re-running after a review. Every `ReviewEntry`
#:   binds to a `run_index` (REQ-75), so appending a snapshot afterwards leaves
#:   entries bound to a run nobody is packaging, and US-13 does not ask for it.
#: - `AWAITING_DECISION -> IN_REVIEW` — un-sending. The packet has left; an
#:   outbox with a file in it cannot support a session claiming not to have
#:   submitted, and `Session`'s submission validator would have to be relaxed to
#:   express it.
TRANSITIONS: dict[SessionState, tuple[SessionState, ...]] = {
    SessionState.CREATED: (SessionState.DETERMINED,),
    SessionState.DETERMINED: (SessionState.DETERMINED, SessionState.IN_REVIEW),
    SessionState.IN_REVIEW: (SessionState.IN_REVIEW, SessionState.AWAITING_DECISION),
    SessionState.AWAITING_DECISION: (SessionState.DECIDED,),
    SessionState.DECIDED: (),
}

assert set(TRANSITIONS) == set(SessionState), (
    "every SessionState needs a row in TRANSITIONS; a state with no row is one "
    "nothing can leave and nothing can reach, and no behavioural test would "
    "say so (D110's shape)"
)


def legal(frm: SessionState, to: SessionState) -> bool:
    """Whether the table declares this move. The one comparison."""
    return to in TRANSITIONS[frm]


def is_terminal(state: SessionState) -> bool:
    """True when the table declares no move out of `state`.

    Derived, never declared on the member (D127). `ErrorCode` carries its
    classification per member because nothing else holds it; terminality is a
    fact about the table above, and a literal on the enum would be a copy —
    which T-104 falsified: `IN_REVIEW` gained the append self-edge and this
    function started answering `False` for it with nothing here edited (D132).
    `T-105` then paid the argument off in the other direction — `DECIDED` is
    terminal because its row is empty, and no member, no flag and no line of this
    function was touched to make it so (D134). It is the only terminal state.

    It lives **here and not on `SessionState`** for a second reason, measured
    rather than guessed: a property on the enum means `contracts` imports
    `session`, `session` already imports `contracts`, and the resulting cycle
    makes every module in the package reach the session plane through the
    import graph. `tests/test_planes.py` failed on all six plane roots the
    first time it was run against that shape.
    """
    return not TRANSITIONS[state]


def advance(session: Session, to: SessionState, *, run: SessionRun | None = None) -> Session:
    """The session in its next state, or `IllegalTransition`.

    Returns a new object; nothing here writes. `run` is appended when given,
    which is the only way `runs` grows — US-12's *a second run is a new
    snapshot, never an edit*.

    Raises before constructing anything, so a refused move leaves the caller
    holding exactly what it passed in (REQ-71).
    """
    if not legal(session.state, to):
        raise IllegalTransition(session.state, to)

    runs = session.runs + (run,) if run is not None else session.runs
    return session.model_copy(update={"state": to, "runs": runs})


def review(session: Session, entry: ReviewEntry) -> Session:
    """The session with one more entry in its review log (REQ-75, T-104, D132).

    Appends and moves to `IN_REVIEW`, which from `IN_REVIEW` is the self-edge
    above — US-13's *her edits append*, plural. Returns a new object; nothing
    here writes, so a refused review is never recorded for `advance()`'s reason.

    **It names `runs` nowhere in what it writes.** A version that read
    `session.runs` and put the same value back in the update is
    indistinguishable from this one on every input, so the claim is held by
    parsing the `model_copy` call rather than by a behavioural test (D65's
    shape, D132). The `len(session.runs)` below is a read and reaches no update.

    The snapshot bound is checked **here as well as on the contract** because
    `model_copy` runs no validator: a `Session` validator alone would be
    unreachable through the only path that appends, and the verb would write a
    session `get()` cannot read back (measured, D132).
    """
    if not legal(session.state, SessionState.IN_REVIEW):
        raise IllegalTransition(session.state, SessionState.IN_REVIEW)
    if entry.run_index >= len(session.runs):
        raise NoSuchSnapshot(session.session_id, entry.run_index, len(session.runs))

    return session.model_copy(
        update={
            "state": SessionState.IN_REVIEW,
            "reviews": session.reviews + (entry,),
        }
    )
