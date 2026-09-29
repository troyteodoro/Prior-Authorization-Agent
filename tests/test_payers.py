"""The payer directory, and the gate it deliberately does not have (T-105, D134).

`data/payers/payers.json` is **committed and in no hashed manifest**.
`verify_sources.py` verifies committed bytes against a public re-download, and
this file is synthesized: there is no upstream, so a manifest record for it would
carry no URL and `--fetch` nothing to fetch — a hashed record asserting provenance
it does not have. That is v2.1's synthetic rule arriving early.

There is a second reason, and it is about the numbers this repository checks.
*Nine policy documents* and *five FDA labels* are counts
`tests/test_docs_consistency.py` re-derives from the two hashed manifests; a payer
record able to raise either one would make two corpora into one. So this file
holds the artifact instead, and the last test here is the one that says the two
corpora stayed two.

`tests/test_knowledge_store.py`'s shape on a fifth port, with the half that does
**not** invert front and centre: an empty directory raises, because this is a
corpus the repository ships and an empty one is a broken checkout. D127's `[]` was
granted to the session plane for the one reason that its contents are written by
the system, and `stores/outbox.py` inherits it for the same reason. This port does
not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import SimulatedPayer
from pa_agent.stores.payer import (
    DEFAULT_PAYER_ROOT,
    DIRECTORY_FILE,
    LocalPayerStore,
    PayerNotFound,
    PayerStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "pa_agent"

#: The committed directory's size, pinned as a literal (D51's move, on a sixth
#: file). A new recipient is then a visible diff in a test rather than a row
#: nobody re-derived — and the count matters here because every test below that
#: says *every record* is satisfied by a file with one.
PAYER_COUNT = 2

#: The ids the CLI's default and its tests name. Pinned for the same reason.
PAYER_IDS = {"sim-national-a", "sim-regional-b"}

#: Exactly the fields a record carries (D51's shape, D127's application of it).
#: A field added to carry a policy, a code set or a chart fails where a reviewer
#: reads its name — this is a **contact**, and the payer axis is v2.0's (D112).
PAYER_FIELDS = {"payer_id", "display_name", "address", "simulated", "note"}


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(
        (DEFAULT_PAYER_ROOT / DIRECTORY_FILE).read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def payers() -> list[SimulatedPayer]:
    return LocalPayerStore().list_payers()


def _record(**overrides) -> dict:
    record = {
        "payer_id": "sim-x",
        "display_name": "Simulated X",
        "address": "prior-auth@sim-x.invalid",
        "simulated": True,
        "note": "a record written in a test",
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------
# The port
# --------------------------------------------------------------------------


def test_the_adapter_satisfies_the_port():
    assert isinstance(LocalPayerStore(), PayerStore)


def test_the_default_root_is_the_payer_directory():
    assert LocalPayerStore().root == DEFAULT_PAYER_ROOT
    assert DEFAULT_PAYER_ROOT == REPO_ROOT / "data" / "payers"


def test_an_unknown_id_raises_rather_than_falling_back(payers):
    """A default is not a fallback (D134).

    `cli.DEFAULT_PAYER_ID` is what the flag defaults to when nobody says; an id
    somebody *did* say and the directory does not serve is a bad request, because
    quietly sending to a different recipient than the one named is the one thing a
    directory must not do.
    """
    with pytest.raises(PayerNotFound, match="no payer 'nope'"):
        LocalPayerStore().get_payer("nope")


def test_the_not_found_error_names_what_the_directory_does_serve():
    with pytest.raises(PayerNotFound) as caught:
        LocalPayerStore().get_payer("nope")
    assert set(caught.value.known) == PAYER_IDS


def test_every_committed_id_resolves(payers):
    store = LocalPayerStore()
    for payer in payers:
        assert store.get_payer(payer.payer_id) == payer


# --------------------------------------------------------------------------
# The half of D127's inversion that does not travel
# --------------------------------------------------------------------------


def test_a_missing_directory_raises_rather_than_serving_nobody(tmp_path):
    """D31 unmodified, on the fifth port.

    Reporting a missing corpus as *there is nobody to send to* is the well-formed
    empty answer every downstream check would agree with, and it is exactly what
    `LocalKnowledgeStore` refuses for the knowledge table. The session and outbox
    roots answer `[]` because the **system writes them**; this one is shipped.
    """
    with pytest.raises(KeyError, match="broken checkout"):
        LocalPayerStore(tmp_path).list_payers()


def test_an_empty_directory_raises(tmp_path):
    (tmp_path / DIRECTORY_FILE).write_text('{"payers": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="carries no payers"):
        LocalPayerStore(tmp_path).list_payers()


def test_a_duplicate_id_raises_at_load(tmp_path):
    """An id served by two records is a recipient chosen by file order."""
    (tmp_path / DIRECTORY_FILE).write_text(
        json.dumps({"payers": [_record(), _record(display_name="Simulated Y")]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="twice"):
        LocalPayerStore(tmp_path).list_payers()


# --------------------------------------------------------------------------
# Every record declares itself, and could not be delivered
# --------------------------------------------------------------------------


def test_the_committed_directory_is_the_pinned_size(payers, raw):
    assert len(payers) == PAYER_COUNT
    assert len(raw["payers"]) == PAYER_COUNT
    assert {p.payer_id for p in payers} == PAYER_IDS


def test_every_record_carries_exactly_these_fields(payers, raw):
    assert set(SimulatedPayer.model_fields) == PAYER_FIELDS
    for record in raw["payers"]:
        assert set(record) == PAYER_FIELDS, (
            f"{record.get('payer_id')} declares {sorted(set(record))}; a payer "
            "record is a contact, and a field this set does not name is one "
            "nobody decided to add (D51's shape)"
        )


def test_every_record_declares_itself_simulated(payers):
    """The declaration is what keeps this file from being a claim about a real
    organisation's prior-authorization mailbox, and no committed artifact in this
    repository states one (D118's rule, D128's on ICD-10 grammar)."""
    assert payers, "the directory is empty; every claim below would be vacuous"
    for payer in payers:
        assert payer.simulated is True
        assert payer.note.split(), f"{payer.payer_id} carries no note"


def test_a_record_that_does_not_declare_itself_simulated_is_refused():
    with pytest.raises(ValidationError, match="does not declare itself simulated"):
        SimulatedPayer(**_record(simulated=False))


def test_every_address_is_under_the_reserved_invalid_tld(payers):
    """RFC 2606's reserved TLD, so a packet that escaped could not be delivered
    anywhere — `form.MESSAGE_ID_DOMAIN`'s argument on the other header."""
    for payer in payers:
        assert payer.address.endswith(".invalid"), payer.address
        assert payer.recipient.endswith(f"<{payer.address}>")


@pytest.mark.parametrize(
    "address",
    ["prior-auth@payer.com", "prior-auth@payer.invalid.com", "prior-auth@payer"],
)
def test_a_deliverable_address_is_refused(address):
    """The check that makes the one above more than a description of today's file."""
    with pytest.raises(ValidationError, match="reserved .invalid TLD"):
        SimulatedPayer(**_record(address=address))


def test_the_recipient_header_is_derived_from_the_record():
    """One source for the `To:` header. `T-103` held a constant here because no
    committed artifact named a recipient; `T-105` gave the repository one, so the
    *source* of the string moved and not its shape (D131, D134)."""
    payer = SimulatedPayer(**_record())
    assert payer.recipient == "Simulated X <prior-auth@sim-x.invalid>"


# --------------------------------------------------------------------------
# It is not a hashed corpus, and the two corpora stayed two
# --------------------------------------------------------------------------


def test_the_directory_says_it_is_synthesized(raw):
    """A synthesized artifact with no stated provenance is one a later reader
    takes for fetched. The file's own note is where that is said, because there is
    no manifest record to say it in."""
    note = raw.get("note", "")
    assert "SYNTHESIZED" in note, "the directory no longer declares its provenance"
    assert "verify_sources" in note, (
        "the note no longer says why it is outside the fetched-corpus gate; a "
        "reader finding it absent from every manifest has nothing else to go on"
    )


def test_no_payer_id_appears_in_either_hashed_manifest(payers):
    """The check that the two corpora did not quietly become one (D118, D134).

    *Nine policy documents* and *five FDA labels* are counts
    `tests/test_docs_consistency.py` re-derives from these two files. A payer
    record entering either is a contact able to raise a claim about what this
    system adjudicates against.
    """
    manifests = (
        REPO_ROOT / "data" / "policies" / "source" / "sources.json",
        REPO_ROOT / "data" / "knowledge" / "sources.json",
    )
    for path in manifests:
        assert path.is_file(), f"{path} moved; re-read this test"
        text = path.read_text(encoding="utf-8")
        for payer in payers:
            assert payer.payer_id not in text, (
                f"{payer.payer_id} appears in {path.name}; the payer directory is "
                "synthesized and belongs in no hashed manifest (D134)"
            )
        assert "payers.json" not in text


def test_verify_sources_does_not_reach_the_payer_directory():
    """Stated as a fact about the script rather than about its output.

    `--offline` passing today says nothing: the gate would pass identically with
    a payer record in a manifest it could not fetch, right up to the first
    `--fetch`. What is asserted is that nothing in the fetched-corpus gate names
    this root.
    """
    source = (REPO_ROOT / "scripts" / "verify_sources.py").read_text(encoding="utf-8")
    assert "payers" not in source, (
        "verify_sources.py names the payer directory; that gate compares "
        "committed bytes to a public re-download, and this file has no upstream"
    )


# --------------------------------------------------------------------------
# Where it may be read from
# --------------------------------------------------------------------------


def test_only_the_store_names_the_payer_root():
    """Article VI's storage rule, on the fifth corpus (D25, REQ-41).

    Every path is named in a store adapter, and the CLI constructs the store —
    it does not name the directory. A literal anywhere else is the second adapter
    nobody declared.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent.name == "stores":
            continue
        source = path.read_text(encoding="utf-8")
        if "data/payers" in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"{offenders} names the payer root; it reaches the system through the "
        "port, constructed in cli.py (REQ-41)"
    )


def test_the_payer_store_is_constructed_only_in_the_cli():
    """REQ-41 over the fifth port, the behavioural half."""
    cli_source = (PACKAGE / "cli.py").read_text(encoding="utf-8")
    assert "LocalPayerStore(" in cli_source
    for module in PACKAGE.rglob("*.py"):
        if module.name == "cli.py" or "stores" in module.parts:
            continue
        assert "LocalPayerStore" not in module.read_text(encoding="utf-8"), (
            f"{module} constructs a payer store; the CLI is the one place a "
            "store is constructed (REQ-41)"
        )
