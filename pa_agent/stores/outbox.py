"""The payer outbox's port, and the adapter that writes it (T-105, D134).

The sixth storage port, and the **second that writes** — `stores/session.py` was
the first (T-100, D127). What it holds is not a `Session`: it is the **rendered
packet**, the same bytes `session packet` prints, so writing it through the
session adapter would falsify that module's own docstring — *four reads and one
write, because that is what the four verbs ask* — and put two planes behind one
protocol.

`data/outbox/` is **output, not evidence**, for exactly the reasons D127 gave the
session root: no manifest pins it, `verify_sources.py` does not reach it, nothing
re-hashes it, and it is gitignored. Hashing it would assert that a document the
system produced is a source the system can re-derive, which is backwards. It
follows that byte-stability here is checked against `tmp_path` and the default
root is asserted by a test rather than exercised by a gate.

**The body is text.** Article VI's line is drawn at resources rather than at
text (REQ-70), and a determination's quoted spans travel with it exactly as they
reach the CLI's stdout — so the packet is stdout on disk, and nothing here is a
`Document` or a FHIR resource.

Layout is `<root>/<payer_id>/<artifact_id>.eml`, so the recipient is recoverable
from the path and a listing needs no sidecar record. Both segments reach a path
and both are guarded: an id carrying a separator would write outside the root,
which is a directory traversal through a store.

Three operations, because three is what the verbs and their graders ask:

- `put(payer_id, artifact_id, body)` — write a packet that does not exist yet,
  returning the `OutboxArtifact` it became. **Raises on a collision**: a sent
  packet is not re-sendable, and the thing an overwrite would destroy is the
  document somebody was sent (`SessionExists`' argument, on a second store).
- `get(payer_id, artifact_id)` — the bytes, as text. **Raises** on an unknown
  artifact (D31).
- `list_artifacts()` — every artifact, by payer then id. **Returns `[]`** on an
  empty or missing root, which is D127's inversion and the one place it applies
  here: this root is written by the system, and empty means nobody has submitted.
  The port beside it, `stores/payer.py`, **raises** on an empty file, because
  that one is a corpus the repository ships. The asymmetry is the point rather
  than an exception to be quiet about.

**The adapter generates nothing.** `put` writes the text it is handed and
computes a digest over those same bytes, so there is no clock inside it and two
writes of one body would be byte-identical — a `datetime.now()` in a serializer
is the classic way a round trip passes on the first write and fails on the
second (D127). The submission's clock lives on `Session.submission`, where the
session records what it did.

It holds no policy, no chart, no drug label and no session, imports nothing from
any plane, and must never be made to.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from pa_agent.contracts import OutboxArtifact

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTBOX_ROOT = REPO_ROOT / "data" / "outbox"

#: The on-disk suffix, named once. The packet is email-shaped (T-103, D131).
SUFFIX = ".eml"


class OutboxArtifactNotFound(LookupError):
    """No artifact in this outbox carries the ids a request named.

    Typed and carrying what the outbox holds, in `SessionNotFound`'s shape. A
    `None` here would read as *this packet was never sent* when the store may
    simply be pointed at the wrong root, which is D31's failure on a sixth port.
    """

    def __init__(self, payer_id: str, artifact_id: str, known: list[str]) -> None:
        super().__init__(
            f"no artifact {artifact_id!r} for payer {payer_id!r} in this outbox; "
            f"it holds {known}"
        )
        self.payer_id = payer_id
        self.artifact_id = artifact_id
        self.known = known


class OutboxArtifactExists(ValueError):
    """`put` was given ids the outbox already holds.

    Its own type because the alternative is an overwrite, and the thing
    overwritten is the document somebody was sent. The lifecycle refuses a second
    `submit` first — `AWAITING_DECISION` has no self-edge — so this is the second
    line of defence and becomes the first on the day a resubmission edge exists.
    """

    def __init__(self, payer_id: str, artifact_id: str) -> None:
        super().__init__(
            f"artifact {artifact_id!r} is already in payer {payer_id!r}'s outbox; "
            "a sent packet is not re-sendable, and an overwrite would destroy the "
            "document that left"
        )
        self.payer_id = payer_id
        self.artifact_id = artifact_id


@runtime_checkable
class OutboxStore(Protocol):
    """What submission and its graders need of the outbox, and nothing more."""

    def put(self, *, payer_id: str, artifact_id: str, body: str) -> OutboxArtifact:
        """Write a packet that does not exist. Raises `OutboxArtifactExists` if it does."""
        ...

    def get(self, *, payer_id: str, artifact_id: str) -> str:
        """The rendered packet, as text. Raises `OutboxArtifactNotFound`."""
        ...

    def list_artifacts(self) -> list[OutboxArtifact]:
        """Every artifact, by payer then artifact id.

        Returns `[]` on an empty or missing root — this root is the system's own
        output, so empty means nobody has submitted, which is D127's reason on a
        second write plane. The payer directory raises on an empty file for the
        opposite reason, and the asymmetry is deliberate.
        """
        ...


def digest(body: str) -> str:
    """The sha256 a session records, over the bytes that were written.

    Exported because the session's `SubmissionRecord` names the same digest, and
    two hash calls over two encodings of one document would be two answers to one
    question — the shape D131 refused for the renderer.
    """
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class LocalOutboxStore:
    """File-backed adapter over `data/outbox/`, one `.eml` per artifact.

    The body is encoded **once** and both the file and the digest are taken from
    those same bytes, so the hash a session records and the file on disk agree by
    construction rather than by two implementations that happen to agree. Writing
    with `write_text` and hashing `body.encode()` separately is the same value
    today and one platform-newline setting away from not being.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_OUTBOX_ROOT

    @property
    def root(self) -> Path:
        """Where this store writes. Read by the CLI for its own messages."""
        return self._root

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @staticmethod
    def _segment(kind: str, value: str) -> str:
        """One path segment, or `ValueError`.

        Both the payer id and the artifact id reach a path, so both are guarded.
        A separator in either would let `put` write outside the root, which is a
        directory traversal through a store — `stores/session.py`'s guard, on two
        segments instead of one.
        """
        if (
            not value
            or "/" in value
            or "\\" in value
            or value in (".", "..")
        ):
            raise ValueError(
                f"{kind} {value!r} is not usable as a path segment; ids are "
                "opaque tokens and a separator in one would write outside the "
                "outbox root"
            )
        return value

    def _path(self, payer_id: str, artifact_id: str) -> Path:
        return (
            self._root
            / self._segment("payer id", payer_id)
            / f"{self._segment('artifact id', artifact_id)}{SUFFIX}"
        )

    def _known(self, payer_id: str) -> list[str]:
        directory = self._root / payer_id if payer_id else self._root
        if not directory.exists():
            return []
        return sorted(p.stem for p in directory.glob(f"*{SUFFIX}"))

    # ------------------------------------------------------------------
    # The port
    # ------------------------------------------------------------------

    def put(self, *, payer_id: str, artifact_id: str, body: str) -> OutboxArtifact:
        path = self._path(payer_id, artifact_id)
        if path.exists():
            raise OutboxArtifactExists(payer_id, artifact_id)
        encoded = body.encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return OutboxArtifact(
            payer_id=payer_id,
            artifact_id=artifact_id,
            sha256=digest(body),
            byte_count=len(encoded),
        )

    def get(self, *, payer_id: str, artifact_id: str) -> str:
        path = self._path(payer_id, artifact_id)
        if not path.exists():
            raise OutboxArtifactNotFound(
                payer_id, artifact_id, self._known(payer_id)
            )
        return path.read_text(encoding="utf-8")

    def list_artifacts(self) -> list[OutboxArtifact]:
        if not self._root.exists():
            return []
        artifacts: list[OutboxArtifact] = []
        for directory in sorted(p for p in self._root.iterdir() if p.is_dir()):
            for path in sorted(directory.glob(f"*{SUFFIX}")):
                body = path.read_text(encoding="utf-8")
                artifacts.append(
                    OutboxArtifact(
                        payer_id=directory.name,
                        artifact_id=path.stem,
                        sha256=digest(body),
                        byte_count=len(body.encode("utf-8")),
                    )
                )
        return artifacts
