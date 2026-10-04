"""The review log (T-104, D132, REQ-75).

A13's third clause: **the log is append-only and the determination's bytes are
unchanged after any number of reviews**. D131 rewrote this row's exit before it
opened, because *never edits* is a claim no behavioural test on this corpus
separates from *edits and puts back the same value* — so the claim is held three
ways here, and the three are not interchangeable.

- **Frozen models** mean `model_copy(update=…)` on a session has no route inside
  a `SessionRun`. Structural, and the weakest of the three: it says a run cannot
  be edited in place, not that `runs` cannot be replaced wholesale.
- **A parse of `session.review`** says the append writes `state` and `reviews`
  and nothing else. A function that read `session.runs` and put the same value
  back is indistinguishable from this one on every input, which is why this one
  is read rather than run (D65's shape).
- **A byte comparison off disk**, over *three* reviews rather than one. A
  one-review test passes a mutant that replaces entry 0; a comparison taken
  after each review passes one that restores the bytes in between.

Every behavioural test here runs the real CLI in a subprocess against a
`tmp_path` root, `tests/test_session_verbs.py`'s style, so the log is read off
disk rather than out of an object still in memory.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys

from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent import cli, form, session as session_machine
from pa_agent.contracts import (
    Intake,
    ReviewAction,
    ReviewEntry,
    Session,
    SessionRun,
    SessionState,
)
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.stores.session import LocalSessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_SOURCE = REPO_ROOT / "pa_agent" / "session.py"

#: E4's chart, which every bariatric row in the eval set already grades.
PATIENT = "07a5f345-3e7c-da0f-da0b-87fa252a5bfd"
COVERED_CODE = "43775"
#: Pinned so a recency verdict does not move with the calendar.
AS_OF = "2025-01-01"

#: E12's chart, the one the committed corpus colours **red** (T-103's
#: `RED_PATIENT`), and the harness clock its determination is recorded at. Used
#: by the one test that closes the loop between the verb and `form.accepted`.
RED_PATIENT = "a8edc52e-9800-0adb-c775-bd81183c355f"
RED_AS_OF = "2026-09-01"
RED_ROW = "hydrochlorothiazide-hyperglycemia"
RED_CODE = "R73.9"


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _session(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _cli("session", "--sessions-root", str(root), *args)


def _created(root: Path, *args: str) -> str:
    proc = _session(root, "create", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["session_id"]


def _determined(root: Path, patient: str = PATIENT, as_of: str = AS_OF) -> str:
    """One session with a snapshot, which is the state a review may be taken in."""
    session_id = _created(
        root, "--patient", patient, "--procedure", COVERED_CODE, "--payer", "medicare", "--state", "WA"
    )
    ran = _session(root, "run", session_id, "--as-of", as_of)
    assert ran.returncode == 0, ran.stderr
    return session_id


def _file(root: Path, session_id: str) -> Path:
    return root / f"{session_id}.json"


def _runs_bytes(root: Path, session_id: str) -> bytes:
    """The stored `runs` sub-document, as the **bytes** on disk.

    Sliced out of the file rather than re-serialized from a parsed object,
    because a comparison of two re-encodings compares the encoder and not the
    document — D127 found that exact failure one port over, where a check
    compared corpus text against JSON-escaped bytes and could never fail.

    The adapter writes the contract's field order with `indent=2`, so `runs` is
    one contiguous region between two known keys. If that order moves this
    raises, which is the right answer: the bytes this test compares would no
    longer be the ones it names.
    """
    text = _file(root, session_id).read_text(encoding="utf-8")
    start = text.index('\n  "runs": ')
    end = text.index('\n  "reviews": ', start)
    sliced = text[start:end].encode("utf-8")
    assert len(sliced) > 200, "the runs region sliced to nothing; re-read this helper"
    return sliced


def _untouched(root: Path, session_id: str) -> tuple[bytes, int]:
    """The session's bytes **and** its `st_mtime_ns`.

    The bytes alone hold *nothing changed*, which is weaker than REQ-71's
    *never recorded* — and the gap is not theoretical. Measured at this close: a
    `store.save(session)` inserted **before** the refusal raises survives a
    byte comparison completely, because the adapter generates nothing (D127) and
    so re-writing the object just read reproduces the file exactly. The
    modification time is what separates *the store was not written* from *the
    store was written with the same content*, and `st_mtime_ns` moves on a
    same-content rewrite.
    """
    path = _file(root, session_id)
    return path.read_bytes(), path.stat().st_mtime_ns


def _reviews(root: Path, session_id: str) -> list[dict]:
    return json.loads(_file(root, session_id).read_text(encoding="utf-8"))["reviews"]


# --------------------------------------------------------------------------
# An order the table forbids
# --------------------------------------------------------------------------


def test_reviewing_a_created_session_is_refused_and_writes_nothing(tmp_path):
    """D129's contract, on the verb this version adds.

    Exit 1, the current state and its legal successors on stderr, nothing on
    stdout — and there is deliberately **no fifth exit code**. The session's
    bytes are compared after the refusal, because a handler that advanced and
    then failed to persist would look identical to one that refused.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--payer", "medicare", "--state", "WA"
    )
    before = _untouched(root, session_id)

    proc = _session(root, "review", session_id, "--reviewer", "Sam", "--note", "x")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "CREATED" in proc.stderr and "DETERMINED" in proc.stderr, (
        "the refusal must name the current state and what it may become; that "
        "is what tells the reviewer to run the session first (D129)"
    )
    assert _untouched(root, session_id) == before, (
        "the store was written on a refused review; REQ-71's claim is that it "
        "is never recorded, not that it is recorded unchanged"
    )


def test_an_unknown_session_is_a_bad_request(tmp_path):
    root = tmp_path / "sessions"
    _determined(root)
    proc = _session(root, "review", "nosuchsession", "--reviewer", "Sam",
                    "--note", "x")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "bad request" in proc.stderr


def test_a_review_of_a_snapshot_the_session_does_not_hold_is_refused(tmp_path):
    """REQ-75's binding clause, over the surface a reviewer types.

    A review of snapshot 5 on a session holding one is a review of nothing, and
    it must not be written: `get()` reads through `model_validate_json`, so a
    session written holding one would be a session the store cannot read back.
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    before = _untouched(root, session_id)

    proc = _session(root, "review", session_id, "--reviewer", "Sam",
                    "--note", "x", "--run", "5")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "holds 1 run" in proc.stderr
    assert _untouched(root, session_id) == before

    # And the store can still read what is there, which is the failure this
    # refusal exists to prevent rather than a restatement of the one above.
    assert LocalSessionStore(root).get(session_id).reviews == ()


# --------------------------------------------------------------------------
# The log appends
# --------------------------------------------------------------------------


def test_three_reviews_append_in_order_and_the_determination_is_untouched(tmp_path):
    """The row's exit, end to end: *any number of* reviews, compared off disk.

    Three, not one. A mutant whose update reads `{"reviews": (entry,)}` replaces
    the log on every call and passes a one-review test; the ordered list of
    three is what fails it. And the `runs` bytes are captured before the
    **first** and compared after the **third**, so an implementation that
    rewrote them and restored them between calls has nowhere to hide.
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    runs_before = _runs_bytes(root, session_id)

    steps = (
        ("--note", "chart reviewed against the note"),
        ("--accept", RED_ROW),
        ("--justify", RED_ROW),
    )
    for index, (flag, value) in enumerate(steps):
        extra: tuple[str, ...] = ()
        if flag != "--note":
            extra = ("--code", RED_CODE)
        if flag == "--justify":
            extra += ("--justification", "Thiazide exposure documented in the chart.")
        proc = _session(root, "review", session_id, "--reviewer", "Sam",
                        flag, value, *extra)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["reviews"] == index + 1

    entries = _reviews(root, session_id)
    assert [e["action"] for e in entries] == [
        "NOTE", "ACCEPT_SUGGESTION", "JUSTIFY_SUGGESTION"
    ], "the log is read by order, so its order is the history (D131)"
    assert entries[0]["note"] == "chart reviewed against the note"
    assert all(e["run_index"] == 0 for e in entries)
    assert all(e["reviewer"] == "Sam" for e in entries)
    assert all(e["at"] for e in entries), "the clock is `_now()`, stamped per entry"

    assert _runs_bytes(root, session_id) == runs_before, (
        "the determination's bytes moved under three reviews; the review log "
        "sits beside the determination and never edits it (A13, REQ-75)"
    )


def test_the_state_moves_once_and_then_stays(tmp_path):
    """The append self-edge, from the outside.

    The first review moves `DETERMINED -> IN_REVIEW`; the second takes
    `IN_REVIEW -> IN_REVIEW`, which is the row `TRANSITIONS` gained. Without it
    a reviewer could write one entry and no more, and US-13 says *her edits
    append*, plural.
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    first = _session(root, "review", session_id, "--reviewer", "Sam", "--note", "a")
    assert json.loads(first.stdout)["state"] == "IN_REVIEW"
    second = _session(root, "review", session_id, "--reviewer", "Kim", "--note", "b")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["state"] == "IN_REVIEW"
    assert len(_reviews(root, session_id)) == 2


def test_session_show_carries_the_log_in_order(tmp_path):
    """`_render_session` publishes the log, and needed no edit to do it.

    It starts from `session.model_dump(mode="json")` and overrides only `runs`,
    so `reviews` has been in this document since `T-103` landed the field. That
    is asserted rather than assumed: *a key appears because a field exists* is
    exactly the fact that stops being true when someone writes an explicit dict.
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    _session(root, "review", session_id, "--reviewer", "Sam", "--note", "one")
    _session(root, "review", session_id, "--reviewer", "Sam", "--note", "two")

    shown = json.loads(_session(root, "show", session_id).stdout)
    assert [entry["note"] for entry in shown["reviews"]] == ["one", "two"]
    assert shown["state"] == "IN_REVIEW"


def test_a_reviewed_session_round_trips_byte_stable_written_twice(tmp_path):
    """The store's rule (D127), over a session that now carries a log.

    Two writes, not one: a one-write comparison catches a regenerated timestamp
    and **not** a serializer stamping a key the contract does not declare —
    pydantic ignores extra keys on read, so the object round-trips equal while
    every write produces different bytes.
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    _session(root, "review", session_id, "--reviewer", "Sam", "--accept", RED_ROW,
             "--code", RED_CODE)

    store = LocalSessionStore(root)
    first = _file(root, session_id).read_bytes()
    store.save(store.get(session_id))
    second = _file(root, session_id).read_bytes()
    store.save(store.get(session_id))
    third = _file(root, session_id).read_bytes()

    assert first == second == third
    assert len(store.get(session_id).reviews) == 1


# --------------------------------------------------------------------------
# What a review may not be
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra,because",
    [
        (("--justify", RED_ROW, "--code", RED_CODE, "--justification", "   "),
         "a whitespace justification is an omission wearing a field's clothes"),
        (("--justify", RED_ROW, "--code", RED_CODE),
         "a JUSTIFY with no justification justifies nothing"),
        (("--accept", RED_ROW),
         "a row action that names no code is a code that traces to nothing"),
        (("--note", "prose", "--code", RED_CODE),
         "a NOTE that touches a code is one of the three row actions"),
        (("--accept", RED_ROW, "--code", RED_CODE, "--justification", "why"),
         "only JUSTIFY carries a justification, so the packet reads one field"),
    ],
    ids=["blank-justification", "no-justification", "no-code", "note-with-code",
         "accept-with-justification"],
)
def test_an_entry_the_contract_refuses_writes_nothing(tmp_path, extra, because):
    """Every refusal is `ReviewEntry`'s, and argparse has no opinion.

    The flags say **which** action was asked for; what each action must carry is
    the contract's validator (T-103, D131). A `required=True` on `--code` would
    be a second rule about one field, and two rules about one field eventually
    disagree — `Intake`'s normalisation argument, one contract over (D128).
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    before = _untouched(root, session_id)

    proc = _session(root, "review", session_id, "--reviewer", "Sam", *extra)

    assert proc.returncode == 1, because
    assert proc.stdout == ""
    assert "bad request" in proc.stderr
    assert _untouched(root, session_id) == before, because


def test_two_actions_at_once_are_refused_by_the_parser(tmp_path):
    """One entry records one action, so the group is mutually exclusive.

    Argparse's usage error is exit **2**, which collides with the documented
    *unbuilt path* code exactly as a missing required flag does on the bare form
    — a collision `tests/test_session_verbs.py` already pins as a known fact
    (D129).
    """
    root = tmp_path / "sessions"
    session_id = _determined(root)
    proc = _session(root, "review", session_id, "--reviewer", "Sam",
                    "--accept", RED_ROW, "--reject", RED_ROW, "--code", RED_CODE)
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_a_review_with_no_reviewer_is_refused(tmp_path):
    """US-13 is *a packet I signed off on*, which an anonymous entry cannot
    support: a record of the decision with the accountability removed."""
    root = tmp_path / "sessions"
    session_id = _determined(root)
    proc = _session(root, "review", session_id, "--note", "x")
    assert proc.returncode != 0
    assert proc.stdout == ""


# --------------------------------------------------------------------------
# The machine, and the two layers the bytes cannot see
# --------------------------------------------------------------------------


def _determination_session(state=SessionState.DETERMINED, runs=1, reviews=()):
    from pa_agent.contracts import Determination, DeterminationOutcome

    run = SessionRun(
        ran_at="2026-09-25T00:00:00Z",
        as_of="2026-09-25",
        policy_version_id="ncd-100.1-jf-v1",
        determination=Determination(
            patient_id="p",
            procedure_code=COVERED_CODE,
            policy_version_id="ncd-100.1-jf-v1",
            payer="medicare",
            outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
        ),
    )
    return Session(
        session_id="s1",
        created_at="2026-09-25T00:00:00Z",
        intake=Intake(patient_id="p", procedure_code=COVERED_CODE, payer="medicare"),
        state=state,
        runs=tuple(run for _ in range(runs)),
        reviews=reviews,
    )


def _entry(action=ReviewAction.NOTE, *, run_index=0, note="x", **kwargs) -> ReviewEntry:
    return ReviewEntry(
        at="2026-09-25T00:00:00Z",
        reviewer="Sam",
        action=action,
        run_index=run_index,
        note=note if action is ReviewAction.NOTE else None,
        **kwargs,
    )


def test_the_machine_returns_a_new_session_and_writes_nothing():
    """`review()` is `advance()`'s shape: it returns or raises, and the caller
    persists — so a refused review is never recorded because there is no write
    on the path, not because the adapter remembered (REQ-71's argument)."""
    before = _determination_session()
    after = session_machine.review(before, _entry())

    assert after is not before
    assert before.reviews == ()
    assert after.state is SessionState.IN_REVIEW
    assert len(after.reviews) == 1
    assert after.runs == before.runs


def test_the_machine_refuses_a_review_of_a_snapshot_that_does_not_exist():
    """Checked **here as well as** on the contract, and neither is redundant.

    `model_copy(update=…)` runs no validator — measured, D132 — so the contract
    validator is unreachable through the only path that appends. Without this
    check the verb would write a session `get()` cannot read back.
    """
    with pytest.raises(session_machine.NoSuchSnapshot) as caught:
        session_machine.review(_determination_session(), _entry(run_index=3))
    assert caught.value.run_index == 3 and caught.value.held == 1


def test_a_session_holding_a_review_of_a_missing_snapshot_cannot_be_built():
    """The contract half, which is what refuses a file someone edited by hand:
    `get()` reads through `model_validate_json`."""
    with pytest.raises(ValidationError, match="names run 2 and the session holds"):
        _determination_session(reviews=(_entry(run_index=2),))


def test_model_copy_runs_no_validator_which_is_why_both_checks_exist():
    """The measurement D132 rests on, asserted rather than remembered.

    If pydantic ever validates on `model_copy`, the check inside `review()`
    becomes the redundant one and this test is what says so.
    """
    copied = _determination_session().model_copy(
        update={"state": SessionState.IN_REVIEW, "reviews": (_entry(run_index=9),)}
    )
    assert copied.reviews[0].run_index == 9, (
        "model_copy validated; re-read D132's argument for checking the bound "
        "inside review() as well as on the contract"
    )


def _review_function() -> ast.FunctionDef:
    tree = ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "review"
    )


def test_the_append_writes_state_and_reviews_and_names_no_run():
    """*Appends, never edits*, held by parsing — because nothing else can hold it.

    `update={"state": …, "reviews": …, "runs": session.runs}` is behaviourally
    identical to the real thing on every input the corpus can produce, so no
    byte comparison and no round trip separates them (D65). The assertion is
    over the `model_copy` call's `update` dict and **not** over the whole body,
    which has a legitimate `len(session.runs)` in it: a check that had to be
    relaxed the first time the function counted its snapshots is a check nobody
    trusts the second time.
    """
    calls = [
        node
        for node in ast.walk(_review_function())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
    ]
    assert len(calls) == 1, "review() no longer makes exactly one copy"

    update = next(kw.value for kw in calls[0].keywords if kw.arg == "update")
    assert isinstance(update, ast.Dict), "the update is no longer a literal dict"
    keys = [key.value for key in update.keys if isinstance(key, ast.Constant)]
    assert keys == ["state", "reviews"], (
        f"review() writes {keys}; appending an entry writes the state and the "
        "log and nothing else, so a determination cannot move through it"
    )
    for node in ast.walk(update):
        name = getattr(node, "attr", None) or getattr(node, "id", None)
        assert name != "runs", (
            "review()'s update names `runs`; putting the same value back is "
            "indistinguishable from not naming it, and this parse is the only "
            "thing that sees the difference (D132)"
        )


def test_the_machine_still_imports_only_contracts():
    """It gained a function, not a dependency: a store import here would put a
    write path one directory above the only place the plane scan tolerates one
    (D127)."""
    imported = set()
    for node in ast.walk(ast.parse(SESSION_SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert {n for n in imported if n.startswith("pa_agent")} == {"pa_agent.contracts"}


# --------------------------------------------------------------------------
# The log the verb writes is the log the packet reads
# --------------------------------------------------------------------------


def test_the_entries_the_verb_wrote_are_the_ones_the_packet_reader_reads(tmp_path):
    """The join between this row and `T-103`'s, which nothing else asserts.

    `T-103` checked `form.accepted` against hand-written entries and this row
    writes entries through a verb; without this test the two facts never meet,
    and a verb writing a log in a shape the reader skips would leave both green.
    Run in-process against the stored session, because what is under test is the
    entries rather than a rendered packet.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", RED_PATIENT, "--procedure", COVERED_CODE, "--payer", "medicare", "--state", "WA"
    )
    assert _session(root, "run", session_id, "--as-of", RED_AS_OF).returncode == 0

    for extra in (
        ("--accept", RED_ROW, "--code", RED_CODE),
        ("--justify", RED_ROW, "--code", RED_CODE,
         "--justification", "Thiazide exposure documented; monitoring ordered."),
    ):
        proc = _session(root, "review", session_id, "--reviewer", "Sam", *extra)
        assert proc.returncode == 0, proc.stderr

    stored = LocalSessionStore(root).get(session_id)
    determination = stored.runs[0].determination
    review = cli._review(
        determination,
        stored.intake.patient_id,
        LocalPolicyStore(),
        LocalPatientStore(),
        LocalKnowledgeStore(),
    ).review

    suggestions = form.accepted(review, stored.reviews, 0)
    assert [s.icd10_code for s in suggestions] == [RED_CODE]
    assert suggestions[0].justification.startswith("Thiazide exposure")
    assert suggestions[0].accepted_by == "Sam"


# --------------------------------------------------------------------------
# The guard for the guard
# --------------------------------------------------------------------------


def test_no_test_in_this_file_writes_to_the_default_root():
    """A test that forgets `--sessions-root` writes into the working tree and
    the suite still passes, silently. Parsed rather than grepped: a substring
    scan for the constructor matches this test's own text (D127's close)."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_session":
            assert node.args, "_session() called with no root"
        if isinstance(func, ast.Name) and func.id == "LocalSessionStore":
            assert node.args or node.keywords, (
                "a test constructed the adapter with no root, which writes "
                "into data/sessions/ in the working tree"
            )
