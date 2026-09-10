"""T-61 — the retrieval port, and the fixed planner behind it (D63).

T-18's graph has a port at its model half (`ExtractionRunner`, REQ-52). This is
the same shape at its retrieval half: what evidence reaches the criteria, and who
decided to fetch it.

    RetrievalPlanner: gather(...) -> RetrievalResult
        FixedRetrievalPlanner     three store reads in a fixed order
        AgenticRetrievalPlanner   the model chooses; in `pa_agent.agent`

Everything downstream — extraction, reconciliation, the seven criteria,
aggregation — is untouched and **cannot tell which planner ran**. That is the
whole point: one variable changes, which is what makes the differential a
comparison rather than two systems being different in unknown ways.

**Why retrieval is where the agentic path lives.** Amendment 1 reserves date
arithmetic, numeric comparisons, counting, sorting and set membership to Python on
both paths, and that is the entire decision procedure for all seven criteria. So
the model cannot decide a verdict without doing something the amendment reserves.
It can decide *what to go and read*, and that decision has real consequences: a
skipped note leaves c3 measuring a shorter run, forgotten observations make
criterion (a) abstain, and a document fetched four times costs four times as much
for the same answer. Each of those produces a determination that is entirely
well-formed and quietly wrong (D63).

This module imports no `google` anything. The agentic planner lives in
`pa_agent.agent`, which is the subpackage that makes the Article I boundary a path
rather than a convention (D16).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pa_agent.contracts import (
    Condition,
    CriteriaTree,
    Document,
    Observation,
    RunTrace,
)
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyStore


class RetrievalError(ValueError):
    """A planner could not produce a usable evidence bundle.

    Raised rather than returning an empty bundle, and the reason is the one this
    repo has now paid for three times. `LocalPolicyStore.resolve` returning `None`
    reports `NO_POLICY_FOUND` for all of Medicare (D31). A patient store returning
    `[]` manufactures E7 for every patient (D39). A planner returning nothing makes
    every patient look like a chart with no weight-management history — c1 abstains
    with `NO_EVIDENCE_RETRIEVED`, the determination is `INSUFFICIENT_EVIDENCE`, and
    **it is a perfectly well-formed answer that every downstream test agrees with.**

    "The model did not look" and "the chart does not say" are different answers and
    the system must not be able to confuse them.
    """


@dataclass
class RetrievalResult:
    """The evidence a planner gathered, and how it got there.

    Deliberately the same shape whatever gathered it. A field the agentic planner
    fills and the fixed one leaves empty would be a field the criteria could branch
    on, and then the two paths would no longer be comparable.
    """

    observations: list[Observation] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    value_set: frozenset[str] = frozenset()
    notes: list[Document] = field(default_factory=list)
    #: `None` for a planner that made no model call, rather than an empty trace —
    #: an empty trace implies a run that recorded nothing (the distinction D62
    #: drew for `ExtractionResult.trace`).
    trace: RunTrace | None = None

    @property
    def document_ids(self) -> list[str]:
        return [note.document_id for note in self.notes]


@runtime_checkable
class RetrievalPlanner(Protocol):
    """Decides what evidence a request is adjudicated on.

    `name` is recorded on the run, so a determination's provenance says which
    planner assembled its inputs. A fixed run and a model-directed run are not the
    same claim and the artifact must not read as though they were.
    """

    name: str

    def gather(
        self,
        patient_id: str,
        tree: CriteriaTree,
        patient_store: PatientStore,
        policy_store: PolicyStore,
    ) -> RetrievalResult:
        """Assemble the evidence bundle for one patient.

        Raises `RetrievalError` when it cannot. It must never return an empty
        bundle to signal failure — see `RetrievalError`.
        """
        ...


class FixedRetrievalPlanner:
    """Three store reads, in a fixed order. T-18's original load steps, verbatim.

    The oracle's half of the comparison, and it stays exactly as deterministic as
    it was: no model, no choice, no branch. Everything it reads is everything the
    ports will give it, because filtering is the predicates' judgment (D39) and a
    filter applied twice is a filter free to disagree with itself.
    """

    name = "fixed"

    def gather(
        self,
        patient_id: str,
        tree: CriteriaTree,
        patient_store: PatientStore,
        policy_store: PolicyStore,
    ) -> RetrievalResult:
        # The value set's id comes from criterion (b)'s own constant, so no caller
        # writes a code-system literal and the policy names what it needs (D52).
        value_set_id = tree.criterion("b").require("value_set_id")
        return RetrievalResult(
            observations=patient_store.get_observations(patient_id),
            conditions=patient_store.get_conditions(patient_id),
            value_set=policy_store.get_value_set(value_set_id),
            notes=patient_store.get_notes(patient_id),
        )
