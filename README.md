# Prior Authorization Determination Agent

A prior authorization determination system built to work across doctors'
practices. Given a patient record and a requested procedure code, it produces
a reviewable determination: a verdict for every criterion in the governing
policy, a citation for every verdict, and a gap list naming exactly what the
chart is missing. The control case it is currently tested against is
bariatric surgery under CMS NCD 100.1, as two MACs implement it — Noridian
Jurisdiction F and Palmetto GBA — one policy chosen to exercise every part of
the engine, not a boundary of the design.

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
engine — a change to data under review, not a change to Python. Two trees are
loaded today over the same NCD — Noridian's and Palmetto's — and they differ
in **shape**, not only in constants: Palmetto states no run length and adds a
criterion the pipeline cannot evaluate, which the engine declares unclaimed
and abstains on rather than approving past. *Where this system degrades*
below is explicit about what that does and does not prove.

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

From a fresh clone, setup is two commands — Python 3.12, exact pins, no API
key:

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pa_agent.cli --patient 49092fd9-d5bf-24e2-474b-00041a279a47 --procedure 43775
```

The last command prints a real determination — seven criterion verdicts, evidence
spans that slice back into the source note, a gap list, and per-run cost
counters — for **zero model calls**, because the default extraction runner
replays a committed recording.

---

## Where the project stands

**v1 and v1.1 are both complete, and v1.2 is under way.**
73 of 73 tasks closed, 0 open, all ten zero-cost gates green, and acceptance
gates A1–A9 holding. The suite collects 1006 tests (27 skip). v1 delivered the
determination end to end; v1.1 closed spec §10's eight known limits — a
second jurisdiction, a bounded re-ask for unanchorable quotes, a second note
per chart, a citation-sufficiency check, a second measured tier, and the
written-down path for the one limit that stays open on purpose. v1.2 opened
with the predicate vocabulary: every criterion a tree declares names the
arithmetic that evaluates it, and a tree naming one the engine lacks fails to
load instead of abstaining past it *(T-91, D110)*. Its second row added the
first tree from an unrelated practice — **infliximab for rheumatoid
arthritis**, compiled from Palmetto GBA's L35677 and A56432 — which loads
beside the bariatric trees, resolves by its own J-code in the same seven
states, and needed one predicate kind the engine did not have *(T-92,
D111)*. Its third row gave that practice patients: two Synthea charts
carrying rheumatoid arthritis in Palmetto's territory, one of them cloned and
given the drug the policy's limitation excludes — because the generator
writes no biologic for this disease at any population size — and three eval
rows over them. **The engine needed nothing**: the same code that closed
T-92 produced every labeled verdict, so what a second practice cost here was
corpus work and not engine work *(T-93, D113)*.

Measured figures live in `eval/report.md`, which is generated and gate-verified
rather than written; *Status in detail* below carries them, and
*Where this system degrades* carries the limits.

### The road from here

One version at a time; a version opens when the previous one closes. Each
names one story and one acceptance gate, and mints its requirements when its
first task opens. The scope of each is in `docs/spec.md` §11 *(D105)*.

| Version | Delivers | Story | State |
|---|---|---|---|
| v1 | bariatric determination end to end, two implementations graded against one oracle | US-1–US-9 | **complete** |
| v1.1 | spec §10's eight known limits, one task each | — | **complete** |
| v1.2 | cross-practice round one: rheumatoid arthritis, then diagnostic ultrasound — the rules engine only | US-10 | **in progress** |
| v1.3 | medical-history review: ICD suggestions with evidence, colour-sorted by how much evidence each has | US-11 | planned |
| v1.4 | sessions and intake, headless | US-12 | planned |
| v1.5 | the form, review, simulated submission and tracking, headless | US-13 | planned |
| v1.6 | cross-practice round two: tree-declared extraction, two more practices | US-14 | planned |
| v2.0 | the reviewer's UI over the session port | US-15 | planned |
| v2.1 | the payer axis: national and regional coverage, and a floor that is checked | US-16 | planned |
| v2.2 | a mimicked commercial payer policy, and the criteria Medicare never states | US-17 | planned |

**v1.2 asks the question this design is built to answer:** whether the engine
is bariatric-shaped. Two trees from unrelated practices are compiled against
the predicate vocabulary the engine already has, and where a criterion cannot
be expressed the engine must say so — declared unclaimed and abstaining, never
quietly omitted, because omitting a criterion approves where a payer would
not. It spends zero model calls.

Its first row made that vocabulary explicit. Until `T-91` the engine chose a
criterion's arithmetic from the criterion's **id**: `a` was the BMI
threshold, `c3` the run length. Coverage documents letter their criteria `a`,
`b`, `c` as a matter of course, so a rheumatology tree would have been
evaluated by bariatric arithmetic against rheumatology constants, answered,
and cited a span — passing every test in the repo. A tree now declares each
criterion's `kind` from a closed set, the engine dispatches on that, and a
kind it does not implement fails at load naming the tree, the criterion and
the kind. **Unbuilt is not unclaimed:** a limit the tree declares is reviewed
and reported as an abstention; a predicate nobody wrote is not allowed to
borrow it *(REQ-57, REQ-58, D110)*.

Its second and third rows put that to work on a real practice: **infliximab
for rheumatoid arthritis**, compiled from Palmetto GBA's L35677 and A56432,
which loads beside the bariatric trees over the same seven states and needed
exactly one predicate kind the engine did not have *(T-92, D111)*; then that
practice's patients and eval rows, which needed **no engine change at all**
*(T-93, D113)*. **Diagnostic ultrasound is the next practice to be tested**,
and the version closes on a compatibility account that classes every
criterion of every tree as evaluated by an existing predicate kind, by a new
one, or unclaimed. The measured results are in *Status in detail* below.

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
unusable (0 of 80 correct in the spike; 0 of 171 in the first full-corpus run;
0 of 169 in its re-measurement; 0 of 175 on the two-note corpus), so spans
are located by searching for the
model's verbatim quote — exact match first, then whitespace-normalized —
always recording raw offsets. A quote the search cannot find is re-asked
once, for its verbatim text, and the answer is searched the same way: the
model supplies a new quote, Python admits or drops it, and nothing rewrites a
quote to an overlap it found. Locating (`anchor.py`), resolving
(`index.py`), and validating (`spans.py`) are separate modules so a locator
cannot launder its bugs through the validator.

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
implements — this build is one procedure family, two jurisdictions, chart
notes as the only unstructured evidence. But the architecture's rules assign *every*
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
| Recent provider notes | Input documents | The model reads the note — its *only* tool | — | Quote anchoring, span validation, and the blind verifier's replay | Live (two notes per patient since T-81; the extractor reads one at a time) |
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
  extraction for zero cost. Both live runners walk the same fixed two-step:
  extract, then one bounded re-ask for the verbatim text of any quote the
  anchorer refused. The recorded runner is why the entire test suite and
  eval harness exercise the full chain end-to-end for free.
- **`RetrievalPlanner`** — *who decides what to fetch.* A fixed planner (three
  store reads in a fixed order) and an agentic planner (the model chooses,
  from a bounded tool allowlist). Everything downstream cannot tell which
  planner ran — which is exactly what makes the comparison a comparison.
- **`VerifierRunner`** — *who checks the citations.* Live, recorded (replays a
  committed 33-claim recording keyed by claim digest — a miss raises, never
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
deterministic oracle on **7/7 outcomes and 49/49 criterion verdicts, with
93/93 spans valid and zero errors**, on the two-note corpus (D104). It cost
**24 model calls and 90,743 input tokens** across seven patients that the fixed
planner spent nothing on — 3.6× the deterministic path's end-to-end input
tokens, on a comparison where extraction and verification are the same
replayed payload on both sides. Quote the delta
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
  individually real. The rule reaches every path — the direct runner's
  re-ask is a second turn on its trace, and a replay carries the recorded
  trace whole.

---

## The policy corpus, and what it took to get right

Five documents, two jurisdictions:

| Document | What it is | What it may be cited for |
|---|---|---|
| `ncd_100_1` | CMS NCD 100.1, bariatric surgery | National coverage and non-coverage |
| `a53028` | Noridian A/B MAC billing & coding article, Jurisdiction F | **Every quantified constant** in Noridian's tree, and the one corpus sentence binding CPT 43775 to its procedure |
| `r931cp` | CMS Pub. 100-04 Transmittal 931 | Code bindings **only** — its coverage content predates the 2012 delegation to the MACs |
| `l34576` | Palmetto GBA LCD, Jurisdictions J and M | **Every quantified constant** in Palmetto's tree |
| `a56852` | Palmetto GBA billing & coding article | Nothing yet — its CPT table sits behind the AMA licence modal and the extracted text names no code |

A request resolves by procedure code **and the patient's state**: Washington
reaches Noridian's tree, Alabama reaches Palmetto's, and Texas — in neither —
is answered `NO_JURISDICTION_TREE`, never a default. The two trees differ in
shape, not only in numbers: Palmetto states no run length, requires weight
rather than BMI monthly, and adds a multidisciplinary evaluation, so two of
its criteria are declared unclaimed and abstained on rather than evaluated
on a proxy.

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

Patient data is entirely synthetic: eleven Synthea v4.0.0 FHIR bundles
(pinned by manifest hashes; one carries a declared synthetic observation, one
is a declared clone re-addressed into Palmetto's territory, and one is a
declared clone carrying a declared synthetic prescription) and fourteen
synthesized chart notes — two per note-bearing chart since T-81, a split of
the facts each manifest declares — the clone's byte-identical to its source's
by declaration. No real or de-identified patient data of any kind is in scope.

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
./venv/bin/python scripts/check_gates.py      # all ten zero-cost gates, ~35s
./venv/bin/python -m pytest -q                # the suite alone, ~55s
./venv/bin/python -m pytest tests/test_criteria_c.py -q         # one file
./venv/bin/python -m pytest tests/test_criteria_c.py -q -k e5   # one test
```

1006 tests across 37 files, 27 of them skipped — the skips are a per-tree
constant matrix, which skips the pairs a given tree does not declare.

Nothing in the gates spends a model call or touches the network — that is a
membership rule enforced by a test, not a habit. The commands that do spend
model calls each carry a free replay flag (below).

#### Testing a practice's rules

Each practice gets two files, and the split is deliberate: one drives the
tree over charts **written in the test**, the other drives it over the
**committed bundles**. A tree that loads and evaluates says nothing about
whether a real chart exercises it, and a corpus that answers correctly says
nothing about the cases the corpus does not contain.

```bash
# bariatric surgery — NCD 100.1 as two contractors operationalize it
./venv/bin/python -m pytest tests/test_criteria_tree.py tests/test_criteria_c.py -q

# rheumatoid arthritis — Palmetto GBA's L35677, the second practice
./venv/bin/python -m pytest tests/test_infliximab_tree.py -q          # the tree
./venv/bin/python -m pytest tests/test_rheumatology_corpus.py -q      # the charts
```

`tests/test_infliximab_tree.py` (23 tests) pins resolution, dispatch by
declared kind, the declared-unclaimed criteria and the exclusion.
`tests/test_rheumatology_corpus.py` (15 tests) pins the committed charts: that
the value sets admit exactly what each chart carries in its declared code
system, that the three labeled determinations answer as `eval/cases.json`
says, that the denial cites the prescription that fired it, and that the
generated pair differs by exactly the one fact criterion (b) turns on. It also
states what it **cannot** catch and where that claim lives instead — no
committed chart carries a finished course of a drug in either value set, so
the "a completed order is not an active one" rule is held by the tree file,
on charts written for it.

**Diagnostic ultrasound is the next practice to be tested** — `T-94`, row 4
of v1.2 — and it arrives as the same pair: a tree compiled from a fetched MAC
document, then patients and rows over the committed corpus, with `MET`,
`NOT_MET` on a frequency limit, and `NO_POLICY_FOUND` for an unlisted code.

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

Twenty labeled cases across two practices, every cited span re-validated by
the scorer. The gate
fails on drift in **either** direction, so a case that *starts* passing is
adopted explicitly with `--update-baseline` and a commit. Four result statuses
stay distinct: `PASS`, `FAIL`, `BLOCKED` (the component does not exist yet —
that names a task, not a bug), and `ERROR` (counted in neither side of the
abstention rate). `--cases` and `--baseline` point at alternate files;
`--json <path>` writes the results out.

### Measurements, and their free replays

Four commands spend model calls. Each records what it measured, and each has a
free path that re-derives every number from the committed recording. All four
take `--tier {ai_studio,vertex}`, defaulting to the development tier; the
output *and* the replay paths are routed per tier, so a Vertex run cannot
overwrite the recordings the gates read:

| Spends model calls | Free replay |
|---|---|
| `scripts/run_extraction.py` — the direct extraction measurement | `--rescore` re-anchors the recorded payloads and re-derives what the re-ask recovered |
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
| `docs/tasks.md` | The board; `Path to v1` at the top says what happens next, and `Roadmap after v1.1` what follows |
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

- **P1 — Two jurisdictions, and the thresholds are still not CMS's.** A
  request resolves by procedure code and state: Noridian's tree for its ten
  states, Palmetto's for its seven, and `NO_JURISDICTION_TREE` for the rest —
  every other MAC is a tree nobody has compiled, and Palmetto's showed that
  MACs differ in shape as much as number. One eval row runs under it: `J1`,
  E4's chart cloned into Alabama, where the three-month run that is a
  shortfall in Washington is not a criterion at all.
- **P2 — Extraction refuses paraphrase.** A model that paraphrases instead of
  quoting produces a claim nobody can anchor, so the system abstains where
  evidence existed. Fail-closed, and still a loss. The bounded re-ask is the
  standing answer; on the two-note corpus it fired once, on the very
  paraphrase P2 was written from, and recovered it. What remains is a claim
  the model never quoted at all.
- **P3 — Small everything.** Eleven patients (two of them declared
  clones), fourteen chart notes, seven policy documents, twenty cases: every
  rate moves in large steps, and one case outweighs a percentage point.
- **P4 — The ground truth is a first draft.** The labels were drafted
  alongside the system; the second pass was taken with T-81, every row
  re-derived from the manifests and the trees' constants and recorded in
  D104. P3's set size is the bound that remains.
- **P5 — The blind verifier cannot check arithmetic.** It sees one claim and
  one quote, so shortfall claims ("only three months") are checked by Python,
  not by the verifier.
- **P6 — The model's judgment is never on the hook.** Model adjudication is
  deliberately unclaimed; the agentic path decides what to *read*, never what
  the answer is. What would change that is written down rather than left
  vague: not a criterion that is merely hard to extract — the second
  contractor's multidisciplinary-evaluation requirement looks like one and
  decomposes into extraction plus set membership plus a date window — but a
  criterion whose *predicate* cannot be compiled at all, of the kind a policy
  asks for when it wants a "diligent effort" rather than a threshold. Three
  things follow, and they are why it stays unclaimed: a model verdict is not
  reproducible run to run, which is what this project's own second article
  forbids; the deterministic path stops being the regression oracle for a
  criterion it cannot compute; and the differential that grades the agentic
  path could not cover it.
- **P7 — Retrieval recall is one measured day.** Since T-81 every chart is
  two notes and the planner can skip one, so the direct figure is measured
  rather than constructed; on the first measurement it gathered every note.
  A free-tier tool loop is not reproducible at temperature 0, so that is a
  sample, re-measured and never re-run.
- **P8 — Every free number is a replay.** The reproducible figures describe
  one measured day against one pinned model. That is still true, and the
  *one tier* half no longer is: the whole corpus was measured a second time
  on Vertex and both sets are committed, rendered as columns in
  `eval/report.md`. Fidelity is identical across the two; **cost is not**, and
  the direct runner spends markedly more input tokens on Vertex for an
  identical prompt, so token figures are only quoted within a tier. Any
  configuration change is still a new measurement, never a re-run.

---

## Status in detail, and what it measured

The summary and the roadmap are at the top of this file; this section is the
measured substance behind them.

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
| A1 | 20 labeled cases, every spec §6 edge case present |
| A2 | precision **1.000** on `MET`, against a **0.567** base rate and an always-`MET` baseline scoring exactly that |
| A3 | **zero** `MET` verdicts with an invalid span, over 98 spans checked |
| A4 | E2 and E3 complete with zero model calls |
| A5 | abstention **0.300**, accounted for per `gap_reason` — the rise is the second practice's three declared-unclaimed criteria, not a criterion answering worse — and swept against `discrepancy_tolerance` |
| A6 | 48 model calls / 39,968 in / 7,688 out / 49.7s across thirteen determinations, from instrumentation |
| A7 | 58 requirements: 56 mapped to a check, 2 declared unclaimed with a decision entry behind each |
| A8 | the failure-modes summary above; full analysis in `docs/spec.md` §10 |
| A9 | zero determinations presented with a criterion in `ERROR` |

### What the second practice measured (T-92, T-93)

v1.2's question is whether the engine is bariatric-shaped, and the first
three rows answer it with numbers rather than with an opinion.

| Figure | Result |
|---|---|
| Predicate kinds the rheumatology tree needed that the engine lacked | **1** of 8 (`medication_value_set_active`) |
| Its criteria evaluated deterministically / declared unclaimed | **2** / **3** — none unclaimed for want of a predicate |
| Engine changes needed to give it patients and rows | **none** — no predicate, step, contract or tree moved |
| Eval rows, and their result | **3** (`RA1`, `RA2`, `RA3`), all `PASS` |
| Model calls spent by those rows | **3** — one replayed verifier call per cited verdict, no extraction at all |
| Verifier claims, both tiers | **33** of 33 accepted, zero verdicts moved between tiers |
| Bariatric verdicts, spans, rows or recordings that moved | **zero** |

Three of five criteria are declared unclaimed, and that ratio is the finding
rather than a shortfall: NYHA class is not in ICD-10, *"untreated"* is a
judgment about the record, and disease activity is a clinical assessment no
diagnosis code grades. A system reporting five deterministic verdicts here
would be reporting three it cannot support. Each is unclaimed because of the
**document or the chart**, never because a predicate is missing — the
distinction `REQ-57` exists to keep, and the one this tree was most able to
blur.

The corpus half came out the same way. Synthea's own rheumatoid arthritis
module supplies the diagnosis and methotrexate and **no biologic or JAK
inhibitor at any population size**, so two of the three charts are generated
patients and the third — the one the policy's combination limitation denies —
is a declared clone of the first carrying one declared prescription, in the
same shape the BMI-boundary observation has been declared since T-41. What a
second practice cost, in the end, was corpus work and not engine work
*(T-93, D113)*.

Two costs are recorded rather than smoothed away. **An eval row with a cited
verdict is a verifier measurement**: a claim digest is the criterion, the
verdict and the sliced quote, so three new `MET` verdicts are three claims
the recording must hold, re-measured on both tiers — v1.2's plan said zero
model calls, which was true of extraction and never true of a cited row.
And a tree declaring **no** note criterion still pays to read a chart's
notes, because the extraction step is unconditional; it changes no verdict,
so it is a cost and not a defect, and it is scheduled where extraction
becomes tree-declared.

**Diagnostic ultrasound is next** — row 4, a MAC document fetched and
compiled, then patients and rows for a `MET`, a `NOT_MET` on a frequency
limit and a `NO_POLICY_FOUND` for an unlisted code. Row 5 closes the version
with the compatibility account: per practice, every criterion classed as
evaluated by an existing predicate kind, by a new one, or unclaimed.

**v1.1 is complete (D107).** Its last row was an entry rather than code: P6's
path for model adjudication, written down and deliberately not taken. **The
second tier landed with T-90 (D106).** The whole corpus was measured a second time on
**Vertex** and committed beside the AI Studio recordings, which did not move;
`eval/report.md` renders the two as columns. Fidelity did not change:
precision, recall, REQ-9 exclusion and field agreement are 1.000 on both
tiers, every span anchors, the verifier accepted the same thirty claims the
recording held at that round with no verdict moving, and 0 of 169, 0 of 165 and 0 of 76 model-emitted character
offsets were usable — the fourth independent reproduction of that finding. The
tool-calling path is where the tier bites: the ADK's injected
`set_model_response` round trip is an AI Studio artifact and disappears on
Vertex's native schema path (tool calls 26 → 12, unescaped spans 4 → 0), while
the token overhead only halves, 4.12x to 2.14x against each tier's own direct
runner. **Cost figures do not transfer between tiers** — the direct runner
spends markedly more input tokens on Vertex for an identical prompt — so only
the within-tier comparisons are quoted. The second note per patient landed
earlier with T-81 (D104): every recording re-measured on a corpus where a
skipped note is reachable, and the direct retrieval-recall figure is now
measured rather than constructed.

**What follows v1.1 is fixed in spec §11 *(D105)*:** two rounds of testing
the rules engine against other practices' coverage rules (rheumatoid
arthritis and ultrasound first; two more, notes included, later), a
medical-history review that suggests ICD codes the chart supports but does
not carry, sessions and a simulated submission path built headless, and a
reviewer's UI as v2.0 over ports that by then already have tests.

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

**What it would take to claim them is written down, and nothing on the roadmap
does (D107).** The criterion that looks like the candidate — a multidisciplinary
evaluation within six months, which the second contractor's policy requires —
turns out to decompose into extraction plus set membership plus a date window,
so building it would add an extractor and leave Python deciding. Claiming model
adjudication needs a criterion whose *predicate* cannot be compiled at all, of
the kind one MAC's policy asks for when it requires a "diligent effort" rather
than a threshold. Three consequences follow, and they are why it stays
unclaimed: a model verdict is not reproducible run to run, which is precisely
what Article II's own test forbids; the deterministic implementation stops
being the regression oracle for a criterion it cannot compute; and the
differential that grades the agentic path could not cover it. The limit is
recorded rather than engineered around.

---

## Repository layout

```
pa_agent/            resolver, criteria, spans, index, anchor, workflow,
                     retrieval, runners, extraction, verifier, reconcile,
                     aggregate, determination, contracts, model_pin, tiers, cli
  agent/             ADK path: extraction_agent, retrieval_agent, tools, bounds
  stores/            policy.py and patient.py — two ports, two planes;
                     __init__.py imports neither, on purpose
data/policies/       seven source documents, four value sets (SNOMED and
                     RxNorm), and three criteria trees — two bariatric
                     (ncd-100.1-jf-v1, ncd-100.1-jjm-v1) and one rheumatology
                     (infliximab-ra-jjm-v1)
data/patients/       eleven Synthea bundles + fourteen synthesized notes, hash-pinned
eval/                cases.json, baseline.json, report.md (generated), and the
                     committed recordings that make replay free — one set per
                     tier, the AI Studio one being what every gate replays
spike/spike_001/     the founding extraction spike — still a gate and a
                     regression corpus; see "What the spike taught"
scripts/             the gates, the measurement scripts, and the corpus tooling
tests/               the suite, including the AST-level pins
docs/                constitution, spec, stories, tasks, decisions
```

---

*Numbers in this README come from instrumentation (Article X) and carry the
ground-truth caveat stated above. Authorship and ownership of this work are
recorded at the git level, which is the only place they were ever asserted
from.*
