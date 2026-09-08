"""Quote anchoring: Python locates what the model quoted (D17, D18, D45).

D19 settled that this is not optional machinery. Zero of eighty model-emitted
`char_start`/`char_end` pairs were usable, and not one sliced back to its own
quote even under whitespace-insensitive comparison — the arithmetic does not
work at all, so the offsets a claim carries are located here or the claim
carries none.

Matching is exact first and whitespace-normalized second (D18); the offsets
produced are always **raw** offsets into the unmodified document, so the span
recorded is the span a reviewer slices. Normalization changes the equivalence
class and never the comparison: there is no threshold here, and a fabricated
quote still misses.

Separate from `spans.py` on purpose (D45): locating and validating are
different operations, and the validator must not depend on the locator —
otherwise a bug in location could launder itself through the check that
exists to catch it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pa_agent.contracts import EvidenceSpan

WHITESPACE = re.compile(r"\s+")
# A two-character backslash-n, -r or -t sitting *inside* a string, which is
# what arrives when the model JSON-escapes a newline twice (D47).
LITERAL_ESCAPE = re.compile(r"\\[nrt]")


def collapse(text: str) -> str:
    """Every run of whitespace becomes one space (D18)."""
    return WHITESPACE.sub(" ", text).strip()


def unescape_literal_whitespace(quote: str) -> str:
    """Treat a literal `\\n` sequence in a quote as the whitespace it encodes.

    The model sometimes returns a double-escaped newline, so the parsed string
    holds a backslash and an `n` where the note holds a line break. That is a
    transport artifact of the JSON encoding, not a property of the claim — the
    same class of fact as D18's line wrap — and it is repaired without a
    threshold: the sequence either is those two characters or it is not.

    Applied to the **quote only**. The document is never rewritten, so the
    offsets produced still point into the unmodified note.
    """
    return LITERAL_ESCAPE.sub(" ", quote)


def collapse_with_index(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace, keeping a map from each collapsed offset to its raw one.

    `index[i]` is the offset in `text` that produced `collapsed[i]`; for a
    collapsed run of whitespace it is the offset of the run's first character.
    The map is what lets a normalized hit come back as offsets into the
    unmodified document — the span cited is raw, only the comparison relaxed.
    """
    out: list[str] = []
    index: list[int] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            out.append(" ")
            index.append(i)
            i = j
        else:
            out.append(text[i])
            index.append(i)
            i += 1
    # A leading space would shift every offset by one against `collapse`,
    # which strips. Drop it here so the two agree.
    if out and out[0] == " ":
        out.pop(0)
        index.pop(0)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


@dataclass
class AnchoredSpan:
    """One located quote, and the evidence about how it was located.

    The bookkeeping fields are not decoration: `model_offsets_yield_quote` is
    the number whose rise would retire this whole module (D17's reversal
    clause), and it cannot rise or fall unnoticed if nobody records it.
    """

    document_id: str
    quote: str
    model_start: int
    model_end: int
    char_start: int | None = None
    char_end: int | None = None
    anchor_mode: str | None = None
    occurrences: int = 0
    # True when the quote occurred more than once and `prefer_near` chose an
    # occurrence other than the first — the D46 mechanism actually firing.
    disambiguated: bool = False
    model_offsets_exact: bool = False
    model_offsets_yield_quote: bool = False

    @property
    def anchored(self) -> bool:
        return self.char_start is not None

    def to_span(self) -> EvidenceSpan:
        if not self.anchored:
            raise ValueError(
                f"{self.document_id}: quote was not located, so there is no span "
                "to build. A claim Python could not anchor cites nothing (Art. III)."
            )
        return EvidenceSpan(
            document_id=self.document_id,
            char_start=self.char_start,
            char_end=self.char_end,
            quote=self.quote,
        )


def _all_occurrences(haystack: str, needle: str) -> list[int]:
    hits, start = [], 0
    while True:
        found = haystack.find(needle, start)
        if found == -1:
            return hits
        hits.append(found)
        start = found + 1


def _closest(hits: list[int], length: int, window: tuple[int, int]) -> tuple[int, bool]:
    """The occurrence nearest `window`, and whether that was not the first.

    Distance is zero inside the window and the gap to its nearer edge outside
    it, so an occurrence within the event beats every occurrence outside it.
    Ties keep the first hit, which leaves the single-occurrence path unchanged.
    """
    low, high = window

    def distance(start: int) -> int:
        end = start + length
        if end <= low:
            return low - end
        if start >= high:
            return start - high
        return 0

    best = min(hits, key=lambda h: (distance(h), h))
    return best, best != hits[0]


def anchor(
    document_id: str,
    text: str,
    quote: str,
    model_start: int = -1,
    model_end: int = -1,
    prefer_near: tuple[int, int] | None = None,
) -> AnchoredSpan:
    """Locate `quote` in `text`, recording how it was found.

    `prefer_near` disambiguates a repeated quote by proximity (D46): a
    per-field span documents *this* encounter, so when the same phrase occurs
    in several encounters the one nearest the event wins. Without it a
    plateaued `BMI 37.6` anchors every month to the first month — a span that
    slices back perfectly, passes T-11, and cites the wrong visit.

    An unanchored result is returned rather than raised: a quote that does not
    occur in the document is a fact about the extraction worth counting, and
    the caller decides whether that costs an event or a whole note.
    """
    span = AnchoredSpan(document_id, quote, model_start, model_end)
    if not quote:
        return span

    # What the model's own arithmetic would have produced, recorded and never
    # trusted (D17). This is the measurement that would reverse D45.
    if 0 <= model_start < model_end <= len(text):
        fragment = text[model_start:model_end]
        span.model_offsets_exact = fragment == quote
        span.model_offsets_yield_quote = collapse(fragment) == collapse(quote)

    hits = _all_occurrences(text, quote)
    if hits:
        span.anchor_mode = "exact"
        span.occurrences = len(hits)
        found = hits[0]
        if len(hits) > 1 and prefer_near is not None:
            found, span.disambiguated = _closest(hits, len(quote), prefer_near)
        span.char_start = found
        span.char_end = found + len(quote)
    else:
        # D18: an exact miss has two causes and they are different facts. EHR
        # text hard-wraps, so a quote crossing a wrap point arrives with its
        # newline flattened — a property of the file, not of the claim. A
        # fabricated quote still misses.
        flat_text, index = collapse_with_index(text)
        flat_quote = collapse(unescape_literal_whitespace(quote))
        flat_hits = _all_occurrences(flat_text, flat_quote) if flat_quote else []
        if not flat_hits:
            return span
        span.anchor_mode = (
            "unescaped" if flat_quote != collapse(quote) else "normalized"
        )
        span.occurrences = len(flat_hits)
        found = flat_hits[0]
        if len(flat_hits) > 1 and prefer_near is not None:
            # The window is in raw offsets; map it into collapsed space by
            # searching the index rather than assuming the two agree.
            low = next((i for i, raw in enumerate(index) if raw >= prefer_near[0]), 0)
            high = next(
                (i for i, raw in enumerate(index) if raw >= prefer_near[1]), len(index)
            )
            found, span.disambiguated = _closest(flat_hits, len(flat_quote), (low, high))
        span.char_start = index[found]
        span.char_end = index[found + len(flat_quote) - 1] + 1

    fragment = text[span.char_start : span.char_end]
    if collapse(fragment) != collapse(unescape_literal_whitespace(quote)):
        # Mapping back to raw offsets produced a slice that is not the quote.
        # Nothing may cite a span whose slice does not yield what it claims.
        span.char_start = None
        span.char_end = None
        span.anchor_mode = None
    return span
