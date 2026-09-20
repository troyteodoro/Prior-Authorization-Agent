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
    CodedValueSet,
    CoverageClaim,
    CoverageStatus,
    CriteriaTree,
    Document,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_ROOT = REPO_ROOT / "data" / "policies"


class UnknownJurisdiction(LookupError):
    """No tree in this store governs the state a request named (T-87, D100).

    Typed, and carrying what the store knows, because the two ways to get it
    wrong are both quiet: `None` would read as REQ-1's "no policy binds the
    code" (D26 rebuilt), and a bare `KeyError` would reach the CLI as a bad
    request beside an unknown patient. `pa_agent.resolver` maps it to its own
    result type.
    """

    def __init__(self, state: str, known_states: list[str]) -> None:
        super().__init__(
            f"no criteria tree governs state {state!r}; this store serves "
            f"{known_states}"
        )
        self.state = state
        self.known_states = known_states


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

    def resolve(self, procedure_code: str, state: str) -> PolicyRef | None:
        """The governing policy for a code in a state, or `None` for `NO_POLICY_FOUND`.

        `None` means the tree governing `state` binds the code in none of its
        sets. It does **not** mean not covered — REQ-2's `NOT_COVERED` is a
        policy saying no, which is a different answer from no policy saying
        anything, and D26 is about what happens when those two get expressed
        as the same absence. A state no tree governs is a third thing again
        and raises `UnknownJurisdiction` (T-87, D100): "no policy binds the
        code" and "no tree covers this state" must never share an answer.
        """
        ...

    def get_tree(self, policy_version_id: str) -> CriteriaTree:
        """The compiled criteria tree, by the version a determination records."""
        ...

    def get_document(self, document_id: str) -> Document:
        """A source document, content-verified, for spans to be checked against."""
        ...

    def get_value_set(self, value_set_id: str) -> CodedValueSet:
        """The codes a value set admits, and the system they are tested in.

        A value set is a compiled fragment of the policy — A53028's Group 1
        decides which comorbidities count — so it travels with the tree and is
        versioned with it (Art. VII, D52). Since T-92 the return carries the
        declared code system beside the codes (REQ-59, D111): set membership is
        still the whole interface a predicate has, but membership is tested
        **within a system**, because the second practice matches medications in
        RxNorm and a bare code set cannot say which vocabulary it is in.
        """
        ...


class LocalPolicyStore:
    """`PolicyStore` over the repository as it stands: T-01's tree, T-02's corpus."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_POLICY_ROOT
        self._source_dir = self._root / "source"
        self._manifest_path = self._source_dir / "sources.json"
        self._trees: dict[str, CriteriaTree] = {}
        self._binding_cache: (
            dict[tuple[str, str], tuple[CriteriaTree, CoverageStatus, Any]] | None
        ) = None
        self._state_cache: dict[str, tuple[CriteriaTree, ...]] | None = None
        self._documents: dict[str, Document] = {}
        self._manifest: dict[str, dict] | None = None
        self._value_set_dir = self._root / "value_sets"
        self._value_sets: dict[str, CodedValueSet] = {}

    # -- policy resolution -------------------------------------------------

    def resolve(self, procedure_code: str, state: str) -> PolicyRef | None:
        """The membership fact for a code under the state's tree, or `None`.

        `None` means the tree governing `state` binds the code as an identity
        in none of its sets. It does **not** mean not covered — REQ-2's
        `NOT_COVERED` is a policy saying no, a different answer from no policy
        saying anything (D26). A state no tree governs raises
        `UnknownJurisdiction` rather than returning anything (D100). The
        mapping from membership to an outcome lives in `pa_agent.resolver`,
        not here: an adapter reports what the tree records,
        contractor-determined included, and makes no judgment (D31).

        Facility code lists are not consulted. They overlap across procedures
        in the source itself, which is why they are not identities (D30).
        """
        self.trees_for_state(state)  # raises UnknownJurisdiction
        index = self._binding_index()
        hit = index.get((state, procedure_code))
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

    def trees_for_state(self, state: str) -> tuple[CriteriaTree, ...]:
        """Every tree whose jurisdiction names `state` (T-87, D100; T-92, D111).

        **Was `tree_for_state`, returning one tree, and raising when two trees
        claimed a state.** That rule was right while one tree per state was the
        model and wrong the moment a second practice arrived: Palmetto GBA
        serves AL, GA, NC, SC, TN, VA and WV for bariatric surgery *and* for
        infliximab, so the rule and `infliximab-ra-jjm-v1` cannot both exist.
        The collision that actually makes resolution depend on load order is a
        **code** bound for one state by two trees, and `_binding_index` below
        has raised on that since T-24.

        `UnknownJurisdiction` keeps REQ-55's meaning exactly: no tree in this
        store serves this state at all. A code no tree binds for a state that
        *is* served stays `NO_POLICY_FOUND` — no policy here governs the code,
        which is not a denial (D26).

        A national tree names no states and is reachable by no request — every
        tree this store serves is a MAC's reading of a document (D21), and a
        request is always for a patient somewhere.
        """
        index = self._state_index()
        try:
            return index[state]
        except KeyError:
            raise UnknownJurisdiction(state, sorted(index)) from None

    def _state_index(self) -> dict[str, tuple[CriteriaTree, ...]]:
        """`{state -> the trees serving it}`, built once, in load order."""
        if self._state_cache is None:
            index: dict[str, list[CriteriaTree]] = {}
            for tree in self._load_trees().values():
                for state in tree.jurisdiction.states:
                    index.setdefault(state, []).append(tree)
            self._state_cache = {k: tuple(v) for k, v in index.items()}
        return self._state_cache

    def _binding_index(
        self,
    ) -> dict[tuple[str, str], tuple[CriteriaTree, CoverageStatus, Any]]:
        """`{(state, code) -> (tree, set, entry)}` over identity bindings, built once.

        `ProcedureSets`' validator already refuses a code bound twice within
        one tree. Two rules live here because no object holds two trees:

        - a code bound by two trees **for one state** raises, since resolution
          would depend on load order (T-24's rule, scoped by D100);
        - a code in a **national** set must carry the same status in every
          tree that binds it, because those sets transcribe one NCD — two
          MAC trees disagreeing about what CMS said is a transcription error,
          not a jurisdictional difference. `contractor_determined` may differ
          between trees; that is the divergence D33 anticipated.
        """
        if self._binding_cache is None:
            index: dict[tuple[str, str], tuple[CriteriaTree, CoverageStatus, Any]] = {}
            national: dict[str, tuple[CoverageStatus, str]] = {}
            for tree in self._load_trees().values():
                if tree.procedure_sets is None:
                    continue
                for status, entry in tree.procedure_sets.entries():
                    for binding in entry.codes:
                        if status is not CoverageStatus.CONTRACTOR_DETERMINED:
                            seen = national.get(binding.code)
                            if seen is not None and seen[0] is not status:
                                raise ValueError(
                                    f"{binding.code} is {seen[0].value} in {seen[1]} "
                                    f"and {status.value} in {tree.policy_version_id}; "
                                    "the national sets transcribe one NCD and "
                                    "cannot disagree"
                                )
                            national[binding.code] = (status, tree.policy_version_id)
                        for state in tree.jurisdiction.states:
                            key = (state, binding.code)
                            if key in index:
                                other = index[key][0].policy_version_id
                                raise ValueError(
                                    f"{binding.code} is bound for {state} by {other} "
                                    f"and {tree.policy_version_id}; resolution would "
                                    "depend on load order"
                                )
                            index[key] = (tree, status, entry)
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

    def get_value_set(self, value_set_id: str) -> CodedValueSet:
        """The codes this value set admits, in the system it declares (D52, D111).

        The system used to be SNOMED by assumption — that is what
        `LocalPatientStore` reports on `Condition.code` and what
        `evaluate_criterion_b` tested against — with the note that a set of
        ICD-10 codes would load cleanly, compare cleanly and match nobody.
        T-92 gave that a mechanism: the file declares one system, every entry
        must be in it, and a mismatch raises here rather than shipping a set
        that is quietly half in another vocabulary (REQ-59).
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

        system = payload.get("system")
        if not system:
            raise ValueError(
                f"{path.name} declares no `system`. A value set whose code "
                "system is implied is one that can be wrong about it silently: "
                "the codes load, compare, and match nobody (REQ-59, D111)."
            )
        wrong = sorted(
            {
                str(entry.get("system"))
                for entry in entries
                if entry.get("system") != system
            }
        )
        if wrong:
            raise ValueError(
                f"{path.name} declares system {system!r} and carries entries in "
                f"{wrong}. One set is one vocabulary; a mixed set is a set every "
                "predicate over it would agree with (REQ-59, D111)."
            )

        value_set = CodedValueSet(
            value_set_id=value_set_id,
            system=system,
            codes=frozenset(str(entry["code"]) for entry in entries),
        )
        self._value_sets[value_set_id] = value_set
        return value_set

    def document_ids(self) -> list[str]:
        """The corpus, for tests and for the plane-separation walk."""
        return sorted(self._load_manifest())
