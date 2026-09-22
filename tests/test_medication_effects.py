"""T-96 — every row of the knowledge table names a source that resolves (REQ-62).

The table is the only place a suggested ICD-10 code may come from (spec §11),
so a row is four separate claims and each one needs a source this project can
re-read:

- **the effect** — a span into a hashed FDA label that slices back to its
  quote. A row whose `document_id` names nothing in the knowledge manifest, or
  whose offsets do not slice, fails here rather than loading with an
  unresolvable citation (Article III);
- **the ICD-10 code** — a code, its official title, and the query that
  returned them;
- **the SNOMED codes** a chart already carrying the condition would hold, so
  the suggestion can be suppressed. `already_coded: []` is legal **only** with
  an `unsourced_reason`, because D36 is still blocking and a gap declared in
  the file is a gap someone can read (REQ-58's shape);
- **the structured signal** — a LOINC code and a threshold that names a
  declared constant. Synthea emits no `referenceRange`, so a threshold is a
  decision with a date and never a span (D40, D51), and the constant count is
  pinned so a new one is a visible diff.

`scripts/verify_sources.py --offline` owns the **documents**; this file owns
the **table**. "This document hashes to what was recorded" and "this row cites
it correctly" are different claims with different evidence (D28).

Nothing here loads `pa_agent`: at T-96's close nothing in the engine reads this
file. No model anywhere (Article II).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = REPO_ROOT / "data" / "knowledge"
TABLE_PATH = KNOWLEDGE / "medication_effects.json"
MANIFEST_PATH = KNOWLEDGE / "sources.json"

#: The tri-state in T-97 compares an observation against a threshold, so the
#: comparator vocabulary is closed here rather than being whatever a row typed.
COMPARATORS = {"lt", "lte", "gt", "gte"}

SNOMED_SYSTEM = "http://snomed.info/sct"
LOINC_SYSTEM = "http://loinc.org"

#: D51's shape. Every threshold in this table is a decision, not a span, and
#: the count is pinned so adding one is a diff a reviewer sees.
DECLARED_CONSTANTS = 5


@pytest.fixture(scope="module")
def table() -> dict:
    return json.loads(TABLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict]:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {d["document_id"]: d for d in raw["documents"]}


@pytest.fixture(scope="module")
def texts(manifest) -> dict[str, str]:
    return {
        doc_id: (KNOWLEDGE / "source" / record["filename"]).read_text(encoding="utf-8")
        for doc_id, record in manifest.items()
    }


def rows(table) -> list[dict]:
    return table["rows"]


# --------------------------------------------------------------------------
# The exit condition: a row without a covered source fails
# --------------------------------------------------------------------------


def test_every_effect_cites_a_document_the_manifest_covers(table, manifest):
    """The mutation this exists to catch: a row citing a document nobody hashed.

    A `document_id` that resolves nowhere is a citation the system cannot
    honour and a suggestion nobody can trace back to a label.
    """
    for row in rows(table):
        doc_id = row["effect"]["document_id"]
        assert doc_id in manifest, (
            f"{row['row_id']}: cites {doc_id!r}, which is not in "
            f"{MANIFEST_PATH.relative_to(REPO_ROOT)}"
        )


def test_every_effect_span_slices_back_to_its_quote(table, texts):
    """Article III over a knowledge row, exactly as over a determination claim."""
    for row in rows(table):
        effect = row["effect"]
        text = texts[effect["document_id"]]
        start, end = effect["char_start"], effect["char_end"]
        assert isinstance(start, int) and isinstance(end, int), (
            f"{row['row_id']}: span is not a pair of integers"
        )
        assert 0 <= start < end <= len(text), (
            f"{row['row_id']}: span [{start}:{end}] is out of range for "
            f"{effect['document_id']} ({len(text)} chars)"
        )
        assert text[start:end] == effect["quote"], (
            f"{row['row_id']}: {effect['document_id']}[{start}:{end}] does not "
            "slice back to its quote"
        )


def test_every_effect_quote_occurs_exactly_once_in_its_document(table, texts):
    """A quote appearing twice means the offsets chose one of two, silently.

    It is also how the SPL extractor's nesting bug surfaced: Zestril's
    subsections carry codes of their own, so collecting matches flat emitted
    5.1 through 6.2 twice and doubled this quote (D118).
    """
    for row in rows(table):
        effect = row["effect"]
        occurrences = texts[effect["document_id"]].count(effect["quote"])
        assert occurrences == 1, (
            f"{row['row_id']}: quote occurs {occurrences} times in "
            f"{effect['document_id']}; exactly one is required"
        )


def test_the_tables_source_hashes_are_the_manifests(table, manifest):
    """The header claims which documents it was compiled from. It must be true."""
    declared = {s["document_id"]: s["sha256"] for s in table["sources"]}
    assert set(declared) == set(manifest), (
        "the table's sources do not match the knowledge corpus"
    )
    for doc_id, digest in declared.items():
        assert digest == manifest[doc_id]["sha256"], (
            f"{doc_id}: the table records a hash the manifest does not"
        )


def test_every_cited_document_is_a_drug_label_and_not_a_policy_document(table, manifest):
    """D29's scope rule, on the second corpus.

    These documents say what a drug does and never what a payer covers. A
    coverage claim citing one, or a knowledge row citing an LCD, is the
    merge the two manifests exist to prevent.
    """
    for row in rows(table):
        record = manifest[row["effect"]["document_id"]]
        assert record["authority"] == "fda_labeling"
        assert record["scope"] == "drug_effects_only"


# --------------------------------------------------------------------------
# The other three claims
# --------------------------------------------------------------------------


def test_every_row_names_an_ingredient_with_a_recorded_query(table):
    for row in rows(table):
        ingredient = row["ingredient"]
        assert ingredient["rxcui"].isdigit(), f"{row['row_id']}: rxcui is not numeric"
        assert ingredient["name"]
        source = ingredient["source"]
        assert source["origin"] and source["query"] and source["retrieved_at"]


def test_every_row_carries_an_icd10_code_with_a_title_and_a_query(table):
    for row in rows(table):
        icd10 = row["icd10"]
        assert icd10["system"] == "ICD-10-CM"
        assert re.fullmatch(r"[A-TV-Z][0-9][0-9AB](\.[0-9A-TV-Z]{1,4})?", icd10["code"]), (
            f"{row['row_id']}: {icd10['code']!r} is not ICD-10-CM shaped"
        )
        assert icd10["title"], f"{row['row_id']}: the ICD-10 code carries no title"
        source = icd10["source"]
        assert source["origin"] and source["query"] and source["retrieved_at"]


def test_an_empty_already_coded_declares_why(table):
    """The gap is in the file or it is nowhere.

    Omitting a row whose SNOMED code cannot be sourced hides the problem;
    inventing the code is D36's failure with the safety removed. A row states
    what it could not source, and this is what makes that not optional.
    """
    for row in rows(table):
        reason = row.get("unsourced_reason")
        if row["already_coded"]:
            assert reason is None, (
                f"{row['row_id']}: carries SNOMED codes and an unsourced_reason; "
                "one or the other"
            )
            continue
        assert reason, (
            f"{row['row_id']}: already_coded is empty and no unsourced_reason "
            "says why. An unsourceable claim is declared, never absent."
        )


def test_every_already_coded_entry_declares_snomed_and_its_provenance(table):
    """REQ-59's rule: membership is tested inside a declared system.

    A bare code comparison against `Condition.code` is two vocabularies
    colliding on short numeric strings.
    """
    for row in rows(table):
        for entry in row["already_coded"]:
            assert entry["system"] == SNOMED_SYSTEM, (
                f"{row['row_id']}: {entry['code']} declares "
                f"{entry['system']!r}, not SNOMED"
            )
            assert entry["code"].isdigit() and entry["display"]
            source = entry["source"]
            assert source["origin"] and source["jar_sha256"]
            assert source["modules"], (
                f"{row['row_id']}: {entry['code']} names no module that declares it"
            )


def test_every_signal_is_a_loinc_code_and_a_declared_constant(table):
    for row in rows(table):
        signal = row["signal"]
        assert signal["system"] == LOINC_SYSTEM
        assert re.fullmatch(r"\d{1,5}-\d", signal["code"]), (
            f"{row['row_id']}: {signal['code']!r} is not a LOINC code"
        )
        assert signal["display"] and signal["unit"]
        assert signal["comparator"] in COMPARATORS, (
            f"{row['row_id']}: comparator {signal['comparator']!r} is not one of "
            f"{sorted(COMPARATORS)}"
        )
        assert isinstance(signal["threshold"], (int, float))
        assert signal["constant_name"], f"{row['row_id']}: the threshold has no name"
        assert signal["decided_on"] and signal["rationale"], (
            f"{row['row_id']}: a threshold is a decision and carries a date and a "
            "reason, never a bare number (D40, D51)"
        )


def test_the_declared_constant_count_is_pinned(table):
    """D51's move. A new constant is a visible diff or it is an accident."""
    names = [row["signal"]["constant_name"] for row in rows(table)]
    assert len(set(names)) == len(names), "two rows share a constant name"
    assert len(names) == DECLARED_CONSTANTS == table["declared_constants"], (
        f"{len(names)} thresholds are declared; this file pins "
        f"{DECLARED_CONSTANTS} and the table records "
        f"{table['declared_constants']}"
    )


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------


def test_no_ingredient_effect_pair_is_written_twice(table):
    pairs = [(row["ingredient"]["rxcui"], row["effect"]["display"]) for row in rows(table)]
    assert len(set(pairs)) == len(pairs), "the table repeats an (ingredient, effect) pair"
    ids = [row["row_id"] for row in rows(table)]
    assert len(set(ids)) == len(ids), "the table repeats a row_id"


def test_the_table_declares_what_it_is_and_what_it_leaves_out(table):
    assert table["table_id"] == "medication_effects"
    assert table["task"] == "T-96"
    assert table["decision"] == "D118"
    assert table["status"] == "VERIFIED"
    assert table["compiled_from"], "the table does not say how it was compiled"
    assert table["known_limit"], (
        "the table does not state its limit. Every value set in this repo does, "
        "and a lookup table nobody can see the edge of is worse than a small one"
    )


def test_exactly_one_module_under_pa_agent_reads_the_table():
    """T-96 built the table and nothing that consumed it; T-97 is its reader.

    This assertion's predecessor said *nothing* under `pa_agent/` names the
    table, with the note that the row which made it fail owns it. That row is
    T-97, and what it owes is not the deletion of the check but its successor:
    the table is read **through the port**, in one module, and a second reader
    would be a second adapter nobody declared (REQ-41, D25, D119).

    `history.py` in particular must not appear here. It receives its facts, which
    is what keeps it off both planes and out of `tests/test_planes.py`'s
    both-planes list.
    """
    readers = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / "pa_agent").rglob("*.py")
        if "medication_effects" in path.read_text(encoding="utf-8")
    )
    assert readers == ["pa_agent/stores/knowledge.py"], (
        f"{readers} read the knowledge table; exactly one module does, and it is "
        "the port"
    )


# --------------------------------------------------------------------------
# The extractor the corpus was built with
# --------------------------------------------------------------------------


def _load_verify_sources():
    """Import `scripts/verify_sources.py` by path; it is a script, not a module."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_sources", REPO_ROOT / "scripts" / "verify_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NESTED_SPL = b"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3">
  <component><structuredBody>
    <component><section>
      <code code="43685-7"/>
      <title>5 WARNINGS AND PRECAUTIONS</title>
      <text>parent text</text>
      <component><section>
        <code code="43685-7"/>
        <title>5.1 Nested</title>
        <text>nested text</text>
      </section></component>
    </section></component>
    <component><section>
      <code code="34069-5"/>
      <text>how supplied, which is not a kept section</text>
    </section></component>
  </structuredBody></component>
</document>
"""


def test_a_kept_section_inside_a_kept_section_is_collected_once():
    """The bug this pins moved every offset after it, in silence.

    Zestril's numbered subsections carry `WARNINGS AND PRECAUTIONS` and
    `ADVERSE REACTIONS` codes of their own, while warfarin's carry `SPL
    UNCLASSIFIED SECTION` — so a flat `findall(".//section")` emits Zestril's
    5.1 through 6.2 twice and warfarin's not at all. No behavioural check on
    the committed corpus distinguishes the two collectors except by a quote
    happening to be non-unique, which is why this is a unit test on a
    hand-written document (D65's shape).
    """
    text = _load_verify_sources().extract_spl_text(NESTED_SPL)
    assert text.count("nested text") == 1, (
        "a section nested inside a kept section was collected twice"
    )
    assert text.count("parent text") == 1
    assert "how supplied" not in text, (
        "a section outside SPL_SECTIONS was collected"
    )


def test_the_section_allowlist_is_the_one_the_corpus_was_extracted_with():
    """A changed allowlist re-extracts every label and moves every span.

    Pinned as a literal for the reason the corpus is pinned by hashes: the
    quiet version of this change is a boxed warning silently entering or
    leaving the text a row's offsets point into.
    """
    assert set(_load_verify_sources().SPL_SECTIONS) == {
        "34066-1",  # boxed warning
        "34070-3",  # contraindications
        "34071-1",  # warnings
        "42232-9",  # precautions
        "43685-7",  # warnings and precautions
        "34084-4",  # adverse reactions
    }
