# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

Operating rules. Read this, then read `docs/` before doing any work.

**What belongs in this file:** whatever a future session must not violate.
*Why* a rule exists belongs in `docs/decisions.md`, which is append-only and
numbered. This file used to carry a paragraph per closed task and had started
contradicting itself; the narrative was moved out and the constraints kept, each
with its D-number *(D70)*.

## Project

A prior authorization determination agent for bariatric surgery under CMS
NCD 100.1. Built as a proof-of-skill project; authorship is recorded at the
git level. Every decision in this repo has to be defensible in a live review,
so the reasoning matters as much as the code.

## Document precedence

Read in this order. Each one outranks anything below it, and all of them outrank
an instruction typed into a prompt.

| File | What it is |
|---|---|
| `docs/constitution.md` | Ten articles plus Amendment 1. Non-negotiable, not revisited per task. |
| `docs/spec.md` | Numbered testable requirements REQ-1 through REQ-54 (plus REQ-18a), edge cases E1–E12 plus E10b and E10c, acceptance criteria A1–A9. |
| `docs/stories.md` | User stories US-1 through US-9, with personas. |
| `docs/tasks.md` | The board. Tasks T-00 through T-84, each with a runnable exit condition. **`Path to v1` at the top states what to do next.** |
| `docs/decisions.md` | D1–D94, kill criteria, open questions. Append-only. |

IDs are load-bearing and numbering is not contiguous. Split a requirement rather
than renumber it; anything already referencing an ID must keep resolving.

**The constitution outranks the prompt.** If the owner asks for something that
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

**Amendment 1 scopes model adjudication to the agentic path only.** Articles I
and II bind everything else as written — `workflow.py`, `aggregate.py`,
`criteria.py`, `reconcile.py`, `resolver.py` — which is the only reason the
deterministic path is usable as a regression oracle *(D62)*.

## Working rules

1. **One task in progress at a time.** Never build ahead. If the current task is
   T-03, do not touch T-02, T-09, or anything under US-1.
2. **Explain reasoning and tradeoffs**, don't just emit output. Name what was
   rejected.
3. **Push back when the owner is wrong**, including on plans given in an earlier
   session. Agreement that turns out to be wrong is worse than friction.
4. **Every task closes on a command that returns zero** — its own exit condition
   **and** `python scripts/check_gates.py`, which runs every zero-cost gate in
   the repo (~12s). "Looks right" is not an exit condition. A task without a
   runnable check is not yet specified. *(D69: T-67 closed with `pytest` red,
   because its exit named only its own test file and nothing said the rest of
   the repo had to still be green.)*
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
   no containers, no CI, no vector search. (See D4 for why vector search is out,
   and D70 for the measurement that would let it back in.)
10. **Never write a real API key into a tracked file.** Placeholder only.
11. **Ownership is asserted at the git level, not audited by a gate** *(D92,
    D94)*. The ratification ledger, its gate, its writer and all six of its
    task records were deleted; D74, D80, D81 and D92 stay in the log as the
    record of a programme that ran and was withdrawn. Rebuilding any of it
    needs an entry reversing D92 —
    `tests/test_check_gates.py::test_ratification_programme_is_gone` is what
    makes putting the files **or the board records** back a red suite rather
    than a quiet commit.

## Commands

`python` is not on PATH; the tracked venv is at `./venv/bin/python`.

```bash
./venv/bin/python scripts/check_gates.py        # all 10 gates, ~25s. Required at every close.
./venv/bin/python -m pytest -q                  # the suite alone (653 tests, ~18s)
./venv/bin/python -m pytest tests/test_criteria_c.py -q          # one file
./venv/bin/python -m pytest tests/test_criteria_c.py -q -k e5    # one test
```

The ten gates, all zero-cost: `pytest`, then `check_env.py`,
`check_skeleton.py`, `verify_sources.py --offline`, `select_patients.py
--verify`, `spike/spike_001/run.py --verify`, `eval/run_eval.py`,
`eval/run_agentic_eval.py`, `eval/build_report.py --verify` *(D85)*,
`check_req_coverage.py` *(D87)*. **Membership is a
rule, not a taste call** — a
command is a gate iff some task's exit condition names it *and* it spends no
model call and touches no network. Everything else tracked under `scripts/`,
`eval/` and `spike/` sits in `EXCLUDED` with a stated reason, and
`tests/test_check_gates.py` fails on a tracked script in neither list *(D69)*.

Run the system:

```bash
./venv/bin/python -m pa_agent.cli --patient <uuid> --procedure 43775   # a real determination, zero model calls
```

CLI exit codes: `0` an answer, `1` a bad request (unknown patient), `2` an
unbuilt path, `3` a determination aborted over a criterion in `ERROR` — the
criterion id and `error_code` go to stderr, nothing to stdout (REQ-29, D76).

Commands that **spend model calls** and are therefore in no gate:
`scripts/run_extraction.py`, `scripts/run_adk_extraction.py`,
`scripts/run_verifier_measurement.py`, `eval/run_agentic_eval.py --measure`.
Each has a `--rescore` / replay path that re-derives every number from the
committed recording for free — use it.

`README.md` is the project end to end, including A8's *Where this system
degrades* — the failure-modes section is the deliverable, not a formality *(T-23,
D87)*.

## Architecture

Request in, determination out. `pa_agent/cli.py` is the one place a store is
constructed (REQ-41); everything else receives ports.

**Two short circuits, then a fixed graph.** `resolver.py` answers sc1 from
procedure-set membership (`NotCovered` / `NoPolicyFound` / `ResolvedByContractor`
/ `Resolved` — four types, never one type with a field). `determination.py`
answers sc2, the national T2DM-with-BMI-under-35 exclusion. Both spend zero model
calls. Anything surviving both enters `workflow.py`.

**`workflow.py` is plain Python and deliberately not an ADK `Workflow`.** `STEPS`
is a module-level tuple of named callables — gather, extract, criterion_a,
reconcile, criterion_b, qualifying_run, criteria_c, unclaimed, sufficiency,
verify — and a driver walks it and records what it visited. Every criteria
step evaluates what the **tree declares**, not Noridian's seven (D101). An ADK `Workflow` would put `google.adk` on the import
path of every deterministic test, and the three `sys.modules` assertions that
would catch that are the ones that would have to be deleted to allow it. The
graph has one conditional — whether a short circuit fired — and that is a
`return`, not an edge *(D62)*.

**Three ports at the model boundary; the first two are what make the
differential real.**

- `ExtractionRunner` (`runners.py`, REQ-52) — *who reads the note*.
  `DirectExtractionRunner` (raw `google-genai`, D45's measured configuration),
  `AdkExtractionRunner` (`pa_agent/agent/`), `RecordedExtractionRunner` (replays
  T-15's recording, spends nothing — this is why `pytest` and `eval/run_eval.py`
  exercise the whole chain end to end for free).
- `RetrievalPlanner` (`retrieval.py`, D63) — *who decides what to fetch*.
  `FixedRetrievalPlanner` (three store reads in a fixed order) and
  `AgenticRetrievalPlanner` (the model chooses). Everything downstream cannot
  tell which planner ran, which is what makes D64's comparison a comparison.
- `VerifierRunner` (`verifier.py`, Article V, D78) — *who checks the
  citations*. `LiveVerifierRunner`, `RecordedVerifierRunner` (replays T-17's
  recording at `eval/verifier/results.json`, keyed by claim digest — a miss
  raises, never defaults), and a raising `NullVerifierRunner`. `("verify",
  step_verify)` is the last `STEPS` entry (T-86's `sufficiency` precedes it, D99): cited verdicts only, first
  rejection → `INSUFFICIENT_EVIDENCE`/`VERIFIER_REJECTED`, no retry, and the
  determination still emits (REQ-18).

**`build_result()` is the trust boundary.** Every runner returns through it. ADK
output is untrusted model output and there is no private route to a `WmEvent`.

**Two storage ports, two planes** (`stores/policy.py`, `stores/patient.py`,
REQ-41, Article VI). `stores/__init__.py` imports neither submodule on purpose —
a package-level re-export would be the module that reaches both planes.
Production is a second adapter, which is the whole reason the ports exist *(D25)*.

**Adjudication is seven criteria.** (a) BMI and (b) comorbidity read structured
FHIR (`criteria.py`); c1–c5 are pure predicates over extracted `wm_events`. c3
computes the qualifying run **once** and c2, c4 and c5 scope to it. `reconcile.py`
then runs REQ-34 across the structured and note BMIs. `aggregate.py` parses the
policy's own `decision_expression` — parsed, never `eval()`'d and never
hardcoded as `all(...)`.

**Evidence is mechanical throughout.** `anchor.py` locates what the model quoted,
`index.py` resolves an id to text and slices, `spans.py` validates or raises with
a classified reason. A locator must not be able to launder its bugs through the
validator, which is why anchoring and validation are separate modules.

## Invariants a fresh session will break silently

The dangerous set. Each of these can be violated while **every test keeps
passing**, because the tests are written in terms of the thing that broke.

- **Never give the extraction agent structured observations** *(D62)*. Hand it
  the structured BMI while asking for the note's and T-33's two independent
  readings stop being two. E10b would quietly start agreeing, and the tests —
  which compare those two readings — would pass *because* the system broke.
  `EXTRACTION_ALLOWLIST` is `("read_note",)`.
- **The two tool allowlists are disjoint, not nested** *(D66)*. The extractor has
  no route to a second document at all: not a bundle, not another patient's note.
- **The accept-all verifier lives in `tests/conftest.py` and nowhere under
  `pa_agent/`** *(D78)*. An accept-all implementation importable from the
  package is Article V silently skipped, and every downstream test would agree
  with it. `RecordedVerifierRunner` raises on an unrecorded claim for the same
  reason (D31's shape).
- **A verifier claim is (criterion, verdict, quotes) — no `as_of`, no dates
  beyond what the quotes contain** *(D78)*. The field rode along for three
  prompt versions and date-bound every claim digest, which broke the CLI
  default (as-of today) against a recording measured at the harness clock.
  The verifier is barred from date and count arithmetic outright: v1 and v2
  measured false rejections on every shortfall-type `NOT_MET`, because the
  shortfall is Article II's arithmetic over a chart Article V hides.
- **Never return `None` or `[]` from an unimplemented store half or planner**
  *(D31, D39, D63)*. A policy store returning `None` reports `NO_POLICY_FOUND`
  for all of Medicare; a patient store returning `[]` manufactures E7 for every
  patient; a planner returning nothing makes every chart look empty. All three
  are well-formed answers that every downstream test agrees with. Raise, and name
  the task.
- **The tool payload is never the evidence path** *(D66)*. `gather()` re-reads
  observations, conditions and the value set from the port, which is what makes
  `MAX_ROWS` truncation a cost control rather than a quiet second filter free to
  disagree with criterion (a).
- **`NOT_MET` / `INSUFFICIENT_EVIDENCE` / `ERROR` never collapse** (Article IV,
  D7, D9). An abstention cites nothing and carries a `gap_reason`; a `MET` or
  `NOT_MET` carries a span and no reason. Both directions are validators. **A
  retrieval fault is an `ERROR` on every criterion, never an abstention** *(D90)*
  — "the system did not look" and "the chart does not say" are the two things
  `RetrievalError` exists to keep apart, at both ends of the wire.
- **Criteria trees never move into a store's write path** (Article VII, D25).
  Git is the source of truth; a store may serve a deploy-time read-only
  projection.
- **A changed call configuration is a new measurement, never a re-run** *(D45)*.
  A changed tool declaration is a changed prompt *(D64, D66)*; a changed SDK is a
  changed measurement *(D71)*; and **the tier can change the prompt too**, not just
  the endpoint — on AI Studio `output_schema` + `tools` becomes an injected
  `SetModelResponseTool` *(D62, measured in D71)*. The ADK path has its own numbers
  now and D45's still may not be quoted for it.
- **A recording's free half drifts, and only `--rescore` finds out** *(D91)*.
  `eval/agentic/results.json`'s oracle columns are re-derivable for nothing, so
  nothing re-derives them — they sat two tasks stale, describing a path with no
  verifier in it, while every gate stayed green because `verify()` checks the
  recording's internal coherence and not its agreement with today's code. The
  visible symptom was a cost *ratio*: D64's 13.9x became 4.3x with no change to
  retrieval, because Article V's verifier entered the shared denominator.
  **Quote the delta beside the ratio** — 24 model calls and 84,925 input tokens
  the fixed planner never spent — because the delta is the figure that does not
  move when the denominator does.
- **Never make a gate call a model** *(D45)*. Measurement scripts spend the
  calls; `pytest` re-reads the recording, re-hashes every note, re-validates
  every span and checks the recorded model is the pin.
- **Count every model turn, not just the first** *(D71, and commit `89d2cd1`
  before it)*. A tool round trip is two LLM calls. A note's singular `metrics` is
  turn one; `trace["metrics"]` is all of them. Summing the wrong one understated
  T-63's output tokens 12.1x and inverted the comparison's sign, using figures
  that were each individually real.
- **`select_patients.py --generate` is not byte-stable, so a regeneration is
  never adopted wholesale** *(D73)*. Synthea reproduced five of six bundles
  byte-identical and one with different bytes under an identical command. The
  committed corpus is pinned by the manifest's hashes; after any regeneration,
  a base bundle whose hash drifts is restored from git. Adopting it instead
  rewrites the manifest, and every gate then agrees with the new corpus —
  while recorded spans still point into the old bytes.
- **`pa_agent/model_pin.py` is the only tracked Python that may name a model**
  *(D20)*, test files included — that rule is what left the suite red in T-67.
- **Spans are located by searching the model's verbatim quote**, exact first then
  whitespace-normalized, always recording raw offsets *(D18)*. The model's own
  offsets are unusable: 0 of 80 in the spike, 0 of 171 in T-15.
- **`BLOCKED` is not `FAIL`, and `skipped` is not `failed`** *(D27, D67)*.
  "Answered wrongly" and "the component does not exist yet" have different next
  actions and only one names a task.
- **The eval gate is a baseline diff.** Drift in **either** direction fails, so a
  case that starts passing is acknowledged with `--update-baseline` and a commit
  *(D27)*.

## Verified environment facts

Established by installing the package and reading the installed API, not from
documentation or memory. **Most ADK material online is 1.x and will mislead
you.** Do not override these from prior knowledge.

- `google-adk` installs at **2.8.0**. Pin exactly: `google-adk==2.8.0`.
- **Python 3.12** is the target. `python3.12` is at `/opt/homebrew/bin/python3.12`.
  The `venv/` in the working tree is **Python 3.12.14** and matches.
- `requirements.txt` is exact pins that follow the **installed** set, because
  that is the environment every recorded number was produced by. Verified by
  `scripts/check_env.py`, which parses every tracked `.py` with `ast` and fails
  on an undeclared import, a stale pin, a range, or an import it cannot map. It
  compares file to environment and **cannot tell you the environment is
  correct** *(D49)*.
- Top-level API surface: `Agent`, `Context`, `Event`, `Runner`, `Workflow`.
- `Workflow` is a Pydantic model. Its `edges` field is a **static list of edges
  supplied at construction**. Other fields: `retry_config`, `max_concurrency`,
  `state_schema`, `input_schema`, `output_schema`, `timeout`.
- `edges` is `list[EdgeItem]` where `EdgeItem = Edge | tuple[ChainElement, ...]`
  and a `RoutingMap` inside `ChainElement` carries conditional routing (*corrected
  in D62*). **Keying a branch off model output violates Article I. Never do
  this.** Branch keys come from deterministic Python values only.
- `SequentialAgent`, `ParallelAgent` and `LoopAgent` are all **deprecated** in
  2.8.0 in favour of `Workflow`. `LlmAgent` is itself a `BaseNode`.
- `output_schema` and `tools` work **together**, but natively only on Vertex:
  `models/_capabilities.py` gates `output_schema_and_tools` on the Vertex
  variant, so on AI Studio ADK injects a `SetModelResponseTool` and an extra
  instruction instead. **The tier changes the prompt, not just the endpoint**
  *(D62)*.
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
  `pa_agent/agent/` was manual *(D16)*.
- `adk web [AGENTS_DIR]` treats each **subdirectory** of `AGENTS_DIR` as one app,
  so `adk web pa_agent` serves the app named `agent`.
- `GET /` on `adk web` returns **307** to `/dev-ui/`, not 200. `GET /list-apps`
  returns 200 with a JSON array of discovered app names *(D16)*.

Per D5: develop against the AI Studio free tier, run final evals and any demo
through Vertex, because Vertex does not train on submitted data. **D19's and
D45's numbers are AI Studio numbers**; a Vertex run of the same corpus is a new
measurement, not a confirmation.

## Method note: mutation testing

Every close in this repo mutation-tests its own gate. Three things a harness gets
wrong silently:

- **Clear `__pycache__` after restoring.** A same-length mutation restored within
  the same second leaves Python's bytecode cache looking valid, and a "passing"
  suite can be running the mutant.
- **Run pytest with `--color=no`.** With `-q`, an ANSI escape prefixes `FAILED`
  lines, so a `^FAILED` scan reports every mutation as surviving — six false
  survivors in T-68's close *(D68)*.
- **A mutation that hangs is not a mutation that was caught.** Deleting T-69's
  recursion guard made the suite spawn itself and the harness returned no exit
  code at all, which is why that guard is asserted by parsing rather than by
  spawning *(D69)*.

Related: **when a behavioural test cannot catch a mutation, parse the AST
instead.** A resolver that branches on an id's shape and *then* falls through to
the record answers identically on every input the corpus can produce *(D65)*; a
label check that falls through to the port answers identically on all eleven
notes *(D67)*. Both are pinned by parsing.

## Current state

**66 of 67 tasks closed, 1 open. All 10 gates green**
(`check_gates.py`, ~25s, 766 tests across 31 files). IDs run to T-87, but
numbering is not contiguous and D92 and D94 deleted six records between them,
so the highest id is well above the count.

Delivered: **US-1 through US-7 and US-9**, and **acceptance gates A1–A9 all
hold**. `python -m pa_agent.cli --patient
<uuid> --procedure 43775` prints a real determination — seven criterion
verdicts, spans that slice back, a gap list and Article X's counters — for zero
model calls, because the default extraction runner replays T-15's recording.
The eval set is full (T-21, D75): `eval/cases.json` holds fifteen labeled rows
— spec §6's fourteen plus `NP1`, the
`NO_POLICY_FOUND` row outside §6 — all `PASS`, criterion-scoped, with every
cited span validated by the scorer (A3). Case rows may carry their own
`as_of`, and E2's does: sc2 fires only for nationally covered codes on
in-window evidence *(D41)*, so E2 runs 43644 at 2024-12-01 while E7 reads the
same chart at the harness clock. US-9 closed with T-29 and T-30 (D76, D77):
a fault is a criterion's `ERROR`, the abort is `DeterminationAborted`, and the
eval harness classifies it as its fourth status — never `FAIL`, never an
abstention; the reported abstention rate counts an `ERROR` in neither its
numerator nor its denominator (REQ-28). US-6 closed with T-17 (D78): every
cited verdict passes through Article V's blind verifier, every gate replays
the committed 27-claim recording for zero calls, and the four-run measurement
history — two false-rejection rounds forcing the verdict-asymmetry rule, then
27/27 twice — is D78's substance. US-7's measurements are in `eval/report.md`
(T-22, T-28, D85), generated and gate-verified: **A2 precision 1.000 on `MET`
against a 0.611 base rate** (the always-`MET` baseline scores exactly the base
rate, which is the comparison A2 asks for), **A3 zero invalid `MET` spans over
82 checked**, **A5 abstention 0.200** with the per-`gap_reason` account and
D82's tolerance sweep, and **A6 33 model calls / 27,175 input / 5,723 output /
36.1s across nine determinations** — replayed instrumentation, not the replay's
own clock.

Open: **T-81 alone**. v1 is complete — **A1–A9 all
hold** — and what remains is spec §10's list of known limits, P1–P8, which
**D97 sequenced into one task each**: T-85 through T-90 plus T-81, in
`docs/tasks.md`'s `Path to v2` table. T-85 (D98) put the anchoring account in
`eval/report.md`; T-86 (D99) made every `NOT_MET` carry a structured
`shortfall` and added the `sufficiency` step — the ninth `STEPS` entry, between
`criteria_c` and `verify` — which re-runs the predicate over only the cited
evidence and maps a mismatch to `ERROR`, never an abstention. T-87 (D100,
D101) added the second jurisdiction: `resolve(code, state)`, Palmetto GBA's
tree `ncd-100.1-jjm-v1`, the fifth resolver type `NoJurisdictionTree`, and
the graph generalized to what a tree declares. T-88 is next. Free tasks first, the runner change
(T-89) before the corpus grows (T-81), the Vertex measurement (T-90) last.
Read the table rather than this paragraph *(D70, D72, D97)*.

**The ratification programme is deleted** *(T-82, D92; finished by T-84, D94)*.
Ownership of this work is a git-level fact and needed no ledger to assert it, so
`docs/ratifications.json`, `check_ownership.py`, `ratify.py` and all six of its
task records are gone. **D74, D80, D81 and D92 stay in the log** — it is
append-only and a reversal is a new entry, never a deletion — and `Path to v1`'s
rows 1, 2 and 6 are what the six ids now resolve into. D92 kept T-73, T-74 and
T-79 as board records while deleting their three siblings; D94 ended that split,
on the ground that one programme recorded two ways at once costs a reader more
than it tells them.
`tests/test_check_gates.py::test_ratification_programme_is_gone` makes putting
any of it back — files or records — a red suite rather than a quiet commit.

**The caveat that travelled with the numbers was reworded by D96.** T-78
would have added a formal review stamp to the eval labels; deleting it
removed paperwork. D96 reframed the rest: the ground truth is a working first
draft, drafted alongside the system, and further label review rides with
later corpus expansion (T-81 and beyond) instead of standing as an open
warning in every document. Spec §10's P4 records the reword; `README.md` and
`eval/report.md` state the scope without the injunction *(D92, D96)*.

Worth knowing before a review: **REQ-44/REQ-47 are unclaimed on purpose** —
Amendment 1 reserves the entire decision procedure to Python, so there is no
verdict a model could determine without doing something reserved. They are
declared in spec §5's *Unclaimed in v1* table, which is what makes A7
satisfiable *(D63, D70)*.

### Domain facts that took work to establish

- **The policy corpus is five documents and two jurisdictions** *(D21, D29,
  D100, D101)*: `ncd_100_1` (national), `a53028` (Noridian, A/B MAC,
  **Jurisdiction F**), `r931cp` (CMS Pub. 100-04 Transmittal 931), and since
  T-87 `l34576` and `a56852` (Palmetto GBA, **Jurisdictions J and M**). **NCD
  100.1 quantifies nothing** — no months, no visit counts, no recency. Every
  constant in a tree comes from its MAC's document, so this system determines
  coverage *as that MAC would*, and a request resolves by procedure code
  **and state** — a state neither tree serves is `NO_JURISDICTION_TREE`
  (REQ-55), never a default. Say that plainly in a review rather than calling
  the thresholds CMS's.
- **The two trees differ in shape, not only in constants** *(D101)*. Palmetto's
  L34576 states no run length (no `c3`), requires *weight* rather than BMI
  monthly, and adds a multidisciplinary evaluation; `c4` and `d` are declared
  `evaluation: "unclaimed"` and the graph abstains on them with
  `NOT_EVALUATED_BY_THIS_SYSTEM` — never omits them. `qualifying_run` admits an
  explicit `None` run length only because the tree declares none; the argument
  stays required. The 43775 binding cites A53028 because A56852's CPT table
  sits behind the AMA licence modal and the extracted text names no code.
- **`r931cp` is citable for code bindings only, never coverage claims** — its
  coverage content predates the 2012 LSG delegation *(D29)*.
- **A procedure code carries two citations of different classes** *(D28)*: "this
  procedure is non-covered" and "this code denotes that procedure" are different
  claims with different sources. Merging them into one `source` field is the move
  D28 refused.
- **43842, not 43775, is the non-covered case** *(D22, D28)*. The NCD non-covers
  laparoscopic sleeve gastrectomy only *"prior to June 27, 2012"*, then delegates
  to the MACs; 43775 is therefore contractor-determined and lands in a **covered**
  case. 43842 (open vertical banded gastroplasty) is named non-covered for all
  beneficiaries with no date qualifier.
- **A53028's facility ICD-10-PCS lists are not lookup keys** *(D30)*. They
  overlap across procedures in the source itself, so a facility code does not
  denote one procedure. Marked `identity: false`. Do not promote them.
- **c5 is a rate, not a count** *(D24)*. It carries c4's `documentation_rate`
  from the same span.
- **The qualifying run is selected jointly, and the selector's constants are
  required arguments** *(D84)*. Among a chart's maximal runs, prefer one
  satisfying c3's length *and* c2's recency; fall back to longest. `qualifying_run`
  cannot be called without `min_consecutive_months`, `recency_window_months` and
  `as_of` — an optional window defaulting to "no preference" would reinstate the
  pre-T-42 false `NOT_MET` with every test agreeing. `evaluate_c3` raises rather
  than selecting its own run.
- **The note's current BMI is a different fact from `WmEvent.bmi`** *(D50)*.
  Criterion (a) asks what the patient's BMI is now; c4 asks what each month
  documented.
- **Two constants are decisions, not spans**, recorded with a name and a date:
  criterion (a)'s 12-month lookback *(D40)* and `discrepancy_tolerance` at 1.0
  BMI points *(D51)*. The tree carries no provisional constant, and the count is
  pinned at zero so a new one is a visible diff.
- **The eval ground truth is a working first draft, drafted alongside the
  system it grades** *(D19, D42; reworded in D96)*. Mechanical safeguards are
  in place, and on a corpus this small a perfect score still means only that
  the approach does not obviously fail.
- **The measured result so far** *(D64, D66, re-measured in D91)*:
  model-directed retrieval agrees with the deterministic oracle on 6/6 outcomes
  and 42/42 criteria, 80/80 spans valid, zero errors — for **24 model calls and
  84,925 input tokens** the fixed planner did not spend, 4.3x its end-to-end
  input tokens. **Quote the delta beside the ratio**: the fixed planner makes no
  model call, so the ratio's denominator is the replayed extraction-plus-verifier
  cost shared by both sides, and it moved from D64's 13.9x to 4.3x when Article
  V's verifier entered that denominator — the delta is the figure that does not
  move *(D91)*. Read the aggregate and the spread, never one patient's ratio.
- **Planner recall is 1.000 over 25 citing cases, on both the cited and the
  gathered figure** *(T-27/T-80, D86/D91)*. The gathered figure is REQ-25's and
  it is **1.000 by construction** — one note per patient, a planner that raises
  rather than returning less, structured facts re-read from the port — so the
  *cited* figure beside it is the one that can still move. It settled D86's open
  question: every patient gathered two documents and two cite only one, so a
  non-cited document here was **gathered and uncitable, never skipped**. T-81
  (a second note per patient) is what would let the direct figure fall.

## Repo layout

```
pa_agent/            resolver, criteria, spans, index, anchor, workflow,
                     retrieval, runners, extraction, verifier, reconcile,
                     aggregate, determination, contracts, model_pin, cli
  agent/             ADK: extraction_agent, retrieval_agent, patient_tools,
                     policy_tools, tool_bounds, agent (adk web entry point)
  stores/            policy.py and patient.py — the two ports and their
                     file-backed adapters. __init__ imports neither.
data/policies/
  source/            ncd_100_1, a53028, r931cp, l34576, a56852 + sources.json,
                     answers.json (q1–q6)
  value_sets/        obesity_comorbidities.json (SNOMED)
  ncd_100_1_jf.json  Noridian JF's tree, policy_version_id ncd-100.1-jf-v1
  ncd_100_1_jjm.json Palmetto JJ/JM's tree, ncd-100.1-jjm-v1 (T-87, D101)
data/patients/
  manifest.json      the corpus pin — every bundle's hash (D73). It is **here,
                     not under bundles/**; select_patients.py --verify reads it
  bundles/           seven Synthea v4.0.0 bundles — six from the base seed and
                     one carrying the declared synthetic BMI-35.0 observation
                     (T-41, D73); E12's patient is note-free by declaration
  notes/             six chart notes, one per <patient_id>/chart_note.txt,
                     plus notes/manifest.json — a second, separate manifest
  work/              gitignored: the Synthea jar and the full 200-patient run
eval/
  run_eval.py        the baseline diff (T-10). Drift in **either** direction
                     fails; a case that starts passing is adopted with
                     --update-baseline and a commit (D27)
  run_agentic_eval.py  the fixed-vs-agentic differential (T-61). Bare is the
                     gate; --measure spends model calls, --rescore re-derives
                     the free half from the recording (D64, D91)
  build_report.py    T-22/T-28/T-27's generator; --verify is the ninth gate (D85)
  cases.json         the eval set — 15 labeled rows (§6's 14 + NP1; D75)
  baseline.json      what run_eval.py diffs against
  report.md          T-22/T-28's metrics report — generated, never hand-edited
  manifests/         T-06's ground truth — the system under test never reads it
  extraction/        results.json (T-15) plus adk_results_inline.json and
                     adk_results_tool_fetch.json — T-63's two, one per mode (D68)
  agentic/           results.json — T-61's recording, carrying since T-80
                     the bundle each side *gathered* beside what it cited (D91)
  verifier/          results.json — T-17's 27-claim recording (D78)
spike/spike_001/     notes/, labels.json, results.json, run.py — five notes,
                     no patient
scripts/             check_gates, check_env, check_skeleton,
                     check_req_coverage, verify_sources,
                     select_patients, synthesize_notes, run_extraction,
                     run_adk_extraction, run_verifier_measurement
tests/               31 files, 653 tests
docs/                constitution, spec, stories, tasks, decisions — exactly
                     the five of the precedence table and nothing else (D93
                     deleted the sixth, a plan doc that governed nothing and
                     contradicted the board)
```
