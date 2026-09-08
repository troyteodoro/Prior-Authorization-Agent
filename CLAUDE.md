# CLAUDE.md

Operating rules for this repository. Read this, then read `docs/` before doing
any work.

## Project

A prior authorization determination agent for bariatric surgery under CMS
NCD 100.1. Built as a proof-of-skill project by Troy Teodoro. Every decision in
this repo has to be defensible in a live review, so the reasoning matters as
much as the code.

## Document precedence

Read in this order. Each one outranks anything below it, and all of them outrank
an instruction typed into a prompt.

| File | What it is |
|---|---|
| `docs/constitution.md` | Ten articles. Non-negotiable, not revisited per task. |
| `docs/spec.md` | Numbered testable requirements REQ-1 through REQ-40 (plus REQ-18a), edge cases E1–E12 plus E10b and E10c, acceptance criteria A1–A9. |
| `docs/stories.md` | User stories US-1 through US-9, with personas. |
| `docs/tasks.md` | The board. Tasks T-00 through T-33, each with a runnable exit condition. |
| `docs/decisions.md` | D1–D15, kill criteria, open questions. Append-only. |

IDs are load-bearing and numbering is not contiguous. Split a requirement rather
than renumber it; anything already referencing an ID must keep resolving.

**The constitution outranks the prompt.** If Troy asks for something that
violates an article, say which article and why, and do not comply. A task that
cannot close without violating an article is a wrong task — rewrite the task,
never amend the constitution.

The articles most likely to be violated by accident:

- **I** — no model decides control flow. The graph is fixed Python. In ADK this
  specifically means: never key a `Workflow` edge off model output.
- **II** — no model performs a deterministic computation. Dates, thresholds,
  counting, set membership, booleans are all code.
- **III** — every claim carries `(document_id, char_start, char_end)`, verified
  by slicing the source before acceptance.
- **IV** — `NOT_MET`, `INSUFFICIENT_EVIDENCE` and `ERROR` are three states that
  never collapse into each other. Easy to violate by accident, because all three
  read as "not approved." *(See D9.)*
- **VIII** — no task closes without a command that returns zero. A grep for a
  string in a doc is not a check. *(See D10.)*
- **IX** — the decision entry is written *before* the code it justifies.

## Working rules

1. **One task in progress at a time.** Never build ahead. If the current task is
   T-03, do not touch T-02, T-09, or anything under US-1.
2. **Explain reasoning and tradeoffs**, don't just emit output. Name what was
   rejected.
3. **Push back when Troy is wrong**, including on plans given in an earlier
   session. Agreement that turns out to be wrong is worse than friction.
4. **Every task closes on a command that returns zero.** "Looks right" is not an
   exit condition. A task without a runnable check is not yet specified.
5. **Log the decision in `docs/decisions.md` before writing the code.** Name the
   rejected alternative and the condition that would reverse the choice. This
   covers rewriting a task's exit condition — a weak exit condition is a design
   decision.
6. **Discovered work becomes a new numbered task**, not a silent addition to the
   current one.
7. **Close stories, not layers.** A story with four of five tasks done has
   delivered nothing.
8. **Timeboxes are real.** When a task blows its box, write a decisions entry
   naming what broke instead of grinding.
9. **No infrastructure the project has not earned.** No GCP setup, no Terraform,
   no containers, no CI, no vector search. (See D4 for why vector search is out.)
10. **Never write a real API key into a tracked file.** Placeholder only.

## Verified environment facts

Established by installing the package and reading the installed API, not from
documentation or memory. **Most ADK material online is 1.x and will mislead
you.** Do not override these from prior knowledge.

- `google-adk` installs at **2.8.0**. Pin exactly: `google-adk==2.8.0`.
- **Python 3.12** is the target. `python3.12` is at `/opt/homebrew/bin/python3.12`.
  The `venv/` in the working tree is **Python 3.12.14** and matches. Rebuilt in
  T-03.
- Top-level API surface: `Agent`, `Context`, `Event`, `Runner`, `Workflow`.
- `Workflow` is a Pydantic model. Its `edges` field is a **static list of edges
  supplied at construction**. Other fields: `retry_config`, `max_concurrency`,
  `state_schema`, `input_schema`, `output_schema`, `timeout`.
- `edges` accepts `dict[bool|int|str, ...]` for conditional routing. **Keying a
  branch off model output violates Article I. Never do this.** Branch keys come
  from deterministic Python values only.
- CLI verbs: `adk web`, `run`, `create`, `eval`, `eval_set`, `test`,
  `conformance`, `migrate`, `api_server`, `deploy`.
- `adk create <name>` writes `__init__.py`, `agent.py`, `.env`, `.gitignore`
  directly into `<name>/`. It does not create an agent subfolder — the move into
  `pa_agent/agent/` was manual. *(D16)*
- `adk web [AGENTS_DIR]` treats each **subdirectory** of `AGENTS_DIR` as one app,
  so `adk web pa_agent` serves the app named `agent`.
- `GET /` on `adk web` returns **307** to `/dev-ui/`, not 200. `GET /list-apps`
  returns 200 with a JSON array of discovered app names. *(D16)*

Per D5: develop against the AI Studio free tier, run final evals and any demo
through Vertex, because Vertex does not train on submitted data.

## Repo layout

```
pa_agent/            package: resolver, criteria, spans, cli
  agent/             adk create writes here
data/policies/
  source/            T-02 lands here
spike/
  spike_001/         T-00: notes/, results.json, run.py
scripts/
tests/
eval/
docs/
```

## Current state

**T-03, T-00, T-34, T-02, T-01 and T-37 are closed.** `python scripts/check_skeleton.py` returns zero:
target layout present, `google-adk` at 2.8.0, `pa_agent.agent` imports with a
reachable `root_agent`, and a spawned `adk web` answers 200 on `/list-apps`
naming the agent before the script terminates it.
`python spike/spike_001/run.py --verify` returns zero and spends no model call.
`pytest tests/test_model_pin.py` and `python scripts/verify_sources.py` return
zero. The latter hits the network; `--offline` skips the re-download and does not
close T-02.

**The policy corpus is three documents and one jurisdiction (D21, D29).**
`data/policies/source/` holds `ncd_100_1` (national) and `a53028` (Noridian
Healthcare Solutions, A/B MAC, **Jurisdiction F**), stored as extracted text —
the MCD emits a fresh CSP nonce per response, so raw HTML has no reproducible
hash. T-40 added `r931cp` (CMS Pub. 100-04 Transmittal 931, CR 5013, 2006), the
claims-processing transmittal that binds procedure names to HCPCS codes — a
static PDF, byte-stable, extracted with pinned `pypdf==6.18.0`. **`r931cp` is
citable for code bindings only, never coverage claims**: its coverage content
predates the 2012 LSG delegation (D29). NCD 100.1 quantifies **nothing**: no
months, no visit counts, no recency.
Every constant in the criteria tree comes from A53028, so this system determines
coverage *as Noridian would*, and a different MAC is a different tree over the
same NCD. Say that plainly in a review rather than calling the thresholds CMS's.

Answered with spans in `answers.json`: c5 documentation is **monthly**; c2's
window is **12 months**, and the same sentence fixes c3's run at **four
consecutive months**.

**The criteria tree exists (D23).** `data/policies/ncd_100_1_jf.json`,
`policy_version_id` `ncd-100.1-jf-v1`. Every sourced constant carries a span the
test slices out of the hashed corpus, and the tree records the corpus hashes so a
moved document fails the gate. Sourced: BMI ≥ 35 inclusive, one comorbidity, c2
12 months, c3 four consecutive months, c4 per-month BMI, c5 both diet and
activity. **Two constants are provisional and must not be defaulted by the task
that consumes them** — criterion (a)'s lookback (question 4, T-13) and
`discrepancy_tolerance` (question 5, T-33).

**c5 is a rate, not a count (D24).** A53028's `monthly` governs the whole
three-item list in the sentence that quantifies c4 and c5, so c5 carries c4's
`documentation_rate` — `every_month_of_run`, same span, sourced rather than
provisional. `c5_min_documented_events` is gone and REQ-37 is rewritten to the
per-month shape. A seven-month run documenting diet and activity in four months
was `MET` under the count and is `NOT_MET` under the rate. c4 and c5 share the
rate and differ in their predicate, so they stay separate criteria. **T-16 builds
against the rate.**

**43775 is not the non-covered case (D22), and T-35 has re-pointed E3 (D28).**
NCD 100.1 non-covers laparoscopic sleeve gastrectomy only *"prior to June 27,
2012"*; after that it is delegated to the MACs, and A53028 records this MAC
covering it. **E3 and T-25 are now 43842, open vertical banded gastroplasty**,
which the NCD names non-covered for all beneficiaries with no date qualifier and
no delegation clause. 43775 belongs in a covered case; T-38 lands it in the
contractor-determined set. **T-36 answered: it is a third sc1 outcome** (D33,
REQ-42).

**A procedure code carries two citations, and since T-40 both are sourced (D28,
D29).** "This procedure is non-covered" and "this code denotes that procedure"
remain different claims with different sources: E3's `coverage_claim` spans
`ncd_100_1`, and its `code_binding` now spans `r931cp[15038:15092]` — "Open
vertical banded gastroplasty ( HCPCS code 43842)" — with `in_corpus: true`.
Open question 7 is closed. The two classes stay separate in every artifact;
merging them into one `source` field is still the move D28 refused. If D29
reverses (the PDF stops re-downloading to its hash, or a binding is disproved),
the affected bindings fall back to `in_corpus: false` and the D28 posture.

**One module names the model (D20).** `pa_agent/model_pin.py` pins
`gemini-3.5-flash-lite` — the model D19 was measured on — and it is the only
tracked Python file allowed to write a model identifier. Everything else imports
`PINNED_MODEL`. T-34's test asserts a bare `python spike/spike_001/run.py` would
measure on the model `results.json` records, and scans tracked Python for stray
literals. **D19's numbers are AI Studio numbers**; a Vertex run of the same
corpus is a new measurement, not a confirmation.

**D2 survives first contact (D19).** Three complete runs of five hand-labeled
notes on `gemini-3.5-flash-lite` at temperature 0: event precision 1.000, recall
1.000, REQ-9 exclusion 51/51, identical event dates on every note across runs.
Read D19's caveats before quoting any of that — 30 of the 51 traps are merely
unrelated sections, so **21/21 is the honest number** against the kill criterion,
and the corpus is five notes Troy wrote scored against labels Troy wrote.

**The model cannot produce character offsets.** 0 of 80 emitted
`char_start`/`char_end` pairs were usable, even compared modulo whitespace.
Spans are located by searching for the model's verbatim quote — exact first,
then whitespace-normalized, always recording raw offsets (D18). T-15 has to
build this; it is not optional machinery, and D17's plan to take the model's
offsets directly is dead.

Outside the spike the skeleton is still empty scaffolding.
`pa_agent/agent/agent.py` remains the `adk create` template — a generic
assistant, not any part of the design, though it now reads the pin instead of a
literal. No criteria tree, no schemas, no policy data, and the only test is
T-34's.

**The end state is a production deployment against two databases (D25)** — one
holding insurance codes, one holding patient data. That target is now a
constraint on code being written, not a someday: T-09 defines `PolicyStore` and
`PatientStore` as two Protocols with file-backed local implementations, and
REQ-41 forbids any module outside an adapter from opening a file path or holding
a connection. Production becomes a second adapter. **Criteria trees do not move
into a database write path** — Article VII wants a diff and a reviewer for every
clinical rule change, so git stays the source of truth and a store may serve a
deploy-time read-only projection. The database fixes lookup scaling and does not
touch the real wall: c1–c5 are hand-written Python for one policy's shape, so
policy #2 costs a developer. A predicate DSL is the answer to that and is
deliberately not open.

**T-38 is closed: the tree carries three procedure sets, and REQ-2 reads
membership (D26, D30).** `covered_procedures` is gone from the spec. Members
are procedures in D28's two-citation shape; ten identity bindings, all
`in_corpus: true` (seven CPT from `r931cp`, 43775 and 0DV64CZ from `a53028`),
pairwise disjoint across sets. **A53028's facility ICD-10-PCS lists are spanned
transcriptions marked `identity: false` and are not lookup keys** — they
overlap across procedures in the source itself (0D160ZB in two lists; 0DV64CZ
and 0DB64Z3 inside the lap Roux-en-Y list while the article assigns them to
LSG), so a facility code does not denote one procedure. Do not promote them.
The contractor-determined set records a corpus fact; its resolver outcome is
`ResolvedByContractor` since T-36 closed (D33). Mutation-tested eleven ways in
the T-38 close.

**T-09 is closed.** `pytest tests/test_schemas.py` returns zero.
`pa_agent/contracts.py` holds the models; `pa_agent/stores/policy.py` and
`pa_agent/stores/patient.py` hold the two ports and their file-backed adapters.
`pa_agent/stores/__init__.py` imports neither submodule on purpose — a
package-level re-export would be the module that reaches both planes. Three
invariants are validators, not conventions: `Document` verifies its hash on
construction, `EvidenceSpan` refuses reversed and negative offsets, and
`CriterionResult` refuses a `MET`/`NOT_MET` without a span *and* an
`INSUFFICIENT_EVIDENCE` with one. `Criterion.require()` raises on a provisional
constant rather than returning `None`.

**Both unimplemented halves raise and name their task.**
`LocalPolicyStore.resolve` raises `NotImplementedError` citing T-38 — the tree
has no procedure sets to read. Every `LocalPatientStore` method raises citing
T-04. Do not "fix" either by returning `None` or `[]`: the first reports
`NO_POLICY_FOUND` for all of Medicare, the second manufactures E7 for every
patient, and in both cases the downstream tests agree with it.

`CriterionVerdict` has two of Article IV's three states. **`ERROR` is T-26's**,
and `tests/test_schemas.py` asserts its absence so adding it without the
classifying enum and the determination validator fails loudly.

**T-10 is closed, and the eval gate is a baseline diff (D27).**
`python eval/run_eval.py` returns zero — it reports `BLOCKED` on E3 and matches
`eval/baseline.json`. The exit code means *observed matches the baseline*, never
*every case passed*: drift in **either** direction fails, so a case that starts
passing has to be acknowledged with `--update-baseline` and a commit. That is
what makes US-1's close a command rather than a table someone reads.

Three case statuses, and **`BLOCKED` is not `FAIL`** — "answered wrongly" and
"the component does not exist yet" have different next actions, and only one
names a task. Blocking is *discovered* from the `NotImplementedError` the system
raises, never declared on the case. The scorer runs seven self-checks before
scoring anything, because every scoring branch is unreachable by a real case
until T-25 produces a `Determination`; a self-check failure exits 2 and
suppresses the report.

**E3 now carries 43842 and reports `BLOCKED/NOT_IMPLEMENTED`** — it reaches
`LocalPolicyStore.resolve` and gets the `NotImplementedError` citing T-38. That
move was the first real exercise of D27's gate: the drift failed the run, and
`--update-baseline` plus a commit is what recorded it. The eval set holds E3
alone; the rest of spec §6 is **T-21**.

**T-35 is closed.** `pytest tests/test_e3_code.py` and `python eval/run_eval.py`
both return zero. Its exit condition was rewritten by D28 for two defects in its
own text: it asked for a *code* spanned to `ncd_100_1`, which no document can
supply, and it closed on `tests/test_resolver.py`, a file **T-24** creates — so
T-35 could not close until the thing it blocks was built, which is the defect D26
named in T-36. Ten mutations, each caught by the check that should catch it; the
sharpest is citing 43775's own bullet, which slices back, is unique, and sits
inside the non-covered list — only the date-qualifier assertion catches it.

Active task: **none. Pick the next one before writing code.**

T-15 is **not** unblocked: it depends on T-00, T-07 and T-11, and only T-00 is
closed. T-11 sits behind T-08. The ready set is now **T-06, T-13, T-26 and
T-31**. US-2 now needs only T-13 — whose dependencies are all closed but
which sits behind **open question 4, criterion (a)'s lookback window, which
no task may answer by default**: it is Troy's judgment or a new source, never
a hardcoded number. **T-24 is closed (D31): facts in the store, judgment in
`pa_agent/resolver.py`.** `resolve()` returns an extended `PolicyRef` (set
membership + spanned claim) or `None`; `resolve_sc1` maps non-covered to
`NotCovered`, absence to `NoPolicyFound`, and — since T-36 closed —
contractor-determined to **`ResolvedByContractor`** (D33, REQ-42): distinct in
type from `Resolved`, identical in flow, because this corpus's MAC exercised
the delegation and covers the procedure. Downstream, both proceed-to-tree
types raise citing T-19, and T-19's exit now obliges a contractor
determination to cite the delegation *and* the MAC's exercise, never the NCD
alone. The contracts carry typed procedure sets whose validators refuse an
unsourced identity binding and a code bound in two sets.

**T-25 is closed (D32), and with it US-1 — the first delivered story.**
`python -m pa_agent.cli --patient X --procedure 43842` prints a `NOT_COVERED`
determination citing its denial with spans, version id recorded, zero model
calls; `python eval/run_eval.py` reports E3 `PASS` against the updated
baseline. Three shapes D32 fixed: `Determination.patient_id` is `None` when no
patient was consulted (refused alongside criterion results),
`Determination.coverage_claim` carries the denial's citation (valid only with
`NOT_COVERED`; sc2's citation shape is **T-14's**, not this field's), and
`NO_POLICY_FOUND` is `NoPolicyResult`, deliberately not a `Determination`
because REQ-4's version id cannot exist for it. `pa_agent/cli.py` is the one
place a store is constructed (REQ-41). `tests/test_determination.py` exists
and is the file **T-19's exit names** — T-19 extends it, and covered codes
currently raise citing T-19. The harness scorer runs eight self-checks; the
eighth pins that an ungoverned code under an outcome expectation is `FAIL`,
never a coerced denial.
**T-40 is closed** — `r931cp` is in the corpus and every binding T-38 writes
can carry an in-corpus span; `python scripts/verify_sources.py` covers all three
documents.

**T-04 is closed (D35), and the patient corpus exists.** Six Synthea v4.0.0
bundles (tagged release jar, pinned by version and measured sha256; seed 1001,
200 patients, ages 30–60, Washington — inside Jurisdiction F) sit in
`data/patients/bundles/` with `data/patients/manifest.json` recording
provenance and per-bundle hashes. Most-recent BMIs span 34.26–42.5; the sub-35
patient carries active T2DM, E2's shape. `python scripts/select_patients.py
--verify` re-reads the disk only — regeneration needs `--generate`, Java and
network, and is never part of the gate. `LocalPatientStore` still raises, now
citing **T-12**: the bundles exist but the FHIR reads do not, and the message
deliberately no longer contains "T-04" (D31's stale-substring lesson).

**T-05 is closed (D36), and the value set speaks SNOMED.**
`data/policies/value_sets/obesity_comorbidities.json` holds two entries — T2DM
(44054006 → E11.9) and essential hypertension (59621000 → I10) — each verified
to appear as an active Condition in a committed bundle, each anchored by an
in-corpus span into A53028's Group 1 naming code and condition together, and
each admitting the SNOMED-to-ICD-10 hop is unsourced (`in_corpus: false`,
D28's posture — the NLM map sits behind UMLS licensing). Growth happens as a
reviewed diff when a manifest needs an entry, never as a predicate
special-case. **Value sets live under `value_sets/`**: the policy store globs
`data/policies/*.json` as criteria trees and errors on anything else.

**T-08 is closed (D37), and the index is deliberately dumb.**
`pa_agent/index.py` resolves `document_id` to text and slices `EvidenceSpan`s
mechanically; REQ-7 is an exception there (rebinding an id to different
content raises), and the module imports only the contracts — no store, no
file, no model, asserted on its AST. It never judges a slice: fabricated and
off-by-one rejection is T-11's. Both planes instantiate the same class and no
instance holds both planes' documents.

**T-11 is closed (D38), and rejection is a classified exception.**
`pa_agent/spans.py` validates spans against a `DocumentIndex`: the verified
raw slice, or `SpanValidationError` carrying `UNKNOWN_DOCUMENT`,
`OUT_OF_RANGE` or `QUOTE_MISMATCH` — the closed reasons REQ-30's
`SPAN_VALIDATION_FAILED` and T-29's fault injection will assert against. The
quote predicate is D18's whitespace-collapsed exact equality; a similarity
knob is refused in writing and by test. Mapping a rejection to a verdict is
deliberately not this module's job.

**T-12 is closed (D39), and the patient plane serves verified facts.**
`LocalPatientStore` resolves patients through T-04's manifest, hash-verifies
each bundle before parsing (REQ-7), and reports everything: all quantitative
observations, all coded conditions with `clinical_status` carried on the
widened `Condition` contract. Filtering is the predicates' judgment — an
active-only or BMI-only read inside the adapter is refused by test.
`get_notes` raises citing **T-07**; Synthea's auto-generated notes are not
ground truth and must never be served as the note corpus.

**T-39 is closed (D34).** Spec §9 states each question's status by subsection
— `### Still open` versus `### Resolved` — and `_question_statuses()` in
`tests/test_criteria_tree.py` reads only that stated split, refusing a missing
heading, a doubled number, or one floating under neither. A provisional
constant citing a resolved question now fails the gate, so T-13 and T-33 can
close questions 4 and 5 and be graded honestly. `~~` markup in the spec is
styling, not status.
