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
- **Given** a range of fail-closed thresholds **When** the harness runs **Then**
  it produces a coverage/accuracy curve including the point where abstention
  reaches one and the system is useless · *A5, A8*
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

If one of these ends up phrased "As a developer, I want…", it belonged in this
table.
