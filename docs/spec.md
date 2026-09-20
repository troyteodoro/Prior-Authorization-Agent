# Specification — Prior Authorization Determination Agent

**Status:** active — v1 complete, A1–A9 hold *(D104)*; v1.1 in progress; the versions after it are §11 *(D105)*. *(Was "draft, pending spike 001"; the spike closed 2026-09-07, D19 — corrected by D72.)*
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

**In scope for v1** *(complete; later versions in §11)*

- One policy, two jurisdictions: NCD 100.1, bariatric surgery, as Noridian
  Jurisdiction F implements it (A53028 supplies every quantified constant,
  *D21*) and as Palmetto GBA's Jurisdictions J and M implement it (L34576,
  *D101*). A request resolves by procedure code and state; a state no tree
  serves is its own answer *(REQ-55, D100)*. *(Was "National coverage only",
  which D21 disproved; corrected by D72; extended to a second MAC by T-87.)*
- Synthetic patients from Synthea plus manifest-driven synthesized notes.
- Local execution. No deployment.
- A deterministic reference implementation.
- A model-directed agentic implementation using bounded tool calling — the
  model directs retrieval; adjudication is permitted by Amendment 1 and
  deliberately unclaimed, see §5's *Unclaimed in v1* *(D63, D72)*.
- Differential evaluation of both implementations on the same cases.

**Explicitly out of scope for v1, and where each lands** *(D105)*

- The automated criteria compiler. The criteria tree is hand-written. Not
  scheduled.
- MCD bulk ingestion. Not scheduled.
- More than one policy, any payer-specific overlay, any LCD. *(Partly
  overtaken: T-87 compiled Palmetto's L34576, an LCD, into the second tree.)*
  Other practices' policies are v1.2 and v1.6.
- Vector search or embedding-based retrieval. Not scheduled *(D4, D70)*.
- Any user interface. v2.0, after every screen's port exists headless.
- Terraform, CI/CD, containers. Never *(working rule 9)*.
- Real or de-identified patient data of any kind. Never.

## 4. Actors

| Actor | Role |
|---|---|
| Specialist | Consumes the determination and gap list. Only human in the v1 loop. |
| Policy author | Reviews and approves criteria tree diffs. Same person as the specialist in v1. |
| System | Retrieves, extracts, adjudicates, verifies, aggregates. Never submits. *(v1.5 rewords this: transmits only on the reviewer's explicit action, D105.)* |
| Upstream system | Creates sessions through the intake contract; never reads a determination. *(v1.4, §11)* |
| Payer (simulated) | Receives the packet on the reviewer's action; never uses the system. *(v1.5, §11)* |

---

## 5. Requirements

### Policy resolution

**REQ-1** Given a procedure code and a state, the resolver returns the
governing criteria tree and its `policy_version_id`, or `NO_POLICY_FOUND`. The
tree is the one whose jurisdiction names the state *(D100)*; a code bound as an
identity in none of that tree's three procedure sets is `NO_POLICY_FOUND` — no
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

**REQ-55** A state no criteria tree governs resolves to its own answer,
`NO_JURISDICTION_TREE`, carrying the states the store does serve. It is never
`NO_POLICY_FOUND`, never a default tree, and never a bad request: "no tree
covers this state" and "the governing tree binds this code nowhere" are
different facts with different next actions. Every request names a state —
explicitly, or through the patient's bundle. Deterministic. *(T-87, D100)*

**REQ-56** A quote Python cannot locate is re-asked at most once for its
verbatim text, in one batched turn per note. The re-ask replaces only the quote
fields it was asked about — it adds, removes and re-dates nothing — and its
answer is anchored exactly as the first turn's was; a quote still unlocatable
after the re-ask is dropped as before. A failed re-ask leaves the first turn's
result standing and is recorded in the trace, never raised. Every re-ask turn
is a counted model call. The decision to re-ask is Python's, over string
search; the model is never asked whether to retry. *(T-89, D103)*

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
| REQ-44 | Amendment 1 reserves date arithmetic, numeric comparison, counting, sorting and set membership to Python on **both** paths, and those are the entire decision procedure for all seven criteria. There is no verdict a model could determine without doing something reserved. | Four preconditions, none met, fixed by *D107*: a tree declaring a predicate that is neither arithmetic nor set membership (not before v1.6); a stability mechanism satisfying Article II's own test, which means n samples with Python aggregating them; an oracle other than the deterministic implementation, since a criterion Python cannot compute has none; and the version's own acceptance gate on label agreement and run-to-run stability. |
| REQ-47 | Same reservation. The obligation it states — verified spans behind any model-determined outcome — has no outcome to attach to while REQ-44 is unclaimed. | Whatever claims REQ-44. |

They stay in §5 rather than being deleted, because Amendment 1 genuinely grants
these permissions and a spec that omitted them would describe a constitution this
repo does not have. T-61 built the agentic path without claiming them and said so;
this table is that statement made auditable. Note also that the agentic path
produces identical answers for real money — 24 model calls and 84,925 input
tokens over six patients the fixed planner spent nothing on *(D64, re-measured
in D91)* — so a task claiming these would buy a passing check rather than a
capability.

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

**REQ-32** c2 is `MET` when the qualifying run ended within the recency window
declared in the criteria tree. A run ending outside the window is `NOT_MET`. The
window length is never hardcoded. *(Art. VII)* The window is also an **input to
selection**, not only a test applied after it — REQ-14 prefers a run that
satisfies both length and recency, so c2 judges the run c3 measured and c4 and
c5 scope to that same run. *(D84)*

**REQ-14** c3 buckets event dates by calendar month and enumerates every
maximal run of consecutive populated months. The **qualifying run** is selected
jointly: among those runs, prefer one satisfying both c3's minimum length and
c2's recency window; among those take the longest, ties to the more recent. When
no run satisfies both, the longest overall is selected. A qualifying run of one
to three months is `NOT_MET`; zero events is `INSUFFICIENT_EVIDENCE`. *(D12,
D84)*

Selection was "the longest run" until T-42. That rule produced a false `NOT_MET`
from its own shape rather than from the evidence: a six-month run three years
ago beat a four-month run last month, and a patient who completed four
consecutive supervised months inside the window was reported stale. The
selection function takes c3's minimum, c2's window and `as_of` as **required**
arguments, so no caller can obtain the pre-T-42 behaviour by omitting one.
*(D48, D84)*

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

**REQ-34a** When more than one note-extracted value exists for the fact — a
chart is several documents since T-81, and each may state a current BMI —
every one is compared to the structured value independently, and no note
value is selected over another by position or date. Any pair across the
threshold resolves the criterion `INSUFFICIENT_EVIDENCE` with `gap_reason`
`SOURCE_CONFLICT`; each remaining pair at or beyond tolerance is one
`discrepancies[]` entry citing the note that stated it. Note-vs-note
disagreement is not adjudicated: the structured value is authoritative and
each note is measured against it. *(Art. IV, D50, D104)*

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
| `NOT_EVALUATED_BY_THIS_SYSTEM` | The policy requires the criterion and the tree declares no evaluator for it | A reviewer evaluates this criterion against the chart | — *(T-87, D101)* |

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
| E10 | Structured 39.23, note 45.0 — beyond tolerance, same side of 35.0 | c(a) `MET`, one `discrepancies[]` entry, gap list untouched *(criterion-scoped — its patient also carries E6, so the overall outcome is E6's; D72)* |
| E10b | Structured 34.6, note 36.2 — disagreement crosses 35.0 | c(a) `INSUFFICIENT_EVIDENCE`, `SOURCE_CONFLICT` |
| E10c | Structured 37.65, note 37.6 — below tolerance | `MET`, `discrepancies[]` empty |
| E11 | Two supervised programs, one qualifying, one not | `MET` on the qualifying run |
| E12 | BMI exactly 35.0 | c(a) `MET`, boundary inclusive |
| E13 | Qualifying run documented across two chart notes | c3 `MET`, cited spans in two distinct `document_id`s *(T-81, D104)* |

E8 is the refusal test. E9 and E10b are the honesty tests. E2 and E3 are the
determinism tests and should complete in milliseconds.

`VERIFIER_REJECTED` has no case here and cannot have one. Every case above is a
property of the data, labelable before the system runs; a verifier rejection is a
property of model behavior, and the only note that provokes one is a note
extraction has already mishandled. T-17 exercises it against a stubbed verifier.

---

## 7. Acceptance criteria

v1 was done when all of the following held on the labeled eval set; they
have held since D104. Each later version states its own gate in §11.

| Gate | Threshold |
|---|---|
| A1 | Every case in §6 present and labeled |
| A2 | Per-criterion precision ≥ 0.90 on `MET` verdicts, reported alongside the `MET` base rate and the precision of a trivial always-`MET` baseline |
| A3 | Zero `MET` verdicts with an invalid span |
| A4 | E2 and E3 complete with zero model calls |
| A5 | Abstention rate reported, accounted for per `gap_reason`, and swept against a real constant the tree carries *(D82)* |
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

A5 no longer asks for a curve across "a range of fail-closed thresholds". No
such threshold exists: every verdict is a deterministic predicate over spans and
constants (Articles I and II), and nothing in the system carries a confidence
score to sweep. T-72 measured the two constants that *are* decisions rather than
spans before rewriting this row. `discrepancy_tolerance` sweeps for free and
moves the **disclosure count** — how much source disagreement reaches the
reviewer — while leaving the abstention rate flat at every grid point;
`a.lookback_months` cannot be swept for free at all, because a changed verdict
is a verifier claim the committed recording has never seen and
`RecordedVerifierRunner` raises rather than accepting by default (D78). And
narrowing a window would not produce abstentions in any case: REQ-16 routes
evidence that exists but is too old to `NOT_MET`, deliberately. **No constant in
this tree can drive abstention to 1**, so the row asks for what the system has —
the rate, the account of *why* per `gap_reason`, and the sweep with the flat
abstention column printed beside it so the claim is a measurement rather than a
sentence. Where the system becomes useless is answered under A8, in the terms it
actually has. *(D82)*

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
   **Answered: 12 months, by decision (T-13, D40).** the owner chose the analogy to
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
   name and a date the way D40's lookback was *(D51, the owner, 2026-09-08)*. A full
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

---

## 10. Problems to address

v1's known limits, stated as problems a later version would address. This is
A8's substance: the README names each mode in one line and points here; this
section carries the analysis *(D95)*. Ids are stable — a solved problem keeps
its number and records what solved it. Ordered roughly by how likely each is
to bite a real deployment.

### P1 — One jurisdiction, and the thresholds are not CMS's

NCD 100.1 quantifies nothing — no months, no visit counts, no recency window.
Every constant in the criteria tree comes from A53028, a Noridian Jurisdiction
F article. This system determines coverage *as Noridian would*. Point it at a
patient in another MAC's territory and it will answer confidently and wrongly,
because the tree is right and the jurisdiction is not. There is no code path
that notices; the resolver returns one tree. A second jurisdiction is a second
tree over the same NCD, and nothing in the design makes that a small change to
*operate* — it makes it a small change to *build*.

Since T-87 there is a second tree and the resolver reads the state
*(D100, D101)*: `ncd-100.1-jjm-v1`, compiled from Palmetto GBA's L34576 for
Jurisdictions J and M, and a request resolves by procedure code and the
patient's state, with a state no tree serves answered as
`NO_JURISDICTION_TREE` *(REQ-55)*. What the second tree showed is that the
difference between MACs is one of **shape** as much as value — Palmetto states
no run length, requires weight rather than BMI monthly, and adds a
multidisciplinary evaluation — so two of its criteria are declared unclaimed
and abstained on rather than evaluated on a proxy. The thresholds are still
not CMS's; there are now two contractors' worth of them, and every other MAC
is still a tree nobody has compiled. Since T-88 one eval row runs under the
second tree *(D102)*: `J1`, a declared clone of E4's chart re-addressed into
Alabama. Its January–March run is a `c3` shortfall of three against four
under Noridian and not a criterion at all under Palmetto — `c2` and `c5` are
`MET` on the same bytes, and the determination abstains on what the tree
declares unclaimed. That is P1 as a determination rather than a paragraph.

### P2 — Extraction refuses paraphrase, and that loses evidence

Spans are located by searching the model's verbatim quote — exact, then
whitespace-normalized — because the model's own character offsets were usable
0 times out of 80 in the spike and 0 out of 171 in T-15. The consequence is
that a model which paraphrases instead of quoting produces a claim nobody can
anchor, and the anchorer correctly drops it. T-63 lost a patient's program
assertion exactly this way. The determination that results is well-formed and
says less than the chart does — it abstains where it should have found
evidence, so the failure is fail-closed rather than a wrong approval, and it
is still a failure. The per-note record shows it; the headline aggregate did
not, until T-71. The instance is one word — *completed* for *completing*, in
the ADK tool-fetch run on E8, with 62 verbatim characters after it — and
`eval/report.md` carries the anchoring account for every extraction
recording since T-85 *(D98)*. Since T-89 the mechanism exists *(REQ-56,
D103)*: a quote the anchorer refuses is re-asked once, in both live runners,
for its verbatim text, and the answer is anchored exactly as the first was —
the model supplies a new quote, Python admits or drops it. Re-measured, the
three recordings anchored every span on the first turn, E8's assertion
included, so the re-ask was asked about nothing and cost nothing; the
instance above was one run's behaviour and the mechanism is the standing
answer to its recurrence. What it cannot recover is a claim the model never
quoted at all, and a re-ask that paraphrases twice is dropped twice.

### P3 — Eight patients, five documents, seventeen cases *(was six, five, fifteen)*

Every rate in `eval/report.md` moves by large steps. One case is worth more
than a percentage point in every table. A precision of 1.000 over thirteen
`MET` calls against a base rate of 0.591 is a real result and a small one; it
says the approach does not obviously fail, and nothing more. Since T-88 the
set is sixteen rows over eight bundles, one of them a declared clone that
shares its note's bytes with its source *(D102)*; the count moved by one row
and the bound did not. Since T-81 it is seventeen rows over fourteen chart
notes *(D104)*: every note-bearing chart is two documents, a split of the
facts its manifest already declared, so the document count doubled while
the fact set — and therefore every existing label — stayed as it was. The
row that moved is `E13`, the one labeled claim the split creates. More
patients is a separate decision, taken after T-81's numbers landed *(D97)*.

### P4 — The ground truth is a first draft

The eval labels and the fact manifests were drafted alongside the system at
creation and reviewed as they were written; they are a working first draft.
The mechanical safeguards are what carry the weight: manifests are written
from the bundles *before* the notes are synthesized, the system under test
never reads them, and every cited span is validated against the source rather
than against a label. The second full pass was taken with T-81 *(D96, D104)*:
every row's expected verdicts were re-derived from the fact manifests and
the two trees' constants — never from an observed run — and the per-row
table sits in D104 with the owner's review of it recorded by the close.
Every label agreed with its re-derivation; the one addition is `E13`. P3's
set size, not provenance, is the real bound on the numbers.

### P5 — The verifier is blind on purpose, and that costs recall of a certain kind

It sees one claim at a time — the requirement text, the verdict, and the
mechanically sliced quote — and no reasoning. It is therefore barred from date
and count arithmetic, because two measured rounds of false rejections showed
it rejecting every shortfall-type `NOT_MET`: the shortfall is arithmetic over
a chart the blindness deliberately hides. A verifier that cannot see the chart
cannot check a claim *about* the chart's arithmetic, and that half of
verification is done by Python instead. Since T-86 it actually is: every
`NOT_MET` carries a structured shortfall, and the graph re-runs the same
predicate over only the cited evidence before the verifier sees the claim,
requiring the same verdict, span set and shortfall back — a failure is that
criterion's `ERROR`, never an abstention *(D99)*. What remains true is the
limit itself: the model checks citation fidelity, and the arithmetic is
checked by the code that computed it, on a shorter input.

### P6 — Model adjudication is unclaimed, so the model's judgment is never on the hook

REQ-44 and REQ-47 are declared *Unclaimed in v1*: Amendment 1 reserves date
arithmetic, numeric comparison, counting, sorting and set membership to Python
on both paths, and those are the entire decision procedure for all seven
criteria. There is no verdict a model could determine without doing something
reserved. That is a deliberate limit on what has been demonstrated, not an
oversight — the agentic path is real and it decides *what to read*, not what
the answer is.

**D107 writes down what would claim them, and nothing through v2.0 does.** The
candidate that looks like the answer is not one: Palmetto's `d`, a
multidisciplinary evaluation within six months, decomposes into extraction plus
set membership plus a window, so building it would add an extractor and leave
Python deciding — REQ-38's shape, not REQ-44's. What claims REQ-44 is a
criterion whose *predicate* cannot be compiled, and this repo has already met
one and turned it away: D97 rejected Novitas's L35022 for asking a "diligent
effort" and giving a deterministic tree nothing to compile. The property that
made it useless as a tree is the property that would claim REQ-44. Three
further preconditions follow. **Article II's test binds harder than Amendment
1's list** — same input, different verdict means the computation is in the
wrong place, and a model verdict is exactly that (D47, D91) — so the model
would supply n opinions and Python would count them, counting staying
reserved. The deterministic implementation stops being the oracle for a
criterion it cannot compute, so the labels become the oracle, at a quality P3
and P4 have not reached. And the version claiming it states its own gate on
label agreement and run-to-run stability, because the differential cannot
cover it. The unclaimed set stays closed at two.

### P7 — Retrieval recall is measured directly, and on this corpus the direct figure cannot fall

The recording carries both the documents each run *cited* and the bundle the
planner *gathered*, and both read 1.000 over 25 citing cases. The gathered
figure is the one REQ-25 asks for and it is 1.000 **by construction**: one
note per patient, a planner that raises rather than returning an empty bundle,
and structured facts re-read from the port rather than taken from the model's
tool payload. A run that did not error gathered everything there was. So the
*cited* figure beside it is the one still carrying information — and it did
settle the question the bound could not: every patient gathered two documents
and two of the six cite only one, which means those notes reached the criteria
and yielded nothing to cite. Gathered and uncitable, never skipped.

Since T-81 every note-bearing chart is two documents and the qualifying run
straddles them wherever a run exists *(D104)*, so the planner may name one
of the two and the run succeeds with a shorter chart — the direct figure
can fall, and `eval/report.md` reports whether it did as a per-patient table
of notes on file against notes gathered. On the first measurement it did
not: the planner gathered both notes for every patient, and the two charts
that cite no note are still gathered-and-uncitable. The direct figure is
now the one to quote and the cited figure the bound beneath it, which is
D91's reversal condition met. What remains true is that one run is a sample:
a free-tier tool loop is not reproducible at temperature 0 (D91), and the
figure is re-measured, never re-run.

### P8 — Everything free is a replay

Every gate, the CLI's default path, and every number in the report run off
committed recordings and spend zero model calls. That is what makes the checks
something that gets run rather than skipped because it costs money — and it
means the freely-reproducible figures describe the model as it behaved on one
measured day, against one pinned model, on one tier. A changed call
configuration, a changed tool declaration, a changed SDK, or a different tier
is a **new measurement, never a re-run**.

Since T-90 there is a second measured day on a second tier *(D106)*. The same
corpus was measured on **Vertex** — all three extraction recordings, the
verifier's thirty claims and the retrieval differential — and committed beside
the AI Studio ones, which did not move; `eval/report.md` renders the two as
columns. What the second tier showed is that the things this system claims
about itself are not properties of one endpoint: precision, recall, REQ-9
exclusion and field agreement are 1.000 on both, every span anchored, the
verifier accepted the same thirty claims with **no verdict moving**, and
**0 of 169, 0 of 165 and 0 of 76 model-emitted offsets were usable** — D18
reproduced a fourth time. It also answered D71's open clause: the injected
`SetModelResponseTool` really is an AI Studio artifact and vanishes on the
native path — tool calls 26 → 12, unescaped spans 4 → 0 — while the tool
path's token overhead only halves, from 4.12x to 2.14x against each tier's
own direct runner. Half of that overhead was the tier; the other half is what
it costs to ask for a document the caller already holds.

What remains true is that two measured days are two samples. Cost figures in
particular do **not** transfer: the direct runner spends markedly more input
tokens on Vertex for an identical prompt and corpus, so token accounting is
not like-for-like across tiers and only the within-tier comparisons carry.
The pinned model resolves on Vertex under the same name, but only at
`location=global`, which is now part of what a recording states. Everything
free is still a replay — that has not changed and is not a defect; what has
changed is that the replayed numbers are no longer from a single tier.

---

## 11. Versions after v1

v1 is complete and v1.1 — the §10 round D97 opened as "v2" and D105
renamed — is in progress. This section fixes what follows: one version at a
time, each with a goal, a scope, the story it closes, the tasks it reserves,
what it spends, and the gate it must hold *(D105)*. **Requirements here are
statements, not ids.** A version mints its REQ ids when its first task
opens, by moving its statements into §5 with numbers; the coverage mapping
and the pinned count move in the same commit. §1's rule stands: a
requirement with no check is a wish, and a wish is not given a number.

| Version | Delivers | Story | Tasks | Model calls | Gate |
|---|---|---|---|---|---|
| v1.1 | §10's P1–P8, one task each *(D97)* | — | T-85–T-90 | the Vertex round (T-90) | A1–A9 |
| v1.2 | cross-practice round one: rheumatoid arthritis, then diagnostic ultrasound; the rules engine only | US-10 | T-91–T-95 | none | A10 |
| v1.3 | medical-history review: ICD suggestions with evidence, colour-sorted | US-11 | T-96–T-99 | one recording round | A11 |
| v1.4 | sessions and intake, headless | US-12 | T-100–T-102 | none | A12 |
| v1.5 | the form, review, simulated submission and tracking, headless | US-13 | T-103–T-106 | none | A13 |
| v1.6 | cross-practice round two: tree-declared extraction, two more practices | US-14 | T-107–T-110 | new extraction recordings; the differential re-measured | A14 |
| v2.0 | the reviewer's UI over the session port | US-15 | T-111–T-116 | none | A15 |

### v1.1 — Finishing §10

**Closed.** The path D97 fixed ran unchanged: T-85 through T-90 and T-81, then
P6's entry. The Vertex column renders in `eval/report.md` *(T-90, D106)* and
P6's path is logged *(D107)*; A1–A9 all hold. **v1.2 is next** and opens when
its first task does, minting its requirements then *(D105 rule 2)*.

### v1.2 — Cross-practice round one: rheumatoid arthritis, then diagnostic ultrasound

**Goal.** Measure how much of the engine is bariatric-shaped. Since D101
every criteria step evaluates what the tree declares, so the question is
whether two trees from unrelated practices compile into the predicate
vocabulary the engine has — and where a criterion cannot be expressed,
whether the engine says so rather than approving past it.

**In scope.** An explicit predicate vocabulary: every tree predicate declares
its kind, and a kind the engine lacks fails at load. Two new criteria trees
compiled from real coverage documents, each chosen by fetching its header as
D97 did for Palmetto: for rheumatoid arthritis, a MAC LCD or billing article
governing infused biologic DMARDs (a J-code; the resolver keys on a code, so
nothing changes there); for ultrasound, a MAC LCD for a non-invasive vascular
or abdominal study. Synthea patients where a module exists, declared
additions in D73's shape where it does not. Eval rows for each: a `MET`, a
`NOT_MET` on the practice's own arithmetic, and one abstention or `NO_POLICY_FOUND`.
A compatibility account in `eval/report.md`: per practice, each criterion
classed as evaluated by an existing predicate kind, by a new kind, or
unclaimed.

**Expected new predicate kinds**, each Article II arithmetic over structured
FHIR: diagnosis membership by ICD-10 or SNOMED (value sets declare a code
system per entry — Synthea codes conditions in SNOMED, LCDs list ICD-10, so
both are carried with the mapping cited); medication trial duration over
`MedicationRequest` dates; a lab-result threshold; a prior-procedure count in
a window, c1's shape.

**Out of scope.** Chart notes and extraction for either practice. Note-only
criteria are declared unclaimed and abstain with
`NOT_EVALUATED_BY_THIS_SYSTEM`, never omitted; their extraction is v1.6's,
because a new extraction schema is a measurement round (D45) and this version
asks a question about the engine. **Zero model calls.**

**Requirements it will mint.**

- Every predicate in a criteria tree declares its kind from a closed set;
  a tree naming a kind the engine lacks raises at load. Unbuilt is not
  unclaimed.
- A tree may declare any criterion `evaluation: "unclaimed"`; the graph
  abstains on it with `NOT_EVALUATED_BY_THIS_SYSTEM` and never omits it
  *(generalising D101)*.
- A value set declares the code system of every entry; membership is
  tested within the declared system.
- Medication trial duration, lab thresholds and prior-procedure counts are
  computed by Python over structured resources, cited to the resource.
- `eval/report.md` carries the compatibility account, generated and
  verified.

**Gate A10.** Every criterion of every loaded tree is evaluated by a declared
predicate kind or declared unclaimed, zero omitted; every eval row `PASS`;
zero model calls in any gate.

### v1.3 — Medical-history review: ICD suggestions with evidence

**Goal.** From the medications and conditions on the chart, surface
conditions the chart supports but does not carry — a corticosteroid and a
low bone density, an anticoagulant and a low blood pressure — each tied to
evidence, so the reviewer can decide whether the packet should carry the
code.

**Design under the constitution.** A reviewed lookup table,
`data/knowledge/medication_effects.json`, is the only place a code can come
from: medication class or RxNorm ingredient → effect condition → ICD-10 and
SNOMED codes → the structured signal that corroborates it (an observation
and a threshold) → a source in `sources.json`. It is a file under git and
review, Article VII's shape. `pa_agent/history.py` is deterministic: an
active medication matching a row is a candidate; a condition already coded
is not a suggestion. Python assigns a closed tri-state:

- **green** — corroborated by structured data: an observation crosses the
  row's threshold. Addable with no further evidence.
- **yellow** — corroborated only by an anchored note quote. Added with that
  citation attached automatically.
- **red** — pharmacological plausibility only; nothing on the chart. The
  reviewer must write a justification before it can enter a form.

The model's role is confined to the agentic path Amendment 1 opened: asked
for verbatim quotes documenting each candidate's effect, anchored by
`anchor.py`, dropped when unanchorable, and verified by Article V's verifier
as `(candidate, quotes)` claims. It never proposes a code. Suggestions ride
in a separate `icd_suggestions` block beside the verdicts, each carrying
`would_affect` — the criteria whose value set contains the code, by set
membership — as a report; **no verdict changes in v1.3.** A recording at
`eval/history/results.json` replays in every gate; the live call is a
measurement script in `EXCLUDED`.

**Requirements it will mint.**

- A suggested code comes only from a row of the knowledge table, and every
  row names a source the offline verifier covers.
- The tri-state is assigned by Python over structured thresholds and span
  presence; the model is never asked which colour, or which code.
- A suggestion is never a code assignment. It enters no determination
  verdict, and no form without a human action; red enters no form without
  a written justification.
- Every yellow suggestion carries a verified span; a suggestion whose quote
  fails to anchor is red, never yellow.
- `would_affect` is computed by set membership against the governing tree's
  value sets and changes nothing.
- The model turn is recorded, replayed and counted like every other.

**Gate A11.** Suggestion precision against manifest labels at or above A2's
bar on `MET`; zero suggestions without a source row; zero yellow without a
valid span; zero verdict drift against the baseline.

### v1.4 — Sessions and intake, headless

**Goal.** A determination becomes something the specialist can come back
to. A third plane — the session plane — with its own port and file-backed
adapter, constructed in `cli.py` and nowhere else (REQ-41's rule); a session
holds ids and its own determination snapshot and never a copy of either
corpus (Article VI). An intake contract, `pa_agent/intake.py`: a procedure,
with or without ICD codes, a patient and a state, from a JSON document an
upstream system produced or from command-line flags a person typed, both
validating to the same object. A closed lifecycle enum walked by a Python
state machine — `CREATED → DETERMINED → IN_REVIEW`, extended by v1.5 — where
an illegal transition raises. Verbs: `session create | list | show | run`.
`session list` is v2.0's dashboard checklist, as text. **Zero model calls.**

**Requirements it will mint.**

- The session store is a port; the CLI is the only constructor; the
  `stores` package still imports no submodule.
- A session records its intake, the `policy_version_id` and determination
  it produced, and its lifecycle state; it stores no policy text and no
  patient resource beyond ids.
- The lifecycle is a closed enum; every transition is code; an illegal
  transition raises and is never recorded.
- A JSON intake and the equivalent flags validate to the same object; a
  malformed intake is a bad request (exit 1), never a session.

**Gate A12.** Every transition in the enum has a test and every illegal one
raises; a session round-trips through the adapter byte-stable; the plane
check extends to the third plane.

### v1.5 — The form, review, simulated submission and tracking, headless

**Goal.** The packet the specialist sends, and what happens to it. A form
assembled by `pa_agent/form.py` from the determination, the accepted
suggestions with their justifications, identity pass-through and the rest
of the fields a prior authorization form carries; a review log the
specialist appends to beside the determination, which is never edited;
transmission to a simulated payer — an `.eml`-shaped file in a payer outbox,
a `payers.json` of simulated contacts — on the specialist's explicit action
after review; and the session tracked to `AWAITING_DECISION`, with a
`session decide` stub that closes it. §1's "does not submit" is reworded by
this version's entry to "transmits only on the reviewer's explicit action
after review, and never decides to". **Zero model calls.**

**Requirements it will mint.**

- The packet's every citation slices back; a packet is refused while any
  red suggestion lacks a justification.
- The review log is append-only; the determination's bytes are unchanged
  by any review.
- Transmission is a lifecycle transition taken only on an explicit verb,
  after `IN_REVIEW`; the system never decides to transmit.
- The outbox is the only side effect of submission, and every session in it
  is `AWAITING_DECISION`.

**Gate A13.** Zero packets in the outbox with a red suggestion lacking a
justification; every citation in every packet valid; every outbox session
`AWAITING_DECISION`.

### v1.6 — Cross-practice round two: tree-declared extraction, two more practices

**Goal.** The second test on different practices, and the engine change
v1.2 deferred. The tree declares its extraction schema — the fact types and
fields the extractor produces — so `WmEvent` stops being the only fact type
and the runners, the anchorer and `build_result` become generic over declared
types; `STEPS` stays a fixed tuple (Article I). Candidates, confirmed at
open: CPAP for obstructive sleep apnea under NCD 240.4, a **nationally
quantified** NCD, unlike 100.1; and one imaging or therapy domain. The
rheumatoid and ultrasound trees gain their note-only criteria here. New
notes are new extraction recordings (D45), and the agentic differential is
re-measured.

**Requirements it will mint.**

- A tree declares its fact types; the bariatric tree declares `WmEvent` and
  every existing recording replays unchanged; an unknown fact type raises
  at load.
- The anchorer, the validator and the trust boundary are generic over
  declared fact types; no fact type reaches a `WmEvent`-shaped private
  route.
- The compatibility account covers four practices.

**Gate A14.** A10 over four practices; every eval row `PASS`; the agentic
differential re-measured with zero errors.

### v2.0 — The reviewer's UI *(tentative)*

**Goal.** The first version a persona uses without a terminal. A local
single-process web app over the session port; the framework is decided in
its own entry (`adk web` already places FastAPI and uvicorn in the pinned
environment, so `check_env.py` parity is the first thing checked). No
authentication, no database beyond the file-backed stores, no deployment —
working rule 9 holds. Screens map to ports: a dashboard with the session
checklist and its statuses, and a create form for a procedure with or
without ICD codes, typed or pasted from an upstream system; a determination
view with criteria, verdicts, spans rendered as highlighted excerpts beside
the structured evidence, and the gap list; a suggestions panel where green
adds, yellow adds with its citation and red opens the justification field at
that point in the form; the rest of the form; the simulated email and its
outbox; tracking to awaiting approval. **No logic in the UI**: every action
is a verb v1.4 and v1.5 already test, and tests are contract tests plus a
test-client smoke test, no browser automation.

**Requirements it will mint.**

- Every UI action maps to a CLI verb and produces identical output.
- Templates carry no logic, pinned by parsing (D65's shape).
- The UI reads and writes through the session port only; it constructs no
  store.

**Gate A15.** Every UI action maps to a CLI verb with identical output; zero
logic in templates; every gate green with the app importable.

### Gates by version

| Gate | Version | Threshold |
|---|---|---|
| A10 | v1.2 | every criterion of every loaded tree evaluated by a declared kind or declared unclaimed, zero omitted; every eval row `PASS`; zero model calls in any gate |
| A11 | v1.3 | suggestion precision at or above A2's bar; zero suggestions without a source row; zero yellow without a valid span; zero verdict drift |
| A12 | v1.4 | every lifecycle transition tested, every illegal one raises; sessions round-trip byte-stable |
| A13 | v1.5 | zero packets with an unjustified red suggestion; every packet citation valid; every outbox session `AWAITING_DECISION` |
| A14 | v1.6 | A10 over four practices; every row `PASS`; the differential re-measured, zero errors |
| A15 | v2.0 | every UI action maps to a CLI verb with identical output; zero logic in templates |
