"""The policy plane's port, and the adapter that reads the repository (D25).

`PolicyStore` is three methods because three is what this system asks of policy
storage. A generic query interface would be a boundary nobody can audit: what
crosses Article VI's line would become a runtime property of the caller instead
of a fact you can establish by reading six signatures.

The production adapter is a database. Nothing above this module changes when it
arrives — with one rule that is not negotiable at that point either. Criteria
trees do not get a write path. Article VII wants a diff and a reviewer for every
clinical rule change, and an `UPDATE` on a tree row is a rule change with
neither. A store may serve `get_tree` from a deploy-time read-only projection;
git stays the source of truth.

This module holds no patient data, imports nothing from the patient plane, and
must never be made to (REQ-33).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.contracts import (
    CoverageClaim,
    CoverageStatus,
    CriteriaTree,
    Document,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_ROOT = REPO_ROOT / "data" / "policies"


class PolicyRef(BaseModel):
    """What a procedure code resolves to: a policy, at a version (REQ-1, REQ-4).

    Since T-24 it also carries the *fact* of where the code is bound — which of
    the tree's three sets — and the spanned coverage claim for that entry, so a
    short-circuit determination can cite its denial (Art. III) without a second
    trip through the port. The fact is not a judgment: mapping membership to an
    outcome is `pa_agent.resolver`'s — contractor-determined included, since
    D33 made it a third outcome there (D31)."""

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)
    policy_version_id: str = Field(min_length=1)
    coverage: CoverageStatus
    procedure: str = Field(min_length=1)
    coverage_claim: CoverageClaim


@runtime_checkable
class PolicyStore(Protocol):
    """Everything the system reads from the policy plane."""

    def resolve(self, procedure_code: str) -> PolicyRef | None:
        """The governing policy for a code, or `None` for `NO_POLICY_FOUND`.

        `None` means no policy in this store governs the code. It does **not**
        mean not covered — REQ-2's `NOT_COVERED` is a policy saying no, which is
        a different answer from no policy saying anything, and D26 is about what
        happens when those two get expressed as the same absence.
        """
        ...

    def get_tree(self, policy_version_id: str) -> CriteriaTree:
        """The compiled criteria tree, by the version a determination records."""
        ...

    def get_document(self, document_id: str) -> Document:
        """A source document, content-verified, for spans to be checked against."""
        ...

    def get_value_set(self, value_set_id: str) -> frozenset[str]:
        """The codes a value set admits, by the id a criterion's constant names.

        A value set is a compiled fragment of the policy — A53028's Group 1
        decides which comorbidities count — so it travels with the tree and is
        versioned with it (Art. VII, D52). The return is deliberately just the
        codes: set membership is the whole interface a predicate has.
        """
        ...


class LocalPolicyStore:
    """`PolicyStore` over the repository as it stands: T-01's tree, T-02's corpus."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_POLICY_ROOT
        self._source_dir = self._root / "source"
        self._manifest_path = self._source_dir / "sources.json"
        self._trees: dict[str, CriteriaTree] = {}
        self._binding_cache: dict[str, tuple[CriteriaTree, CoverageStatus, Any]] | None = None
        self._documents: dict[str, Document] = {}
        self._manifest: dict[str, dict] | None = None
        self._value_set_dir = self._root / "value_sets"
        self._value_sets: dict[str, frozenset[str]] = {}

    # -- policy resolution -------------------------------------------------

    def resolve(self, procedure_code: str) -> PolicyRef | None:
        """The membership fact for a code, or `None` for no governing policy.

        `None` means no policy in this store binds the code as an identity. It
        does **not** mean not covered — REQ-2's `NOT_COVERED` is a policy
        saying no, a different answer from no policy saying anything (D26).
        The mapping from membership to an outcome lives in
        `pa_agent.resolver`, not here: an adapter reports what the tree
        records, contractor-determined included, and makes no judgment (D31).

        Facility code lists are not consulted. They overlap across procedures
        in the source itself, which is why they are not identities (D30).
        """
        index = self._binding_index()
        hit = index.get(procedure_code)
        if hit is None:
            return None
        tree, status, entry = hit
        return PolicyRef(
            procedure_code=procedure_code,
            policy_version_id=tree.policy_version_id,
            coverage=status,
            procedure=entry.procedure,
            coverage_claim=entry.coverage_claim,
        )

    def _binding_index(self) -> dict[str, tuple[CriteriaTree, CoverageStatus, Any]]:
        """`{code -> (tree, set, entry)}` over identity bindings, built once.

        `ProcedureSets`' validator already refuses a code bound twice within
        one tree; this refuses a code bound by two trees, which no validator
        can see because no object holds both trees.
        """
        if self._binding_cache is None:
            index: dict[str, tuple[CriteriaTree, CoverageStatus, Any]] = {}
            for tree in self._load_trees().values():
                if tree.procedure_sets is None:
                    continue
                for status, entry in tree.procedure_sets.entries():
                    for binding in entry.codes:
                        if binding.code in index:
                            other = index[binding.code][0].policy_version_id
                            raise ValueError(
                                f"{binding.code} is bound by {other} and "
                                f"{tree.policy_version_id}; resolution would "
                                "depend on load order"
                            )
                        index[binding.code] = (tree, status, entry)
            self._binding_cache = index
        return self._binding_cache

    # -- trees -------------------------------------------------------------

    def _load_trees(self) -> dict[str, CriteriaTree]:
        if not self._trees:
            for path in sorted(self._root.glob("*.json")):
                tree = CriteriaTree.model_validate_json(path.read_text(encoding="utf-8"))
                self._trees[tree.policy_version_id] = tree
        return self._trees

    def get_tree(self, policy_version_id: str) -> CriteriaTree:
        self._load_trees()
        try:
            return self._trees[policy_version_id]
        except KeyError:
            known = sorted(self._trees)
            raise KeyError(
                f"no criteria tree with policy_version_id {policy_version_id!r}; "
                f"this store holds {known}"
            ) from None

    # -- documents ---------------------------------------------------------

    def _load_manifest(self) -> dict[str, dict]:
        if self._manifest is None:
            if not self._manifest_path.is_file():
                raise FileNotFoundError(
                    f"{self._manifest_path} is absent. Run "
                    "`python scripts/verify_sources.py --fetch` (T-02)."
                )
            raw = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            self._manifest = {d["document_id"]: d for d in raw["documents"]}
        return self._manifest

    def get_document(self, document_id: str) -> Document:
        """Load a source document and verify it against T-02's recorded hash.

        The hash comes from `sources.json` rather than from the text, so a
        document edited on disk raises here instead of silently re-anchoring
        every span into it (REQ-7). `Document`'s own validator does the
        comparison; this method only supplies the record it is checked against.
        """
        if document_id in self._documents:
            return self._documents[document_id]

        manifest = self._load_manifest()
        try:
            record = manifest[document_id]
        except KeyError:
            raise KeyError(
                f"no document {document_id!r} in the corpus; this store holds "
                f"{sorted(manifest)}"
            ) from None

        path = self._source_dir / record["filename"]
        if not path.is_file():
            raise FileNotFoundError(f"{document_id}: {path} is absent")

        document = Document(
            document_id=document_id,
            text=path.read_text(encoding="utf-8"),
            sha256=record["sha256"],
        )
        self._documents[document_id] = document
        return document

    def get_value_set(self, value_set_id: str) -> frozenset[str]:
        """The SNOMED codes this value set admits (D52).

        SNOMED because that is what `LocalPatientStore` reports on
        `Condition.code` and what `evaluate_criterion_b` tests membership
        against. A set of ICD-10 codes would load cleanly, compare cleanly and
        match nobody — criterion (b) would abstain for every patient and every
        downstream test would agree with it.
        """
        if value_set_id in self._value_sets:
            return self._value_sets[value_set_id]

        path = self._value_set_dir / f"{value_set_id}.json"
        if not path.exists():
            available = sorted(p.stem for p in self._value_set_dir.glob("*.json"))
            raise KeyError(
                f"no value set {value_set_id!r} in {self._value_set_dir}; "
                f"available: {available}. An empty set here would be a claim "
                "about the policy, and would deny every patient a criterion "
                "they might meet (D52)."
            )

        payload = json.loads(path.read_text(encoding="utf-8"))
        declared = payload.get("value_set_id")
        if declared != value_set_id:
            raise ValueError(
                f"{path.name} declares value_set_id {declared!r}; a file whose "
                "contents no longer match its path is a rename that half "
                "happened"
            )
        entries = payload.get("entries") or []
        if not entries:
            raise ValueError(f"{path.name} admits no codes; see D52")
        codes = frozenset(str(entry["code"]) for entry in entries)
        self._value_sets[value_set_id] = codes
        return codes

    def document_ids(self) -> list[str]:
        """The corpus, for tests and for the plane-separation walk."""
        return sorted(self._load_manifest())
