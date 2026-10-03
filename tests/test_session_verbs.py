"""The session verbs, and the promise that the bare form did not move (T-102, D129).

Two claims this file exists to keep apart.

**The verbs work** — `create | list | show | run` round-trip a determination
through the adapter, a bad request writes nothing, and an illegal order is
refused. Every one of those runs the real CLI in a subprocess against a
`tmp_path` root, so `show` reads bytes off disk rather than an object still in
memory: an adapter that returned its own cache would pass an in-process test
and fail here.

**The bare form did not move.** `python -m pa_agent.cli --patient X --procedure
Y` is quoted in `README.md`, `CLAUDE.md`, `docs/spec.md` and six task records,
and `T-102` promised it byte-identical. Two checks hold that, because one is not
enough: the parser is read with `ast` to confirm both flags still carry
`required=True`, and `session run`'s determination is compared **byte for byte**
against the whole stdout of the bare form for the same request. The second is
the one with teeth — it fails if either surface moves, and it cannot be
satisfied by a second renderer that happens to agree today.

The git-level evidence is in D129 and was taken at the close: the four documents
the bare form prints for a covered request, a non-covered one, a code no policy
governs and a `--suggest` run were captured at `1bbd244` and compared after. A
commit hash is not pinned here, because a test that goes red on a rebase is a
test nobody keeps.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys

from pathlib import Path

import pytest

from pa_agent import cli
from pa_agent.contracts import (
    Intake,
    PayerDecisionOutcome,
    Session,
    SessionRun,
    SessionState,
)
from pa_agent.stores.outbox import DEFAULT_OUTBOX_ROOT
from pa_agent.stores.payer import LocalPayerStore
from pa_agent.stores.session import DEFAULT_SESSION_ROOT, LocalSessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = REPO_ROOT / "pa_agent" / "cli.py"

#: E4's chart, which every bariatric row in the eval set already grades.
PATIENT = "07a5f345-3e7c-da0f-da0b-87fa252a5bfd"
COVERED_CODE = "43775"
#: A code no policy in the store governs (NP1's, outside §6).
FOREIGN_CODE = "99213"
#: Pinned so a recency verdict does not move with the calendar.
AS_OF = "2025-01-01"


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _session(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _cli("session", "--sessions-root", str(root), *args)


def _session_out(root: Path, outbox: Path, *args: str) -> subprocess.CompletedProcess:
    """A session command that may write a packet. **Both** roots, always.

    A separate helper rather than a defaulted argument on `_session`, so the AST
    guard at the bottom of this file can require an outbox root on every call that
    could produce one — a test that forgot it would write into `data/outbox/` in
    the working tree and the suite would still pass (D127's failure mode, on the
    root D134 added).
    """
    return _cli(
        "session",
        "--sessions-root", str(root),
        "--outbox-root", str(outbox),
        *args,
    )


def _created(root: Path, *args: str) -> str:
    proc = _session(root, "create", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["session_id"]


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under `root`, with its bytes **and** its `st_mtime_ns`.

    The timestamp is not decoration. T-104 measured that a `save()` inserted
    before a refusal reproduces the session file exactly, because the adapter
    generates nothing (D127) — so a byte comparison holds *nothing changed* where
    the requirement says *never written*. `st_mtime_ns` moves on a same-content
    rewrite and the bytes catch a changed one; neither alone is the check (D132,
    D134).
    """
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------
# The bare form did not move
# --------------------------------------------------------------------------


def _bare_parser_flags() -> dict[str, dict]:
    """Every `add_argument` call in `main`'s own parser, read with `ast`.

    Parsed rather than invoked because the claim is about the *source*: D129
    chose pre-dispatch precisely so this block would not be edited, and a test
    that only ran the parser would pass on a rewrite that happened to behave
    the same until the first flag moved.
    """
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    flags: dict[str, dict] = {}
    for node in ast.walk(main):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_argument":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        flags[node.args[0].value] = {
            kw.arg: getattr(kw.value, "value", kw.value) for kw in node.keywords
        }
    return flags


def test_the_bare_parser_still_requires_a_patient_and_a_procedure():
    """D129's whole argument, as a check.

    If these ever stop being `required=True` at the top level, the owner's
    decision — *the bare invocation keeps working unchanged* — has been
    reversed, and every doc quoting it is wrong.
    """
    flags = _bare_parser_flags()
    assert flags["--patient"].get("required") is True
    assert flags["--procedure"].get("required") is True


def test_the_dispatch_is_on_the_literal_verb_and_not_on_any_positional():
    """A typo must be an unknown argument, not a session command (D129).

    `parse_known_args` and re-dispatch would accept `sesion show X` as a bare
    request with three positionals; this pins the comparison to the word.
    """
    source = CLI_SOURCE.read_text(encoding="utf-8")
    assert 'args_in[0] == "session"' in source, (
        "the dispatch no longer compares the first argument to the literal "
        "'session'; dispatching on any positional makes a typo a verb (D129)"
    )


def test_the_unbuilt_path_handler_survived_the_refactor():
    """`return 2` and its handler moved into a helper; both must still exist.

    `tests/test_determination.py` asserts the same two literals. This is the
    second reader, here because T-102 is the row that moved them.
    """
    source = CLI_SOURCE.read_text(encoding="utf-8")
    assert "except NotImplementedError as exc:" in source
    assert "return 2" in source


@pytest.mark.parametrize(
    "args",
    [
        ("--patient", PATIENT, "--procedure", COVERED_CODE, "--as-of", AS_OF),
        ("--patient", "X", "--procedure", "43842", "--state", "WA"),
        ("--patient", "X", "--procedure", FOREIGN_CODE, "--state", "WA"),
    ],
    ids=["covered", "not-covered", "no-policy"],
)
def test_the_bare_form_still_answers_and_exits_zero(args):
    proc = _cli(*args)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)


#: Every top-level key the bare form prints for a determination, as a literal.
#:
#: **Pinned against a list, not against the renderer.** The byte comparison
#: below asserts that `session run` and the bare form agree; it does **not**
#: assert that either is unchanged, because a key added to `_render` appears on
#: both sides and the comparison stays green. Measured at this close: adding
#: `mutant_key` to `_render` left 85 tests passing while the document the CLI
#: publishes had moved. This list is what a new key has to get past — D51's
#: move, on an output shape.
BARE_DETERMINATION_KEYS = frozenset({
    "patient_id",
    "procedure_code",
    "policy_version_id",
    "outcome",
    "coverage_claim",
    "exclusion_evidence",
    "criterion_results",
    "metrics",
    "model_calls",
    "gap_list",
    "discrepancies",
    "total_input_tokens",
    "total_output_tokens",
    "total_wall_time_ms",
})

#: The short-circuit shapes, same rule.
BARE_NO_POLICY_KEYS = frozenset({"result", "procedure_code", "note"})
BARE_NO_JURISDICTION_KEYS = frozenset(
    {"result", "procedure_code", "state", "known_states", "note"}
)


def test_the_bare_forms_document_still_carries_exactly_these_keys():
    """The bare form's published shape, pinned so a new key is a visible diff.

    `--suggest` adds `icd_suggestions` and nothing else adds anything: REQ-65
    says a suggestion rides beside the verdicts and changes none of them, and
    every other key here is what `T-25` and `T-99` between them settled.
    """
    determination = json.loads(
        _cli("--patient", PATIENT, "--procedure", COVERED_CODE,
             "--state", "WA", "--as-of", AS_OF).stdout
    )
    assert set(determination) == BARE_DETERMINATION_KEYS

    suggested = json.loads(
        _cli("--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA",
             "--as-of", AS_OF, "--suggest").stdout
    )
    assert set(suggested) == BARE_DETERMINATION_KEYS | {"icd_suggestions"}

    no_policy = json.loads(
        _cli("--patient", "X", "--procedure", FOREIGN_CODE, "--state", "WA").stdout
    )
    assert set(no_policy) == BARE_NO_POLICY_KEYS

    no_tree = json.loads(
        _cli("--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "TX").stdout
    )
    assert set(no_tree) == BARE_NO_JURISDICTION_KEYS


def test_a_missing_required_flag_still_exits_two():
    """Argparse's own usage error, unchanged by the verbs.

    D129 records that this collides with the documented *unbuilt path* code and
    that the collision predates this row. Pinned so the collision is a known
    fact rather than a surprise.
    """
    assert _cli("--procedure", COVERED_CODE).returncode == 2


def test_an_unknown_patient_is_still_a_bad_request():
    proc = _cli("--patient", "NOSUCH", "--procedure", COVERED_CODE, "--state", "WA")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "bad request" in proc.stderr


# --------------------------------------------------------------------------
# The verbs
# --------------------------------------------------------------------------


def test_list_on_a_fresh_root_is_empty_and_not_an_error(tmp_path):
    """D127's inversion of D31, measured through the CLI.

    An empty session directory is a fresh clone, not a broken checkout, so
    `session list` answers zero rather than raising.
    """
    proc = _session(tmp_path / "sessions", "list")
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["count"] == 0
    assert printed["sessions"] == []


def test_create_list_show_run_round_trip_a_determination(tmp_path):
    """The row's exit condition, end to end and through the adapter.

    Every step is a separate process, so nothing is carried in memory between
    them: `show` and `list` read what `create` and `run` wrote.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )

    listed = json.loads(_session(root, "list").stdout)
    assert listed["count"] == 1
    assert listed["sessions"][0]["state"] == "CREATED"
    assert listed["sessions"][0]["runs"] == 0

    shown = json.loads(_session(root, "show", session_id).stdout)
    assert shown["state"] == "CREATED"
    assert shown["runs"] == []
    assert shown["intake"]["procedure_code"] == COVERED_CODE

    ran = _session(root, "run", session_id, "--as-of", AS_OF)
    assert ran.returncode == 0, ran.stderr
    printed = json.loads(ran.stdout)
    assert printed["recorded"] is True
    assert printed["state"] == "DETERMINED"
    assert printed["determination"]["outcome"]

    after = json.loads(_session(root, "show", session_id).stdout)
    assert after["state"] == "DETERMINED"
    assert len(after["runs"]) == 1
    # `_render`'s computed properties, which a bare `model_dump` drops.
    assert "model_calls" in after["runs"][0]["determination"]
    assert "gap_list" in after["runs"][0]["determination"]


def test_a_second_run_appends_a_snapshot_and_never_edits_the_first(tmp_path):
    """US-12's sentence, and the reason `TRANSITIONS` carries a self-edge.

    The first snapshot's bytes are captured before the second run and compared
    after — an implementation that overwrote would keep `runs` at one, but one
    that *replaced* the first while appending a second would not, and only the
    byte comparison sees that.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    _session(root, "run", session_id, "--as-of", AS_OF)
    first = json.loads(_session(root, "show", session_id).stdout)["runs"][0]

    _session(root, "run", session_id, "--as-of", AS_OF)
    after = json.loads(_session(root, "show", session_id).stdout)

    assert len(after["runs"]) == 2
    assert after["state"] == "DETERMINED"
    assert after["runs"][0] == first, (
        "the first snapshot changed when a second run appended; US-12 says a "
        "second run is a new snapshot, never an edit"
    )


def test_the_determination_is_byte_identical_to_the_bare_forms(tmp_path):
    """The strong form of *the bare invocation did not move* (D129).

    `session run` renders through `_render`, the same function the bare form
    uses and the one `T-99` kept pure for this caller (D126). If either surface
    grows a key, or `session run` acquires a renderer of its own, these bytes
    diverge. v2.2's `T-116` makes this comparison over every UI action; this is
    its first instance.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    ran = _session(root, "run", session_id, "--as-of", AS_OF)
    through_the_verb = json.loads(ran.stdout)["determination"]

    bare = _cli(
        "--patient", PATIENT, "--procedure", COVERED_CODE,
        "--state", "WA", "--as-of", AS_OF,
    )
    assert bare.returncode == 0, bare.stderr

    rendered = json.dumps(through_the_verb, indent=2, ensure_ascii=False) + "\n"
    assert rendered == bare.stdout


def test_suggest_reaches_the_verb_and_still_changes_no_verdict(tmp_path):
    """`--suggest` on `session run` is the same block the bare form emits."""
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    ran = _session(root, "run", session_id, "--as-of", AS_OF, "--suggest")
    assert ran.returncode == 0, ran.stderr
    determination = json.loads(ran.stdout)["determination"]
    assert "icd_suggestions" in determination

    bare = _cli(
        "--patient", PATIENT, "--procedure", COVERED_CODE,
        "--state", "WA", "--as-of", AS_OF, "--suggest",
    )
    rendered = json.dumps(determination, indent=2, ensure_ascii=False) + "\n"
    assert rendered == bare.stdout


# --------------------------------------------------------------------------
# A request no tree governs (D129's finding)
# --------------------------------------------------------------------------


def test_a_code_no_policy_governs_answers_and_records_nothing(tmp_path):
    """D129's third choice, and the reason it is not exit 1.

    `SessionRun.determination` is a `Determination`; `NO_POLICY_FOUND` is not
    one. The verb prints the answer, exits 0 because a deterministic non-answer
    is an answer (D32), and leaves the session `CREATED` because nothing was
    determined. The directory is what is checked, not the printed claim.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", "X", "--procedure", FOREIGN_CODE, "--state", "WA"
    )
    ran = _session(root, "run", session_id)
    assert ran.returncode == 0, ran.stderr

    printed = json.loads(ran.stdout)
    assert printed["recorded"] is False
    assert printed["state"] == "CREATED"
    assert printed["determination"]["result"] == "NO_POLICY_FOUND"
    assert "unchanged" in ran.stderr

    stored = LocalSessionStore(root).get(session_id)
    assert stored.state is SessionState.CREATED
    assert stored.runs == ()


def test_that_answer_is_the_bare_forms_answer(tmp_path):
    """The two surfaces agree about a request neither can determine."""
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", "X", "--procedure", FOREIGN_CODE, "--state", "WA"
    )
    through_the_verb = json.loads(_session(root, "run", session_id).stdout)
    bare = _cli("--patient", "X", "--procedure", FOREIGN_CODE, "--state", "WA")
    rendered = (
        json.dumps(through_the_verb["determination"], indent=2, ensure_ascii=False)
        + "\n"
    )
    assert rendered == bare.stdout


# --------------------------------------------------------------------------
# Bad requests write nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("create", "--patient", "X"),
        ("create", "--procedure", COVERED_CODE),
        ("create",),
    ],
    ids=["no-procedure", "no-patient", "neither"],
)
def test_a_malformed_intake_is_a_bad_request_and_writes_no_session(tmp_path, args):
    """REQ-72's other half, which `T-101` minted at unit level and named here.

    The **directory** is what proves it, not a return value: a handler that
    wrote the session and then reported an error would satisfy any check of the
    exit code alone.
    """
    root = tmp_path / "sessions"
    proc = _session(root, *args)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "bad request" in proc.stderr
    assert not root.exists() or list(root.glob("*.json")) == [], (
        "a refused intake left a file behind; a bad request is never a session"
    )


def test_a_malformed_intake_document_is_refused_the_same_way(tmp_path):
    """The JSON route reaches the same fault and the same exit code."""
    root = tmp_path / "sessions"
    document = tmp_path / "intake.json"
    document.write_text('{"patient_id": "X"}', encoding="utf-8")
    proc = _session(root, "create", "--intake", str(document))
    assert proc.returncode == 1
    assert "bad request" in proc.stderr
    assert not root.exists() or list(root.glob("*.json")) == []


def test_an_intake_document_and_the_flags_create_the_same_session(tmp_path):
    """REQ-72 over the real surface: two routes, one object.

    The session ids and timestamps differ — they are generated per create — so
    the comparison is over the intake, which is the thing the two routes
    produce.
    """
    root = tmp_path / "sessions"
    document = tmp_path / "intake.json"
    document.write_text(
        json.dumps(
            {
                "patient_id": PATIENT,
                "procedure_code": COVERED_CODE,
                "state": "WA",
                "icd10_codes": [" e66.01 ", "E66.01", "z68.41"],
            }
        ),
        encoding="utf-8",
    )
    from_document = _created(root, "--intake", str(document))
    from_flags = _created(
        root,
        "--patient", PATIENT,
        "--procedure", COVERED_CODE,
        "--state", "WA",
        "--icd10", " e66.01 ", "E66.01", "z68.41",
    )
    store = LocalSessionStore(root)
    assert store.get(from_document).intake == store.get(from_flags).intake
    assert store.get(from_document).intake.icd10_codes == ("E66.01", "Z68.41")


def test_an_unknown_session_is_a_bad_request(tmp_path):
    root = tmp_path / "sessions"
    _created(root, "--patient", "X", "--procedure", COVERED_CODE, "--state", "WA")
    for verb in ("show", "run"):
        proc = _session(root, verb, "nosuchsession")
        assert proc.returncode == 1, verb
        assert proc.stdout == ""
        assert "bad request" in proc.stderr


def test_an_illegal_transition_is_a_bad_request_and_records_nothing(tmp_path):
    """`IN_REVIEW -> DETERMINED` is not in the table, so running one is refused
    (REQ-71).

    `IN_REVIEW` stopped being terminal at `T-104`, which gave it the append
    self-edge — but it gained no edge back to `DETERMINED`, so this refusal is
    what it was. The state is still written directly rather than reached through
    `session review`, because what is under test is this verb's exit-code
    mapping and not the other verb's.

    The bytes **and** `st_mtime_ns` are compared after the refusal (T-135,
    D139). Bytes alone hold *nothing changed*, and REQ-71 says *never
    recorded*: the adapter generates nothing, so a `store.save(session)` ahead
    of the refusal reproduces the file exactly. That mutation passed this test
    until the modification time joined it, which is
    `tests/test_review_log.py::_untouched`'s shape (D132).
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    _session(root, "run", session_id, "--as-of", AS_OF)

    store = LocalSessionStore(root)
    determined = store.get(session_id)
    store.save(determined.model_copy(update={"state": SessionState.IN_REVIEW}))
    path = root / f"{session_id}.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    proc = _session(root, "run", session_id, "--as-of", AS_OF)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "IN_REVIEW" in proc.stderr
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before, (
        "a refused transition wrote the session; REQ-71 says it is never "
        "recorded"
    )


# --------------------------------------------------------------------------
# Submission and tracking (T-105, D134, REQ-76, REQ-77)
# --------------------------------------------------------------------------


def _reviewed(root: Path) -> str:
    """A session in `IN_REVIEW`, reached through the verbs and not written by hand.

    Four processes, because that is the route a reviewer takes: create, run,
    review, and then this returns the id. Building the state directly would test
    the refusals against a shape the verbs cannot produce.
    """
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    ran = _session(root, "run", session_id, "--as-of", AS_OF)
    assert ran.returncode == 0, ran.stderr
    reviewed = _session(
        root, "review", session_id, "--reviewer", "R. Chen", "--note", "Signed off."
    )
    assert reviewed.returncode == 0, reviewed.stderr
    return session_id


def _submitted(root: Path, outbox: Path) -> str:
    session_id = _reviewed(root)
    proc = _session_out(root, outbox, "submit", session_id)
    assert proc.returncode == 0, proc.stderr
    return session_id


@pytest.mark.parametrize(
    "reach,expected_legal",
    [
        ("created", ["DETERMINED"]),
        ("determined", ["DETERMINED", "IN_REVIEW"]),
    ],
)
def test_submitting_before_review_is_refused_and_writes_nothing(
    tmp_path, reach, expected_legal
):
    """REQ-76, and the clause the **directory** holds rather than the exit code.

    US-13's fourth bullet is this refusal: there is no `DETERMINED ->
    AWAITING_DECISION` edge, so the system never decides to transmit. The stderr
    line names the current state **and its legal successors**, and there is no
    fifth exit code (D129).

    What proves *writes nothing* is that the outbox does not exist. A handler that
    rendered the packet, wrote it, and then checked the order would return the same
    1 with a file on disk — which is T-135's row on the session file, one plane
    over. The session's bytes **and** `st_mtime_ns` are compared for the same
    reason: the adapter generates nothing, so a `save()` before the refusal
    reproduces the file exactly (measured in T-104).
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"

    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    if reach == "determined":
        assert _session(root, "run", session_id, "--as-of", AS_OF).returncode == 0

    before = _snapshot(root)
    proc = _session_out(root, outbox, "submit", session_id)

    assert proc.returncode == 1
    assert proc.stdout == ""
    for name in expected_legal:
        assert name in proc.stderr
    assert not outbox.exists(), (
        "a refused submission created the outbox; nothing is written until the "
        "transition is legal (REQ-76)"
    )
    assert _snapshot(root) == before, (
        "a refused submission rewrote the session; the bytes and st_mtime_ns are "
        "compared together because a same-content rewrite moves only the second"
    )


def test_submitting_a_reviewed_session_writes_exactly_one_file(tmp_path):
    """REQ-77's first half, end to end.

    Exactly one artifact, whose sha256 is the hash the session records — so the
    claim and the bytes are one comparison a gate can make — and nothing else on
    disk moves except the session that now records it.
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _reviewed(root)

    before = _snapshot(root)
    proc = _session_out(root, outbox, "submit", session_id)
    assert proc.returncode == 0, proc.stderr

    printed = json.loads(proc.stdout)
    assert printed["state"] == "AWAITING_DECISION"
    assert printed["submitted"] is True

    artifacts = sorted(outbox.rglob("*.eml"))
    assert len(artifacts) == 1, f"submission wrote {artifacts}"
    body = artifacts[0].read_bytes()

    stored = LocalSessionStore(root).get(session_id)
    assert stored.state is SessionState.AWAITING_DECISION
    assert stored.submission is not None
    assert stored.submission.sha256 == hashlib.sha256(body).hexdigest()
    assert stored.submission.run_index == 0
    assert stored.submission.citation_count > 0
    assert artifacts[0].name == f"{stored.submission.artifact_id}.eml"
    assert artifacts[0].parent.name == stored.submission.payer_id

    after = _snapshot(root)
    assert set(after) == set(before), "submission added or removed a session file"
    changed = {name for name in after if after[name] != before[name]}
    assert changed == {f"{session_id}.json"}, (
        f"submission changed {sorted(changed)} under the session root; the outbox "
        "artifact and the session that records it are the only side effects"
    )


def test_the_transmitted_packet_is_addressed_to_the_directorys_payer(tmp_path):
    """The recipient's **source** moved to `payers.json` and its shape did not.

    `T-103` held a placeholder constant here because no committed artifact named a
    recipient (D131). The header is now rendered from a record that declares itself
    simulated and sits under `.invalid`, so an address written from memory cannot
    reach a packet (D134).
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _submitted(root, outbox)

    stored = LocalSessionStore(root).get(session_id)
    assert stored.submission.payer_id == cli.DEFAULT_PAYER_ID
    payer = LocalPayerStore().get_payer(cli.DEFAULT_PAYER_ID)

    body = (outbox / payer.payer_id / f"{stored.submission.artifact_id}.eml").read_text(
        encoding="utf-8"
    )
    assert body.startswith(f"To: {payer.recipient}\n")
    assert ".invalid>" in body.splitlines()[0]
    # `Date:` is emitted only when the packet carries a submission timestamp
    # (D131), and submission is the first thing that does.
    assert f"Date: {stored.submission.submitted_at}" in body


def test_an_unknown_payer_id_is_a_bad_request_and_writes_nothing(tmp_path):
    """A default is not a fallback (D134). Quietly sending to a different
    recipient than the one named is the one thing a directory must not do."""
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _reviewed(root)

    before = _snapshot(root)
    proc = _session_out(root, outbox, "submit", session_id, "--payer-id", "nope")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "no payer 'nope'" in proc.stderr
    assert not outbox.exists()
    assert _snapshot(root) == before


def test_a_second_submission_of_the_same_snapshot_is_refused(tmp_path):
    """Two guards, in order, and the first is the lifecycle's.

    `AWAITING_DECISION` has no self-edge, so the second `submit` is refused before
    the outbox is reached — and the artifact already there is untouched. The
    outbox's own collision guard is the second line (unit-level in
    `tests/test_outbox_store.py`), and it becomes the first on the day a
    resubmission edge exists.
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _submitted(root, outbox)

    before_sessions = _snapshot(root)
    before_outbox = _snapshot(outbox)

    proc = _session_out(root, outbox, "submit", session_id)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "AWAITING_DECISION" in proc.stderr
    assert _snapshot(outbox) == before_outbox, "a refused resend rewrote the artifact"
    assert _snapshot(root) == before_sessions


def test_deciding_closes_the_session_with_the_outcome_and_both_dates(tmp_path):
    """REQ-77's second half and US-13's fifth bullet, which A13 had in no clause
    until D131 rewrote it.

    **Two dates**: the payer's own and the clock this system was told at.
    Collapsing them makes a decision recorded a week late read as one taken a week
    late (D78's category, D134). The answering payer is read off the submission and
    never from a flag.
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _submitted(root, outbox)

    proc = _session_out(
        root, outbox, "decide", session_id,
        "--outcome", "approved", "--decided-on", "2026-09-20",
        "--reference", "PA-4471",
    )
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["state"] == "DECIDED"
    assert printed["terminal"] is True

    stored = LocalSessionStore(root).get(session_id)
    assert stored.state is SessionState.DECIDED
    assert stored.decision.outcome.value == "APPROVED"
    assert stored.decision.decided_on.isoformat() == "2026-09-20"
    assert stored.decision.reference == "PA-4471"
    assert stored.decision.payer_id == stored.submission.payer_id
    # The system's clock, and not the payer's date wearing its name.
    assert stored.decision.recorded_at != "2026-09-20"
    assert not stored.decision.recorded_at.startswith("2026-09-20")


def test_the_artifact_stays_in_the_outbox_after_the_decision(tmp_path):
    """§11's fourth statement had this backwards, and D131 corrected it.

    *Every session in the outbox is `AWAITING_DECISION`* is false the moment
    `decide` runs: the packet stays and the session becomes `DECIDED`. The
    directional form is what is true and what is checked here — a session acquires
    an artifact when it enters `AWAITING_DECISION`, **and a decided session keeps
    it** (REQ-77).
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _submitted(root, outbox)

    before = _snapshot(outbox)
    assert len(before) == 1

    proc = _session_out(
        root, outbox, "decide", session_id,
        "--outcome", "information-requested", "--decided-on", "2026-09-22",
    )
    assert proc.returncode == 0, proc.stderr

    assert _snapshot(outbox) == before, (
        "recording the decision touched the outbox; the artifact that left is not "
        "the session's to edit"
    )
    stored = LocalSessionStore(root).get(session_id)
    assert stored.state is SessionState.DECIDED
    assert stored.submission is not None, (
        "a decided session dropped its submission record; the directional half of "
        "REQ-77 is that it keeps it"
    )
    assert stored.decision.outcome.value == "INFORMATION_REQUESTED"


@pytest.mark.parametrize("reach", ["created", "determined", "reviewed"])
def test_deciding_a_session_nobody_submitted_is_refused(tmp_path, reach):
    """`DECIDED` is legal only from `AWAITING_DECISION`, so an answer for a
    session nobody sent is exit 1 naming the state it is in and what it may
    become. `IN_REVIEW` is the case the board's exit names (D131, REQ-76)."""
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"

    if reach == "reviewed":
        session_id = _reviewed(root)
    else:
        session_id = _created(
            root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
        )
        if reach == "determined":
            assert _session(root, "run", session_id, "--as-of", AS_OF).returncode == 0

    before = _snapshot(root)
    proc = _session_out(
        root, outbox, "decide", session_id,
        "--outcome", "denied", "--decided-on", "2026-09-20",
    )
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "DECIDED" in proc.stderr
    assert not outbox.exists()
    assert _snapshot(root) == before


def test_decide_requires_the_payers_own_date(tmp_path):
    """Required, because a payer decision with no date of its own is exactly what
    the two-date pair exists to keep apart (D134)."""
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _submitted(root, outbox)
    proc = _session_out(root, outbox, "decide", session_id, "--outcome", "approved")
    assert proc.returncode != 0
    assert proc.stdout == ""


def test_the_cli_outcome_words_are_the_closed_enum():
    """`DECISION_OUTCOMES` against `PayerDecisionOutcome`, in both directions.

    `PREDICATES` against `PredicateKind`, one surface over (T-91, D110): an
    argparse `choices` list spelled after an enum is a second vocabulary, and a
    value in one and not the other is a word that parses and cannot be recorded, or
    an outcome nothing can ask for.
    """
    assert set(cli.DECISION_OUTCOMES.values()) == set(PayerDecisionOutcome)
    for word, outcome in cli.DECISION_OUTCOMES.items():
        assert word == outcome.value.lower().replace("_", "-")


def test_submit_and_packet_resolve_the_recipient_the_same_way(tmp_path):
    """One assembly, so the draft a reviewer reads and the document that leaves
    cannot be addressed differently (D134).

    The comparison is over the headers rather than the whole body, because the
    submitted packet carries a `Date:` the draft does not — that difference is
    D131's, and it is the one thing that *should* differ.
    """
    root = tmp_path / "sessions"
    outbox = tmp_path / "outbox"
    session_id = _reviewed(root)

    draft = _session(root, "packet", session_id)
    assert draft.returncode == 0, draft.stderr

    assert _session_out(root, outbox, "submit", session_id).returncode == 0
    stored = LocalSessionStore(root).get(session_id)
    sent = (
        outbox / stored.submission.payer_id / f"{stored.submission.artifact_id}.eml"
    ).read_text(encoding="utf-8")

    assert draft.stdout.splitlines()[0] == sent.splitlines()[0]
    assert "Date:" not in draft.stdout.split("\n\n", 1)[0]
    assert "Date:" in sent.split("\n\n", 1)[0]


# --------------------------------------------------------------------------
# The surface is closed, and the default root is the repository's
# --------------------------------------------------------------------------


def test_every_declared_verb_has_a_handler_and_a_subparser():
    """`SESSION_VERBS`, `VERB_HANDLERS` and the parser are one vocabulary.

    `PREDICATES` against `PredicateKind`, one surface over (T-91, D110): a verb
    named in one place and missing from another is a command that parses and
    does nothing, or a handler nothing can reach.
    """
    assert set(cli.SESSION_VERBS) == set(cli.VERB_HANDLERS)

    source = CLI_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    session_main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_session_main"
    )
    declared = {
        node.args[0].value
        for node in ast.walk(session_main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    assert declared == set(cli.SESSION_VERBS)


def test_an_undeclared_verb_is_refused_and_every_declared_one_answers(tmp_path):
    """The inverse of the test this replaces, and the reason for the swap (D134).

    Until this row the check read *v1.5's remaining verbs are not silently
    accepted no-ops* over a list of `("submit", "decide")`. `T-105` ships both, so
    that list is empty — and a loop over an empty list is a green test asserting
    nothing, which is the shape T-95 found in A10 and D123 in A11.

    What is checked instead is a claim that does not expire: a verb **no** version
    declares is refused, and **every** verb in `SESSION_VERBS` answers. The second
    half is what catches a handler nothing can reach; `--help` is used for it
    because the verbs take different required arguments and what is under test is
    that the subparser exists.
    """
    for absent in ("cancel", "withdraw", "sumbit"):
        proc = _session(tmp_path / "sessions", absent)
        assert proc.returncode != 0, absent
        assert proc.stdout == "", absent

    for verb in cli.SESSION_VERBS:
        proc = _session(tmp_path / "sessions", verb, "--help")
        assert proc.returncode == 0, f"{verb}: {proc.stderr}"
        assert verb in proc.stdout


def test_the_default_roots_are_the_repositorys_and_are_gitignored():
    """The adapters' defaults, and the reason no test may use either.

    A test that forgets `--sessions-root` or `--outbox-root` writes into the
    working tree, and the suite still passes — silently, which is the whole
    failure mode. Both roots are asserted here and every test above passes
    `tmp_path`.
    """
    assert DEFAULT_SESSION_ROOT == REPO_ROOT / "data" / "sessions"
    assert DEFAULT_OUTBOX_ROOT == REPO_ROOT / "data" / "outbox"
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/sessions/" in ignore
    assert "data/outbox/" in ignore


def test_no_test_in_this_file_writes_to_a_default_root():
    """The guard for the guard, on **both** write planes.

    Every helper here takes a root. If one stopped, this file would quietly
    start writing into `data/sessions/` — or, since `T-105`, `data/outbox/` — and
    nothing else would say so. `_session_out` needs two, and the count is
    asserted rather than its presence, because one root passed twice would satisfy
    a presence check and put a packet under the session root.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_session":
            assert node.args, "_session() called with no root"
        if isinstance(func, ast.Name) and func.id == "_session_out":
            assert len(node.args) >= 2, (
                f"_session_out() at line {node.lineno} was not given both a "
                "session root and an outbox root"
            )
        # Parsed rather than grepped: a substring scan for the constructor
        # matches *this test's own text*, which is a check that reports itself
        # and can never report anything else. Measured — the text form failed
        # on its first run, on itself.
        if isinstance(func, ast.Name) and func.id in (
            "LocalSessionStore", "LocalOutboxStore",
        ):
            assert node.args or node.keywords, (
                f"a test constructed {func.id} with no root, which writes into "
                "the working tree"
            )


def test_the_session_plane_is_constructed_only_in_the_cli():
    """REQ-41 over the fourth port, checked where the verbs land.

    `tests/test_planes.py` holds this structurally for the package; this is the
    behavioural half T-102 owns — the verbs reach the store through `cli.py`
    and no other module builds one.
    """
    source = CLI_SOURCE.read_text(encoding="utf-8")
    assert "LocalSessionStore(" in source
    for module in (REPO_ROOT / "pa_agent").rglob("*.py"):
        if module.name in ("cli.py", "session.py") or "stores" in module.parts:
            continue
        assert "LocalSessionStore" not in module.read_text(encoding="utf-8"), (
            f"{module} constructs a session store; the CLI is the one place "
            "a store is constructed (REQ-41)"
        )
