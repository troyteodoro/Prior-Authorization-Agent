"""T-11 — fabricated, off-by-one, and reversed spans are all rejected (D38).

The genuine spans come from T-02's recorded answers — real offsets into the
hashed corpus — so the validator is exercised against the spans the system
actually records, and each attack is a perturbation of a real citation rather
than a synthetic strawman. No model is imported by the module under test,
asserted on its AST.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from pa_agent.contracts import EvidenceSpan
from pa_agent.index import DocumentIndex
from pa_agent.spans import SpanRejection, SpanValidationError, validate
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSWERS_PATH = REPO_ROOT / "data" / "policies" / "source" / "answers.json"
SPANS_MODULE = REPO_ROOT / "pa_agent" / "spans.py"


@pytest.fixture(scope="module")
def index() -> DocumentIndex:
    store = LocalPolicyStore()
    idx = DocumentIndex()
    for document_id in ("ncd_100_1", "a53028", "r931cp"):
        idx.add(store.get_document(document_id))
    return idx


@pytest.fixture(scope="module")
def real_answer() -> dict:
    """T-02's first recorded answer: a genuine span with a genuine quote."""
    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))["answers"]
    return answers[0]


def _span(answer: dict, **overrides) -> EvidenceSpan:
    fields = {
        "document_id": answer["document_id"],
        "char_start": answer["char_start"],
        "char_end": answer["char_end"],
        "quote": answer["quote"],
    }
    fields.update(overrides)
    return EvidenceSpan(**fields)


# --------------------------------------------------------------------------
# The exit condition's three rejections
# --------------------------------------------------------------------------


def test_a_genuine_recorded_span_validates_and_returns_the_raw_slice(
    index, real_answer
):
    sliced = validate(_span(real_answer), index)
    assert sliced == index.get(real_answer["document_id"]).text[
        real_answer["char_start"] : real_answer["char_end"]
    ]


def test_a_fabricated_quote_is_rejected(index, real_answer):
    """The single worst failure available to this system (D18): offsets that
    exist, text that does not say what the claim says it says."""
    forged = _span(
        real_answer,
        quote="The program requires two months of documentation.",
    )
    with pytest.raises(SpanValidationError) as err:
        validate(forged, index)
    assert err.value.reason is SpanRejection.QUOTE_MISMATCH


def test_an_off_by_one_span_is_rejected(index, real_answer):
    shifted = _span(
        real_answer,
        char_start=real_answer["char_start"] + 1,
        char_end=real_answer["char_end"] + 1,
    )
    with pytest.raises(SpanValidationError) as err:
        validate(shifted, index)
    assert err.value.reason is SpanRejection.QUOTE_MISMATCH


def test_a_reversed_span_cannot_be_constructed(real_answer):
    """Rejected before the validator ever sees it: the contract refuses the
    object (T-09), so no code path can carry one to acceptance."""
    with pytest.raises(Exception, match="empty or reversed"):
        _span(
            real_answer,
            char_start=real_answer["char_end"],
            char_end=real_answer["char_start"],
        )


# --------------------------------------------------------------------------
# The other two classified rejections
# --------------------------------------------------------------------------


def test_an_unknown_document_is_rejected_as_its_own_reason(index, real_answer):
    ghost = _span(real_answer, document_id="chart_note_007")
    with pytest.raises(SpanValidationError) as err:
        validate(ghost, index)
    assert err.value.reason is SpanRejection.UNKNOWN_DOCUMENT


def test_a_span_past_the_end_is_rejected_as_out_of_range(index):
    text_len = len(index.get("ncd_100_1").text)
    over = EvidenceSpan(
        document_id="ncd_100_1", char_start=text_len - 5, char_end=text_len + 5
    )
    with pytest.raises(SpanValidationError) as err:
        validate(over, index)
    assert err.value.reason is SpanRejection.OUT_OF_RANGE


# --------------------------------------------------------------------------
# D18's equivalence class, inherited exactly
# --------------------------------------------------------------------------


def test_a_line_wrapped_quote_still_validates(index, real_answer):
    """The false negative D18 exists to prevent: a quote differing from the
    document only in where the line breaks fall is the same citation."""
    words = real_answer["quote"].split()
    midpoint = len(words) // 2
    wrapped = " ".join(words[:midpoint]) + "\n" + " ".join(words[midpoint:])
    sliced = validate(_span(real_answer, quote=wrapped), index)
    raw = index.get(real_answer["document_id"]).text[
        real_answer["char_start"] : real_answer["char_end"]
    ]
    assert sliced == raw, "the validator must return the raw slice, not the quote"


def test_a_changed_word_is_rejected_however_similar(index, real_answer):
    """The knob D18 refused: an equivalence generous enough to absorb a line
    wrap must not be generous enough to absorb a changed word."""
    altered = real_answer["quote"].replace("month", "week", 1)
    assert altered != real_answer["quote"], "the fixture quote no longer says 'month'"
    with pytest.raises(SpanValidationError) as err:
        validate(_span(real_answer, quote=altered), index)
    assert err.value.reason is SpanRejection.QUOTE_MISMATCH


def test_a_quoteless_span_is_validated_for_mechanical_existence_only(index):
    sliced = validate(
        EvidenceSpan(document_id="a53028", char_start=100, char_end=200), index
    )
    assert len(sliced) == 100


# --------------------------------------------------------------------------
# No model in the module — Article II, asserted on the imports
# --------------------------------------------------------------------------


def test_the_validator_imports_no_model_no_store_and_no_file_api():
    tree = ast.parse(SPANS_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"__future__", "enum", "pa_agent.contracts", "pa_agent.index"}, (
        f"pa_agent/spans.py imports {sorted(imported)}; rejection is string "
        "comparison and nothing else (REQ-6, D38)"
    )
