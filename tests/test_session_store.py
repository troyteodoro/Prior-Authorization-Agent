"""The session plane's adapter (T-100, D127, REQ-69, REQ-70).

`tests/test_knowledge_store.py`'s shape on a fourth port, with one inversion
this file is mostly about: `list_sessions()` answers `[]` where every other
port raises, and `get()` still raises. Both halves are asserted, because the
inversion is only safe while the asymmetry holds (D127).

Two of these tests were written against a specific way of passing for the wrong
reason:

- **byte-stability writes twice.** A round trip that writes once cannot see a
  `datetime.now()` inside the serializer, which is the classic way a session
  store starts producing different bytes for the same session.
- **the Article VI check walks the contract graph**, not the serialized keys. A
  test looking for the string `resourceType` passes on a policy document
  embedded as raw text, which carries no key at all.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    Determination,
    DeterminationOutcome,
    Document,
    Intake,
    PayerDecision,
    PayerDecisionOutcome,
    ReviewAction,
    ReviewEntry,
    Session,
    SessionRun,
    SessionState,
    SubmissionRecord,
)
from pa_agent.stores.session import (
    DEFAULT_SESSION_ROOT,
    LocalSessionStore,
    SessionExists,
    SessionNotFound,
    SessionStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "pa_agent"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


def _determination() -> Determination:
    return Determination(
        patient_id="07a5f345",
        procedure_code="43775",
        policy_version_id="ncd-100.1-jf-v1",
        payer="medicare",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
    )


def _run(ran_at: str = "2026-09-25T10:00:00Z") -> SessionRun:
    return SessionRun(
        ran_at=ran_at,
        as_of="2026-09-25",
        policy_version_id="ncd-100.1-jf-v1",
        determination=_determination(),
    )


def _session(
    session_id: str = "s1",
    created_at: str = "2026-09-25T09:00:00Z",
    state: SessionState = SessionState.CREATED,
    runs: tuple = (),
) -> Session:
    return Session(
        session_id=session_id,
        created_at=created_at,
        intake=Intake(
            patient_id="07a5f345", procedure_code="43775", icd10_codes=("E66.01",)
        ),
        state=state,
        runs=runs,
    )


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


def test_the_adapter_satisfies_the_port():
    assert isinstance(LocalSessionStore(), SessionStore)


def test_the_default_root_is_the_session_directory():
    """Asserted rather than exercised: the root is gitignored and per-user, so
    no gate writes to it (D127). A test that constructed the adapter without a
    root and wrote would pollute the working tree."""
    assert LocalSessionStore().root == DEFAULT_SESSION_ROOT
    assert DEFAULT_SESSION_ROOT == REPO_ROOT / "data" / "sessions"


def test_the_session_root_is_gitignored():
    """The corpus is output, not evidence. Tracking it would mean every local
    run writes into a tracked directory — the hazard T-91 and D119 are about."""
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/sessions/" in ignored


# --------------------------------------------------------------------------
# The inversion, and the half of it that does not invert
# --------------------------------------------------------------------------


def test_listing_a_missing_root_is_empty_rather_than_a_failure(root):
    """D31 inverted, for this port only and for a stated reason (D127).

    An empty knowledge table is a broken checkout; an empty session directory
    is nobody having created one, and `session list` is the first thing a fresh
    clone runs.
    """
    assert LocalSessionStore(root).list_sessions() == []


def test_listing_an_empty_root_is_empty(root):
    root.mkdir(parents=True)
    assert LocalSessionStore(root).list_sessions() == []


def test_an_unknown_id_raises_rather_than_returning_none(root):
    """The half that does **not** invert. *This store holds no sessions* and
    *this store does not hold the one you named* are different facts, and only
    the second is a lookup failure (D31, D127)."""
    store = LocalSessionStore(root)
    store.create(_session("s1"))
    with pytest.raises(SessionNotFound, match="no session 'nope'"):
        store.get("nope")


def test_the_not_found_error_names_what_the_store_does_hold(root):
    store = LocalSessionStore(root)
    store.create(_session("s1"))
    store.create(_session("s2"))
    with pytest.raises(SessionNotFound) as caught:
        store.get("s3")
    assert caught.value.known == ["s1", "s2"]


def test_creating_over_an_existing_session_raises(root):
    """The overwritten thing is a determination somebody may have acted on."""
    store = LocalSessionStore(root)
    store.create(_session("s1"))
    with pytest.raises(SessionExists, match="already exists"):
        store.create(_session("s1"))


def test_saving_a_session_that_does_not_exist_raises(root):
    with pytest.raises(SessionNotFound):
        LocalSessionStore(root).save(_session("s1"))


def test_a_session_id_that_is_a_path_is_refused(root):
    """An id is an opaque token. A separator in one would write outside the
    root, which is a directory traversal through a store."""
    store = LocalSessionStore(root)
    for bad in ("../escape", "a/b", "..\\escape", "."):
        with pytest.raises(ValueError, match="not usable as a filename"):
            store.create(_session(bad))
    assert not list(root.parent.glob("escape*")), "a session escaped the root"


def test_the_guard_also_refuses_an_id_no_contract_could_carry(root):
    """`Session` refuses an empty id at construction, so `create` can never see
    one — but `get` and `save` take a bare string from the CLI, and the guard is
    what stands between that and `root/.json`."""
    store = LocalSessionStore(root)
    with pytest.raises(ValueError, match="not usable as a filename"):
        store.get("")


# --------------------------------------------------------------------------
# Byte stability — written twice, on purpose
# --------------------------------------------------------------------------


def test_a_session_round_trips_byte_stable(root):
    """A12's second clause.

    **Written twice.** One write cannot see a `datetime.now()` in the
    serializer; the second can. The session carries two runs because the bug
    most likely to hide here is a timestamp regenerated per run rather than per
    session.
    """
    store = LocalSessionStore(root)
    session = _session(
        state=SessionState.DETERMINED, runs=(_run("first"), _run("second"))
    )

    store.create(session)
    first = (root / "s1.json").read_bytes()

    read_back = store.get("s1")
    assert read_back == session

    store.save(read_back)
    second = (root / "s1.json").read_bytes()

    assert first == second, "writing the same session twice produced different bytes"

    store.save(store.get("s1"))
    assert (root / "s1.json").read_bytes() == first


def test_the_serialized_form_is_json_with_a_trailing_newline(root):
    store = LocalSessionStore(root)
    store.create(_session())
    text = (root / "s1.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["session_id"] == "s1"


def test_listing_is_newest_first(root):
    store = LocalSessionStore(root)
    store.create(_session("older", created_at="2026-09-01T00:00:00Z"))
    store.create(_session("newer", created_at="2026-09-20T00:00:00Z"))
    assert [s.session_id for s in store.list_sessions()] == ["newer", "older"]


def test_a_run_appended_through_the_store_keeps_the_earlier_one(root):
    """US-12's *a second run is a new snapshot, never an edit*, through the
    write path rather than only through the machine."""
    from pa_agent.session import advance

    store = LocalSessionStore(root)
    store.create(_session())
    store.save(advance(store.get("s1"), SessionState.DETERMINED, run=_run("first")))
    store.save(advance(store.get("s1"), SessionState.DETERMINED, run=_run("second")))

    assert [r.ran_at for r in store.get("s1").runs] == ["first", "second"]


# --------------------------------------------------------------------------
# Every write is validated, because model_copy validates nothing (T-138, D140)
# --------------------------------------------------------------------------


def _on_disk(root: Path, session_id: str) -> tuple[bytes, int]:
    """Bytes **and** `st_mtime_ns`: a refused write is *never recorded*, which
    bytes alone cannot tell from a same-content rewrite (D132)."""
    path = root / f"{session_id}.json"
    return path.read_bytes(), path.stat().st_mtime_ns


def _a_review(run_index: int) -> ReviewEntry:
    return ReviewEntry(
        at="2026-09-25T11:00:00Z", reviewer="a reviewer",
        action=ReviewAction.NOTE, run_index=run_index, note="a note",
    )


def _submission() -> SubmissionRecord:
    return SubmissionRecord(
        artifact_id="a1", sha256="0" * 64, payer_id="p1",
        submitted_at="2026-09-25T12:00:00Z", run_index=0, citation_count=0,
    )


#: One incoherent `model_copy` per validator, each the shape a verb's own path
#: produces: `advance()` (run), `review()` (review), and the submit and decide
#: verbs. Every one of them used to be written without a check on two of the
#: four paths.
INCOHERENT = {
    "determined-with-no-run": {"runs": ()},
    "review-of-a-run-not-held": {"reviews": (_a_review(run_index=1),)},
    "submission-while-determined": {"submission": _submission()},
    "decision-while-determined": {
        "decision": PayerDecision(
            outcome=PayerDecisionOutcome.APPROVED, decided_on="2026-09-30",
            recorded_at="2026-10-01T09:00:00Z", payer_id="p1",
        )
    },
}


@pytest.mark.parametrize("update", INCOHERENT.values(), ids=INCOHERENT.keys())
def test_saving_a_session_its_own_validators_refuse_writes_nothing(root, update):
    """The adapter re-validates before it opens the file (D140).

    Before `T-138`, `save` wrote these: `get()` then refused the file, one verb
    after the one that caused it.
    """
    store = LocalSessionStore(root)
    store.create(_session())
    store.save(_session(state=SessionState.DETERMINED, runs=(_run(),)))
    before = _on_disk(root, "s1")

    incoherent = store.get("s1").model_copy(update=update)
    with pytest.raises(ValidationError):
        store.save(incoherent)
    assert _on_disk(root, "s1") == before
    store.get("s1")  # what is on disk still reads back


def test_creating_a_session_its_own_validators_refuse_writes_nothing(root):
    incoherent = _session().model_copy(update={"runs": (_run(),)})
    with pytest.raises(ValidationError):
        LocalSessionStore(root).create(incoherent)
    assert not (root / "s1.json").exists()


def test_the_composition_root_carries_no_second_validator():
    """`cli._revalidated` was a check no input could fail once the adapter
    checks, so deleting it would have survived every test (D131's shape). It
    is gone, and this keeps it gone (D140)."""
    tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_revalidated" not in defined


# --------------------------------------------------------------------------
# Article VI — it holds ids and snapshots, not corpora (REQ-70)
# --------------------------------------------------------------------------


def _field_types(model, seen=None):
    """Every pydantic model reachable from `model`'s fields."""
    from pydantic import BaseModel

    seen = seen if seen is not None else set()
    if model in seen:
        return seen
    seen.add(model)
    for field in model.model_fields.values():
        for arg in (field.annotation, *getattr(field.annotation, "__args__", ())):
            if isinstance(arg, type) and issubclass(arg, BaseModel):
                _field_types(arg, seen)
    return seen


def test_no_corpus_type_is_reachable_from_a_session():
    """Article VI on the contract graph, not on the serialized key names.

    A string check for `resourceType` passes on a policy document embedded as
    raw text, which carries no key at all. This walks what `Session` can hold:
    a `Document` anywhere in that graph is a corpus the session would have to
    re-serve, which is the thing REQ-70 forbids. A determination's quoted spans
    travel with it — Article VI's line is drawn at resources, not at text
    (D127).
    """
    reachable = _field_types(Session)
    assert Document not in reachable, (
        "a Session can reach Document; a session holds ids and snapshots, and a "
        "hashed corpus document in one is a second copy of the corpus (REQ-70)"
    )
    names = {m.__name__ for m in reachable}
    assert "Bundle" not in names and "Observation" not in names, (
        f"a Session reaches {sorted(names)}; no FHIR resource belongs in it"
    )


#: Exactly the fields a session carries. Pinned as a literal (D51's shape),
#: because the structural walk below sees **models** and a corpus embedded in a
#: plain `str` is invisible to it. Measured at this close: adding
#: `cached_policy_text: str` to `SessionRun` — a field that can hold the entire
#: policy corpus — passed all nineteen tests until this pin existed.
#: `submission` and `decision` joined at T-105 (D134) — an artifact id, a digest,
#: counts and two dates. Neither is a `Document` or a resource: the transmitted
#: text lives in the outbox, which is REQ-70's line drawn at resources rather
#: than at text.
SESSION_FIELDS = {
    "session_id", "created_at", "intake", "state", "runs", "reviews",
    "submission", "decision",
}
SUBMISSION_FIELDS = {
    "artifact_id", "sha256", "payer_id", "submitted_at", "run_index",
    "citation_count",
}
DECISION_FIELDS = {
    "outcome", "decided_on", "recorded_at", "payer_id", "reference",
}
RUN_FIELDS = {"ran_at", "as_of", "policy_version_id", "determination"}
#: `requesting_provider` and `servicing_provider` joined at T-103 (D131):
#: identity **pass-through text**, never a `Practitioner` resource — Article VI's
#: line is drawn at resources, not at text, which is the same reason
#: `icd10_codes` is a tuple of strings and not a list of `Condition`s.
INTAKE_FIELDS = {
    "patient_id", "procedure_code", "state", "icd10_codes",
    "requesting_provider", "servicing_provider",
}


def test_a_session_carries_exactly_these_fields():
    """A new field on a session is a visible diff, never a quiet one (REQ-70).

    The graph walk catches a `Document`; it cannot catch a `str` holding one.
    So the field set is pinned by name, and a field added to hold corpus text
    fails here — where a reviewer reads the name — rather than nowhere.
    """
    assert set(Session.model_fields) == SESSION_FIELDS
    assert set(SessionRun.model_fields) == RUN_FIELDS
    assert set(Intake.model_fields) == INTAKE_FIELDS
    assert set(SubmissionRecord.model_fields) == SUBMISSION_FIELDS
    assert set(PayerDecision.model_fields) == DECISION_FIELDS


def _strings(value):
    """Every string in a decoded JSON tree."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_a_written_session_carries_no_corpus_text(root):
    """And the same claim on real content, against the real corpora.

    **Decoded, not raw.** The first version of this test compared committed
    text against the file's bytes, and JSON escapes newlines — so a session
    carrying an entire policy document read as *absent* and the test could
    never fail. It now parses the session and walks every decoded string, which
    is what makes it a check rather than a sentence (measured at this close).
    """
    store = LocalSessionStore(root)
    store.create(_session(state=SessionState.DETERMINED, runs=(_run(),)))
    written = list(_strings(json.loads((root / "s1.json").read_text(encoding="utf-8"))))

    corpora = sorted((REPO_ROOT / "data" / "policies" / "source").glob("*.txt"))
    corpora += sorted((REPO_ROOT / "data" / "patients" / "notes").rglob("*.txt"))
    assert corpora, "the corpora moved; re-read this test"

    for path in corpora:
        body = path.read_text(encoding="utf-8")
        probe = body[200:400] if len(body) > 400 else body
        for value in written:
            assert probe not in value, (
                f"a written session carries text from {path.name}; a session "
                "holds ids and snapshots, not a corpus (REQ-70)"
            )


# --------------------------------------------------------------------------
# Where it may be read from
# --------------------------------------------------------------------------


def test_only_the_store_names_the_session_root():
    """Article VI's storage rule, on the fourth corpus (D25, REQ-41, REQ-69).

    `pa_agent/session.py` receives objects; a path literal in it would be the
    second adapter nobody declared, and it is one directory above the only
    place `tests/test_planes.py` tolerates one.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent.name == "stores":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "data/sessions" in node.value:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        f"{offenders} names the session root; it reaches the system through the "
        "port, constructed in cli.py (REQ-41, REQ-69, D127)"
    )


def test_the_state_machine_has_no_write_path():
    """Why REQ-71's *never recorded* is structural rather than remembered.

    `advance()` returns or raises; it cannot persist. A write helper beside it
    would have to be trusted not to fire on the failing path.
    """
    source = (PACKAGE / "session.py").read_text(encoding="utf-8")
    for name in ("open", "write_text", "mkdir", "Path"):
        assert name not in source, (
            f"pa_agent/session.py names {name!r}; the machine is pure and the "
            "adapter is the only writer (D127)"
        )
