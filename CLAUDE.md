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
- `edges` is `list[EdgeItem]` where `EdgeItem = Edge | tuple[ChainElement, ...]`
  and a `RoutingMap` inside `ChainElement` carries conditional routing (*corrected
  in D62; the earlier note said `dict[bool|int|str, ...]`*). **Keying a branch off
  model output violates Article I. Never do this.** Branch keys come from
  deterministic Python values only.
- `SequentialAgent`, `ParallelAgent` and `LoopAgent` are all **deprecated** in
  2.8.0 in favour of `Workflow`. `LlmAgent` is itself a `BaseNode`.
- `output_schema` and `tools` work **together**, but natively only on Vertex:
  `models/_capabilities.py` gates `output_schema_and_tools` on the Vertex
  variant, so on AI Studio ADK injects a `SetModelResponseTool` and an extra
  instruction instead. **The tier changes the prompt, not just the endpoint** (D62).
- `LlmAgent(model=<BaseLlm instance>)` runs the whole flow — tool calls,
  `output_schema`, plugin hooks, `usage_metadata` — with **no network and no
  credential**. That is how `tests/test_adk_agent.py` tests ADK rather than a
  mock of it. Do not import `google.adk.cli.agent_test_runner`; copy the pattern.
- Setting `disallow_transfer_to_parent` and `disallow_transfer_to_peers` with no
  `sub_agents` makes `_llm_flow` select `SingleFlow`, so `transfer_to_agent` is
  never injected. `RunConfig(max_llm_calls=N)` is the only budget knob and counts
  LLM calls, so one tool round trip costs two.
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
activity. **One constant is provisional and must not be defaulted by the task
that consumes it** — `discrepancy_tolerance` (question 5, T-33). Criterion
(a)'s lookback resolved to **12 months by Troy's decision** (D40, question 4
closed), carried as a note and never a span.

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

**T-43 is closed (D49), and the declared environment equals the installed one.**
`requirements.txt` claimed pydantic 2.12.3 and pytest 8.4.2 while the venv held
2.13.5 and 9.1.1, and `google-genai` — the SDK issuing every model call the
project has measured — was undeclared, arriving transitively through
`google-adk`. **The pins move up to the installed set**, because that is the
environment every recorded number in this repo was produced by; the file is
what drifted. `python scripts/check_env.py` returns zero: it parses every
tracked `.py` with `ast`, maps third-party imports to distributions, and fails
on an undeclared import, a stale pin, a range instead of an exact pin, or an
import it cannot map. Mutation-tested five ways. It compares file to
environment and **cannot tell you the environment is correct** — model
provenance still rests on `PINNED_MODEL`.

**T-60 is closed (D50), and the note's current BMI is a fact of its own.**
Discovered in T-33: E10b's BMI sits in a standalone `Measured in clinic today`
line, its patient has no encounters *by design* (it is also E8), and both sub-35
patients in the population are deliberately encounter-free — so **no fixture
could host the case** and nothing in the system could reach the value REQ-34
needs. `Extraction` gained `current_bmi` and `current_bmi_quote`, anchored like
any other claim and with no `prefer_near`, because it belongs to no encounter.
A note-level BMI is a **different fact** from `WmEvent.bmi`: criterion (a) asks
what the patient's BMI is now, c4 asks what each month of a run documented, and
conflating them was the real modelling error.

**The widened schema held every number** — a new measurement per D45's rule, not
a re-run: precision 1.000, recall 1.000, REQ-9 exclusion 11/11, field agreement
1.000, 171/171 spans anchored, 0 model offsets usable. Only E10b's note yields a
value; the ten others record `None`, including every note carrying per-encounter
BMIs, so nothing was borrowed upward. E8 still yields zero events. Mutation-tested
four ways, and `--rescore` reproduces the aggregate exactly.

**T-33 is closed (D51), open question 5 is answered, and the tree carries no
provisional constant.** `discrepancy_tolerance` is **1.0 BMI points by Troy's
decision** — recorded like D40's lookback, a judgment with a name and a date,
never a span. The eval set does not constrain it: E10's gap is 5.77 and E10c's
is 0.05, so 0.5, 1.0 and 2.0 all pass every case, and saying so is the point.

`pa_agent/reconcile.py` implements REQ-34 and REQ-39 over **real committed
data** — criterion (a) on the FHIR bundles, the note side from the recording.
Opposite sides of 35.0 resolve `INSUFFICIENT_EVIDENCE`/`SOURCE_CONFLICT` with
**no spans** (an abstention cites nothing) and magnitude is irrelevant there;
same-side disagreements leave the verdict alone and record an advisory
`Discrepancy` only at or beyond tolerance. E10b is a genuine **downgrade** —
(a) alone answers `NOT_MET` — which is what makes it test REQ-34's second
branch rather than an abstention that was going to happen anyway.

`reconciled_facts` is now a typed `ReconciledFact` whose tolerance is a
`PolicyConstant`, so `require()`-style guards reach it; the BMI selection is
extracted to `criteria.most_recent_bmi` so reconciliation and criterion (a)
cannot drift about which value is authoritative. **Two guards in
`tests/test_criteria_tree.py` were retired deliberately** — they asserted a
provisional constant and an open question still existed — and replaced by a
count pinned at zero, so a new one is a visible diff. Mutation-tested six ways.
Spec §6's BMI figures were corrected to the committed data (E10b 34.6, E10c
37.65/37.6); they were illustrative numbers written in T-01 before any patient
existed, and nothing read them.

**T-46 is closed (D52), and the value set reaches criterion (b) through the
port.** T-13 left "which port serves it at runtime" explicitly to T-18; until
now the only readers were two test files, by path. `PolicyStore.get_value_set`
returns a `frozenset[str]` of **SNOMED** codes — the system `Condition.code`
actually carries — and the test proves it by intersecting the set with the
committed bundles. An ICD-10 set would load cleanly, compare cleanly and match
nobody, so criterion (b) would abstain for every patient and every downstream
test would agree with it; the mutation that swaps them is caught. An unknown id
raises rather than returning an empty set, and a file whose `value_set_id` no
longer matches its path raises.

Active task: **none. T-61 is next — see the plan below.**

**T-18, T-19, T-20, T-26 and T-62 are closed (D62). The system answers.**
`python -m pa_agent.cli --patient <uuid> --procedure 43775` prints a real
determination — seven criterion verdicts, spans that slice back, a gap list, and
Article X's counters — for **zero model calls**, because the default extraction
runner replays T-15's recording.

**The seam is one port.** `pa_agent/runners.py` declares `ExtractionRunner`
(REQ-52), and three things satisfy it: `DirectExtractionRunner` (raw
`google-genai`, D45's measured configuration), `AdkExtractionRunner`
(`google-adk` 2.8.0, in `pa_agent/agent/`), and `RecordedExtractionRunner`
(replays `notes[].raw`, spends nothing). **Every one returns through
`build_result()`, which is the trust boundary** — ADK output is untrusted model
output and there is no private route to a `WmEvent`. The recorded runner is why
`pytest` and `eval/run_eval.py` evaluate the whole chain end to end for free.

**The graph is plain Python and deliberately not an ADK `Workflow`.**
`pa_agent/workflow.py` holds `STEPS`, a module-level tuple of nine named
callables, and a driver that walks it and records what it visited. An ADK
`Workflow` would put `google.adk` on the import path of every deterministic
test, and the three `sys.modules` assertions that would catch that are the ones
that would have to be deleted to allow it. The graph has one conditional —
whether a short circuit fired — and that is a `return`, not an edge.

**Article I is asserted structurally, not promised.** The driver's body contains
no branch on model output; every loop iterable is enumerated in the gate
(`STEPS`, `tree.criteria`, `range(max_attempts)`, `state.notes`); the count of
branches anywhere in `workflow.py` that read an extraction field is **pinned at
one** — the note-level BMI merge, which routes nothing — so a second is a visible
diff. On the agent side: `SingleFlow` (so `transfer_to_agent` is never injected),
`include_contents="none"`, a literal tool list, and a `max_llm_calls` ceiling.

**The extraction agent's allowlist is narrower than its toolset, and that is the
sharpest decision in T-62.** It gets `get_patient_document` and
`get_patient_notes`. It is **not** given `get_patient_observations` — handing the
model the structured BMI while asking it for the note's is how T-33's and T-60's
two independent readings stop being two, and E10b would quietly start agreeing
while every test kept passing. `patient_tools.py` and `policy_tools.py` are
separate modules and nothing imports both (Art. VI, REQ-53). **The policy tools
have no model consumer today** — they are the declared surface T-61 will hand its
adjudicator.

**Nothing may quote D45's numbers for the ADK path.** It is a different SDK, and
under `--tool-fetch` a different prompt. `scripts/run_adk_extraction.py` is the
measurement and **T-63 is open**; it reuses `run_extraction.py`'s corpus and
scorer by import, so the runner is the only difference.

**Next is T-61**, and its runway is clear: T-18, T-19, T-20, T-26 and T-62 all
closed this pass, and T-26 was added to its `Depends` because its exit needs an
`ERROR` state that did not exist. Amendment 1 is what makes T-61 legal — D62
restored Articles I and II after they had been rewritten in place, and kept the
amendment appended and scoped instead, so the deterministic path stays bound by
the articles and therefore stays an oracle.

**US-4 and US-5 have not closed.** Their closing conditions are E4–E11 and E1/E8
passing *in the harness*, and `eval/cases.json` still holds one case. That is
**T-21**. A determination that works and an eval set that grades it are two
deliverables and only the first landed.

**US-2 and US-3 are delivered**, and c1–c5 now evaluate correctly on every edge
case. **T-24 is closed (D31): facts in the store, judgment in
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

**T-13 is closed (D40), US-2 is delivered, and question 4 is answered.**
The lookback is **12 months by Troy's decision** — analogy to the
program-participation window, a note and never a span. `pa_agent/criteria.py`
holds (a) and (b): boundary inclusive, stale BMI `NOT_MET` per REQ-16, empty
chart abstains; (b) is `MET` or abstention, **never `NOT_MET`** — a chart
cannot prove a comorbidity absent. Structured claims cite the bundle document
itself: `PatientStore` grew `get_document`, the adapter computes each
resource's exact extent in the raw text, and every produced span validates
through T-11. `Observation`/`Condition` carry an optional `span`; the value
set reaches (b) as a parameter, and which port serves it at runtime is
T-18's wiring question, deliberately open.

**T-14 is closed (D41), US-3 is delivered, and sc2 is a spanned exclusion.**
The NCD body never states the T2DM/BMI<35 exclusion — the transmittal
history's 04/2009 sentence does, and the tree's new `categorical_exclusions`
spans it. Its `bmi_upper_bound` is the one numeric constant legitimately
sourced to `ncd_100_1` (a national exclusion CMS quantified itself), gated
separately from D21's Noridian rule. sc2 fires only on an in-window BMI
(criterion (a)'s lookback, borrowed), only for the nationally covered set
(the sentence predates the LSG delegation), and only when both sides are
citable: `coverage_claim` carries the rule, the new
`Determination.exclusion_evidence` spans the patient's BMI and T2DM in the
bundle document. Spanless facts deny nobody. CLI exit 1 is now a bad request
(unknown patient), distinct from 0 (answer) and 2 (unbuilt path).

**T-06 is closed (D42): the ground truth exists, and it is self-authored.**
`eval/manifests/` holds six manifests, `as_of` pinned to 2026-09-01, recording
facts and **never expected verdicts** — labels are T-21's. They live in
`eval/` because the system under test must never read them (D27's line).
Case-to-patient assignment follows the committed structured data: Felipe is
the only possible E1. Eleven §6 cases covered; E3 needs no patient, and
**E12 has none possible — that is T-41**, which blocks A1 but not US-2, since
the boundary is pinned at the criterion level today. Do **not** close T-41 by
giving a patient a note BMI of 35.0: criterion (a) reads structured data, so
that tests reconciliation under E12's name.

**The ground truth was authored by the agent building the system it grades.**
D19's caveat about the spike corpus applies here with a different author —
structural mitigations (cross-checks against the bundles, T-07's honoring
assertions, review of the diff) are in place, and a perfect score still means
only that the approach does not obviously fail.

**T-07 is closed (D43): the note corpus exists and no model wrote it.**
`scripts/synthesize_notes.py` renders six charts from T-06's manifests by
seeded templating — a model-written corpus would make T-15 measure
model-to-model agreement. The **78-column wrap is load-bearing**, not
cosmetic: D18's anchoring exists because quotes cross wrap points, and this
corpus genuinely splits `BMI 42.7` across a line break (the gate had to
normalize to see it). **No note contains a date the manifest does not
declare** — that assertion is what makes the corpus ground truth, since an
invented date would be scored as a model failure that was really a corpus
defect. Traps never name their own type. `get_notes` serves the notes
hash-verified and the T-07 raise is retired.

**T-31 is closed (D44): an abstention must say what to go collect.**
`GapReason` is a closed four-member enum, and a `CriterionResult` validator
requires it on `INSUFFICIENT_EVIDENCE` and refuses it everywhere else — the
mirror of REQ-5's span rule, so a result is either evidence or an explanation
of its absence. It propagates onto `GapEntry` and survives serialization.
Criteria (a) and (b) carry `NO_EVIDENCE_RETRIEVED`. **T-16, T-17, T-19 and
T-33 must supply a reason at every abstention branch** — the validator makes
that mechanical rather than remembered.

**T-15 is closed (D45, D46, D47), and the model is in the system.**
`pa_agent/extraction.py` is the one model leaf — a declared leaf that routes
nothing (Art. I). It carries spike 001's prompt essentially verbatim so D19's
result keeps meaning something, widened by REQ-38's per-field spans and the
BMI as a value; that widening makes it **a new measurement, not D19's re-run**.
Measured twice over 11 notes: precision 1.000, recall 1.000, REQ-9 exclusion
11/11, field agreement 1.000, **0 model-emitted offsets usable**.

**Two anchoring defects were found by running it, not by review.** Seven
per-field spans cited the *wrong encounter* while passing T-11 — a repeated
`BMI 37.6` anchoring to the first month — so `pa_agent/anchor.py` now
disambiguates by proximity to the event, and T-15's exit gained the clause
T-11 structurally cannot check. And the model double-escaped newlines on one
run, which cost six encounters until the quote is unescaped before matching.
**Both were verified by `--rescore` for zero model calls**; D18 built that
path for exactly this.

**Measurement and gate are separate:** `scripts/run_extraction.py` spends the
calls, `pytest` re-reads the recording, re-hashes every note, re-validates
every span through T-11, and checks the recorded model is the pin. Do not make
the gate call a model. Both anchoring repairs also carry direct synthetic
tests, because their triggers are intermittent and a recording may not
exercise them.

**T-16 is closed (D48): c1–c5 evaluate, and seven edge cases pass on real
extracted events.** c3 computes the qualifying run once; c2, c4 and c5 scope
to it (the tree's `scoped_to: "c3"`). A zero-event abstention reads
`program_assertions` for its reason — with a claim it is
`UNSUBSTANTIATED_ASSERTION` (E8), without one `NO_EVIDENCE_RETRIEVED` (E7),
D12's rule at both c1 and c3. Every `NOT_MET` cites the evidence that fell
short, never the absence.

**Three open items discovered while building this pass.** **T-63** measures the
ADK runner against the direct one (spends calls, in no gate). **T-64** is one
document namespace over the patient plane — `PatientStore.get_document` resolves
bundle filenames only, so validating a determination's spans means reading through
`get_notes` too, and T-17's verifier will hit it first because it receives a span
and no patient id. And three `sys.modules` guards in `test_schemas.py`,
`test_reconciliation.py` and `test_error_state.py` were **order-dependent** — they
asserted "nothing in this process loaded the ADK", not "this module does not" — and
are now fresh-interpreter subprocess probes, the shape `test_resolver.py` had
already written down (D62).

**T-42 is registered, not fixed:** REQ-14 picks the *longest* run and c2 then
tests that run's recency, so a long stale run beats a short recent one and the
patient reads stale. Implemented as written and pinned by
`test_the_longest_run_wins_even_when_an_older_one_is_stale` — if that test
starts passing differently, REQ-14's selection changed and it needs T-42's
decision entry, not a quiet fix.

*Method note:* when mutation-testing, **clear `__pycache__` after restoring** —
a same-length mutation restored within the same second leaves Python's
bytecode cache looking valid, and a "passing" suite can be running the mutant.

**T-39 is closed (D34).** Spec §9 states each question's status by subsection
— `### Still open` versus `### Resolved` — and `_question_statuses()` in
`tests/test_criteria_tree.py` reads only that stated split, refusing a missing
heading, a doubled number, or one floating under neither. A provisional
constant citing a resolved question now fails the gate, so T-13 and T-33 can
close questions 4 and 5 and be graded honestly. `~~` markup in the spec is
styling, not status.
