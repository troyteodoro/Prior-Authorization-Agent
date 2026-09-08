"""T-05 — the comorbidity value set is verified against real codes (D36).

Three claims per entry, each checked mechanically:

- the SNOMED code appears as an *active* Condition, display verbatim, in at
  least one of the six committed bundles — a value set entry no patient can
  ever match is a wrong code failing criterion (b) silently, T-05's defect;
- the ICD-10-CM anchor slices back out of the hashed A53028 to a quote naming
  both the code and its condition, and the span falls inside Group 1's list
  rather than merely inside the document — T-38's containment gate, reused,
  because Group 2 (the BMI Z-codes) slices back just as cleanly and means
  something else;
- the SNOMED-to-ICD-10 mapping admits it is unsourced (`in_corpus: false`) —
  the D28 posture, because no corpus document and no credential-free
  re-downloadable source binds the two systems.

`status: "VERIFIED"` is the recorded claim; this file is what keeps it from
being a declaration. No model anywhere (Article II).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VALUESET_PATH = REPO_ROOT / "data" / "policies" / "value_sets" / "obesity_comorbidities.json"
TREE_PATH = REPO_ROOT / "data" / "policies" / "ncd_100_1_jf.json"
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
SOURCES_MANIFEST = SOURCE_DIR / "sources.json"
BUNDLES_DIR = REPO_ROOT / "data" / "patients" / "bundles"
PATIENT_MANIFEST = REPO_ROOT / "data" / "patients" / "manifest.json"

GROUP1_START_MARK = "(543 Codes)"
GROUP2_MARK = "Group 2\n(10 Codes)"


@pytest.fixture(scope="module")
def valueset() -> dict:
    return json.loads(VALUESET_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def a53028() -> str:
    manifest = json.loads(SOURCES_MANIFEST.read_text(encoding="utf-8"))
    doc = next(d for d in manifest["documents"] if d["document_id"] == "a53028")
    return (SOURCE_DIR / doc["filename"]).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def active_conditions() -> set[tuple[str, str, str]]:
    """(system, code, display) of every active Condition across the six
    committed bundles — the population the exit condition names."""
    listed = json.loads(PATIENT_MANIFEST.read_text(encoding="utf-8"))["bundles"]
    found: set[tuple[str, str, str]] = set()
    for record in listed:
        bundle = json.loads(
            (BUNDLES_DIR / record["filename"]).read_text(encoding="utf-8")
        )
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource.get("resourceType") != "Condition":
                continue
            status_codes = {
                c.get("code")
                for c in resource.get("clinicalStatus", {}).get("coding", [])
            }
            if "active" not in status_codes:
                continue
            for coding in resource.get("code", {}).get("coding", []):
                found.add(
                    (coding.get("system"), coding.get("code"), coding.get("display"))
                )
    return found


def test_the_set_parses_and_is_the_one_the_tree_names(valueset):
    tree = json.loads(TREE_PATH.read_text(encoding="utf-8"))
    criterion_b = next(c for c in tree["criteria"] if c["id"] == "b")
    assert valueset["value_set_id"] == criterion_b["constants"]["value_set_id"]["value"]


def test_the_status_is_verified(valueset):
    """The exit condition's word. The rest of this file is what makes it true."""
    assert valueset["status"] == "VERIFIED"


def test_the_set_is_bound_to_the_hashed_corpus(valueset):
    """A span into a document that has since changed cites nothing (D23)."""
    manifest = json.loads(SOURCES_MANIFEST.read_text(encoding="utf-8"))
    recorded = {d["document_id"]: d["sha256"] for d in manifest["documents"]}
    for source in valueset["sources"]:
        assert source["sha256"] == recorded[source["document_id"]], (
            f"{source['document_id']}: the value set was built against a "
            "different version of this document"
        )


def test_the_set_is_not_empty_and_codes_are_unique(valueset):
    entries = valueset["entries"]
    assert entries, "an empty value set fails criterion (b) for every patient"
    codes = [(e["system"], e["code"]) for e in entries]
    assert len(codes) == len(set(codes)), "a code appears twice"


def test_every_code_appears_in_the_population_as_an_active_condition(
    valueset, active_conditions
):
    """T-05's exit: a code no committed bundle carries can never match a
    patient, which is a wrong code failing criterion (b) silently."""
    for entry in valueset["entries"]:
        key = (entry["system"], entry["code"], entry["display"])
        assert key in active_conditions, (
            f"{entry['code']} ({entry['display']}) appears in no committed "
            "bundle as an active condition — system, code and display must all "
            "match verbatim"
        )


def test_every_anchor_slices_back_naming_code_and_condition(valueset, a53028):
    for entry in valueset["entries"]:
        anchor = entry["icd10_anchor"]
        where = f"{entry['code']} -> {anchor['code']}"
        assert anchor["document_id"] == "a53028", (
            f"{where}: Group 1 lives in A53028; another document is another claim"
        )
        sliced = a53028[anchor["char_start"] : anchor["char_end"]]
        assert sliced == anchor["quote"], f"{where}: span does not slice back"
        assert a53028.count(anchor["quote"]) == 1, (
            f"{where}: anchor quote is not unique, offsets not pinned (D17)"
        )
        assert anchor["quote"].startswith(anchor["code"] + "\n"), (
            f"{where}: the quote does not open with the ICD-10 code line"
        )
        description = anchor["quote"].split("\n\n", 1)[1]
        assert description.strip(), f"{where}: the quote names no condition"


def test_every_anchor_falls_inside_group_1(valueset, a53028):
    """Group 2's BMI codes slice back just as cleanly and support a different
    claim. Containment is what separates 'a comorbidity the policy accepts'
    from 'a code that appears somewhere in the article' (T-38's gate, D30)."""
    group1_start = a53028.index(GROUP1_START_MARK)
    group2_start = a53028.index(GROUP2_MARK)
    assert group1_start < group2_start, "the marks moved; re-derive the bounds"
    for entry in valueset["entries"]:
        anchor = entry["icd10_anchor"]
        assert group1_start < anchor["char_start"] < anchor["char_end"] <= group2_start, (
            f"{entry['code']} -> {anchor['code']}: anchor falls outside Group 1"
        )


def test_every_mapping_admits_it_is_unsourced(valueset):
    """The D28 posture. A mapping that claimed a span would let the anchor's
    credibility bleed into a claim this corpus cannot verify."""
    for entry in valueset["entries"]:
        mapping = entry["mapping"]
        assert mapping["in_corpus"] is False
        assert mapping["source_class"] == "external_code_system"
        assert "char_start" not in mapping and "quote" not in mapping, (
            f"{entry['code']}: the mapping claims a span; nothing in the "
            "corpus can back it (D36)"
        )
        assert mapping.get("basis"), (
            f"{entry['code']}: an unsourced mapping without a stated basis is "
            "a bare assertion"
        )
