"""T-09 — the data contracts every other module speaks.

Pydantic models, no I/O, no model call, no knowledge of where anything is
stored. Storage lives behind the two ports in `pa_agent.stores` (REQ-41, D25);
this module is the vocabulary those ports move.

Three invariants are enforced here rather than left to the code that builds
these objects, because "enforced by a model validator, not by convention" is
what REQ-26 asks for and the same argument applies below it:

- a `Document` whose text does not hash to its recorded `sha256` cannot be
  constructed (REQ-7). A span into a document that changed underneath it is the
  failure Article III exists to prevent, and it is cheaper to catch at load.
- an `EvidenceSpan` that is reversed or negative cannot be constructed
  (Art. III). Document-backed validation — does this span slice to non-empty
  text — is T-11's, because it needs the document and this model does not have
  one.
- a `CriterionResult` that is `MET` or `NOT_MET` without a span, or
  `INSUFFICIENT_EVIDENCE` with one, cannot be constructed (REQ-5).

**Deliberately absent, and not oversights.** Each belongs to a task that has not
run, and building it here would be building ahead:

- `CriterionVerdict.ERROR`, `error_code`, `error_detail` — **T-26**. Article IV
  requires three states that never collapse; this enum currently has two of them
  and the third arrives with the machinery that classifies it.
- `gap_reason` on `CriterionResult` — **T-31**.
- `CriterionResult.discrepancies[]` — **T-33** (REQ-39).
- a resolver outcome for a procedure CMS delegated to the MACs — **T-36** (D22).
  `DeterminationOutcome` carries only the four values spec §6 already labels.
"""

from __future__ import annotations

import hashlib
from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------


class EvidenceSpan(BaseModel):
    """A claim's anchor: `(document_id, char_start, char_end)` (Art. III).

    Half-open, `[char_start, char_end)`, matching Python slicing — the same
    convention `scripts/verify_sources.py` recorded T-02's answers under, so an
    offset from `answers.json` drops in without adjustment.

    `quote` is what the span is expected to slice to. It is carried rather than
    trusted: the whole point of an offset pair is that the text can be recovered
    from the document instead of from whoever wrote the claim.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    quote: str | None = None

    @model_validator(mode="after")
    def _end_after_start(self) -> EvidenceSpan:
        if self.char_end <= self.char_start:
            raise ValueError(
                f"span [{self.char_start}:{self.char_end}] into {self.document_id} "
                "is empty or reversed; a claim anchored to no text is not a claim"
            )
        return self

    def __len__(self) -> int:
        return self.char_end - self.char_start


class Document(BaseModel):
    """Immutable, content-addressed source text that spans point into.

    Both planes use this type and neither shares an instance with the other: a
    policy document and a clinical note are the same shape and never the same
    object. Article VI restricts what data crosses the boundary, not what
    vocabulary the two sides are written in.

    The hash is verified on construction, so `Document` is the point at which
    REQ-7 stops being a policy and starts being a precondition.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    text: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _hash_matches_text(self) -> Document:
        actual = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if actual != self.sha256:
            raise ValueError(
                f"{self.document_id}: text hashes to {actual[:12]} but the record "
                f"says {self.sha256[:12]}. Every span into this document is "
                "suspect (REQ-7)."
            )
        return self

    @classmethod
    def from_text(cls, document_id: str, text: str) -> Document:
        """Hash `text` and wrap it. For adapters that hold no separate record."""
        return cls(
            document_id=document_id,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def slice(self, span: EvidenceSpan) -> str:
        """The text `span` points at, or `ValueError` if it points past the end.

        The accessor T-11's validator will be built on, not the validator
        itself. This one answers "what does this span say"; T-11 answers "should
        this span have been accepted", which is a different question with more
        rules behind it.
        """
        if span.document_id != self.document_id:
            raise ValueError(
                f"span cites {span.document_id}, this document is {self.document_id}"
            )
        if span.char_end > len(self.text):
            raise ValueError(
                f"span [{span.char_start}:{span.char_end}] runs past the end of "
                f"{self.document_id} ({len(self.text)} chars)"
            )
        return self.text[span.char_start : span.char_end]


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


class PolicyConstant(BaseModel):
    """One number, boolean or rate the criteria tree supplies, with its source.

    D23's rule, enforced at load: a constant is either sourced to a span or
    flagged `provisional` naming the open question it awaits. A constant that is
    neither — `value_set_id` is the only one today — is an internal reference
    rather than a quantity read off a policy, and carries neither.
    """

    model_config = ConfigDict(frozen=True)

    value: Any = None
    type: str
    source: EvidenceSpan | None = None
    provisional: bool = False
    open_question: int | None = None
    comparison: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _provisional_constants_are_empty_and_attributed(self) -> PolicyConstant:
        if self.provisional:
            if self.value is not None:
                raise ValueError(
                    "a provisional constant carries no value; a flagged default is "
                    "still a default, and the task consuming it will not notice"
                )
            if self.open_question is None:
                raise ValueError(
                    "a provisional constant names the open question it awaits"
                )
        elif self.value is None:
            raise ValueError(
                "a constant with no value is provisional, and says which question "
                "would settle it"
            )
        return self


class Criterion(BaseModel):
    """The compiled criterion — the one object that crosses the plane boundary.

    Article VI is stated as the smaller, true claim: the criterion text crosses,
    the policy corpus and any index over it do not. That is why this model
    carries constants and spans and holds no reference to a store.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    label: str
    evaluation: str = "deterministic"
    constants: dict[str, PolicyConstant] = Field(default_factory=dict)
    scoped_to: str | None = None
    national_floor: EvidenceSpan | None = None

    def constant(self, name: str) -> PolicyConstant:
        try:
            return self.constants[name]
        except KeyError:
            raise KeyError(f"criterion {self.id} declares no constant {name!r}") from None

    def require(self, name: str) -> Any:
        """The value of a constant, refusing a provisional one.

        The refusal is the point. Questions 4 and 5 are open, and T-13 and T-33
        are told in writing not to invent a default; this is that instruction
        made mechanical so it fails loudly instead of silently adjudicating.
        """
        constant = self.constant(name)
        if constant.provisional:
            raise ValueError(
                f"criterion {self.id}: {name} is provisional pending open question "
                f"{constant.open_question}. Resolve the question, do not default it."
            )
        return constant.value


class SourceRef(BaseModel):
    """A document the tree was compiled against, and the hash it was read at."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Jurisdiction(BaseModel):
    """Who published the constants. Not decoration — see D21.

    Every quantified constant in the tree comes from a Noridian article, not
    from CMS. A determination that does not carry this cannot be read correctly.
    """

    model_config = ConfigDict(frozen=True)

    authority: str
    contractor: str | None = None
    states: list[str] = Field(default_factory=list)
    note: str | None = None


class CoverageStatus(str, Enum):
    """Which of the tree's three procedure sets binds a code (T-38, D30).

    Set membership — a fact about the corpus, not a determination outcome.
    Mapping membership to an outcome is `pa_agent.resolver`'s job (D31), and
    the contractor-determined mapping is T-36's open question. Do not add a
    value here without a set in the tree that carries it.
    """

    NATIONALLY_COVERED = "nationally_covered"
    NATIONALLY_NON_COVERED = "nationally_non_covered"
    CONTRACTOR_DETERMINED = "contractor_determined"


class CoverageClaim(EvidenceSpan):
    """A procedure's coverage statement, spanned, with its qualifying context.

    The nested quotes are not decoration: a §C bullet alone names a procedure
    and says nothing about coverage — the scoping sentence is what makes it a
    denial, and a span into §D's delegation paragraph slices back perfectly
    well while meaning the opposite (D22, D28)."""

    scoping_quote: EvidenceSpan | None = None
    corroborating_quote: EvidenceSpan | None = None


class CodeBinding(EvidenceSpan):
    """The claim that a code denotes a procedure, spanned to what asserts it.

    A different claim class from coverage (D28): "this procedure is
    non-covered" and "this code denotes that procedure" have different sources
    and different failure modes. Since D29 the binding document is `r931cp`,
    citable for bindings and nothing else."""

    code: str = Field(min_length=1)
    system: str = Field(min_length=1)
    identity: bool
    in_corpus: bool

    @model_validator(mode="after")
    def _an_identity_is_sourced_and_names_its_code(self) -> CodeBinding:
        """D30's invariant at load, the way `Document` verifies its hash.

        An unsourced identity is the mechanism that put 43775 in the spec, and
        a quote that does not contain the code is a span onto something else."""
        if self.identity:
            if not self.in_corpus:
                raise ValueError(
                    f"identity binding for {self.code} is not in_corpus. D29 "
                    "landed r931cp so no binding would rest on recall; span it "
                    "or it is not an identity."
                )
            if not self.quote or self.code not in self.quote:
                raise ValueError(
                    f"identity binding for {self.code} carries a quote that does "
                    "not name the code; the span is anchored to something else"
                )
        return self


class ProcedureEntry(BaseModel):
    """One procedure in one of the three sets (D30's shape).

    `codes` holds identity bindings — the lookup keys. A53028's facility
    ICD-10-PCS lists stay in the tree artifact as `identity: false`
    transcriptions and are deliberately not modeled here: they overlap across
    procedures in the source itself, so nothing above the artifact may key on
    them (D30)."""

    model_config = ConfigDict(frozen=True)

    procedure: str = Field(min_length=1)
    coverage_claim: CoverageClaim
    codes: list[CodeBinding] = Field(default_factory=list)


class ProcedureSets(BaseModel):
    """The three pairwise-disjoint answers to "what does policy say about this
    code" (D26): nationally denied, delegated to the contractor, and covered —
    with absence from all three meaning REQ-1's `NO_POLICY_FOUND`.

    Disjointness is a validator, not a convention, so a production adapter
    cannot serve a colliding projection: a code with two answers fails at
    construction, not at whichever lookup happens to run first."""

    model_config = ConfigDict(frozen=True)

    nationally_covered: list[ProcedureEntry] = Field(default_factory=list)
    nationally_non_covered: list[ProcedureEntry] = Field(default_factory=list)
    contractor_determined: list[ProcedureEntry] = Field(default_factory=list)

    def entries(self) -> list[tuple[CoverageStatus, ProcedureEntry]]:
        return [
            (status, entry)
            for status, members in (
                (CoverageStatus.NATIONALLY_COVERED, self.nationally_covered),
                (CoverageStatus.NATIONALLY_NON_COVERED, self.nationally_non_covered),
                (CoverageStatus.CONTRACTOR_DETERMINED, self.contractor_determined),
            )
            for entry in members
        ]

    @model_validator(mode="after")
    def _no_code_has_two_answers(self) -> ProcedureSets:
        seen: dict[str, CoverageStatus] = {}
        for status, entry in self.entries():
            for binding in entry.codes:
                if binding.code in seen:
                    raise ValueError(
                        f"{binding.code} is bound in {seen[binding.code].value} "
                        f"and {status.value}; no code has two answers (D30)"
                    )
                seen[binding.code] = status
        return self


class CriteriaTree(BaseModel):
    """A policy compiled into criteria, constants and a decision expression.

    Loaded through `PolicyStore.get_tree`, never constructed from a path by a
    caller (REQ-41). Its write path stays in git: Article VII wants a diff and a
    reviewer for every clinical rule change, so a store may serve this from a
    deploy-time projection but never from a row someone can `UPDATE` (D25).
    """

    model_config = ConfigDict(frozen=True)

    policy_version_id: str = Field(min_length=1)
    title: str
    compiled_by: str | None = None
    jurisdiction: Jurisdiction
    sources: list[SourceRef]
    decision_expression: str
    criteria: list[Criterion]
    reconciled_facts: list[dict[str, Any]] = Field(default_factory=list)
    procedure_sets: ProcedureSets | None = None

    @model_validator(mode="after")
    def _criteria_ids_unique_and_expression_closed(self) -> CriteriaTree:
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate criterion ids in {self.policy_version_id}")
        named = set(self.decision_expression.replace("(", " ").replace(")", " ").split())
        named -= {"AND", "OR", "NOT"}
        missing = named - set(ids)
        if missing:
            raise ValueError(
                f"decision expression names {sorted(missing)}, which the tree does "
                "not define"
            )
        return self

    def criterion(self, criterion_id: str) -> Criterion:
        for criterion in self.criteria:
            if criterion.id == criterion_id:
                return criterion
        raise KeyError(
            f"{self.policy_version_id} defines no criterion {criterion_id!r}"
        )


# --------------------------------------------------------------------------
# Patient facts
#
# Minimal on purpose. T-12 owns what a FHIR bundle yields; these exist because
# `PatientStore` cannot be typed without them, and a port returning `dict` is
# not a contract.
# --------------------------------------------------------------------------


class Observation(BaseModel):
    """A structured measurement. BMI is the only one v1 reads (REQ-11)."""

    model_config = ConfigDict(frozen=True)

    code: str
    value: float
    unit: str | None = None
    effective_date: date


class Condition(BaseModel):
    """A coded diagnosis, for the criterion (b) set intersection (REQ-12)."""

    model_config = ConfigDict(frozen=True)

    code: str
    system: str | None = None
    onset_date: date | None = None


class WmEvent(BaseModel):
    """One supervised weight-management encounter extracted from a note.

    The only object in the system a model produces (REQ-8). Everything done to
    it afterwards — month bucketing, run length, window arithmetic — is Python
    (Art. II).

    `bmi` is present only when the note documents a BMI in this encounter. D15
    is explicit that a recorded weight plus a height on file elsewhere does not
    count, because a derived value has no single span to cite, and REQ-40 turns
    on exactly that distinction.
    """

    model_config = ConfigDict(frozen=True)

    event_date: date
    span: EvidenceSpan
    bmi: float | None = None
    bmi_span: EvidenceSpan | None = None
    diet_documented: bool = False
    diet_span: EvidenceSpan | None = None
    activity_documented: bool = False
    activity_span: EvidenceSpan | None = None

    @model_validator(mode="after")
    def _flags_carry_their_own_spans(self) -> WmEvent:
        """REQ-38: each flag carries its own span when true.

        A shared encounter-level span would let one sentence mentioning diet
        substantiate an activity claim it never made.
        """
        for flag, span, name in (
            (self.bmi is not None, self.bmi_span, "bmi"),
            (self.diet_documented, self.diet_span, "diet_documented"),
            (self.activity_documented, self.activity_span, "activity_documented"),
        ):
            if flag and span is None:
                raise ValueError(f"{name} is asserted without a span of its own (REQ-38)")
            if not flag and span is not None:
                raise ValueError(f"{name} is not asserted but carries a span")
        return self


class ProgramAssertion(BaseModel):
    """A claim of program participation with no encounter behind it (REQ-35).

    Never counted as an event. Its only consumer is REQ-31, which turns it into
    `UNSUBSTANTIATED_ASSERTION` so the gap list can say "find the visit notes
    behind this claim" rather than "nothing found". D12.
    """

    model_config = ConfigDict(frozen=True)

    span: EvidenceSpan
    text: str | None = None


# --------------------------------------------------------------------------
# Verdicts and the determination
# --------------------------------------------------------------------------


class CriterionVerdict(str, Enum):
    """Two of Article IV's three states. `ERROR` arrives with T-26.

    `NOT_MET` and `INSUFFICIENT_EVIDENCE` are not synonyms and the article
    forbids collapsing them: evidence that exists and falls outside a required
    window is `NOT_MET`; evidence that cannot be found is
    `INSUFFICIENT_EVIDENCE`, and only the second tells the specialist what to go
    collect.
    """

    MET = "MET"
    NOT_MET = "NOT_MET"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DeterminationOutcome(str, Enum):
    """The overall result. Only the values spec §6 already labels.

    `NOT_COVERED` is a short-circuit outcome (REQ-2, REQ-3) reached without a
    model call and without criterion verdicts. Whether a procedure CMS left to
    the contractor needs a fifth member is **T-36**, and D22 argues it is closer
    to `NO_POLICY_FOUND` than to `NOT_COVERED`.
    """

    MET = "MET"
    NOT_MET = "NOT_MET"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NOT_COVERED = "NOT_COVERED"


class CallMetrics(BaseModel):
    """One model call, measured (Art. X). Numbers in the README come from these."""

    model_config = ConfigDict(frozen=True)

    model: str
    purpose: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_time_ms: float = Field(ge=0)


class CriterionResult(BaseModel):
    """One criterion, adjudicated.

    REQ-5 is enforced here: `MET` and `NOT_MET` carry at least one span, and
    `INSUFFICIENT_EVIDENCE` carries none. The second half matters as much as the
    first — an abstention that ships a span invites a reader to treat it as a
    weak finding rather than as an absence.
    """

    model_config = ConfigDict(frozen=True)

    criterion_id: str = Field(min_length=1)
    verdict: CriterionVerdict
    spans: list[EvidenceSpan] = Field(default_factory=list)
    detail: str | None = None

    @model_validator(mode="after")
    def _spans_match_the_verdict(self) -> CriterionResult:
        substantiated = self.verdict in (
            CriterionVerdict.MET,
            CriterionVerdict.NOT_MET,
        )
        if substantiated and not self.spans:
            raise ValueError(
                f"{self.criterion_id}: {self.verdict.value} carries no span (REQ-5). "
                "A verdict nobody can check is not a verdict."
            )
        if not substantiated and self.spans:
            raise ValueError(
                f"{self.criterion_id}: {self.verdict.value} carries "
                f"{len(self.spans)} span(s) (REQ-5). An abstention cites nothing."
            )
        return self


class GapEntry(BaseModel):
    """One criterion the chart did not carry, and why (REQ-21).

    `gap_reason` — the closed enum that says what to go collect — is T-31.
    """

    model_config = ConfigDict(frozen=True)

    criterion_id: str
    verdict: CriterionVerdict
    detail: str | None = None


class Determination(BaseModel):
    """The reviewable output. Prepared for a human; never submitted (spec §4).

    `gap_list` is computed rather than stored. REQ-21 wants it to name every
    criterion not resolved `MET`, and a stored copy is a second source of truth
    that can disagree with the results it summarizes.
    """

    model_config = ConfigDict(frozen=True)

    patient_id: str
    procedure_code: str
    policy_version_id: str = Field(min_length=1)
    outcome: DeterminationOutcome
    criterion_results: list[CriterionResult] = Field(default_factory=list)
    metrics: list[CallMetrics] = Field(default_factory=list)

    @model_validator(mode="after")
    def _abstention_never_reports_met(self) -> Determination:
        """REQ-20, as an invariant rather than as an aggregation.

        T-19 owns computing the outcome from the decision expression. This only
        refuses the one combination Article IV rules out in every case: an
        unsupported criterion cannot sit under an approval.
        """
        if self.outcome is DeterminationOutcome.MET and any(
            r.verdict is CriterionVerdict.INSUFFICIENT_EVIDENCE
            for r in self.criterion_results
        ):
            raise ValueError(
                "outcome MET over a criterion resolved INSUFFICIENT_EVIDENCE "
                "(REQ-20). Unsupported never becomes met."
            )
        return self

    @property
    def gap_list(self) -> list[GapEntry]:
        return [
            GapEntry(
                criterion_id=r.criterion_id, verdict=r.verdict, detail=r.detail
            )
            for r in self.criterion_results
            if r.verdict is not CriterionVerdict.MET
        ]

    @property
    def total_input_tokens(self) -> int:
        return sum(m.input_tokens for m in self.metrics)

    @property
    def total_output_tokens(self) -> int:
        return sum(m.output_tokens for m in self.metrics)

    @property
    def total_wall_time_ms(self) -> float:
        return sum(m.wall_time_ms for m in self.metrics)

    @property
    def model_calls(self) -> int:
        """The counter E2 and E3 assert reads zero (A4)."""
        return len(self.metrics)
