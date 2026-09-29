"""The payer directory's port, and the adapter that reads it (T-105, D134).

The fifth storage port, and a **read corpus the repository ships** — which is
why it is a module of its own rather than a half of the outbox beside it. One
protocol over a gitignored write root and a tracked read corpus would undo the
separation D127 spent a section making: the two answer *what does empty mean*
in opposite directions, and a protocol carrying both has to document the
exception instead of the rule.

`data/payers/payers.json` holds **contacts**, not coverage. Nothing here binds a
code, a state or a criteria tree; the payer *axis* — a request resolving by payer
as well as by code and state — is v2.0's (D112, D125). What this serves is the
`To:` header a packet is addressed to, replacing the placeholder `T-103` declared
(D131): the shape of that string does not move, its **source** does.

**It is committed and deliberately not in `verify_sources.py`.** That gate
verifies committed bytes against a public re-download, and this file is
synthesized — there is no upstream, so a manifest record for it would carry no
URL and `--fetch` nothing to fetch, which is a hashed record asserting provenance
it does not have. It is v2.1's synthetic rule arriving early: *a policy artifact
the project synthesized declares itself synthetic and carries no fetched-corpus
provenance*. A second reason is about the checked claims — *nine policy
documents* and *five FDA labels* are counts `tests/test_docs_consistency.py`
re-derives from the two hashed manifests, and a payer record able to raise either
one would make two corpora into one. `tests/test_payers.py` holds the file
instead.

Two reads, because two is what the verbs ask:

- `get_payer(payer_id)` — one recipient. **Raises** on an unknown id (D31).
- `list_payers()` — every recipient, in file order. **Raises** on a missing or
  empty file, which is where this port and the outbox beside it part company: an
  empty shipped corpus is a broken checkout, and reporting it as *there is
  nobody to send to* is the well-formed empty answer every downstream check
  would agree with (D31, D39). It does **not** inherit D127's inversion, which
  was granted to the session plane for the one reason that its contents are
  written by the system.

It holds no policy, no chart and no drug label, imports nothing from any plane,
and must never be made to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from pa_agent.contracts import SimulatedPayer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PAYER_ROOT = REPO_ROOT / "data" / "payers"

#: The one file this adapter reads, named once.
DIRECTORY_FILE = "payers.json"


class PayerNotFound(LookupError):
    """No record in the directory carries the id a request named.

    Typed and carrying what the directory does hold, in `SessionNotFound`'s and
    `UnknownJurisdiction`'s shape. A `None` here would send a packet to an empty
    recipient header, which is a document that leaves addressed to nobody.
    """

    def __init__(self, payer_id: str, known: list[str]) -> None:
        super().__init__(
            f"no payer {payer_id!r} in the directory; it serves {known}"
        )
        self.payer_id = payer_id
        self.known = known


@runtime_checkable
class PayerStore(Protocol):
    """What the verbs need of the payer directory, and nothing more."""

    def get_payer(self, payer_id: str) -> SimulatedPayer:
        """One recipient. Raises `PayerNotFound` rather than returning `None`."""
        ...

    def list_payers(self) -> list[SimulatedPayer]:
        """Every recipient, in file order.

        Raises on a missing or empty directory. This is a corpus the repository
        ships, so an empty one is a broken checkout and not a true fact about a
        fresh clone — D31 unmodified, and the half of D127's inversion that does
        not travel.
        """
        ...


class LocalPayerStore:
    """File-backed adapter over `data/payers/payers.json`.

    Read-only: this port has no write path at all, which is what keeps it on the
    other side of the line from `outbox.py`. Every malformed shape raises here,
    at load, naming the file — `SimulatedPayer`'s own validators then refuse a
    record that does not declare itself simulated or is addressed outside RFC
    2606's reserved `.invalid` TLD, so a deliverable address cannot reach a
    packet through this port.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_PAYER_ROOT
        self._path = self._root / DIRECTORY_FILE
        self._payers: list[SimulatedPayer] | None = None

    @property
    def root(self) -> Path:
        """Where this store reads. Read by the CLI for its own messages."""
        return self._root

    def _load(self) -> list[SimulatedPayer]:
        if self._payers is not None:
            return self._payers

        if not self._path.exists():
            raise KeyError(
                f"no {DIRECTORY_FILE} under {self._root}. The payer directory is "
                "a committed artifact; an absent one is a broken checkout, not a "
                "directory with nobody in it (D31, D134)."
            )
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        raw = payload.get("payers") or []
        if not raw:
            raise ValueError(
                f"{DIRECTORY_FILE} carries no payers. An empty directory means "
                "no packet can ever be addressed, and every check downstream "
                "would agree with it (D31's shape)."
            )

        payers = [SimulatedPayer(**record) for record in raw]
        seen: set[str] = set()
        for payer in payers:
            if payer.payer_id in seen:
                raise ValueError(
                    f"{DIRECTORY_FILE} declares payer {payer.payer_id!r} twice; "
                    "an id served by two records is a recipient chosen by file "
                    "order."
                )
            seen.add(payer.payer_id)

        self._payers = payers
        return payers

    def get_payer(self, payer_id: str) -> SimulatedPayer:
        for payer in self._load():
            if payer.payer_id == payer_id:
                return payer
        raise PayerNotFound(payer_id, [p.payer_id for p in self._load()])

    def list_payers(self) -> list[SimulatedPayer]:
        return list(self._load())
