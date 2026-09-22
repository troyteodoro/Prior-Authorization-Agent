"""The knowledge plane's port, and the adapter that reads the repository (T-97, D119).

The knowledge corpus is neither of the other two. `data/policies/` holds what a
**payer covers**; `data/patients/` holds one person's chart; `data/knowledge/`
holds what a **drug is known to do**, which is a claim about pharmacology that
no payer and no patient owns. T-96 gave it its own manifest for exactly that
reason (D118), and this port is the only route from it into the engine.

Three reads, because three is what the review and its graders ask of it:

- `get_medication_effect_rows()` — the reviewed table. The only place a
  suggested ICD-10 code may come from (REQ-63).
- `get_ingredient_products(rxcui)` — the pinned RxNorm expansion, as a
  `CodedValueSet` so membership is tested inside a declared system (REQ-59).
  Without it nothing matches: a row declares an ingredient, Synthea prescribes
  clinical drugs, and the comparison loads cleanly and matches nobody in all
  fourteen bundles (measured, D119).
- `get_document(document_id)` — the hashed label text an effect span points into.
  It exists so a grader can slice a suggestion's citation the way it slices a
  verdict's (Article III), and `Document` re-verifies the hash on construction,
  so every eval run re-checks the corpus for free (REQ-7).

This module exists because `pa_agent/history.py` **may not name a path**.
Article VI puts every storage location behind a port and `cli.py` is the one
place a store is constructed (REQ-41, D25, D83); a module-level
`Path("data/knowledge/…")` in the review would be a second adapter nobody
declared, and `tests/test_planes.py` fails on it.

It holds no patient data and no policy, imports nothing from either plane, and
must never be made to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pa_agent.contracts import (
    CodedConcept,
    CodedValueSet,
    Document,
    EffectSignal,
    EvidenceSpan,
    MedicationEffectRow,
    SourceQuery,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_KNOWLEDGE_ROOT = REPO_ROOT / "data" / "knowledge"

#: The comparators a row's signal may declare. Closed here as well as in
#: `EffectSignal`, so a file carrying `approximately` fails at load naming the
#: file rather than at construction naming a pydantic field.
COMPARATORS = ("lt", "lte", "gt", "gte")


@runtime_checkable
class KnowledgeStore(Protocol):
    """What the review needs of knowledge storage, and nothing more."""

    def get_medication_effect_rows(self) -> list[MedicationEffectRow]:
        """Every row of the reviewed table, in file order.

        Raises rather than returning `[]`. An empty table is a well-formed
        answer meaning *no drug on this chart is known to cause anything*, and
        every downstream check would agree with it (D31, D39).
        """
        ...

    def get_ingredient_products(self, ingredient_rxcui: str) -> CodedValueSet:
        """The RxNorm concepts that mean *this drug*, as a value set.

        Raises on an ingredient the pinned expansion does not carry. Returning
        an empty set would silently un-match a row the table declares, which is
        the same failure as the missing expansion it exists to fix.
        """
        ...

    def get_document(self, document_id: str) -> Document:
        """One hashed label's extracted text. Raises on an unknown id."""
        ...


class LocalKnowledgeStore:
    """File-backed adapter over `data/knowledge/`.

    Every malformed shape raises here, at load, naming the file. The alternative
    is a row that loads with an unresolvable citation, which is the one thing a
    suggestion must never be (REQ-62).
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_KNOWLEDGE_ROOT
        self._table_path = self._root / "medication_effects.json"
        self._expansion_path = self._root / "rxnorm_ingredient_products.json"
        self._manifest_path = self._root / "sources.json"
        self._rows: list[MedicationEffectRow] | None = None
        self._products: dict[str, CodedValueSet] | None = None

    # ------------------------------------------------------------------
    # The table
    # ------------------------------------------------------------------

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise KeyError(
                f"no {path.name} under {self._root}. The knowledge corpus is a "
                "committed artifact; an absent one is a broken checkout, not an "
                "empty table (D118)."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _document_ids(self) -> set[str]:
        manifest = self._read(self._manifest_path)
        return {d["document_id"] for d in manifest.get("documents") or []}

    def get_medication_effect_rows(self) -> list[MedicationEffectRow]:
        if self._rows is not None:
            return list(self._rows)

        payload = self._read(self._table_path)
        raw_rows = payload.get("rows") or []
        if not raw_rows:
            raise ValueError(
                f"{self._table_path.name} carries no rows. An empty table means "
                "no chart can ever be reviewed, and every check downstream "
                "would pass (D31's shape)."
            )

        covered = self._document_ids()
        rows: list[MedicationEffectRow] = []
        for raw in raw_rows:
            row_id = raw.get("row_id") or "<unnamed>"
            effect = raw.get("effect") or {}
            document_id = effect.get("document_id")
            if document_id not in covered:
                raise ValueError(
                    f"row {row_id} cites {document_id!r}, which "
                    f"{self._manifest_path.name} does not cover. A code whose "
                    "effect claim traces to nothing is a citation the system "
                    "cannot honour (REQ-62)."
                )
            signal = raw.get("signal") or {}
            if signal.get("comparator") not in COMPARATORS:
                raise ValueError(
                    f"row {row_id} declares comparator "
                    f"{signal.get('comparator')!r}; one of {list(COMPARATORS)} "
                    "is the closed vocabulary the tri-state compares with "
                    "(REQ-64)."
                )
            rows.append(
                MedicationEffectRow(
                    row_id=row_id,
                    ingredient=CodedConcept(
                        system="http://www.nlm.nih.gov/research/umls/rxnorm",
                        code=str(raw["ingredient"]["rxcui"]),
                        display=raw["ingredient"]["name"],
                    ),
                    ingredient_source=SourceQuery(**raw["ingredient"]["source"]),
                    effect_display=effect["display"],
                    effect=EvidenceSpan(
                        document_id=document_id,
                        char_start=effect["char_start"],
                        char_end=effect["char_end"],
                        quote=effect["quote"],
                    ),
                    icd10_code=raw["icd10"]["code"],
                    icd10_title=raw["icd10"]["title"],
                    icd10_source=SourceQuery(**raw["icd10"]["source"]),
                    already_coded=tuple(
                        CodedConcept(
                            system=entry["system"],
                            code=str(entry["code"]),
                            display=entry.get("display"),
                        )
                        for entry in raw.get("already_coded") or []
                    ),
                    unsourced_reason=raw.get("unsourced_reason"),
                    signal=EffectSignal(
                        system=signal["system"],
                        code=signal["code"],
                        display=signal.get("display"),
                        comparator=signal["comparator"],
                        threshold=signal["threshold"],
                        unit=signal.get("unit"),
                        constant_name=signal["constant_name"],
                    ),
                    note=raw.get("note"),
                )
            )

        self._rows = rows
        return list(rows)

    # ------------------------------------------------------------------
    # The pinned expansion
    # ------------------------------------------------------------------

    def _load_products(self) -> dict[str, CodedValueSet]:
        if self._products is not None:
            return self._products

        payload = self._read(self._expansion_path)
        system = payload.get("system")
        if not system:
            raise ValueError(
                f"{self._expansion_path.name} declares no `system`. An expansion "
                "whose vocabulary is implied is one that can be wrong about it "
                "silently (REQ-59, D111)."
            )

        products: dict[str, CodedValueSet] = {}
        for record in payload.get("ingredients") or []:
            rxcui = str(record.get("ingredient_rxcui"))
            entries = record.get("entries") or []
            if not entries:
                raise ValueError(
                    f"{self._expansion_path.name} carries an empty expansion for "
                    f"{rxcui}; a row whose drug matches nothing is a row that "
                    "silently never fires (D119)."
                )
            wrong = sorted(
                {str(e.get("system")) for e in entries if e.get("system") != system}
            )
            if wrong:
                raise ValueError(
                    f"{self._expansion_path.name} declares {system!r} and carries "
                    f"entries for {rxcui} in {wrong}. One set is one vocabulary "
                    "(REQ-59)."
                )
            codes = frozenset(str(e["code"]) for e in entries)
            if rxcui not in codes:
                raise ValueError(
                    f"the expansion for {rxcui} does not contain the ingredient "
                    "itself; a chart coding the ingredient directly would then "
                    "not match its own row (D119)."
                )
            products[rxcui] = CodedValueSet(
                value_set_id=f"rxnorm_products_{rxcui}",
                system=system,
                codes=codes,
            )

        if not products:
            raise ValueError(
                f"{self._expansion_path.name} carries no ingredients; see D119"
            )
        self._products = products
        return products

    def get_document(self, document_id: str) -> Document:
        """One hashed label, with its hash re-verified on construction (REQ-7).

        Keyed by the manifest, so a span into a document the manifest does not
        carry raises here rather than resolving against whatever file happens to
        sit in `source/`.
        """
        manifest = self._read(self._manifest_path)
        records = {d["document_id"]: d for d in manifest.get("documents") or []}
        record = records.get(document_id)
        if record is None:
            raise KeyError(
                f"no knowledge document {document_id!r} in "
                f"{self._manifest_path.name}; available: {sorted(records)}"
            )
        path = self._root / "source" / record["filename"]
        if not path.exists():
            raise KeyError(
                f"{record['filename']} is recorded in "
                f"{self._manifest_path.name} and absent from {path.parent}"
            )
        return Document(
            document_id=document_id,
            text=path.read_text(encoding="utf-8"),
            sha256=record["sha256"],
        )

    def get_ingredient_products(self, ingredient_rxcui: str) -> CodedValueSet:
        products = self._load_products()
        rxcui = str(ingredient_rxcui)
        if rxcui not in products:
            raise KeyError(
                f"no pinned expansion for ingredient {rxcui!r} in "
                f"{self._expansion_path.name}; available: "
                f"{sorted(products)}. An empty set here would un-match a row the "
                "table declares, which is the failure this file exists to fix "
                "(REQ-63, D119)."
            )
        return products[rxcui]
