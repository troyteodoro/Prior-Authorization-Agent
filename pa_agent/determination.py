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
- a contractor-determined code raises inside `resolve_sc1`, citing T-36; that
  raise propagates untouched (D31).

No model is imported here, and nothing in this module could record a call if
one happened — `Determination.metrics` is the only counter and this module
always writes it empty (Art. II).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.contracts import Determination, DeterminationOutcome
from pa_agent.resolver import NoPolicyFound, NotCovered, Resolved, resolve_sc1
from pa_agent.stores.policy import PolicyStore


class NoPolicyResult(BaseModel):
    """REQ-1: no policy governs the code. Not a denial, not a determination.

    Carries no outcome and no `policy_version_id`, on purpose: there is no
    policy to version and nothing was determined (D32).
    """

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)


def determine(
    store: PolicyStore, procedure_code: str, patient_id: str | None = None
) -> Determination | NoPolicyResult:
    """Assemble the determination for a request, as far as the system exists."""
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

    assert isinstance(resolution, Resolved)
    raise NotImplementedError(
        "T-19 has not built the aggregator. "
        f"{resolution.policy_ref.policy_version_id} covers {procedure_code}, so "
        "this request needs the criteria evaluation chain (T-12 through T-19) "
        "before a determination can be assembled."
    )
