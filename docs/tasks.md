# Tasks

Grouped under the story each one serves. Every task carries a runnable exit
condition. `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked
*(Constitution, Article VIII)*

Task IDs are unchanged from the day-sliced version. Regrouping renumbers nothing,
because renumbering breaks the link to anything that already references a task.

**Build order is now vertical.** US-1 ships a running end-to-end system on day
one. Each story after it makes that system do more. Nothing waits on a layer to
finish.

---

## Enablers — before any story

### `[ ] T-00` Spike 001: model reliability on consecutive-month reasoning
**Type:** spike · **Informs:** US-4 · **Blocks:** T-15
**Exit:** `grep -q "Spike 001" docs/decisions.md && grep -q "Decision:" docs/decisions.md`

### `[ ] T-01` Hand-compile NCD 100.1 criteria tree
**Exit:** `python -c "import json;json.load(open('data/policies/ncd-100-1.v1.json'))"`

### `[ ] T-02` Download and hash policy source documents
**Serves:** US-2, since spans anchor here
**Exit:** `python scripts/verify_sources.py`

### `[ ] T-03` Repo skeleton and environment
**Exit:** `python scripts/check_skeleton.py` — target layout present,
`google-adk` imports at exactly 2.8.0, `pa_agent.agent` imports, and an `adk web`
subprocess answers HTTP 200 on `localhost:8000` within the timeout before the
script terminates it
**Timebox:** two hours. If it is not serving by then, write a decisions entry
naming what broke instead of grinding.
Exit condition rewritten per D6. The original — "`adk web` serves a hello-world
agent on localhost:8000" — was a blocking server judged by eye, which Article VIII
does not accept.

---

## `US-1` Screen out non-covered procedures — day 1

Target: this story closes before you sleep on day one.

### `[ ] T-09` Data contracts as Pydantic models
**REQ:** 5, 22 · **Depends:** T-00
**Exit:** `pytest tests/test_schemas.py`
`Criterion`, `EvidenceSpan`, `Verdict`, `Determination`, `WmEvent`, `CallMetrics`.

### `[ ] T-10` Eval harness with US-1 acceptance cases, failing
**Depends:** T-09
**Exit:** `python eval/run_eval.py` runs and reports E3 failing
Pulled forward from day three by the restructure. US-1 cannot close without
acceptance tests, so the harness arrives with the first story rather than after
the fourth. A failing harness is the correct state.

### `[ ] T-24` Policy resolver and short-circuit sc1
**REQ:** 1, 2, 4 · **Depends:** T-09
**Exit:** `pytest tests/test_resolver.py` — E3 returns `NOT_COVERED`, unknown code returns `NO_POLICY_FOUND`, model-call counter reads zero

### `[ ] T-25` Determination assembly, minimal
**REQ:** 4, 21 · **Depends:** T-24
**Exit:** `python -m pa_agent.cli --patient X --procedure 43775` prints a determination carrying `policy_version_id`

**US-1 closes when:** `python eval/run_eval.py` reports E3 passing.

---

## `US-2` Structured criteria with citations — day 2

### `[ ] T-04` Generate and select the Synthea population
**Exit:** `python scripts/select_patients.py --verify` — six bundles, seed recorded, BMI spanning 33 to 45

### `[ ] T-05` Rebuild the comorbidity value set from real codes
**REQ:** 12 · **Depends:** T-04 · **Blocks:** T-13
**Exit:** `pytest tests/test_valueset.py` — every code appears in the population, `status` is `VERIFIED`
Currently written from memory and marked UNVERIFIED. A wrong code fails criterion
b silently for every patient, with no error anywhere.

### `[ ] T-08` Document index with character offsets
**REQ:** 6, 7 · **Depends:** T-02 · **Blocks:** T-11
**Exit:** `pytest tests/test_index.py` — content hash per document, round-trip slice returns the original for 1000 random spans

### `[ ] T-11` Span validator
**REQ:** 6 · **Depends:** T-08, T-09
**Exit:** `pytest tests/test_spans.py` — fabricated, off-by-one, and reversed spans all rejected; no model imported in the module

### `[ ] T-12` FHIR fact extractor
**REQ:** 11, 12 · **Depends:** T-04, T-09
**Exit:** `pytest tests/test_fhir.py` — BMI observations and Conditions with dates, from all six bundles

### `[ ] T-13` Deterministic criteria (a) and (b)
**REQ:** 11, 12 · **Depends:** T-05, T-12
**Exit:** `pytest tests/test_criteria_ab.py` — includes the BMI 35.0 boundary; asserts zero model calls

**US-2 closes when:** E12 passes and every span produced in the run survives T-11.

---

## `US-3` Categorical exclusion — day 2, late

### `[ ] T-14` Short-circuit sc2
**REQ:** 3 · **Depends:** T-13
**Exit:** `pytest tests/test_short_circuits.py` — E2 returns `NOT_COVERED` with a model-call counter of zero

**US-3 closes when:** E2 passes.

Half a day at most, because US-2 already built everything it needs.

---

## `US-4` The narrative criterion — days 3 and 4

The big one. Everything before it was lookup.

### `[ ] T-06` Author fact manifests
**REQ:** 8, and cases E1–E12 · **Depends:** T-04
**Exit:** `pytest tests/test_manifests.py` — one per patient, every edge case covered
Ground truth. Written before the notes exist.

### `[ ] T-07` Manifest-driven note synthesizer
**REQ:** 8, 9 · **Depends:** T-06
**Exit:** `pytest tests/test_notes.py` — every note honors its manifest, by assertion rather than by reading
E9 requires missed-visit dates sitting in the gap month.

### `[ ] T-15` `wm_events` extraction agent
**REQ:** 8, 9, 10 · **Depends:** T-00, T-07, T-11
**Exit:** `pytest tests/test_extraction.py` — correct events on `spike/note_001.txt` and three synthesized notes; every span passes T-11
The only place in the system where a model exercises judgment.

### `[ ] T-16` Deterministic predicates c1 through c5
**REQ:** 13, 14, 15, 16 · **Depends:** T-15
**Exit:** `pytest tests/test_criteria_c.py` — E4, E5, E6, E9 correct; c4 returns `INSUFFICIENT_EVIDENCE` when c3 fails; identical verdicts across three runs
Test the empty list, a single event, and events out of chronological order.

### `[ ] T-18` Workflow graph
**REQ:** 1, 4, 19 · **Depends:** T-13, T-16
**Exit:** `pytest tests/test_workflow.py` — fan-out, fan-in, retry, and fail-closed all exercised; grep the module for branching on model output and find none

### `[ ] T-20` Cost and latency instrumentation
**REQ:** 22 · **Depends:** T-15
**Exit:** `python eval/run_eval.py` prints per-run token counts and wall time
Lands with the first model call, not on day five. *(Article X)*

**US-4 closes when:** E4, E5, E6, E7, E9, E10, E11 all pass.

---

## `US-5` The gap list — day 4

### `[ ] T-19` Aggregator and gap list
**REQ:** 19, 20, 21 · **Depends:** T-18
**Exit:** `pytest tests/test_determination.py` — boolean tree evaluated in Python, gap list populated, documentation gaps distinguishable from substantive failures

**US-5 closes when:** E1 and E8 pass.

---

## `US-6` Trustworthy citations — day 4

### `[ ] T-17` Blind verifier
**REQ:** 17, 18 · **Depends:** T-15
**Exit:** `pytest tests/test_verifier.py` — mismatched span and verdict rejected; assert the verifier's input contains no reasoning trace and no other criterion

**US-6 closes when:** retry to `INSUFFICIENT_EVIDENCE` is exercised end to end.

---

## `US-7` Where the system stops being reliable — day 5

### `[ ] T-21` Expand the eval set to all of spec §6
**Depends:** T-06, T-10
**Exit:** `python eval/run_eval.py` runs E1 through E12, all labeled

### `[ ] T-22` Metrics report
**Depends:** T-20, T-21
**Exit:** `eval/report.md` with per-criterion precision, span validity rate, abstention rate, coverage/accuracy curve, cost and latency
The curve is the deliverable. Name the threshold where abstention reaches one.

### `[ ] T-23` README
**Depends:** T-22
**Exit:** `python scripts/check_req_coverage.py` — every REQ maps to a passing check

**US-7 closes when:** A1, A2, A3, A5, A6, A7, A8 all hold.

---

## `US-8` Policy governance

Satisfied by git plus REQ-4. Becomes real work when the criteria compiler lands
in week 2.

---

## Discovered — design review

Work that came out of the design review rather than out of a story. Kept in its
own section because none of it changes an existing story's closing condition, and
folding it into US-1 or US-7 would silently move those goalposts. *(Working rule 6)*

### `[ ] T-26` `ERROR` state and no-silent-failure audit
**REQ:** 23, 24 · **Depends:** T-09 · **Gates:** A9
**Exit:** `pytest tests/test_error_state.py` — a forced model timeout, a
schema-invalid response, and a raised exception in a predicate each produce
`ERROR` and abort the determination; grep the package for bare `except:` and
`except Exception:` without re-raise and find none.

### `[ ] T-27` Retrieval recall instrumentation
**REQ:** 25 · **Depends:** T-21 · **Serves:** US-7
**Exit:** `python eval/run_eval.py` prints per-criterion recall@k against the
manifest ground truth.
Makes D4's stated reversal condition executable instead of rhetorical.

### `[ ] T-28` Baseline and base rate in the metrics report
**Depends:** T-22 · **Serves:** US-7
**Exit:** `eval/report.md` contains the `MET` base rate and an always-`MET`
baseline score next to measured precision.

---

## Working rules

1. One task in `in_progress` at a time.
2. Close stories, not layers. A story with four of five tasks done has delivered
   nothing.
3. A task that cannot close without violating the constitution is a wrong task.
   Rewrite it; do not amend the constitution.
4. Discovered work becomes a new numbered task, not a silent addition to the
   current one.
5. Log the decision before writing the code it justifies. *(Article IX)*
