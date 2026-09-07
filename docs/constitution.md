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

*(none yet)*
