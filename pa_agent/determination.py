"""T-25 — determination assembly, minimal: sc1 becomes a reviewable artifact.

One function turns a resolution into the thing US-1 ships: request in,
determination out. Today it assembles exactly the paths that exist —

- `NotCovered` → a `Determination` with outcome `NOT_COVERED`, the governing
  `policy_version_id` (REQ-4), the spanned coverage claim it denies under
  (Art. III), an empty gap list (REQ-21 — nothing was evaluated, nothing is
  missing), and an empty metrics list, so the model-call counter reads zero by
  construction rather than by restraint (REQ-2, A4).
- `NoPolicyFound` → `NoPolicyResult`, which is deliberately **not** a
  `Determination`: REQ-4 requires every determination to record the policy
  version it was evaluated against, and a code no policy governs has none.
  US-1 asks for this "rather than a denial", and the type system is where that
  distinction cannot be lost (D32, D26).
- `Resolved` → `NotImplementedError` citing T-19. A covered code needs the
  criteria evaluation chain, and the aggregator that turns criterion results
  into a determination is T-19's.
- `ResolvedByContractor` → the same `NotImplementedError`, because the flow is
  the same chain (REQ-42, D33) — with one obligation named for T-19: the
  assembled determination must cite the NCD's delegation *and* the MAC's
  exercise of it, never the NCD alone, since the NCD deliberately does not
  answer for this procedure.

No model is imported here, and nothing in this module could record a call if
one happened — `Determination.metrics` is the only counter and this module
always writes it empty (Art. II).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from datetime import date

from pa_agent.contracts import Determination, DeterminationOutcome
from pa_agent.criteria import evaluate_sc2
from pa_agent.resolver import (
    NoPolicyFound,
    NotCovered,
    Resolved,
    ResolvedByContractor,
    resolve_sc1,
)
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyStore


class NoPolicyResult(BaseModel):
    """REQ-1: no policy governs the code. Not a denial, not a determination.

    Carries no outcome and no `policy_version_id`, on purpose: there is no
    policy to version and nothing was determined (D32).
    """

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)


def determine(
    store: PolicyStore,
    procedure_code: str,
    patient_id: str | None = None,
    patient_store: PatientStore | None = None,
    as_of: date | None = None,
) -> Determination | NoPolicyResult:
    """Assemble the determination for a request, as far as the system exists.

    sc1 needs only the policy store. sc2 (REQ-3, D41) additionally needs the
    patient's structured facts and an explicit `as_of` — both optional here
    because sc1's answers exist without them, and a covered code without them
    raises the same T-19 message it always did.
    """
    resolution = resolve_sc1(store, procedure_code)

    if isinstance(resolution, NotCovered):
        return Determination(
            patient_id=patient_id,
            procedure_code=resolution.procedure_code,
            policy_version_id=resolution.policy_version_id,
            outcome=DeterminationOutcome.NOT_COVERED,
            coverage_claim=resolution.coverage_claim,
            criterion_results=[],
            metrics=[],
        )

    if isinstance(resolution, NoPolicyFound):
        return NoPolicyResult(procedure_code=resolution.procedure_code)

    if isinstance(resolution, ResolvedByContractor):
        raise NotImplementedError(
            "T-19 has not built the aggregator. "
            f"{resolution.policy_ref.policy_version_id} covers {procedure_code} "
            "by the MAC's exercise of the NCD's delegation (REQ-42, D33), so "
            "this request needs the criteria evaluation chain (T-12 through "
            "T-19) — and the determination it assembles must cite both the "
            "delegation and the MAC's exercise, never the NCD alone."
        )

    assert isinstance(resolution, Resolved)
    # sc2 runs before the criteria chain, and only for the nationally covered
    # set — the 04/2009 exclusion names exactly those procedures and predates
    # the LSG delegation, so contractor requests skip it (D41).
    if patient_id is not None and patient_store is not None and as_of is not None:
        tree = store.get_tree(resolution.policy_ref.policy_version_id)
        lookback = tree.criterion("a").require("lookback_months")
        observations = patient_store.get_observations(patient_id)
        conditions = patient_store.get_conditions(patient_id)
        for exclusion in tree.categorical_exclusions:
            if exclusion.procedure_scope != "nationally_covered":
                continue
            match = evaluate_sc2(
                exclusion, observations, conditions, as_of, lookback
            )
            if match is not None:
                return Determination(
                    patient_id=patient_id,
                    procedure_code=procedure_code,
                    policy_version_id=resolution.policy_ref.policy_version_id,
                    outcome=DeterminationOutcome.NOT_COVERED,
                    coverage_claim=match.claim,
                    exclusion_evidence=match.evidence,
                    criterion_results=[],
                    metrics=[],
                )
    raise NotImplementedError(
        "T-19 has not built the aggregator. "
        f"{resolution.policy_ref.policy_version_id} covers {procedure_code}, so "
        "this request needs the criteria evaluation chain (T-12 through T-19) "
        "before a determination can be assembled."
    )
