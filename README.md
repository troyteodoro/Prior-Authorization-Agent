# Prior Authorization Determination Agent

A prior authorization determination system for bariatric surgery under CMS
NCD 100.1, as Noridian Jurisdiction F implements it. Given a patient record and
a requested procedure code, it produces a reviewable determination: a verdict
for every criterion in the governing policy, a citation for every verdict, and
a gap list naming exactly what the chart is missing.

The system does not submit, does not decide, and does not adjudicate on a
payer's behalf. It prepares a packet for a human specialist — and the primary
output is the **gap list**, not the verdict. The highest-value sentence the
system produces is "criterion c3 is not supported by this chart," because that
is actionable *before* submission. An overall approve/deny is a summary of the
criterion verdicts and carries less information than they do.

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
reconciliation step (REQ-34) then cross-checks the structured BMI against the
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
unusable (0 of 80 correct in the spike; 0 of 171 in the first full run), so
spans are located by searching for the model's verbatim quote — exact match
first, then whitespace-normalized — always recording raw offsets. Locating
(`anchor.py`), resolving (`index.py`), and validating (`spans.py`) are separate
modules so a locator cannot launder its bugs through the validator.

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

**The measured result so far:** model-directed retrieval agrees with the
deterministic oracle on **6/6 outcomes and 42/42 criterion verdicts, with
80/80 spans valid and zero errors — at 13.9× the input tokens.** Read the
aggregate and the spread, never one patient's ratio.

> **Caveat that must travel with every number in this repo:** the eval ground
> truth was authored by the agent that built the system it grades. Structural
> mitigations exist (spans validated mechanically by the scorer, an owner
> ratification pass on the labels is on the board), but a perfect score means
> only that the approach does not obviously fail. Do not quote these numbers
> without this sentence.

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

## Running it

`python` is not assumed on PATH; the tracked venv is Python 3.12.

```bash
# All nine zero-cost gates (~13s). Required green at every task close.
./venv/bin/python scripts/check_gates.py

# The test suite alone (604 tests, ~9s)
./venv/bin/python -m pytest -q

# A real determination, zero model calls
./venv/bin/python -m pa_agent.cli --patient <uuid> --procedure 43775
```

CLI exit codes: `0` an answer (including honest abstentions — Article IV),
`1` a bad request, `2` an unbuilt path, `3` a determination aborted because a
criterion is in `ERROR` — the criterion id and error code go to stderr,
nothing to stdout.

**Nothing in the gates spends a model call or touches the network.** That is a
membership rule, not a taste call: a command is a gate iff some task's exit
condition names it *and* it costs nothing, and a test fails if a tracked
script is in neither the gate list nor the exclusion list with a stated
reason. The scripts that do spend model calls (extraction, agentic
measurement, verifier measurement) each have a `--rescore`/replay path that
re-derives every number from the committed recording for free.

The eval harness (`eval/run_eval.py`) scores fifteen labeled cases — the
spec's fourteen edge-case rows plus one `NO_POLICY_FOUND` row — with every
cited span re-validated by the scorer. The gate is a **baseline diff**: drift
in either direction fails, so a case that *starts* passing must be
acknowledged with `--update-baseline` and a commit. Four statuses, never
collapsed: `PASS`, `FAIL`, `BLOCKED` (the component does not exist yet — that
names a task, not a bug), and `ERROR` (counted in neither the abstention
rate's numerator nor its denominator).

---

## How the project is governed

The repo is run under a document hierarchy, strictest first, and all of it
outranks any instruction typed into a prompt:

| File | Role |
|---|---|
| `docs/constitution.md` | Ten articles plus Amendment 1. Never silently edited; amendments are appended with a date and reason. |
| `docs/spec.md` | Numbered testable requirements (REQ-1…54), edge cases (E1–E12), acceptance criteria (A1–A9). |
| `docs/stories.md` | User stories US-1…US-9, with personas. |
| `docs/tasks.md` | The board. Every task has a runnable exit condition; `Path to v1` at the top states what to do next. |
| `docs/decisions.md` | Append-only, numbered decision log. Every entry names the rejected alternative and its reversal condition. |
| `docs/ratifications.json` | The ownership ledger: an agent may write `proposed` and nothing else; `ratified`/`amended`/`overruled` are the human owner's edits, enforced by a gate. |

Working rules that follow from it: one task in progress at a time, stories
close vertically (four of five tasks done has delivered nothing), discovered
work becomes a new numbered task, timebox overruns get a decision entry naming
what broke, and no infrastructure the project has not earned — no cloud setup,
no containers, no CI, no vector search until a measurement says retrieval
precision demands it.

**Every close mutation-tests its own gate.** When a behavioural test cannot
catch a mutation — a resolver that branches on an id's shape and then falls
through to the same answer, a guard whose deletion makes the suite hang rather
than fail — the invariant is pinned by parsing the AST instead. The method
notes also record the harness bugs found the hard way: stale `__pycache__`
running the mutant after restore, ANSI color codes defeating `^FAILED` scans,
and a hung mutation being counted as caught.

---

## Status and the road to v1

**51 of 64 tasks closed; all nine gates green.** Delivered: US-1 through
US-6 and US-9 — instant screening of non-covered procedures, cited structured
criteria, the categorical exclusion, note-only criteria with two independent
BMI readings, the gap list, the blind verifier, and full `ERROR`-state
accounting.

Open, in order:

1. **Ratification** — the human owner ratifies the load-bearing decisions,
   the board, and (as a review-after-the-fact) the eval labels. This is what
   retires the self-graded-ground-truth caveat above from "structural
   mitigation" to "adjudicated."
2. **Article VI enforcement as a gate** (plane separation, REQ-33).
3. **The report chain** — cost/latency aggregation, `eval/report.md` with
   per-criterion precision *next to its base rate and an always-`MET`
   baseline*, and finally the REQ-coverage gate: a script proving every
   requirement in the spec maps to a passing check or to an explicitly
   declared *Unclaimed in v1* entry backed by a decision log entry. A
   requirement in neither fails the build.

Two requirements are **unclaimed on purpose**: model-performed adjudication
(REQ-44/REQ-47) is reserved out of v1 because Amendment 1 keeps the entire
decision procedure in Python — there is no verdict a model could determine
without doing something reserved. Declaring that explicitly, rather than
quietly not doing it, is what makes the coverage gate satisfiable.

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
eval/                cases.json, baseline.json, and the committed recordings
                     (extraction, agentic, verifier) that make replay free
spike/spike_001/     the throwaway span-anchoring spike that killed
                     model-reported offsets
scripts/             the gates, the measurement scripts, and the corpus tooling
tests/               the suite (604 tests), including the AST-level pins
docs/                constitution, spec, stories, tasks, decisions,
                     ratifications
```

---

*Numbers in this README come from instrumentation (Article X) and carry the
ground-truth caveat stated above. Authorship is recorded at the git level.*
