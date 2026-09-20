# User stories

Vertical slices. Each one ends with the system doing something a specialist could
watch happen. Acceptance criteria are `Given / When / Then` and map to numbered
requirements in `docs/spec.md`, which remains the source of truth for detail.

Hierarchy mirrors ADO work item types:

```
Feature  →  User Story  →  Task
                ↑
        Acceptance criteria (= REQ-n in spec.md)
```

Spikes and environment setup are work items but not stories. They deliver no
user-visible behavior, and pretending otherwise is how "As a developer, I want a
virtualenv" ends up on a board.

---

## Personas

**Sam — prior authorization specialist.** Reviews charts, assembles PA packets,
submits to payers, works denials. Measures her day in charts cleared. Her worst
outcome is a denial she did not see coming, because it means the packet went out
incomplete and the patient's surgery slips.

**Dr. Vance — clinical documentation lead.** Owns which coverage rules the system
enforces. Does not use the system daily; reviews changes to what it checks.

---

## Feature F1 — Automated coverage determination for bariatric surgery

### `US-1` Screen out non-covered procedures instantly

> **As** Sam
> **I want** requests for nationally non-covered procedures rejected before I start
> **So that** I don't spend an hour assembling a packet Medicare will never pay

**Value:** the cheapest hour anyone saves all week, and it needs no model.

- **Given** a request for a procedure absent from the policy's covered list
  **When** the determination runs **Then** the result is `NOT_COVERED`, with the
  reason and the governing `policy_version_id`, and zero model calls are
  recorded · *REQ-2, REQ-4, A4*
- **Given** a procedure code with no governing policy **When** the determination
  runs **Then** the result is `NO_POLICY_FOUND` rather than a denial · *REQ-1*

**Covers:** E3
**Ships:** the end-to-end skeleton. Request in, determination out, on day one.

---

### `US-2` Answer the structured criteria with citations

> **As** Sam
> **I want** the BMI and comorbidity requirements answered from the chart with a pointer to where
> **So that** I stop hunting through the record for numbers that are already there

- **Given** a patient with a BMI observation of 35.0 or above within the lookback
  window **When** criterion (a) is evaluated **Then** it returns `MET` with a
  span identifying the source observation, and no model call is made ·
  *REQ-5, REQ-11*
- **Given** a BMI of exactly 35.0 **When** criterion (a) is evaluated **Then** it
  returns `MET`, boundary inclusive · *E12*
- **Given** a patient with an active condition in the comorbidity value set
  **When** criterion (b) is evaluated **Then** it returns `MET` with the
  contributing condition cited · *REQ-12*
- **Given** any returned span **When** the source document is sliced at those
  offsets **Then** the text is non-empty and matches, checked without a model ·
  *REQ-6, REQ-7*

**Covers:** E12
**Ships:** FHIR extraction, the document index, the span validator, and the first
real criterion verdicts. Still no LLM anywhere in the pipeline.

---

### `US-3` Catch the categorical exclusion

> **As** Sam
> **I want** the diabetes-with-BMI-under-35 exclusion caught before I build the packet
> **So that** I don't submit a request that is categorically excluded and looks like an obvious approval

- **Given** a patient with type 2 diabetes and a most-recent BMI below 35
  **When** the determination runs **Then** the result is `NOT_COVERED` citing the
  national exclusion, with zero model calls · *REQ-3, A4*
- **Given** the same patient **When** the result is returned **Then** it is
  distinguishable from a criteria failure, because the specialist's next action
  differs · *REQ-21*

**Covers:** E2
**Note:** small story, high signal. This case pattern-matches to an approval and
is the one a model would get wrong.

---

### `US-4` Answer the criterion that lives only in the notes

> **As** Sam
> **I want** the supervised weight-management requirement evaluated from the narrative
> **So that** I stop reading six months of visit notes to count months by hand

**Value:** the reason the project exists. Everything before this is lookup.

- **Given** a chart note describing a supervised program **When** extraction runs
  **Then** one event is produced per documented encounter, each with a span that
  passes the mechanical check · *REQ-8, REQ-10*
- **Given** a note containing missed visits, failed contact attempts, or prior
  unsupervised attempts **When** extraction runs **Then** none of them appear as
  encounters · *REQ-9, E9*
- **Given** a chart with no weight-management documentation at all **When** c1 is
  evaluated **Then** it returns `INSUFFICIENT_EVIDENCE`, never `NOT_MET` ·
  *REQ-36, E7*
- **Given** extracted events spanning January, February, March, May, June
  **When** c3 is evaluated **Then** it returns `NOT_MET`, because the longest
  consecutive run is three · *REQ-14, E4*
- **Given** a qualifying four-month run ending fourteen months before the request
  **When** c2 is evaluated **Then** it returns `NOT_MET`, not
  `INSUFFICIENT_EVIDENCE` · *REQ-32, REQ-16, E5*
- **Given** c3 has failed **When** c4 and c5 are evaluated **Then** both return
  `INSUFFICIENT_EVIDENCE`, because no qualifying period exists to scope to ·
  *REQ-15*
- **Given** a qualifying run **When** c5 is evaluated **Then** the required
  rate — diet and activity documented in every month of the run — comes from
  the criteria tree · *REQ-37, Article VII, D24*
- **Given** a structured and a note BMI disagreeing beyond tolerance on the same
  side of 35.0 **When** reconciliation runs **Then** the structured value stands
  and the disagreement appears on `discrepancies[]`, not the gap list ·
  *REQ-34, REQ-39, E10*
- **Given** the two values falling on opposite sides of 35.0 **When**
  reconciliation runs **Then** criterion (a) resolves `INSUFFICIENT_EVIDENCE`
  with `gap_reason` `SOURCE_CONFLICT` · *REQ-34, REQ-31, E10b*
- **Given** identical input run three times **When** c1 through c5 are evaluated
  **Then** the verdicts are identical · *REQ-13, Article II*

**Covers:** E4, E5, E6, E7, E9, E10, E10b, E10c, E11
**Ships:** the single model leaf in the system, plus every deterministic predicate
over its output.

---

### `US-5` Tell me what's missing, not just whether it passed

> **As** Sam
> **I want** a list of exactly which criteria the chart does not support and why
> **So that** I know what to go collect instead of guessing why a packet was thin

**Value:** the actual product. The verdict is a summary; the gap list is the work.

- **Given** a determination with any criterion not resolved `MET` **When** the
  result is returned **Then** the gap list names each one with its reason ·
  *REQ-21*
- **Given** any criterion resolved `INSUFFICIENT_EVIDENCE` **When** the overall
  result is computed **Then** it is `INSUFFICIENT_EVIDENCE`, never `MET` ·
  *REQ-20*
- **Given** criterion verdicts **When** the overall result is computed **Then**
  the policy's boolean expression is evaluated in Python and no model is
  consulted · *REQ-19, Article I*
- **Given** a note asserting program completion with no visit detail **When** c3
  is evaluated **Then** it returns `INSUFFICIENT_EVIDENCE` with `gap_reason`
  `UNSUBSTANTIATED_ASSERTION` · *REQ-14, REQ-31, E8*
- **Given** two gaps with different `gap_reason` values **When** they are
  displayed **Then** they read differently, because Sam's next action differs ·
  *REQ-31, E7, E8*

**Covers:** E1, E8

---

### `US-6` Let me trust a citation without re-reading the chart

> **As** Sam
> **I want** every citation checked before I see it
> **So that** I can spot-check three instead of verifying all seven

- **Given** a verdict and its span **When** verification runs **Then** the
  verifier's input contains the criterion, the span, and the claimed verdict, and
  nothing else · *REQ-17, Article V*
- **Given** a span that does not support its verdict **When** verification runs
  **Then** the verdict is rejected and the criterion resolves
  `INSUFFICIENT_EVIDENCE` without retrying · *REQ-18, Article IV*
- **Given** a rejected verdict **When** the gap list is built **Then** the
  criterion carries `gap_reason` `VERIFIER_REJECTED` · *REQ-31*
- **Given** a model call that fails with a retryable error **When** N attempts
  are exhausted **Then** the criterion resolves `ERROR`, not
  `INSUFFICIENT_EVIDENCE`, and N is recorded · *REQ-18a, REQ-23*

---

### `US-7` Show me where the system stops being reliable

> **As** Dr. Vance
> **I want** to see accuracy, abstention, and cost across the labeled set
> **So that** I can decide where this is safe to use and where it is not

**Value:** the deliverable an external reviewer actually reads.

- **Given** the labeled eval set **When** the harness runs **Then** it reports
  per-criterion precision on `MET`, span validity rate, and abstention rate ·
  *A2, A3, A5*
- **Given** reported precision **When** the report is produced **Then** the `MET`
  base rate and an always-`MET` baseline appear beside it · *A2*
- **Given** the abstentions the labeled set produces **When** the report is
  produced **Then** each is accounted for by its `gap_reason`, and the one
  constant that can be swept for free — `discrepancy_tolerance` — is swept, with
  the abstention rate printed at every grid point beside what the sweep actually
  moves · *A5* · *(D82: no constant in this tree drives abstention to 1, and
  where the system becomes useless is answered under A8 instead)*
- **Given** any determination **When** it completes **Then** model, token counts,
  and wall time are recorded from instrumentation, not estimated · *REQ-22, A6,
  Article X*
- **Given** the edge cases in spec §6 **When** the report is produced **Then**
  every one is present and labeled · *A1*
- **Given** the requirements in spec §5 **When** the report is produced **Then**
  every REQ maps to a check that passed, and any that does not is named · *A7*

**Covers:** all

---

### `US-9` Withhold what the system couldn't compute

> **As** Sam
> **I want** a determination that failed partway through withheld rather than shown
> **So that** I never read "this criterion is not supported by the chart" about a criterion the system never evaluated

**Value:** a broken pipeline that looks like thin documentation sends Sam hunting
for records that were there all along.

- **Given** any criterion resolving `ERROR` **When** the determination is
  assembled **Then** no `Determination` is emitted and the CLI exits non-zero
  with the `error_code` and criterion id on stderr · *REQ-24, REQ-26, REQ-29, A9*
- **Given** an `ERROR` **When** it is recorded **Then** it carries an
  `error_code` from the closed enum and the exception text, never a free-text
  reason · *REQ-30*
- **Given** a retryable `error_code` **When** N attempts are exhausted **Then**
  the criterion resolves `ERROR`; **given** a terminal code **Then** it resolves
  on the first occurrence without spending an attempt · *REQ-18a, REQ-30*
- **Given** a seeded `ERROR` **When** the eval harness reports **Then** the
  abstention rate is unchanged, because a crash is not a caution · *REQ-28*

**Covers:** A9

---

## Feature F2 — Policy governance

### `US-8` Review what the system checks before it checks it

> **As** Dr. Vance
> **I want** a change to a coverage rule to arrive as a reviewable diff
> **So that** "four consecutive months became six" is something I approved rather than discovered

- **Given** a change to a criteria tree **When** it is proposed **Then** it
  appears as a diff requiring approval before any determination uses it ·
  *Article VII*
- **Given** any completed determination **When** it is inspected **Then** the
  exact `policy_version_id` evaluated is recorded and the run is replayable ·
  *REQ-4*

**Status:** satisfied by git plus REQ-4. Written down because an unstated control
is not a control, and because this becomes real work when the criteria compiler
lands in week 2.

---

## Feature F3 — Cross-practice compatibility *(v1.2, v1.6; D105)*

Stories for versions after v1.1 carry a **Version** line. Their acceptance
criteria cite the statements in spec §11 and the gate that version names; the
REQ ids are minted when the version's first task opens.

### `US-10` Take a new practice's coverage rules without a rewrite

> **As** Dr. Vance
> **I want** a rheumatology tree and an ultrasound tree to load beside the bariatric ones and be checked by the same engine
> **So that** I learn which of the engine's assumptions were bariatric surgery's, before a second practice depends on it

**Version:** v1.2 · **Value:** the engine's claim is "what the tree
declares"; this is the first time a tree declares something bariatric surgery
never needed.

- **Given** a criteria tree compiled from a rheumatology coverage document
  **When** it is loaded **Then** every predicate names a kind the engine has,
  or the load fails naming the kind it lacks — it never abstains past an
  unbuilt predicate · *(REQ-57, T-91; A10)*
- **Given** a criterion the document states as a judgment **When** the tree
  declares it unclaimed **Then** the determination abstains on it with
  `NOT_EVALUATED_BY_THIS_SYSTEM` and never omits it · *(REQ-58, T-91; D101;
  A10)*
- **Given** a patient carrying the diagnosis and the concurrent drug the
  document names **When** the determination runs **Then** both criteria are
  `MET` citing the condition and the prescription, by set membership over
  structured resources in each value set's declared code system · *(REQ-59,
  T-92; Article II; A10)*
- **Given** a patient on a drug the document's limitation excludes **When**
  the determination runs **Then** the request is `NOT_COVERED` citing every
  offending prescription, and a chart carrying none produces no exclusion
  rather than a `MET` nobody can cite · *(REQ-60, T-92; A10)*
- **Given** an ultrasound request past the document's frequency limit **When**
  the determination runs **Then** the criterion is `NOT_MET` with each prior
  study cited, counted by Python · *(A10)*
- **Given** both trees loaded **When** the report is built **Then** it classes
  every criterion of every tree as evaluated by an existing kind, by a new
  kind, or unclaimed — and zero model calls were spent · *(A10)*

**Covers:** A10
**Ships:** the compatibility account in `eval/report.md`, and the first
predicate kinds bariatric surgery never used.

---

### `US-14` Two more practices, notes included

> **As** Dr. Vance
> **I want** a second pair of practices — one under a nationally quantified NCD — evaluated all the way through the notes
> **So that** the extractor, not only the rules engine, is shown to be declared by the tree rather than shaped by bariatric surgery

**Version:** v1.6 · **Value:** v1.2 left every note-only criterion unclaimed;
this is where the abstentions it declared become verdicts, on four practices.

- **Given** a tree declaring its own fact types **When** the bariatric tree
  declares `WmEvent` **Then** every existing recording replays unchanged ·
  *(A14)*
- **Given** a tree declaring a fact type the engine lacks **When** it is loaded
  **Then** the load fails, naming it · *(A14)*
- **Given** the rheumatoid and ultrasound trees **When** their note-only
  criteria are declared with fact types **Then** they resolve to verdicts with
  anchored spans, and their v1.2 abstentions are gone from the baseline by a
  committed `--update-baseline` · *(D27; A14)*
- **Given** four practices **When** the differential is measured **Then** the
  agentic path agrees with the oracle on every outcome, or the disagreement is
  reported · *(Amendment 1; A14)*

**Covers:** A14

---

## Feature F4 — Medical history review *(v1.3; D105)*

### `US-11` Show me what the chart implies but does not code

> **As** Sam
> **I want** the medications on the chart checked against what they are known to cause, with each suggested code coloured by how much evidence the chart holds for it
> **So that** a steroid patient's bone density loss is in the packet before the payer asks why it wasn't

**Version:** v1.3 · **Value:** a denial for an undocumented comorbidity that
the medication list already implied is the denial Sam did not see coming.

- **Given** an active corticosteroid and a DEXA observation past the table's
  threshold, with no osteoporosis condition coded **When** the review runs
  **Then** the suggestion is **green** — addable with no further evidence —
  and cites the observation · *(A11)*
- **Given** an active anticoagulant and a note documenting hypotension, with
  no observation past threshold **When** the review runs **Then** the
  suggestion is **yellow**, added with the anchored quote attached
  automatically, and the quote is verified blind · *(Article III, Article V;
  A11)*
- **Given** an active medication in the table and nothing on the chart
  **When** the review runs **Then** the suggestion is **red** and cannot enter
  a form without a written justification · *(A11)*
- **Given** any suggestion **When** the determination is emitted **Then** every
  criterion verdict is unchanged, and `would_affect` names the criteria whose
  value set contains the code, by set membership · *(Article II; A11)*
- **Given** a quote the anchorer refuses **When** the tri-state is assigned
  **Then** the suggestion is red, never yellow · *(A11)*
- **Given** any suggestion **When** its code is traced **Then** it resolves to
  one row of the knowledge table with a source the offline verifier covers ·
  *(A11)*

**Covers:** A11
**Note:** the model quotes; it never proposes a code and never picks a colour.

---

## Feature F5 — Sessions and submission *(v1.4, v1.5; D105)*

### `US-12` Keep my determinations as sessions I can come back to

> **As** Sam
> **I want** a determination created from a procedure — with or without ICD codes, typed by me or sent from another system — kept as a session with a status
> **So that** the checklist of what I am working on is the system's, not a spreadsheet beside it

**Version:** v1.4 · **Value:** the dashboard v2.0 draws is a list the system
already keeps.

- **Given** a procedure code and a patient, with or without ICD codes, from
  flags or from a JSON intake **When** a session is created **Then** both
  routes produce the same session object, in `CREATED` · *(A12)*
- **Given** a malformed intake **When** it is submitted **Then** it is a bad
  request, exit 1, and no session exists · *(A12)*
- **Given** a session **When** it is run **Then** it holds the determination
  and its `policy_version_id` and moves to `DETERMINED`; a second run is a
  new snapshot, never an edit · *(A12)*
- **Given** any lifecycle state **When** an illegal transition is attempted
  **Then** it raises and nothing is recorded · *(Article I; A12)*
- **Given** the session store **When** the plane check runs **Then** it holds
  ids and snapshots only — no policy text, no patient resource · *(Article
  VI; A12)*

**Covers:** A12
**Ships:** `session create | list | show | run`.

---

### `US-13` Let me review, complete and send the packet

> **As** Sam
> **I want** to read the determination, accept or reject the suggested codes, fill in what the form still needs, and send it to the payer, with the session then tracked as awaiting their decision
> **So that** the packet that leaves is one I signed off on, and I can see which ones are out

**Version:** v1.5 · **Value:** the point of the determination is the packet;
until v1.5 nothing leaves.

- **Given** a determined session **When** Sam reviews it **Then** her edits
  append to a review log beside the determination, whose bytes are unchanged ·
  *(A13)*
- **Given** a red suggestion **When** the form is assembled without a
  justification **Then** the packet is refused, naming the code · *(A13)*
- **Given** a reviewed session **When** Sam submits it **Then** an
  email-shaped packet is written to the simulated payer's outbox, every
  citation in it slices back, and the session is `AWAITING_DECISION` · *(A13)*
- **Given** a session not yet in review **When** submission is attempted
  **Then** it raises; the system never decides to transmit · *(A13)*
- **Given** a payer's simulated decision **When** it is recorded **Then** the
  session closes with the outcome and the date · *(A13)*

**Covers:** A13
**Note:** this version rewords spec §1's "does not submit" to "transmits only
on the reviewer's explicit action after review".

---

## Feature F6 — Reviewer UI *(v2.0; D105; tentative)*

### `US-15` Work from a dashboard, not a terminal

> **As** Sam
> **I want** a dashboard of my sessions, each opening to its determination with the criteria, the evidence beside them and the coloured code suggestions, the form, and a send button
> **So that** clearing a chart is one screen, not a sequence of commands

**Version:** v2.0 · **Value:** every persona-facing behaviour before this
version is reachable only by someone who reads JSON.

- **Given** the dashboard **When** it renders **Then** it lists what `session
  list` lists, with the same statuses, and offers a create form for a
  procedure with or without ICD codes, typed or pasted from an upstream
  system · *(A15)*
- **Given** a session opened **When** the determination renders **Then** each
  criterion shows its verdict, its cited spans highlighted in the note beside
  the structured evidence, and the gap list · *(A15)*
- **Given** the suggestions panel **When** Sam acts **Then** green adds with
  one action, yellow adds with its citation attached, and red opens the
  justification field at that point in the form · *(A15)*
- **Given** a reviewed session **When** Sam sends it **Then** the simulated
  email is previewed, written to the outbox, and the dashboard shows the
  session awaiting approval · *(A15)*
- **Given** any UI action **When** it is traced **Then** it maps to one CLI
  verb with identical output, and the templates carry no logic · *(A15)*

**Covers:** A15
**Status:** tentative — rows open after v1.6 closes, and the framework is
decided in the app shell's entry.

---

### `US-16` Ask one question about two payers

> **As** Sam
> **I want** the same request answered under the payer that actually covers
> this patient, and a regional rule that can never be looser than the national
> one it sits under
> **So that** I stop keeping one payer's thresholds in my head while reading
> another's chart

**Version:** v2.1 · **Value:** every answer the system has given so far is
Medicare's. A second payer is the first time *whose rule is this* has more than
one answer, and the first time a regional rule can quietly exceed its national
grant.

- **Given** two payers whose policies both bind a procedure in one state
  **When** the request names a payer **Then** it resolves to that payer's tree,
  and neither answer depends on the order the trees were loaded · *(A16)*
- **Given** a payer this store does not serve **When** the request names it
  **Then** the answer says so, and it is not the answer for a state no tree
  serves · *(REQ-55's distinction; A16)*
- **Given** a regional tree declaring a constant looser than the national floor
  it cites **When** it is loaded **Then** the load fails naming the constant,
  the floor and the direction — a regional rule may be stricter and never
  broader · *(A16)*
- **Given** a determination **When** it is read **Then** it names the payer and
  the scope of the tree that produced it, so no figure is quoted as another
  payer's · *(A16)*

**Covers:** A16
**Ships:** resolution by payer, and the first check of a relation
`national_floor` has asserted by its name since T-01 *(D112)*.

---

### `US-17` See what a commercial payer would ask that Medicare does not

> **As** Dr. Vance
> **I want** a policy written the way a private plan writes one — step therapy
> with a stated duration, a screening before initiation, a lab value in a
> window — evaluated by the same engine
> **So that** I learn whether the engine is Medicare-shaped before anyone
> points it at commercial work

**Version:** v2.2 · **Value:** the practices tested so far are all CMS's, and
CMS's drug documents quantify almost nothing — a fact T-92 established by
fetching four of them. The criteria this system was built to evaluate are
commonplace in commercial utilisation management and absent from Medicare's
Part B drug policies.

- **Given** a policy the project synthesized **When** any artifact cites it
  **Then** that artifact says the policy is synthetic, and it appears nowhere in
  the fetched corpus or its hashes · *(A17)*
- **Given** a patient whose step-therapy trial is shorter than the policy
  requires **When** the determination runs **Then** the criterion is `NOT_MET`
  citing the prescriptions, with the duration computed by Python over their
  dates · *(Article II; A17)*
- **Given** a policy requiring a screening the chart does not carry **When** the
  determination runs **Then** the criterion abstains naming what to collect, and
  never reports it as failed · *(Article IV; A17)*
- **Given** the commercial tree loaded **When** the report is built **Then** the
  compatibility account covers it beside the CMS practices, and zero model calls
  were spent · *(A17)*

**Covers:** A17
**Ships:** the predicate kinds v1.2 expected and could not earn, against a
document that states them *(D111, D112)*.

---

## Not stories

Tracked as spikes or technical tasks.

| Item | Type | Why not a story |
|---|---|---|
| Spike 001 | Spike | Answers a question, ships no behavior |
| Repo and environment setup | Task | Enabler |
| Synthea generation | Task | Test data, not a user outcome |
| Value set verification | Task | Enabler under US-2 |
| Note synthesizer | Task | Test data, not a user outcome |
| Plane separation check | Task | Enforces Article VI, no user outcome |
| US-5.5 Orchestration (T-61–T-68 on the board) | Task group | The agentic differential against the deterministic oracle — a measurement, not a persona-visible behavior. Numbered like a story only so its tasks have a home on a board organised by story *(D72)* |
| Predicate vocabulary audit (T-91, v1.2) | Task | Makes the tree schema say what it already assumes; no persona sees it *(D105)* |
| Tree-declared extraction (T-107, v1.6) | Task | The extractor becomes generic over declared fact types; a refactor the four-practice account measures, not a behavior *(D105)* |

If one of these ends up phrased "As a developer, I want…", it belonged in this
table.
