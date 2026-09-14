# Prior Authorization Determination Agent

A prior authorization determination system built to work across doctors'
practices. Given a patient record and a requested procedure code, it produces
a reviewable determination: a verdict for every criterion in the governing
policy, a citation for every verdict, and a gap list naming exactly what the
chart is missing. The control case it is currently tested against is
bariatric surgery under CMS NCD 100.1, as Noridian Jurisdiction F implements
it — one policy chosen to exercise every part of the engine, not a boundary
of the design.

The system does not submit, does not decide, and does not adjudicate on a
payer's behalf. It prepares a packet for a human specialist — and the primary
output is the **gap list**, not the verdict. The highest-value sentence the
system produces is "criterion c3 is not supported by this chart," because that
is actionable *before* submission. An overall approve/deny is a summary of the
criterion verdicts and carries less information than they do.

**The rules engine is dynamic — the policy is data, not code.** Nothing in
the engine knows it is adjudicating bariatric surgery. The criteria tree
lives in a reviewed JSON file, the aggregator parses the policy's own
`decision_expression` rather than hardcoding one, and the resolver maps
procedure codes to policies by set membership. A new specialty, payer policy,
or jurisdiction is therefore a new policy file and value set over the same
engine — a change to data under review, not a change to Python. The one tree
currently loaded is the control; *Where this system degrades* below is
explicit that a second jurisdiction is a second tree, and that the design
makes adding one a small change to build.

**And it is modular enough to sit inside a practice's back office.** The
application is ports and adapters end to end: two storage ports keep the
policy plane and the patient plane apart, three ports at the model boundary
make every model call swappable and replayable, and the CLI is the single
place an adapter is constructed — nothing else in the system knows where its
data comes from. Patient records are standard FHIR bundles, so integrating
with a production EHR or practice-management system is a second adapter
behind the same ports, with no engine change. The output already matches the
back-office workflow: the gap list tells staff what to chase down before a
request goes out, and the packet gives the reviewing specialist
criterion-level verdicts whose citations slice back into the chart.

This is a proof-of-skill project. Every decision in it is logged, numbered, and
meant to be defensible in a live review, so the reasoning is as much the
deliverable as the code.

```bash
./venv/bin/python -m pa_agent.cli --patient 49092fd9-d5bf-24e2-474b-00041a279a47 --procedure 43775
```

That command prints a real determination — seven criterion verdicts, evidence
spans that slice back into the source note, a gap list, and per-run cost
counters — for **zero model calls**, because the default extraction runner
replays a committed recording.

---

## The problem this design answers

Agent systems fail in production three ways: hallucinated routing, infinite
loops, and runaway cost — and all three are downstream of letting a model
steer. Clinical determinations add a fourth failure: a citation that sounds
right but doesn't exist.

This project's answer is a written constitution — ten articles that bind every
task and are never revisited per-task. The ones that shape the architecture
most:

| Article | Constraint |
|---|---|
| **I** | No model decides control flow. The execution graph is fixed Python; models are invoked at declared leaves only. |
| **II** | No model performs a deterministic computation. Dates, thresholds, counting, set membership, and booleans are code. A model extracts and structures; it does not calculate. |
| **III** | Every claim carries `(document_id, char_start, char_end)`, and Python slices the source at those offsets before acceptance. A fabricated citation fails on string comparison, not on judgment. |
| **IV** | Fail closed, and `INSUFFICIENT_EVIDENCE` is a success state. Evidence that exists but falls outside a required window is `NOT_MET`; evidence that cannot be found is `INSUFFICIENT_EVIDENCE`; a fault is `ERROR`. The three never collapse, because all three read as "not approved" and have entirely different next actions. |
| **V** | The verifier is blind. It receives the criterion, the cited span, and the claimed verdict — never the adjudicator's reasoning, other criteria, or the rest of the chart. A verifier that sees the reasoning ratifies it. |
| **VI** | Planes do not cross. The policy plane has no read access to patient data; the patient plane holds no policy corpus. The only object crossing the boundary is a compiled criterion. |
| **VII** | Policy is a file under review. Criteria trees live in the repo; a clinical rule changes only as a diff with a reviewer. |
| **VIII** | No task closes without a command that returns zero. |
| **IX** | Decisions are logged in `docs/decisions.md` *before* the code they justify, naming the rejected alternative and the condition that would reverse the choice. |
| **X** | Cost and latency are measured per run from the first model call, never estimated. Numbers in this README come from instrumentation. |

**Amendment 1** opens a second, separately measured *agentic* path where the
model may direct retrieval and (in principle) adjudicate — while deterministic
Python remains authoritative for validation, date arithmetic, comparisons,
counting, span checking, budgets, and output shape, on both paths. The
deterministic implementation is the regression oracle; the agentic path is
evaluated against it on identical cases, and a disagreement is reported, never
reconciled.

---

## What a determination looks like

**Two short circuits, then a fixed graph.**

1. **sc1 — procedure resolution.** Answered from procedure-set membership
   alone: `NotCovered`, `NoPolicyFound`, `ResolvedByContractor`, or `Resolved`
   — four types, never one type with a flag. Zero model calls.
2. **sc2 — the national exclusion.** Bariatric surgery for T2DM with BMI below
   35 is nationally non-covered; this is computed from structured FHIR. Zero
   model calls.
3. **The workflow.** Anything surviving both enters `pa_agent/workflow.py` — a
   module-level tuple of eight named steps walked by a plain-Python driver:
   *gather → extract → criterion_a → reconcile → criterion_b → qualifying_run
   → criteria_c → verify*. The single conditional in the graph is whether a
   short circuit fired, and that is a `return`, not an edge.

**Seven criteria**, all sourced from the policy's own criteria tree:

| id | Question | Reads |
|---|---|---|
| a | BMI at or above the coverage threshold | structured FHIR observations |
| b | At least one obesity-related co-morbidity | structured FHIR conditions × SNOMED value set |
| c1 | A physician-supervised weight management program is documented | extracted note events |
| c2 | The qualifying run is recent enough | extracted note events |
| c3 | The program ran for a minimum number of consecutive months | extracted note events |
| c4 | BMI documented in every month of the qualifying run | extracted note events |
| c5 | Diet and activity documented across the qualifying run | extracted note events |

c3 computes the qualifying run **once**; c2, c4, and c5 scope to it. A
reconciliation step then cross-checks the structured BMI against the
note's BMI — two independent readings, kept independent on purpose. The final
verdict is produced by parsing the policy's own `decision_expression`
(`a AND b AND c1 AND c2 AND c3 AND c4 AND c5`) — parsed, never `eval()`'d, and
never hardcoded, so a different policy is a different file, not a code change.

**Every cited verdict then passes the blind verifier** (Article V). The first
rejection converts the verdict to `INSUFFICIENT_EVIDENCE` with reason
`VERIFIER_REJECTED` — no retry, and the determination still emits. The verifier
is barred from date and count arithmetic outright: measurement showed it
false-rejecting every shortfall-type `NOT_MET`, because the shortfall is
Article II's arithmetic over a chart the verifier deliberately cannot see.

**Evidence is mechanical throughout.** Model-reported character offsets proved
unusable (0 of 80 correct in the spike; 0 of 171 in the first full-corpus run), so
spans are located by searching for the model's verbatim quote — exact match
first, then whitespace-normalized — always recording raw offsets. Locating
(`anchor.py`), resolving (`index.py`), and validating (`spans.py`) are separate
modules so a locator cannot launder its bugs through the validator.

---

## How the policy file drives the engine

The criteria tree (`data/policies/ncd_100_1_jf.json`) is not configuration
*about* the code — it is the policy itself, and the engine is deliberately
split so that policy content is data while clinical logic is reviewed code.
This is not a generic rules interpreter; it is a fixed vocabulary of
deterministic predicates, parameterized and combined by the JSON.

**What the JSON decides** (change the file, and behavior changes with no code
change):

- **Every quantified constant.** The BMI threshold (≥ 35.0), the four
  consecutive months, the 12-month recency and lookback windows, the 1.0-point
  discrepancy tolerance. The workflow pulls each one at runtime with
  `criterion.require("min_consecutive_months")` — *required*, never defaulted,
  so a tree missing a constant fails loudly instead of silently evaluating
  with "no preference."
- **The boolean rule.** `decision_expression`
  (`a AND b AND c1 AND c2 AND c3 AND c4 AND c5`) is parsed by a real
  recursive-descent parser — parentheses, `AND`-over-`OR` precedence,
  three-valued truth tables in which `INSUFFICIENT_EVIDENCE` propagates rather
  than collapsing to false. It is not hardcoded as `all(...)`, precisely
  because a second jurisdiction's "one comorbidity *or* a documented attempt"
  would be silently ignored by that shortcut while every existing test kept
  passing.
- **Procedure-set membership.** Which codes are nationally covered, nationally
  non-covered, or contractor-determined is data in the tree; the store builds
  a code→set index across every loaded tree and refuses a code bound twice.
  That 43842 short-circuits to a denial and 43775 proceeds to the criteria is
  the JSON's doing, not the code's.
- **A citation for every constant**, so the tree is auditable line by line
  against its source documents.

**What stays in Python** (fixed, and auditable as code): the predicate
implementations — what "consecutive months" or "documented" *means* — the
workflow's step order, reconciliation, all date and count arithmetic
(Article II), and span validation.

**The envelope for a new jurisdiction.** A second MAC's tree — different
constants, different code bindings, a different boolean structure including
`OR`s — evaluates with zero code changes, as long as its criterion ids map to
implemented predicates and its operators are `AND`/`OR`. An unknown criterion
id or an unimplemented operator (`NOT`, `XOR`, …) raises rather than being
approximated: the rule is applied as written or refused. A genuinely new *kind*
of requirement — say, a psychological evaluation within six months — needs a
new predicate in reviewed Python, and that is the design working as intended:
a new piece of clinical logic should arrive as a diff a reviewer reads, not as
an expression a generic engine improvises over.

---

## A full prior-auth form, mapped to these lanes

A complete prior authorization request carries more than this build
implements — this build is one procedure family, one jurisdiction, chart notes
as the only unstructured evidence. But the architecture's rules assign *every*
field of a full form to a lane mechanically, and the assignment is worth
seeing whole, because it is what "applies to a different modality" actually
means here. Four lanes:

- **Input** — arrives with the request. Never trusted as evidence directly;
  the workflow re-reads everything through the ports.
- **Agentic (model leaf)** — a declared model call: turning unstructured text
  into structured claims, planning retrieval, or blind-checking a citation.
  Never control flow, never arithmetic.
- **Dynamic (policy JSON)** — swappable per jurisdiction or modality:
  constants, the boolean rule, code sets, value sets. A new modality is
  chiefly a new file here.
- **Python (deterministic)** — fixed, reviewed code: resolution, dates,
  counting, set membership, aggregation, span validation.

| Form field | Enters as | Agentic part | Dynamic (policy JSON) | Python (deterministic) | In this build |
|---|---|---|---|---|---|
| **Administrative** | | | | | |
| Patient data | Input — a FHIR bundle | None — the extractor is *barred* from structured data, so the note reading stays independent | — | Structured observations and conditions read from the patient port | Live |
| Requesting provider | Input | — | — | Identity pass-through and validation | Not in v1 |
| Servicing provider | Input | — | — | Identity pass-through and validation | Not in v1 |
| **Code alignment (code request)** | | | | | |
| Diagnosis codes (ICD / SNOMED) | Input — inside the bundle | — | The comorbidity value set (543 codes) and the exclusion's condition binding | Set membership: criterion (b), and the national T2DM exclusion (sc2) | Live (SNOMED, per the corpus) |
| CPT / HCPCS procedure code | Input — `--procedure` | — | The tree's three procedure sets: covered, non-covered, contractor-determined | sc1 resolution to one of four typed answers, zero model calls | Live |
| Medication | Input | — | The tree already records "pharmacological management alone is insufficient" as policy — no predicate consumes it yet | Would be value-set membership, like (b) | Constant recorded; no predicate in v1 |
| **Clinical justification & evidence** | | | | | |
| Step-therapy / prior-treatment log | Inside the chart note | The model extracts treatment encounters (`wm_events`) with verbatim quotes | Months required, recency window — A53028's constants | Qualifying-run selection, consecutive-month counting, recency arithmetic (c1–c5) | **Live — the weight-management program history is exactly this shape** |
| Disease severity markers | Structured observations *and* the note | The note's BMI reading is extracted by the model | The BMI threshold, the discrepancy tolerance | Criteria (a) and (b); reconciliation of the two independent BMI readings | Live |
| Urgency indicator | Input flag | — | Could be a tree field (a different SLA, not a different rule) | Routing — Article I keeps prioritization out of the model | Not in v1 |
| **Supporting documents** | | | | | |
| Recent provider notes | Input documents | The model reads the note — its *only* tool | — | Quote anchoring, span validation, and the blind verifier's replay | Live (one note per patient; a second is the open task) |
| Diagnostic imaging / lab reports | Input documents | Same extraction lane: unstructured → cited claims | Criteria naming them would be tree data | Identical span validation — the mechanism does not care what kind of document it slices | Not in v1 |
| Letter of medical necessity (LOMN) | **Output**, not input | Drafting narrative prose would be a model leaf | — | Every claim in it would carry a validated span; the verdicts it summarizes stay Python's | Not in v1 — the emitted packet (seven verdicts + gap list + citations) is the deterministic equivalent |

Two things the table is really saying. First, **the lane is decided by the
field's nature, not by engineering taste**: anything quantified by policy goes
in the JSON, anything computable goes in Python (Article II), and the model
touches a field only where unstructured text has to become a structured,
citable claim (Article I). Second, **a new modality mostly fills existing
lanes rather than adding new ones** — an oncology drug PA swaps in a different
tree (different value sets, different constants, a step-therapy expression
with `OR`s) and different attached documents, while the resolver, the
expression evaluator, the anchoring, and the verifier run unchanged. What it
*cannot* do without a reviewed code change is introduce a new kind of
predicate — which is the point.

---

## Two implementations, one oracle

The same determination runs two ways, and the difference between them is the
project's central measurement.

Three ports meet at the model boundary:

- **`ExtractionRunner`** — *who reads the note.* A direct `google-genai`
  runner, an ADK agent runner, and a recorded runner that replays a committed
  extraction for zero cost. The recorded runner is why the entire test suite
  and eval harness exercise the full chain end-to-end for free.
- **`RetrievalPlanner`** — *who decides what to fetch.* A fixed planner (three
  store reads in a fixed order) and an agentic planner (the model chooses,
  from a bounded tool allowlist). Everything downstream cannot tell which
  planner ran — which is exactly what makes the comparison a comparison.
- **`VerifierRunner`** — *who checks the citations.* Live, recorded (replays a
  committed 27-claim recording keyed by claim digest — a miss raises, never
  defaults), and a deliberately raising null runner.

**Where ADK sits: it is a leaf, never the skeleton.** `google-adk` is imported
in exactly one package, `pa_agent/agent/`, which supplies one implementation
each of the first two ports — the ADK extraction runner and the agentic
retrieval planner — plus their tool allowlists. The natural design ADK invites,
an ADK `Workflow` orchestrating the steps, was explicitly rejected: it would
hand the graph to a framework whose edges can route on model output
(Article I), and it would put `google.adk` on the import path of every
deterministic test and every zero-model-call determination. The workflow is a
plain Python tuple instead, and `sys.modules` assertions keep the model SDK off
the deterministic path.

**What ADK bought, concretely.** A leaf role is not a small role: both live
implementations at the model boundary — the extraction agent and the agentic
retrieval planner, the two halves of the project's central measurement — are
ADK agents, and the framework earned its place three ways. First, the
constitution is enforceable by *configuration*, not prompting:
`include_contents="none"` gives each note a fresh context, two
`disallow_transfer` flags select `SingleFlow` so the handoff tool is never
even injected, the tool allowlist is a literal list, and `max_llm_calls` is a
hard ceiling in which one tool round trip counts as two calls. A rule that
lives in configuration can be asserted by a test; a rule that lives in a
prompt cannot. Second, per-turn `usage_metadata` is where every cost figure
in this README comes from — calls, input and output tokens for every turn,
including the turn a tool response adds. Third, `LlmAgent` accepts a plain
`BaseLlm` *instance* as its model, and that one seam is what made the
framework cheap to test rather than expensive to trust — the suite drives
the real ADK flow on every run, as described under *Testing the ADK path*
below.

**The measured result so far:** model-directed retrieval agrees with the
deterministic oracle on **6/6 outcomes and 42/42 criterion verdicts, with
80/80 spans valid and zero errors.** It cost **24 model calls and 84,925 input
tokens** across six patients that the fixed planner spent nothing on — 4.3× the
deterministic path's end-to-end input tokens, on a comparison where extraction
and verification are the same replayed payload on both sides. Quote the delta
beside the ratio: the fixed planner makes no model call, so the ratio's
denominator is the shared replayed cost and it moves when that cost changes,
while the delta does not. Read the aggregate and the spread, never one patient's
ratio.

> **Scope of every number in this repo:** the eval ground truth was drafted
> alongside the system as a working first draft — spans are validated
> mechanically by the scorer, never against a label — and on a set this small
> a perfect score means the approach does not obviously fail. A larger,
> re-reviewed label set belongs to a later version.

Guardrails that keep the differential honest:

- The extraction agent **never sees structured observations** — hand it the
  structured BMI and the two independent BMI readings quietly become one, and
  the tests that compare them would pass *because* the system broke.
- The extractor's and retriever's tool allowlists are **disjoint, not
  nested** — the extractor has no route to a second document at all.
- The tool payload is never the evidence path: the workflow re-reads
  everything from the ports, so payload truncation is a cost control, not a
  quiet second filter.
- A changed call configuration — tool declaration, SDK, or API tier — is a
  **new measurement, never a re-run**. The API tier can change the effective
  prompt, not just the endpoint.
- Every model turn is counted, not just the first: a tool round trip is two
  LLM calls, and summing only first turns once understated output tokens
  12.1× and inverted a comparison's sign using figures that were each
  individually real.

---

## The policy corpus, and what it took to get right

Three documents, one jurisdiction:

| Document | What it is | What it may be cited for |
|---|---|---|
| `ncd_100_1` | CMS NCD 100.1, bariatric surgery | National coverage and non-coverage |
| `a53028` | Noridian A/B MAC billing & coding article, Jurisdiction F | **Every quantified constant** in the criteria tree |
| `r931cp` | CMS Pub. 100-04 Transmittal 931 | Code bindings **only** — its coverage content predates the 2012 delegation to the MACs |

Domain facts that took real work to establish, and that a reviewer should hear
stated plainly:

- **NCD 100.1 quantifies nothing.** No months, no visit counts, no recency
  windows. Every constant comes from A53028, so this system determines
  coverage *as Noridian would* — a different MAC is a different criteria tree
  over the same NCD. The thresholds are not "CMS's."
- **43842, not 43775, is the non-covered case.** The NCD non-covers
  laparoscopic sleeve gastrectomy (43775) only *"prior to June 27, 2012,"*
  then delegates to the MACs — so 43775 is contractor-determined and covered
  in this jurisdiction. 43842 (open vertical banded gastroplasty) is named
  non-covered for all beneficiaries, no date qualifier.
- **A procedure code carries two citations of different classes:** "this
  procedure is non-covered" and "this code denotes that procedure" are
  different claims with different sources, and they are never merged into one
  field.
- **Exactly two constants are decisions rather than spans** — criterion (a)'s
  12-month lookback and the 1.0-BMI-point discrepancy tolerance — each
  recorded with a name and date in the decision log. The count is pinned at
  zero provisional constants, so a new one is a visible diff.

Patient data is entirely synthetic: seven Synthea v4.0.0 FHIR bundles
(pinned by manifest hashes, one carrying a declared synthetic observation) and
six synthesized chart notes. No real or de-identified patient data of any kind
is in scope.

---

## What the spike taught, and where it landed

Before anything was built, `spike/spike_001/` tested the assumption the whole
design rests on: can a single model call read a chart note and return correct
weight-management encounters, with citable spans, without counting the things
the spec excludes — unsupervised attempts, missed visits, failed contact
attempts? Five hand-written notes, three runs at temperature 0, scored entirely
by Python. It is still alive: `--verify` is one of the gates, and the five
notes are permanent regression cases.

| The spike found | What shipped because of it |
|---|---|
| Extraction held: event precision and recall 1.000, 21/21 on the hard exclusion traps — on the *first* prompt formulation, no tuning spent | The prompt and schema were promoted **verbatim** to `pa_agent/extraction.py`. Rewriting a measured prompt would restart its history at zero. |
| Model-emitted character offsets: **0 of 80 usable** — not one sliced back to its own quote | Quote anchoring became mandatory and its own module, `pa_agent/anchor.py`, kept separate from span *validation* so a locator bug cannot launder itself through the check meant to catch it |
| Three real quotes failed exact matching only because the notes hard-wrap at ~76 columns, as EHR exports do | Anchoring is whitespace-normalized but still records raw offsets into the unmodified document. Fuzzy matching was rejected: a threshold generous enough to absorb a line wrap is generous enough to absorb a changed date. |
| A gate that re-runs the model answers differently on every invocation and spends quota on every check | The measure / `--rescore` / `--verify` split — spend calls once, re-derive and re-check from the recording for free — which became the pattern behind **every** recording in the repo |
| A shared ADK session carried note N−1 into note N's context | One fresh context per note, preserved in the ADK runner as `include_contents="none"` |
| Three model-name literals sat in two files disagreeing with each other and with the recording, while every gate stayed green | `pa_agent/model_pin.py` — the only tracked Python file permitted to name a model, enforced by test |
| Multi-occurrence quotes (a quote appearing twice, anchoring to the wrong place): measured **zero** | The one number the spike got wrong. Its hand-written notes gave the patient a different BMI every month; real charts plateau. The first real corpus measured 12 of 169, seven anchoring to the *wrong encounter* — spans true about the document and false about the claim. Fixed deterministically: per-field spans anchor to the occurrence nearest their own event. |

The last row carries the method lesson the rest of the repo quotes: **a clean
spike number can be a property of the spike's corpus, not of the mechanism** —
which is why the spike's own caveats (owner-authored notes, owner-authored
labels, small n) still travel with every 1.000 in this README.

---

## Using it

`python` is not assumed on PATH; the tracked venv is Python 3.12.

### A determination

```bash
./venv/bin/python -m pa_agent.cli --patient 49092fd9-d5bf-24e2-474b-00041a279a47 --procedure 43775
```

| Flag | Meaning |
|---|---|
| `--patient <id>` | required — the patient identifier |
| `--procedure <code>` | required — the requested CPT/HCPCS code |
| `--extraction recorded\|direct\|adk` | which model leaf reads the notes. Default `recorded` replays the committed extraction for **zero model calls**; `direct` (raw `google-genai`) and `adk` spend live calls |
| `--recording <path>` | the recording replayed under `--extraction recorded` |
| `--tool-fetch` | with `--extraction adk`: the agent fetches the note through its `read_note` tool instead of receiving it in the message |
| `--as-of YYYY-MM-DD` | the date recency windows are measured from (default: today). Pin it to reproduce a determination |

Exit codes: `0` an answer (honest abstentions included), `1` a bad request
(unknown patient), `2` an unbuilt path, `3` a determination aborted because a
criterion is in `ERROR` — the criterion id and error code go to stderr,
nothing to stdout.

The live modes need a Gemini API key in `pa_agent/agent/.env` (gitignored — no
real key ever appears in a tracked file).

### Gates and tests

```bash
./venv/bin/python scripts/check_gates.py      # all ten zero-cost gates, ~25s
./venv/bin/python -m pytest -q                # the suite alone (653 tests, ~18s)
./venv/bin/python -m pytest tests/test_criteria_c.py -q         # one file
./venv/bin/python -m pytest tests/test_criteria_c.py -q -k e5   # one test
```

Nothing in the gates spends a model call or touches the network — that is a
membership rule enforced by a test, not a habit. The commands that do spend
model calls each carry a free replay flag (below).

### Testing the ADK path

```bash
./venv/bin/python -m pytest tests/test_adk_agent.py tests/test_agentic_workflow.py -q
```

ADK was not just shipped here — it was tested against, constantly. `LlmAgent`
accepts a `BaseLlm` *instance* as its model, so a scripted fake of about
twenty lines drives the entire real flow: real `FunctionTool` declarations
built from real signatures, real tool dispatch, the real `output_schema`
handling, real plugin hooks, real session state, real `usage_metadata`. What
is faked is the network, and nothing else. (ADK ships a `MockModel` for its
own CLI conformance runner, but nothing in it is public API — the pattern was
copied, not imported.)

Ninety tests across these two files run that way, and they sit in the
main suite rather than behind a marker — so every `pytest` run, and therefore
every gate close, exercises the real framework for zero model calls and no
credential. That is what keeps the structural rules honest: the tool
allowlists, the `SingleFlow` selection, the fresh-context setting and the
call ceiling are asserted on constructed ADK agents, not promised in
docstrings. The skeleton gate goes one step further and boots a real
backgrounded `adk web` server on every close, probing `/list-apps` to prove
the installed framework still discovers the agent.

### The eval harness

```bash
./venv/bin/python eval/run_eval.py                     # baseline diff — the gate
./venv/bin/python eval/run_eval.py --update-baseline   # adopt drift, as a reviewed diff
```

Fifteen labeled cases, every cited span re-validated by the scorer. The gate
fails on drift in **either** direction, so a case that *starts* passing is
adopted explicitly with `--update-baseline` and a commit. Four result statuses
stay distinct: `PASS`, `FAIL`, `BLOCKED` (the component does not exist yet —
that names a task, not a bug), and `ERROR` (counted in neither side of the
abstention rate). `--cases` and `--baseline` point at alternate files;
`--json <path>` writes the results out.

### Measurements, and their free replays

Four commands spend model calls. Each records what it measured, and each has a
free path that re-derives every number from the committed recording:

| Spends model calls | Free replay |
|---|---|
| `scripts/run_extraction.py` — the direct extraction measurement | `--rescore` re-anchors the recorded payloads |
| `scripts/run_adk_extraction.py [--tool-fetch] [--limit N]` — the ADK extraction measurement, in either mode | `--compare` diffs this mode's recording against the direct one |
| `eval/run_agentic_eval.py --measure [--limit N]` — the fixed-vs-agentic differential | bare run is the gate; `--rescore` recomputes the oracle side; `--report` prints the comparison |
| `scripts/run_verifier_measurement.py` — the blind-verifier measurement | `--rescore` re-checks the committed recording |

### The ADK dev UI

```bash
./venv/bin/adk web pa_agent
```

Serves the ADK development UI over the agent package; the app is named
`agent`. `GET /list-apps` returns the discovered apps, and `GET /` redirects
(307) to `/dev-ui/`.

The dev UI earned real mileage during development: watching the extraction
and retrieval agents make their tool calls turn by turn is how prompt and
tool-declaration changes were sanity-checked before spending a measured run —
a changed tool declaration is a changed prompt, so seeing the turns beats
guessing at them. The same server is what the skeleton gate boots and probes
on every close, so the UI shown here is never drifting ahead of what the
gates verify.

### Regenerating the metrics report

```bash
./venv/bin/python eval/build_report.py           # regenerate eval/report.md
./venv/bin/python eval/build_report.py --verify  # check it matches its sources (a gate)
```

---

## How the project is governed

The reasoning behind this project lives in five documents under `docs/`, not
in this README. They are worth knowing about because every claim above traces
back to one of them:

| Read | To learn |
|---|---|
| `docs/constitution.md` | The ten articles above in full, with Amendment 1 |
| `docs/spec.md` | Numbered requirements, edge cases, acceptance criteria — and §10, the problems to address |
| `docs/stories.md` | The user stories and personas |
| `docs/tasks.md` | The board; `Path to v1` at the top says what happens next |
| `docs/decisions.md` | Why everything is the way it is — every choice, the alternative it rejected, and the condition that would reverse it |

Two habits from that system show up in the code: every task closes on a
command that returns zero, and every gate is mutation-tested — where a
behavioural test cannot catch a mutation, the invariant is pinned by parsing
the AST instead.

---

## Where this system degrades

Accuracy is not where this breaks — the measured rates are perfect on a set
small enough that perfection mostly means "did not obviously fail." The real
failure modes are structural. Each is analyzed in full in `docs/spec.md` §10
(*Problems to address*, P1–P8); the short version:

- **P1 — One jurisdiction.** Every threshold is Noridian Jurisdiction F's, not
  CMS's. Pointed at another MAC's patient it answers confidently and wrongly,
  and no code path notices.
- **P2 — Extraction refuses paraphrase.** A model that paraphrases instead of
  quoting produces a claim nobody can anchor, so the system abstains where
  evidence existed. Fail-closed, and still a loss.
- **P3 — Small everything.** Six patients, three documents, fifteen cases:
  every rate moves in large steps, and one case outweighs a percentage point.
- **P4 — The ground truth is a first draft.** The labels were drafted
  alongside the system and labeled once; re-labeling and review ride with
  the corpus expansion of a later version, alongside P3's set size.
- **P5 — The blind verifier cannot check arithmetic.** It sees one claim and
  one quote, so shortfall claims ("only three months") are checked by Python,
  not by the verifier.
- **P6 — The model's judgment is never on the hook.** Model adjudication is
  deliberately unclaimed; the agentic path decides what to *read*, never what
  the answer is.
- **P7 — Retrieval recall cannot currently fall.** With one note per patient
  the gathered-recall figure is 1.000 by construction; a second note per
  patient — the one open task — is what would make it a real test.
- **P8 — Every free number is a replay.** The reproducible figures describe
  one measured day, one pinned model, one API tier. Any configuration change
  is a new measurement, never a re-run.

---

## Status and the road to v1

**64 of 65 tasks closed, 1 open; all ten gates green.**
Delivered: US-1 through US-7 and US-9 — instant screening of non-covered
procedures, cited structured criteria, the categorical exclusion, note-only
criteria with two independent BMI readings, the gap list, the blind verifier,
the metrics report, and full `ERROR`-state accounting.

**Acceptance gates A1–A9 all hold.** The measured figures live in
`eval/report.md`, which is generated rather than written: `python
eval/build_report.py --verify` recomputes every number from the committed
recordings and fails on any that no longer matches, so a stale figure is a red
gate rather than a plausible-looking table.

| Gate | Result |
|---|---|
| A1 | 15 labeled cases, every spec §6 edge case present |
| A2 | precision **1.000** on `MET`, against a **0.611** base rate and an always-`MET` baseline scoring exactly that |
| A3 | **zero** `MET` verdicts with an invalid span, over 82 spans checked |
| A4 | E2 and E3 complete with zero model calls |
| A5 | abstention **0.200**, accounted for per `gap_reason`, swept against `discrepancy_tolerance` |
| A6 | 33 model calls / 27,175 in / 5,723 out / 36.1s across nine determinations, from instrumentation |
| A7 | 55 requirements: 53 mapped to a check, 2 declared unclaimed with a decision entry behind each |
| A8 | the failure-modes summary above; full analysis in `docs/spec.md` §10 |
| A9 | zero determinations presented with a criterion in `ERROR` |

**One task is open, and it is not on the path to the acceptance gates:** a
second note per patient, which is what would let the direct retrieval-recall
figure fall. It costs a new extraction recording and a new verifier recording —
both are keyed by note content — and therefore most of the repo's committed
numbers.

**Nothing else is outstanding — including the one thing a reader might assume
is.** An earlier board carried a programme to add a formal review stamp to the
eval labels; it was deleted, with the full record kept in the decision log.
The labels stand as a working first draft — every cited span is validated
against the source by the scorer — and further label review rides with the
corpus expansion of a later version rather than sitting on this board.

Two requirements are **unclaimed on purpose**: model-performed adjudication
is reserved out of v1 because Amendment 1 keeps the entire
decision procedure in Python — there is no verdict a model could determine
without doing something reserved. Declaring that explicitly, rather than quietly
not doing it, is what makes the coverage gate satisfiable.

---

## Repository layout

```
pa_agent/            resolver, criteria, spans, index, anchor, workflow,
                     retrieval, runners, extraction, verifier, reconcile,
                     aggregate, determination, contracts, model_pin, cli
  agent/             ADK path: extraction_agent, retrieval_agent, tools, bounds
  stores/            policy.py and patient.py — two ports, two planes;
                     __init__.py imports neither, on purpose
data/policies/       three source documents, the SNOMED value set, and the
                     criteria tree (policy_version_id ncd-100.1-jf-v1)
data/patients/       seven Synthea bundles + six synthesized notes, hash-pinned
eval/                cases.json, baseline.json, report.md (generated), and the
                     committed recordings that make replay free
spike/spike_001/     the founding extraction spike — still a gate and a
                     regression corpus; see "What the spike taught"
scripts/             the gates, the measurement scripts, and the corpus tooling
tests/               the suite (653 tests), including the AST-level pins
docs/                constitution, spec, stories, tasks, decisions
```

---

*Numbers in this README come from instrumentation (Article X) and carry the
ground-truth caveat stated above. Authorship and ownership of this work are
recorded at the git level, which is the only place they were ever asserted
from.*
