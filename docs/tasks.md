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

*Superseded in part by T-37:* `c5_min_documented_events` is gone. c5 carries
c4's `documentation_rate` instead, sourced rather than provisional, so two
constants are flagged now and not three *(D24)*.

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

### `[x] T-09` Data contracts and the two storage ports
**REQ:** 5, 22, 33, 41 · **Depends:** T-00 · **Blocks:** everything
**Exit:** `pytest tests/test_schemas.py` —
- the models: `Criterion`, `EvidenceSpan`, `CriterionVerdict`, `CriterionResult`,
  `Determination`, `WmEvent`, `CallMetrics`
- `Document` carrying text and `sha256`, so a span's target is addressable and
  its content verifiable without touching whatever stored it
- two Protocols, `PolicyStore` and `PatientStore`, with the methods D25 names
- file-backed implementations of both, reading the artifacts that exist today:
  `LocalPolicyStore` over `data/policies/` returns the T-01 tree and the T-02
  corpus with hashes matching `sources.json`; `LocalPatientStore` is defined and
  raises `NotImplementedError` until T-04 lands bundles for it to read
- no single class satisfies both Protocols, asserted directly *(REQ-41)*
- a round trip: `get_document()` then slice at a T-02 answer's offsets returns
  that answer's quote — the storage port preserves Article III's guarantee

**Scope changed by D25** *(was: the seven models alone)*. Troy's end state is a
production deployment against a code database and a patient database. The ports
are the whole of what that costs the code written now; everything above an
adapter is unchanged when the adapter becomes Postgres and FHIR. Defined here it
is a few hours. Defined after T-25 it is a rewrite of US-1 and US-2.

Criteria trees do **not** move into a store's write path. Article VII wants a
diff and a reviewer for every clinical rule change, and `get_tree()` reading a
deploy-time projection satisfies it while an `UPDATE` does not. *(D25)*

**Closed.** `pa_agent/contracts.py` and `pa_agent/stores/{policy,patient}.py`;
39 tests in `tests/test_schemas.py`. Three invariants are enforced by validators
rather than by convention, so the objects that break them cannot be built: a
`Document` whose text does not hash to its record (REQ-7), a span that is
reversed or negative (Art. III), and a `CriterionResult` whose spans disagree
with its verdict in **either** direction (REQ-5 — an abstention carrying a
citation is refused as firmly as a `MET` carrying none).

`Criterion.require()` raises on a provisional constant instead of returning
`None`, which makes "T-13 must not invent a default" mechanical rather than
advisory. Both unimplemented halves raise and name their task —
`LocalPolicyStore.resolve` cites T-38, `LocalPatientStore` cites T-04 — because
a store that returned `None` or `[]` would report `NO_POLICY_FOUND` for all of
Medicare, or manufacture E7 for every patient, while every downstream test
agreed with it.

Mutation-tested six ways, each caught by the test that should catch it: dropping
the REQ-5 validator, adding `ERROR` to `CriterionVerdict` without doing T-26,
making `require()` return a provisional value, a class satisfying both
Protocols, a package-level re-export in `stores/__init__.py`, and
`get_document` hashing the file it just read instead of checking it against
`sources.json`.

### `[x] T-10` Eval harness with US-1 acceptance cases, failing
**Depends:** T-09
**Exit:** `python eval/run_eval.py` runs and reports E3 failing
US-1 cannot close without acceptance tests, so the harness arrives with the first
story. A failing harness is the correct state.

**Closed by D27.** `eval/run_eval.py` over `eval/cases.json`, gated against
`eval/baseline.json`. The exit code means *observed matches the baseline*, not
*every case passed* — Article VIII wants zero on a day when nothing the harness
grades exists, and drift in **either** direction fails, so a case that starts
passing is acknowledged in a diff rather than noticed by someone reading a table.

Three statuses, and `BLOCKED` never folds into `FAIL`: "the system answered
wrongly" and "the component that would answer does not exist" have different next
actions and only one of them names a task. Blocking is *discovered* from the
`NotImplementedError` the system raises, never declared on the case — a
`blocked_by` list would keep naming T-35 after T-35 landed, which is T-39's defect
with different spelling.

**E3 lands labeled and without a procedure code.** D22 disproved 43775 and T-35
has not picked its replacement, so the case reports `BLOCKED/CASE_UNSPECIFIED`.
Landing the disproved code with a `known_wrong` marker would put an acceptance
case in the eval set that asserts the opposite of the source.

The scorer checks itself on every run — seven synthetic scorings, covering every
branch no real case can reach until T-25 produces a `Determination`. Mutation-tested
seven ways, each caught by the check that should catch it: a baseline claiming
`PASS`, a case absent from the baseline, a baseline case absent from the set, and
— with the T-25 seam stubbed — a correct determination, a wrong outcome, a spent
model call against A4's zero budget, and a scorer edited to score a wrong outcome
`PASS` (self-check fails, exit 2, and the report below it is suppressed).

### `[ ] T-24` Policy resolver and short-circuit sc1
**REQ:** 1, 2, 4 · **Depends:** T-09
**Exit:** `pytest tests/test_resolver.py` — E3 returns `NOT_COVERED`, unknown code
returns `NO_POLICY_FOUND`, model-call counter reads zero

### `[ ] T-25` Determination assembly, minimal
**REQ:** 4, 21 · **Depends:** T-24
**Exit:** `python -m pa_agent.cli --patient X --procedure 43842` prints a
`NOT_COVERED` determination carrying `policy_version_id`, model-call counter zero
43842 is open vertical banded gastroplasty, which NCD 100.1 names non-covered for
all Medicare beneficiaries unconditionally, so the case exits through sc1.

*Was 43775, chosen from memory.* T-02 confirmed the opposite — CMS delegated
stand-alone laparoscopic sleeve gastrectomy to the MACs in 2012 and A53028 records
this MAC covering it *(D22)* — and T-35 re-pointed it *(D28)*. **43775 belongs in
a covered case**, and T-38 is where it lands: the contractor-determined set, in
neither of the other two. Do not build that case here.

**US-1 closes when:** `python eval/run_eval.py` reports E3 passing — which is
the commit moving E3 to `PASS` in `eval/baseline.json`, after T-35 supplies the
code, T-38 the procedure sets, T-24 the resolver and T-25 determination assembly.
Until then the gate returns zero on a `BLOCKED` E3 and fails the moment that
changes without being recorded *(D27)*.

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
**REQ:** 11, 12 · **Depends:** T-05, T-12, T-39
**Exit:** `pytest tests/test_criteria_ab.py` — includes the BMI 35.0 boundary;
asserts zero model calls
Closes open question 4, so T-39 comes first: until it does, unflagging the
lookback is not checked by anything.

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
- c5 reads `documentation_rate` from the tree and requires diet and activity in
  every month of the run, not a count of qualifying events *(T-37, D24)*. A
  seven-month run documented in four months is `NOT_MET`.
- c4 and c5 return `INSUFFICIENT_EVIDENCE` when c3 fails
- E7 carries `NO_EVIDENCE_RETRIEVED`, E8 `UNSUBSTANTIATED_ASSERTION`
- identical verdicts across three runs

Test the empty list, a single event, and events out of chronological order.

### `[ ] T-33` Source reconciliation for criterion (a)
**REQ:** 31, 34, 39 · **Depends:** T-13, T-15, T-31, T-39
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
**REQ:** 33, 41 · **Depends:** T-09, T-12, T-24 · **Gates:** Article VI
**Exit:** `pytest tests/test_planes.py` — walks the import graph from the policy
modules and finds no path to a patient-data module, walks it from the
patient-data modules and finds no path to the policy corpus or an index over it,
and asserts `Criterion` is the only type crossing

Second assertion, added by D25: no module outside `pa_agent/stores/` opens a file
path, holds a connection, or names a storage location, and no class satisfies
both Protocols. **Scope that scan to `pa_agent/`** *(D27)*: `tests/` opens
fixtures and `eval/run_eval.py` opens its own case labels and baseline, and
neither is the system under test — a scan over the whole tree would fail on the
grader and get relaxed until it stopped asserting anything. Under the production target the planes are two connections rather
than two package trees, and an assertion that only reads imports would pass a
module that reaches the wrong database at runtime.

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

### `[x] T-35` Re-point E3 and T-25 at a genuinely non-covered procedure
**REQ:** 2 · **Blocks:** T-25, and US-1's close · **Discovered in:** T-02 *(D22)*
**Exit:** `pytest tests/test_e3_code.py` and `python eval/run_eval.py` — E3's
procedure code is one NCD 100.1 names as non-covered for all Medicare
beneficiaries, the choice cites `ncd_100_1` with a span the way T-02's answers do,
and the span is asserted to fall inside the non-covered list rather than anywhere
else in the document. 43775 appears in a covered case instead.

*Exit condition rewritten by D28, twice over.* It read `pytest
tests/test_resolver.py`, a file **T-24** creates — and T-24 depends on T-38, which
depends on this task, so T-35 could not close until the thing it blocks was built.
That is the defect D26 named in T-36, sitting in T-35's own text. It also asked
for a *code* spanned to `ncd_100_1`, which is impossible: see the closing note.

The NCD's national non-covered list is the candidate pool: open adjustable
gastric banding, open sleeve gastrectomy, open and laparoscopic vertical banded
gastroplasty, intestinal bypass surgery, gastric balloon. Pick one, span it, and
carry the code through spec §6's E3 row and T-25's CLI example.

Not folded into T-25: the code is wrong in `docs/spec.md` too, and a task that
edits a higher-precedence document is its own decision.

**Closed by D28. E3 is 43842, open vertical banded gastroplasty.** NCD 100.1 names
it in a list scoped "non-covered for all Medicare beneficiaries" with no date
qualifier and no delegation clause — the two failure modes D22 caught in 43775.
Both spans are unique in the hashed document: `ncd_100_1[7076:7166]` scopes the
list, `[7287:7338]` names the procedure, and A53028 corroborates at `[9483:9629]`
and `[9848:9898]` without exercising discretion over it.

**Neither document binds any non-covered procedure to a code**, which is the
finding that reshaped the task. `ncd_100_1` holds no procedure codes at all and
states the reason itself; A53028 names one bariatric code, 43775, the code D22
disproved. So the code carries **two citations of different classes**: a
`coverage_claim` spanned into the hashed corpus and checked by slicing, and a
`code_binding` marked `source_class: "external_code_system"`, `in_corpus: false`,
against new open question 7. The gate asserts the artifact is honest about not
knowing; it does **not** assert 43842 is the right CPT code, because nothing here
can verify that and a test that pretended to would be asserting someone's recall
— which is how 43775 reached the spec. **T-40** carries the gap.

Rejected candidates, with the reason each was rejected: gastric balloon bills to
an unlisted code, and an unlisted code is not a procedure identity; intestinal
bypass's near neighbour 43847 is *covered*, so a mis-binding is D22 in reverse;
open adjustable gastric banding is disqualified in writing by A53028 — billed with
a Not Otherwise Classified code, so it has no code to be E3's `procedure_code`.

E3 moves from `BLOCKED/CASE_UNSPECIFIED` to `BLOCKED/NOT_IMPLEMENTED`: it now
reaches `LocalPolicyStore.resolve` and gets the `NotImplementedError` citing T-38.
The baseline update recording that is the first real exercise of D27's gate.

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

### `[x] T-37` Reconcile REQ-37 with the source: is c5 a count or a rate?
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

**Closed: a rate.** `monthly` governs the sentence's whole three-item list, and
T-02 had already answered this as open question 1 without anyone connecting it.
`c5_min_documented_events` is gone; c5 carries c4's `documentation_rate` from the
same span, sourced rather than provisional. REQ-37 rewritten, open question 6
closed, and the seven-month run documented in four months is `NOT_MET` in
`tests/test_criteria_tree.py`. See D24. T-16 builds against the rate.

### `[ ] T-38` Procedure sets in the criteria tree, and REQ-2 rewritten to read them
**REQ:** 1, 2 · **Depends:** T-35 · **Blocks:** T-24, and US-1's close ·
**Discovered in:** a design walkthrough, not a task *(D26)*
**Timebox:** two hours
**Exit:** `pytest tests/test_criteria_tree.py` —
- the tree carries three named procedure sets: nationally covered, nationally
  non-covered, and contractor-determined
- every code in every set carries a `(document_id, char_start, char_end)` that
  slices back to a quote naming that code, the way D23 requires of every other
  constant in the file
- the three sets are pairwise disjoint, so no code has two answers
- 43775 is in the contractor-determined set and in neither of the others *(D22)*
- the code T-35 picked for E3 is in the nationally non-covered set
- a code in the non-covered set and a code in none of the three are
  distinguishable **from the tree alone**, with no resolver involved — the test
  asserts the two conditions are different lookups and not one absence
- REQ-2 in `docs/spec.md` reads membership in the non-covered set; REQ-1's
  `NO_POLICY_FOUND` covers the code in no set

`covered_procedures` is named in REQ-2 and exists nowhere else in the repository.
Worse than missing: absence from a single covered list means *denied*,
*delegated to the MAC*, and *no bariatric policy applies* all at once, and REQ-2
answers `NOT_COVERED` to all three. D22 caught one code in the wrong bucket; this
is the bucket structure. *(D26)*

Not folded into T-24 for the reason T-37 gives: a requirement changed by the task
that implements it is a requirement nobody agreed to. Not merged into T-36
because T-36 decides what the resolver returns and this decides what the tree
records, and T-36's exit needs a resolver that does not exist yet.

### `[ ] T-39` A provisional constant must name an *open* question, not any question
**REQ:** 39 · **Discovered in:** T-37 · **Timebox:** one hour
**Exit:** a decision entry choosing how a question's status is recorded, then
`pytest tests/test_criteria_tree.py` —
- a provisional constant naming a **resolved** question fails the gate, asserted
  by mutation the way T-01's eight cases are
- the open/resolved split is read from something the spec states, not inferred
  from `~~` strike-through markup
- the two live provisional constants still pass: criterion (a)'s lookback
  (question 4) and `discrepancy_tolerance` (question 5)

`_open_questions()` in `tests/test_criteria_tree.py` collects section numbers
with `^(\d+)\.\s`, which matches resolved questions as readily as open ones.
Questions 1, 2, 3 and 6 are all closed and all still in the returned set, so
`test_provisional_constants_name_an_open_question_that_exists` currently accepts
a constant citing any of them.

The gate passes today because questions 4 and 5 are genuinely open, which is
exactly why this is worth a task rather than a note: it is a gate that will start
lying at a predictable moment. When T-13 closes question 4 and T-33 closes
question 5, a constant left `provisional` against a question that has since been
answered keeps passing, and the flag that was supposed to stop a defaulted
constant from reaching a predicate stops meaning anything. D23's whole argument
is that a flagged constant names the thing that would unflag it.

Not folded into T-13 or T-33: whichever of them lands first would be the task
that repairs the check it is about to defeat, and it would be graded by that
check. The repair belongs before either of them.

### `[x] T-40` Source the code-to-procedure binding, or state that it stays unsourced
**REQ:** 2 · **Depends:** T-35 · **Discovered in:** T-35 *(D28)* ·
**Answers:** open question 7
**Exit:** a decision entry resolving it, then either
- a third document in `data/policies/source/`, hashed and re-downloadable to the
  same hash the way T-02's two are, with `python scripts/verify_sources.py`
  returning zero and every `code_binding` in the repo carrying a
  `(document_id, char_start, char_end)` that slices back to a quote naming both
  the code and the procedure — `in_corpus` becomes `true`; **or**
- an entry stating in writing that the binding stays outside the corpus, and
  `pytest tests/test_e3_code.py` asserting no artifact anywhere claims a span for
  one.

Neither corpus document binds a non-covered procedure to a code. `ncd_100_1`
carries no procedure codes at all and says why; A53028 names exactly one bariatric
code, 43775, and that is the code D22 disproved. D28 therefore split E3's citation
in two — a spanned `coverage_claim` and an unspanned `code_binding` marked
`in_corpus: false` — which makes the gap visible rather than closing it.

**Not on US-1's critical path.** T-24 and T-25 resolve a code they are handed;
nothing in the chain to E3 `PASS` depends on knowing which document says 43842 is
open vertical banded gastroplasty. It becomes load-bearing the moment a second
procedure code enters the tree, which is T-38 — so if T-38 finds itself writing
several unsourced bindings rather than one, this moves ahead of it.

Reversing D21's two-document corpus is the substance of the first option, which is
why this is a decision and not a download. The MCD's per-response CSP nonce
problem applies to any third document too *(D21)*.

**Closed by the first option, and it ran before T-38 by its own reordering
clause.** T-38's sets need seven bindings, which is "several unsourced ones"
unless a source lands first. The source is `r931cp` — CMS Pub. 100-04
Transmittal 931 (CR 5013, 2006), the claims-processing transmittal implementing
this NCD's reconsideration. Measured before the entry was written: the PDF
re-downloads byte-identical and pypdf 6.18.0 extracts it deterministically, so
the CSP-nonce concern above did not materialize for a static archival file.
Scoped to code bindings only — its coverage content predates the 2012 LSG
delegation (D29). `python scripts/verify_sources.py` verifies all three
documents; E3's `code_binding` is now `in_corpus: true` with a span naming both
code and procedure, `pytest tests/test_e3_code.py` slices it back, and
`python eval/run_eval.py` shows no drift — E3 stays `BLOCKED/NOT_IMPLEMENTED`
until T-38 and T-24. Open question 7 closed.

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
