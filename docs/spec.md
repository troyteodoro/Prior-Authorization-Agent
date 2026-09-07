# Specification — Prior Authorization Determination Agent, v1

**Status:** draft, pending spike 001
**Governed by:** `docs/constitution.md`
**Stories:** `docs/stories.md` · **Tasks:** `docs/tasks.md` · **Rationale:** `docs/decisions.md`

Requirements are numbered and testable. Every task references at least one REQ.
Every REQ has an acceptance check in the eval harness or the test suite. A REQ
with no check is not a requirement, it is a wish, and it gets deleted.

Numbering is not contiguous. IDs are stable because other documents reference
them; a split becomes REQ-18a rather than a renumber.

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

**REQ-33** The policy plane and the patient plane share no module-level
dependency. Policy modules import nothing from patient-data modules, and
patient-data modules import no policy corpus and no index over it. The only type
crossing is the compiled `Criterion`, enforced by an import-graph assertion.
*(Art. VI)*

### Evidence and citation

**REQ-5** A criterion verdict of `MET` or `NOT_MET` carries at least one span as
`(document_id, char_start, char_end)`. `INSUFFICIENT_EVIDENCE` and `ERROR` carry
none. *(Art. III)*

**REQ-6** A span is rejected if slicing the source document at those offsets does
not yield non-empty text. Rejection is by string comparison, no model. *(Art. III)*

**REQ-7** Source documents are immutable once indexed. A document whose content
hash changes invalidates every span into it.

### Extraction

**REQ-8** A single model extraction over the chart notes produces `wm_events[]`
conforming to the schema in `ncd-100-1.v1.json`.

**REQ-9** Extraction excludes non-encounters: missed visits, unsuccessful contact
attempts, prior non-supervised weight loss attempts, and dates appearing in
unrelated sections.

**REQ-10** Every extracted event carries a span satisfying REQ-6.

**REQ-35** Extraction records `program_assertions[]` alongside `wm_events[]`:
spans where a note claims program participation or completion without documenting
an encounter. An assertion is never counted as an event. Its only consumer is
REQ-31. *(D12)*

**REQ-38** Each `wm_event` carries `diet_documented` and `activity_documented`,
optional booleans defaulting false, each with its own span when true. *(D13)*

### Adjudication

**REQ-11** Criterion (a) is evaluated by numeric comparison against the most
recent BMI observation within its lookback window. No model call. *(Art. II)*

**REQ-12** Criterion (b) is evaluated by set intersection against the
`obesity_comorbidities` value set. No model call. *(Art. II)*

**REQ-13** Criteria c1 through c5 are evaluated by deterministic predicates over
`wm_events`. No model call after extraction. *(Art. II)*

**REQ-36** c1 is `MET` when `wm_events[]` holds at least one event; REQ-9 already
excludes unsupervised attempts, so membership is the supervision claim. Zero
events is `INSUFFICIENT_EVIDENCE`, never `NOT_MET`. *(D13)*

**REQ-32** c2 is `MET` when the qualifying run identified by c3 ended within the
recency window declared in the criteria tree. A run ending outside the window is
`NOT_MET`. The window length is never hardcoded. *(Art. VII)*

**REQ-14** c3 buckets event dates by calendar month and returns the longest run
of consecutive populated months. A run of one to three months is `NOT_MET`;
zero events is `INSUFFICIENT_EVIDENCE`. *(D12)*

**REQ-40** c4 is `MET` when every month of the qualifying run contains an
encounter with a documented BMI. A recorded weight with a height on file
elsewhere does not count — a derived value has no single span to cite. *(D15)*

**REQ-37** c5 is `MET` when the qualifying run contains at least
`c5_min_documented_events` events carrying both `diet_documented` and
`activity_documented`. The count is read from the criteria tree. *(Art. VII, D13)*

**REQ-15** c4 and c5 are scoped to the qualifying run identified by c3. If c3
fails, both return `INSUFFICIENT_EVIDENCE`, not `NOT_MET`.

**REQ-16** Evidence present but outside a required window yields `NOT_MET`.
Evidence not found yields `INSUFFICIENT_EVIDENCE`. *(Art. IV)*

**REQ-34** When a structured value and a note-extracted value for the same fact
disagree, the structured value is authoritative and the discrepancy is recorded
per REQ-39. If the two fall on opposite sides of the criterion's threshold, the
criterion instead resolves `INSUFFICIENT_EVIDENCE` with `gap_reason`
`SOURCE_CONFLICT`. The comparison runs in Python. Reconciliation happens after
extraction and may downgrade a verdict criterion (a) already produced; criterion
(a)'s own evaluation stays model-free. *(Art. II, D11)*

### Verification

**REQ-17** Each accepted verdict is checked by a verifier receiving only the
criterion text, the span, and the claimed verdict. *(Art. V)*

**REQ-18** A verdict rejected by the verifier resolves the criterion to
`INSUFFICIENT_EVIDENCE` on the first rejection. No retry, no `error_code`, and
the determination is still emitted with the criterion on the gap list.
*(Art. IV, D9)*

**REQ-18a** A model call failing with an `error_code` classified retryable under
REQ-30 consumes an attempt and is retried. N is configurable and recorded.
Exhaustion resolves the criterion to `ERROR` with `MODEL_CALL_FAILED`. A terminal
code resolves to `ERROR` on first occurrence without retrying. *(D8)*

REQ-18 and REQ-18a are distinct loops. A verifier rejection means the system
could not substantiate a claim; a call failure means it could not evaluate.

**REQ-31** `INSUFFICIENT_EVIDENCE` carries a `gap_reason` from a closed enum:

| Value | Meaning | Next action | Case |
|---|---|---|---|
| `NO_EVIDENCE_RETRIEVED` | Nothing found for this criterion | Find documentation of a program | E7 |
| `UNSUBSTANTIATED_ASSERTION` | A claim was found, no encounter behind it | Find the visit notes behind the claim | E8 |
| `VERIFIER_REJECTED` | A span was found and did not support the verdict | Re-read the cited passage | — |
| `SOURCE_CONFLICT` | Two sources disagreed across a threshold | Reconcile the two values | E10b |

The enum exists to say what to go collect. Two values producing the same action
are one value. *(D9, D11, D12)*

**REQ-23** A criterion resolves to `ERROR` when evaluation could not complete: a
model call failure surviving REQ-18a, a response failing schema validation, a
span validator exception, or an unhandled exception in a predicate. `ERROR` is
distinct from `NOT_MET` and `INSUFFICIENT_EVIDENCE`. *(D7)*

**REQ-24** An `ERROR` on any criterion aborts the determination. The underlying
exception is surfaced, not swallowed.

**REQ-26** A `Determination` containing any `ERROR` criterion cannot be
constructed. Enforced by a model validator, not by convention.

**REQ-27** No bare `except` clauses. Every handler either re-raises or maps to a
named `error_code`.

**REQ-29** The CLI exits non-zero when any criterion resolves to `ERROR` and
writes the `error_code` and criterion id to stderr.

**REQ-30** Every `ERROR` carries an `error_code` from a closed enum and the
exception text. Free-text reasons are not permitted. Each member is classified:

| Code | Class |
|---|---|
| `MODEL_CALL_FAILED` | retryable |
| `SOURCE_UNAVAILABLE` | retryable |
| `SCHEMA_INVALID` | terminal |
| `SPAN_VALIDATION_FAILED` | terminal |
| `PREDICATE_EXCEPTION` | terminal |

A member added without a classification defaults to terminal. *(D8)*

### Aggregation

**REQ-19** The overall result is computed by evaluating the policy's boolean
expression over criterion verdicts, in Python. *(Art. I)*

**REQ-20** Any criterion resolving to `INSUFFICIENT_EVIDENCE` propagates to an
overall result of `INSUFFICIENT_EVIDENCE`, never `MET`.

**REQ-21** The determination includes a gap list naming every criterion not
resolved `MET`, with the reason.

**REQ-39** A recorded discrepancy is an entry on `CriterionResult.discrepancies[]`,
aggregated onto `Determination.discrepancies[]`, naming the criterion, both
values, and both spans. It is advisory: it never blocks emission, never changes a
verdict, and never appears on the gap list. A disagreement is recorded only when
it meets `discrepancy_tolerance` for that fact, declared in the criteria tree.
*(Art. VII, D14)*

### Instrumentation

**REQ-22** Every model call records model name, input tokens, output tokens, and
wall time. Totals appear on the determination. *(Art. X)*

**REQ-25** Retrieval recall is measured per criterion on the eval set: the
fraction of cases where the retrieved set contains the span holding the
ground-truth fact. Reported in `eval/report.md`.

**REQ-28** The eval harness counts `ERROR` separately. `ERROR` is never folded
into the abstention rate or into any `INSUFFICIENT_EVIDENCE` count.

---

## 6. Edge cases

Each becomes a labeled eval case. This list is the eval set's outline.

| # | Case | Expected |
|---|---|---|
| E1 | Clean approval, all criteria met | `MET` |
| E2 | BMI 33 with T2DM | `NOT_COVERED` via REQ-3, zero model calls |
| E3 | Non-covered procedure requested | `NOT_COVERED` via REQ-2, zero model calls |
| E4 | Program run of 3 consecutive months, gap in month 4 | c3 `NOT_MET` |
| E5 | Program complete but ended 14 months ago | c2 `NOT_MET` |
| E6 | Weight recorded monthly, BMI recorded in 2 of 4 months | c4 `NOT_MET` |
| E7 | No weight-management documentation anywhere | c1 `INSUFFICIENT_EVIDENCE`, `NO_EVIDENCE_RETRIEVED` |
| E8 | Note asserts program completion; no visit detail | c3 `INSUFFICIENT_EVIDENCE`, `UNSUBSTANTIATED_ASSERTION`, zero `wm_events` |
| E9 | Missed-visit dates present in the gap month | c3 `NOT_MET`, not fooled |
| E10 | Structured and note BMI disagree beyond tolerance, same side of 35.0 | `MET`, one `discrepancies[]` entry, gap list untouched |
| E10b | Structured 34.8, note 36.2 — disagreement crosses 35.0 | c(a) `INSUFFICIENT_EVIDENCE`, `SOURCE_CONFLICT` |
| E10c | Structured 38.1, note 38.0 — below tolerance | `MET`, `discrepancies[]` empty |
| E11 | Two supervised programs, one qualifying, one not | `MET` on the qualifying run |
| E12 | BMI exactly 35.0 | c(a) `MET`, boundary inclusive |

E8 is the refusal test. E9 and E10b are the honesty tests. E2 and E3 are the
determinism tests and should complete in milliseconds.

`VERIFIER_REJECTED` has no case here and cannot have one. Every case above is a
property of the data, labelable before the system runs; a verifier rejection is a
property of model behavior, and the only note that provokes one is a note
extraction has already mishandled. T-17 exercises it against a stubbed verifier.

---

## 7. Acceptance criteria

v1 is done when all of the following hold on the labeled eval set.

| Gate | Threshold |
|---|---|
| A1 | Every case in §6 present and labeled |
| A2 | Per-criterion precision ≥ 0.90 on `MET` verdicts, reported alongside the `MET` base rate and the precision of a trivial always-`MET` baseline |
| A3 | Zero `MET` verdicts with an invalid span |
| A4 | E2 and E3 complete with zero model calls |
| A5 | Abstention rate reported, with the coverage/accuracy curve |
| A6 | Cost and latency per determination reported from instrumentation |
| A7 | Every REQ mapped to a passing check |
| A8 | Failure modes documented in the README, including where the system degrades |
| A9 | Zero determinations presented with a criterion in `ERROR` state |

A2 is asymmetric on purpose. A false `MET` produces a denial the specialist did
not expect; a false `NOT_MET` produces an unnecessary chart review. The first is
worse, so precision on `MET` is gated and recall is only reported.

A2 carries a baseline because 0.90 alone is not a result. On a set where 0.90 of
cases are truly `MET`, a system answering `MET` unconditionally clears the gate
while knowing nothing.

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

Both source-text questions are now closed by T-02, each on a quote and a span
into a hashed document, recorded in `data/policies/source/answers.json`.

1. ~~Does A53028 require diet and activity documentation monthly or once?~~
   **Monthly.** `a53028[6339:6503]`. The same sentence is also the source for c4
   (BMI documented) and shows c5 covers diet and activity together, not
   separately. Feeds `c5_min_documented_events`.
2. ~~Is c2's recency window 12 months?~~ **Yes, 12 months.** `a53028[6123:6337]`.
   The same sentence fixes c3's qualifying run at four consecutive months, which
   is what E4 is labeled against.

One opened in their place, and it is a design question rather than a source one:

3. **E3's procedure code is wrong, and sc1 may be missing an outcome.** T-02
   found that NCD 100.1 neither covers nor non-covers 43775 — CMS delegated
   stand-alone laparoscopic sleeve gastrectomy to the MACs effective 2012-06-27,
   and A53028 records this MAC covering it. So §6's E3 row and T-25 both assume a
   `NOT_COVERED` that the source contradicts. **T-35** re-points E3 at a code the
   NCD names non-covered for all beneficiaries; **T-36** decides whether "left to
   the contractor" is a third outcome beside `NOT_COVERED` and `NO_POLICY_FOUND`.
   See D22. Until T-35 lands, the E3 row below is known-wrong in its code and
   right in its expected verdict.

Three more opened in T-01, each one a constant the criteria tree carries as
`provisional: true` rather than as a bare number *(D23)*. Each names the task
that must resolve it before that task can be trusted.

4. **What lookback window applies to criterion (a)'s BMI?** REQ-11 evaluates (a)
   against the most recent BMI observation "within its lookback window" and
   neither source document defines one. A53028's only windows are 12 months for
   program participation and six months for the multidisciplinary evaluation, and
   neither governs the measurement. **T-13 must not invent a default.** A stale
   BMI is the difference between a real 36 and a 36 from four years and forty
   pounds ago.
5. **What is `discrepancy_tolerance` for BMI?** D14 requires a materiality
   threshold so a structured 38.1 against a note 38.0 is not listed beside 38.1
   against 45. No source text bounds it — it is a judgment about what wastes
   Sam's attention, not a coverage rule. **T-33 must not supply its own default.**
6. **Is c5 a count or a rate?** A53028 governs c4 and c5 in one sentence —
   "monthly documentation of patient's weight and BMI, current dietary regimen and
   physical activity". REQ-40 renders the first half as *every month of the
   qualifying run*; REQ-37 renders the second as *at least
   `c5_min_documented_events` events*. One sentence, two shapes, and the count
   shape is permissive in the false-`MET` direction: a seven-month run with diet
   and activity in four months passes. **T-37** owns the reconciliation. Until it
   lands, the constant is 4 and flagged.

Also settled by T-02 and worth stating once: every quantified constant in the
criteria tree comes from A53028, a **Noridian Jurisdiction F** article, not from
CMS. NCD 100.1 quantifies nothing. The system therefore determines coverage as
one MAC would, and a different jurisdiction is a different tree over the same
NCD *(D21)*.

Resolved earlier: structured-versus-note BMI disagreement *(D11)*, weight-only
documentation for c4 *(D15)*, gap-list ranking *(out of scope for v1 — ranking
needs a cost model for closing each gap type, and nothing in v1 measures that)*.
