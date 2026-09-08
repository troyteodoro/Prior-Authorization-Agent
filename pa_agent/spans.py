"""The span validator: Article III's gate, by string comparison alone (D38).

`validate` returns the verified raw slice or raises `SpanValidationError`
carrying a closed `SpanRejection` reason. It never returns `None`, never a
bool, and never maps a rejection to a criterion outcome — what a rejected
span *means* is criterion-level judgment (REQ-18, REQ-23) and belongs to the
modules that own it.

The quote check is D18's predicate verbatim: runs of whitespace collapse to
one space on both sides, then exact equality. No similarity ratio, no edit
distance — a knob generous enough to absorb a line wrap is generous enough to
absorb a changed date.

No model is imported here and never will be (Article II, REQ-6): a fabricated
citation fails on string comparison, not on judgment.
"""

from __future__ import annotations

from enum import Enum

from pa_agent.contracts import EvidenceSpan
from pa_agent.index import DocumentIndex


class SpanRejection(str, Enum):
    """Why a span was rejected. Closed, because REQ-30 folds these into
    `SPAN_VALIDATION_FAILED` and T-29's fault injection asserts which fired."""

    UNKNOWN_DOCUMENT = "UNKNOWN_DOCUMENT"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    QUOTE_MISMATCH = "QUOTE_MISMATCH"


class SpanValidationError(ValueError):
    def __init__(self, reason: SpanRejection, message: str) -> None:
        super().__init__(f"{reason.value}: {message}")
        self.reason = reason


def _normalize(text: str) -> str:
    """D18's equivalence class: whitespace runs collapse, nothing else moves."""
    return " ".join(text.split())


def validate(span: EvidenceSpan, index: DocumentIndex) -> str:
    """The verified raw slice, or a classified rejection.

    Mechanical validity first (REQ-6): the document is known and the offsets
    address non-empty text. Then, when the span carries a quote, the slice
    must equal it under D18's whitespace-collapsed comparison — a span whose
    text does not say what the claim says it says is a fabricated citation,
    whatever produced it.

    Reversed and empty spans cannot arrive: `EvidenceSpan` refuses to
    construct them (T-09).
    """
    try:
        document = index.get(span.document_id)
    except KeyError:
        raise SpanValidationError(
            SpanRejection.UNKNOWN_DOCUMENT,
            f"{span.document_id!r} is not in the index; it holds {index.ids()}",
        ) from None

    if span.char_end > len(document.text):
        raise SpanValidationError(
            SpanRejection.OUT_OF_RANGE,
            f"[{span.char_start}:{span.char_end}] runs past the end of "
            f"{span.document_id} ({len(document.text)} chars)",
        )

    sliced = document.text[span.char_start : span.char_end]
    if span.quote is not None and _normalize(sliced) != _normalize(span.quote):
        raise SpanValidationError(
            SpanRejection.QUOTE_MISMATCH,
            f"{span.document_id}[{span.char_start}:{span.char_end}] slices to "
            f"{sliced[:80]!r}, not the claimed quote {span.quote[:80]!r}",
        )
    return sliced
