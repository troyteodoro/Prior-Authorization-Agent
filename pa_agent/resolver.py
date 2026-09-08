"""T-24 — short-circuit sc1: membership becomes an answer, and the undecided branch raises.

The store reports facts — which policy binds a code, and in which of its three
sets (D31). This module owns the judgment REQ-1 and REQ-2 describe:

- bound in the nationally non-covered set → `NotCovered`, carrying the
  `policy_version_id` a determination must record (REQ-4) and the spanned
  coverage claim it must cite (Art. III). Reached with zero model calls.
- bound in the nationally covered set → `Resolved`; the caller proceeds to the
  criteria tree.
- bound nowhere → `NoPolicyFound`. No bariatric policy governs the code, which
  is a different answer from a policy saying no (D26).

The three results are three types rather than one type with a status string, so
"a policy says no" and "no policy says anything" stay two lookups a caller has
to acknowledge separately — D26's collapse cannot be rebuilt by ignoring a
field.

**The contractor-determined branch raises, citing T-36.** Whether "left to the
contractor" is a third sc1 outcome or resolves like a covered code is T-36's
open question, and an undecided half raises and names its task rather than
picking quietly (D31). Do not "fix" the raise by choosing either mapping here.

No model is imported anywhere in this module, the control flow is fixed Python
(Art. I), and every branch is a deterministic computation over store facts
(Art. II).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pa_agent.contracts import CoverageClaim, CoverageStatus
from pa_agent.stores.policy import PolicyRef, PolicyStore


class NotCovered(BaseModel):
    """REQ-2: the governing policy names this procedure non-covered.

    A determination built from this is `NOT_COVERED` with zero model calls and
    no criterion results (spec E3); the claim below is what it cites.
    """

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)
    policy_version_id: str = Field(min_length=1)
    procedure: str = Field(min_length=1)
    coverage_claim: CoverageClaim


class Resolved(BaseModel):
    """REQ-1: a policy governs the code and coverage runs the criteria tree."""

    model_config = ConfigDict(frozen=True)

    policy_ref: PolicyRef


class NoPolicyFound(BaseModel):
    """REQ-1: no policy in the store binds this code. Not a denial (D26)."""

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)


def resolve_sc1(
    store: PolicyStore, procedure_code: str
) -> NotCovered | Resolved | NoPolicyFound:
    """Resolve a code through the policy port and apply short-circuit sc1."""
    ref = store.resolve(procedure_code)
    if ref is None:
        return NoPolicyFound(procedure_code=procedure_code)

    if ref.coverage is CoverageStatus.NATIONALLY_NON_COVERED:
        return NotCovered(
            procedure_code=ref.procedure_code,
            policy_version_id=ref.policy_version_id,
            procedure=ref.procedure,
            coverage_claim=ref.coverage_claim,
        )

    if ref.coverage is CoverageStatus.NATIONALLY_COVERED:
        return Resolved(policy_ref=ref)

    # CoverageStatus.CONTRACTOR_DETERMINED — deliberately unmapped.
    raise NotImplementedError(
        "T-36 has not decided whether a contractor-determined procedure is a "
        "third sc1 outcome or resolves like a covered code. The store reports "
        f"the membership fact for {procedure_code}; this module refuses to map "
        "it until that decision is written (D31)."
    )
