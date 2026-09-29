"""T-106 — the two committed packets, compared three ways (REQ-74, A13, D136).

v1.5's last row. Four rows built the packet, the review log, the submission and
the document index; this one is the check over the **rendered** surface, and it
is the first thing in this repository to re-read a **session** from a committed
file. That is D127's own named reversal being taken: *reverses if a session ever
needs to be re-read by a gate — v1.5's rendered packet is the candidate, and it
would be a committed fixture rather than a tracked live root*. `data/sessions/`
and `data/outbox/` stay gitignored output; the fixtures live here, under
`tests/`, because a packet has no upstream for `verify_sources.py` to re-fetch
and no manifest could pin it (D136).

**Three comparisons, because they catch three different mutations.** None of
them implies another.

1. **The render equals the committed bytes.** The only one that can see a change
   nobody meant to make — a key added to the renderer, a section reordered, a
   count that stops counting.
2. **Rendering twice is identical.** D127 measured this distinction on the
   session adapter: one write passes a serializer that stamps a clock-derived or
   randomised value and two writes fail it. Held here in-process *and* across a
   process boundary, since the committed bytes were produced by one process and
   read by another.
3. **The determination is re-derived from the live engine and compared.** The
   one this row exists for. Without it the fixture is a recording of an answer,
   and D91 is the entry about what happens to a recording's free half:
   `eval/agentic/results.json`'s oracle columns sat two tasks stale describing a
   path with no verifier in it, while every gate stayed green because the
   recording's *internal* coherence was what was checked. A committed packet is
   that shape exactly — it embeds a determination, and the renderer's own tests
   keep passing over a determination the engine no longer produces.

**Why a committed fixture is possible at all:** `pa_agent/form.py` imports no
clock. Every date in a packet arrives **on the `Session`** — `created_at`, each
run's `ran_at` and `as_of`, each `ReviewEntry.at` — all stamped by `cli._now()`,
the one clock this system has (D127), and `Message-ID` is a digest of
`(session_id, run_index)` rather than the random value `EmailMessage` would
stamp (D131). `tests/test_form.py::test_form_names_no_clock` is the parse that
holds it; this file is the month-later comparison that parse says it is waiting
for, and it re-asserts the behavioural half over the fixture below.

**The fixtures are parsed, not grepped.** `Session.model_validate_json` reads
each `session.json`, so a fixture that has drifted from the contract is a red
suite rather than a string comparison against a blob that no longer loads. The
**count** is a pinned literal (D51's move, on a third file): deleting a fixture
is a visible diff and a red suite rather than a quieter suite over whatever is
left.

**The two shapes are not arbitrary.** `accepted_red` is the only packet this
repository can produce that reaches the **knowledge** corpus — a suggestion's
`effect` is a span into an FDA label (T-134, D133) — and the only one that
exercises the justification path; every claim about a packet carrying a
suggestion is invisible on a packet without one, which is how D133's defect
survived two rows with every gate green. `no_suggestion` is the widest render
the corpus produces: three documents over nine spans, with a non-empty review
log that contributes nothing to the bytes.

**Spends no model call and touches no network**, and one test holds that
directly: `pa_agent.tiers.client_for` is patched to raise and both fixtures still
render to their committed bytes. `tiers.py` is the one place a client is built
(T-90, D106) and every runner takes an injected client, so a render that
completes without one cannot have called a model. That is A13's fifth clause
over this version's surface; the gate **list** is held by
`tests/test_check_gates.py::test_no_gate_spends_a_model_call_or_reaches_the_network`.

**To regenerate a fixture** after deliberately changing the renderer — there is
no builder script, because the product's own verb is the builder (D136):

    cp tests/fixtures/packets/<name>/session.json /tmp/root/<name>.json
    python -m pa_agent.cli session --sessions-root /tmp/root packet <name> \
      > tests/fixtures/packets/<name>/packet.eml

Deliberately not a flag on this file: an `--update-fixture` switch is a way to
make comparison 1 self-fulfilling by typing one word.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pa_agent import cli, form
from pa_agent.contracts import ReviewAction, Session, SuggestionColour
from pa_agent.model_pin import MEASURED_TIER
from pa_agent.spans import validate as validate_span
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.stores.session import DEFAULT_SESSION_ROOT

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "packets"
FORM_SOURCE = REPO_ROOT / "pa_agent" / "form.py"

#: Pinned as a literal, so deleting a fixture is a red suite rather than a
#: suite that quietly checks one packet (D51's move, D136).
FIXTURE_COUNT = 2

#: What each fixture is *for*, asserted rather than assumed — a fixture that
#: stopped carrying its shape would leave the clause it holds checking an empty
#: set, which is D123's finding on A11 and D131's rewrite of A13's first clause.
#:
#: `spans` is the citation manifest's length and `documents` the corpora the
#: packet opens, written as a count here and compared below against ids read off
#: the **stores**: a set compared to the traversal that built it agrees under
#: every mutation of that traversal (D65's shape).
SHAPES: dict[str, dict] = {
    "accepted_red": {
        "patient_id": "a8edc52e-9800-0adb-c775-bd81183c355f",
        "accepted": 1,
        "spans": 3,
        "documents": 2,
    },
    "no_suggestion": {
        "patient_id": "07a5f345-3e7c-da0f-da0b-87fa252a5bfd",
        "accepted": 0,
        "spans": 9,
        "documents": 3,
    },
}

#: The knowledge corpus' own document, named because `accepted_red` is the only
#: packet that reaches it and a failure should say which corpus went missing.
LABEL_DOCUMENT = "spl_hydrochlorothiazide"

FIXTURES = sorted(SHAPES)


# --------------------------------------------------------------------------
# Reading the fixtures
# --------------------------------------------------------------------------


def _session_path(name: str) -> Path:
    return FIXTURE_ROOT / name / "session.json"


def _packet_path(name: str) -> Path:
    return FIXTURE_ROOT / name / "packet.eml"


def _session(name: str) -> Session:
    """The fixture, **parsed**. A drifted fixture fails here and not later."""
    return Session.model_validate_json(_session_path(name).read_text(encoding="utf-8"))


def _committed(name: str) -> str:
    return _packet_path(name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def policies() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patients() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def knowledge() -> LocalKnowledgeStore:
    return LocalKnowledgeStore()


@pytest.fixture(scope="module")
def payer() -> str:
    """The recipient the verb resolves, from the committed payer directory.

    Read through the port rather than written down, because `session packet`
    resolves it the same way and the fixture's `To:` line is that string
    (T-105, D134).
    """
    return cli._payer_store().get_payer(cli.DEFAULT_PAYER_ID).recipient


def _assemble(name: str, payer: str):
    """One packet, through the composition root the verb uses.

    `cli._assemble_packet` builds the three ports, computes the review and calls
    `form.assemble`; it is the function `session packet` and `session submit`
    share, so this is the assembly a payer would receive and not a second one
    written here (D131).
    """
    return cli._assemble_packet(
        _session(name), 0, payer=payer, submitted_at=None
    )


# --------------------------------------------------------------------------
# The fixtures exist, load, and are the two shapes
# --------------------------------------------------------------------------


def test_the_fixture_count_is_pinned():
    """Two packets, each a session and the bytes it renders to.

    The literal is the point. *Every fixture renders byte-identically* is
    satisfied by one fixture, and by none — D131 rewrote this row's exit because
    *one packet* is a sample of one chosen by whoever wrote it.
    """
    directories = sorted(p.name for p in FIXTURE_ROOT.iterdir() if p.is_dir())
    assert directories == FIXTURES, (
        f"{FIXTURE_ROOT} holds {directories}; this row commits {FIXTURES}"
    )
    assert len(directories) == FIXTURE_COUNT
    for name in FIXTURES:
        assert _session_path(name).is_file(), f"{name} has no session.json"
        assert _packet_path(name).is_file(), f"{name} has no packet.eml"


def test_the_fixtures_are_not_a_live_session_root():
    """D136's placement, pinned so it cannot be reversed quietly.

    The fixtures are under `tests/`, not under `data/`: a packet has no upstream
    for `verify_sources.py` to re-fetch, no manifest pins it, and
    `data/sessions/` is the directory D127 kept gitignored precisely so a local
    run and a mutation run could write into it without touching tracked bytes.
    """
    assert FIXTURE_ROOT.is_relative_to(REPO_ROOT / "tests")
    # The module constant, not a constructed store: `tests/test_session_verbs.py`
    # refuses a `LocalSessionStore()` with no root because one writes into the
    # working tree, and the claim here is about where the default *points*.
    assert DEFAULT_SESSION_ROOT != FIXTURE_ROOT, (
        "the adapter's default root is the fixture directory; a local "
        "`session create` would then write into tracked bytes (D127)"
    )
    assert DEFAULT_SESSION_ROOT.is_relative_to(REPO_ROOT / "data"), (
        "the live session root moved out of data/; this test's whole point is "
        "that the tracked fixtures and the written sessions are two places"
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_session_validates_against_the_contract(name):
    """Parsed, never grepped.

    `Session`'s validators are the ones that matter here: runs must match the
    state, every review must bind to a snapshot the session holds (REQ-75), and
    a submission or decision must exist exactly when the state says so
    (REQ-77). A fixture that has drifted from any of them is a red suite rather
    than a `.eml` still matching a blob nothing can load.
    """
    session = _session(name)
    assert session.session_id == name, (
        "the fixture's id is its directory name, so the regeneration recipe in "
        "this module's docstring is a copy and not a rename"
    )
    assert len(session.runs) == 1, "each fixture is one snapshot, and says so"
    assert session.submission is None and session.decision is None, (
        "these fixtures are what `session packet` prints; the submitted "
        "artifact is T-105's and lives in the outbox"
    )


def test_the_two_fixtures_are_the_two_shapes(payer):
    """One carries a justified red, one carries none — the exit's own words.

    Asserted rather than assumed. If the knowledge table or the bundle moved and
    `accepted_red` stopped carrying a suggestion, every claim below about the
    justification path and the third corpus would pass over an empty set, which
    is the vacuity D123 found in A11 and D131 rewrote A13's first clause over.
    """
    counts = {}
    for name in FIXTURES:
        packet = _assemble(name, payer)
        counts[name] = len(packet.suggestions)
    assert counts == {n: SHAPES[n]["accepted"] for n in FIXTURES}, counts

    red = _assemble("accepted_red", payer)
    suggestion = red.suggestions[0]
    assert suggestion.colour is SuggestionColour.RED
    assert suggestion.justification, (
        "the red fixture's whole job is the justified-acceptance path; a red "
        "with no justification cannot be assembled at all (REQ-74)"
    )
    assert suggestion.effect.document_id == LABEL_DOCUMENT, (
        "the accepted suggestion's effect cites the FDA label, which is the "
        "third corpus a packet's citations point into (D133)"
    )


def test_the_acceptance_and_its_justification_share_one_timestamp():
    """The fixture is a witness to the log-order rule, not just to the bytes.

    `form._latest_by_row` reads the log **in order** and never by comparing
    `at`, because two entries written in the same second compare equal (D131).
    The verbs produced exactly that — the accept and the justify landed inside
    one second — so the fixture keeps it: a reader that sorted on `at` could not
    tell which of these two entries came last, and the packet would be decided
    by whatever the sort happened to do.
    """
    session = _session("accepted_red")
    actions = [entry.action for entry in session.reviews]
    assert actions == [
        ReviewAction.ACCEPT_SUGGESTION,
        ReviewAction.JUSTIFY_SUGGESTION,
    ], actions
    assert session.reviews[0].at == session.reviews[1].at, (
        "the two entries no longer share a timestamp; the fixture stops being "
        "the case that distinguishes log order from a sort on `at` (D131)"
    )


# --------------------------------------------------------------------------
# Comparison 1: the render is the committed bytes
# --------------------------------------------------------------------------


def _session_cmd(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", "session",
         "--sessions-root", str(root), *args],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_the_verb_prints_the_committed_bytes(tmp_path, name):
    """Comparison 1, through the whole composition root.

    The fixture session is copied into a scratch root and `session packet` is
    run against it, so what is compared is a user's stdout and not a value
    assembled in process. `_verb_packet` writes `form.render(packet)` and
    nothing else, which is why stdout is the whole artifact.
    """
    root = tmp_path / "sessions"
    root.mkdir()
    shutil.copy(_session_path(name), root / f"{name}.json")

    proc = _session_cmd(root, "packet", name)
    assert proc.returncode == 0, proc.stderr
    assert not proc.stderr, proc.stderr

    expected = _committed(name)
    assert proc.stdout == expected, (
        f"`session packet {name}` no longer prints its committed fixture. If "
        "the renderer changed on purpose, regenerate it with the recipe in this "
        "module's docstring and commit the diff; if it did not, this is the "
        "change nobody meant to make (D136)."
    )


@pytest.mark.parametrize("name", FIXTURES)
def test_assembling_and_rendering_twice_is_identical(payer, name):
    """Comparison 2, in-process, and against the committed bytes.

    Two assemblies and two renders: a clock or a random value inside the
    renderer fails the render pair, and one inside `assemble` — a serializer
    stamping a non-contract key, which is what D127 measured on the session
    adapter — fails the assembly pair. Both are then compared to the file,
    because the file was written by a different process on a different day, and
    a value that is stable within one run and not across runs is caught only
    there.
    """
    first = _assemble(name, payer)
    second = _assemble(name, payer)
    rendered = [form.render(first), form.render(first), form.render(second)]
    assert rendered[0] == rendered[1], "two renders of one packet differ"
    assert rendered[0] == rendered[2], (
        "two assemblies of one session render differently; something between "
        "the session and the bytes is generating a value (D127)"
    )
    assert rendered[0] == _committed(name)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


@pytest.mark.parametrize("name", FIXTURES)
def test_every_iso_date_in_the_render_is_a_value_the_session_carries(payer, name):
    """The behavioural half of *`form.py` names no clock*, over the fixture.

    `tests/test_form.py::test_form_names_no_clock` parses the module and refuses
    `datetime`, `time`, `calendar` and any `.now()`. This is the same claim from
    the other side: every ISO-8601 date in the rendered packet is a substring of
    the session that produced it, so there is no date in the artifact that did
    not arrive on the object. A clock reached by some route the parse does not
    name — an import alias, a helper in another module — would put one here.
    """
    rendered = form.render(_assemble(name, payer))
    carried = _session_path(name).read_text(encoding="utf-8")
    stamped = sorted(
        {match for match in re.findall(r"\d{4}-\d{2}-\d{2}", rendered)}
        - {match for match in re.findall(r"\d{4}-\d{2}-\d{2}", carried)}
    )
    assert not stamped, (
        f"{stamped} appear in the rendered packet and in no field of the "
        "session; every date in a packet arrives on the Session, stamped by "
        "cli._now() (D127, D131)"
    )


def test_form_still_names_no_clock():
    """Re-asserted here because this is the row whose fixture the absence protects.

    `tests/test_form.py` owns the scan; it is repeated over the import graph in
    one line so that the file holding the committed bytes fails too, naming the
    fixture. A reader who deletes the parse check should meet this one.
    """
    tree = ast.parse(FORM_SOURCE.read_text(encoding="utf-8"))
    clocked = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            clocked |= {
                alias.name for alias in node.names
                if alias.name.split(".")[0] in {"datetime", "time", "calendar"}
            }
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in {"datetime", "time", "calendar"}:
                clocked.add(node.module)
        if isinstance(node, ast.Attribute) and node.attr in {"now", "today", "utcnow"}:
            clocked.add(f".{node.attr}()")
    assert not clocked, (
        f"form.py names {sorted(clocked)}; the committed fixtures in "
        f"{FIXTURE_ROOT.relative_to(REPO_ROOT)} are only comparable because "
        "every unclockable value arrives as an argument (D127, D136)"
    )


# --------------------------------------------------------------------------
# Comparison 3: the determination is re-derived, never trusted
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_the_fixture_determination_is_what_the_live_engine_produces(
    name, patients, policies
):
    """Comparison 3 — D91's lesson made structural, and the reason the fixture
    is not just a recording of an answer.

    The request is re-run through `determine()` at the run's own `as_of`, with
    the runners `session run` builds under the default `--extraction recorded`,
    and the result is compared field for field to the determination the fixture
    embeds. Without this, a criterion whose verdict moved — a constant edited, a
    predicate rewired, a span slipping by a character — leaves both the fixture
    and the renderer's own tests passing, because what they compare is the
    packet to itself.

    It is the clause that would have to be **replaced** rather than dropped on
    the day a fixture's determination stops being re-derivable for free (D136's
    reversal condition).
    """
    session = _session(name)
    run = session.runs[0]
    runner = cli._build_runner(
        "recorded", cli.DEFAULT_RECORDING, False, patients, MEASURED_TIER
    )
    verifier = cli._build_verifier(
        "recorded", cli.DEFAULT_VERIFIER_RECORDING, MEASURED_TIER
    )

    code, live = cli._determine_or_report(
        policies,
        session.intake.procedure_code,
        patient_id=session.intake.patient_id,
        patient_store=patients,
        as_of=run.as_of,
        runner=runner,
        verifier=verifier,
        state=session.intake.state,
    )
    assert code == 0 and live is not None, (
        f"re-running {name}'s request reported {code}; a fixture whose request "
        "no longer answers is a fixture over a path that no longer exists"
    )
    assert live.model_dump(mode="json") == run.determination.model_dump(mode="json"), (
        f"the engine's answer for {name}'s request is not the determination the "
        "fixture carries. The fixture is a snapshot of this engine's output, so "
        "either a verdict moved and the fixture is stale, or the fixture was "
        "edited by hand (D91)."
    )
    assert run.policy_version_id == live.policy_version_id


@pytest.mark.parametrize("name", FIXTURES)
def test_the_rendered_determination_is_the_session_s_own(payer, name):
    """The re-derivation reaches the *bytes*, not only the object.

    `form.assemble` embeds `cli._render`'s dict verbatim and holds no renderer
    of its own (D129, D131), so the determination the fixture's `.eml` shows is
    the determination comparison 3 just re-derived — asserted here rather than
    inferred, because a renderer that embedded a summary instead would pass
    every check above.
    """
    session = _session(name)
    expected = json.dumps(
        cli._render(session.runs[0].determination), indent=2, ensure_ascii=False
    )
    assert expected in _committed(name), (
        "the committed packet does not contain the rendered determination "
        "verbatim; a second renderer is a second answer to one question (D129)"
    )


# --------------------------------------------------------------------------
# A13's second clause, re-checked over the committed fixture
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_every_citation_in_the_committed_fixture_slices_back(
    name, payer, policies, patients, knowledge
):
    """A13's clause 2, which names this row: *re-checked over the committed
    fixture by `T-106`*.

    The count is reported beside the check, because *every citation valid* is
    satisfied by a packet with none — the defect D131 rewrote the gate over. The
    index is built by the real `cli._packet_index` over all three ports, and
    every span is re-validated here independently of `assemble`'s own pass.
    """
    packet = _assemble(name, payer)
    manifest = form.citations(packet)
    assert len(manifest) == SHAPES[name]["spans"], (
        f"{name} cites {len(manifest)} span(s) and this fixture is committed "
        f"with {SHAPES[name]['spans']}"
    )

    index = cli._packet_index(
        form.source_ids(
            determination=_session(name).runs[0].determination,
            review=None,
        )
        + tuple(span.document_id for span in manifest),
        policies,
        patients,
        knowledge,
    )
    for span in manifest:
        assert validate_span(span, index), span
        assert index.slice(span) == span.quote or span.quote is None, span

    assert len(packet.cited_documents) == SHAPES[name]["documents"], (
        f"{name} names {packet.cited_documents}"
    )
    where = f"Citation manifest ({len(manifest)})"
    assert where in _committed(name), (
        f"the committed packet does not report {where}; the count is part of "
        "the artifact, not of this test"
    )


def test_the_fixtures_between_them_open_two_corpora(payer, patients):
    """The two shapes reach different corpora, which is why there are two.

    `accepted_red` cites the chart's bundle and an **FDA label**; `no_suggestion`
    cites the bundle and both chart notes. Read off the patient store rather
    than back off the traversal under test — a set compared to the walk that
    built it agrees under every mutation of that walk (D65's shape, D135).
    """
    red = _assemble("accepted_red", payer)
    red_bundle = patients.get_observations(SHAPES["accepted_red"]["patient_id"])[0]
    assert set(red.cited_documents) == {
        red_bundle.span.document_id,
        LABEL_DOCUMENT,
    }, red.cited_documents

    plain = _assemble("no_suggestion", payer)
    patient_id = SHAPES["no_suggestion"]["patient_id"]
    notes = {f"{patient_id}/chart_note_{n}.txt" for n in (1, 2)}
    assert notes <= set(plain.cited_documents), plain.cited_documents
    assert LABEL_DOCUMENT not in plain.cited_documents, (
        "a packet with no accepted suggestion cites no label; the two fixtures "
        "would then be one shape twice"
    )


# --------------------------------------------------------------------------
# A13's fifth clause, over this version's surface
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_rendering_the_committed_fixtures_builds_no_model_client(
    monkeypatch, payer, name
):
    """A13's fifth clause: **zero model calls**, held where this version adds surface.

    `pa_agent/tiers.py` is the one place a model client is built (T-90, D106) and
    every runner takes an **injected** client, so a render that completes with
    `client_for` raising cannot have called a model. Every import of it in this
    repository is function-local, which is what makes one patch reach all of
    them.

    The gate **list** is a different claim and is held by
    `tests/test_check_gates.py::test_no_gate_spends_a_model_call_or_reaches_the_network`.
    *The suite as a whole spends nothing* is held by no command and is numbered
    `T-141` rather than asserted here.
    """
    import pa_agent.tiers as tiers

    def refuse(tier):  # pragma: no cover - the point is that it is not called
        raise AssertionError(
            f"a model client was built for tier {tier!r} while rendering a "
            "committed packet; this path replays T-15's extraction recording, "
            "T-17's verifier recording and T-98's quote recording and spends "
            "nothing (A13 clause 5)"
        )

    monkeypatch.setattr(tiers, "client_for", refuse)
    assert form.render(_assemble(name, payer)) == _committed(name)
