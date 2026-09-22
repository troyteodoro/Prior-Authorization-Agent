"""T-97 — the knowledge plane's port (D119).

`data/knowledge/` is neither of the other two corpora: the policy plane holds
what a payer covers, the patient plane holds one person's chart, and this holds
what a drug is known to do. T-96 gave it its own manifest for that reason
(D118); this file holds the port to its own rule.

**Every malformed shape raises, and none of them defaults.** That is the whole
subject here. A store that answered `[]` for a missing table would report *no
drug on this chart is known to cause anything*, on every chart, and every check
downstream would agree with it — D31's failure, on a third corpus. Each test
below breaks one thing in a copy of the committed corpus and requires the port to
say so.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pa_agent.contracts import Document
from pa_agent.stores.knowledge import (
    COMPARATORS,
    KnowledgeStore,
    LocalKnowledgeStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = REPO_ROOT / "data" / "knowledge"
PACKAGE = REPO_ROOT / "pa_agent"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A writable copy of the committed knowledge corpus."""
    destination = tmp_path / "knowledge"
    shutil.copytree(KNOWLEDGE, destination)
    return destination


def rewrite(root: Path, name: str, mutate) -> None:
    path = root / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# What it serves
# --------------------------------------------------------------------------


def test_the_adapter_satisfies_the_port():
    assert isinstance(LocalKnowledgeStore(), KnowledgeStore)


def test_every_committed_row_loads_with_all_four_of_its_claims():
    rows = LocalKnowledgeStore().get_medication_effect_rows()
    assert len(rows) == 5
    for row in rows:
        assert row.ingredient.system == RXNORM and row.ingredient.code.isdigit()
        assert row.ingredient_source.origin
        assert row.effect.quote and row.effect.document_id.startswith("spl_")
        assert row.icd10_code and row.icd10_title and row.icd10_source.origin
        assert row.signal.comparator in COMPARATORS and row.signal.constant_name
        # REQ-62's rule, restated by the contract: a row with no reachable code
        # declares why rather than being silently empty.
        assert row.already_coded or row.unsourced_reason


def test_every_row_has_a_pinned_expansion_that_contains_its_own_ingredient():
    store = LocalKnowledgeStore()
    for row in store.get_medication_effect_rows():
        products = store.get_ingredient_products(row.ingredient.code)
        assert products.system == RXNORM
        assert products.admits(row.ingredient.code, RXNORM)


def test_a_documents_hash_is_re_verified_every_time_it_is_served():
    store = LocalKnowledgeStore()
    row = store.get_medication_effect_rows()[0]
    document = store.get_document(row.effect.document_id)
    assert isinstance(document, Document)
    assert document.text[row.effect.char_start : row.effect.char_end] == row.effect.quote


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------


def test_a_missing_table_raises_rather_than_serving_no_rows(root: Path):
    (root / "medication_effects.json").unlink()
    with pytest.raises(KeyError, match="broken checkout"):
        LocalKnowledgeStore(root).get_medication_effect_rows()


def test_an_empty_table_raises(root: Path):
    rewrite(root, "medication_effects.json", lambda p: p.update(rows=[]))
    with pytest.raises(ValueError, match="no rows"):
        LocalKnowledgeStore(root).get_medication_effect_rows()


def test_a_row_citing_an_uncovered_document_raises_naming_the_manifest(root: Path):
    def mutate(payload):
        payload["rows"][0]["effect"]["document_id"] = "spl_something_else"

    rewrite(root, "medication_effects.json", mutate)
    with pytest.raises(ValueError, match="does not cover"):
        LocalKnowledgeStore(root).get_medication_effect_rows()


def test_a_comparator_outside_the_closed_vocabulary_raises_naming_the_row(root: Path):
    """The port's guard is redundant with `EffectSignal`'s closed `Literal`, and
    what earns it is the message.

    Measured by mutation: deleting the guard leaves the row unloadable anyway,
    because pydantic refuses the field — so a test matching on `"comparator"`
    passes either way and the guard is dead weight it cannot see. What pydantic
    cannot say is **which row of which file** is wrong, and that is the whole
    value of failing at load. So the assertion is on the row id.
    """
    def mutate(payload):
        payload["rows"][0]["signal"]["comparator"] = "approximately"

    row_id = json.loads(
        (root / "medication_effects.json").read_text(encoding="utf-8")
    )["rows"][0]["row_id"]
    rewrite(root, "medication_effects.json", mutate)
    with pytest.raises(ValueError) as caught:
        LocalKnowledgeStore(root).get_medication_effect_rows()
    assert row_id in str(caught.value) and "closed vocabulary" in str(caught.value), (
        "the port's comparator guard must name the row it refused; pydantic names "
        "the field and the file is what a reader needs"
    )


def test_an_empty_already_coded_with_no_reason_fails_to_load(root: Path):
    def mutate(payload):
        row = next(r for r in payload["rows"] if r["already_coded"])
        row["already_coded"] = []

    rewrite(root, "medication_effects.json", mutate)
    with pytest.raises(ValueError, match="unsourced_reason"):
        LocalKnowledgeStore(root).get_medication_effect_rows()


def test_an_unknown_ingredient_raises_listing_the_ones_it_has():
    with pytest.raises(KeyError) as caught:
        LocalKnowledgeStore().get_ingredient_products("0")
    assert "8640" in str(caught.value)


def test_an_expansion_missing_its_own_ingredient_raises(root: Path):
    def mutate(payload):
        record = payload["ingredients"][0]
        record["entries"] = [
            e for e in record["entries"] if e["code"] != record["ingredient_rxcui"]
        ]

    rewrite(root, "rxnorm_ingredient_products.json", mutate)
    with pytest.raises(ValueError, match="does not contain the ingredient"):
        LocalKnowledgeStore(root).get_ingredient_products("8640")


def test_an_expansion_mixing_code_systems_raises(root: Path):
    def mutate(payload):
        payload["ingredients"][0]["entries"][1]["system"] = "http://snomed.info/sct"

    rewrite(root, "rxnorm_ingredient_products.json", mutate)
    with pytest.raises(ValueError, match="One set is one vocabulary"):
        LocalKnowledgeStore(root).get_ingredient_products("8640")


def test_an_empty_expansion_raises(root: Path):
    def mutate(payload):
        payload["ingredients"][0]["entries"] = []

    rewrite(root, "rxnorm_ingredient_products.json", mutate)
    with pytest.raises(ValueError, match="empty expansion"):
        LocalKnowledgeStore(root).get_ingredient_products("8640")


def test_an_expansion_with_no_declared_system_raises(root: Path):
    rewrite(root, "rxnorm_ingredient_products.json", lambda p: p.pop("system"))
    with pytest.raises(ValueError, match="declares no `system`"):
        LocalKnowledgeStore(root).get_ingredient_products("8640")


def test_an_unknown_document_raises_rather_than_reading_whatever_is_on_disk():
    with pytest.raises(KeyError, match="no knowledge document"):
        LocalKnowledgeStore().get_document("spl_not_in_the_manifest")


def test_a_document_whose_text_no_longer_hashes_to_the_manifest_raises(root: Path):
    document_id = LocalKnowledgeStore().get_medication_effect_rows()[0].effect.document_id
    manifest = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    record = next(d for d in manifest["documents"] if d["document_id"] == document_id)
    path = root / "source" / record["filename"]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hashes to"):
        LocalKnowledgeStore(root).get_document(document_id)


def test_a_recorded_document_absent_from_disk_raises(root: Path):
    document_id = LocalKnowledgeStore().get_medication_effect_rows()[0].effect.document_id
    manifest = json.loads((root / "sources.json").read_text(encoding="utf-8"))
    record = next(d for d in manifest["documents"] if d["document_id"] == document_id)
    (root / "source" / record["filename"]).unlink()
    with pytest.raises(KeyError, match="absent from"):
        LocalKnowledgeStore(root).get_document(document_id)


# --------------------------------------------------------------------------
# Where it may be read from
# --------------------------------------------------------------------------


def test_only_the_store_names_the_knowledge_corpus_location():
    """Article VI's storage rule, on the third corpus (D25, REQ-41).

    `history.py` receives its facts; a path literal in it would be a second
    adapter nobody declared, and `tests/test_planes.py` scans for the general
    case. This names the specific one, so the failure message says which corpus.
    """
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.parent.name == "stores":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "data/knowledge" in node.value or "medication_effects" in node.value:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, (
        f"{offenders} names the knowledge corpus's location; it reaches the "
        "system through the port, constructed in cli.py (REQ-41, D119)"
    )


def test_the_expansion_is_pinned_and_says_so():
    """It is a compilation adopted deliberately, never a refresh (D73's shape)."""
    payload = json.loads(
        (KNOWLEDGE / "rxnorm_ingredient_products.json").read_text(encoding="utf-8")
    )
    expansion = payload["expansion"]
    assert expansion["source"].startswith("RxNav")
    assert "related.json" in expansion["query"]
    assert expansion["retrieved_at"]
    assert "not regenerated by any gate" in expansion["note"]
    assert payload["status"] == "VERIFIED"
    assert {r["ingredient_rxcui"] for r in payload["ingredients"]} == {
        row["ingredient"]["rxcui"]
        for row in json.loads(
            (KNOWLEDGE / "medication_effects.json").read_text(encoding="utf-8")
        )["rows"]
    }, "the expansion covers exactly the ingredients the table declares"
