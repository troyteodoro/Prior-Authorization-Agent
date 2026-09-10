# Constitution

Constraints that bind every task. These are not preferences and they are not
revisited per-task. A task that cannot be completed without violating an article
is a wrong task, and the response is to change the task.

Amendments are appended with a date and a reason. Articles are never silently
edited.

---

## Article I — No model decides control flow

The execution graph is fixed and written in Python. Models are invoked at
declared leaves only. Nothing routes, branches, loops, or terminates based on
model output.

*Rationale:* hallucinated routing, infinite loops, and runaway cost are the three
ways agent systems fail in production, and all three are downstream of letting a
model steer.

## Article II — No model performs a deterministic computation

Date arithmetic, threshold comparison, counting, set membership, sorting, and
boolean combination are code. A model extracts and structures; it does not
calculate.

*Test:* if the same input could produce a different verdict on a second run, the
computation is in the wrong place.

## Article III — Every claim carries a span, and spans are verified mechanically

No verdict is accepted without `(document_id, char_start, char_end)`. Before
acceptance, Python slices the source document at those offsets and confirms the
span exists and is non-empty. A fabricated citation fails on string comparison,
not on judgment.

Semantic sufficiency is a separate, later check. Mechanical validity comes first
because it is free.

## Article IV — Fail closed, and INSUFFICIENT is a success state

Unsupported becomes `INSUFFICIENT_EVIDENCE`. It never becomes `MET`. A
determination that reports an honest gap is a correct determination, because the
gap tells the specialist what documentation to collect.

`NOT_MET` and `INSUFFICIENT_EVIDENCE` are distinct and never collapsed.
Evidence that exists but falls outside a required window is `NOT_MET`. Evidence
that cannot be found is `INSUFFICIENT_EVIDENCE`.

## Article V — The verifier is blind

The verifier receives the criterion, the cited span, and the claimed verdict.
It does not receive the adjudicator's reasoning, other criteria, or the rest of
the chart. A verifier that sees the reasoning ratifies it.

## Article VI — Planes do not cross

The policy plane has no read access to patient data. The patient plane holds no
policy corpus and no retrieval index over it. The only object crossing the
boundary is a compiled criterion.

This is stated as the smaller, true claim. The criterion text does cross. The
policy corpus and its index do not.

## Article VII — Policy is a file under review

Criteria trees live in the repo. A change to a clinical rule arrives as a diff
with a reviewer. No policy change reaches a determination without human approval.

## Article VIII — No task closes without a passing exit condition

Every task carries a command that returns zero. "Looks right" is not an exit
condition. A task without a runnable check is not yet specified.

## Article IX — Decisions are logged before they are built

An entry in `docs/decisions.md` precedes the implementation it justifies. The
entry names the rejected alternative and the condition that would reverse the
choice.

## Article X — Cost and latency are measured, never estimated

Token counts and wall time are recorded per run from the first model call.
Numbers in the README come from instrumentation.

---

## Amendments

## Amendment 1 — Bounded model-directed orchestration and adjudication — 2026-09-09

Articles I and II bind the deterministic implementation as written. This amendment
opens a **second, separately measured path** alongside it — the agentic path built
under T-61 — and states what is permitted there and what is not. It adds a path; it
removes nothing.

On the agentic path the model may select allowlisted tools, choose among declared
workflow steps, request additional evidence, retry within a fixed budget, and
terminate a run. It may interpret clinical evidence, evaluate criteria, and
propose criterion-level and overall outcomes.

Deterministic Python remains authoritative, on both paths, for:

- schema and input validation;
- policy-version selection;
- date arithmetic and window calculation;
- numeric comparisons;
- counting, sorting, and set membership;
- evidence-span validation;
- tool allowlists, timeouts, retry limits, and step budgets;
- serialization and final output-shape validation.

The model may not invent tools, policy rules, criteria, policy versions, evidence,
or citations. It may not modify policy artifacts or bypass evidence validation.

Every model-determined criterion and overall outcome must carry supporting,
mechanically verified evidence spans or explicitly resolve to
`INSUFFICIENT_EVIDENCE`. Malformed, contradictory, unsupported, unverifiable, or
budget-exhausted model output resolves to `ERROR` or human review; it never
silently becomes approval or denial.

Every agentic run records the model version, prompt/version identifier, tool-call
trace, selected workflow steps, retries, token usage, latency, termination reason,
policy version, and final outcome.

**The deterministic implementation is the regression oracle**, and stays so unless a
later amendment says otherwise. The agentic path is evaluated against it on
identical cases, and a disagreement is reported rather than reconciled.

*Scope note (2026-09-09):* this amendment was first drafted as an in-place rewrite
of Articles I, II, IV, V, VII and X. That rewrite was reverted and the amendment
recorded here instead — the preamble above says articles are never silently edited,
and an amendment that deletes the constraint it amends leaves nothing to measure the
new path against. The permissions above reach the agentic path only. Anything
outside it — `pa_agent/workflow.py`, `pa_agent/aggregate.py`, `pa_agent/criteria.py`,
`pa_agent/reconcile.py`, `pa_agent/resolver.py` — is governed by Articles I and II
as written. *(D62)*
