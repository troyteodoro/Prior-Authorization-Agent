"""T-35 — E3's procedure code is cited, and the citation means what it claims.

D22 established what this test exists to prevent. 43775 was written into the spec
from memory, and the source says the opposite: NCD 100.1 non-covers stand-alone
laparoscopic sleeve gastrectomy only "prior to June 27, 2012", after which CMS
delegated it to the MACs, and A53028 records this MAC covering it. A confidently
wrong denial, and US-1's acceptance case would have certified it.

So the gate here is not "E3 has a code." It is that the code's *coverage claim*
slices back out of the hashed corpus, and that the passage it slices back to is
inside the list scoped "non-covered for all Medicare beneficiaries" rather than
somewhere else in a document that also contains a delegation paragraph reading
almost identically at a glance.

What this file deliberately does **not** assert is that CPT 43842 denotes open
vertical banded gastroplasty. Neither corpus document binds any non-covered
procedure to a code — `ncd_100_1` carries no procedure codes at all and says so —
so a test claiming to verify that would be asserting someone's recall with a
green check next to it, which is the mechanism that produced the 43775 defect in
the first place. It asserts instead that the artifact says so itself: the
`code_binding` is marked `in_corpus: false` against an open question. See D28,
and T-40 for the gap.

No model is involved anywhere here (Article II).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = REPO_ROOT / "eval" / "cases.json"
SOURCE_DIR = REPO_ROOT / "data" / "policies" / "source"
MANIFEST_PATH = SOURCE_DIR / "sources.json"
SPEC_PATH = REPO_ROOT / "docs" / "spec.md"
PACKAGE_DIR = REPO_ROOT / "pa_agent"

# The code D22 disproved. Named explicitly so that re-introducing it fails loudly
# rather than being caught only by whoever remembers the entry.
DISPROVED_CODE = "43775"


@pytest.fixture(scope="module")
def e3() -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    by_id = {c["case_id"]: c for c in cases}
    assert "E3" in by_id, "E3 is missing from eval/cases.json"
    return by_id["E3"]


@pytest.fixture(scope="module")
def source_texts() -> dict[str, str]:
    """Documents read through the manifest, with the recorded hash verified.

    Same pattern as `tests/test_criteria_tree.py`. Verifying the hash before
    slicing is what makes an offset a citation rather than a coincidence: a
    document that moved underneath a recorded span fails here instead of
    mis-citing quietly (D23, REQ-7).
    """
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    texts: dict[str, str] = {}
    for document in manifest["documents"]:
        raw = (SOURCE_DIR / document["filename"]).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == document["sha256"], (
            f"{document['document_id']} does not hash to the value sources.json "
            f"records; every span into it is citing a document that changed"
        )
        texts[document["document_id"]] = raw.decode("utf-8")
    return texts


def _spans(claim: dict):
    """Every `(label, span)` in a coverage claim, nested quotes included."""
    yield "coverage_claim", claim
    for key in ("scoping_quote", "corroborating_quote"):
        if key in claim:
            yield key, claim[key]


# --------------------------------------------------------------------------
# The case carries a code, and it is not the one the source disproved
# --------------------------------------------------------------------------


def test_e3_carries_a_procedure_code(e3):
    code = e3.get("procedure_code")
    assert code, (
        "E3 has no procedure_code. It landed without one on purpose (D27) "
        "pending T-35; T-35 is what fills it in."
    )
    assert isinstance(code, str)


def test_e3_is_not_the_code_d22_disproved(e3):
    assert e3["procedure_code"] != DISPROVED_CODE, (
        f"E3 is {DISPROVED_CODE}. NCD 100.1 non-covers stand-alone laparoscopic "
        "sleeve gastrectomy only 'prior to June 27, 2012' and delegates it to the "
        "MACs after that; A53028 records this MAC covering it. Labeling it "
        "NOT_COVERED asserts the opposite of the source (D22)."
    )


def test_e3_still_expects_not_covered_without_a_model_call(e3):
    """The label was always right. Only the code was wrong (spec §9, question 3)."""
    assert e3["expect"]["outcome"] == "NOT_COVERED"
    assert e3["expect"]["max_model_calls"] == 0


def test_e3_declares_no_unspecified_reason(e3):
    assert "unspecified_reason" not in e3, (
        "unspecified_reason explains an absent procedure_code. With the code "
        "present it is stale text that will keep naming T-35 after T-35 landed — "
        "the T-39 defect with different spelling (D27)."
    )


# --------------------------------------------------------------------------
# The coverage claim slices back out of the hashed corpus (Article III)
# --------------------------------------------------------------------------


def test_every_span_slices_back_to_its_quote(e3, source_texts):
    for label, span in _spans(e3["coverage_claim"]):
        doc_id = span["document_id"]
        assert doc_id in source_texts, f"{label}: unknown document {doc_id}"
        text = source_texts[doc_id]
        sliced = text[span["char_start"] : span["char_end"]]
        assert sliced == span["quote"], (
            f"{label}: {doc_id}[{span['char_start']}:{span['char_end']}] slices to "
            f"{sliced!r}, not to its recorded quote"
        )


def test_every_quote_occurs_exactly_once_in_its_document(e3, source_texts):
    """A span into a repeated string is one arbitrary occurrence of it (D17)."""
    for label, span in _spans(e3["coverage_claim"]):
        text = source_texts[span["document_id"]]
        assert text.count(span["quote"]) == 1, (
            f"{label}: its quote occurs {text.count(span['quote'])} times in "
            f"{span['document_id']}, so the offsets are not pinned by the text"
        )


def test_the_claim_is_scoped_by_the_national_non_covered_sentence(e3, source_texts):
    scope = e3["coverage_claim"]["scoping_quote"]
    assert "non-covered for all Medicare beneficiaries" in scope["quote"], (
        "The scoping quote is what makes the citation a denial. A procedure "
        "bullet on its own names a procedure and says nothing about coverage."
    )


def test_the_procedure_falls_inside_the_non_covered_list(e3, source_texts):
    """The assertion that separates a denial from a delegation.

    NCD 100.1 §D reads "Medicare Administrative Contractors ... may determine
    coverage", 78 characters after §C's list ends. A span pointing there slices
    back to its quote perfectly well and means the opposite of non-covered. Only
    containment in the list §C opens rules that out.
    """
    claim = e3["coverage_claim"]
    scope = claim["scoping_quote"]
    assert claim["document_id"] == scope["document_id"], (
        "the procedure and the scope that qualifies it must be in one document"
    )

    text = source_texts[claim["document_id"]]
    list_start = scope["char_end"]

    # The list runs until the next lettered section heading, e.g. "D. Other".
    following = re.search(r"\n\n[A-Z]\.\s", text[list_start:])
    assert following is not None, "no section heading follows the non-covered list"
    list_end = list_start + following.start()

    assert list_start <= claim["char_start"] < claim["char_end"] <= list_end, (
        f"{claim['document_id']}[{claim['char_start']}:{claim['char_end']}] falls "
        f"outside the non-covered list at [{list_start}:{list_end}]. It may slice "
        "back to its quote and still be citing a passage that does not deny "
        "coverage — see §D's delegation paragraph (D22)."
    )


def test_the_cited_procedure_carries_no_date_qualifier(e3, source_texts):
    """43775's entry is scoped 'prior to June 27, 2012'. A qualified bullet is a
    conditional denial, and E3 is labeled against an unconditional one."""
    quote = e3["coverage_claim"]["quote"]
    assert not re.search(r"prior to|effective|on and after|\b(19|20)\d{2}\b", quote), (
        f"the cited procedure is date-qualified: {quote!r}. That is a denial with "
        "a boundary, and NCD 100.1 delegates what falls on the other side of it."
    )


# --------------------------------------------------------------------------
# The code binding says it is not sourced, because it is not
# --------------------------------------------------------------------------


def test_the_code_binding_admits_it_is_not_in_the_corpus(e3):
    binding = e3["code_binding"]
    assert binding["in_corpus"] is False, (
        "in_corpus is true, so some document must bind this code to this "
        "procedure — name it and give it a span. Neither corpus document does: "
        "ncd_100_1 carries no procedure codes at all, and A53028 names one "
        "bariatric code, 43775 (D28). If T-40 has landed a third document, this "
        "test moves with it."
    )
    assert binding["source_class"] == "external_code_system"
    assert binding["system"] == "CPT"


def test_the_code_binding_claims_no_span(e3):
    binding = e3["code_binding"]
    for field in ("char_start", "char_end", "document_id", "quote"):
        assert field not in binding, (
            f"code_binding carries {field}. A span here would put an unverifiable "
            "claim in the same shape as a verified one, which is the whole thing "
            "the two classes exist to keep apart (D28)."
        )


def test_the_code_binding_names_a_question_the_spec_states(e3):
    """The flag has to name the thing that would clear it (D23's argument)."""
    question = e3["code_binding"]["open_question"]
    spec = SPEC_PATH.read_text(encoding="utf-8")
    section = spec.split("## 9. Open questions", 1)
    assert len(section) == 2, "spec §9 not found"

    pattern = rf"^{question}\.\s"
    assert re.search(pattern, section[1], re.MULTILINE), (
        f"code_binding names open question {question}, which spec §9 does not "
        "state. A flag citing a question nobody wrote is a flag nothing clears."
    )


# --------------------------------------------------------------------------
# The code belongs to the eval case, not to a predicate
# --------------------------------------------------------------------------


def test_the_code_is_not_a_literal_in_the_package(e3):
    """Same argument as T-34's literal scan, one artifact over.

    A procedure code hardcoded in `pa_agent/` is a coverage rule outside the
    criteria tree, where no diff and no reviewer sees it change (Article VII).
    The codes belong to T-38's procedure sets and to this case.
    """
    code = e3["procedure_code"]
    offenders = []
    for path in PACKAGE_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if re.search(rf'["\']{re.escape(code)}["\']', path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO_ROOT)))

    assert not offenders, (
        f"{code} appears as a literal in {offenders}. Procedure codes live in the "
        "criteria tree (T-38) and in the eval case, not in a predicate."
    )
