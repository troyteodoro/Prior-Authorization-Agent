# Specification — Prior Authorization Determination Agent, v1

**Status:** draft, pending spike 001
**Governed by:** `docs/constitution.md`
**Plan:** `docs/architecture.md`
**Tasks:** `docs/tasks.md`

Requirements are numbered and testable. Every task references at least one REQ.
Every REQ has an acceptance check in the eval harness or the test suite. A REQ
with no check is not a requirement, it is a wish, and it gets deleted.

---

## 1. Purpose

Given a patient record and a requested procedure, produce a reviewable
determination stating, for each criterion in the governing coverage policy,
whether the record supports it, with citations a human can verify.

The system does not submit, does not decide, and does not adjudicate on a
payer's behalf. It prepares a packet and a gap list for a human specialist.

## 2. Primary output

The gap list, not the verdict. The highest-value sentence the system produces is
"criterion c3 is not supported by this chart," because that is actionable before
submission. An overall approve/deny is a summary of the criterion verdicts and
carries less information than they do.

## 3. Scope

**In scope for v1**

- One policy: NCD 100.1, bariatric surgery.
- National coverage only. No MAC jurisdiction resolution.
- Synthetic patients from Synthea plus manifest-driven synthesized notes.
- Local execution. No deployment.

**Explicitly out of scope for v1**

- The automated criteria compiler. The criteria tree is hand-written.
- MCD bulk ingestion.
- More than one policy, any payer-specific overlay, any LCD.
- Vector search or embedding-based retrieval.
- Any user interface.
- Terraform, CI/CD, containers.
- Real or de-identified patient data of any kind.

## 4. Actors

| Actor | Role |
|---|---|
| Specialist | Consumes the determination and gap list. Only human in the v1 loop. |
| Policy author | Reviews and approves criteria tree diffs. Same person as the specialist in v1. |
| System | Retrieves, extracts, adjudicates, verifies, aggregates. Never submits. |

---

## 5. Requirements

### Policy resolution

**REQ-1** Given a procedure code, the resolver returns the governing criteria
tree and its `policy_version_id`, or `NO_POLICY_FOUND`. Deterministic.

**REQ-2** A requested procedure absent from `covered_procedures` returns
`NOT_COVERED` without any model call. *(short-circuit sc1)*

**REQ-3** A patient with type 2 diabetes and BMI below 35 returns `NOT_COVERED`
without any model call. *(short-circuit sc2)*

**REQ-4** Every determination records the `policy_version_id` it was evaluated
against, sufficient to replay the determination later.

### Evidence and citation

**REQ-5** Every criterion verdict other than `INSUFFICIENT_EVIDENCE` carries at
least one span as `(document_id, char_start, char_end)`.

**REQ-6** A span is rejected if slicing the source document at those offsets does
not yield non-empty text. Rejection is by string comparison, no model. *(Art. III)*

**REQ-7** Source documents are immutable once indexed. A document whose content
hash changes invalidates every span into it.

### Extraction

**REQ-8** A single model extraction over the chart notes produces
`wm_events[]` conforming to the schema in `ncd-100-1.v1.json`.

**REQ-9** Extraction excludes non-encounters: missed visits, unsuccessful contact
attempts, prior non-supervised weight loss attempts, and dates appearing in
unrelated sections.

**REQ-10** Every extracted event carries a span satisfying REQ-6.

### Adjudication

**REQ-11** Criterion (a) is evaluated by numeric comparison against the most
recent BMI observation within its lookback window. No model call. *(Art. II)*

**REQ-12** Criterion (b) is evaluated by set intersection against the
`obesity_comorbidities` value set. No model call. *(Art. II)*

**REQ-13** Criteria c1 through c5 are evaluated by deterministic predicates over
`wm_events`. No model call after extraction. *(Art. II)*

**REQ-14** c3 buckets event dates by calendar month and returns the longest run
of consecutive populated months. A run below four is `NOT_MET`.

**REQ-15** c4 is scoped to the qualifying run identified by c3. If c3 fails, c4
returns `INSUFFICIENT_EVIDENCE`, not `NOT_MET`.

**REQ-16** Evidence present but outside a required window yields `NOT_MET`.
Evidence not found yields `INSUFFICIENT_EVIDENCE`. *(Art. IV)*

### Verification

**REQ-17** Each accepted verdict is checked by a verifier receiving only the
criterion text, the span, and the claimed verdict. *(Art. V)*

**REQ-18** A rejected verdict triggers retrieval retry. After N attempts the
criterion resolves to `INSUFFICIENT_EVIDENCE`. N is configurable and recorded.

**REQ-23** A criterion resolves to `ERROR` when the system fails to evaluate it:
model API failure, timeout, response that fails schema validation, or an
unhandled exception in a predicate. `ERROR` is distinct from `NOT_MET` and
`INSUFFICIENT_EVIDENCE` and is never collapsed into either.

**REQ-24** An `ERROR` on any criterion aborts the determination. No determination
containing an `ERROR` is presented to a specialist. The underlying exception is
surfaced, not swallowed.

### Aggregation

**REQ-19** The overall result is computed by evaluating the policy's boolean
expression over criterion verdicts, in Python. *(Art. I)*

**REQ-20** Any criterion resolving to `INSUFFICIENT_EVIDENCE` propagates to an
overall result of `INSUFFICIENT_EVIDENCE`, never `MET`.

**REQ-21** The determination includes a gap list naming every criterion not
resolved `MET`, with the reason.

### Instrumentation

**REQ-22** Every model call records model name, input tokens, output tokens, and
wall time. Totals appear on the determination. *(Art. X)*

**REQ-25** Retrieval recall is measured per criterion on the eval set: the
fraction of cases where the retrieved set contains the span holding the
ground-truth fact. Reported in `eval/report.md`.

---

## 6. Edge cases

Each of these becomes a labeled eval case. This list is the eval set's outline.

| # | Case | Expected |
|---|---|---|
| E1 | Clean approval, all criteria met | `MET` |
| E2 | BMI 33 with T2DM | `NOT_COVERED` via REQ-3, zero model calls |
| E3 | Non-covered procedure requested | `NOT_COVERED` via REQ-2, zero model calls |
| E4 | Program run of 3 consecutive months, gap in month 4 | c3 `NOT_MET` |
| E5 | Program complete but ended 14 months ago | c2 `NOT_MET` |
| E6 | Weight recorded monthly, BMI recorded in 2 of 4 months | c4 `NOT_MET` |
| E7 | No weight-management documentation anywhere | c1 `INSUFFICIENT_EVIDENCE` |
| E8 | Note asserts program completion; no visit detail | c3 `INSUFFICIENT_EVIDENCE` |
| E9 | Missed-visit dates present in the gap month | c3 `NOT_MET`, not fooled |
| E10 | Structured record and note disagree on BMI | structured wins, disagreement flagged |
| E11 | Two supervised programs, one qualifying, one not | `MET` on the qualifying run |
| E12 | BMI exactly 35.0 | c(a) `MET`, boundary inclusive |

E8 is the refusal test. E9 and E10 are the honesty tests. E2 and E3 are the
determinism tests and should complete in milliseconds.

---

## 7. Acceptance criteria

v1 is done when all of the following hold on the labeled eval set.

| Gate | Threshold |
|---|---|
| A1 | Every case in §6 present and labeled |
| A2 | Per-criterion precision ≥ 0.90 on `MET` verdicts, reported alongside the `MET` base rate in the eval set and the precision of a trivial always-`MET` baseline |
| A3 | Zero `MET` verdicts with an invalid span |
| A4 | E2 and E3 complete with zero model calls |
| A5 | Abstention rate reported, with the coverage/accuracy curve |
| A6 | Cost and latency per determination reported from instrumentation |
| A7 | Every REQ mapped to a passing check |
| A8 | Failure modes documented in the README, including where the system degrades |
| A9 | Zero determinations presented with a criterion in `ERROR` state |

A2 is asymmetric on purpose. A false `MET` produces a denial the specialist did
not expect. A false `NOT_MET` produces an unnecessary chart review. The first is
worse, so precision on `MET` is gated and recall is only reported.

A2 also carries a baseline because 0.90 alone is not a result. On an eval set
where 0.90 of cases are truly `MET`, a system that answers `MET` unconditionally
clears the gate while knowing nothing. The base rate and the always-`MET` score
are what make the measured number mean something, so they are reported next to
it rather than left to the reader to reconstruct.

A8 is not a formality. A README that names the point at which the system becomes
useless is the deliverable.

---

## 8. Validation method

- Deterministic requirements: `pytest`.
- Model-dependent requirements: the eval harness over labeled cases.
- Span validity: mechanical, asserted on every case.
- The harness is written before the components it grades. A failing harness is
  the correct state on day two.

---

## 9. Open questions

1. Does A53028 require diet and activity documentation monthly or once? c5
   assumes once. Resolve against source text before labeling.
2. Does c4 accept weight-only documentation when height is on file and BMI is
   computable? Currently no.
3. When structured BMI and note BMI disagree, structured wins. Is that right
   clinically, or should the discrepancy block the determination? *(E10)*
4. Should the gap list rank gaps by how easily the specialist can close them?
   Out of scope for v1, likely the most useful v2 feature.
