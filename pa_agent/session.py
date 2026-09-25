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
table, and a flag on the member would be a copy that v1.5 contradicts the
moment `AWAITING_DECISION` gives `IN_REVIEW` an outgoing edge.

**Advancing never writes.** `advance()` returns a new `Session` or raises, and
the caller persists it — so "an illegal transition raises and is never
recorded" (REQ-71) is structural rather than a rule the adapter has to
remember. A machine that wrote would have to be trusted not to write on the
failing path; this one has no way to.
"""

from __future__ import annotations

from pa_agent.contracts import Session, SessionRun, SessionState


class IllegalTransition(ValueError):
    """A move the table does not declare (REQ-71).

    Typed and carrying both ends plus the legal set, because the CLI maps it to
    a bad request and the message is what tells the reviewer what they *could*
    have done. A bare `ValueError` would reach stderr saying only that
    something was wrong with the request.
    """

    def __init__(self, frm: SessionState, to: SessionState) -> None:
        legal = [s.value for s in TRANSITIONS[frm]]
        super().__init__(
            f"cannot move a session from {frm.value} to {to.value}; "
            f"{frm.value} may become {legal or 'nothing — it is terminal'}"
        )
        self.frm = frm
        self.to = to
        self.legal = legal


#: Every state, and what it may become. Derived from US-12's own text:
#:
#: - *a session is created … in `CREATED`* — the entry state.
#: - *when it is run … moves to `DETERMINED`; a second run is a new snapshot,
#:   never an edit* — so `CREATED -> DETERMINED` and `DETERMINED ->
#:   DETERMINED`, the second being what makes re-running append rather than
#:   overwrite.
#: - spec §11: *`CREATED -> DETERMINED -> IN_REVIEW`, extended by v1.5* — so
#:   `IN_REVIEW` has no outgoing edge in this version and is terminal by that
#:   fact alone.
#:
#: `CREATED -> IN_REVIEW` is **absent on purpose**: reviewing a session that has
#: determined nothing is reviewing an empty packet, and `Session`'s own
#: validator already refuses a non-`CREATED` state with no run.
TRANSITIONS: dict[SessionState, tuple[SessionState, ...]] = {
    SessionState.CREATED: (SessionState.DETERMINED,),
    SessionState.DETERMINED: (SessionState.DETERMINED, SessionState.IN_REVIEW),
    SessionState.IN_REVIEW: (),
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
    fact about the table above, and a literal on the enum would be a copy that
    v1.5 contradicts the moment `AWAITING_DECISION` gives `IN_REVIEW` an
    outgoing edge.

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
