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
- A deterministic reference implementation.
- A model-adjudicated agentic implementation using bounded tool calling.
- Differential evaluation of both implementations on the same cases.

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
tree and its `policy_version_id`, or `NO_POLICY_FOUND`. A code bound as an
identity in none of the tree's three procedure sets is `NO_POLICY_FOUND` — no
bariatric policy governs it, which is a different answer from a policy saying
no. Deterministic.

**REQ-2** A requested procedure code bound in the tree's nationally non-covered
set returns `NOT_COVERED` without any model call. Membership is read from
`procedure_sets`; it is never inferred from absence in a covered list, because
absence carries three meanings — denied, delegated to the contractor, outside
the policy — and collapsing them was D26's defect. The contractor-determined
set resolves per REQ-42 *(D33)*.
*(short-circuit sc1; rewritten by T-38, D30)*

**REQ-3** A patient with type 2 diabetes and BMI below 35 returns `NOT_COVERED`
without any model call. *(short-circuit sc2)*

**REQ-4** Every determination records the `policy_version_id` it was evaluated
against, sufficient to replay the determination later.

**REQ-42** A requested procedure code bound in the tree's contractor-determined
set resolves to an outcome distinct from both REQ-2's `NOT_COVERED` and a
nationally covered code's resolution, carrying the NCD's delegation claim and
the governing MAC's exercise of it. Under a jurisdiction whose MAC covers the
procedure, it proceeds to that MAC's criteria tree the way a covered code does
— the distinction is the type, not the flow. Deterministic, no model call, and
it never collapses into REQ-1's or REQ-2's answers. A determination assembled
from it cites both the delegation and the exercise, never the NCD alone.
*(short-circuit sc1's third outcome; T-36, D33)*

**REQ-33** The policy plane and the patient plane share no module-level
dependency. Policy modules import nothing from patient-data modules, and
patient-data modules import no policy corpus and no index over it. The only type
crossing is the compiled `Criterion`, enforced by an import-graph assertion.
*(Art. VI)*

**REQ-41** All data reaches the system through two storage ports, `PolicyStore`
and `PatientStore`. No module outside a store adapter opens a file path, holds a
connection, or names a storage location. The ports are separate types with
separate implementations; no single object satisfies both. *(Art. VI, D25)*

The ports are what makes a production database a second adapter rather than a
rewrite. They are also how Article VI stops being a lint check: two planes become
two connections, and a module that could read both would have to hold both
handles to do it.

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

**REQ-52** Extraction reaches the system through an `ExtractionRunner` port. Every
runner's output passes through `build_result()`, which anchors each quote and is the
only place a `WmEvent`, a `ProgramAssertion` or a note-level BMI is constructed from
model output. No caller builds one directly, and a quote Python cannot anchor is
counted in `dropped[]` rather than accepted. *(Art. III, D62)*

The port is what makes the model leaf replaceable without touching a predicate: a
direct SDK runner, an ADK runner, and a replay runner over a recorded payload are
three implementations of one interface, and the deterministic chain cannot tell
them apart. It is also what lets `pytest` evaluate the whole chain for zero model
calls.

**REQ-53** A model's tool allowlist is declared per agent and is single-plane. The
extraction agent receives patient-plane tools only; no module holds both the
patient and the policy toolset. *(Art. VI, D62)*

Single-plane is the smaller, true claim, and it is stronger than it looks: the
extraction agent is not given the patient's structured observations either.
REQ-34 exists because the structured BMI and the note BMI are two independent
sources that can disagree, and a model shown both has no reason to disagree.

### Agentic orchestration and model adjudication

The requirements in this subsection govern the agentic path Amendment 1 opens and
T-61 builds. They do not relax REQ-11, REQ-12, REQ-13 or REQ-19 on the
deterministic path, which stays the regression oracle. *(D62)*

**REQ-43** The system provides an agentic orchestration path in which the model
may select only from an explicit allowlist of tools and workflow steps.

**REQ-44** The model may use tool results and verified evidence to evaluate
criteria and determine criterion-level and overall outcomes.

**REQ-45** The model may not create or modify policy rules, policy versions,
tools, evidence, citations, or schemas.

**REQ-46** Agentic execution is bounded by a maximum step count, timeout, retry
budget, and tool-call allowlist. Exceeding any bound produces `ERROR` or human
review.

**REQ-47** Every model-determined criterion and overall outcome includes one or
more mechanically verified evidence spans, or returns
`INSUFFICIENT_EVIDENCE`. An unsupported or unverifiable outcome cannot be
emitted as approval or denial.

**REQ-48** Malformed, contradictory, or schema-invalid model output produces
`ERROR` and never silently becomes `NOT_MET`, `MET`, or `INSUFFICIENT_EVIDENCE`.

**REQ-49** Each agentic run records model identifier, prompt/version identifier,
tool-call trace, selected steps, retries, token usage, latency, termination
reason, policy version, and outcome.

**REQ-50** The agentic path is evaluated against the deterministic implementation
on the same cases. Differences are reported by criterion and outcome rather than
silently reconciled.

**REQ-51** Deterministic validation remains authoritative for date arithmetic,
numeric comparisons, counting, sorting, set membership, policy-version selection,
span validation, execution budgets, and output serialization.

**REQ-54** A declared tool's response is bounded. No tool returns a collection whose
size is a property of the patient's chart or the policy corpus: a read past the
declared ceiling either returns the ceiling's worth of rows alongside the true total
and a truncation flag, or fails with the ceiling named. A truncated response is
permitted only where the payload informs the model's plan; where the payload is the
set of things the model may then ask for, exceeding the ceiling is a fault. *(REQ-46,
Art. X, D66)*

Because cost is tool-payload size times turns, an unbounded tool scales with the
chart rather than with the question — D64 measured one patient's 3,780 observations
as 446x the deterministic path's input tokens for an identical answer. Bounding the
response is also what keeps the model's view and the evidence path separable: the
criteria read the port's full result, never the model's copy.

#### Unclaimed in v1

A7 requires every REQ to map to a passing check. These map to none, deliberately.
The list is closed: **it does not grow without an entry in `docs/decisions.md`**,
and `scripts/check_req_coverage.py` (T-23) reads it rather than assuming it empty.
*(D63, D70)*

| REQ | Why unclaimed | What would claim it |
|---|---|---|
| REQ-44 | Amendment 1 reserves date arithmetic, numeric comparison, counting, sorting and set membership to Python on **both** paths, and those are the entire decision procedure for all seven criteria. There is no verdict a model could determine without doing something reserved. | A later amendment relaxing one of those reservations, or a criterion whose decision procedure falls outside the reserved list. |
| REQ-47 | Same reservation. The obligation it states — verified spans behind any model-determined outcome — has no outcome to attach to while REQ-44 is unclaimed. | Whatever claims REQ-44. |

They stay in §5 rather than being deleted, because Amendment 1 genuinely grants
these permissions and a spec that omitted them would describe a constitution this
repo does not have. T-61 built the agentic path without claiming them and said so;
this table is that statement made auditable. Note also that D64 measured the
agentic path as producing identical answers at 13.9x the input tokens, so a task
claiming these would buy a passing check rather than a capability.

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

**REQ-37** c5 is `MET` when every month of the qualifying run contains an event
carrying both `diet_documented` and `activity_documented`. The rate is read from
the criteria tree, where it is the same constant c4 carries, cited to the same
sentence. A month documenting only one of the two does not count.
*(Art. VII, D13, D24)*

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

**REQ-25** Planner recall is measured per criterion on the eval set: the fraction
of cases where the evidence the agentic planner gathered contains the span the
deterministic planner read for that criterion. Reported in `eval/report.md`.
*(rewritten by D70)*

It was drafted as `recall@k` against a ranked retriever, which D4 rejected and the
system therefore never had — the deterministic path serves whole notes and reads
the port's full observation list, so the figure was 1.000 by construction. Since
T-61 the requirement has a mechanism: `AgenticRetrievalPlanner` chooses what to
gather, and a skipped note leaves c3 measuring a shorter run while producing a
determination that is entirely well-formed *(D63)*. This is the number D4's 0.85
reversal condition now reads against.

**REQ-28** The eval harness counts `ERROR` separately. `ERROR` is never folded
into the abstention rate or into any `INSUFFICIENT_EVIDENCE` count.

---

## 6. Edge cases

Each becomes a labeled eval case. This list is the eval set's outline.

| # | Case | Expected |
|---|---|---|
| E1 | Clean approval, all criteria met | `MET` |
| E2 | BMI 33 with T2DM | `NOT_COVERED` via REQ-3, zero model calls |
| E3 | Non-covered procedure requested — 43842, open vertical banded gastroplasty | `NOT_COVERED` via REQ-2, zero model calls |
| E4 | Program run of 3 consecutive months, gap in month 4 | c3 `NOT_MET` |
| E5 | Program complete but ended 14 months ago | c2 `NOT_MET` |
| E6 | Weight recorded monthly, BMI recorded in 2 of 4 months | c4 `NOT_MET` |
| E7 | No weight-management documentation anywhere | c1 `INSUFFICIENT_EVIDENCE`, `NO_EVIDENCE_RETRIEVED` |
| E8 | Note asserts program completion; no visit detail | c3 `INSUFFICIENT_EVIDENCE`, `UNSUBSTANTIATED_ASSERTION`, zero `wm_events` |
| E9 | Missed-visit dates present in the gap month | c3 `NOT_MET`, not fooled |
| E10 | Structured 39.23, note 45.0 — beyond tolerance, same side of 35.0 | `MET`, one `discrepancies[]` entry, gap list untouched |
| E10b | Structured 34.6, note 36.2 — disagreement crosses 35.0 | c(a) `INSUFFICIENT_EVIDENCE`, `SOURCE_CONFLICT` |
| E10c | Structured 37.65, note 37.6 — below tolerance | `MET`, `discrepancies[]` empty |
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
| A7 | Every REQ maps to a passing check, or appears in §5's *Unclaimed in v1* list with a stated reason and the condition that would claim it. The list does not grow without a decision entry. |
| A8 | Failure modes documented in the README, including where the system degrades |
| A9 | Zero determinations presented with a criterion in `ERROR` state |

A2 is asymmetric on purpose. A false `MET` produces a denial the specialist did
not expect; a false `NOT_MET` produces an unnecessary chart review. The first is
worse, so precision on `MET` is gated and recall is only reported.

A2 carries a baseline because 0.90 alone is not a result. On a set where 0.90 of
cases are truly `MET`, a system answering `MET` unconditionally clears the gate
while knowing nothing.

A7 admits a list because the alternative is worse. It read "every REQ mapped to a
passing check" while REQ-44 and REQ-47 were unclaimed on purpose, which made the
gate unsatisfiable and left T-23's coverage script with a choice between failing
forever and silently skipping two requirements. A declared list with a reason per
entry is the shape T-69's `EXCLUDED` mapping already uses: membership is
auditable, and a new member is a visible diff rather than an omission. What the
gate must never become is a list that absorbs whatever is inconvenient — hence
"does not grow without a decision entry". *(D70)*

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

Numbers are stable and never reused; the tree's `open_question` fields keep
resolving when a question moves. A question's status is the subsection it sits
under — the criteria-tree gate reads the `Still open` list and nothing parses
`~~strike-through~~`, which is styling *(T-39, D34)*.

### Still open

**None.** T-33 closed the last one, and the criteria tree now carries no
`provisional: true` constant. The heading stays because §9 states status by
subsection and a missing one is supposed to fail the gate *(D34)*, and the
count of provisional constants is pinned at zero in
`tests/test_criteria_tree.py` so a new one is a visible diff rather than a
silent return to the old state *(D51)*.

### Resolved

Both source-text questions were closed by T-02, each on a quote and a span
into a hashed document, recorded in `data/policies/source/answers.json`.

1. ~~Does A53028 require diet and activity documentation monthly or once?~~
   **Monthly.** `a53028[6339:6503]`. The same sentence is also the source for c4
   (BMI documented) and shows c5 covers diet and activity together, not
   separately. Feeds c5's `documentation_rate`, and settled question 6 a task
   before anyone asked it *(D24)*.
2. ~~Is c2's recency window 12 months?~~ **Yes, 12 months.** `a53028[6123:6337]`.
   The same sentence fixes c3's qualifying run at four consecutive months, which
   is what E4 is labeled against.

Question 3 opened in their place — a design question rather than a source one —
and both of its halves have since closed:

3. ~~E3's procedure code is wrong~~, ~~and sc1 may be missing an outcome~~. T-02
   found that NCD 100.1 neither covers nor non-covers 43775 — CMS delegated
   stand-alone laparoscopic sleeve gastrectomy to the MACs effective 2012-06-27,
   and A53028 records this MAC covering it, so §6's E3 row and T-25 both assumed a
   `NOT_COVERED` the source contradicts. See D22.

   **The code half is closed by T-35.** E3 is now 43842, open vertical banded
   gastroplasty, which NCD 100.1 names in a list scoped "non-covered for all
   Medicare beneficiaries" with no date qualifier and no delegation clause —
   `ncd_100_1[7076:7166]` for the scope and `[7287:7338]` for the procedure. The
   expected verdict never changed; only the code was wrong. See D28.

   **The sc1 half is closed by T-36: it is a third outcome.** REQ-1 returns
   `NO_POLICY_FOUND` and REQ-2 returns `NOT_COVERED`, and neither describes "no
   national determination, delegated to the contractor" — so REQ-42 does. The
   resolver returns a distinct type that proceeds to the MAC's criteria tree,
   because under this corpus the MAC exercised the delegation and covers the
   procedure. See D33.

Questions 4 and 6 opened in T-01 alongside question 5, D23's three
provisional constants; 4 and 6 have since closed:

4. **What lookback window applies to criterion (a)'s BMI?** REQ-11 evaluates (a)
   against the most recent BMI observation "within its lookback window" and
   neither source document defines one. A53028's only windows are 12 months for
   program participation and six months for the multidisciplinary evaluation, and
   neither governs the measurement. **T-13 must not invent a default.** A stale
   BMI is the difference between a real 36 and a 36 from four years and forty
   pounds ago.
   **Answered: 12 months, by decision (T-13, D40).** Troy chose the analogy to
   A53028's program-participation window — the measurement must be
   contemporaneous with the 12 months of evidence the determination examines.
   The constant carries a note naming D40 and no span, because no source text
   states it.

6. ~~Is c5 a count or a rate?~~ **A rate**, and the same rate c4 carries.
   `a53028[6339:6503]` is one sentence in which `monthly` governs a three-item
   list — weight and BMI, dietary regimen, physical activity — so there is no
   reading where it applies to the first item only. Question 1 had already
   answered this in T-02 and the disagreement survived because REQ-37 and REQ-40
   were drafted before anyone read the source. REQ-37 is rewritten to the
   per-month shape, `c5_min_documented_events` is gone, and a seven-month run
   documenting diet and activity in four months is now `NOT_MET`. Closed by T-37,
   see D24.

Question 7 opened in T-35, a corpus question rather than a clinical one:

7. ~~What source binds a procedure code to a procedure NCD 100.1 names only in
   prose?~~ **CMS Pub. 100-04 Transmittal 931 (CR 5013)**, the claims-processing
   transmittal that implemented this NCD's 2006 reconsideration — in the corpus
   as `r931cp` since T-40, a static PDF that re-downloads byte-identical and
   extracts deterministically under pinned pypdf, so D21's CSP-nonce problem
   does not apply. It binds every needed code in CMS's own words; E3's binding
   is the single phrase "Open vertical banded gastroplasty ( HCPCS code 43842)",
   `r931cp[15038:15092]`, and `in_corpus` is now `true`. Scope rule: `r931cp`
   is citable for code bindings **only** — its coverage content predates the
   June 27, 2012 LSG delegation and is stale, so a coverage claim spanned into
   it would be D22 rebuilt with a citation. Closed by T-40, see D29.

Question 5 opened in T-01 and is the last provisional constant the tree carried:

5. ~~What is `discrepancy_tolerance` for BMI?~~ **1.0 BMI points.** Decided, not
   sourced — no source text bounds it, so it is recorded as a judgment with a
   name and a date the way D40's lookback was *(D51, Troy, 2026-09-08)*. A full
   point is beyond rounding and beyond the variance of two measurements taken
   weeks apart, roughly six pounds. The eval set does **not** constrain it:
   measured on the committed bundles, E10's gap is 5.77 and E10c's is 0.05, so
   0.5, 1.0 and 2.0 all pass every case — the choice is a materiality judgment,
   not something the suite validates. Closed by T-33, see D51.

Also settled by T-02 and worth stating once: every quantified constant in the
criteria tree comes from A53028, a **Noridian Jurisdiction F** article, not from
CMS. NCD 100.1 quantifies nothing. The system therefore determines coverage as
one MAC would, and a different jurisdiction is a different tree over the same
NCD *(D21)*.

Resolved earlier: structured-versus-note BMI disagreement *(D11)*, weight-only
documentation for c4 *(D15)*, gap-list ranking *(out of scope for v1 — ranking
needs a cost model for closing each gap type, and nothing in v1 measures that)*.
