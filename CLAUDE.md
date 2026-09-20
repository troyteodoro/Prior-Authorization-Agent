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
| `docs/spec.md` | Numbered testable requirements REQ-1 through REQ-60 (plus REQ-18a and REQ-34a), edge cases E1–E13 plus E10b and E10c, acceptance criteria A1–A9. §11 is the versions after v1, with the requirements each will mint — statements, not ids, until **the task that checks one** opens *(D105, D109)*. |
| `docs/stories.md` | User stories US-1 through US-9, with personas; US-10 through US-15 are the roadmap's, one per version *(D105)*. |
| `docs/tasks.md` | The board. Task records T-00 through T-92, each with a runnable exit condition; T-93 through T-125 are reserved rows whose records are written when they open. **`Path to v1` at the top states what to do next; `Roadmap after v1.1` states the versions that follow.** |
| `docs/decisions.md` | D1–D112, kill criteria, open questions. Append-only. |

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
12. **Before a task closes, the documents must agree** *(D108)*. The exit
    condition and `check_gates.py` are not the whole close: figures copied into
    `README.md` and `CLAUDE.md` go stale silently, and A2, A5 and A6 sat wrong
    in this file for two whole tasks with every gate green. **The generated
    artifact owns the figure and prose is a copy** — `eval/report.md` owns every
    measured number, `docs/tasks.md` owns the counts, the suite owns its own
    size. Re-derive each copy from its owner, never the reverse.
    `tests/test_docs_consistency.py` checks the ones that actually drifted, so
    a stale copy is a red suite; the ones it does not cover are still yours to
    check. **The exception, and it is not optional:** figures inside *closed
    task records* in `docs/tasks.md` and anything in `docs/decisions.md` are
    **never** updated — they record what was true at that close, the log is
    append-only, and a reversal is a new entry.


## Commands

`python` is not on PATH; the tracked venv is at `./venv/bin/python`.

```bash
./venv/bin/python scripts/check_gates.py        # all 10 gates, ~25s. Required at every close.
./venv/bin/python -m pytest -q                  # the suite alone, ~40s
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

`--tier {ai_studio,vertex}` reaches the four measurement scripts and the CLI,
defaulting to the development tier so every existing invocation is unchanged;
the output *and* `--rescore` paths are routed per tier, so a Vertex run cannot
overwrite the AI Studio recordings the gates read *(T-90, D106)*.

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
step evaluates what the **tree declares** (D101), and since T-91 it dispatches
on the **kind** each criterion declares rather than on its id (D110). The step
names are still bariatric-shaped and deliberately unchanged: they are the
graph's, no step's identity depends on a tree, and renaming them churns
recordings for nothing. An ADK `Workflow` would put `google.adk` on the import
path of every deterministic test, and the three `sys.modules` assertions that
would catch that are the ones that would have to be deleted to allow it. The
graph has one conditional — whether a short circuit fired — and that is a
`return`, not an edge *(D62)*.

**Three ports at the model boundary; the first two are what make the
differential real.**

- `ExtractionRunner` (`runners.py`, REQ-52) — *who reads the note*.
  `DirectExtractionRunner` (raw `google-genai`, D103's measured configuration —
  D45's plus the re-ask), `AdkExtractionRunner` (`pa_agent/agent/`),
  `RecordedExtractionRunner` (replays the direct recording, trace and all,
  spends nothing — this is why `pytest` and `eval/run_eval.py` exercise the
  whole chain end to end for free). Both live runners walk
  `extract_with_reask`: extract, then at most `REASK_ROUNDS` re-asks for the
  verbatim text of what the anchorer refused (T-89).
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

**`pa_agent/tiers.py` is the one place a client is built** *(T-90, D106)*.
`client_for(tier)` sets `GOOGLE_GENAI_USE_ENTERPRISE` and constructs the client
in one call, verifies what it built, and raises rather than defaulting;
`tier_of(client)` reads the tier back for the recording. Every runner still
takes an **injected** client and names no tier, which is what let the second
tier land without touching them.

**`build_result()` is the trust boundary.** Every runner returns through it. ADK
output is untrusted model output and there is no private route to a `WmEvent`.

**Two storage ports, two planes** (`stores/policy.py`, `stores/patient.py`,
REQ-41, Article VI). The patient port serves observations, conditions,
**medications** (T-92), notes, the jurisdiction state and documents; the
policy port serves resolution, trees, documents and value sets. `stores/__init__.py` imports neither submodule on purpose —
a package-level re-export would be the module that reaches both planes.
Production is a second adapter, which is the whole reason the ports exist *(D25)*.

**Adjudication is eight predicate kinds and two exclusion kinds.**
`PredicateKind` (contracts) is the closed vocabulary, `criteria.PREDICATES`
maps each kind to a binder naming the inputs its predicate receives, and
`workflow.STEP_KINDS` assigns each kind to the step that evaluates it — a
partition, checked (T-91, D110). `ExclusionKind` and `criteria.EXCLUSIONS` are
the same shape for categorical exclusions (T-92, D111). Under both NCD 100.1
trees that resolves to (a) BMI and (b) comorbidity over structured FHIR and
c1–c5 over extracted `wm_events`; under `infliximab-ra-jjm-v1` it resolves to
(a) a diagnosis set and (b) a medication set, and the letters are labels: the
`kind` chooses the arithmetic. The run-length criterion computes the
qualifying run **once** and every criterion whose `scoped_to` names it scopes
to that run. `reconcile.py`
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

- **Every note-level BMI is reconciled; never select one** *(REQ-34a, D104)*.
  `step_extract` carries every anchored current BMI in `WorkflowState.note_bmis`
  and `reconcile_bmi` compares each to the structured value independently. It
  was "first note wins" while the corpus had one note per patient; with two,
  that is store order deciding a threshold question, and a second note that
  straddles 35.0 would be hidden by the first that agrees.
- **A fact belongs to exactly one document** *(D104)*. Every encounter,
  assertion and trap in a fact manifest carries a scalar `document`; the
  synthesizer renders it there and nowhere else, and `tests/test_notes.py`
  scans each document for only its own dates. A fact rendered twice inflates
  c1's count and cites one visit from two places, with every verdict test
  agreeing.
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
- **The re-ask writes quote fields at the paths Python named, and nothing
  else; a failed re-ask is recorded, never raised** *(T-89, D103)*. The
  targets come from `build_result`'s drops, the patch is applied only there,
  and the anchorer admits or drops the answer exactly as it did the first —
  the model never decides whether a citation stands. Letting the re-ask raise
  would send a successful extraction through the attempt budget and report
  "the system did not look" for a note it read (D90).
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
- **A value set's system is declared, and membership is tested inside it**
  *(REQ-59, T-92, D111)*. Conditions are SNOMED and medications are RxNorm, so
  a bare code comparison is two vocabularies colliding on short numeric
  strings. `CodedValueSet.admits(code, system)` is the one comparison; a
  resource declaring no system is not a member; a file whose entries are not
  all in its declared system raises at load. Measured before the comparison
  was narrowed: 419 of 421 conditions in the committed bundles are SNOMED and
  the other two are resolved dental ICD-10 codes outside every set, so nothing
  moved — which is why this could have been got wrong silently.
- **An exclusion that does not fire produces nothing, and never a `MET`**
  *(REQ-60, T-92, D111)*. A categorical exclusion is not a criterion: written
  as one, "no excluded drug on this chart" would have to answer `MET` with no
  span, and REQ-5 refuses that because there is no span for an absence. It
  fires citing what fired it, or it is silent. `ExclusionKind` is dispatched
  the way `PredicateKind` is, and an unimplemented kind raises at load — an
  exclusion silently skipped approves past a denial the policy states.
- **A state is served by one tree *per practice*, not by one tree** *(T-92,
  D111)*. Palmetto GBA serves the same seven states for bariatric surgery and
  for infliximab. The collision that matters is a **code** bound for one state
  by two trees, which `_binding_index` raises on; `UnknownJurisdiction` still
  means no tree serves the state at all (REQ-55), and a code no tree binds for
  a served state is `NO_POLICY_FOUND` (D26).
- **Constant *names* are in a measured tool payload** *(T-92, D111)*.
  `get_policy_context` emits each criterion's constant names, so renaming one
  in a committed tree is a changed configuration and a new measurement (D45),
  not a tidy-up. That is why `infliximab-ra-jjm-v1` declares
  `min_comorbidity_count` for a criterion counting the patient's *indication*,
  with a note on the constant saying so.
- **A criterion's arithmetic comes from its declared `kind`, never from its
  id** *(REQ-57, D110)*. Both trees letter their criteria `a`, `b`, `c1`…
  because NCD 100.1's MACs do, and coverage documents from other practices
  letter theirs the same way — so id-keyed dispatch hands a rheumatology
  criterion `a` to the BMI comparison, which answers, cites a span, and passes
  every test in this repo. `tests/test_predicate_kinds.py` parses `pa_agent/`
  for a criterion-id literal, because no behavioural test can tell the two
  dispatches apart on a corpus whose ids match. **Unbuilt is not unclaimed:** a
  kind the engine lacks raises at load; only a tree's *declared* limit
  (`evaluation: "unclaimed"`, with a note) becomes
  `NOT_EVALUATED_BY_THIS_SYSTEM` *(REQ-58)*. A kind in the enum with no
  predicate, or none a step claims, is a red suite — it would otherwise be a
  criterion that silently produces no verdict, which approves past a
  requirement.
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
  retrieval, because Article V's verifier entered the shared denominator, and
  3.6x when T-81's second extraction call per chart entered it too.
  **Quote the delta beside the ratio** — 24 model calls and 90,743 input tokens
  the fixed planner never spent — because the delta is the figure that does not
  move when the denominator does.
- **A tier is set in one place, and the recording states what ran, not what was
  asked for** *(T-90, D106)*. `pa_agent/tiers.py` sets the environment and
  builds the client together, because they are coupled: with the enterprise
  switch left at `1` even an `api_key=` client comes back claiming
  `vertexai=True`. An unknown tier raises and a Vertex client without a project
  raises — both silent alternatives are recordings that lie about their
  provenance, which is D19's failure and one every downstream gate agrees with.
  Recordings stamp `tier_of(client)` and `output_schema_and_tools` read off the
  model; neither `adk_version` nor `tool_fetch` distinguishes the two prompts.
  **`native_schema_enabled` lives in `pa_agent/agent/`, never in `tiers.py`** —
  nothing under `pa_agent/` outside that subpackage may import `google.adk`,
  even lazily, and `tests/test_adk_agent.py` scans for it.
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
  offsets are unusable: 0 of 80 in the spike, 0 of 171 in T-15, 0 of 169 in
  T-89's re-measurement, 0 of 175 on T-81's two-note corpus.
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

- **A tier is an environment, not only a credential** *(T-90, D106, measured)*.
  `Gemini.capabilities.output_schema_and_tools` — the thing D62 is about —
  resolves through `is_enterprise_mode_enabled()`, which reads
  **`GOOGLE_GENAI_USE_ENTERPRISE` from the process environment**. The injected
  `genai.Client` is never consulted; only request routing reads
  `api_client.vertexai`. Measured: unset → `False`; `GOOGLE_GENAI_USE_VERTEXAI=true`
  → `True`; `GOOGLE_GENAI_USE_ENTERPRISE=0` → `False`; **both set → `False`,
  enterprise winning silently**; `=1` → `True`. `pa_agent/agent/.env` holds
  `...=0` and every loader `setdefault`s it, so a Vertex client alone keeps the
  injected `SetModelResponseTool`.
- **`genai.Client(vertexai=True)` with no project keeps `GOOGLE_API_KEY`** and
  targets `aiplatform.googleapis.com` — Vertex express mode on the free tier's
  credential. Passing project *and* location drops the key to ADC and uses the
  regional endpoint. A Vertex client **constructs without any credential**; the
  failure surfaces on the first request, so construction proves nothing.
- **The pinned model is served on Vertex only at `location=global`** *(T-90)*.
  `us-central1`, `us-east5` and `europe-west4` all answer 404 for it. The name
  is unchanged, so `PINNED_MODEL` did not move.

Per D5: develop against the AI Studio free tier, run final evals and any demo
through Vertex, because Vertex does not train on submitted data. **D19's and
D45's numbers are AI Studio numbers**; a Vertex run of the same corpus is a new
measurement, not a confirmation. Since T-90 both tiers are measured and
committed side by side *(D106)*, and **cost figures do not transfer** — the
direct runner spends markedly more input tokens on Vertex for an identical
prompt, so only within-tier comparisons are quoted.

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

- **A mutation run can delete the note corpus.**
  `tests/test_notes.py::test_regenerating_the_corpus_is_byte_identical` runs
  `synthesize_notes.py --generate`, which `rmtree`s `data/patients/notes/`
  before rebuilding it — so a mutation that breaks the engine leaves the
  tracked corpus half-deleted, and the next suite run reports failures that
  have nothing to do with the mutation. Deselect that test in a mutation run,
  and `git checkout -- data/patients/notes` plus
  `select_patients.py --verify` before trusting any result *(T-91)*. Restoring
  from git is exact; regenerating is not byte-stable *(D73)*.

Related: **when a behavioural test cannot catch a mutation, parse the AST
instead.** A resolver that branches on an id's shape and *then* falls through to
the record answers identically on every input the corpus can produce *(D65)*; a
label check that falls through to the port answers identically on all eleven
notes *(D67)*. Both are pinned by parsing.

## Current state

**72 of 72 tasks closed, 0 open. All 10 gates green**
(`check_gates.py`, ~45s; the suite collects 990 tests across 36 files, 27 of
which skip — the skips are `test_criteria_tree.py`'s per-tree constant
matrix, which skips the pairs a given tree does not declare, D101's pattern).
IDs run to T-92, but
numbering is not contiguous and D92 and D94 deleted six records between them,
so the highest id is well above the count.

Delivered: **US-1 through US-7 and US-9**, and **acceptance gates A1–A9 all
hold**. `python -m pa_agent.cli --patient
<uuid> --procedure 43775` prints a real determination — seven criterion
verdicts, spans that slice back, a gap list and Article X's counters — for zero
model calls, because the default extraction runner replays T-15's recording.
The eval set is full (T-21, D75): `eval/cases.json` holds seventeen labeled rows
— spec §6's fifteen plus `NP1`, the `NO_POLICY_FOUND` row outside §6, and
`J1`, the second-jurisdiction row *(D102)* — all `PASS`, criterion-scoped,
with every cited span validated by the scorer (A3). Case rows may carry their own
`as_of`, and E2's does: sc2 fires only for nationally covered codes on
in-window evidence *(D41)*, so E2 runs 43644 at 2024-12-01 while E7 reads the
same chart at the harness clock. US-9 closed with T-29 and T-30 (D76, D77):
a fault is a criterion's `ERROR`, the abort is `DeterminationAborted`, and the
eval harness classifies it as its fourth status — never `FAIL`, never an
abstention; the reported abstention rate counts an `ERROR` in neither its
numerator nor its denominator (REQ-28). US-6 closed with T-17 (D78): every
cited verdict passes through Article V's blind verifier, every gate replays
the committed 30-claim recording for zero calls, and the measurement
history — two false-rejection rounds forcing the verdict-asymmetry rule, then
27/27 twice, then 30/30 when T-88's three new claims joined *(D102)* — is
D78's substance. US-7's measurements are in `eval/report.md`
(T-22, T-28, D85), generated and gate-verified: **A2 precision 1.000 on `MET`
against a 0.609 base rate** (the always-`MET` baseline scores exactly the base
rate, which is the comparison A2 asks for), **A3 zero invalid `MET` spans over
95 checked**, **A5 abstention 0.235** with the per-`gap_reason` account and
D82's tolerance sweep, and **A6 45 model calls / 36,956 input / 7,590 output /
47.7s across ten determinations** — replayed instrumentation, not the replay's
own clock. Read them from `eval/report.md`, which is generated; these are a
copy and the report is the source.

Open: **nothing on the board. v1 and v1.1 are complete and `v1.2` is under
way** — §11's closing condition for v1.1 is met and A1–A9 hold. **`T-91`
opened v1.2** (D110): every criterion a tree declares deterministic names a
`kind` from `PredicateKind`, the engine dispatches on that and on no
criterion id, and a kind it does not implement fails at load. It minted
REQ-57 and REQ-58 — **a statement is minted by the task whose close checks
it**, not by the version's opening commit *(D109, refining D105 rule 2)*.

**`T-92` closed row 2** (D111): the first tree from an unrelated practice.
`infliximab-ra-jjm-v1`, compiled from Palmetto GBA's **L35677** and
**A56432**, both fetched into the hashed corpus with answers q7–q10. It
loads beside the two bariatric trees over the **same seven states**, which
ended the one-tree-per-state rule — a state is served by one tree per
practice, and the collision that matters is a code bound for one state by
two trees. The engine needed **one** predicate kind it did not have
(`medication_value_set_active`); two more of the document's statements are
an exclusion rather than criteria, and three are unclaimed because of the
document and the chart. It minted REQ-59 (a value set declares its code
system and membership is tested inside it) and REQ-60 (an exclusion declares
its kind and its scope; one that does not fire produces nothing). No
bariatric verdict, span, eval row or recording moved. It also **rewrote row
3's exit before it opened**: the board asked for a `NOT_MET` on trial
duration and an abstention on a missing screen, and no Medicare rheumatology
LCD states either — D97's Palmetto correction, one row later. **`T-93` is
next**, row 3: patients in Palmetto's territory with rheumatoid arthritis,
and their eval rows.

Row 8 of v1.1
closed with D107: P6's path for REQ-44/47 is written down and deliberately not
taken. The candidate that looks like it claims model adjudication does not —
Palmetto's `d` decomposes into extraction plus set membership plus a window,
so it adds an extractor and leaves Python deciding. Claiming REQ-44 needs a
predicate that cannot be compiled (the shape D97 rejected in Novitas's
"diligent effort"), a stability mechanism satisfying **Article II's own test**,
an oracle other than the deterministic path, and its own gate. None is met and
nothing through v2.0 schedules one; the unclaimed set stays closed at two.

T-90 (D106) took row 7: the whole corpus measured a second time
on **Vertex** and committed beside the AI Studio recordings, which did not
move. Fidelity is identical on both tiers, the verifier accepts the same 30
claims with no verdict moving, and 0 of 169 / 0 of 165 / 0 of 76 model offsets
were usable — D18's fourth reproduction. D71's clause is answered *partly*:
the injected `set_model_response` round trip is an AI Studio artifact and
vanishes natively (tool calls 26 → 12, unescaped spans 4 → 0) while the token
overhead only halves, 4.12x → 2.14x against each tier's own direct runner.
Cost figures do not transfer between tiers and only within-tier comparisons
are quoted.

The §10 round was
opened as "v2" and renamed v1.1 by D105, which also fixed the versions after
it — v1.2 through v2.0, one story and one gate each — in spec §11, on the
board's `Roadmap after v1.1`, and in stories F3–F6. **D112 added v2.1 and
v2.2** after them: the payer axis (a request resolves by payer as well as by
code and state, and `national_floor` stops being a citation nothing checks)
and a **mimicked** commercial policy, synthesized and declared synthetic
because a real one cannot be committed or re-fetched by a gate — which is what
earns the predicate kinds Medicare's drug documents do not state *(D111)*.
A version's REQ ids are
minted when it opens, so the count below is unchanged. v1 and v1.1 are both
complete — **A1–A9 all hold** — and spec §10's list of known limits, P1–P8,
which **D97 sequenced into one task each**, is **all eight rows closed**. T-85 (D98)
put the anchoring account in `eval/report.md`; T-86 (D99) made every
`NOT_MET` carry a structured `shortfall` and added the `sufficiency` step —
the ninth `STEPS` entry, between `criteria_c` and `verify` — which re-runs
the predicate over only the cited evidence and maps a mismatch to `ERROR`,
never an abstention. T-87 (D100, D101) added the second jurisdiction:
`resolve(code, state)`, Palmetto GBA's tree `ncd-100.1-jjm-v1`, the fifth
resolver type `NoJurisdictionTree`, and the graph generalized to what a tree
declares. T-88 (D102) put a patient in Palmetto's territory: a declared
clone of E4's chart, computed by `select_patients.py --clone` and recomputed
by `--verify`, whose notes are byte-identical to its source's so the
extraction recording replays them by content — `RecordedExtractionRunner`
is keyed by sha256 first — and whose eval row `J1` pins that the three-month
run is a `c3` shortfall in Washington and not a criterion in Alabama. T-89
(D103) added the bounded verbatim re-ask to both live runners — one extra
call, only on a note with a quote the anchorer refused, the answer admitted
by the anchorer and never by the model. **T-81 (D104) gave every
note-bearing chart a second note**: a split of the facts each manifest
already declared — every encounter, assertion and trap carries a scalar
`document`, and the qualifying run straddles `chart_note_1.txt` and
`chart_note_2.txt` wherever a run exists — so every existing label was
invariant by construction and the round's one variable was the layout.
The one deterministic-core change is REQ-34a: every note-level BMI is
reconciled against the structured value, none selected by store order.
`E13` pins that c3 cites two documents. Every recording was re-measured:
175/175, 165/165 and 76/76 spans anchored, the re-ask firing once under
tool-fetch — on E8's *completed*/*completing*, the paraphrase P2 was written
from — and recovering it; the verifier 30 of 30; the eval set 17 of 17; the
agentic differential measured fresh at 7/7 and 49/49 with the planner
gathering both notes for every patient, so the direct recall figure is
measured and reads 1.000 on the first day it could have fallen. The label
re-read D96 deferred to this task is D104's table. The Vertex measurement
(T-90, D106) and P6's entry (D107) closed the round.
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
later corpus expansion instead of standing as an open warning in every
document. T-81 took that pass: every row re-derived from the manifests and
the trees' constants, tabled in D104, reviewed at the close. Spec §10's P4
records both; `README.md` and `eval/report.md` state the scope without the
injunction *(D92, D96, D104)*.

Worth knowing before a review: **REQ-44/REQ-47 are unclaimed on purpose** —
Amendment 1 reserves the entire decision procedure to Python, so there is no
verdict a model could determine without doing something reserved. They are
declared in spec §5's *Unclaimed in v1* table, which is what makes A7
satisfiable *(D63, D70)*. **D107 fixes the four preconditions that would claim
them** and none is met; adding an extractor for a judgment-*shaped* criterion
is REQ-38's shape, not REQ-44's, and a task that built one and ticked REQ-44
would buy a passing check rather than a capability.

### Domain facts that took work to establish

- **The policy corpus is seven documents, two jurisdictions and two
  practices** *(D21, D29, D100, D101, D111)*. The two added by T-92 are
  Palmetto GBA's **L35677** (*Infliximab*) and **A56432** (its billing and
  coding article), which is the same MAC and the same seven states as the
  bariatric second jurisdiction — the case that tests resolution rather than
  the one that avoids it. **L35677 states no trial duration and no screening
  requirement for its rheumatoid arthritis indication**, and neither does any
  other Medicare rheumatology LCD: Part B drug LCDs restate FDA labelling. Its
  one quantified trial — three months of steroids and immunosuppressants —
  belongs to its pulmonary sarcoidosis bullet. Say that plainly rather than
  looking for a document that must have one.

  The bariatric five: `ncd_100_1` (national), `a53028` (Noridian, A/B MAC,
  **Jurisdiction F**), `r931cp` (CMS Pub. 100-04 Transmittal 931), and since
  T-87 `l34576` and `a56852` (Palmetto GBA, **Jurisdictions J and M**). **NCD
  100.1 quantifies nothing** — no months, no visit counts, no recency. Every
  constant in a tree comes from its MAC's document, so this system determines
  coverage *as that MAC would*, and a request resolves by procedure code
  **and state** — a state no tree serves is `NO_JURISDICTION_TREE` (REQ-55),
  never a default, while a code no tree binds for a state that *is* served is
  `NO_POLICY_FOUND` (D26, D111). Say that plainly in a review rather than
  calling the thresholds CMS's.
- **The rheumatology tree's letters are not the bariatric ones** *(D111)*.
  `infliximab-ra-jjm-v1` letters its criteria `a` through `e` because L35677
  does, and `a` is a diagnosis set where the bariatric `a` is a BMI
  comparison. Four of its five criteria — the two named contraindications and
  the disease-activity statement — are declared unclaimed **because of the
  document and the chart**: NYHA class is not in ICD-10, *"untreated"* is a
  judgment, and disease activity is a clinical assessment no code grades.
  None is unclaimed because the engine lacks a predicate, which is the
  distinction REQ-57 keeps and the one this tree was most able to blur. Its
  J1745 binding cites a **revision-history line** — the only sentence in the
  corpus that names the code, because A56432's CPT table sits behind the AMA
  licence modal exactly as A56852's does.
- **The two bariatric trees differ in shape, not only in constants** *(D101)*. Palmetto's
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
- **The measured result so far** *(D64, D66, re-measured in D91 and D104)*:
  model-directed retrieval agrees with the deterministic oracle on 7/7 outcomes
  and 49/49 criteria, 93/93 spans valid, zero errors — for **24 model calls and
  90,743 input tokens** the fixed planner did not spend, 3.6x its end-to-end
  input tokens. **Quote the delta beside the ratio**: the fixed planner makes no
  model call, so the ratio's denominator is the replayed extraction-plus-verifier
  cost shared by both sides, and it moved from D64's 13.9x to 4.3x when Article
  V's verifier entered that denominator and to 3.6x when T-81's second
  extraction call per chart did — the delta is the figure that does not move
  *(D91)*. Read the aggregate and the spread, never one patient's ratio.
- **Planner recall is 1.000 over 29 citing cases, on both the cited and the
  gathered figure, and since T-81 the gathered figure is a measurement**
  *(T-27/T-80/T-81, D86/D91/D104)*. The gathered figure is REQ-25's; it was
  1.000 by construction on a one-note corpus and is now the one to quote —
  every chart is two notes, the planner may name one, and on the measured day
  it named both for all seven patients. `eval/report.md` carries the
  per-patient table of notes on file against notes gathered. The two charts
  that cite no note (E7's, E8's) are still **gathered and uncitable, never
  skipped**. A free-tier tool loop is not reproducible at temperature 0, so one
  day's 1.000 is a sample, re-measured and never re-run.

## Repo layout

```
pa_agent/            resolver, criteria, spans, index, anchor, workflow,
                     retrieval, runners, extraction, verifier, reconcile,
                     aggregate, determination, contracts, model_pin, tiers, cli
  agent/             ADK: extraction_agent, retrieval_agent, patient_tools,
                     policy_tools, tool_bounds, agent (adk web entry point)
  stores/            policy.py and patient.py — the two ports and their
                     file-backed adapters. __init__ imports neither.
data/policies/
  source/            ncd_100_1, a53028, r931cp, l34576, a56852, l35677, a56432
                     + sources.json, answers.json (q1–q10)
  value_sets/        obesity_comorbidities and rheumatoid_arthritis (SNOMED);
                     methotrexate and biologic_dmards_and_jak_inhibitors
                     (RxNorm, expanded through RxNav and pinned — T-92, D111).
                     Each declares the one system its membership is tested in
  ncd_100_1_jf.json  Noridian JF's tree, policy_version_id ncd-100.1-jf-v1
  ncd_100_1_jjm.json Palmetto JJ/JM's tree, ncd-100.1-jjm-v1 (T-87, D101)
  infliximab_ra_jjm.json
                     Palmetto JJ/JM's infliximab tree, infliximab-ra-jjm-v1
                     (T-92, D111) — the second practice, same seven states
data/patients/
  manifest.json      the corpus pin — every bundle's hash (D73). It is **here,
                     not under bundles/**; select_patients.py --verify reads it
  bundles/           eight Synthea v4.0.0 bundles — six from the base seed,
                     one carrying the declared synthetic BMI-35.0 observation
                     (T-41, D73; E12's patient is note-free by declaration),
                     and one declared clone of E4's chart re-addressed into
                     Alabama (T-88, D102), recomputed by --verify
  notes/             fourteen chart notes, two per note-bearing chart as
                     <patient_id>/chart_note_1.txt and chart_note_2.txt (T-81,
                     D104), the clone's byte-identical to its source's per
                     document; plus notes/manifest.json — a second, separate
                     manifest, one record per document
  work/              gitignored: the Synthea jar and the full 200-patient run
eval/
  run_eval.py        the baseline diff (T-10). Drift in **either** direction
                     fails; a case that starts passing is adopted with
                     --update-baseline and a commit (D27)
  run_agentic_eval.py  the fixed-vs-agentic differential (T-61). Bare is the
                     gate; --measure spends model calls, --rescore re-derives
                     the free half from the recording (D64, D91)
  build_report.py    T-22/T-28/T-27's generator; --verify is the ninth gate (D85)
  cases.json         the eval set — 17 labeled rows (§6's 15 + NP1 + J1; D75, D102, D104)
  baseline.json      what run_eval.py diffs against
  report.md          T-22/T-28's metrics report — generated, never hand-edited
  manifests/         T-06's ground truth — the system under test never reads it;
                     since T-81 each fact names the document it renders into
  extraction/        results.json plus adk_results_inline.json and
                     adk_results_tool_fetch.json — one per mode (D68), all
                     three re-measured by T-81 on the two-note corpus (D104);
                     and *_vertex.json beside each, T-90's second tier (D106)
  agentic/           results.json — T-61's recording, carrying since T-80
                     the bundle each side *gathered* beside what it cited (D91),
                     measured fresh by T-81 over all seven charts (D104)
  verifier/          results.json — T-17's recording, 30 claims since T-88,
                     re-measured whole by T-89 and T-81 (D78, D102, D103, D104)
spike/spike_001/     notes/, labels.json, results.json, run.py — five notes,
                     no patient
scripts/             check_gates, check_env, check_skeleton,
                     check_req_coverage, verify_sources,
                     select_patients, synthesize_notes, run_extraction,
                     run_adk_extraction, run_verifier_measurement
tests/               36 files
docs/                constitution, spec, stories, tasks, decisions — exactly
                     the five of the precedence table and nothing else (D93
                     deleted the sixth, a plan doc that governed nothing and
                     contradicted the board)
```
