"""T-24 — short-circuit sc1: membership becomes an answer, and the answers stay four.

The store reports facts — which policy binds a code, and in which of its three
sets (D31). This module owns the judgment REQ-1, REQ-2 and REQ-42 describe:

- bound in the nationally non-covered set → `NotCovered`, carrying the
  `policy_version_id` a determination must record (REQ-4) and the spanned
  coverage claim it must cite (Art. III). Reached with zero model calls.
- bound in the nationally covered set → `Resolved`; the caller proceeds to the
  criteria tree.
- bound in the contractor-determined set → `ResolvedByContractor` (REQ-42,
  D33). Same flow as `Resolved` — under this corpus the MAC exercised the
  delegation and covers the procedure, so it proceeds to the MAC's own
  criteria — but a distinct type, because "covered because CMS says so" and
  "covered because the MAC chose to" diverge the moment a second jurisdiction
  exists, and a caller must acknowledge the delegation before treating the
  code as covered.
- bound nowhere → `NoPolicyFound`. No bariatric policy governs the code, which
  is a different answer from a policy saying no (D26).

The results are distinct types rather than one type with a status string, so
"a policy says no", "no policy says anything" and "delegated, and the MAC
answered" stay separate lookups a caller has to acknowledge — D26's collapse
cannot be rebuilt by ignoring a field.

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


class ResolvedByContractor(BaseModel):
    """REQ-42: the NCD delegates this procedure, and the governing MAC covers it.

    Proceeds to the criteria tree the way `Resolved` does — the criteria are
    the MAC's own — but the type is distinct so no caller can treat the
    delegation as a national grant without noticing (D33). The determination
    built downstream must cite both the delegation and the MAC's exercise,
    carried on the ref's `coverage_claim` and its corroborating quote (T-19).
    """

    model_config = ConfigDict(frozen=True)

    policy_ref: PolicyRef


class NoPolicyFound(BaseModel):
    """REQ-1: no policy in the store binds this code. Not a denial (D26)."""

    model_config = ConfigDict(frozen=True)

    procedure_code: str = Field(min_length=1)


def resolve_sc1(
    store: PolicyStore, procedure_code: str
) -> NotCovered | Resolved | ResolvedByContractor | NoPolicyFound:
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

    # CoverageStatus.CONTRACTOR_DETERMINED — the third outcome (REQ-42, D33).
    return ResolvedByContractor(policy_ref=ref)
