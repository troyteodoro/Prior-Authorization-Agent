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
import json
import subprocess
import sys

from pathlib import Path

import pytest

from pa_agent import cli
from pa_agent.contracts import Intake, Session, SessionRun, SessionState
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


def _created(root: Path, *args: str) -> str:
    proc = _session(root, "create", *args)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["session_id"]


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
    mapping and not the other verb's. The bytes are compared after the refusal:
    a machine that advanced and then failed to persist would look identical to
    one that refused.
    """
    root = tmp_path / "sessions"
    session_id = _created(
        root, "--patient", PATIENT, "--procedure", COVERED_CODE, "--state", "WA"
    )
    _session(root, "run", session_id, "--as-of", AS_OF)

    store = LocalSessionStore(root)
    determined = store.get(session_id)
    store.save(determined.model_copy(update={"state": SessionState.IN_REVIEW}))
    before = (root / f"{session_id}.json").read_bytes()

    proc = _session(root, "run", session_id, "--as-of", AS_OF)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "IN_REVIEW" in proc.stderr
    assert (root / f"{session_id}.json").read_bytes() == before, (
        "a refused transition rewrote the session; REQ-71 says it is never "
        "recorded"
    )


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


def test_a_verb_this_version_does_not_have_is_refused(tmp_path):
    """v1.5's remaining verbs are not silently accepted no-ops.

    `review` left this list at `T-104` and `submit` and `decide` leave it at
    `T-105`. The list shrinking is the point: a verb named here and shipped is
    a test that would pass on a command that parses and does nothing.
    """
    for absent in ("submit", "decide"):
        proc = _session(tmp_path / "sessions", absent)
        assert proc.returncode != 0, absent
        assert proc.stdout == "", absent


def test_the_default_session_root_is_the_repositorys_and_is_gitignored():
    """The adapter's default, and the reason no test may use it.

    A test that forgets `--sessions-root` writes into the working tree, and
    the suite still passes — silently, which is the whole failure mode. The
    root is asserted here and every test above passes `tmp_path`.
    """
    assert DEFAULT_SESSION_ROOT == REPO_ROOT / "data" / "sessions"
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/sessions/" in ignore


def test_no_test_in_this_file_writes_to_the_default_root():
    """The guard for the guard.

    Every helper here takes a root. If one stopped, this file would quietly
    start writing into `data/sessions/` and nothing else would say so.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_session":
            assert node.args, "_session() called with no root"
        # Parsed rather than grepped: a substring scan for the constructor
        # matches *this test's own text*, which is a check that reports itself
        # and can never report anything else. Measured — the text form failed
        # on its first run, on itself.
        if isinstance(func, ast.Name) and func.id == "LocalSessionStore":
            assert node.args or node.keywords, (
                "a test constructed the adapter with no root, which writes "
                "into data/sessions/ in the working tree"
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
