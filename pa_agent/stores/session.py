"""The session plane's port, and the adapter that writes it (T-100, D127).

The fourth storage port, and the **first one that writes**. Every adapter
before it reads a corpus the repository ships: `data/policies/` is what a payer
covers, `data/patients/` is one person's chart, `data/knowledge/` is what a
drug is known to do. `data/sessions/` is none of those — it is the system's own
record of having answered a question, so it is gitignored, no manifest pins it,
and `verify_sources.py` does not reach it. Hashing it would assert that a
determination the system produced is a source the system can re-derive, which
is backwards (D127).

Being the first writer in the package is the thing to watch. `mkdir` and
`write_text` now appear inside `pa_agent/`, and what keeps that honest is that
they appear **here**: `tests/test_planes.py` scans every other module for
`open`, `Path` and their neighbours, and `STORAGE_SCAN_EXEMPT` stays one module
wide. `pa_agent/session.py` — the state machine — therefore has no write path
at all, which is also why "an illegal transition is never recorded" (REQ-71) is
structural: the thing that refuses the move cannot persist anything.

Four reads and one write, because that is what the four verbs ask:

- `create(session)` — write a session that does not exist yet. Raises on a
  collision rather than overwriting; a silent overwrite is a lost
  determination.
- `get(session_id)` — one session. **Raises** on an unknown id (D31).
- `list_sessions()` — every session, newest first. **Returns `[]`** on an
  empty or missing root, which inverts D31 for this port only and for a stated
  reason (D127): an empty knowledge table is a broken checkout, an empty
  session directory is nobody having created one, and `session list` is the
  first thing a fresh clone runs.
- `save(session)` — replace an existing session. Raises on one that does not
  exist, for `create`'s reason in the other direction.

It holds no policy and no chart, imports nothing from either plane, and must
never be made to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from pa_agent.contracts import Session

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SESSION_ROOT = REPO_ROOT / "data" / "sessions"

#: The on-disk suffix, named once. A directory holding anything else is a
#: directory this adapter did not write.
SUFFIX = ".json"


class SessionNotFound(LookupError):
    """No session in this store carries the id a request named.

    Typed and carrying what the store holds, in `UnknownJurisdiction`'s shape
    (T-87, D100). A bare `KeyError` would reach the CLI indistinguishable from
    an unknown patient, and `None` would read as *there is no such session*
    when the store may simply be pointed at the wrong root — D31's failure on a
    fourth port.
    """

    def __init__(self, session_id: str, known: list[str]) -> None:
        super().__init__(
            f"no session {session_id!r} in this store; it holds {known}"
        )
        self.session_id = session_id
        self.known = known


class SessionExists(ValueError):
    """`create` was given an id the store already holds.

    Its own type because the alternative is an overwrite, and the thing
    overwritten is a determination somebody may already have acted on.
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(
            f"session {session_id!r} already exists; a second determination is "
            "a new run on the same session, never a new session with its id"
        )
        self.session_id = session_id


@runtime_checkable
class SessionStore(Protocol):
    """What the verbs need of session storage, and nothing more."""

    def create(self, session: Session) -> None:
        """Write a session that does not exist. Raises `SessionExists` if it does."""
        ...

    def get(self, session_id: str) -> Session:
        """One session. Raises `SessionNotFound` rather than returning `None`."""
        ...

    def list_sessions(self) -> list[Session]:
        """Every session, newest first.

        Returns `[]` on an empty or missing root — the one place this package
        answers a well-formed empty rather than raising, because an empty
        session directory is a true fact about a fresh clone and not a broken
        one (D127).
        """
        ...

    def save(self, session: Session) -> None:
        """Replace an existing session. Raises `SessionNotFound` if absent."""
        ...


class LocalSessionStore:
    """File-backed adapter over `data/sessions/`, one JSON document per session.

    Serialization is the repository's file idiom — `indent=2`,
    `ensure_ascii=False`, trailing newline — with `sort_keys=False` so the
    bytes follow the contract's field order rather than the alphabet. Both are
    deterministic; the field order is the one a reader of `contracts.py`
    expects, and byte-stability is asserted against writing the same session
    twice rather than against a committed fixture, because the root is
    per-user.

    Nothing here generates a value. `created_at`, `ran_at` and the session id
    arrive on the object already set, so writing a session twice produces the
    same bytes — a `datetime.now()` in a serializer is the classic way to make
    a round-trip test pass on the first write and fail on the second.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_SESSION_ROOT

    @property
    def root(self) -> Path:
        """Where this store writes. Read by the CLI for its own messages."""
        return self._root

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _path(self, session_id: str) -> Path:
        """The file one session lives in.

        The id is used as a filename, so it may not be a path. A session id
        carrying a separator would let `create` write outside the root, which
        is a directory traversal through a store.
        """
        if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
            raise ValueError(
                f"session id {session_id!r} is not usable as a filename; ids "
                "are opaque tokens and a separator in one would write outside "
                "the session root"
            )
        return self._root / f"{session_id}{SUFFIX}"

    def _known(self) -> list[str]:
        if not self._root.exists():
            return []
        return sorted(p.stem for p in self._root.glob(f"*{SUFFIX}"))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def _serialize(self, session: Session) -> str:
        """The bytes one session becomes. Deterministic, and generates nothing.

        **Every write is validated here**, before any file is opened (T-138,
        D140). `model_copy(update=…)` runs no validator (D132), and
        `advance()`, `review()` and the submit and decide verbs all hand this
        adapter a `model_copy`. So the session is run back through its own
        validators, and a session `get()` could not read back is refused rather
        than written. `create` and `save` both come through this one function,
        which is why the check lives here and not in either of them.
        """
        checked = Session.model_validate(session.model_dump())
        return (
            json.dumps(
                checked.model_dump(mode="json"),
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )

    # ------------------------------------------------------------------
    # The port
    # ------------------------------------------------------------------

    def create(self, session: Session) -> None:
        path = self._path(session.session_id)
        if path.exists():
            raise SessionExists(session.session_id)
        self._root.mkdir(parents=True, exist_ok=True)
        path.write_text(self._serialize(session), encoding="utf-8")

    def get(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFound(session_id, self._known())
        return Session.model_validate_json(path.read_text(encoding="utf-8"))

    def list_sessions(self) -> list[Session]:
        if not self._root.exists():
            return []
        sessions = [
            Session.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(self._root.glob(f"*{SUFFIX}"))
        ]
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)

    def save(self, session: Session) -> None:
        path = self._path(session.session_id)
        if not path.exists():
            raise SessionNotFound(session.session_id, self._known())
        path.write_text(self._serialize(session), encoding="utf-8")
