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
    ("c5", "documentation_rate"),
]

# The rate vocabulary the tree speaks, decoded here so T-37's seven-month case can
# be evaluated before T-16 exists to evaluate it properly. This is deliberately
# not the c5 predicate — it is the least machinery that can ask whether the
# constant in the file means what D24 says it means.
RATE_MONTHS_REQUIRED = {
    "every_month_of_run": lambda run_months: run_months,
}

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


def _question_numbers(block: str) -> set[int]:
    return {int(n) for n in re.findall(r"^(\d+)\.\s", block, re.MULTILINE)}


def _question_statuses(section: str) -> tuple[set[int], set[int]]:
    """(open, resolved) question numbers, read from the subsection each sits
    under — the stated status D34 chose. Nothing here reads `~~` markup or
    closure prose, which is what T-39 removed: the old parser returned every
    numbered question regardless of status, so a provisional constant citing a
    resolved question passed.

    Raises on a section whose structure cannot be read honestly: a missing or
    unexpected subsection, a number under both headings, or a number floating
    outside either — because an unreadable section returning an empty or
    too-full set would fail some other test for the wrong reason, or pass all
    of them for no reason.
    """
    headings = list(re.finditer(r"^### (.+?)\s*$", section, re.MULTILINE))
    names = [h.group(1) for h in headings]
    assert names == ["Still open", "Resolved"], (
        f"§9's subsections are {names}; D34 requires exactly "
        "['Still open', 'Resolved']"
    )
    preamble = section[: headings[0].start()]
    assert not _question_numbers(preamble), (
        f"questions {sorted(_question_numbers(preamble))} sit above the first "
        "subsection and so state no status"
    )
    open_qs = _question_numbers(section[headings[0].end() : headings[1].start()])
    resolved_qs = _question_numbers(section[headings[1].end() :])
    both = open_qs & resolved_qs
    assert not both, f"questions {sorted(both)} are listed as both open and resolved"
    return open_qs, resolved_qs


def _open_questions() -> set[int]:
    """Numbers under §9's `Still open` subsection, and only there.

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
    open_qs, _ = _question_statuses(section)
    return open_qs


#: Provisional constants the tree is expected to carry. T-33 closed the last
#: one (question 5, D51), so this is zero. It is pinned rather than dropped
#: because the assertion it replaces — "at least one provisional constant
#: exists" — was what kept this test from silently becoming vacuous. A new
#: provisional constant is now a visible diff against a stated number, and the
#: per-constant rules below still apply to it.
EXPECTED_PROVISIONAL_CONSTANTS = 0


def test_the_count_of_provisional_constants_is_the_expected_one(tree):
    """D51: closing the last open question is a deliberate event, not a quiet
    one. Raising this number means a task is consuming a value nobody has
    decided; lowering it means a question closed and §9 should say so."""
    provisional = [n for n, b in _all_constants(tree) if b.get("provisional")]
    assert len(provisional) == EXPECTED_PROVISIONAL_CONSTANTS, (
        f"tree carries {len(provisional)} provisional constant(s) {provisional}, "
        f"expected {EXPECTED_PROVISIONAL_CONSTANTS}. Update this constant in the "
        "same commit that opens or closes the question, so the change is reviewed."
    )


def test_provisional_constants_name_an_open_question_that_exists(tree):
    """Vacuous while the tree carries none, and kept for the next one. The
    guard that made it non-vacuous now lives in the test above (D51)."""
    open_questions = _open_questions()
    provisional = [(n, b) for n, b in _all_constants(tree) if b.get("provisional")]
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
# The status parser itself, on synthetic sections (T-39, D34)
#
# The branch that matters — a resolved question being cited — has no live
# exemplar while the spec is healthy, so like D27's scorer self-checks these
# exercise it synthetically. Without them the code deciding whether a stale
# provisional flag passes would sit unrun until the moment it decides.
# --------------------------------------------------------------------------

SYNTHETIC_SECTION = """
Preamble prose, unnumbered.

### Still open

4. **An open question.** Awaits T-13.
5. **Another open question.** Awaits T-33.

### Resolved

1. ~~A resolved question?~~ **Answered.** Closed by T-02.
6. ~~Another?~~ **Answered.** Closed by T-37, see D24.
"""


def test_a_resolved_question_is_not_in_the_open_set():
    """The T-39 defect, pinned: the old parser returned {1, 4, 5, 6} here."""
    open_qs, resolved_qs = _question_statuses(SYNTHETIC_SECTION)
    assert open_qs == {4, 5}
    assert resolved_qs == {1, 6}


def test_a_section_missing_a_subsection_fails_loudly():
    """No heading, no answer. An empty set would vacuously fail the wrong test;
    a too-full set would pass everything for no reason."""
    with pytest.raises(AssertionError, match="subsections"):
        _question_statuses("### Still open\n\n4. **Only open listed.**\n")
    with pytest.raises(AssertionError, match="subsections"):
        _question_statuses("### Resolved\n\n1. ~~Only resolved listed.~~\n")
    with pytest.raises(AssertionError, match="subsections"):
        _question_statuses("4. **No subsections at all.**\n")


def test_a_question_under_both_headings_fails():
    with pytest.raises(AssertionError, match="both open and resolved"):
        _question_statuses(
            "### Still open\n\n4. **A question.**\n\n"
            "### Resolved\n\n4. **The same question.**\n"
        )


def test_a_question_outside_both_subsections_fails():
    """A numbered question above the first heading states no status, and a
    parser that silently dropped it would re-create the ghost-question gap."""
    with pytest.raises(AssertionError, match="state no status"):
        _question_statuses(
            "3. **A floating question.**\n\n"
            "### Still open\n\n4. **A question.**\n\n"
            "### Resolved\n\n1. ~~Done.~~\n"
        )


def test_the_specs_own_section_parses_and_states_a_status_for_every_question():
    """On the real spec: the structure D34 requires still holds.

    The "Still open is non-empty" half was retired by T-33, which closed the
    last question (D51). Both subsections must still exist — a missing heading
    is supposed to fail — and `_question_statuses` still refuses a number under
    both or under neither. `Resolved` stays asserted non-empty: questions have
    been closed, and an empty Resolved list would mean the parser stopped
    reading them."""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    match = re.search(r"^##\s+\d+\.\s+Open questions\s*$", spec, re.MULTILINE)
    section = spec[match.end() :]
    nxt = re.search(r"^## ", section, re.MULTILINE)
    if nxt:
        section = section[: nxt.start()]
    open_qs, resolved_qs = _question_statuses(section)
    assert resolved_qs, "the Resolved subsection lists nothing"
    assert not open_qs & resolved_qs, "a question is listed as both"


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
# c5 is a rate, and it is c4's rate (T-37, D24)
# --------------------------------------------------------------------------


def test_c4_and_c5_declare_the_same_documentation_rate(criteria):
    """One sentence, one shape.

    A53028[6339:6503] says `monthly` once and then lists three things. If c4 and
    c5 ever disagree about the rate again, one of them has stopped reading the
    sentence it cites.
    """
    c4 = criteria["c4"]["constants"]["documentation_rate"]
    c5 = criteria["c5"]["constants"]["documentation_rate"]
    assert c4["value"] == c5["value"] == "every_month_of_run"
    assert c4["source"] == c5["source"], (
        "c4 and c5 quantify from the same sentence, so they cite the same span"
    )


def test_c5_declares_no_event_count(criteria):
    """The shape D24 removed, kept out.

    Re-adding a count is the regression, because the count was `MET` on runs the
    source does not cover and nothing else in the tree would notice.
    """
    constants = criteria["c5"]["constants"]
    assert "c5_min_documented_events" not in constants, (
        "the count is back. D24 replaced it with a rate; see its reversal condition"
    )
    counts = [
        name
        for name, body in constants.items()
        if body.get("type") in ("integer", "number") and not isinstance(body["value"], bool)
    ]
    assert not counts, f"c5 declares a numeric threshold again: {counts}"


def test_c5s_rate_is_sourced_and_no_longer_provisional(criteria):
    """T-37's exit condition, on the branch it took."""
    rate = criteria["c5"]["constants"]["documentation_rate"]
    assert not rate.get("provisional"), "open question 6 is closed by D24"
    assert "open_question" not in rate
    assert rate.get("source"), "a rate is a policy claim and carries a span like any other"


def test_a_seven_month_run_documented_in_four_months_is_not_met(criteria):
    """The case T-37 exists to settle.

    Under the old count of 4 this run was `MET` — four qualifying events cleared a
    floor of four, while three months of the run held no diet or activity
    documentation at all. Under the rate the same run is `NOT_MET`, which is the
    conservative direction and the one the source actually states.

    `NOT_MET` and not `INSUFFICIENT_EVIDENCE`: c3 identified a run, so the months
    were looked at and found undocumented. That is evidence of absence, which is
    Article IV's distinction and D13's.
    """
    run_months = 7
    months_documenting_both = 4

    rate = criteria["c5"]["constants"]["documentation_rate"]["value"]
    assert rate in RATE_MONTHS_REQUIRED, f"unknown rate vocabulary {rate!r}"
    required = RATE_MONTHS_REQUIRED[rate](run_months)

    assert required == run_months, "every_month_of_run means every month of the run"
    assert months_documenting_both < required, (
        f"a {run_months}-month run documented in {months_documenting_both} months "
        f"needs {required} and would resolve MET. The count shape is back."
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


# --------------------------------------------------------------------------
# T-38 — three procedure sets, spanned, disjoint, and readable from the tree
# alone (D26, D28, D29, D30)
# --------------------------------------------------------------------------

SET_NAMES = ["nationally_covered", "nationally_non_covered", "contractor_determined"]

# Cases E2/E3 want sc1 and sc2 answered in milliseconds with zero model calls,
# and E12 pins the BMI boundary; none of that touches this file. The foreign
# code below is an E/M office visit — real, common, and governed by no
# bariatric policy, which is exactly REQ-1's case.
FOREIGN_CODE = "99213"


@pytest.fixture(scope="module")
def procedure_sets(tree) -> dict:
    assert "procedure_sets" in tree, "T-38's sets are missing from the tree"
    return tree["procedure_sets"]


def _entries(procedure_sets):
    for set_name in SET_NAMES:
        for entry in procedure_sets[set_name]:
            yield set_name, entry


def _identity_codes(procedure_sets, set_name: str) -> list[str]:
    return [
        binding["code"]
        for entry in procedure_sets[set_name]
        for binding in entry.get("codes", [])
    ]


def _claim_spans(entry):
    """Every span inside an entry's coverage claim, nested quotes included."""
    claim = entry["coverage_claim"]
    yield "coverage_claim", claim
    for key in ("scoping_quote", "corroborating_quote"):
        if key in claim:
            yield key, claim[key]
    if "no_code_reason" in entry:
        yield "no_code_reason", entry["no_code_reason"]


def test_the_tree_carries_the_three_sets(procedure_sets):
    for set_name in SET_NAMES:
        assert set_name in procedure_sets, f"{set_name} is missing"
        assert procedure_sets[set_name], f"{set_name} is empty"


def test_the_non_covered_set_records_all_six_ncd_procedures(procedure_sets):
    """NCD 100.1 §C names six. A shorter list under-records the source; a longer
    one added a denial nobody wrote."""
    assert len(procedure_sets["nationally_non_covered"]) == 6


def test_every_coverage_claim_slices_back(procedure_sets, source_texts):
    for set_name, entry in _entries(procedure_sets):
        for label, span in _claim_spans(entry):
            doc_id = span["document_id"]
            assert doc_id in source_texts, (
                f"{set_name}/{entry['procedure']}: {label} cites unknown {doc_id}"
            )
            sliced = source_texts[doc_id][span["char_start"] : span["char_end"]]
            assert sliced == span["quote"], (
                f"{set_name}/{entry['procedure']}: {label} "
                f"{doc_id}[{span['char_start']}:{span['char_end']}] does not slice "
                "back to its quote"
            )


def test_no_coverage_claim_cites_the_transmittal(procedure_sets):
    """D29's scope rule. r931cp predates the 2012 LSG delegation; it binds names
    to codes and says nothing current about coverage. A coverage claim spanned
    into it would be D22 rebuilt with a citation."""
    for set_name, entry in _entries(procedure_sets):
        for label, span in _claim_spans(entry):
            assert span["document_id"] != "r931cp", (
                f"{set_name}/{entry['procedure']}: {label} cites r931cp, which is "
                "citable for code bindings only (D29)"
            )


def test_every_non_covered_claim_falls_inside_the_section_c_list(
    procedure_sets, source_texts
):
    """The assertion that separates a denial from a delegation (D22, D28). §D's
    'may determine coverage' paragraph starts 78 characters after §C's list
    ends and slices back just as cleanly."""
    for entry in procedure_sets["nationally_non_covered"]:
        claim = entry["coverage_claim"]
        scope = claim["scoping_quote"]
        assert "non-covered for all Medicare beneficiaries" in scope["quote"]
        assert claim["document_id"] == scope["document_id"]

        text = source_texts[claim["document_id"]]
        list_start = scope["char_end"]
        following = re.search(r"\n\n[A-Z]\.\s", text[list_start:])
        assert following is not None
        list_end = list_start + following.start()
        assert list_start <= claim["char_start"] < claim["char_end"] <= list_end, (
            f"{entry['procedure']}: bullet falls outside the non-covered list"
        )


def test_every_identity_binding_slices_back_naming_code_and_procedure(
    procedure_sets, source_texts
):
    """T-38's exit as rewritten by D30: the binding quote names the code, and
    the entry's procedure is what the quote is about. After T-40 there is no
    such thing as an unsourced binding in this tree."""
    for set_name, entry in _entries(procedure_sets):
        for binding in entry.get("codes", []):
            where = f"{set_name}/{entry['procedure']}/{binding['code']}"
            assert binding["identity"] is True, f"{where}: codes[] holds identities"
            assert binding["in_corpus"] is True, (
                f"{where}: an unsourced binding in the tree. D29 landed r931cp so "
                "no binding would rest on recall; either span it or it does not "
                "belong here."
            )
            text = source_texts[binding["document_id"]]
            sliced = text[binding["char_start"] : binding["char_end"]]
            assert sliced == binding["quote"], f"{where}: span does not slice back"
            assert text.count(binding["quote"]) == 1, (
                f"{where}: binding quote is not unique, offsets not pinned (D17)"
            )
            assert binding["code"] in binding["quote"], (
                f"{where}: the quote does not name the code"
            )


def test_facility_lists_are_transcriptions_not_identities(procedure_sets, source_texts):
    """D30's finding: A53028's facility lists overlap across procedures whose
    coverage differs (0D160ZB in two lists; 0DV64CZ and 0DB64Z3 inside the lap
    Roux-en-Y list while the article assigns them to LSG). A lookup keyed on
    them answers two ways, so they are recorded and excluded."""
    seen_any = False
    for set_name, entry in _entries(procedure_sets):
        for flist in entry.get("facility_code_lists", []):
            seen_any = True
            where = f"{set_name}/{entry['procedure']}/{flist['label']}"
            assert flist["identity"] is False, (
                f"{where}: a facility list promoted to an identity is D26's "
                "collapse rebuilt out of billing data (D30)"
            )
            block = source_texts[flist["document_id"]][
                flist["char_start"] : flist["char_end"]
            ]
            for code in flist["codes"]:
                assert code in block, (
                    f"{where}: transcribed code {code} is not in the spanned block"
                )
    assert seen_any, "the covered set records A53028's facility lists (D30)"


def test_the_sets_are_pairwise_disjoint_over_identity_codes(procedure_sets):
    """No code has two answers. Over identities only: the facility lists overlap
    in the source itself, which is why they are not identities (D30)."""
    for i, first in enumerate(SET_NAMES):
        for second in SET_NAMES[i + 1 :]:
            overlap = set(_identity_codes(procedure_sets, first)) & set(
                _identity_codes(procedure_sets, second)
            )
            assert not overlap, f"{sorted(overlap)} in both {first} and {second}"


def test_no_identity_code_appears_twice_within_a_set(procedure_sets):
    for set_name in SET_NAMES:
        codes = _identity_codes(procedure_sets, set_name)
        assert len(codes) == len(set(codes)), (
            f"{set_name} binds a code under two entries"
        )


def test_43775_is_contractor_determined_and_nothing_else(procedure_sets):
    """D22. The code that reached the spec as non-covered and is not."""
    assert "43775" in _identity_codes(procedure_sets, "contractor_determined")
    assert "43775" not in _identity_codes(procedure_sets, "nationally_covered")
    assert "43775" not in _identity_codes(procedure_sets, "nationally_non_covered")


def test_e3s_code_is_nationally_non_covered(procedure_sets):
    """The code T-35 picked (D28), in the set REQ-2 reads."""
    assert "43842" in _identity_codes(procedure_sets, "nationally_non_covered")


def test_non_covered_and_absent_are_different_lookups(procedure_sets):
    """D26's defect 2, asserted on the artifact alone with no resolver involved.

    'A policy says no' and 'no policy says anything' must come from two
    different lookups, not one absence. 43842 is a membership hit in exactly
    one set; an E/M visit code is a membership hit in none. If the tree could
    only express the covered set, both would read as the same absence — which
    is the collapse REQ-2 used to encode.
    """
    membership = {
        set_name: set(_identity_codes(procedure_sets, set_name))
        for set_name in SET_NAMES
    }
    hits = [name for name, codes in membership.items() if "43842" in codes]
    assert hits == ["nationally_non_covered"]

    foreign_hits = [name for name, codes in membership.items() if FOREIGN_CODE in codes]
    assert foreign_hits == []
    # The two conditions are distinguishable: one is a positive membership
    # answer, the other is absence from every set.
    assert hits != foreign_hits


def test_the_dated_lsg_entry_carries_no_code(procedure_sets):
    """§C's LSG bullet is scoped 'prior to June 27, 2012'. The code's current
    determination is the contractor entry; binding 43775 here too would give it
    two answers and re-run D22."""
    for entry in procedure_sets["nationally_non_covered"]:
        if "sleeve" in entry["procedure"].lower() and "laparoscopic" in entry["procedure"].lower():
            assert entry.get("date_qualifier") == "prior to June 27, 2012"
            assert entry.get("codes", []) == []
            return
    raise AssertionError("§C's laparoscopic sleeve gastrectomy entry is missing")


def test_req2_reads_membership_not_absence(procedure_sets):
    """REQ-2 in the spec reads the non-covered set; `covered_procedures`, the
    field that collapsed three outcomes into one absence, is gone (D26)."""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    assert "covered_procedures" not in spec
    req2 = re.search(r"\*\*REQ-2\*\*(.+?)\n\n", spec, re.S)
    assert req2 is not None, "REQ-2 not found in docs/spec.md"
    assert "non-covered set" in " ".join(req2.group(1).split())


# --------------------------------------------------------------------------
# T-14 — the categorical exclusion is spanned, scoped, and honest (D41)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exclusions(tree) -> list[dict]:
    assert "categorical_exclusions" in tree, "T-14's exclusion is missing"
    return tree["categorical_exclusions"]


def test_the_exclusion_claim_slices_back_and_states_the_denial(
    exclusions, source_texts
):
    """The NCD body never states this exclusion; the transmittal-history
    sentence does, and it is self-scoping — procedures, population and denial
    in one breath (D41)."""
    assert len(exclusions) == 1
    claim = exclusions[0]["claim"]
    text = source_texts[claim["document_id"]]
    assert claim["document_id"] == "ncd_100_1", "the exclusion is national"
    assert text[claim["char_start"] : claim["char_end"]] == claim["quote"]
    assert text.count(claim["quote"]) == 1
    assert "therefore are not covered" in claim["quote"], (
        "the quote must state the denial itself, not merely name a population"
    )
    assert "type 2 diabetes mellitus" in claim["quote"]


def test_the_exclusion_constant_cites_the_ncd_deliberately(
    exclusions, source_texts
):
    """The one numeric constant legitimately sourced to the NCD: the exclusion
    is national and quantified by CMS itself — outside `_all_constants`, so
    the every-number-is-Noridian's gate keeps its rule (D21, D41)."""
    bound = exclusions[0]["constants"]["bmi_upper_bound"]
    assert bound["value"] == 35.0 and bound["comparison"] == "lt"
    source = bound["source"]
    assert source["document_id"] == "ncd_100_1"
    text = source_texts["ncd_100_1"]
    assert text[source["char_start"] : source["char_end"]] == source["quote"]
    assert "less than 35" in source["quote"]


def test_the_exclusion_binding_admits_it_is_unsourced(exclusions):
    binding = exclusions[0]["condition_binding"]
    assert binding["in_corpus"] is False
    assert binding["source_class"] == "external_code_system"
    assert "char_start" not in binding, (
        "the mapping claims a span; nothing in the corpus binds T2DM to a code"
    )


def test_the_exclusion_is_scoped_to_the_covered_set(exclusions):
    """The sentence names RYGBP, LAGB and BPD/DS — the covered set exactly —
    and predates the LSG delegation, so contractor requests skip it (D41)."""
    assert exclusions[0]["procedure_scope"] == "nationally_covered"
