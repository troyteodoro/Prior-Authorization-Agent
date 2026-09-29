"""The payer outbox's adapter (T-105, D134, REQ-77).

`tests/test_session_store.py`'s shape on a sixth port, and the file where the
**asymmetry** between the two write planes and the four read corpora is asserted
rather than described. This root is output: an empty listing is nobody having
submitted, which is D127's inversion of D31 and the second place it applies.
`tests/test_payers.py` holds the other side — a shipped corpus raises on an empty
read — and both are here because an inversion granted for a reason spreads by
imitation if nobody writes the reason down.

Two things this file is careful about, both learned:

- **nothing may write to the default root.** A test that forgets `tmp_path`
  writes into the working tree and the suite still passes, silently. The session
  plane has an AST guard for exactly that (D127) and this version creates the
  same hazard one root over — sharper, because `put` is a write a mutation run
  will execute (D119's `--declare-additions`, T-91's `rmtree`).
- **the digest and the file come from one encoding.** Two hash calls over two
  encodings of one document is two answers to one question, and they agree today
  and one platform-newline setting from now.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from pa_agent.contracts import OutboxArtifact
from pa_agent.stores.outbox import (
    DEFAULT_OUTBOX_ROOT,
    SUFFIX,
    LocalOutboxStore,
    OutboxArtifactExists,
    OutboxArtifactNotFound,
    OutboxStore,
    digest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "pa_agent"

PAYER = "sim-national-a"
BODY = "To: Simulated National Payer A <prior-auth@sim-national-a.invalid>\n\nbody\n"

#: Exactly the fields an artifact carries (D51's shape). Every one is derivable
#: **from the outbox alone** — two path segments, a digest and a size — so the
#: adapter generates nothing and a listing is a fact about the directory rather
#: than about when it was read. The submission clock lives on
#: `Session.submission`, where the session records what it did.
ARTIFACT_FIELDS = {"payer_id", "artifact_id", "sha256", "byte_count"}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "outbox"


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


def test_the_adapter_satisfies_the_port():
    assert isinstance(LocalOutboxStore(), OutboxStore)


def test_the_default_root_is_the_outbox_directory():
    """Asserted rather than exercised: the root is gitignored and per-user, so no
    gate writes to it (D127's rule, D134's second application)."""
    assert LocalOutboxStore().root == DEFAULT_OUTBOX_ROOT
    assert DEFAULT_OUTBOX_ROOT == REPO_ROOT / "data" / "outbox"


def test_the_outbox_root_is_gitignored():
    """The packet that left is output, not evidence. Tracking it would mean every
    local run writes into a tracked directory — the hazard T-91 and D119 are
    about, and the reason `verify_sources.py` does not reach it."""
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/outbox/" in ignored


def test_an_artifact_carries_exactly_these_fields():
    assert set(OutboxArtifact.model_fields) == ARTIFACT_FIELDS


# --------------------------------------------------------------------------
# put / get / list
# --------------------------------------------------------------------------


def test_put_writes_one_file_under_the_payer_and_reports_it(root):
    store = LocalOutboxStore(root)
    artifact = store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)

    written = sorted(root.rglob(f"*{SUFFIX}"))
    assert len(written) == 1
    assert written[0] == root / PAYER / f"s1-run0{SUFFIX}"
    assert artifact.payer_id == PAYER
    assert artifact.artifact_id == "s1-run0"
    assert artifact.byte_count == len(BODY.encode("utf-8"))


def test_the_reported_digest_is_the_digest_of_the_bytes_on_disk(root):
    """The one comparison a gate can make: what the session recorded against what
    is in the outbox. Taken over the written bytes rather than over a second
    encoding of the same text (D134)."""
    store = LocalOutboxStore(root)
    artifact = store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    on_disk = (root / PAYER / f"s1-run0{SUFFIX}").read_bytes()
    assert artifact.sha256 == hashlib.sha256(on_disk).hexdigest()
    assert artifact.sha256 == digest(BODY)


def test_get_returns_the_body_verbatim(root):
    store = LocalOutboxStore(root)
    store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    assert store.get(payer_id=PAYER, artifact_id="s1-run0") == BODY


def test_putting_the_same_artifact_twice_raises_rather_than_overwriting(root):
    """A sent packet is not re-sendable, and the thing an overwrite destroys is
    the document somebody was sent (`SessionExists`' argument, one store over).

    The lifecycle refuses a second `submit` first — `AWAITING_DECISION` has no
    self-edge — so this is the second line of defence. It becomes the first on the
    day a resubmission edge exists, which is why it is written now.
    """
    store = LocalOutboxStore(root)
    store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    with pytest.raises(OutboxArtifactExists, match="already in payer"):
        store.put(payer_id=PAYER, artifact_id="s1-run0", body="different")
    assert store.get(payer_id=PAYER, artifact_id="s1-run0") == BODY


def test_an_unknown_artifact_raises_rather_than_returning_none(root):
    """D31 on a sixth port. `None` would read as *this packet was never sent*
    when the store may simply be pointed at the wrong root."""
    store = LocalOutboxStore(root)
    store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    with pytest.raises(OutboxArtifactNotFound, match="no artifact 'nope'"):
        store.get(payer_id=PAYER, artifact_id="nope")


def test_the_not_found_error_names_what_that_payers_outbox_does_hold(root):
    store = LocalOutboxStore(root)
    store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    store.put(payer_id=PAYER, artifact_id="s2-run0", body=BODY)
    with pytest.raises(OutboxArtifactNotFound) as caught:
        store.get(payer_id=PAYER, artifact_id="s3-run0")
    assert caught.value.known == ["s1-run0", "s2-run0"]


def test_listing_a_missing_root_is_empty_rather_than_a_failure(root):
    """D127's inversion, and the **second** place it applies (D134).

    An empty outbox is nobody having submitted, which is a true fact about a
    fresh clone. The payer directory beside it raises on an empty file, because
    that one is a corpus the repository ships — and the asymmetry is the point
    rather than an exception to be quiet about.
    """
    assert LocalOutboxStore(root).list_artifacts() == []


def test_listing_an_empty_root_is_empty(root):
    root.mkdir(parents=True)
    assert LocalOutboxStore(root).list_artifacts() == []


def test_listing_is_by_payer_then_artifact(root):
    store = LocalOutboxStore(root)
    store.put(payer_id="sim-regional-b", artifact_id="s9-run0", body=BODY)
    store.put(payer_id=PAYER, artifact_id="s2-run0", body=BODY)
    store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)

    listed = [(a.payer_id, a.artifact_id) for a in store.list_artifacts()]
    assert listed == [
        (PAYER, "s1-run0"),
        (PAYER, "s2-run0"),
        ("sim-regional-b", "s9-run0"),
    ]


def test_a_listing_re_derives_the_digest_from_the_file(root):
    """So a gate can compare the outbox to a session without trusting either.

    `put`'s return value and a later listing are two paths to one number, and if
    they could disagree the recorded hash would be a claim about the writer rather
    than about the file.
    """
    store = LocalOutboxStore(root)
    put = store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    (listed,) = store.list_artifacts()
    assert listed == put


# --------------------------------------------------------------------------
# Both path segments are guarded
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..\\escape", ".", "", ".."])
def test_a_payer_id_that_is_a_path_is_refused(root, bad):
    """Both segments reach a path, so both are guarded. A separator in either
    would let `put` write outside the root, which is a directory traversal
    through a store (`stores/session.py`'s guard, on two segments)."""
    store = LocalOutboxStore(root)
    with pytest.raises(ValueError, match="not usable as a path segment"):
        store.put(payer_id=bad, artifact_id="s1-run0", body=BODY)
    assert not list(root.parent.rglob(f"escape*{SUFFIX}")), "an artifact escaped"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..\\escape", ".", "", ".."])
def test_an_artifact_id_that_is_a_path_is_refused(root, bad):
    store = LocalOutboxStore(root)
    with pytest.raises(ValueError, match="not usable as a path segment"):
        store.put(payer_id=PAYER, artifact_id=bad, body=BODY)
    assert not list(root.parent.rglob(f"escape*{SUFFIX}")), "an artifact escaped"


def test_the_guard_also_refuses_on_the_read_path(root):
    """`get` takes bare strings from the CLI, so the guard stands between that
    and reading outside the root — `LocalSessionStore`'s argument for guarding
    `get` and not only `create`."""
    store = LocalOutboxStore(root)
    with pytest.raises(ValueError, match="not usable as a path segment"):
        store.get(payer_id=PAYER, artifact_id="../../etc/passwd")


# --------------------------------------------------------------------------
# Article VI — it holds a rendered document as text, and nothing else
# --------------------------------------------------------------------------


def test_the_adapter_generates_nothing(root):
    """No clock inside the writer, so what is on disk is what it was handed.

    A `datetime.now()` in a serializer makes a round trip pass on the first write
    and fail on the second (D127, measured). Here the body **is** the input, so
    the claim is that the file equals it byte for byte and the artifact record
    carries no timestamp at all.
    """
    store = LocalOutboxStore(root)
    artifact = store.put(payer_id=PAYER, artifact_id="s1-run0", body=BODY)
    assert (root / PAYER / f"s1-run0{SUFFIX}").read_bytes() == BODY.encode("utf-8")
    assert not any(
        "at" in name or "time" in name for name in OutboxArtifact.model_fields
    ), "an outbox artifact carries a clock; the submission's clock is the session's"
    assert artifact.byte_count == len(BODY.encode("utf-8"))


def test_no_corpus_type_is_reachable_from_an_artifact():
    """Article VI on the contract graph (REQ-70).

    The artifact record names a file and hashes it; the packet itself is **text**,
    because Article VI's line is drawn at resources rather than at text and a
    determination's quoted spans travel with it exactly as they reach stdout.
    """
    from pydantic import BaseModel

    for field in OutboxArtifact.model_fields.values():
        annotation = field.annotation
        assert not (
            isinstance(annotation, type) and issubclass(annotation, BaseModel)
        ), f"an OutboxArtifact reaches {annotation}; it names a file, it is not one"


# --------------------------------------------------------------------------
# Where it may be written, and by whom
# --------------------------------------------------------------------------


def test_only_the_store_names_the_outbox_root():
    """Article VI's storage rule, on the sixth port (D25, REQ-41)."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent.name == "stores":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "data/outbox" in node.value:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        f"{offenders} names the outbox root; it reaches the system through the "
        "port, constructed in cli.py (REQ-41, D134)"
    )


def test_the_outbox_store_is_constructed_only_in_the_cli():
    """REQ-41 over the sixth port, the behavioural half."""
    cli_source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
    assert "LocalOutboxStore(" in cli_source
    for module in PACKAGE.rglob("*.py"):
        if module.name == "cli.py" or "stores" in module.parts:
            continue
        assert "LocalOutboxStore" not in module.read_text(encoding="utf-8"), (
            f"{module} constructs an outbox store; the CLI is the one place a "
            "store is constructed (REQ-41)"
        )


def test_no_test_in_this_file_writes_to_the_default_root():
    """The guard for the guard, and the reason it exists **here** (D134).

    `put` is the first write this repository's mutation runs can execute inside a
    committed-adjacent directory since `select_patients.py --declare-additions`
    (D119) and `synthesize_notes.py --generate` (T-91). A test that constructed
    the adapter with no root would write into `data/outbox/` in the working tree
    and nothing else would say so.

    Parsed rather than grepped: a substring scan for the constructor matches this
    test's own text, which is a check that reports itself and can never report
    anything else — measured on the session plane, where the text form failed on
    its first run, on itself (D127).
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))

    def _is_construction(node) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "LocalOutboxStore"
        )

    # A root-less adapter may be **inspected** and never used: the two tests that
    # build one read `.root` or hand it to `isinstance`, and neither touches the
    # filesystem. Any other appearance is a write into the working tree.
    inspected = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "root":
            if _is_construction(node.value):
                inspected.add(id(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and node.args
            and _is_construction(node.args[0])
        ):
            inspected.add(id(node.args[0]))

    for node in ast.walk(tree):
        if _is_construction(node) and not (node.args or node.keywords):
            assert id(node) in inspected, (
                f"line {node.lineno} builds the outbox adapter with no root and "
                "does something other than read `.root` or check the protocol; "
                "that writes into data/outbox/ in the working tree"
            )
