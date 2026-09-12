# Finish v1 — close all 14 open tasks

## Part 1 — Overarching flow

44 of 58 tasks closed, all 8 gates green. Troy chose to close **all 14** open
tasks: the board's critical path in order, then the four off-path tasks. v1 is
done when acceptance gates A1–A9 (spec §7) all hold.

**Order:** T-41 → T-21 → T-29 → T-30 → T-17 → T-32 → T-72 → T-22 → T-28 →
T-23, then T-71 → T-70 → T-27 → T-42.

**Decisions Troy made up front:**
- Scope: all 14 open tasks.
- T-41: skip the seed search — documented synthetic BMI-35.0 observation under
  its own provenance record.
- T-72 / A5: abstention account per `gap_reason` **plus** a
  `discrepancy_tolerance` sweep (the one real curve the system has).
- T-17: stubbed/recorded verifier in every gate, plus a one-time live
  measurement recorded and replayed (T-63's shape; second pinned model per
  D20's reversal clause). **The only model-call spend in the whole plan.**

**Method, every task:** one task at a time, in order; decision entry in
`docs/decisions.md` **before** the code (Article IX), naming the rejected
alternative and the reversal condition; discovered work becomes a new numbered
task; close on the task's own exit **and** `./venv/bin/python
scripts/check_gates.py` returning zero; mutation-test each new gate (clear
`__pycache__`, `pytest --color=no`, a hang is not a catch); update
`docs/tasks.md` (state, close note) and `CLAUDE.md`'s current-state block; one
commit per close, no session trailer, no third-party names in docs.

**First action after approval:** save a feedback memory — Troy wants
multi-task plans delivered as an overarching flow plus an individual plan per
task.

---

## Part 2 — Individual task plans

### T-41 — E12 patient (synthetic observation)

**Objective:** a 7th committed patient whose structured most-recent BMI is
exactly 35.0 inside the 12-month lookback from 2026-09-01, claiming E12.

**Decision entry (write first):** the population stops being purely generated.
A Synthea-generated base bundle (new recorded seed; jar already at
`data/patients/work/`) gains one appended synthetic BMI observation (LOINC
39156-5, value 35.0, dated in-window), declared in
`data/patients/manifest.json` under a `synthetic_observations` provenance
block (patient id, resource id, value, date, task, D-number). Rejected: the
seed search (Troy chose to skip; record that), a note-BMI stand-in (the board
refuses it — tests reconciliation, not the boundary), a fully hand-built
bundle (loses generated realism). Reversal: a naturally generated 35.0 patient
found later may replace it. Second decision folded in: **the E12 patient is
note-free** — E12 reads structured data only, and no note means no new
extraction recording and zero model calls.

**Steps:**
1. Run Synthea once with a new recorded seed; pick a patient aged 30–60 with a
   plausible near-35 BMI and no active T2DM (avoid colliding with E2's shape).
2. Append the synthetic observation; recompute the bundle sha256; write the
   manifest record + provenance block.
3. `scripts/select_patients.py`: `BUNDLE_COUNT` 6→7; `verify()` additionally
   checks the declared synthetic observation exists in the bundle, is the
   most-recent BMI, and matches the provenance record; existing range checks
   (all in [33,45], min<35, max≥40) still hold. Decide how `--generate`
   (which rmtree's and rebuilds 6) coexists with the 7th — likely a separate
   documented build step recorded in the manifest command block.
4. Manifest gate (`tests/test_manifests.py:34-39`): `EXPECTED_CASES` gains
   E12, `DELIBERATELY_ABSENT` drops it; new `eval/manifests/<pid>.json` with
   `cases: ["E12"]`, no programs, rationale; new structural test via
   `_latest_structured_bmi(store, pid) == 35.0` and in-window.
5. Note-free support: relax `tests/test_notes.py:74` and `:139-143` to permit
   a manifest patient with no note content; `scripts/synthesize_notes.py`
   skips it (per-patient RNG means the six existing note hashes are stable).

**Exit:** `./venv/bin/python scripts/select_patients.py --verify` and
`pytest tests/test_manifests.py`, then check_gates.
**Mutations:** value shifted to 35.01; provenance block deleted; observation
dated out-of-window; stray 8th bundle; a second manifest claiming E12; the
synthetic observation removed from the bundle while the manifest still
declares it.

---

### T-21 — expand the eval set to all of spec §6 (closes US-4 + US-5)

**Objective:** 13 labeled rows (E1–E12, E10b, E10c), all `PASS`; gates A1 and
A3. Zero model calls — everything runs on the recorded extraction.

**Decision entry:** case-row semantics. The scorer today compares only overall
outcome + call budget (`eval/run_eval.py:142-184`), while §6 rows are
criterion-scoped (E10 explicitly, per D72). Chosen shape: each row names its
patient/procedure; `expect` gains optional `criteria` (criterion id →
`{verdict, gap_reason?}`) and `discrepancies` (count) beside `outcome` and
`max_model_calls`. One determination per unique (patient, procedure), cached,
evaluated against every row that shares it — rows sharing a patient cannot
contradict each other or the shared run fails both. Also: a `NO_POLICY_FOUND`
expectation shape (the harness comment at `:224-228` names this task).

**Steps:**
1. Write the 12 new rows in `eval/cases.json` from the manifests: patients
   map E1+E11+E10c, E6+E10, E2+E7, E8+E10b, E4+E9, E5, E12 (new), E3 exists.
2. Extend `_determine` with per-(patient, procedure) caching; extend `score()`
   for `criteria`/`discrepancies`; A3 enforcement — validate every `MET`
   criterion span through `pa_agent.spans.validate` against an index built
   over the policy corpus and the patient documents (`get_document`).
3. Extend `self_check()` (D27's pattern) with synthetic scorings for every new
   branch: wrong criterion verdict, wrong `gap_reason`, missing discrepancy
   entry, `MET` with an invalid span, contradictory rows on one patient.
4. Run; every row moves off `BLOCKED` — acknowledge with `--update-baseline`
   and review the baseline diff in the commit (that diff is the substance).

**Exit:** `./venv/bin/python eval/run_eval.py` returns zero with 13 rows,
then check_gates.
**Mutations:** E4 labeled c3 `MET` (scorer must FAIL); a corrupted span offset
in the recording (A3 check fires); baseline left claiming `BLOCKED`; duplicate
case id; the discrepancy expectation dropped from scoring.

---

### T-29 — fault injection and no-silent-failure audit

**Objective:** every failure point resolves to `ERROR` with a classified code,
no `Determination` emitted, non-zero CLI exit; no handler under `pa_agent/`
can swallow an exception silently. Gates A9 (with T-30).

**Decision entry:** the `ExtractionFailure` → `ErrorCode` mapping
(`runners.py:50-64` says "Mapped onto ErrorCode by T-29"): CALL_FAILED →
MODEL_CALL_FAILED (retryable); NO_PAYLOAD/UNPARSEABLE/SCHEMA_INVALID →
SCHEMA_INVALID; out-of-range span → SPAN_VALIDATION_FAILED; predicate raise →
PREDICATE_EXCEPTION. Also where the mapping lives (workflow's retry-exhaustion
path) and which criteria carry the `ERROR` (the extraction-consuming ones),
plus the CLI contract: structured error report, no `Determination` JSON,
non-zero exit (extending `cli.py:177-203`, whose comment assigns this to
T-29).

**Steps:**
1. Implement the mapping at the workflow boundary; retry loop already exists
   (`workflow.py:369-375`, `DEFAULT_MAX_ATTEMPTS = 3`).
2. `tests/test_fault_injection.py`, four tests: (a) a runner that raises —
   retried to budget, attempts counted, then `ERROR/MODEL_CALL_FAILED`; (b)
   unparseable payload — `ERROR/SCHEMA_INVALID` on first occurrence, one call;
   (c) a recorded payload whose span points past the document end —
   `ERROR/SPAN_VALIDATION_FAILED`; (d) a monkeypatched predicate that raises —
   `ERROR/PREDICATE_EXCEPTION`. Each asserts no `Determination` (T-26's
   validator makes that bite) and the CLI exit.
3. AST audit test (parsed, not grepped, per D72): walk every module under
   `pa_agent/`; no `except` is bare, catches `Exception`/`BaseException`
   without re-raising, or maps to nothing named. Rewrite the known offenders:
   `pa_agent/agent/extraction_agent.py:207-273, 493` and
   `retrieval_agent.py:446` (silent "never kill a run" sites) to narrow types
   or named codes.

**Exit:** `pytest tests/test_fault_injection.py`, then check_gates.
**Mutations:** mapping deleted; retry budget off by one; a `Determination`
emitted over `ERROR`; a bare `except` reintroduced (audit fires); the CLI
printing a determination on the error path.

---

### T-30 — ERROR accounting in the eval harness

**Objective:** an `ERROR` is neither a pass nor an abstention in any reported
rate (REQ-28); closes US-9 with T-29.

**Steps:** `eval/run_eval.py` gains an ERROR reason class that never folds
into abstention (`:24-27` anticipates it); abstention-rate reporting excludes
it; `tests/test_metrics_error_accounting.py` seeds a synthetic `ERROR` and
asserts the abstention rate is unchanged; `self_check()` gains the
classification branch.

**Exit:** `pytest tests/test_metrics_error_accounting.py`, then check_gates.
**Mutations:** ERROR folded into abstention (rate moves — test fails); ERROR
counted as FAIL-without-class; the self-check branch deleted.

---

### T-17 — blind verifier (closes US-6, implements Article V)

**Objective:** Article V gets its implementation: a verifier that sees one
claim at a time and no reasoning, whose rejection resolves that criterion to
`INSUFFICIENT_EVIDENCE`/`VERIFIER_REJECTED` while still emitting a
`Determination`.

**Decision entry (the board flags both points; log before code):**
1. **Shape and zero-call story:** a `VerifierRunner` port mirroring
   `ExtractionRunner` — `LiveVerifierRunner` (google-genai),
   `RecordedVerifierRunner` (replays the committed recording), and a raising
   stub for structural zero-call proofs. New `("verify", step_verify)` entry
   in `workflow.STEPS` after `criteria_c` (a visible diff `test_workflow.py`
   asserts). Default CLI and every gate use the recorded runner — the same
   pattern that keeps extraction free. Verification applies to **cited**
   verdicts (`MET`/`NOT_MET` carry spans); abstentions cite nothing and pass
   through — state this in the entry.
2. **Blindness:** the payload is one criterion's requirement text, verdict,
   and the mechanically sliced quote (via `index`/`spans`) — no reasoning
   trace, no other criterion — asserted on the payload builder.
3. **Second pinned model:** `VERIFIER_MODEL` in `pa_agent/model_pin.py`
   (D20's reversal clause anticipated it), tier named per D5.

**Steps:**
1. `pa_agent/verifier.py` (payload builder + rejection application) +
   runner implementations; wire the workflow step; rejection produces the
   abstention (reason `VERIFIER_REJECTED`, `contracts.py:609`), one verifier
   call counted in `metrics`, determination still assembles.
2. `scripts/run_verifier_measurement.py` (added to `check_gates.EXCLUDED`
   with reason "spends model calls"): runs the live verifier once over the six
   patients' criterion results (~42 claims) on AI Studio, records
   `eval/verifier/results.json` (payload digests, accept/reject, model, tier,
   tokens); `--rescore` replays for free.
3. `tests/test_verifier.py`: mismatched span/verdict rejected (synthetic +
   fake-LLM, T-62's no-network pattern); blindness asserted; rejection →
   `INSUFFICIENT_EVIDENCE` + `VERIFIER_REJECTED` + exactly one call +
   `Determination` emitted; recorded model equals the pin; recording replays
   with hashes intact.
4. Run the measurement (the plan's only spend), commit the recording, make the
   recorded runner the default.

**Exit:** `pytest tests/test_verifier.py`, then check_gates (all still
zero-call).
**Mutations:** payload gains a second criterion (blindness); rejection
collapsing to `NOT_MET` (Article IV); the verifier call not counted; pin
drift; recording tampered (hash check).

---

### T-32 — plane separation walk

**Objective:** the global assertion the per-module checks never gave: no
import path from the policy plane to the patient plane or back; gates
Article VI / REQ-33.

**Decision entry:** the whitelist of composition holders (`workflow`,
`determination`, `cli`, `agent/retrieval_agent` — each with its stated
reason), the shared-type ruling (`contracts.py` is shared by design;
`EvidenceSpan` spans both planes' documents; the "only `Criterion` crosses"
assertion targets plane-specific types), and `cli.py`'s exemption from the
D25 storage-location scan (it is the composition root, `cli.py:14-17`).

**Steps:** `tests/test_planes.py` — AST-based import graph over the 26
modules under `pa_agent/`; BFS from policy roots (`resolver`, `stores/policy`,
`agent/policy_tools`) finds no patient module, and from patient roots
(`stores/patient`, `agent/patient_tools`) no policy module, whitelist
excepted; the D25 scan (no module outside `stores/` opens a file path, holds a
connection, or names a storage location) scoped to `pa_agent/` with the
`cli.py` exemption; global no-class-satisfies-both-Protocols. Upgrade
`tests/test_resolver.py:213-219`'s substring form (it names T-32) to the
parsed form.

**Exit:** `pytest tests/test_planes.py`, then check_gates.
**Mutations:** a policy→patient import added to a scratch module under
`pa_agent/`; a whitelist entry removed (its holder must then fail); the scan
widened to `tests/` (must not be needed); the substring check restored.

---

### T-72 — what A5's curve is over (decided: account + sweep)

**Objective:** reconcile acceptance gate A5 with a system that has no
confidence threshold. Docs-only task; blocks T-22.

**Decision entry:** A5's "coverage/accuracy curve" is replaced by (1) an
abstention account per `gap_reason` — abstentions here have named causes, not
a dial — and (2) an abstention-vs-`discrepancy_tolerance` sweep, re-running
reconciliation over the recorded extractions at a small grid of tolerances
(zero model calls; the one real constant that produces a genuine curve).
Rejected: a curve over a fabricated confidence score (theater); sweep-only
(loses the causal account); account-only (A5 loses its curve entirely).
Reversal: a future scored component (e.g. a live verifier confidence) would
restore a real threshold to sweep.

**Steps:** write the entry; rewrite spec §7's A5 and US-7's third bullet in
`docs/stories.md`; update T-22's exit in `docs/tasks.md` to name the
mechanism.

**Exit:** the entry exists, both docs read the choice, T-22's exit names the
mechanism; check_gates still green.

---

### T-22 — metrics report

**Objective:** `eval/report.md` with per-criterion precision, span validity
rate, abstention rate, T-72's account + sweep, cost and latency. Gates A2
(partly), A3, A5, A6.

**Decision entry:** the report is generated, never hand-edited —
`eval/build_report.py` derives every number from committed recordings
(`eval/run_eval.py` results in-process, `eval/extraction/results.json`,
`eval/agentic/results.json`, T-20's instrumentation) and a `--verify` mode
recomputes and diffs against the committed `report.md`, which lets it join
`GATES` under T-69's membership rule (exit names it, zero cost, no network).
The report carries the D19/D42 authorship caveat and the Noridian-not-CMS
framing in its own text.

**Steps:** build the generator; sections: outcomes vs labels, per-criterion
precision, span validity, abstention account per `gap_reason`, the tolerance
sweep (grid around the pinned 1.0 — e.g. 0.0/0.25/0.5/1.0/2.0 — re-running
`reconcile` over recorded `current_bmi` vs structured), cost/latency per
determination. Add to `GATES`.

**Exit:** `./venv/bin/python eval/build_report.py --verify` returns zero and
`eval/report.md` exists; check_gates (now one gate longer).
**Mutations:** a hand-edited number in `report.md` (`--verify` catches);
the sweep section dropped; the caveat paragraph deleted; a figure computed
from the wrong recording.

---

### T-28 — base rate and always-MET baseline

**Objective:** A2 is only satisfiable with its denominators: the `MET` base
rate and the precision of a trivial always-`MET` baseline beside measured
precision.

**Steps:** extend `eval/build_report.py` and `report.md`; state A2's asymmetry
argument (false `MET` = wrong denial; false `NOT_MET` = chart review) in the
section text; `--verify` covers the new figures.

**Exit:** the report carries both, `--verify` returns zero; check_gates.
**Mutations:** baseline computed over the wrong denominator; base rate read
from labels the system under test can see.

---

### T-23 — README and the REQ-coverage gate

**Objective:** last on the path; gates A7 and A8.

**Decision entry:** what "maps to a passing check" means operationally — the
script holds an explicit REQ → check mapping (test file / gate command) and
verifies completeness: every REQ in spec §5 is mapped **or** sits in §5's
*Unclaimed in v1* table (currently REQ-44, REQ-47); a REQ in neither fails; a
table row without a `docs/decisions.md` mention fails; a mapped check that
doesn't exist fails. (Execution is check_gates' job; this gate audits the
mapping.)

**Steps:**
1. `scripts/check_req_coverage.py` parsing spec §5 and the unclaimed table;
   add to `GATES` — the reference at `tests/test_check_gates.py:207-217`
   flips from "does not exist yet", and the classification test would fail a
   script in neither list.
2. `README.md`: what it is and why, quickstart (CLI + gates, venv path),
   architecture (two short circuits, fixed graph, two ports, trust boundary),
   the measured results **with their caveats** (D19/D42 authorship; D64/D71
   figures; Noridian-as-the-jurisdiction framing), and **failure modes /
   where the system degrades** — A8's section is the deliverable: extraction
   paraphrase risk (T-63's anchoring refusal), single-jurisdiction scope,
   corpus size, the unclaimed REQs, eval-set authorship. No third-party
   names.

**Exit:** `./venv/bin/python scripts/check_req_coverage.py` returns zero;
check_gates (now including it); US-7 closes — A1–A8 all hold.
**Mutations:** a REQ deleted from the mapping; an unclaimed row added without
a decisions entry; a mapped check pointed at a missing file.

---

### T-71 — a lost assertion must show in the aggregate

**Objective:** the ADK measurement aggregate reports assertion coverage, so a
note with `assertion_required: true` and zero assertions is visible without
opening per-note records (the E8 paraphrase loss D71 found).

**Steps:** add `assertion_coverage` (notes with required assertions that
produced ≥1 / notes requiring them) to the aggregate computation in the
rescore path — recomputed from committed recordings, no calls; test with a
synthetic recording where required-but-zero drives it below 1.0; assert it is
distinguishable from the aggregate alone.

**Exit:** `pytest tests/test_adk_measurement.py`; check_gates.
**Mutations:** coverage over the wrong denominator; the required flag ignored;
the aggregate key dropped.

---

### T-70 — the sentinel assertion checks a column, not the output

**Objective:** `test_compare_recomputes_over_the_intersection_and_never_pools`
asserts `"99" not in out` over the whole rendering — one collision from a
false failure (the corrected tool-fetch total is 22,969).

**Steps:** expose or parse the comparison's structured rows and assert the
sentinel is absent from the specific parsed column; re-run the pooling
mutation to confirm it still fails.

**Exit:** `pytest tests/test_adk_measurement.py`; check_gates.

---

### T-27 — planner recall against the oracle's evidence bundle

**Objective:** per-criterion planner recall in `eval/report.md` — for each
criterion, the fraction of cases where the agentic bundle
(`eval/agentic/results.json`) contains the span the fixed planner read —
beside the differential's outcome agreement. D4's reversal condition becomes
falsifiable against this number.

**Steps:** compute from the two recorded bundles (re-run the fixed planner
in-process for the denominator — zero calls); extend `eval/build_report.py` +
`--verify`; a synthetic skipped-document case resolves below 1.000 (test).

**Exit:** `report.md` carries the section, `--verify` returns zero;
check_gates.

---

### T-42 — the longest run is not always the qualifying run

**Objective:** resolve the false `NOT_MET` where a six-month run three years
ago beats a four-month run last month.

**Decision entry:** choose between joint selection (prefer a run satisfying
c3's length **and** c2's recency, falling back to longest) and
longest-plus-c2-considers-every-run. Leaning **joint selection** — it kills
the false `NOT_MET` and keeps c4/c5's scope well-defined (they scope to the
selected run either way); the entry names the alternative and what each does
to c4/c5 scoping, plus the reversal condition.

**Steps:** implement in `criteria.py`'s run selection; add the two-run chart
case to `tests/test_criteria_c.py`; rewrite REQ-14/REQ-32 in `docs/spec.md`;
deliberately change the pinned
`test_the_longest_run_wins_even_when_an_older_one_is_stale` (that pin exists
so this change is a visible act).

**Exit:** `pytest tests/test_criteria_c.py`; check_gates. Board reaches
58/58.
**Mutations:** selection reverted to longest (the new case fails); c4/c5
scoping to a different run than c2 judged.

---

## Verification (end state)

Per close: task exit zero, `./venv/bin/python scripts/check_gates.py` zero,
mutations caught. End state: `eval/run_eval.py` reports 13/13 `PASS`;
`eval/build_report.py --verify` and `check_req_coverage.py` sit in `GATES`
and pass; `./venv/bin/python -m pa_agent.cli --patient <uuid> --procedure
43775` still prints a determination for zero model calls; README exists;
`docs/tasks.md` shows 58/58 closed and US-4/5/6/7/9 delivered; A1–A9 all
hold.

## Risks

- T-17's measurement needs the AI Studio credential (the only spend).
- T-41 touches the population manifest's verify invariants; the provenance
  record keeps `--verify` honest.
- T-21's per-criterion scorer is the largest single change; D27's self-check
  pattern is the guard.
- Discovered work gets a new task number — expect the board to grow before it
  finishes.

