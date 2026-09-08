"""T-08 — the document index round-trips spans and enforces REQ-7 (D37).

The exit condition: a content hash per document, and a round-trip slice
returning the original for 1000 random spans. The random spans are seeded, so
a failure reproduces; the documents are the real corpus served through the
store, not synthetic strings, so the round-trip covers the text spans will
actually point into.
"""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import pytest

from pa_agent.contracts import Document, EvidenceSpan
from pa_agent.index import DocumentConflictError, DocumentIndex
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_MANIFEST = REPO_ROOT / "data" / "policies" / "source" / "sources.json"
INDEX_MODULE = REPO_ROOT / "pa_agent" / "index.py"

SPAN_COUNT = 1000
SEED = 8  # T-08's; recorded so a failing span reproduces


@pytest.fixture(scope="module")
def corpus_ids() -> list[str]:
    manifest = json.loads(SOURCES_MANIFEST.read_text(encoding="utf-8"))
    return [d["document_id"] for d in manifest["documents"]]


@pytest.fixture(scope="module")
def index(corpus_ids) -> DocumentIndex:
    """The real corpus, read through the port (REQ-41) and handed over."""
    store = LocalPolicyStore()
    idx = DocumentIndex()
    for document_id in corpus_ids:
        idx.add(store.get_document(document_id))
    return idx


# --------------------------------------------------------------------------
# Content hash per document
# --------------------------------------------------------------------------


def test_every_document_carries_the_recorded_content_hash(index, corpus_ids):
    """The hash the index holds is T-02's, not one derived from whatever is on
    disk today — `get_document` checks against `sources.json` and `Document`
    re-verifies on construction, so a drifted file cannot reach here."""
    manifest = json.loads(SOURCES_MANIFEST.read_text(encoding="utf-8"))
    recorded = {d["document_id"]: d["sha256"] for d in manifest["documents"]}
    assert index.ids() == sorted(corpus_ids)
    for document_id in corpus_ids:
        assert index.get(document_id).sha256 == recorded[document_id]


# --------------------------------------------------------------------------
# The round trip: 1000 random spans slice back to the original
# --------------------------------------------------------------------------


def test_a_thousand_random_spans_round_trip(index, corpus_ids):
    rng = random.Random(SEED)
    for _ in range(SPAN_COUNT):
        document = index.get(rng.choice(corpus_ids))
        start = rng.randrange(0, len(document.text))
        end = rng.randrange(start + 1, min(start + 2000, len(document.text)) + 1)
        span = EvidenceSpan(
            document_id=document.document_id, char_start=start, char_end=end
        )
        sliced = index.slice(span)
        assert sliced == document.text[start:end]
        assert sliced, "a span admitted by EvidenceSpan sliced to nothing"


# --------------------------------------------------------------------------
# REQ-7 is an exception, not a convention
# --------------------------------------------------------------------------


def test_rebinding_an_id_to_different_content_raises(index):
    victim = index.get("ncd_100_1")
    imposter = Document.from_text("ncd_100_1", victim.text + " ")
    with pytest.raises(DocumentConflictError, match="REQ-7"):
        index.add(imposter)
    assert index.get("ncd_100_1").sha256 == victim.sha256, "the conflict mutated the index"


def test_re_adding_the_identical_document_is_idempotent(index):
    before = len(index)
    index.add(index.get("a53028"))
    assert len(index) == before


def test_an_unknown_document_raises_naming_what_is_held(index):
    with pytest.raises(KeyError, match="ncd_100_1"):
        index.get("no_such_document")


def test_a_span_past_the_end_raises(index):
    document = index.get("r931cp")
    over = EvidenceSpan(
        document_id="r931cp",
        char_start=len(document.text) - 1,
        char_end=len(document.text) + 1,
    )
    with pytest.raises(IndexError, match="past the end"):
        index.slice(over)


def test_a_reversed_span_cannot_be_constructed_at_all():
    """The index never sees one; the contract refuses first (T-09)."""
    with pytest.raises(Exception, match="empty or reversed"):
        EvidenceSpan(document_id="ncd_100_1", char_start=10, char_end=10)


# --------------------------------------------------------------------------
# The module stays on its side of every line
# --------------------------------------------------------------------------


def test_the_index_module_imports_no_store_no_file_api_and_no_model():
    """REQ-41 and Article II, asserted on the module's imports: the index is
    handed documents, it does not fetch them, and nothing model-shaped is
    reachable from it."""
    tree = ast.parse(INDEX_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"__future__", "pa_agent.contracts"}, (
        f"pa_agent/index.py imports {sorted(imported)}; the index takes Document "
        "objects and must not reach a store, the filesystem, or a model (D37)"
    )
