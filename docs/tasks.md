# Tasks

Grouped under the story each one serves. Every task carries a runnable exit
condition. `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked
*(Article VIII)*

Build order is vertical. US-1 ships a running end-to-end system on day one; each
story after it makes that system do more. Task IDs are stable — renumbering
breaks every reference to them.

---

## Enablers — before any story

### `[x] T-00` Spike 001: extraction fidelity on hand-written notes
**Type:** spike · **REQ:** 8, 9, 10 · **Informs:** US-4 · **Blocks:** T-15
**Timebox:** four hours
**Exit:** `python spike/spike_001/run.py --verify` —
- extraction runs over five hand-labeled notes, at least one carrying
  missed-visit dates inside a gap month
- writes `results.json` with per-note event precision, event recall, and the
  REQ-9 exclusion count
- every produced span slices non-empty
- `docs/decisions.md` holds a Spike 001 entry quoting the measured precision

The gate asserts a number was measured, not that it cleared a bar. A spike
answering "unreliable" is a successful spike; the bar is the second kill
criterion. *(D10)*

### `[x] T-01` Hand-compile NCD 100.1 criteria tree
**REQ:** 32, 37, 39 · **Depends:** T-02, for the constants and their spans
**Exit:** `pytest tests/test_criteria_tree.py` — the file parses and every
policy-supplied constant is present and typed: c2's recency window,
`c5_min_documented_events`, and `discrepancy_tolerance` per reconciled fact. Any
provisional value carries `provisional: true` naming the open question it awaits.

**Closed by D23.** `data/policies/ncd_100_1_jf.json`, `policy_version_id`
`ncd-100.1-jf-v1`. Sourced: BMI ≥ 35 inclusive, one comorbidity, c2 at 12 months,
c3 at four consecutive months, c4 per-month BMI, c5 requiring both diet and
activity. Provisional and flagged: criterion (a)'s lookback (question 4),
`discrepancy_tolerance` (question 5), `c5_min_documented_events` (question 6).

Beyond the stated exit, every sourced constant carries a
`(document_id, char_start, char_end)` the test slices out of the hashed corpus,
and the tree records the corpus hashes so a moved document fails the gate rather
than mis-citing quietly. Mutation-tested eight ways — a missing constant, a wrong
type, a ghost open question, a null without its flag, a shifted offset, a stale
corpus hash, a criterion dropped from the decision expression, and a numeric
constant sourced to the NCD — each caught by the test that should catch it.

### `[x] T-02` Download and hash policy source documents
**Serves:** US-2, since spans anchor here · **Answers:** open questions 1, 2
**Exit:** `python scripts/verify_sources.py` — every source document present,
content-hashed, re-downloadable to the same hash, plus `answers.json` resolving
three questions, each with a quote and a `(document_id, char_start, char_end)`:

1. c5's documentation frequency — monthly or once
2. c2's recency window in months
3. whether 43775 is nationally covered

An answer without a span does not close this task. Criteria-tree constants trace
to source text the same way a determination's claims do.

**Closed by D21 and D22.** Two documents, stored as extracted text because the
MCD emits a fresh CSP nonce per response and raw HTML has no reproducible hash:
`ncd_100_1` (national) and `a53028` (Noridian, **Jurisdiction F — not
national**). Answers: c5 is **monthly**; c2's window is **12 months**, and the
same sentence fixes c3's run at four consecutive months; **43775 is not
nationally covered and not nationally non-covered either** — CMS delegated it to
the MACs in 2012 and this MAC covers it, so T-25 and E3 are wrong (T-35, T-36).
Mutation-tested: altering a document, shifting an offset by one, bumping the
extractor version, and a quote that is not verbatim each fail the gate.

### `[x] T-03` Repo skeleton and environment
**Timebox:** two hours
**Exit:** `python scripts/check_skeleton.py` — target layout present,
`google-adk` imports at exactly 2.8.0, `pa_agent.agent` imports, and an `adk web`
subprocess answers HTTP 200 on `localhost:8000/list-apps` naming the agent,
within the timeout before the script terminates it. *(D6, refined by D16)*
The probe hits `/list-apps`, not `/`: on 2.8.0 `GET /` returns 307 to `/dev-ui/`.

---

## `US-1` Screen out non-covered procedures — day 1

Closes before you sleep on day one.

### `[ ] T-09` Data contracts as Pydantic models
**REQ:** 5, 22 · **Depends:** T-00
**Exit:** `pytest tests/test_schemas.py` — `Criterion`, `EvidenceSpan`,
`CriterionVerdict`, `CriterionResult`, `Determination`, `WmEvent`, `CallMetrics`.

### `[ ] T-10` Eval harness with US-1 acceptance cases, failing
**Depends:** T-09
**Exit:** `python eval/run_eval.py` runs and reports E3 failing
US-1 cannot close without acceptance tests, so the harness arrives with the first
story. A failing harness is the correct state.

### `[ ] T-24` Policy resolver and short-circuit sc1
**REQ:** 1, 2, 4 · **Depends:** T-09
**Exit:** `pytest tests/test_resolver.py` — E3 returns `NOT_COVERED`, unknown code
returns `NO_POLICY_FOUND`, model-call counter reads zero

### `[ ] T-25` Determination assembly, minimal
**REQ:** 4, 21 · **Depends:** T-24
**Exit:** `python -m pa_agent.cli --patient X --procedure 43775` prints a
`NOT_COVERED` determination carrying `policy_version_id`, model-call counter zero
43775 is sleeve gastrectomy, assumed not nationally covered so the case exits
through sc1. Chosen from memory — T-02 confirms it against source.

**US-1 closes when:** `python eval/run_eval.py` reports E3 passing.

---

## `US-2` Structured criteria with citations — day 2

### `[ ] T-04` Generate and select the Synthea population
**Exit:** `python scripts/select_patients.py --verify` — six bundles, seed
recorded, BMI spanning 33 to 45

### `[ ] T-05` Rebuild the comorbidity value set from real codes
**REQ:** 12 · **Depends:** T-04 · **Blocks:** T-13
**Exit:** `pytest tests/test_valueset.py` — every code appears in the population,
`status` is `VERIFIED`
Currently written from memory. A wrong code fails criterion b silently for every
patient, with no error anywhere.

### `[ ] T-08` Document index with character offsets
**REQ:** 6, 7 · **Depends:** T-02 · **Blocks:** T-11
**Exit:** `pytest tests/test_index.py` — content hash per document, round-trip
slice returns the original for 1000 random spans

### `[ ] T-11` Span validator
**REQ:** 6 · **Depends:** T-08, T-09
**Exit:** `pytest tests/test_spans.py` — fabricated, off-by-one, and reversed
spans all rejected; no model imported in the module

### `[ ] T-12` FHIR fact extractor
**REQ:** 11, 12 · **Depends:** T-04, T-09
**Exit:** `pytest tests/test_fhir.py` — BMI observations and Conditions with
dates, from all six bundles

### `[ ] T-13` Deterministic criteria (a) and (b)
**REQ:** 11, 12 · **Depends:** T-05, T-12
**Exit:** `pytest tests/test_criteria_ab.py` — includes the BMI 35.0 boundary;
asserts zero model calls

**US-2 closes when:** E12 passes and every span produced in the run survives T-11.

---

## `US-3` Categorical exclusion — day 2, late

### `[ ] T-14` Short-circuit sc2
**REQ:** 3 · **Depends:** T-13
**Exit:** `pytest tests/test_short_circuits.py` — E2 returns `NOT_COVERED` with a
model-call counter of zero

**US-3 closes when:** E2 passes. Half a day at most; US-2 built everything it needs.

---

## `US-4` The narrative criterion — days 3 and 4

The big one. Everything before it was lookup.

### `[ ] T-06` Author fact manifests
**REQ:** 8, and every case in spec §6 · **Depends:** T-04
**Exit:** `pytest tests/test_manifests.py` — one per patient, every edge case
covered
Ground truth. Written before the notes exist.

### `[ ] T-07` Manifest-driven note synthesizer
**REQ:** 8, 9 · **Depends:** T-06
**Exit:** `pytest tests/test_notes.py` — every note honors its manifest, by
assertion rather than by reading
E9 requires missed-visit dates sitting in the gap month.

### `[ ] T-15` `wm_events` extraction agent
**REQ:** 8, 9, 10, 35, 38 · **Depends:** T-00, T-07, T-11
**Exit:** `pytest tests/test_extraction.py` — correct events on the five
`spike/spike_001/notes/` cases and three synthesized notes; every span passes
T-11; E8's note yields zero `wm_events` and at least one `program_assertions[]`
span
The only place in the system where a model exercises judgment. The spike's notes
double as regression cases.

### `[ ] T-31` `gap_reason` on `INSUFFICIENT_EVIDENCE`
**REQ:** 31 · **Depends:** T-09 · **Blocks:** T-16, T-17, T-19, T-33
**Exit:** `pytest tests/test_gap_reason.py` — a `GapReason` enum with
`NO_EVIDENCE_RETRIEVED`, `UNSUBSTANTIATED_ASSERTION`, `VERIFIER_REJECTED` and
`SOURCE_CONFLICT`; the field required on any `CriterionResult` resolving
`INSUFFICIENT_EVIDENCE` and rejected on any other verdict; E7, E8 and E10b
carrying three different values
Separate from T-26: `gap_reason` describes an honest abstention, `error_code`
describes a fault, and one field for both is the collapse Article IV forbids.

### `[ ] T-16` Deterministic predicates c1 through c5
**REQ:** 13, 14, 15, 16, 32, 36, 37, 40 · **Depends:** T-15, T-31
**Exit:** `pytest tests/test_criteria_c.py` —
- E4, E5, E6, E7, E8, E9, E11 all correct
- c1 `MET` on a single event, `INSUFFICIENT_EVIDENCE` on zero
- c2 reads its window from the criteria tree, not a constant
- c3 `NOT_MET` on a one-to-three-month run, `INSUFFICIENT_EVIDENCE` on zero
  events — two distinct cases
- c4 `NOT_MET` on E6, asserting no BMI derived from weight plus on-file height
- c5 reads `c5_min_documented_events` from the tree
- c4 and c5 return `INSUFFICIENT_EVIDENCE` when c3 fails
- E7 carries `NO_EVIDENCE_RETRIEVED`, E8 `UNSUBSTANTIATED_ASSERTION`
- identical verdicts across three runs

Test the empty list, a single event, and events out of chronological order.

### `[ ] T-33` Source reconciliation for criterion (a)
**REQ:** 31, 34, 39 · **Depends:** T-13, T-15, T-31
**Exit:** `pytest tests/test_reconciliation.py` — E10 (same side of 35.0, beyond
tolerance) keeps the structured verdict and records one `discrepancies[]` entry;
E10c (below tolerance) records none; E10b (34.8 against 36.2) resolves
`INSUFFICIENT_EVIDENCE` with `gap_reason` `SOURCE_CONFLICT`; the gap list is
untouched in all three; no model call; tolerance read from the criteria tree
Runs after extraction rather than inside T-13, because the note BMI does not
exist until T-15 and US-2 makes no model call. *(D11)*

### `[ ] T-18` Workflow graph
**REQ:** 1, 4, 19 · **Depends:** T-13, T-16
**Exit:** `pytest tests/test_workflow.py` — fan-out, fan-in, retry, and
fail-closed all exercised; grep the module for branching on model output and find
none

### `[ ] T-20` Cost and latency instrumentation
**REQ:** 22 · **Depends:** T-15
**Exit:** `python eval/run_eval.py` prints per-run token counts and wall time
Lands with the first model call, not on day five. *(Article X)*

**US-4 closes when:** E4, E5, E6, E7, E9, E10, E10b, E10c and E11 all pass.

---

## `US-5` The gap list — day 4

### `[ ] T-19` Aggregator and gap list
**REQ:** 19, 20, 21, 31, 39 · **Depends:** T-18, T-31
**Exit:** `pytest tests/test_determination.py` — boolean tree evaluated in
Python; gap list populated; documentation gaps distinguishable from substantive
failures by `gap_reason` rather than by prose; `discrepancies[]` surfaced as a
separate list with no entry appearing on both

**US-5 closes when:** E1 and E8 pass.

---

## `US-6` Trustworthy citations — day 4

### `[ ] T-17` Blind verifier
**REQ:** 17, 18, 31 · **Depends:** T-15, T-31
**Exit:** `pytest tests/test_verifier.py` — mismatched span and verdict rejected;
the verifier's input contains no reasoning trace and no other criterion; a
rejection resolves the criterion to `INSUFFICIENT_EVIDENCE` with `gap_reason`
`VERIFIER_REJECTED`, spends exactly one verifier call, and still emits a
`Determination`

**US-6 closes when:** a verifier rejection resolves to `INSUFFICIENT_EVIDENCE`
end to end, carrying `VERIFIER_REJECTED`.

---

## `US-7` Where the system stops being reliable — day 5

### `[ ] T-21` Expand the eval set to all of spec §6
**Depends:** T-06, T-10
**Exit:** `python eval/run_eval.py` runs every case in spec §6, all labeled

### `[ ] T-22` Metrics report
**Depends:** T-20, T-21
**Exit:** `eval/report.md` with per-criterion precision, span validity rate,
abstention rate, coverage/accuracy curve, cost and latency
The curve is the deliverable. Name the threshold where abstention reaches one.

### `[ ] T-28` Baseline and base rate in the metrics report
**Depends:** T-22
**Exit:** `eval/report.md` contains the `MET` base rate and an always-`MET`
baseline score next to measured precision
A2 requires it: a precision figure without its base rate does not satisfy the gate.

### `[ ] T-23` README
**Depends:** T-22
**Exit:** `python scripts/check_req_coverage.py` — every REQ maps to a passing
check

**US-7 closes when:** A1, A2, A3, A5, A6, A7 and A8 all hold. A4 closes under
US-1 and US-3, A9 under US-9.

---

## `US-8` Policy governance

Satisfied by git plus REQ-4. Becomes real work when the criteria compiler lands
in week 2.

---

## `US-9` Withhold what the system couldn't compute

### `[ ] T-26` `ERROR` state in the data contracts
**REQ:** 18a, 23, 24, 26, 30 · **Depends:** T-09 · **Blocks:** T-29, T-30 ·
**Gates:** A9
**Exit:** `pytest tests/test_error_state.py` — `ERROR` on `CriterionVerdict`; an
`ErrorCode` enum whose every member declares retryable or terminal; `error_code`
and `error_detail` on `CriterionResult`; a `Determination` validator raising
`ValidationError` when any criterion is `ERROR`; an unclassified code defaulting
to terminal
The validator assertion is required — a determination constructible over an
`ERROR` is the failure REQ-24 exists to prevent.

### `[ ] T-29` Fault injection suite and no-silent-failure audit
**REQ:** 23, 24, 27, 29 · **Depends:** T-11, T-15, T-26 · **Gates:** A9
**Exit:** `pytest tests/test_fault_injection.py` — four tests, one per failure
point: the model call raises, the model returns unparseable JSON, span offsets
point past the end of the document, a predicate raises. Each asserts `ERROR` with
the right `error_code`, no `Determination` emitted, and a non-zero CLI exit. The
raising call is retryable, so it asserts `ERROR` only after N attempts and checks
the count; the other three assert `ERROR` on first occurrence with one model call
spent. Greps the package for bare `except:` and `except Exception:` without a
re-raise and finds none.

### `[ ] T-30` `ERROR` accounting in the eval harness
**REQ:** 28 · **Depends:** T-10, T-26 · **Gates:** A9
**Exit:** `pytest tests/test_metrics_error_accounting.py` — a seeded `ERROR`
leaves the reported abstention rate unchanged
An `ERROR` counted as an abstention would make T-22's curve report caution where
there was a crash.

**US-9 closes when:** A9 holds — no determination carrying an `ERROR` can be
emitted, and a seeded `ERROR` leaves the abstention rate unchanged.

---

## Attached to no story

Real work with a runnable exit that delivers no user outcome.

### `[ ] T-27` Retrieval recall instrumentation
**REQ:** 25 · **Depends:** T-21
**Exit:** `python eval/run_eval.py` prints per-criterion recall@k against the
manifest ground truth
Makes D4's reversal condition measurable against the 0.85 kill criterion.

### `[ ] T-32` Plane separation check
**REQ:** 33 · **Depends:** T-09, T-12, T-24 · **Gates:** Article VI
**Exit:** `pytest tests/test_planes.py` — walks the import graph from the policy
modules and finds no path to a patient-data module, walks it from the
patient-data modules and finds no path to the policy corpus or an index over it,
and asserts `Criterion` is the only type crossing

### `[x] T-34` Pin the model a measurement runs against
**Guards:** D19 · **Depends:** none · **Discovered in:** T-00
**Timebox:** one hour
**Exit:** `pytest tests/test_model_pin.py` —
- the model a bare `python spike/spike_001/run.py` would measure on equals the
  model `results.json` records, so re-running cannot silently replace D19's
  finding with one measured somewhere else
- no model identifier appears as a bare string literal in tracked Python outside
  the single module that defines the pin

`DEFAULT_MODEL` in `run.py` reads `gemini-2.5-flash-lite` while D19 was measured
on `gemini-3.5-flash-lite`. Nothing catches that today: a bare re-run would
overwrite a recorded finding with numbers from a different model and every gate
would still return zero, because each check is internally consistent with the
file it reads. A finding whose provenance can drift without failing anything is
not a finding.

Second bullet covers the undocumented model edit sitting in `pa_agent/agent/agent.py`.
That file is `adk create` scaffolding T-15 replaces, so the fix is a decision about
which model the pin names, not a second literal somewhere else.

Per D5 the entry naming the pinned model also has to say which tier it runs
against, since development is AI Studio and final evals are Vertex — the pin is
one identifier with two credentials behind it, and conflating them is how a demo
ends up training on submitted data.

**Closed by D20.** `pa_agent/model_pin.py` pins `gemini-3.5-flash-lite`, measured
against AI Studio. `run.py` and `agent/agent.py` import it; all three former
literals are gone. Each check was mutation-tested: moving the pin, restoring a
literal default, and re-adding a third identifier each fail the suite.

### `[ ] T-35` Re-point E3 and T-25 at a genuinely non-covered procedure
**REQ:** 2 · **Blocks:** T-25, and US-1's close · **Discovered in:** T-02 *(D22)*
**Exit:** `pytest tests/test_resolver.py` — E3's procedure code is one NCD 100.1
names as non-covered for all Medicare beneficiaries, and the choice cites
`ncd_100_1` with a span the way T-02's answers do. 43775 appears in a covered
case instead.

The NCD's national non-covered list is the candidate pool: open adjustable
gastric banding, open sleeve gastrectomy, open and laparoscopic vertical banded
gastroplasty, intestinal bypass surgery, gastric balloon. Pick one, span it, and
carry the code through spec §6's E3 row and T-25's CLI example.

Not folded into T-25: the code is wrong in `docs/spec.md` too, and a task that
edits a higher-precedence document is its own decision.

### `[ ] T-36` Decide whether sc1 needs a third outcome
**REQ:** 1, 2 · **Depends:** T-35 · **Discovered in:** T-02 *(D22)*
**Exit:** a decision entry resolving it, and `pytest tests/test_resolver.py`
asserting the chosen behavior for a procedure NCD 100.1 leaves to MAC discretion
— distinct from both `NOT_COVERED` and a covered code, or explicitly and in
writing not distinct.

REQ-1 returns `NO_POLICY_FOUND`, REQ-2 returns `NOT_COVERED`, and neither
describes "no national determination, delegated to the contractor." Under a
single-jurisdiction corpus (D21) the MAC's article settles it and the question
looks academic; it stops being academic the moment a second jurisdiction exists,
and Article IV's whole argument is that states which read alike must not merge
before anyone notices.

### `[ ] T-37` Reconcile REQ-37 with the source: is c5 a count or a rate?
**REQ:** 37, 40 · **Blocks:** T-16 · **Discovered in:** T-01 *(D23)* ·
**Answers:** open question 6
**Exit:** a decision entry resolving it, then `pytest tests/test_criteria_tree.py`
— `c5_min_documented_events` is either sourced and no longer `provisional`, or
replaced by a rate constant shaped like c4's `documentation_rate`. A seven-month
qualifying run documenting diet and activity in four of its months must resolve
the way the entry says it should, and the case must exist in the test.

A53028 governs c4 and c5 in one sentence — "monthly documentation of patient's
weight and BMI, current dietary regimen and physical activity". REQ-40 renders
the first half as *every month of the qualifying run*; REQ-37 renders the second
as *at least `c5_min_documented_events` events*. One sentence, two shapes.

The count shape is permissive in the false-`MET` direction, which is the wrong
direction for a system whose entire argument is that it abstains rather than
guesses. T-01 landed the constant at 4 and flagged it rather than rewriting
REQ-37 while implementing it — a requirement changed by the task that implements
it is a requirement nobody agreed to.

Do this before T-16 builds the predicate. Afterwards it is a behavior change with
eval cases already labeled against it.

---

## Working rules

1. One task in progress at a time.
2. Close stories, not layers. A story with four of five tasks done has delivered
   nothing.
3. A task that cannot close without violating the constitution is a wrong task.
   Rewrite it; do not amend the constitution.
4. Discovered work becomes a new numbered task, not a silent addition to the
   current one.
5. Log the decision before writing the code it justifies. *(Article IX)*
