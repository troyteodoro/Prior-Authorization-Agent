"""T-25 — determination assembly: the request's front door, and the two short
circuits that answer without a patient or a model.

*Extended by T-18 and T-19 (D62): the covered paths no longer raise.*

One function turns a resolution into the thing US-1 ships. All four of sc1's
answers, plus sc2, plus the criteria chain:

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
- `Resolved` → sc2 first (REQ-3, D41), then T-18's criteria graph.
- `ResolvedByContractor` → the same graph, skipping sc2 (REQ-42, D33). The
  determination it assembles cites the NCD's delegation *and* the MAC's exercise
  of it, never the NCD alone, since the NCD deliberately declines to answer for
  this procedure — those two quotes ride on the ref's `coverage_claim`.

**The ordering is the Article II claim worth reading twice.** Both short circuits
run before `_criteria_determination` exists in the call stack, so E2 and E3 reach
an answer without an extraction runner having been constructed, let alone called.
A4 is a property of this function's shape, not of a counter someone remembers to
check — and `tests/test_workflow.py` proves it by handing in a runner that raises.

No model is imported here. `pa_agent.workflow` is, and it holds the one model leaf
behind the `ExtractionRunner` port (REQ-52) — so the ADK stays in `pa_agent.agent`
and this module still imports nothing from `google` (Art. I's boundary, D16).
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
from pa_agent.runners import ExtractionRunner
from pa_agent.verifier import VerifierRunner
from pa_agent.stores.patient import PatientStore
from pa_agent.stores.policy import PolicyStore
from pa_agent.workflow import run_criteria_workflow


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
    extraction_runner: ExtractionRunner | None = None,
    verifier: VerifierRunner | None = None,
) -> Determination | NoPolicyResult:
    """Assemble the determination for a request.

    sc1 needs only the policy store. sc2 (REQ-3, D41) additionally needs the
    patient's structured facts and an explicit `as_of`. A covered or
    contractor-determined code additionally needs an `extraction_runner`, because
    c1 through c5 adjudicate what the notes say (REQ-52, T-18).

    Every argument after `procedure_code` stays optional, and that is not
    laziness: sc1's two answers are facts about the *procedure*, reachable with no
    patient and no model, and a signature that demanded a patient to learn that
    43842 is non-covered would make E3 impossible to ask. What is refused is
    answering a criteria request without them — see `_criteria_determination`.
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
        # REQ-42: same chain as `Resolved` (the criteria are the MAC's own), and
        # sc2 is skipped — the 04/2009 exclusion names the nationally covered
        # procedures and predates the 2012 LSG delegation, so it does not reach a
        # procedure CMS had not yet delegated when it was written (D41).
        return _criteria_determination(
            store,
            patient_store,
            resolution.policy_ref,
            patient_id,
            as_of,
            extraction_runner,
            verifier,
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

    return _criteria_determination(
        store,
        patient_store,
        resolution.policy_ref,
        patient_id,
        as_of,
        extraction_runner,
        verifier,
    )


def _criteria_determination(
    store: PolicyStore,
    patient_store: PatientStore | None,
    policy_ref,
    patient_id: str | None,
    as_of: date | None,
    extraction_runner: ExtractionRunner | None,
    verifier: VerifierRunner | None,
) -> Determination:
    """Hand a covered request to T-18's graph, once its inputs are all present.

    The three guards below raise rather than substituting a default, and each one
    is a lesson this repo already paid for. D31: `resolve` returning `None`
    reports `NO_POLICY_FOUND` for all of Medicare. D39: a patient store returning
    `[]` manufactures E7 for every patient. Same shape here — a missing extraction
    runner that silently produced no events would adjudicate every patient as
    having no weight-management history, `c1` would abstain with
    `NO_EVIDENCE_RETRIEVED`, and **every downstream test would agree with it.**

    So the message names what is missing and what to pass, and the caller
    chooses. `pa_agent.cli` constructs one; `eval/run_eval.py` passes one; a test
    that wants to prove a path spends no model call passes
    `NullExtractionRunner`, which raises if anything reaches it.
    """
    if patient_id is None or patient_store is None or as_of is None:
        raise NotImplementedError(
            f"{policy_ref.policy_version_id} covers {policy_ref.procedure_code}, "
            "so this request runs the criteria chain and needs a patient. Pass "
            "patient_id, patient_store and as_of. Answering without them would "
            "adjudicate a chart nobody read."
        )
    if extraction_runner is None:
        raise NotImplementedError(
            f"{policy_ref.policy_version_id} covers {policy_ref.procedure_code}, "
            "so this request reads the patient's notes and needs an "
            "extraction_runner (REQ-52). Pass RecordedExtractionRunner for a "
            "replay of eval/extraction/results.json, DirectExtractionRunner or "
            "AdkExtractionRunner to spend a call. Defaulting to no extraction "
            "would report every patient as having no documented program (D31, "
            "D39's lesson)."
        )

    # `verifier` has no guard here on purpose: whether verification is
    # needed depends on whether the chain produces a cited verdict, which only
    # `step_verify` knows. It raises there, naming what to pass (T-17, D78).
    run = run_criteria_workflow(
        policy_store=store,
        patient_store=patient_store,
        extraction_runner=extraction_runner,
        policy_ref=policy_ref,
        patient_id=patient_id,
        as_of=as_of,
        verifier=verifier,
    )
    return run.determination
