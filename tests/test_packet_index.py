"""T-134 — the packet's document index reaches **three** corpora (REQ-74, D133).

D131 built `cli._packet_index` from the patient and policy stores and wrote that
*a packet's spans point into both*. A suggestion's `effect` is a span into an FDA
label, served by the **knowledge** store — the third hashed corpus (T-96, D118;
its port T-97, D119) — so every packet carrying an accepted suggestion was
refused with `UncitedPacket`/`UNKNOWN_DOCUMENT`, and `T-103`'s headline
capability could not be exercised at all.

**Every gate stayed green because the two halves of the claim live in two files
and never met.** `tests/test_form.py`'s red tests exercise `form.accepted`, which
validates no span; its assembling tests use a determination with **no accepted
suggestion**, so the traversal never reaches a label. `tests/test_review_log.py`
drives `--accept` and `--justify` through the verb and then reads
`form.accepted` in process, stopping one call short of `assemble`. This file is
where they meet.

Four checks, and the last three are here because the first cannot see them.

- **End to end through the verb**, which is how the defect was found: create,
  run, accept, justify, packet. It is the test that fails on the code as it
  stood, with the real refusal rather than a signature error.
- **The label span sliced out of the hashed bytes**, through the real
  `cli._packet_index` and the real validator, because *the packet assembled* and
  *the citation addresses the committed label* are two claims and only the second
  is Article III's.
- **The set of ports the index consults, derived from the package.** A
  `knowledge_store` added by hand today is a fourth corpus forgotten tomorrow, so
  the universe is every store `Protocol` that declares `get_document` and the
  assertion is that `_packet_index` takes and iterates each one. It is
  `test_the_stated_port_count_is_the_adapter_count`'s shape, applied to a call
  rather than to prose.
- **No document id is served by two ports.** The other mutation on this function
  is to reorder the stores so an id served by two resolves through the wrong one;
  measured, the three id spaces are disjoint, so no reorder changes any index this
  corpus can build. An unreachable mutation is not a caught one, so what is
  asserted is the property that makes it unreachable (D133).

**`T-137` (D135) added to two of them rather than a fifth.** The packet this
file assembles is the only one in the repository that can separate *the
manifest's documents* from *the determination's* — every other packet carries no
accepted suggestion, so the two sets are equal — which is why
`Packet.cited_documents` is asserted here, in the object and in the rendered
`.eml`, against ids read off the **patient store** rather than off the traversal
under test.

**Spends no model call and touches no network.** The one determination replays
`T-15`'s extraction recording and `T-98`'s quote recording, the two replays
`--suggest` has used since `T-99`.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys

from pathlib import Path

import pytest

from pa_agent import cli, form
from pa_agent.contracts import (
    Intake,
    ReviewAction,
    ReviewEntry,
    Session,
    SessionRun,
    SessionState,
    SuggestionColour,
)
from pa_agent.index import DocumentIndex
from pa_agent.spans import validate as validate_span
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = REPO_ROOT / "pa_agent" / "cli.py"
STORES = REPO_ROOT / "pa_agent" / "stores"
PATIENT_DATA = REPO_ROOT / "data" / "patients"
POLICY_MANIFEST = REPO_ROOT / "data" / "policies" / "source" / "sources.json"
KNOWLEDGE_MANIFEST = REPO_ROOT / "data" / "knowledge" / "sources.json"

#: `E12`'s chart, the one the committed corpus colours **red**
#: (`tests/test_form.py`'s `RED_PATIENT`, `tests/test_review_log.py`'s), its row
#: and its code. The clock is the harness's, pinned because a verdict moves with
#: it and the verifier recording is keyed by the verdict (D78's claim digest).
RED_PATIENT = "a8edc52e-9800-0adb-c775-bd81183c355f"
RED_AS_OF = "2026-09-01"
RED_ROW = "hydrochlorothiazide-hyperglycemia"
RED_CODE = "R73.9"
COVERED_CODE = "43775"
JUSTIFICATION = "Thiazide is active and the analyte was never drawn; coding review asked."

#: The knowledge corpus' own document, named here only so a failure says which
#: corpus went missing. The **span** is never written down: it is read off the
#: suggestion the review computes, because a hand-written offset would be this
#: file agreeing with itself instead of with the label.
LABEL_DOCUMENT = "spl_hydrochlorothiazide"


# --------------------------------------------------------------------------
# The stores, and the review the corpus produces
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policies() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patients() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def knowledge() -> LocalKnowledgeStore:
    return LocalKnowledgeStore()


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def _session_cmd(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _cli("session", "--sessions-root", str(root), *args)


def _reviewed(root: Path, *, justify: bool = True) -> str:
    """One session carrying a determination and a reviewer's acceptance.

    Driven through the verbs, in `tests/test_review_log.py`'s style, because the
    defect this file closes lived in the seam between two verbs and only appeared
    when they met.
    """
    created = _session_cmd(
        root, "create", "--patient", RED_PATIENT, "--procedure", COVERED_CODE,
        "--state", "WA", "--icd10", "E66.01",
    )
    assert created.returncode == 0, created.stderr
    session_id = json.loads(created.stdout)["session_id"]

    ran = _session_cmd(root, "run", session_id, "--as-of", RED_AS_OF)
    assert ran.returncode == 0, ran.stderr

    actions = [("--accept", RED_ROW, "--code", RED_CODE)]
    if justify:
        actions.append(
            ("--justify", RED_ROW, "--code", RED_CODE, "--justification", JUSTIFICATION)
        )
    for extra in actions:
        proc = _session_cmd(root, "review", session_id, "--reviewer", "Sam", *extra)
        assert proc.returncode == 0, proc.stderr
    return session_id


def _review_for(determination, policies, patients, knowledge):
    return cli._review(
        determination, RED_PATIENT, policies, patients, knowledge
    ).review


def _red_suggestion(review):
    return next(s for s in review.suggestions if s.colour is SuggestionColour.RED)


# --------------------------------------------------------------------------
# End to end: the two verbs, met
# --------------------------------------------------------------------------


def test_a_packet_carrying_an_accepted_suggestion_assembles(tmp_path):
    """The reproduction, and the test that failed on the code as it stood.

    `session packet` exited 1 with
    `UNKNOWN_DOCUMENT: 'spl_hydrochlorothiazide' is not in the index`, so the
    capability `T-103` delivered — the packet carries the accepted suggestions
    and their justifications — was unreachable through the verbs that produce it
    (D133).
    """
    root = tmp_path / "sessions"
    session_id = _reviewed(root)

    proc = _session_cmd(root, "packet", session_id)
    assert proc.returncode == 0, proc.stderr
    assert not proc.stderr, proc.stderr

    rendered = proc.stdout
    assert f"{RED_CODE}  " in rendered, "the accepted code is not on the form"
    assert JUSTIFICATION in rendered, "the justification the reviewer wrote is absent"
    assert LABEL_DOCUMENT in rendered, (
        f"{LABEL_DOCUMENT} is the corpus the suggestion's effect cites and the "
        "rendered packet names no document from it"
    )


def test_the_rendered_manifest_counts_the_label_citation(tmp_path):
    """The citation manifest is `form.citations`, and the label span is in it.

    Asserted over the **rendered** packet as well as the object, because
    `T-106`'s fixture is the rendered bytes and a manifest that carried the span
    without printing it would pass the object-level check alone.
    """
    root = tmp_path / "sessions"
    session_id = _reviewed(root)

    proc = _session_cmd(root, "packet", session_id, "--json")
    assert proc.returncode == 0, proc.stderr
    packet = json.loads(proc.stdout)
    effect = packet["suggestions"][0]["effect"]
    assert effect["document_id"] == LABEL_DOCUMENT

    rendered = _session_cmd(root, "packet", session_id)
    assert rendered.returncode == 0, rendered.stderr
    where = f"{LABEL_DOCUMENT}[{effect['char_start']}:{effect['char_end']}]"
    assert where in rendered.stdout, (
        f"{where} is a citation of this packet and the manifest does not list it"
    )
    counted = re.search(r"Citation manifest \((\d+)\)", rendered.stdout)
    assert counted, "the rendered packet carries no citation manifest"
    assert int(counted.group(1)) >= 1, "the manifest counts nothing"

    # The document index, rendered. `T-106` commits these bytes, so the section
    # that names the corpora is asserted here rather than left to the object
    # (D135): a list that held the label without printing it reads, to the payer,
    # as a packet that opened one corpus.
    listed = re.search(r"Cited documents \((\d+)\)\n((?:  \S.*\n)+)", rendered.stdout)
    assert listed, "the rendered packet carries no document index"
    documents = [line.strip() for line in listed.group(2).splitlines()]
    assert int(listed.group(1)) == len(documents) == 2, (
        f"the rendered index counts {listed.group(1)} and lists {documents}"
    )
    assert LABEL_DOCUMENT in documents, (
        f"the rendered index names {documents}; the accepted suggestion's effect "
        f"cites {LABEL_DOCUMENT} and the index is the manifest's documents (D135)"
    )


def test_an_unjustified_accepted_red_is_still_refused(tmp_path):
    """The refusal this close must not have bought its way past.

    A packet that assembles is only progress if the one it is *supposed* to
    refuse still is: REQ-74's clause is over the **accepted** red, and an index
    with a third corpus in it changes nothing about the justification (A13's
    first clause).
    """
    root = tmp_path / "sessions"
    session_id = _reviewed(root, justify=False)

    proc = _session_cmd(root, "packet", session_id)
    assert proc.returncode == 1
    assert not proc.stdout
    assert RED_CODE in proc.stderr, (
        f"the refusal must name the unjustified code; got {proc.stderr!r}"
    )


# --------------------------------------------------------------------------
# The span slices back out of the hashed label
# --------------------------------------------------------------------------


def _session_over(determination, entries) -> Session:
    return Session(
        session_id="packet-index-under-test",
        created_at="2026-09-01T00:00:00+00:00",
        intake=Intake(
            patient_id=determination.patient_id,
            procedure_code=determination.procedure_code,
            icd10_codes=("E66.01",),
        ),
        state=SessionState.IN_REVIEW,
        runs=(
            SessionRun(
                ran_at="2026-09-01T00:00:01+00:00",
                as_of=RED_AS_OF,
                policy_version_id=determination.policy_version_id,
                determination=determination,
            ),
        ),
        reviews=tuple(entries),
    )


def _stored(root: Path, session_id: str):
    from pa_agent.stores.session import LocalSessionStore

    return LocalSessionStore(root).get(session_id)


def test_the_index_slices_the_effect_span_out_of_the_hashed_label(
    tmp_path, policies, patients, knowledge
):
    """Article III over the third corpus, through the real builder.

    The index is built by `cli._packet_index` — not by a copy written here, which
    is what let `tests/test_form.py`'s private `_index` agree with a wrong
    `cli.py` — and the slice is compared to the quote the knowledge table
    declares. `Document` re-verifies the label's hash on construction (REQ-7), so
    this also re-checks the corpus for free.
    """
    root = tmp_path / "sessions"
    session_id = _reviewed(root)
    stored = _stored(root, session_id)
    determination = stored.runs[0].determination
    review = _review_for(determination, policies, patients, knowledge)

    ids = form.source_ids(determination=determination, review=review)
    assert LABEL_DOCUMENT in ids, "source_ids does not report the label"

    index = cli._packet_index(ids, policies, patients, knowledge)
    assert LABEL_DOCUMENT in index, (
        f"the index holds {index.ids()} and not {LABEL_DOCUMENT}; the knowledge "
        "port is the third corpus a packet's citations point into (D133)"
    )

    effect = _red_suggestion(review).effect
    assert index.slice(effect) == effect.quote, (
        "the effect span does not address the quote the knowledge table declares"
    )
    assert validate_span(effect, index), effect

    packet = form.assemble(
        session=stored,
        run_index=0,
        rendered_determination=cli._render(determination),
        review=review,
        payer="Payer <pa@payer.invalid>",
        index=index,
    )
    manifest = form.citations(packet)
    assert effect in manifest, "the accepted suggestion's effect is not a citation"
    for span in manifest:
        assert validate_span(span, index), span
    assert len(manifest) >= 2, (
        f"the packet cites {len(manifest)} span(s); this chart's determination "
        "cites at least one and the accepted suggestion adds its effect"
    )
    # Read off the **store**, not off the packet: a set compared to the
    # traversal that built it agrees under every mutation of that traversal
    # (D65's shape, and the reason this file never writes a span down either).
    bundle = patients.get_observations(RED_PATIENT)[0].span.document_id
    assert set(packet.cited_documents) == {bundle, LABEL_DOCUMENT}, (
        f"the packet names {packet.cited_documents}; it cites this chart's "
        f"bundle and the label the accepted suggestion's effect points into, "
        "and nothing else (D135)"
    )
    assert packet.cited_documents == tuple(
        dict.fromkeys(span.document_id for span in manifest)
    ), "the document index is the manifest's documents in first-cited order"


def test_an_id_no_port_serves_is_left_out_rather_than_decided_here(
    policies, patients, knowledge
):
    """D38, restated because with three ports it now looks like an omission.

    `_packet_index` leaves an unresolvable id out and `form.assemble` refuses the
    packet with a classified `UNKNOWN_DOCUMENT`. Deciding **here** what a missing
    document means would be the composition root answering a question
    `pa_agent.spans` exists to answer — and swallowing it would be worse: a
    packet rendered with a citation nothing checked.
    """
    index = cli._packet_index(
        ("no_such_document", LABEL_DOCUMENT), policies, patients, knowledge
    )
    assert "no_such_document" not in index
    assert LABEL_DOCUMENT in index, "one bad id must not drop the good ones"


# --------------------------------------------------------------------------
# The ports the index consults, derived from the package
# --------------------------------------------------------------------------


def _document_serving_ports() -> set[str]:
    """Every store module whose `Protocol` declares `get_document`.

    The universe, derived rather than listed: a fourth hashed corpus arriving
    with a manifest and an adapter joins this set on the day it is written, and
    the assertions below then fail until `_packet_index` consults it. The session
    store is absent because it declares no `get_document` — it records what the
    system answered and holds no citable document, which is the same line D133
    draws when it leaves the session plane out of `ALL_READ_PLANES`.
    """
    found: set[str] = set()
    for path in sorted(STORES.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any(
                isinstance(base, ast.Name) and base.id == "Protocol"
                for base in node.bases
            ):
                continue
            if any(
                isinstance(item, ast.FunctionDef) and item.name == "get_document"
                for item in node.body
            ):
                found.add(path.stem)
    return found


def _packet_index_def() -> ast.FunctionDef:
    tree = ast.parse(CLI_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_packet_index":
            return node
    raise AssertionError("cli._packet_index moved; re-read this test")


def test_the_derivation_finds_the_ports_it_is_supposed_to():
    """The guard for the guard: a parser returning nothing makes the two
    assertions below pass over an empty universe, which is the failure mode of
    every check that compares two derived sets (D27's lesson)."""
    ports = _document_serving_ports()
    assert ports == {"policy", "patient", "knowledge"}, (
        f"the store package's document-serving ports parsed as {sorted(ports)}; "
        "three declare get_document and the session store does not"
    )


def test_the_packet_index_consults_every_port_that_serves_a_document():
    """The check a fourth corpus cannot pass by being forgotten (D133).

    Both halves matter and neither implies the other: a parameter the function
    does not iterate is a store passed and never read, and an iterated name that
    is not a parameter does not compile. The names are derived from the port —
    `PolicyStore` → `policy_store` — so nothing here is a literal a new corpus
    could satisfy by absence.
    """
    expected = {f"{port}_store" for port in _document_serving_ports()}
    node = _packet_index_def()

    parameters = {arg.arg for arg in node.args.args}
    missing = expected - parameters
    assert not missing, (
        f"_packet_index takes {sorted(parameters)} and every port that serves a "
        f"document must be one of them; {sorted(missing)} is absent. A packet's "
        "citations point into every corpus anything it carries can cite, which "
        "since T-96 is three (REQ-74, D133)"
    )

    iterated: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.For) and isinstance(inner.iter, ast.Tuple):
            iterated.update(
                element.id
                for element in inner.iter.elts
                if isinstance(element, ast.Name)
            )
    unconsulted = expected - iterated
    assert not unconsulted, (
        f"_packet_index iterates {sorted(iterated)}; {sorted(unconsulted)} is a "
        "parameter it never reads, which is a store passed in and ignored — the "
        "shape a runtime check cannot see because it holds for the store nobody "
        "passed"
    )


# --------------------------------------------------------------------------
# The order of the stores is irrelevant, and that is the asserted property
# --------------------------------------------------------------------------


def _patient_document_ids() -> set[str]:
    """The patient plane's id space, read off the two manifests that own it.

    Bundles are keyed by filename and notes by `document_id`, and they share one
    id space told apart by the record that names them (`LocalPatientStore`'s own
    rule). Derived here rather than probed, because the other two ports' sets
    have to be tested *against* it.
    """
    population = json.loads((PATIENT_DATA / "manifest.json").read_text(encoding="utf-8"))
    notes = json.loads(
        (PATIENT_DATA / "notes" / "manifest.json").read_text(encoding="utf-8")
    )
    ids = {b["filename"] for b in population["bundles"]}
    ids |= {n["document_id"] for n in notes["notes"]}
    assert len(ids) > 14, "the patient id space parsed to almost nothing"
    return ids


def _manifest_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = {d["document_id"] for d in payload["documents"]}
    assert ids, f"{path.name} lists no documents"
    return ids


def test_no_document_id_is_served_by_two_ports(policies, patients, knowledge):
    """Why the store tuple's **order** is not a decision (D133).

    The second mutation on `_packet_index` is to reorder the stores so an id
    served by two resolves through the wrong one. Measured: unreachable — the
    three id spaces are disjoint, so the loop's `break` can only fire on one
    store. An unreachable mutation is not a caught one, so the property that
    makes it unreachable is what is asserted here, in both directions and through
    the public `get_document`: every port serves all of its own ids, and raises
    on every id of the other two.

    It is also the check that fails first on the day a corpus ships an id another
    already has, at which point the index has to state a precedence in code.
    """
    spaces = {
        "policy": (policies, _manifest_ids(POLICY_MANIFEST)),
        "patient": (patients, _patient_document_ids()),
        "knowledge": (knowledge, _manifest_ids(KNOWLEDGE_MANIFEST)),
    }
    for name, (store, ids) in spaces.items():
        for other, (_, other_ids) in spaces.items():
            if other == name:
                continue
            overlap = ids & other_ids
            assert not overlap, (
                f"{sorted(overlap)} is served by both the {name} and the {other} "
                "port, so which document an id resolves to is decided by the "
                "order of the stores in _packet_index (D133)"
            )

    for name, (store, ids) in spaces.items():
        for document_id in sorted(ids):
            assert store.get_document(document_id).document_id == document_id
        for other, (_, other_ids) in spaces.items():
            if other == name:
                continue
            for foreign in sorted(other_ids):
                with pytest.raises(KeyError):
                    store.get_document(foreign)


def test_the_index_is_the_same_whatever_order_the_ports_are_given(
    policies, patients, knowledge
):
    """The behavioural half of the paragraph above, over a real packet's ids.

    Held over every permutation rather than over the one the function uses, so
    *the order does not matter* is a measurement on this corpus and not a claim
    about the code that happens to read it.
    """
    from itertools import permutations

    ids = (
        LABEL_DOCUMENT,
        *sorted(_manifest_ids(POLICY_MANIFEST))[:2],
        *sorted(_patient_document_ids())[:2],
    )
    baseline = cli._packet_index(ids, policies, patients, knowledge).ids()
    assert len(baseline) == len(set(ids))
    for order in permutations((policies, patients, knowledge)):
        index = DocumentIndex()
        for document_id in ids:
            for store in order:
                try:
                    index.add(store.get_document(document_id))
                except KeyError:
                    continue
                break
        assert index.ids() == baseline, (
            "a permutation of the ports built a different index; an id is served "
            "by two of them and the tuple's order is deciding which"
        )
