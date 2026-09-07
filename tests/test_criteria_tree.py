"""T-01 — the criteria tree parses, and every constant is present, typed and traceable.

The exit condition asks for three things: the file parses, every policy-supplied
constant is present and typed, and any provisional value carries
`provisional: true` naming the open question it awaits.

This adds a fourth that D23 argues for and T-02 established the pattern of: a
constant sourced from the policy carries a span, and the span is checked by
slicing the hashed document. A threshold nobody can trace is someone's memory
with a number attached.

No model is involved anywhere here (Article II).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = REPO_ROOT / "data" / "policies" / "ncd_100_1_jf.json"
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
MANIFEST_PATH = SOURCE_DIR / "sources.json"
SPEC_PATH = REPO_ROOT / "docs" / "spec.md"

# The criteria NCD 100.1 decomposes into (D1). Renaming one breaks every
# reference to it, so the set is asserted rather than discovered.
EXPECTED_CRITERIA = ["a", "b", "c1", "c2", "c3", "c4", "c5"]

# Constants the exit condition names explicitly, as (criterion_id, constant_name).
REQUIRED_CONSTANTS = [
    ("a", "bmi_threshold"),
    ("a", "lookback_months"),
    ("b", "min_comorbidity_count"),
    ("c1", "min_events"),
    ("c2", "recency_window_months"),
    ("c3", "min_consecutive_months"),
    ("c4", "documentation_rate"),
    ("c5", "c5_min_documented_events"),
]

TYPE_CHECKS = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "number_absolute": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "integer_months": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "rate": lambda v: isinstance(v, str),
}


@pytest.fixture(scope="module")
def tree() -> dict:
    return json.loads(TREE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def criteria(tree) -> dict:
    return {c["id"]: c for c in tree["criteria"]}


@pytest.fixture(scope="module")
def source_texts() -> dict[str, str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        d["document_id"]: (SOURCE_DIR / d["filename"]).read_text(encoding="utf-8")
        for d in manifest["documents"]
    }


def _constants(criterion: dict):
    for name, body in criterion.get("constants", {}).items():
        yield name, body


def _all_constants(tree: dict):
    """Every constant in the tree, criteria and reconciled facts alike."""
    for criterion in tree["criteria"]:
        for name, body in _constants(criterion):
            yield f"{criterion['id']}.{name}", body
    for fact in tree.get("reconciled_facts", []):
        yield f"{fact['fact']}.discrepancy_tolerance", fact["discrepancy_tolerance"]


# --------------------------------------------------------------------------
# The file parses and holds the criteria it claims to
# --------------------------------------------------------------------------


def test_the_tree_parses_and_declares_a_policy_version(tree):
    assert tree["policy_version_id"], "REQ-4: every determination records this"


def test_every_criterion_is_present_exactly_once(tree, criteria):
    assert list(criteria) == EXPECTED_CRITERIA
    assert len(tree["criteria"]) == len(EXPECTED_CRITERIA), "a criterion id is duplicated"


def test_the_decision_expression_covers_every_criterion_and_nothing_else(tree, criteria):
    """REQ-19 evaluates this expression in Python. A criterion missing from it is
    a criterion that cannot affect the outcome, which is the quiet failure."""
    named = set(re.findall(r"\b(?:a|b|c[1-5])\b", tree["decision_expression"]))
    assert named == set(criteria), (
        f"decision_expression names {sorted(named)}, criteria are {sorted(criteria)}"
    )


def test_the_tree_is_bound_to_the_hashed_corpus(tree):
    """A tree citing offsets into a document that has since changed cites nothing."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    recorded = {d["document_id"]: d["sha256"] for d in manifest["documents"]}
    for source in tree["sources"]:
        assert source["document_id"] in recorded, f"{source['document_id']} is not in the corpus"
        assert source["sha256"] == recorded[source["document_id"]], (
            f"{source['document_id']}: the tree was compiled against a different "
            "version of this document. Every offset in it is suspect."
        )


# --------------------------------------------------------------------------
# Present and typed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("criterion_id,constant", REQUIRED_CONSTANTS)
def test_required_constant_is_present(criteria, criterion_id, constant):
    assert constant in criteria[criterion_id].get("constants", {}), (
        f"{criterion_id} declares no {constant}"
    )


def test_the_discrepancy_tolerance_is_declared_per_reconciled_fact(tree):
    facts = tree.get("reconciled_facts", [])
    assert facts, "REQ-39 requires a tolerance per reconciled fact; none are declared"
    for fact in facts:
        assert "discrepancy_tolerance" in fact, f"{fact['fact']} declares no tolerance"


def test_every_constant_declares_a_type_and_honors_it(tree):
    for name, body in _all_constants(tree):
        declared = body.get("type")
        assert declared in TYPE_CHECKS, f"{name}: unknown or missing type {declared!r}"
        assert "value" in body, f"{name}: no value key"
        value = body["value"]
        if value is None:
            assert body.get("provisional") is True, (
                f"{name}: null value without provisional: true. An unanswered "
                "constant has to say so; a bare null is indistinguishable from a bug."
            )
            continue
        assert TYPE_CHECKS[declared](value), (
            f"{name}: value {value!r} does not satisfy declared type {declared!r}"
        )


# --------------------------------------------------------------------------
# Provisional values name the open question they await
# --------------------------------------------------------------------------


def _open_questions() -> set[int]:
    """Numbers listed under the spec's Open questions heading, and only there.

    Scoped to the section rather than grepped from the whole file: the spec holds
    other numbered lists, and matching one of those would let a provisional
    constant point at a question that does not exist.
    """
    spec = SPEC_PATH.read_text(encoding="utf-8")
    match = re.search(r"^##\s+\d+\.\s+Open questions\s*$", spec, re.MULTILINE)
    assert match, "docs/spec.md has no Open questions section"
    section = spec[match.end() :]
    nxt = re.search(r"^## ", section, re.MULTILINE)
    if nxt:
        section = section[: nxt.start()]
    return {int(n) for n in re.findall(r"^(\d+)\.\s", section, re.MULTILINE)}


def test_provisional_constants_name_an_open_question_that_exists(tree):
    open_questions = _open_questions()
    assert open_questions, "the Open questions section lists nothing"
    provisional = [(n, b) for n, b in _all_constants(tree) if b.get("provisional")]
    assert provisional, (
        "no provisional constants. If every value is now sourced, this test and "
        "D23's third section should be retired deliberately, not by accident."
    )
    for name, body in provisional:
        question = body.get("open_question")
        assert isinstance(question, int), (
            f"{name}: provisional without an open_question. A flagged value that "
            "names nothing is a TODO, and TODOs do not get resolved."
        )
        assert question in open_questions, (
            f"{name}: names open question {question}, which docs/spec.md does not hold"
        )


def test_a_non_provisional_constant_is_never_null(tree):
    for name, body in _all_constants(tree):
        if not body.get("provisional"):
            assert body["value"] is not None, f"{name}: null but not marked provisional"


# --------------------------------------------------------------------------
# Sourced constants trace to the document, by slicing it (D23)
# --------------------------------------------------------------------------


def test_every_sourced_constant_slices_back_to_its_quote(tree, source_texts):
    checked = 0
    for name, body in _all_constants(tree):
        source = body.get("source")
        if source is None:
            continue
        doc_id = source["document_id"]
        assert doc_id in source_texts, f"{name}: cites {doc_id}, not in the corpus"
        text = source_texts[doc_id]
        start, end = source["char_start"], source["char_end"]
        assert isinstance(start, int) and isinstance(end, int), f"{name}: non-integer offsets"
        assert 0 <= start < end <= len(text), f"{name}: span [{start}:{end}] out of range"
        assert text[start:end] == source["quote"], (
            f"{name}: {doc_id}[{start}:{end}] does not slice back to its quote"
        )
        assert text.count(source["quote"]) == 1, (
            f"{name}: quote occurs {text.count(source['quote'])} times in {doc_id}; "
            "a citation has to identify one passage"
        )
        checked += 1
    assert checked, "no sourced constants were checked; the test is vacuous"


def test_a_constant_is_either_sourced_or_provisional(tree):
    """The whole point of D23. A bare number with neither a span nor a flag is a
    number someone remembered."""
    orphans = [
        name
        for name, body in _all_constants(tree)
        if not body.get("source") and not body.get("provisional") and "note" not in body
    ]
    assert not orphans, (
        "constants with no source span, no provisional flag and no note explaining "
        f"why: {orphans}"
    )


def test_the_national_floor_is_cited_where_it_is_claimed(tree, criteria, source_texts):
    floor = criteria["a"]["national_floor"]
    text = source_texts[floor["document_id"]]
    assert text[floor["char_start"] : floor["char_end"]] == floor["quote"]
    assert floor["document_id"] == "ncd_100_1", (
        "the national floor must cite the NCD; A53028 is one MAC's article (D21)"
    )


# --------------------------------------------------------------------------
# The jurisdiction is on the record
# --------------------------------------------------------------------------


def test_the_tree_states_its_jurisdiction(tree):
    """D21: every quantified constant comes from one MAC, and a tree that does not
    say so invites the numbers being described as CMS's."""
    jurisdiction = tree["jurisdiction"]
    assert jurisdiction["authority"] != "national"
    assert jurisdiction["states"], "a MAC tree has to name the states it governs"


def test_quantified_constants_come_from_the_mac_article(tree):
    """NCD 100.1 quantifies nothing (D21). A numeric constant citing the NCD means
    someone read a number into the national text that is not there."""
    for name, body in _all_constants(tree):
        source = body.get("source")
        if source is None or not isinstance(body.get("value"), (int, float)):
            continue
        if isinstance(body["value"], bool):
            continue
        assert source["document_id"] == "a53028", (
            f"{name}: numeric constant sourced to {source['document_id']}. Only the "
            "BMI threshold appears in both documents, and it is cited to A53028 "
            "because that is the text this tree operationalizes."
        )
