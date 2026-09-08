"""The document index: id to text, spans to slices, and REQ-7 as an exception (D37).

Plane-agnostic machinery. An instance holds whatever `Document` objects its
owner registered — the policy corpus in one instance, patient notes in another
— and this module opens no file and imports no store (REQ-41), so it cannot
decide to hold both. Article VI is enforced where the data enters, not here.

`slice` is mechanical: it returns exactly what the offsets address or raises.
Judging the result — fabricated, off-by-one, not matching its quote — is the
span validator's job (T-11, REQ-6). No model is imported here and never will
be (Article II): everything this module does is string arithmetic.
"""

from __future__ import annotations

from pa_agent.contracts import Document, EvidenceSpan


class DocumentConflictError(ValueError):
    """An id rebound to different content. Every span into it is suspect (REQ-7)."""


class DocumentIndex:
    """A registry of immutable, content-addressed documents."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def __contains__(self, document_id: str) -> bool:
        return document_id in self._documents

    def __len__(self) -> int:
        return len(self._documents)

    def ids(self) -> list[str]:
        return sorted(self._documents)

    def add(self, document: Document) -> None:
        """Register a document. Idempotent for identical content; a changed
        hash under a known id raises rather than re-anchoring every span into
        it silently (REQ-7)."""
        held = self._documents.get(document.document_id)
        if held is None:
            self._documents[document.document_id] = document
            return
        if held.sha256 != document.sha256:
            raise DocumentConflictError(
                f"{document.document_id} is already indexed with hash "
                f"{held.sha256[:12]} and cannot be rebound to {document.sha256[:12]}. "
                "A document whose content changes invalidates every span into it "
                "(REQ-7); index the new content under a new id."
            )

    def get(self, document_id: str) -> Document:
        try:
            return self._documents[document_id]
        except KeyError:
            raise KeyError(
                f"no document {document_id!r} in this index; it holds {self.ids()}"
            ) from None

    def slice(self, span: EvidenceSpan) -> str:
        """The text a span addresses, from the unmodified document.

        Raises on an unknown document or offsets past the end. Reversed and
        empty spans cannot arrive: `EvidenceSpan` refuses to construct them.
        The result is never empty and never `None` — a silent absence is the
        failure REQ-6 exists to prevent, so failure here is always loud.
        """
        document = self.get(span.document_id)
        if span.char_end > len(document.text):
            raise IndexError(
                f"span [{span.char_start}:{span.char_end}] runs past the end of "
                f"{span.document_id} ({len(document.text)} chars)"
            )
        return document.text[span.char_start : span.char_end]
