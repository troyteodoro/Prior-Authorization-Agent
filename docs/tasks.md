# Tasks

Grouped under the story each one serves. Every task carries a runnable exit
condition. `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked
*(Article VIII)*

Build order is vertical. US-1 ships a running end-to-end system on day one; each
story after it makes that system do more. Task IDs are stable — renumbering
breaks every reference to them.

The sections below are organised by **the story a task serves**, which answers
*why this task exists* and does not answer *what to do next*. `Path to v1` below
answers the second question, once.

---

## Path to v1

Sixty-two tasks are on this board — IDs run to T-76 but numbering is not
contiguous, so the highest id is not the count. **46 are closed and 16 are
open.** Eleven of the 16 sit on the critical path to the acceptance gates in
spec §7. This is that path, in order. *(D70, extended by D72; reordered by D74
— ownership is ratified before the path resumes)*

| # | Task | Closes / gates | State |
|---|---|---|---|
| 1 | `T-73` | the ratification ledger and its gate *(D74)* | **closed** |
| 2 | `T-74` | Troy ratifies constitution, spec, stories *(D74)* | **in progress** — scaffold built (D75); Troy's reading |
| 3 | `T-75` | Troy ratifies the load-bearing decisions and the board *(D74)* | after T-74; Troy's reading |
| 4 | `T-21` | **closes US-4 and US-5** · gates **A1**, **A3** | after T-75 *(D74)* |
| 5 | `T-29` → `T-30` | **closes US-9** · gates **A9** | ready |
| 6 | `T-17` | **closes US-6** · implements **Article V** | ready |
| 7 | `T-32` | gates **Article VI** / REQ-33 | ready |
| 8 | `T-72` → `T-22` → `T-28` → `T-23` | **closes US-7** · gates **A2**, **A5**, **A6**, **A7**, **A8** | after T-21; T-72 any time |

Off the path. Real work, nothing waiting on it:

| Task | Why it is not sequenced | When |
|---|---|---|
| `T-76` | ratifies the remaining decision entries; the load-bearing set is T-75's | after T-73, in batches, blocks nothing *(D74)* |
| `T-27` | needs the full eval set and the report to write into | after T-21 and T-22 |
| `T-42` | a decision task; no §6 case distinguishes the two readings | any time, blocks nothing |
| `T-71` | an aggregate that hides a refused citation; found by T-63 | any time, blocks nothing |
| `T-70` | a brittle substring assertion in a gate; found by T-63 | any time, blocks nothing |

**Why the ratification tasks go first.** D42 put on record that the eval ground
truth is authored by the agent building the system it grades, and the docs the
whole repo obeys were agent-drafted under direction. D74 converts that
direction into recorded ownership — a ledger, a gate, and statuses only Troy
may write — and the conversion is cheapest *before* T-21 authors the labels
the acceptance gates will be scored against, not after.

**Why T-21 is second and not later.** US-4 and US-5 are *built and ungraded* —
every predicate, the reconciliation, the aggregator and the gap list work and are
pinned by unit tests, but both stories close on eval-harness rows and
`eval/cases.json` holds one case. T-21 converts two stories' worth of finished
work into two closed stories for one task's cost. Nothing else on the board has
that ratio, and working rule 7 is the reason it goes near the front.

**Why T-17 is on the path at all.** Article V — the verifier is blind — has zero
implementation today. It is the largest constitutional hole in the repo and it is
one task.

**Why the report chain is last.** T-22, T-28 and T-23 all read the eval set
beneath them. Built before T-21 they would be rewritten after it.

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

### `[x] T-24` Policy resolver and short-circuit sc1
**REQ:** 1, 2, 4 · **Depends:** T-09
**Exit:** `pytest tests/test_resolver.py` — E3 returns `NOT_COVERED`, unknown code
returns `NO_POLICY_FOUND`, model-call counter reads zero

**Closed by D31, two layers.** `LocalPolicyStore.resolve` reports facts — the
set membership and spanned coverage claim on an extended `PolicyRef`, or `None`
— and `pa_agent/resolver.py` owns the judgment: non-covered membership maps to
`NotCovered` carrying the `policy_version_id` and the claim that slices back
through the store; absence maps to `NoPolicyFound`; the three results are three
types so D26's two lookups cannot merge by ignoring a field. The
contractor-determined branch **raises citing T-36** — the store reports 43775's
membership, the resolver refuses to map it until that decision is written. Zero
model calls proven by a fresh-interpreter import probe. The contracts gained the
typed procedure sets with D30's invariants as validators (unsourced identity and
two-set codes fail at construction). E3 now blocks on T-25's seam with no
baseline drift. Seven mutations, each caught — including the contractor branch
quietly mapping like covered, and a schema gate D31 records as having passed by
accidental substring match.

### `[x] T-25` Determination assembly, minimal
**REQ:** 4, 21 · **Depends:** T-24
**Exit:** `python -m pa_agent.cli --patient X --procedure 43842` prints a
`NOT_COVERED` determination carrying `policy_version_id`, model-call counter zero

**Closed by D32.** `pa_agent/determination.py` assembles sc1:
`NOT_COVERED` arrives with the resolver's spanned claim copied onto the
artifact (`Determination.coverage_claim`, valid only with that outcome),
`patient_id` may be `None` when no patient was consulted, and
`NO_POLICY_FOUND` is `NoPolicyResult` — not a `Determination`, because REQ-4's
version id cannot exist for it. Covered codes raise citing T-19; the T-36
raise propagated until T-36 closed it (D33). The CLI prints one JSON document and exits 0 for answers, 2
for unbuilt paths. Filling the harness seam flipped E3 to `PASS`; the gate
failed on drift and the baseline diff rides in this commit — which, per this
story's close condition, is US-1 closing. The scorer gained an eighth
self-check (an ungoverned code under an outcome expectation is `FAIL`, never a
coerced denial). Nine mutations, each caught, including the harness coercing
`NO_POLICY_FOUND` into a denial — caught by that eighth check.
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

**US-1 is closed.** That commit is T-25's: E3 reports `PASS`,
`python eval/run_eval.py` returns zero against the updated baseline, and the
chain ran exactly as written — T-35 the code, T-38 the sets, T-24 the resolver,
T-25 the assembly.

---

## `US-2` Structured criteria with citations — day 2

### `[x] T-04` Generate and select the Synthea population
**Exit:** `python scripts/select_patients.py --verify` — six bundles, seed
recorded, BMI spanning 33 to 45

**Closed by D35.** Synthea **v4.0.0** — the tagged release jar, pinned by
version and measured sha256; `master-branch-latest` rejected as a moving
nightly — seed 1001, 200 patients aged 30–60 in **Washington**, inside
Noridian Jurisdiction F (D21). Six bundles committed under
`data/patients/bundles/` with `data/patients/manifest.json` recording seed,
jar hash, full command line and per-bundle content hashes. Most-recent BMIs
(LOINC 39156-5): 34.26, 34.6, 35.89, 37.65, 39.23, 42.5 — one below 35 **with
active T2DM**, E2's shape, recorded as a fact rather than gated (labeling E2
is T-06's work). `--verify` re-reads the disk only: no network, no Java, no
generation, because Synthea's cross-machine determinism is unmeasured and the
committed hashes are the ground truth. The patient-store raise moved from
T-04 to T-12 with no "T-04" left in the message, so a stale test match fails
loudly rather than passing by substring (D31). Mutation-tested four ways,
each caught by the check built for it: a tampered bundle byte, an edited
manifest BMI, the seed removed, a stray seventh bundle on disk.

### `[x] T-05` Rebuild the comorbidity value set from real codes
**REQ:** 12 · **Depends:** T-04 · **Blocks:** T-13
**Exit:** `pytest tests/test_valueset.py` — every code appears in the population,
`status` is `VERIFIED`
Currently written from memory. A wrong code fails criterion b silently for every
patient, with no error anywhere.

**Closed by D36.** `data/policies/value_sets/obesity_comorbidities.json`,
keyed on **SNOMED** because that is the only system the population's
Conditions carry, anchored to **ICD-10-CM in-corpus**: each entry's
`icd10_anchor` spans A53028's Group 1 naming code and condition together, and
the gate asserts the span falls inside Group 1's list, not merely inside the
document — Group 2's BMI codes slice back just as cleanly and mean something
else. The SNOMED-to-ICD-10 mapping hop is marked `in_corpus: false` (D28's
posture): the one authoritative source sits behind UMLS licensing and cannot
be re-downloaded credential-free. Two entries, both face-unambiguous — T2DM
(44054006 → E11.9) and essential hypertension (59621000 → I10) — with the
diabetic-complication mappings deliberately excluded for leaf-level ambiguity
and hypertriglyceridemia/metabolic syndrome/CKD/emphysema excluded because
their codes are absent from Group 1: the source decides membership.
Mutation-tested seven ways, each caught by the test built for it; the
sharpest is the Z68.35 anchor, which slices back perfectly and only the
containment gate refuses. The file lives under `value_sets/` because
`LocalPolicyStore._load_trees` globs `data/policies/*.json` as trees —
measured when the first draft errored eight store tests.

### `[x] T-08` Document index with character offsets
**REQ:** 6, 7 · **Depends:** T-02 · **Blocks:** T-11
**Exit:** `pytest tests/test_index.py` — content hash per document, round-trip
slice returns the original for 1000 random spans

**Closed by D37.** `pa_agent/index.py`, `DocumentIndex`: plane-agnostic,
in-memory, fed `Document` objects by whoever holds a store — the module opens
no file and imports nothing but the contracts, asserted on its AST (REQ-41).
`add` makes REQ-7 an exception: rebinding an id to different content raises
naming both hashes; identical re-adds are idempotent. `slice` takes an
`EvidenceSpan` and returns the unmodified text or raises — never `None`,
never normalized (D18: normalization is the anchorer's concern). Judging a
slice is T-11's job; this is the primitive it rejects against. The 1000-span
round trip runs seeded over the real three-document corpus served through
`LocalPolicyStore`. Mutation-tested five ways, each caught by the test built
for it: a conflict that silently rebinds, an out-of-range slice returning
`""`, a slice that normalizes whitespace, a store import, an off-by-one.

### `[x] T-11` Span validator
**REQ:** 6 · **Depends:** T-08, T-09
**Exit:** `pytest tests/test_spans.py` — fabricated, off-by-one, and reversed
spans all rejected; no model imported in the module

**Closed by D38.** `pa_agent/spans.py`: `validate(span, index)` returns the
verified **raw** slice or raises `SpanValidationError` with a closed
`SpanRejection` reason — `UNKNOWN_DOCUMENT`, `OUT_OF_RANGE`,
`QUOTE_MISMATCH` — never `None`, never a bool, and never a criterion outcome
(what a rejection *means* is REQ-18's and REQ-23's question, not this
module's). The quote check is D18's predicate verbatim: whitespace-collapsed
exact equality, no similarity knob. Reversed spans are refused at
construction by `EvidenceSpan` and the test asserts that refusal. Genuine
spans in the test are T-02's recorded answers, perturbed — not synthetic
strawmen. The import list is exactly `__future__`, `enum`, and the two
`pa_agent` modules, asserted on the AST. Mutation-tested five ways, each
caught: the quote check deleted, equality replaced by a difflib 0.8 knob
(caught twice — the changed-word test and the import scan), success
returning the quote instead of the slice, the range check deleted, a stray
import.

### `[x] T-12` FHIR fact extractor
**REQ:** 11, 12 · **Depends:** T-04, T-09
**Exit:** `pytest tests/test_fhir.py` — BMI observations and Conditions with
dates, from all six bundles

**Closed by D39.** The extractor *is* `LocalPatientStore`'s read side —
building it anywhere else would be a second module opening patient files
(REQ-41). Patients resolve through T-04's manifest and every bundle is
hash-verified before parsing, the `get_document`/`sources.json` pattern on
the patient plane. The adapter reports facts and filters nothing: all
quantitative observations (not BMI-only — REQ-34's reconciliation needs the
rest), all coded conditions with `clinical_status` carried on a widened
`Condition` contract, because an adapter serving only active conditions
would decide criterion (b)'s question for it, and a contract that cannot say
"resolved" lets a resolved diagnosis become a false `MET`. The gate checks
the adapter against T-04's independently-recorded most-recent BMIs — two
implementations agreeing, not one agreeing with itself. `get_notes` keeps
raising, re-cited to **T-07**: Synthea's auto-notes carry no ground truth,
and serving them would hand T-15 a corpus whose eval cases grade against
labels that do not exist. Mutation-tested six ways, each caught: an
active-only filter, a BMI-only filter, the hash check deleted, an unknown
patient served an empty chart, dates collapsed to January 1, a policy
import appearing.

### `[x] T-13` Deterministic criteria (a) and (b)
**REQ:** 11, 12 · **Depends:** T-05, T-12, T-39
**Exit:** `pytest tests/test_criteria_ab.py` — includes the BMI 35.0 boundary;
asserts zero model calls
Closes open question 4, so T-39 comes first: until it does, unflagging the
lookback is not checked by anything.

**Closed by D40.** Open question 4 is answered — **12 months, decided by
Troy**, by analogy to the program-participation window, carried as a `note`
and never a span, unflagged under T-39's rebuilt gate on its first real
exercise. `pa_agent/criteria.py` evaluates (a) and (b) over contracts alone
(imports asserted on the AST): (a) reads threshold and window through
`require()`, boundary inclusive (E12), stale BMI `NOT_MET` citing the stale
observation (REQ-16), empty chart `INSUFFICIENT_EVIDENCE`; (b) intersects
**active** conditions with the value set and is `MET` or abstention, never
`NOT_MET` — a chart cannot prove a comorbidity absent. Structured claims cite
the bundle itself: `PatientStore` gained `get_document`, the adapter locates
each resource's exact extent in the raw bundle text, and the real-population
test validates every produced span through T-11 against the bundle document.
Mutation-tested six ways, each caught: a hardcoded window, an exclusive
boundary, stale collapsed into abstention (three tests object), resolved
conditions counted, highest-BMI-decides, and the tree re-flagging the
lookback — which fails both `require()` and T-39's subsection gate at once.

**US-2 closes when:** E12 passes and every span produced in the run survives T-11.

**US-2 is closed** — the second delivered story. E12's boundary case passes in
`tests/test_criteria_ab.py`, and `test_the_real_population_adjudicates_as_the_manifest_says`
validates every span the run produces through T-11 against the bundle
documents. E12's *harness* row arrives with T-21 alongside the rest of spec
§6, on the determination path T-18/T-19 build.

---

## `US-3` Categorical exclusion — day 2, late

### `[x] T-14` Short-circuit sc2
**REQ:** 3 · **Depends:** T-13
**Exit:** `pytest tests/test_short_circuits.py` — E2 returns `NOT_COVERED` with a
model-call counter of zero

**Closed by D41.** The source finding first: the NCD *body* never states the
exclusion — it lives in the transmittal history's 04/2009 entry, self-scoping
(procedures, population, denial in one sentence). The tree gains
`categorical_exclusions` with that claim spanned, a `bmi_upper_bound` of 35.0
(`lt`) that is **the one numeric constant legitimately sourced to the NCD**
(national, quantified by CMS itself — gated separately so D21's
every-number-is-Noridian rule keeps its scope), a T2DM binding in D28's
unsourced posture, and `procedure_scope: nationally_covered` — the sentence
predates the LSG delegation, so contractor requests skip sc2.
`evaluate_sc2` fires only on an **in-window** BMI (borrowing criterion (a)'s
lookback): a categorical denial issued on evidence the approval path would
refuse is confidence asymmetry in the wrong direction. The `Determination`
cites both sides — `coverage_claim` reuses D32's field (settling the shape it
deferred here) and the new `exclusion_evidence` spans the BMI observation and
the T2DM condition in the bundle document, both validated through T-11 in the
test. The E2 case runs on the real T2DM patient at an `as_of` inside his
BMI's window; the same chart at today's date correctly falls through to the
criteria path. Spanless facts deny nobody. Mutation-tested six ways, each
caught: firing on stale evidence, an inclusive boundary, the contractor scope
dropped, the denial shipped without patient evidence, the claim re-pointed at
§B's coverage sentence (slices back, means the opposite — caught by the
artifact test and the tree gate both), resolved T2DM counted.

**US-3 closes when:** E2 passes. Half a day at most; US-2 built everything it needs.

**US-3 is closed** — the third delivered story. E2's shape passes end to end
on a real chart with zero model calls, distinguishable from a criteria
failure by structure (claim + evidence, no criterion verdicts). Its harness
row arrives with T-21.

---

## `US-4` The narrative criterion — days 3 and 4

The big one. Everything before it was lookup.

### `[x] T-06` Author fact manifests
**REQ:** 8, and every case in spec §6 · **Depends:** T-04
**Exit:** `pytest tests/test_manifests.py` — one per patient, every edge case
covered
Ground truth. Written before the notes exist.

**Closed by D42.** Six manifests in `eval/manifests/`, one per committed
patient, `as_of` pinned to D35's 2026-09-01 so "recent" and "stale" are
arithmetic rather than wall-clock. They record **facts only** — programs,
dated encounters with per-encounter documentation flags, program assertions,
and spike-001-typed traps — and **no expected verdicts**: labels are
`eval/cases.json`'s (T-21), and a manifest stating its own outcome would be a
second label source that can disagree with the first. They live in `eval/`
because they are the grader's ground truth (D27's line): the system under
test never reads them, and T-07's notes are the only path a fact takes to the
model.

Case assignment follows the structured data rather than fighting it — Felipe
is the **only** possible E1 (the one patient with an in-window BMI ≥ 35 *and*
an active value-set comorbidity), and E9 overlays E4, E10 overlays E6, E10b
overlays E8 as criterion-scoped expectations that do not collide. Eleven of
spec §6's cases are covered; E3 has no patient by design (sc1 is a fact about
the procedure) and **E12 had no possible patient** until T-41 added one (D73).

Every manifest fact that touches the bundles is cross-checked against the
structured record through the port, so the ground truth cannot drift from the
population. Mutation-tested eight ways, each caught by the test built for it:
E4's gap filled, E9's trap moved into a visit month, E10b's note BMI stopping
short of the threshold, E8 gaining an encounter, E5's program moved inside
the recency window, E6 documenting BMI in three months, E11 dropped from the
board, E1's run leaving a month undocumented.

The authorship caveat D19 raised propagates and is recorded in D42: this
ground truth was written by the agent building the system it grades. A
perfect score on it means the approach does not obviously fail, nothing more.

### `[x] T-07` Manifest-driven note synthesizer
**REQ:** 8, 9 · **Depends:** T-06
**Exit:** `pytest tests/test_notes.py` — every note honors its manifest, by
assertion rather than by reading
E9 requires missed-visit dates sitting in the gap month.

**Closed by D43.** `scripts/synthesize_notes.py` renders six chart notes from
T-06's manifests by deterministic seeded templating — **no model writes
them**, because the notes are the input to the model T-15 measures and a
model-written corpus would score model-to-model agreement, high and
meaningless. The corpus mimics EHR export (caps headers, `MM/DD/YYYY` entries,
metric units, matching spike 001 so T-15's one prompt covers both) and
**wraps hard at 78 columns on purpose**: D18 chose whitespace-insensitive
anchoring because quotes cross wrap points, and a corpus of clean single lines
would leave that path untested until production. It does cross them — the
gate itself had to normalize, because `BMI 42.7` genuinely lands split across
a line break.

The assertion that makes the corpus ground truth: **no note contains a date
the manifest does not declare** (the patient's structured DOB aside). An
invented date would be extracted, scored against ground truth that never
mentioned it, and counted as a model failure that was really a corpus defect.
Traps read like the chart events they are and never name their own type, and
every non-encounter entry states its non-encounter status in prose — E9's
substance. Weight is derived from the note's BMI and the patient's structured
height, so a note is internally consistent; E10/E10b disagree with the
structured BMI only in the one fact their case is about.

`LocalPatientStore.get_notes` now serves them as hash-verified `Document`s
and the T-07 raise is retired (D31's rule about stale citations). Mutation-
tested seven ways, each caught: an invented follow-up date, a trap labeling
itself, the wrap turned off, a weight-only month quietly gaining a BMI (D15's
case erased), a missed visit reading like a visit, a documented BMI written
off by one, and a note tampered on disk — which fails at `Document`'s hash
validator before any test sees it.

### `[x] T-15` `wm_events` extraction agent
**REQ:** 8, 9, 10, 35, 38 · **Depends:** T-00, T-07, T-11
**Exit** *(gains a clause from D46 — see below)*:
`pytest tests/test_extraction.py` — correct events on the five
`spike/spike_001/notes/` cases and three synthesized notes; every span passes
T-11; **every per-field span is nearer its own event than any other event's**;
E8's note yields zero `wm_events` and at least one `program_assertions[]` span
The only place in the system where a model exercises judgment. The spike's notes
double as regression cases.

**Closed by D45, D46 and D47.** `pa_agent/extraction.py` carries the spike's
prompt and schema essentially verbatim — D19 measured 1.000/1.000 on that
formulation with no tuning, and rewriting it would restart the prompt's
history — widened only by REQ-38's per-field spans and the BMI as a *value*
(the contract stores `WmEvent.bmi`, and T-33 has nothing to reconcile
without it). So this is a **new measurement on a wider schema, not D19's
re-run**. Anchoring is `pa_agent/anchor.py`, separate from `spans.py` because
a locator must not be able to launder its bugs through the validator.

**Measured over 11 notes, twice:** event precision 1.000, recall 1.000, REQ-9
exclusion 11/11, REQ-38 field agreement 1.000 (126 fields), every span
anchored, and **0 of the model's own offsets usable** — D19's finding
reproduced, so D17's reversal clause stays dead. Event dates and every
documentation flag were identical across both runs; quote *encoding* and
assertion emission on notes that also have events were not (D47).

**Two defects the first measurement found, both fixed here.** Seven of 162
per-field spans cited the **wrong encounter** — a repeated `BMI 37.6`
anchoring to the first month — while slicing back perfectly and passing T-11;
the exit condition as written would have shipped them, so D46 fixed the
anchoring by proximity and added the clause above. And the model returned
double-escaped newlines on one run, costing six real encounters until D47
unescaped them. Both repairs were verified by replaying recorded payloads for
**zero model calls** — D18 built `--rescore` for exactly this and it paid on
its first use.

Mutation-tested seven ways, each caught: the proximity fix undone, the
unescape fix undone, a synthesized note edited after measurement, a spike
note edited after measurement, the recorded model not the pin, a trap
smuggled in as an event, and a fabricated quote. The unescape mutation
initially passed — the live recording did not exercise that path — which is
why both anchoring repairs now carry direct synthetic tests.

### `[x] T-31` `gap_reason` on `INSUFFICIENT_EVIDENCE`
**REQ:** 31 · **Depends:** T-09 · **Blocks:** T-16, T-17, T-19, T-33
**Exit:** `pytest tests/test_gap_reason.py` — a `GapReason` enum with
`NO_EVIDENCE_RETRIEVED`, `UNSUBSTANTIATED_ASSERTION`, `VERIFIER_REJECTED` and
`SOURCE_CONFLICT`; the field required on any `CriterionResult` resolving
`INSUFFICIENT_EVIDENCE` and rejected on any other verdict; E7, E8 and E10b
carrying three different values
Separate from T-26: `gap_reason` describes an honest abstention, `error_code`
describes a fault, and one field for both is the collapse Article IV forbids.

**Closed by D44.** `GapReason` is a closed four-member enum, and a validator
on `CriterionResult` makes it **required on `INSUFFICIENT_EVIDENCE` and
refused on every other verdict** — the mirror of REQ-5's span rule, so a
result is either evidence or an explanation of its absence, never a mix. It
propagates onto `GapEntry`, because US-5's argument is that two gaps with
different reasons must *read* differently and the gap list is where Sam
reads them. Criterion (a)'s and (b)'s live abstentions now carry
`NO_EVIDENCE_RETRIEVED`.

T-31 gates the **vocabulary**, not the predicates: E8's
`UNSUBSTANTIATED_ASSERTION` needs T-15 plus T-16 and E10b's
`SOURCE_CONFLICT` needs T-33, so the gate asserts the three values are
distinct and reachable and that each case's *manifest* carries the shape its
reason describes — E7 nothing to retrieve, E8 a claim with no encounter
behind it, E10b two sources straddling 35.0. Putting expected reasons in the
manifests was refused: D42 made them carry facts, never verdicts.
Mutation-tested five ways, each caught: the requirement dropped, a `MET`
allowed to carry a reason, the gap list dropping it, a fifth undocumented
member, and two reasons collapsing to one value — which three separate tests
object to, since that collapse is Article IV's.

### `[x] T-16` Deterministic predicates c1 through c5
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

**Closed by D48.** `pa_agent/criteria.py` gains c1–c5 as pure predicates over
`wm_events`. c3 computes the qualifying run **once** and c2, c4 and c5 scope
to it — the tree already says `scoped_to: "c3"`, and three independent
implementations of "the run" would be three chances to disagree about which
months are in it. A zero-event abstention reads `program_assertions` for its
reason: with a claim it is `UNSUBSTANTIATED_ASSERTION` (E8, find the visit
notes), without one `NO_EVIDENCE_RETRIEVED` (E7, find a program) — D12's rule,
applied identically at c1 and c3.

**The seven edge cases run against T-15's real recorded extraction**, so the
verdicts come from events a model actually produced rather than events written
to make a predicate pass. E4's longest run is 3; E9 is not fooled by the
April no-show or the failed outreach call; E5 is c3 `MET` and c2 `NOT_MET`;
E6 is c4 `NOT_MET` with c5 `MET`, isolating the criterion; E7 and E8 abstain
with different reasons; E11 picks the 2026 run out of two programs; E1 is
`MET` on all five with a span each.

Each `NOT_MET` cites the evidence that falls short (REQ-5): c3 the short run,
c2 the run's last event, c4 and c5 the encounters in the deficient months.
Mutation-tested seven ways under cleared bytecode, each caught: zero events
treated as a short run, E7's and E8's reasons collapsed, c5 back to a count of
four, c4 deriving a BMI, REQ-15's cascade removed, c2's window hardcoded, and
the run bridging a gap month.

**A defect is recorded rather than fixed here: T-42.** REQ-14 selects the
*longest* run and REQ-32 then asks whether that run is recent, so a six-month
run three years ago beats a four-month run last month and the patient reads as
stale — a false `NOT_MET` produced by the mechanics. Implementing the
requirement as written and putting the defect on the board is the move D24,
D26 and D28 each argued for; no eval case distinguishes the two readings, so
nothing is being papered over.


### `[x] T-33` Source reconciliation for criterion (a)
**REQ:** 31, 34, 39 · **Depends:** T-13, T-15, T-31, T-39
**Exit:** `pytest tests/test_reconciliation.py` — E10 (same side of 35.0, beyond
tolerance) keeps the structured verdict and records one `discrepancies[]` entry;
E10c (below tolerance) records none; E10b (34.6 against 36.2) resolves
`INSUFFICIENT_EVIDENCE` with `gap_reason` `SOURCE_CONFLICT`; the gap list is
untouched in all three; no model call; tolerance read from the criteria tree
Runs after extraction rather than inside T-13, because the note BMI does not
exist until T-15 and US-2 makes no model call. *(D11)*

### `[x] T-18` Workflow graph
**REQ:** 1, 4, 19, 52 · **Depends:** T-13, T-16, T-46 · **Blocks:** T-19, T-61
**Exit:** `pytest tests/test_workflow.py` —
- the step sequence is a module-level constant, and a run visits exactly those
  steps in exactly that order — asserted against the recorded trace, not read
- **fan-out:** N notes produce N extraction calls, one per note, each recorded
- **fan-in:** events merge across notes and every merged span still validates
  through T-11 against the document it came from
- **retry:** a runner raising a retryable fault is retried to the declared budget
  and the attempts are recorded; exhaustion raises rather than returning a
  partial result
- **fail-closed:** a malformed model payload raises and never becomes a
  zero-event extraction
- sc1 `NotCovered`, sc1 `NoPolicyFound` and sc2 each complete with an extraction
  runner whose `run()` raises, so zero model calls is *proved* rather than counted
- no branch key derives from model output: asserted on the module AST, plus two
  different extractions visiting the same step sequence

*Exit condition rewritten by D62* (was: "fan-out, fan-in, retry, and fail-closed
all exercised; grep the module for branching on model output and find none"). The
clauses are the same four; each is now a check. A grep is not an exit condition
(D10), and "fail-closed" had to name *what* fails closed — a malformed extraction
becoming an empty one is the failure `spike/spike_001/run.py` already documents in
writing, where a transport error scores as flawless precision.

The graph is plain Python and deliberately **not** an ADK `Workflow`: that would
put `google.adk` on the import path of every deterministic test, and the three
`sys.modules` assertions that would catch it are the ones that would have to be
deleted to allow it. This graph has one conditional — whether a short circuit
fired — and that is a `return`, not an edge. *(D62)*

### `[x] T-20` Cost and latency instrumentation
**REQ:** 22 · **Depends:** T-15
**Exit:** `python eval/run_eval.py` prints per-run token counts and wall time
Lands with the first model call, not on day five. *(Article X)*

**US-4 closes when:** E4, E5, E6, E7, E9, E10, E10b, E10c and E11 all pass.

---

## `US-5` The gap list — day 4

### `[x] T-19` Aggregator and gap list
**REQ:** 19, 20, 21, 31, 39, 42 · **Depends:** T-18, T-31 · **Blocks:** T-61
**Exit:** `pytest tests/test_determination.py` —
- the **policy's** `decision_expression` is parsed and evaluated in Python over
  the criterion verdicts — parsed, not `eval()`'d and not hardcoded as `all(...)`,
  because REQ-19 says the expression decides and a hardcoded conjunction silently
  ignores an `OR` a future tree carries. An unknown token raises.
- REQ-20: any criterion resolving `INSUFFICIENT_EVIDENCE` propagates to an overall
  `INSUFFICIENT_EVIDENCE`, never `MET`
- gap list populated; documentation gaps distinguishable from substantive failures
  by `gap_reason` rather than by prose
- `discrepancies[]` surfaced as a separate list with no entry appearing on both
- a determination for a contractor-determined code cites both the NCD's delegation
  and the MAC's exercise of it, never the NCD alone *(added by D33 — the NCD
  deliberately does not answer for a delegated procedure)*
- E1's and E8's patients each produce their expected determination end to end from
  the recorded extraction, with zero model calls

*Exit condition extended by D62*: every original clause stands. Added are the
expression-parsing requirement, REQ-20's propagation, and the two end-to-end rows —
without those the aggregator could be a conjunction over verdicts nobody produced.

**US-5 closes when:** E1 and E8 pass.

---

## `US-5.5` Orchestration

Not a user story — the differential measurement has no persona-visible
behaviour, which is the Not-stories table's own criterion, and it has a row
there (`docs/stories.md`). Numbered like a story so this chain of tasks has a
home on a board organised by story-of-origin *(D72)*.

### `[x] T-62` ADK extraction runner and its declared tools
**REQ:** 22, 41, 52, 53 · **Depends:** T-12, T-15, T-46 · **Blocks:** T-61, T-63
**Discovered in:** the T-18 build *(D62)*
**Exit:** `pytest tests/test_adk_agent.py` — **zero model calls**, driven against a
fake `BaseLlm` so the test exercises real `LlmAgent`, `Runner` and `FunctionTool`
objects rather than a mock of them:

- each declared tool reaches its data through the injected port and by no other
  route: one port call per invocation with the arguments forwarded, and a store
  that raises leaves the tool with no answer to give
- neither tool module opens a file, names a path, or holds a connection —
  asserted on the module AST (REQ-41)
- the patient toolset and the policy toolset live in separate modules and no third
  module imports both (REQ-53, Art. VI)
- the extraction agent's allowlist contains the note-text tools **only**: it is not
  given `get_patient_observations` or `get_patient_conditions`
- Article I holds by construction: `include_contents="none"`, both
  `disallow_transfer_to_*` set with no `sub_agents` so the flow is `SingleFlow`
  and no `transfer_to_agent` tool is ever injected, a literal tool list, and a
  `max_llm_calls` ceiling
- ADK's structured output round-trips through `build_result()` and produces the
  same `ExtractionResult` the direct path does from the same payload, with every
  span re-validated through T-11 (REQ-52)
- a fabricated quote is counted in `dropped[]` and yields no `WmEvent`; a
  fabricated `bmi_quote` demotes the BMI rather than failing the note (D15)
- malformed output raises `ExtractionOutputError` and **never** becomes a
  zero-event extraction
- the run records one `CallMetrics` naming the pinned model, an ordered tool-call
  trace, the attempt count and a termination reason (REQ-22, Art. X)

**Why the allowlist is narrower than the toolset.** Handing extraction
`get_patient_observations` would give the model the structured BMI while asking it
for the note's. T-33 and T-60 exist because those are two independent readings that
can disagree; E10b is the case where they disagree across 35.0. A model shown both
has no reason to disagree, and the system would keep passing every test it has
because the tests compare the two values and would now find them equal. *(D62)*

### `[x] T-64` One document namespace over the patient plane
**REQ:** 41 · **Depends:** T-12, T-13 · **Discovered in:** the T-19 build *(D62)*
**Blocks:** T-17 · **Designed by:** D65
**Exit:** `pytest tests/test_fhir.py` — `PatientStore.get_document` resolves any
`document_id` a patient-plane span can carry: a bundle filename **and** a note id.
A caller holding a validated span must not have to know which read served it.

Today `get_document` resolves bundle filenames only, and notes arrive through
`get_notes`. So validating a determination's spans means building the index from
both reads, and the alternative a caller reaches for is branching on whether the
id contains a slash — a convention, which is the thing REQ-41's ports exist to
remove. `tests/test_determination.py` does the two-read version with a comment
naming this task rather than hiding it.

Not fixed inside T-19 because it widens a port a closed task owns (T-12), and
because "is `document_id` one namespace per plane or one per accessor" is a design
question with a second answer worth writing down: notes and bundles have different
provenance and different hashes, and a single namespace has to keep them
distinguishable. **T-17's verifier will hit this first** — it receives a span and
nothing else, so it has no patient id to call `get_notes` with.

**D65 answers the design question and narrows the exit.** The namespace is one per
plane, and the adapter resolves an id by looking it up in the manifests it already
loads — never by inspecting the id's shape, which would be the same convention
moved one layer down. Uniqueness is enforced rather than assumed: two records
claiming one id raise at resolution. A `kind` field on `Document` was rejected —
no consumer reads it.

**The `get_patient_document` tool is deliberately *not* widened, and `_patient_of`
survives this task.** The tool is scoping, not resolving: point it at the widened
port and the extraction agent, whose allowlist is exactly this tool plus
`get_patient_notes`, can read a FHIR bundle by filename and get the structured BMI
that T-62 withheld from it on purpose. That is **T-66**.

**Closed by D65.** `pytest tests/test_fhir.py` returns zero (18 tests).
`get_document` resolves the union of the two manifests, a colliding id raises
instead of picking a winner, and REQ-7's re-hash covers both halves —
parametrized, because the bundle reaches its hash through `_bundle` and the note
through `Document`'s own validator, and either could be dropped without the other
noticing.

**One pin is structural rather than behavioural.** A resolver that branches on
`.json` or on a slash and *then* falls through to the record answers identically
on every input this corpus can produce — the fast path is redundant with the
lookup behind it, and the first mutation written to catch it survived. So
`test_the_resolver_reads_no_structure_out_of_an_id` parses the adapter and refuses
a path shape or a `startswith`/`endswith`/`split` inside the two resolving
functions, docstrings exempt. Mutation-tested seven ways.

`tests/test_determination.py` and `eval/run_agentic_eval.py` each lost their
hand-rolled union of the two reads. That they got shorter is the deliverable.

### `[x] T-66` The document tool scopes by argument, not by id shape
**REQ:** 41, 53 · **Depends:** T-64 · **Discovered in:** the T-64 design *(D65)*
**Designed by:** D66
**Exit:** `pytest tests/test_adk_agent.py tests/test_agentic_workflow.py` —
`get_patient_document(patient_id, document_id)`, `_patient_of` deleted, and a test
proving the tool refuses a bundle filename: the extraction agent must not be able
to reach structured observations through the document tool (REQ-53, T-62's
allowlist argument).

Today the tool derives the owning patient from the id, which works only because
T-07 names every note `<patient_id>/chart_note.txt`. The model already holds the
patient id — it called `get_patient_notes` with it — so passing it is free and the
convention buys nothing.

**Changes a function declaration, therefore changes the prompt, therefore
invalidates D64's measurement.** D64 refused to change a tool for that reason and
registered T-65; this obeys the same rule. **Batch T-65 and T-66** so one
re-measurement of the agentic path covers both.

**Closed by D66**, with a defect found in this task's own text. "The model already
holds the patient id" is true of the retrieval agent and **false of the extraction
agent**: `ExtractionRunner.run(document_id, text)` has no patient id to pass and
that model never called `get_patient_notes`, so deleting `_patient_of` breaks the
`tool_fetch` variant T-63 exists to measure.

So the scope is supplied twice over, and never parsed. The retrieval agent gets
`get_patient_document(patient_id, document_id)`. The extraction agent gets
`build_note_reader(patient_store, document_id)` — one declared tool, `read_note`,
closed over the single id under review, refusing every other by an equality check
against a cell the model cannot see or name. `EXTRACTION_ALLOWLIST` is now
`("read_note",)` and the two allowlists are **disjoint** rather than nested, which
is a stronger statement than "narrower": the extractor has no route to a second
document at all — not a bundle, not another patient's note.

`_patient_of` is gone, and `test_no_tool_module_reads_structure_out_of_an_identifier`
parses both tool modules to keep it gone — the same AST shape T-64 needed when a
parse-then-fall-through mutation survived a behavioural test. Fifteen mutations
across both tasks, all caught.

### `[x] T-65` Bound what a tool may return
**REQ:** 46, 54 · **Depends:** T-61 · **Discovered in:** the T-61 measurement *(D64)*
**Designed by:** D66
**Exit:** `pytest tests/test_agentic_workflow.py` — no declared tool can return an
unbounded collection: a patient with thousands of observations yields a bounded or
paged response, and the agentic input-token ratio for E2+E7 drops from 446x toward
the 10-30x the other five patients cost.

D64 measured the whole of that outlier to one cause. `get_patient_observations`
returns every row — 3,780 for E2+E7's patient — the payload enters the context
window, and `include_contents="default"` re-sends it on every subsequent turn.
Cost is tool-payload size times turns, and it scales with the patient's chart
rather than with the question.

**This is a tool-contract question, not a model question.** The deterministic path
reads the same 3,780 observations through the same port and pays nothing for them,
because they never enter a context window and `most_recent_bmi` picks one. The
options are a filtered or paged view, or keeping structured facts out of the
model's reach entirely — which is what REQ-53 already does for the extractor, for
a correctness reason rather than a cost one.

Registered rather than fixed inside T-61: changing the tool would invalidate the
measurement that found this.

**Closed by D66. E2+E7 went 446x → 20.3x and the aggregate went 73.4x → 13.9x**,
with 6/6 outcomes, 42/42 criteria, 80/80 spans and zero errors unchanged. The
spread across six patients collapsed from 10x–446x to 9.5x–20.3x, which is the
real result: the term that scaled with the patient's chart is gone, and what is
left is turn variance that moves in both directions.

**REQ-54 is new and this task claims it** — REQ-46 lists step count, timeout, retry
budget and allowlist, and a bound on a tool's *response* is a fifth thing rather
than a re-reading of those four. Split rather than edited, so nothing a closed task
claimed changes meaning.

One ceiling, `MAX_ROWS`, in `pa_agent/agent/tool_bounds.py`, and two behaviours
behind it: **truncate where the payload informs the model's plan** (observations,
conditions, the policy value set — each returning `total`, `returned` and
`truncated`), **fault where the payload is the model's action space**
(`get_patient_notes`, because every `document_id` the model may then ask for comes
out of it and there is no page two). A single document is not a collection and is
exempt.

**Paging was rejected**: it bounds the payload and not the run, so the model pages
until it holds the chart and the cost returns as turns × payload. **A summary was
rejected** for foreclosing REQ-44, which is unclaimed and still in the spec.

The check that makes truncation *safe* rather than merely cheap is
`test_the_bundle_is_the_ports_full_read_and_never_the_models_view`, run on the
3,780-observation patient: the evidence bundle is the port's full read, so a capped
tool is a cost control and not a quiet correctness change. The mutation that
assembles the bundle from the model's view fails it.

### `[x] T-67` The spike notes have no address on the patient plane
**REQ:** 41 · **Depends:** T-64 · **Discovered in:** the T-66 build *(D66)*
**Blocks:** T-63 under `--tool-fetch`
**Exit:** `pytest tests/test_adk_measurement.py` returns zero: the eleven notes
partition into addressable and not *by asking the port*, a note the model was
never asked about is counted `skipped` and never `failed`, the `tool_fetch` path
runs end to end on an addressable note for zero model calls, and `--compare`
refuses to print two aggregates computed over different note sets.

**The registered exit was rewritten by D67** — it named
`python scripts/run_adk_extraction.py --tool-fetch`, which spends model calls, and
T-63 says in its own text that it is in no gate for that reason. A check that
costs money and varies run to run is not a gate. The measurement stays T-63's.

**Closed.** `pytest tests/test_adk_measurement.py` returns zero, 18 tests,
thirteen mutations caught. The spike notes stay off the patient plane (D67 rejects
the manifest entry in writing); `--tool-fetch` skips them by asking the port, names
each skip, and reports `by_corpus` in every mode. Building the gate also found that
the script had **never run at all** — all three record sites read a `case["labels"]`
key the corpus builders do not produce, so it raised `KeyError` on note one in both
modes. T-63 is unblocked in both modes, not only under `--tool-fetch`.

Spike 001's notes are `n01_clean_run` and friends. They are in no patient manifest,
so `PatientStore.get_document` raises and the scoped note reader has nothing to
serve — five of T-63's eleven notes fail before the model sees anything.

**Pre-existing, and not caused by T-66.** `_patient_of("n01_clean_run")` derived the
patient `n01_clean_run` and `get_notes` raised on it just as loudly; the tool got
narrower without getting less capable. It surfaces now because T-63 is next.

Registered rather than fixed inside T-66, because "do the spike notes belong to the
patient plane at all" is a question about what the corpus is (D42, D43), not about a
tool signature. The two honest answers are a manifest entry that admits they have no
patient, and a script that stops pretending one runner reads both corpora.

### `[x] T-63` Measure the ADK runner against the direct runner
**REQ:** 22 · **Depends:** T-62 · **Timebox:** two hours of calls
**Status:** **closed** — both modes measured and recorded *(D71; status line
corrected by D72)*. Still in no gate: a re-run spends model calls.
**Exit:** `python scripts/run_adk_extraction.py` writes a recording over the same
eleven notes, `--tool-fetch` writes one over the **six addressable** notes, and a
decisions entry quotes each aggregate beside `eval/extraction/results.json`'s —
precision, recall, REQ-9 exclusion, field agreement, spans anchored, model offsets
usable, tokens, wall time — for both `tool_fetch` modes, **naming the tier**.

**Eleven and six, not eleven and eleven** *(D67)*. Spike 001's five notes have no
patient, so the scoped note reader cannot resolve their ids; `--tool-fetch` skips
them by asking the port and says so. Quote `by_corpus["synthesized"]` when
comparing across modes — `--compare` recomputes over the notes both recordings
scored, so the tool and no-tool columns are the same six notes.

**One recording per mode** *(T-68, D68)*: `eval/extraction/adk_results_inline.json`
and `eval/extraction/adk_results_tool_fetch.json`. `--compare` reads the mode its
own `--tool-fetch` flag names and prints the path, so the two aggregates this exit
asks for come from two `--compare` invocations and each says which file it read.

**Spends model calls, so it is in no gate.** Nothing may claim D45's numbers for the
ADK path until this runs: D45's rule is that a changed call configuration is a new
measurement, and this changes the SDK, and under `tool_fetch` the prompt too.

**The tier is not cosmetic here.** `output_schema` and `tools` are usable together
in 2.8.0, but natively only on Vertex: `models/_capabilities.py` gates
`output_schema_and_tools` on the Vertex variant, so on AI Studio ADK instead injects
a `SetModelResponseTool` and an instruction to answer through it. D5 develops on AI
Studio and evals on Vertex, so the tool-calling path runs a **different prompt** on
the two tiers, and a number from one is not a number for the other. *(D62)*


**Closed by D71.** Both recordings written, both aggregates quoted, tier named.
**Extraction fidelity is identical on all three paths** — precision, recall,
REQ-9 exclusion and field agreement all 1.000, and **0 model-emitted offsets
usable** for a third time across a third call configuration (D18 holds).

Inline costs nothing: +5.9% input tokens, +3.7% wall over `google-genai`.
**`--tool-fetch` costs 3.64x the input tokens and 1.26x the wall time** to fetch a
document the caller was already holding — `ExtractionRunner.run(document_id, text)`
receives the text as an argument, and the tool path spends a turn asking for it.
Twelve tool calls for six notes.

**Two findings the run produced, neither by review.** One span in 76 failed to
anchor: under `--tool-fetch` the model wrote `completed` where E8's note says
`completing`, and Article III refused the paraphrase on string comparison. That
span is E8's only evidence, so its loss would change `UNSUBSTANTIATED_ASSERTION`
into `NO_EVIDENCE_RETRIEVED` — and no aggregate figure reports it, which is
**T-71**. And the measurement was **counting one turn of two**, understating
tool-fetch output tokens 12.1x and inverting the comparison's sign; fixed here
(D68's precedent — bookkeeping, and the data was already in `trace["metrics"]`,
so the repair spent no calls), with four mutations caught.

**These are AI Studio numbers and the tier changed the prompt**, not just the
endpoint: the 11 unescaped spans are the `SetModelResponseTool` round trip D62
predicted. A Vertex run is a new measurement.

### `[x] T-68` The two `tool_fetch` modes overwrite one recording
**REQ:** 22 · **Depends:** T-67 · **Discovered in:** the T-67 build *(D67)*
**Blocks:** T-63
**Exit:** `python scripts/run_adk_extraction.py` and
`python scripts/run_adk_extraction.py --tool-fetch` leave two recordings on disk,
and `--compare` names which one it is reading. A test builds both for zero model
calls, as `tests/test_adk_measurement.py` already builds one.

`ADK_PATH` is a module constant, so the second run of the pair overwrites the
first and T-63 cannot quote both aggregates — its exit condition asks for both
modes. The payload already records `"tool_fetch"`, so the file knows which mode
produced it; nothing else does.

Registered rather than folded into T-67, because "one recording per mode" is a
question about how the measurement is stored and compared, and T-67's was about
which notes each mode can reach. Small, but it has a real choice in it — a
mode-suffixed filename, an `--out` argument, or one file holding both runs — and
whichever is picked, `--compare` has to say what it is comparing.

**Closed.** `pytest tests/test_adk_measurement.py` returns zero, 26 tests, six
mutations caught. The path is **derived from the mode** —
`adk_results_inline.json` and `adk_results_tool_fetch.json`, selected by
`adk_path(tool_fetch)` — so no invocation of either mode can land on the other's
file. `--out` was rejected for keeping one default (avoidable, not impossible);
one file holding both runs was rejected for needing read-modify-write and for
breaking the record-for-record diff against `results.json` *(D68)*.

`--compare` takes the same flag, prints the path it read, labels the column from
the payload's own `tool_fetch` key, and **refuses (exit 2) when the two disagree**
— louder than the model and tier mismatches beside it, because those still print
true numbers under a true caption and this one would not.

The regression is a test that runs `measure()` in **both** modes against one
directory and asserts the first file's bytes are unchanged after the second run;
asserting two filenames differ passes on a program that writes both and truncates
one.

**The close also found T-67 had left the suite red.** `_recording()` wrote the
model name as a literal and `tests/test_model_pin.py` scans tracked Python, test
files included (D20). T-67's exit names `pytest tests/test_adk_measurement.py`,
which passed. A task's exit command is not a substitute for the suite; fixed here
on D67's precedent and recorded in D68 rather than registered. **T-69 registers
the gap that let it happen.**

### `[x] T-69` A task can close with the rest of the repo red
**REQ:** none — this is a working rule, not a spec requirement
**Depends:** none · **Discovered in:** the T-68 build *(D69)*
**Exit:** `python scripts/check_gates.py` returns zero, running every zero-cost
gate in the repo; `pytest tests/test_check_gates.py` returns zero and spends
nothing, asserting that a failing gate fails the command, that every tracked
script under `scripts/`, `eval/` and `spike/` is either a gate or an exclusion
with a stated reason, and that the runner refuses to run inside pytest.
Working rule 4 in `CLAUDE.md` names the second command.

Two consecutive closes produced a finding of this shape — T-67's found a script
no gate ran, T-68's found a gate no close ran. Both are one sentence read from
two ends: the set of checks a task runs is smaller than the set the repo has, and
nothing measured the difference.

**Closed.** Eight gates, about twelve seconds, seven mutations caught. Membership
is auditable rather than judged: a command is a gate iff some task's exit
condition names it *and* it spends no model call and touches no network — the
cost half alone would admit `run_extraction.py --rescore`, which is a
measurement's bookkeeping and not a check on the repo. `EXCLUDED` carries a
reason per script and the test fails on a tracked script in neither list, so the
list cannot rot the way the unwritten rule did.

**A test was deleted during the build and the deletion is the interesting part.**
Spawning `check_gates.py` from inside pytest to assert the recursion guard is a
fork bomb once the guard is removed: the child runs the suite, the suite reaches
the test, and it spawns another child. The mutation pass hung for two minutes and
returned no exit code — a hang is not a catch. The guard is asserted by parsing
`main` instead (D69).

**It cannot make anyone type the command.** A pre-commit hook was rejected —
untracked, so it survives no clone and shows in no diff, and bypassable — and
working rule 9 names CI explicitly.

### `[x] T-61` Agentic orchestration and model adjudication

**REQ:** 43, 45, 46, 48, 49, 50, 51  
**Depends:** T-18, T-19, T-20, T-26, T-46, T-62

**REQ-44 and REQ-47 are deliberately not claimed (D63).** They describe the model
*evaluating criteria and determining outcomes*, and this task does not build that.
Amendment 1 reserves date arithmetic, numeric comparisons, counting, sorting and
set membership to Python **on both paths** — which is the entire decision procedure
for all seven criteria — so there is no verdict the model could decide without
doing something the amendment reserves. The model directs retrieval here and Python
still adjudicates. Both requirements stay in the spec, unclaimed, which is the
honest state: a permission the constitution grants and no task has yet taken up.
Listing them here would make A7's "every REQ maps to a passing check" a lie.

**T-26 and T-62 added to `Depends` by D62.** T-26 was missing and it is real: this
task's exit requires malformed and contradictory output to resolve to `ERROR`, and
`ERROR` did not exist — `CriterionVerdict` carried two of Article IV's three states
and `tests/test_schemas.py` asserted the third's absence on purpose. T-62 for the
same reason: this task's tool allowlist, tool-call trace and run bounds are T-62's,
reused rather than rebuilt.

**Amendment 1 is what makes this task legal.** D62 restored Articles I and II to
their committed text after they had been rewritten in place, and kept Amendment 1
appended and scoped instead. Model adjudication is permitted here, on this path,
measured against the deterministic implementation — which is only an oracle because
it is still bound by the articles as written.

**Exit:** `pytest tests/test_agentic_workflow.py` and
`python eval/run_agentic_eval.py` — **both spend zero model calls.**

The tests must prove that:

- the model can select only allowlisted tools, and a call to anything else is
  refused rather than answered;
- tool calls are recorded in order, with arguments digested rather than stored;
- the model can request additional evidence — a second document, the structured
  facts — and the run reflects what it asked for;
- execution stops at the step, timeout and retry limits, and exhausting any of
  them terminates with a named reason rather than a partial answer;
- every document the model gathered is hash-verified and every span it produced
  validates through T-11 before a criterion sees it;
- malformed and contradictory model output resolves to `ERROR` with a classified
  code, never to a verdict;
- a planner that gathered nothing yields `INSUFFICIENT_EVIDENCE`, never `NOT_MET`
  — "the model did not look" and "the chart does not say" are different answers;
- policy artifacts cannot be modified by the model: the policy tools are
  read-only and the criteria tree is never written;
- deterministic validation remains authoritative — the same gathered evidence
  produces the same verdicts as the fixed planner, because the criteria code is
  the same code;
- agentic and deterministic results are compared on identical inputs.

`python eval/run_agentic_eval.py` scores a recording and reports criterion-level
and overall differences against the deterministic oracle, plus unsupported-outcome
rate, citation validity, error rate, token usage, latency, tool-call count and
termination reason. `--measure` spends the calls and writes the recording.

*Exit condition rewritten by D63*, for two defects in its own text. It named
`python eval/run_agentic_eval.py` as a gate while requiring live comparison, so
every run would cost money — and **a gate that costs money is a gate that gets
skipped** (Art. VIII). It also asked for model *outcomes* to carry verified spans,
which describes a path this task does not build; the equivalent obligation, that
gathered evidence is verified before a criterion sees it, replaces it.

**US-5.5 closes when:** the agentic path completes the differential without
violating its execution bounds, and every discrepancy is reported rather than
silently reconciled.

*(Was "US-7 closes when". T-61 sits under US-5.5; US-7 is "Show me where the
system stops being reliable" and closes on T-21, T-22, T-23 and T-28, none of
which this task touches. Corrected by D63.)*

**Closed by D64.** `pytest tests/test_agentic_workflow.py` returns zero (29
tests, no model call) and `python eval/run_agentic_eval.py` returns zero against
`eval/agentic/results.json`.

The measurement: **6/6 outcomes and 42/42 criteria agreeing, 80/80 spans valid,
zero errors, every bound respected — at 73.4x the input tokens and 4.7x the model
calls.** The agentic path is exactly as correct as the oracle and pointless on
this corpus, which is a more useful result than a disagreement would have been.

The per-patient spread is 10x to 446x and the outlier has one cause: a patient
with 3,780 observations, a tool that returns all of them, and a context that
re-sends them every turn. **The model pays to look at data Python filters for
free.** That is T-65.

---

## `US-6` Trustworthy citations — day 4

### `[ ] T-17` Blind verifier
**REQ:** 17, 18, 31 · **Depends:** T-15, T-31 · **Gates:** Article V
**Status:** **ready** — both dependencies closed. Fourth on the critical path, and
the only task implementing Article V, which has no implementation today *(D70)*.
**Exit:** `pytest tests/test_verifier.py` — mismatched span and verdict rejected;
the verifier's input contains no reasoning trace and no other criterion; a
rejection resolves the criterion to `INSUFFICIENT_EVIDENCE` with `gap_reason`
`VERIFIER_REJECTED`, spends exactly one verifier call, and still emits a
`Determination`

Two design points the build must decide and log before the code *(working rule
5, noted by D72)*: how the verifier keeps every gate and the default CLI path at
zero model calls once it sits in the chain — spec §6 implies a stubbed verifier
and the recorded-or-stubbed harness story is unwritten — and whether the live
verifier gets its own measurement, since the repo's measure-then-replay pattern
(T-63's shape) has no analogue for it and D20's reversal clause already
anticipates the second pinned model.

**US-6 closes when:** a verifier rejection resolves to `INSUFFICIENT_EVIDENCE`
end to end, carrying `VERIFIER_REJECTED`.

---

## `US-7` Where the system stops being reliable — day 5

### `[ ] T-21` Expand the eval set to all of spec §6
**Depends:** T-06, T-10, T-41 *(for the E12 row)*, T-73, T-75 *(D74)* ·
**Gates:** A1, A3 · **Rewritten by:** D74
**Status:** on the critical path, after T-75 — the labels this task authors are
what the acceptance gates score against, so ownership is ratified first *(D74)*
**Exit:** `python eval/run_eval.py` runs every case in spec §6, all labeled;
every case in `eval/cases.json` and every manifest in `eval/manifests/` carries
an `adjudicated: {by, date}` record, and `python scripts/check_ownership.py
--require eval` returns zero — closing flips `eval` into the ledger's
`required_tiers` *(D74)*

Agent-drafted labels land as proposals carrying reasoning and spans; Troy's
adjudication records are what close them, and the adjudication of each
patient's manifest happens in the same reading pass that labels its cases
*(D74 — this is where D42's authorship caveat is retired for the eval set)*.

**This is the task that closes two stories.** US-4 and US-5 are built — every
predicate, the reconciliation, the aggregator and the gap list pass their unit
tests — and both close on harness rows that do not exist, because
`eval/cases.json` still holds one case. Their determinations already run end to
end on recorded extractions for zero model calls, so this is labeling and
baselining, not building *(D70)*.

Expect the baseline diff to be the substance of the close: D27's gate fails on
drift in **either** direction, so every case moving off `BLOCKED` is acknowledged
in the commit rather than noticed in a table.

### `[ ] T-72` A5's curve names a threshold the system does not have
**REQ:** none — reconciles acceptance gate A5 · **Blocks:** T-22 ·
**Discovered in:** the D72 documentation review · **Timebox:** two hours
**Status:** on the critical path — T-22 cannot be specified without it; runnable
any time, since it depends on nothing open
**Exit:** a decision entry choosing what A5's coverage/accuracy curve is a curve
*over* — or replacing it — then spec §7's A5 and US-7's third bullet read the
choice, and T-22's exit names the mechanism.

A5, US-7 and T-22's exit all ask for a curve across "a range of fail-closed
thresholds," naming the threshold where abstention reaches one. No such
threshold exists: every verdict is a deterministic predicate with no confidence
score, and no decision entry defines the sweep variable. The candidate readings
differ in kind — sweep a real constant the tree carries
(`discrepancy_tolerance`, the lookback window) and report abstention against
it, or replace the curve with an abstention account per `gap_reason` — and they
produce different reports, which is why this is a decision and not a patch made
while building T-22 *(working rule 5; T-37's shape)*.

### `[ ] T-22` Metrics report
**Depends:** T-20, T-21, T-72 *(D72)* · **Gates:** A2, A3, A5, A6
**Status:** on the critical path; blocked on T-21 and T-72
**Exit:** `eval/report.md` with per-criterion precision, span validity rate,
abstention rate, the A5 measurement T-72 chooses, cost and latency

### `[ ] T-28` Baseline and base rate in the metrics report
**Depends:** T-22 · **Gates:** A2
**Status:** on the critical path; blocked on T-22
**Exit:** `eval/report.md` contains the `MET` base rate and an always-`MET`
baseline score next to measured precision
A2 requires it: a precision figure without its base rate does not satisfy the gate.

### `[ ] T-23` README
**Depends:** T-22 · **Gates:** A7, A8
**Status:** last on the critical path; blocked on T-22
**Exit:** `python scripts/check_req_coverage.py` — every REQ in spec §5 maps to a
passing check **or** appears in §5's *Unclaimed in v1* list, and the script reads
that list rather than assuming it empty. A REQ in neither fails. A REQ added to
the list without a `docs/decisions.md` entry naming it fails — the list is what
makes A7 satisfiable, and a list that absorbs whatever is inconvenient makes it
meaningless instead. *(Exit extended by D70.)*

Closing this task **moves `scripts/check_req_coverage.py` into
`scripts/check_gates.py`'s `GATES`**, under T-69's membership rule: some task's
exit condition names it, it spends no model call, it touches no network. The name
is already referenced in `tests/test_check_gates.py` as a script that does not
exist yet, so that reference flips rather than being added — and the
classification test fails if it is left in neither list.

**US-7 closes when:** A1, A2, A3, A5, A6, A7 and A8 all hold. A4 closes under
US-1 and US-3, A9 under US-9.

---

## `US-8` Policy governance

Satisfied by git plus REQ-4. Becomes real work when the criteria compiler lands
in week 2.

---

## `US-9` Withhold what the system couldn't compute

### `[x] T-26` `ERROR` state in the data contracts
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
**Status:** **ready** — all three dependencies closed. Third on the critical path.
**Exit:** `pytest tests/test_fault_injection.py` — four tests, one per failure
point: the model call raises, the model returns unparseable JSON, span offsets
point past the end of the document, a predicate raises. Each asserts `ERROR` with
the right `error_code`, no `Determination` emitted, and a non-zero CLI exit. The
raising call is retryable, so it asserts `ERROR` only after N attempts and checks
the count; the other three assert `ERROR` on first occurrence with one model call
spent. Asserts on the AST of every module under `pa_agent/` that no `except`
handler is bare or catches `Exception` without re-raising or mapping to a named
`error_code` (REQ-27) — parsed, not grepped. *(Was a grep; strengthened by D72
on D65's and D67's precedent that substring scans are the gameable form.)*

### `[ ] T-30` `ERROR` accounting in the eval harness
**REQ:** 28 · **Depends:** T-10, T-26 · **Gates:** A9
**Status:** **ready** by its stated dependencies; sequenced after T-29 so US-9
closes in one pass rather than half-closing
**Exit:** `pytest tests/test_metrics_error_accounting.py` — a seeded `ERROR`
leaves the reported abstention rate unchanged
An `ERROR` counted as an abstention would make T-22's curve report caution where
there was a crash.

**US-9 closes when:** A9 holds — no determination carrying an `ERROR` can be
emitted, and a seeded `ERROR` leaves the abstention rate unchanged.

---

## Attached to no story

Real work with a runnable exit that delivers no user outcome.

### `[x] T-73` The ratification ledger and its gate
**REQ:** none — implements D74's protocol · **Blocks:** T-74, T-75, T-76,
T-21's rewritten exit · **Discovered in:** the D74 ownership review ·
**Timebox:** half a day
**Status:** closed — 235 ids seeded `proposed`, the gate is ninth in `GATES`,
20 tests in `tests/test_check_ownership.py`
**Exit:** `python scripts/check_ownership.py` returns zero — and
`python scripts/check_gates.py` green with the new gate in `GATES`, which
T-69's membership test forces in the same commit

`docs/ratifications.json` maps every load-bearing ID — Articles I–X and
Amendment 1, every REQ in spec §5, §6's edge cases, §7's acceptance criteria,
US-1–US-9, every task on this board, every decision entry — to
`{status, by, date}`, seeded all-`proposed`, grouped into D74's seven tiers.
The gate parses the five docs to enumerate the IDs and fails on: a doc ID the
ledger is missing; a ledger ID resolving in no doc; an `overruled`/`amended`
entry whose linked task does not resolve on this board; a malformed entry; a
`proposed` entry in any tier the ledger's `required_tiers` names; and,
once `eval` is required, a manifest or case in `eval/` without a well-formed
`adjudicated` record. `--require <tier>` (repeatable) requires a tier for one
run — the ratification tasks' exits use it before flipping the tier into
`required_tiers`, after which the bare gate invocation enforces it forever.
Statuses other than `proposed` are Troy's edits only (working rule 11, D74).

### `[~] T-74` Ratify the constitution, the spec and the stories
**Depends:** T-73 · **Discovered in:** D74 · **Timebox:** one session
**Status:** in progress — the scaffold (`scripts/ratify.py`, D75) is built;
the reading is Troy's, not an agent's
**Exit:** `python scripts/check_ownership.py --require constitution --require
spec --require stories` returns zero, and the close flips those three tiers
into `required_tiers`

An agent may scaffold a checklist view; every status written is Troy's edit.
A disagreement is an `amended` or `overruled` status naming a new numbered
task (working rule 6) — the gate refuses one that names nothing.

### `[ ] T-75` Ratify the load-bearing decisions and the whole board
**Depends:** T-74 · **Discovered in:** D74 · **Timebox:** two sessions
**Status:** third on the critical path; the reading is Troy's, not an agent's
**Exit:** `python scripts/check_ownership.py --require decisions-core
--require tasks` returns zero, and the close flips both tiers into
`required_tiers`

`decisions-core` is the 27-entry set D74 froze — the decisions CLAUDE.md's
invariants and domain-facts sections cite. A blown timebox gets a decisions
entry naming what broke (working rule 8), not a silent grind.

### `[ ] T-76` Ratify the remaining decision entries
**Depends:** T-73 · **Discovered in:** D74
**Status:** off the critical path; proceeds in batches, blocks nothing
**Exit:** `python scripts/check_ownership.py --require decisions-all` returns
zero, and the close flips `decisions-all` into `required_tiers`

D74's reversal clause applies here: if this stalls indefinitely, it shrinks to
opportunistic ratification and the ledger's `proposed` count keeps the gap
visible rather than hidden.

### `[ ] T-27` Planner recall against the oracle's evidence bundle
**REQ:** 25 · **Depends:** T-21, T-22, T-61 · **Rewritten by:** D70
**Status:** off the critical path; blocked on T-21 and T-22 (needs the full eval
set and a report to write into)
**Exit:** `eval/report.md` carries per-criterion planner recall — for each
criterion, the fraction of cases where the evidence `AgenticRetrievalPlanner`
gathered contains the span `FixedRetrievalPlanner` read for it — reported beside
the differential's outcome agreement, and a case where the planner skips a
document resolves below 1.000 rather than being invisible.

*Was "Retrieval recall instrumentation", exiting on `python eval/run_eval.py`
printing per-criterion recall@k.* There is no k: D4 rejected the ranked retriever
that phrasing presumes, so the deterministic path serves whole notes and reads the
port's full observation list, and the figure was 1.000 by construction.

**The requirement acquired a mechanism in T-61 and nobody re-aimed it.**
`AgenticRetrievalPlanner` chooses what to gather, and D63 names the failure mode
in its own docstring — a skipped note leaves c3 measuring a shorter run,
forgotten observations make criterion (a) abstain — each producing a
determination that is well-formed and quietly wrong. D64's differential would
catch that only when it happened to change a verdict on these six patients.

The oracle supplies the denominator, which is what makes this cheap: the harness
already holds both bundles on identical inputs and today compares only the
verdicts downstream of them.

**D4's reversal condition now reads against this number.** It was set as
"measured retrieval recall below 0.85 — a number, not a hunch" and has been
unfalsifiable since it was written, because nothing measured retrieval recall and
nothing could. Vector search stays rejected on rule 9 and on a six-document
corpus; this is what would let it back in on evidence *(D70)*.

### `[ ] T-32` Plane separation check
**REQ:** 33, 41 · **Depends:** T-09, T-12, T-24 · **Gates:** Article VI
**Status:** **ready** — all three dependencies closed. Fifth on the critical path.
**Partly asserted already, and do not rebuild those.** `test_index.py`,
`test_spans.py`, `test_criteria_ab.py` and `test_adk_agent.py` each parse one
module's AST for its own import restriction, and `test_schemas.py:389` and
`test_resolver.py:215` both name this task where a combined handle would be
caught. What is missing is the **global** walk: those are per-module assertions
that a new module joins by remembering to.
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

### `[x] T-36` Decide whether sc1 needs a third outcome
**REQ:** 1, 2, 42 · **Depends:** T-35 · **Discovered in:** T-02 *(D22)* ·
**Answers:** open question 3's second half
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

**Closed: it is a third outcome (D33, REQ-42).** `resolve_sc1` returns
`ResolvedByContractor` — distinct in type from `Resolved`, `NotCovered` and
`NoPolicyFound`, identical in flow to a covered code because this corpus's MAC
exercised the delegation and covers the procedure. The delegation claim and
the MAC's exercise both slice back (Art. III), asserted in
`tests/test_resolver.py`. Rejected: mapping to `Resolved` (the distinction
survives only as a field nobody must read — D26 one layer up), and gating on
MAC exercise (the no-exercise branch has no exemplar under D21; D33's reversal
clause picks it up if one lands). Assembly raises citing T-19 for the
contractor code too, and T-19's exit gains the obligation to cite both the
delegation and the exercise, never the NCD alone. Mutation-tested three ways,
each caught: contractor mapped to `Resolved` (the rejected alternative), the
raise dropping the obligation phrase, and the contractor branch folded into
the generic covered raise.

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

### `[x] T-38` Procedure sets in the criteria tree, and REQ-2 rewritten to read them
**REQ:** 1, 2 · **Depends:** T-35 · **Blocks:** T-24, and US-1's close ·
**Discovered in:** a design walkthrough, not a task *(D26)*
**Timebox:** two hours
**Exit** *(rewritten by D30 — the original demanded a span "naming that code"
for every code, which D28 had already proven the corpus cannot supply, while
also demanding 43842 in the non-covered set)*:
`pytest tests/test_criteria_tree.py` —
- the tree carries three named procedure sets: nationally covered, nationally
  non-covered, and contractor-determined; members are **procedures** in D28's
  two-citation shape
- every member's `coverage_claim` carries a `(document_id, char_start,
  char_end)` that slices back to its quote, scoped the way E3's is; every
  non-covered claim falls inside §C's list, not merely inside the document
- every identity code binding is `in_corpus: true` and slices back to a quote
  naming **both** the code and the procedure, in `r931cp` or `a53028` *(D29)*;
  facility billing lists are spanned transcriptions marked `identity: false`
  and are not lookup keys *(D30)*
- the three sets are pairwise disjoint over identity codes, so no code has two
  answers
- 43775 is in the contractor-determined set and in neither of the others *(D22)*
- the code T-35 picked for E3 is in the nationally non-covered set
- no coverage claim anywhere in the tree cites `r931cp` *(D29's scope rule)*
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

**Closed by D30, after T-40 ran first by this task's own reordering clause.**
Members are procedures in D28's two-citation shape; ten identity bindings, all
`in_corpus: true` — seven CPT from `r931cp`, 43775 and 0DV64CZ from `a53028` —
plus A53028's facility ICD-10-PCS lists as spanned transcriptions marked
`identity: false`, because recording them surfaced the finding that they
overlap across procedures (0D160ZB in two lists; 0DV64CZ and 0DB64Z3 inside
the lap Roux-en-Y list while the article assigns them to LSG), so a facility
code is not a procedure identity in this source. Disjointness runs over
identities. REQ-1 and REQ-2 rewritten; `covered_procedures` is gone from the
spec. `LocalPolicyStore.resolve` now cites T-24, and E3 stays
`BLOCKED/NOT_IMPLEMENTED` with no baseline drift. Mutation-tested eleven ways,
each caught by the gate built for it — the sharpest being the VBG claim
re-pointed at §D's delegation paragraph, which slices back perfectly and means
the opposite, and only the containment gate catches it.

### `[ ] T-42` The longest run is not always the qualifying run
**REQ:** 14, 32 · **Depends:** T-16 · **Discovered in:** T-16 *(D48)* ·
**Timebox:** two hours
**Status:** **ready, off the critical path** — blocks nothing, since no §6 case
distinguishes the two readings. Kept on the board rather than withdrawn: it is a
real false `NOT_MET` produced by the selection rule, and the behaviour is pinned
by a test that has to be changed deliberately *(D70)*.
**Exit:** a decision entry resolving it, then `pytest tests/test_criteria_c.py`
— a chart carrying a long stale run *and* a shorter run inside c2's window
resolves c2 and c3 the way the entry says it should, and the case exists in
the test. `docs/spec.md`'s REQ-14 and REQ-32 read whatever was chosen.

REQ-14 returns the longest run of consecutive populated months; REQ-32 then
asks whether **that** run ended inside the recency window. A six-month run
three years ago therefore beats a four-month run last month, and a patient who
completed four consecutive supervised months within the window is reported
stale. It is a false `NOT_MET` — the cheaper direction, an unnecessary chart
review rather than a wrong denial — but it comes from the selection rule
rather than from the evidence.

T-16 implemented the requirement as written and pinned the behavior in
`test_the_longest_run_wins_even_when_an_older_one_is_stale`, so changing it is
a deliberate act. Two candidate readings: select jointly (prefer a run that
satisfies c3 *and* c2, falling back to longest), or keep longest and let c2
consider every qualifying run. They differ on which run c4 and c5 then scope
to, which is why this needs a decision and not a patch.

Not folded into T-16: a requirement changed by the task that implements it is
a requirement nobody agreed to *(working rule 5)*. No case in spec §6
distinguishes the readings today — E5 has one run and E11's longest is also
its most recent — so this blocks nothing until a chart with two real programs
lands.

### `[ ] T-71` A lost assertion reports as a flawless run
**REQ:** 31, 35 · **Depends:** T-16, T-31 · **Discovered in:** the T-63
measurement *(D71)* · **Timebox:** two hours
**Status:** ready, off the critical path
**Exit:** `pytest tests/test_adk_measurement.py` — the aggregate reports
assertion coverage, and a recording in which a note carrying
`assertion_required: true` yields zero assertions is distinguishable **from the
aggregate alone**, without opening a per-note record.

T-63 lost E8's `program_assertions[]` span to a paraphrase the anchorer correctly
refused. The per-note score says so exactly — `assertion_required: true`,
`assertions: 0` — and the aggregate reports precision 1.000, recall 1.000, REQ-9
exclusion 1.000 and field agreement 1.000, because a note with zero labeled events
contributes to no fidelity ratio. The only visible trace is `spans_emitted` sitting
one above `spans_anchored`.

That is spike 001's documented trap wearing new clothes: *a transport error scores
as flawless precision*. Here a refused citation does. E8 is the refusal test and
the whole argument for `gap_reason` (D12, D44) — losing its evidence changes what
the determination tells Sam to collect, and no headline figure moves.

Not folded into T-63: T-63's exit names the figures it must quote, and adding one
inside it is a requirement changed by the task that implements it *(working rule
5, and the reason T-37 and T-38 exist)*.

### `[ ] T-70` A comparison gate asserts on a substring
**REQ:** none — a test-quality defect · **Discovered in:** the T-63 measurement
*(D71)* · **Timebox:** one hour
**Status:** ready, off the critical path
**Exit:** `pytest tests/test_adk_measurement.py` —
`test_compare_recomputes_over_the_intersection_and_never_pools` asserts that the
sentinel value is absent **from the parsed column it belongs to**, not from the
whole rendered output, and a mutation that pools the corpora still fails it.

The test writes a sentinel of 99 into one runner's spike-note score and asserts
`"99" not in out`. Any figure anywhere in the comparison that happens to contain
those two digits fails it — and this task's own corrected tool-fetch input total is
**22,969**. The assertion is one substring collision away from failing for a reason
unrelated to what it tests, which is D31's stale-substring lesson pointed at a gate
rather than at a message.

Registered rather than fixed inside T-63 for the same reason as T-71. Honest note
on how it surfaced: the test failed **once**, during a mutation pass that was
rewriting the script and clearing `__pycache__` between runs, and did not reproduce
in five subsequent full-suite runs. The flake is unproven; **the brittleness is
not** — it is visible by reading the line, and that alone is the defect.

### `[x] T-41` E12 has no patient, and the boundary case needs one
**REQ:** 11 · **Depends:** T-04 · **Blocks:** T-21's E12 row · **Gates:** A1 ·
**Discovered in:** T-06 *(D42)* · **Timebox:** two hours
**Exit:** `python scripts/select_patients.py --verify` and
`pytest tests/test_manifests.py` — a committed patient whose **structured**
most-recent BMI is exactly 35.0 within the lookback window, recorded in the
population manifest, and an `eval/manifests/` entry claiming E12; the
manifest gate's `EXPECTED_CASES` gains E12 and `DELIBERATELY_ABSENT` loses it.

Criterion (a) reads structured data, so E12 — "BMI exactly 35.0, boundary
inclusive" — needs a patient whose *observation* holds 35.0. No committed
patient does, and Synthea cannot be seeded to produce one to order. The
boundary is pinned today at the criterion level in `tests/test_criteria_ab.py`
(T-13), which is why this does not block US-2; what it blocks is A1, since
spec §6 requires every case present and labeled in the harness.

Papering over it with a note BMI of 35.0 is the move to refuse: that tests
reconciliation (REQ-34), not the boundary, and it would certify the wrong
mechanism under E12's name. The likely shapes are a seventh committed bundle
found by seed search, or a documented synthetic observation appended under
its own provenance record — which is a **decision** about whether the
population stays purely generated, not a data edit.

**Closed by D73, the second shape, with the seed search skipped by choice.**
A seventh bundle from a second recorded Synthea run (seed 1002) carries one
appended synthetic observation — a clone of the patient's own most-recent BMI
observation with a new id, value exactly 35.0, dated 2026-08-15 — declared in
the population manifest's `synthetic_observations` provenance block, which
`select_patients.py --verify` now checks unconditionally: exactly one
declaration, present in the declared bundle with the declared value and date,
most-recent, in-window. The patient is **note-free** (`"note": false` in its
eval manifest, with the rule enforced in both directions: a declared
note-free patient with a note fails, a missing note without the declaration
fails), so the task spent zero model calls. Mutation-tested seven ways, each
caught. The risk the board named did not materialize, but a different one
did: the base seed-1001 regeneration reproduced only five of six committed
bundles byte-identical — Synthea is not byte-deterministic — so the drifted
bundle was restored from git and the committed corpus stays pinned by hash,
recorded in D73.

### `[x] T-39` A provisional constant must name an *open* question, not any question
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

**Closed by D34.** Spec §9 splits into `### Still open` and `### Resolved`;
a question's status is the subsection it sits under, and
`_question_statuses()` in `tests/test_criteria_tree.py` reads only that stated
split — refusing a missing or extra heading, a number under both, or a number
floating under neither, because an unreadable section must fail loudly rather
than return a set that passes the wrong test. `~~` markup carries no semantic
load anymore. The parser is exercised by permanent synthetic tests (D27's
self-check pattern), since the resolved-question branch has no live exemplar
while the spec is healthy. Mutation-tested three ways, each caught by the test
built for it: question 4 moved under Resolved, `a.lookback_months` pointed at
resolved question 6 — the exact citation the old parser accepted — and the
`Still open` heading deleted. Questions 4 and 5 still pass, still flagged.

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
### `[x] T-43` Reconcile the environment pins and declare every direct import
**Depends:** none · **Discovered in:** the T-18 architecture review *(D49)* ·
**Guards:** D19, D45, D47, D48
**Exit:** `python scripts/check_env.py` returns zero —
- every pin in `requirements.txt` equals the version installed in the running
  interpreter's environment;
- every third-party top-level import in tracked Python resolves to a
  distribution named in `requirements.txt`, found by parsing each file with
  `ast` rather than from a hand-kept list;
- an import the scan cannot map to a distribution **fails** rather than being
  skipped;
- `google-adk` is still pinned at exactly `2.8.0`;

and `pytest` still returns 289 passing on the declared set.

`requirements.txt` claimed `pydantic==2.12.3` and `pytest==8.4.2` while the venv
held 2.13.5 and 9.1.1, and `google-genai` — the SDK issuing every model call the
project has measured — was undeclared, arriving transitively through
`google-adk`. The pins move **up** to the installed set, because that is the
environment every recorded number in `docs/decisions.md` was produced by; the
file is what drifted. *(D49)*

### `[x] T-60` A note-level current BMI, and a new extraction measurement
**REQ:** 34, 38 · **Depends:** T-15 · **Discovered in:** T-33 *(D50)* ·
**Blocks:** T-33
**Exit:** `python scripts/run_extraction.py` records a fresh
`eval/extraction/results.json`, then `pytest tests/test_extraction.py` returns
zero over the recording, spending no model call, and additionally —
- `Extraction` carries `current_bmi` and `current_bmi_quote`, and
  `ExtractionResult` carries the value with a span that validates through T-11;
- the E10b note yields `current_bmi` 36.2 anchored to its own clinic line,
  with **zero** `wm_events`, so E8 is unchanged;
- notes stating no standalone BMI yield `current_bmi` `None` rather than a
  value borrowed from an encounter;
- event precision, recall and REQ-9 exclusion are reported for the widened
  schema and any movement against T-15's figures is recorded, not retried.

E10b's BMI sits in a standalone `Measured in clinic today` line and its patient
has no encounters by design (it is also E8), so nothing in the system could
reach the value REQ-34 needs. Both sub-35 patients are deliberately
encounter-free, so no fixture can host the case instead. *(D50)*

### `[x] T-46` The value set arrives through the policy port
**REQ:** 41 · **Depends:** T-05, T-09 · **Discovered in:** the T-18
architecture review *(D52)* · **Blocks:** T-18
**Exit:** `pytest tests/test_valueset_port.py` — `PolicyStore` declares
`get_value_set(value_set_id) -> frozenset[str]`; `LocalPolicyStore` serves it;
the id comes from criterion (b)'s `value_set_id` constant rather than a
literal; an unknown id raises and a file whose `value_set_id` no longer matches
its path raises; the codes returned are the system `Condition.code` actually
carries, proved by intersecting them with the committed bundles; criterion (b)
answers `MET` for a real patient end to end; and no module under `pa_agent/`
outside `stores/` names the value set's location.

T-13 built criterion (b) to take the set as a parameter and left "which port
serves it at runtime" explicitly to T-18. Until now the only readers were two
test files, both by path. The set is a compiled fragment of the **policy** —
A53028's Group 1 decides which comorbidities count — so it travels with the
tree and is versioned with it. *(D52)*


## Working rules

**The rules live in `CLAUDE.md`.** This section used to hold five of them while
`CLAUDE.md` held ten, and two copies of a rule set are one copy plus a thing that
drifts — which is the defect this document was just cleaned of. Removed rather
than reconciled *(D70)*.

Two of them decide how this file changes, so they are worth naming here:

- **Discovered work becomes a new numbered task**, not a silent addition to the
  task in progress.
- **Log the decision in `docs/decisions.md` before writing the code it
  justifies** — including a rewrite of a task's exit condition, because a weak
  exit condition is a design decision. *(Article IX)*
