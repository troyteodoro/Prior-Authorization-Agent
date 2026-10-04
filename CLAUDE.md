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

A prior authorization determination agent. Its control case is bariatric
surgery under CMS NCD 100.1; it also runs two trees from unrelated practices
— infliximab for rheumatoid arthritis and abdominal/visceral vascular
ultrasound — which is what v1.2 measured. Built as a proof-of-skill project; authorship is recorded at the
git level. Every decision in this repo has to be defensible in a live review,
so the reasoning matters as much as the code.

## Document precedence

Read in this order. Each one outranks anything below it, and all of them outrank
an instruction typed into a prompt.

| File | What it is |
|---|---|
| `docs/constitution.md` | Ten articles plus Amendment 1. Non-negotiable, not revisited per task. |
| `docs/spec.md` | Numbered testable requirements REQ-1 through REQ-77 (plus REQ-18a and REQ-34a), edge cases E1–E13 plus E10b and E10c, acceptance criteria A1–A13 (§7 holds A1–A9; A10 is v1.2's, A11 v1.3's, A12 v1.4's and A13 v1.5's, in §11's gate table rather than §7 — A13 was rewritten into five clauses before v1.5 opened, D131). §11 is the versions after v1, with the requirements each will mint — statements, not ids, until **the task that checks one** opens *(D105, D109)*. |
| `docs/stories.md` | User stories US-1 through US-9, with personas; US-10 through US-17 are the roadmap's, one per version *(D105, extended by D112)*. US-10 through US-13 closed with v1.2, v1.3, v1.4 and v1.5; **US-14 is v1.6's and not yet open**. |
| `docs/tasks.md` | The board. Task records T-00 through T-106 plus T-126, T-127, T-128, T-129, T-130, T-131, T-132, T-133, T-134, T-135, T-136, T-137, T-138, T-139, T-140 and T-141, each with a runnable exit condition; T-107 through T-125 are reserved rows whose records are written when they open. Every row the board has numbered off the path has a record *(D137–D146)*. **`Path to v1` at the top states what to do next; `Roadmap after v1.1` states the versions that follow.** |
| `docs/decisions.md` | D1–D147, kill criteria, open questions. Append-only. |

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
   the repo. "Looks right" is not an exit condition. A task without a
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
./venv/bin/python scripts/check_gates.py        # all 10 gates. Required at every close.
./venv/bin/python -m pytest -q                  # the suite alone
./venv/bin/python -m pytest tests/test_criteria_c.py -q          # one file
./venv/bin/python -m pytest tests/test_criteria_c.py -q -k e5    # one test
```

The ten gates, all zero-cost: `pytest`, then `check_env.py`,
`check_skeleton.py`, `verify_sources.py --offline` (**two corpora since
T-96** — the nine policy documents and the five FDA labels), `select_patients.py
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
./venv/bin/python -m pa_agent.cli session create --patient <uuid> --procedure 43775
./venv/bin/python -m pa_agent.cli session run <session-id>
./venv/bin/python -m pa_agent.cli session review <session-id> --reviewer NAME --note TEXT
./venv/bin/python -m pa_agent.cli session packet <session-id>          # the .eml; --json for the fields
./venv/bin/python -m pa_agent.cli session submit <session-id>          # writes it to the payer outbox
./venv/bin/python -m pa_agent.cli session decide <session-id> --outcome approved --decided-on YYYY-MM-DD
```

The eight session verbs dispatch on `argv[0]` **before** the bare parser is built,
so the bare invocation above is unchanged (D129). `session packet` is `T-103`'s
and prints a document rather than a record, which is why it is the one verb whose
default output is not JSON *(D131)*. `session review` is `T-104`'s and appends
one entry per action — `--accept`/`--reject`/`--justify`/`--note`, mutually
exclusive — with argparse deciding **only** which action was asked for and
`ReviewEntry` refusing everything else *(D132)*. `session submit` and `session
decide` are `T-105`'s: transmission is a **table row taken on a verb**, legal only
from `IN_REVIEW`, and the payer's answer closes the session as one state carrying
an outcome and two dates *(D134)*.

CLI exit codes: `0` an answer, `1` a bad request (unknown patient; from the verbs
also a malformed intake, an unknown session, an illegal order, an unknown payer
and a **refused packet**), `2` an unbuilt path, `3` a determination aborted over a
criterion in `ERROR` — the criterion id and `error_code` go to stderr, nothing to
stdout (REQ-29, D76, D129, D131). **A lifecycle refusal is exit 1 and there is
deliberately no fifth code** *(D129, D134, REQ-76)* — the stderr line names the
current state and its legal successors, which is the distinction a fifth code
would encode in five documents instead.

`--tier {ai_studio,vertex}` reaches the six measurement scripts and the CLI,
defaulting to the development tier so every existing invocation is unchanged;
the output *and* `--rescore` paths are routed per tier, so a Vertex run cannot
overwrite the AI Studio recordings the gates read *(T-90, D106)*.

Commands that **spend model calls** and are therefore in no gate:
`scripts/run_extraction.py`, `scripts/run_adk_extraction.py`,
`scripts/run_verifier_measurement.py`, `scripts/run_quote_measurement.py`,
`scripts/run_adk_quote_measurement.py` (T-98's two, `--tool-fetch` on the
second), `eval/run_agentic_eval.py --measure`. Each has a `--rescore` /
replay path that re-derives every number from the committed recording for
free — use it.

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

**Three ports at the model boundary, and a fourth beside the graph; the
first two are what make the differential real.**

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
- `QuoteRunner` (`quotes.py`, T-98, D122) — *who reads the note for the
  review*. `DirectQuoteRunner`, `AdkQuoteRunner` (`pa_agent/agent/`, inline
  and tool-fetch), `RecordedQuoteRunner` (replays `eval/history/results.json`,
  keyed by note sha256, refusing a table row it was not asked) and a raising
  `NullQuoteRunner`. Consulted by `history.run_review`, never by a `STEPS`
  entry, so it is **not drawn** in the README's diagram and
  `tests/test_readme_structure.py` still counts three ports (D121). Every
  runner walks T-89's `extract_with_reask` with this module's builder and
  locator — one re-ask core, two payload shapes — and returns through
  `build_quote_result`, the trust boundary.

**`pa_agent/tiers.py` is the one place a client is built** *(T-90, D106)*.
`client_for(tier)` sets `GOOGLE_GENAI_USE_ENTERPRISE` and constructs the client
in one call, verifies what it built, and raises rather than defaulting;
`tier_of(client)` reads the tier back for the recording. Every runner still
takes an **injected** client and names no tier, which is what let the second
tier land without touching them.

**`build_result()` is the trust boundary.** Every runner returns through it. ADK
output is untrusted model output and there is no private route to a `WmEvent`.

**Six storage ports, four corpora and two outputs** (`stores/policy.py`,
`stores/patient.py`, `stores/knowledge.py`, `stores/session.py`,
`stores/payer.py`, `stores/outbox.py`, REQ-41, Article VI). The patient port serves observations, conditions,
**medications** (T-92), **procedures** (T-94, each carrying the care setting
it was performed in), notes, the jurisdiction state and documents; the
policy port serves resolution, trees, documents and value sets. `stores/__init__.py` imports no submodule on purpose —
a package-level re-export would be the module that reaches every plane.
The knowledge port
serves `data/knowledge/` — what a **drug** is known to do, which is neither
what a payer covers nor what one chart says — as reviewed rows plus the pinned
RxNorm expansion that lets a row's ingredient reach a prescription *(T-97,
D119)*. The payer port serves `data/payers/` — **synthesized** simulated
contacts, in no hashed manifest because there is no upstream to re-fetch, every
address under `.invalid` — and the outbox port **writes** the rendered packet
*(T-105, D134)*. Production is a second adapter, which is the whole reason the
ports exist *(D25)*.

**A shipped corpus raises on an empty read; an output root answers `[]`**
*(D127, D134)*. `payer` is shipped, so an empty directory is a broken checkout
and reporting it as *there is nobody to send to* is the well-formed empty answer
every downstream check agrees with (D31, D39). `session` and `outbox` are the two
the system **writes**, and empty there means nobody has created a session or sent
a packet. The asymmetry is stated in `stores/__init__.py` so it does not spread
by imitation to a seventh port.

**`pa_agent/history.py` is the medical-history review, and it sits beside the
determination rather than inside it** *(T-97, D119)*. A pure function: it takes
facts and returns a `HistoryReview`, so `Determination`, `STEPS`,
`aggregate.assemble` and every recording are untouched and spec §11's *no
verdict changes in v1.3* is structural rather than measured. An active
prescription matching a table row is a **candidate**; Python then either
suggests it — **green** on a declared threshold crossed, **yellow** on an
anchored note quote, **red** on the drug's labeling alone — or **withholds** it
for one of two declared reasons, because the chart already codes the condition
or because the signal was measured and did not cross. `would_affect` names the
criteria of the governing tree whose value set admits the codes a chart
carrying the condition would hold, and changes nothing. The model is asked for
a note quote and nothing else *(T-98, D122)*: `run_review` consults a
`QuoteRunner` once per note for **every** table condition by display — never
the drug, the code or a colour — returns a `HistoryRun` (the review beside a
trace per note, D62's two-object shape), hands every yellow to Article V's
verifier as a `(candidate, quotes)` claim and demotes a rejected one to red
that says so (`verifier_rejected`, REQ-67). The review's calls never enter
`Determination.metrics`; an eval row budgets them with its own
`max_review_model_calls`.

**`pa_agent/form.py` is the packet, and it is pure** *(T-103, D131)*. No path,
no clock, no store, and **no renderer**: `assemble` takes
`rendered_determination` — the dict `cli._render` produced — handed **down** and
embeds it verbatim, because a second renderer is a second answer to one question
(D129) and a `form.py` importing `cli.py` would join `BOTH_PLANES`. It validates
every citation through `pa_agent.spans` against an index the composition root
filled from the ids `source_ids` reports — `cli._packet_index`, over **three**
ports since `T-134`, because a packet's citations point into all three hashed
corpora *(D133)* — and `_cited_spans` is the one traversal the validator, the
renderer, `Packet.cited_documents` and T-106's gate all read, `citations()`
being its public name over an assembled packet *(D135)*. Four refusal conditions
under `PacketRefused`, three of them typed: an accepted red with no
justification (**naming every** unjustified code), an acceptance naming a row
the review does not hold, a citation that does not slice, and — on the base
type — a run the session does not have. The `.eml` is
hand-rolled — `EmailMessage.as_string()` stamps a clock-derived `Date:` and a
randomised `Message-ID`, so no fixture could equal two renders — and carries no
`From:`, because nothing in the repository names who sends. `session packet` is
the fifth verb; the payer is a string the composition root supplies, and since
`T-105` its **source** is the payer directory rather than a declared placeholder —
the shape did not move *(D131, D134)*.

**The review log is a field, appended by a pure function, and the table is why
it can be appended twice** *(T-104, D132)*. `pa_agent/session.review` returns a
new `Session` carrying one more `ReviewEntry` or raises; only the adapter
writes. `TRANSITIONS` gained **`IN_REVIEW: (IN_REVIEW,)`**, the append
self-edge — US-13's *her edits append*, plural — so `IN_REVIEW` stopped being
terminal with **no line outside the table edited**, which is D127's reason for
deriving terminality rather than declaring it, paid off. *The determination's
bytes are unchanged* is structural in three layers: the models are frozen, so
`model_copy` has no route inside a run; `review()` **names `runs` nowhere in
what it writes**, held by parsing the `update` dict because a function that read
them and put them back is indistinguishable from it on every input; and the
stored runs are compared **off disk across three reviews**, since a one-review
comparison passes a mutant that replaces entry 0. Each entry's `run_index` is
bounded **on the contract and inside `review()`** — `model_copy` runs no
validator, so the contract alone is unreachable from the only path that appends
and the verb would write a session `get()` cannot read back.

**Adjudication is nine predicate kinds and two exclusion kinds.**
`PredicateKind` (contracts) is the closed vocabulary, `criteria.PREDICATES`
maps each kind to a binder naming the inputs its predicate receives, and
`workflow.STEP_KINDS` assigns each kind to the step that evaluates it — a
partition, checked (T-91, D110). `ExclusionKind` and `criteria.EXCLUSIONS` are
the same shape for categorical exclusions (T-92, D111). Under both NCD 100.1
trees that resolves to (a) BMI and (b) comorbidity over structured FHIR and
c1–c5 over extracted `wm_events`; under `infliximab-ra-jjm-v1` it resolves to
(a) a diagnosis set and (b) a medication set; under
`us-abdominal-visceral-j5-j8-v1` to (a) a diagnosis set and (b) the interval
to the most recent prior procedure. The letters are labels: the `kind`
chooses the arithmetic, and three practices now letter their criteria `a` and
mean three different things by it. The run-length criterion computes the
qualifying run **once** and every criterion whose `scoped_to` names it scopes
to that run. **A tree also declares the `practice` it belongs to** *(T-95,
D116)* — required, slug-shaped, and read by **nothing under `pa_agent/`**. It
is the key `eval/report.md`'s compatibility account groups by, and it exists
because no field the tree already carried could say that two bariatric trees
under two contractors are one practice while Palmetto's rheumatology tree is
not. It is deliberately absent from `get_policy_context`'s payload, which is
built field by field so a new field cannot become a changed prompt (D45).
**A criterion may also declare a `national_floor`** *(T-129, REQ-73, D130)* —
the national document's own sentence, spanned, beside the **constant** it bounds,
the **value** and the **comparison** — and a `Criterion` validator refuses at
load any constant looser than its floor. Every constant in these trees is a
MAC's, not CMS's *(D21)*, so a MAC may be stricter than the NCD it
operationalizes and may never be looser. Both bariatric trees hold it at
equality; the floor is not in the tool payload either.
`reconcile.py`
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
  shortfall is Article II's arithmetic over a chart Article V hides. **And
  from set membership, since `verifier-v5`** *(D115)*: a claim names the
  criterion's `value_set_id` and never shows the set, so a verifier judging
  whether a quoted condition belongs to it is doing Article II's arithmetic
  over a value set Article V hides — the same category, found three rounds
  later because until T-94 every membership claim's quote was obvious from
  the criterion's own label. A prompt edit re-measures **every** claim on
  **both** tiers (D45), and v5's round measured a regression in the NOT_MET
  rule its new paragraph now followed, which is what v6 states positively
  instead of as a prohibition.
- **A frequency limit is an interval, never a count** *(REQ-61, T-94, D114)*.
  *At most one study a year* and *the last study was over a year ago* are the
  same arithmetic and only the second has a span when it passes: a count has
  to answer `MET` on a chart with no prior study, and REQ-5 refuses a `MET`
  with no span — D111's problem, one layer along, and the reason that one
  became an exclusion and this one did not. No prior study documented is an
  **abstention**, because a chart that records none has not recorded that
  none was performed elsewhere (D40's asymmetry, on a third resource type).
- **A prior study is dropped from a frequency limit only on positive
  evidence** *(T-94, D114)*. L35755 excludes inpatient and emergency-room
  places of service from its own arithmetic, so the predicate reads each
  procedure's `Encounter.class`; a procedure whose encounter cannot be
  resolved **counts**. The directions are not symmetric — dropping an
  unresolvable study approves past a limit the policy states, while counting
  it produces a `NOT_MET` the reviewer lifts with the documentation the
  policy itself asks for — and no committed chart can tell the two behaviours
  apart, so `tests/test_ultrasound_tree.py` holds it on charts written there.
- **A declared resource is appended to a committed bundle as text, never by
  re-serializing it** *(T-97, D119)*. `build_claim_payload` carries no offsets
  but it carries the **quote**, and a quote is the raw slice of the resource, so
  reformatting a bundle changes every claim digest into it and
  `RecordedVerifierRunner` raises on all of them. Measured: writing it the
  obvious way turned RA3 from `PASS` to `ERROR` in one run. `--verify` asserts
  that re-applying the declaration reproduces the committed bytes and that the
  base bundle underneath still hashes to the recorded hash, which is the
  byte-level recomputation a clone gets from its source and this chart has no
  pristine copy to get any other way.
- **An ingredient never matches a prescription; the pinned expansion is the
  match** *(REQ-63, T-97, D119)*. Rows declare RxNorm **ingredients** (`8640`,
  `29046`) and Synthea writes **clinical drugs** (`310798`, `314076`,
  `105585`): measured, zero hits in all fourteen bundles. So a comparison
  without `data/knowledge/rxnorm_ingredient_products.json` loads cleanly,
  compares cleanly and suggests nothing on every chart forever — D52's failure
  one layer up, and the reason an eval row pins the **withheld** list and not
  just the suggestions.
- **A candidate the chart answers is withheld, and withheld is not red**
  *(REQ-64, T-97, D119)*. Red means *nothing on the chart*, which is US-11's own
  wording. A glucose of 76 against a threshold of 126 is the chart answering, so
  that candidate is withheld with its measurement; suggesting the condition over
  it is a claim the record refutes, and it would make *the lab says no*
  indistinguishable from *nobody measured*.
- **`quotes=None` raises on a chart that carries notes** *(T-97, D119; one
  layer up since T-98, D122)*. D90's rule on a second wire: "the system did
  not look" and "the chart does not say" are the two things the fault exists
  to keep apart, and red is the second. A note-free chart needs no quote
  source; `run_review` with no runner on a note-bearing chart still raises,
  and the harness computes the review **only for rows that label one** —
  `bc6748d3` is note-bearing, carries an active lisinopril and no creatinine,
  E8 and E10b grade its determination without a review, and `H4` reads it
  red through `RecordedQuoteRunner` with both notes consulted.
- **The quote prompt names a condition and never a drug, a code or a
  colour** *(T-98, D122)*. Every knowledge-table `row_id` embeds the
  ingredient, and a model told *lisinopril* beside *renal impairment* is
  invited to quote the medication line as evidence of the condition — the
  one thing a prescription may never be (D119). `format_effects` renders
  `effect_display`s only, every note is asked about every row so the request
  is one configuration a chart cannot vary, and `RecordedQuoteRunner`
  refuses a `(row_id, effect_display)` pair it was not asked: adding a table
  row is a new measurement (D45). The same rule shapes the history verifier
  payload, which carries the condition and its ICD-10 title and nothing else.
- **A quote is yellow only after the anchorer located it and the validator
  accepted it; the refusal is recorded, never repaired** *(REQ-67, T-98,
  D122)*. `build_quote_result` anchors by searching the note for the model's
  verbatim text, re-asks once (REQ-56) and then drops, and a candidate with no
  anchored passage is red. Checked at unit level through the real anchorer on
  a hand-written note, because no committed note can produce a yellow until
  `T-110`; the six recordings measured **0 of 60** `(note, condition)` pairs
  with any passage returned, and a pair that *anchored* is a red gate — a
  yellow this corpus was not supposed to produce. The review's turns are
  counted beside the determination's (`HistoryRun`), never in A6.
- **A packet's citations point into three corpora, and the index is built from
  every port that serves a document** *(REQ-74, T-134, D133)*. A criterion cites
  the chart, a coverage claim cites the policy corpus, and an accepted
  suggestion's `effect` cites an **FDA label** — so `cli._packet_index` consults
  the knowledge port too. D131 built it from two and wrote *a packet's spans
  point into both*; the count was wrong and **no packet carrying an accepted
  suggestion could be assembled at all**, while every gate stayed green because
  `tests/test_form.py`'s red tests validate no span and its assembling tests use
  a determination with no accepted suggestion. No behavioural test on a packet
  without one can tell. The set of ports is therefore **derived** — every store
  `Protocol` declaring `get_document`, which the session store does not — so a
  fourth corpus cannot reintroduce it by being forgotten, and the store tuple's
  **order** is not a precedence rule: the three id spaces are disjoint, measured,
  and that disjointness is what is asserted, because a reorder is otherwise a
  mutation no committed chart can catch. An id **no** port serves is still left
  out and refused by `form.assemble` as `UNKNOWN_DOCUMENT` (D38).
- **A packet's document list is the *manifest's* documents, and a packet with no
  accepted suggestion cannot tell that from the determination's** *(REQ-74,
  T-137, D135)*. `Packet.cited_documents` is `form.citations` deduplicated,
  written by `assemble` from the **same** `_cited_spans` call — not a second walk
  a few lines away, which is what named one document of two on the first packet
  that could carry a suggestion. The two sets are identical on every packet
  without one, so the check lives in `tests/test_packet_index.py` on the packet
  that separates them, with the expected ids read off the **patient store**
  rather than off the traversal under test. It is **not** the form's supporting
  documents: that box is attachments, this packet attaches nothing (REQ-70's
  line), and on a `NOT_COVERED` packet the list is the policy corpus and no chart
  — which is why it is declared a non-form field and why the name moved.
- **A red suggestion's justification lives in the review log, and a suggestion
  enters a packet only through a recorded acceptance** *(REQ-74, T-103, D131)*.
  `IcdSuggestion` is frozen, red carries **no citations by construction**, and
  `history.run_review` recomputes the whole review on every `--suggest` — so a
  justification stored on the suggestion is recomputed away, silently, on the
  next call. Widening `citations` to hold it is worse: a justification is the one
  thing in a packet that is **not** evidence, so it would put a non-span where
  Article III validates spans. `ReviewEntry`/`ReviewAction` are the home, on
  `Session.reviews`; the form reads **acceptance and justification as two
  questions**, each answered by the latest entry in **log order** that speaks to
  it, scoped to the `run_index` being packaged. A single latest-entry rule is
  wrong in both directions — an accept then a justify would un-accept the row,
  a justify then a reject would keep it — and comparing `at` strings is worse
  still, because two entries written in one second compare equal.
- **The blank-justification guard is on the contract and nowhere else** *(T-103,
  D131, measured)*. `ReviewEntry` refuses a whitespace-only justification at
  construction, so a second guard in `form.accepted` would be a check no input
  can reach: `if not justification` and `if justification is None` are the same
  function there, and the mutation between them survives every test. A check
  that cannot fail is not a check — `accepted()` reads `is None`.
- **`would_affect` compares the row's already-coded SNOMED codes, never its
  ICD-10 code** *(REQ-66, T-97, D119)*. Measured across every committed value
  set: the eight `icd10_anchor`s any set carries are `I10`, `N18.1`, `N18.2`,
  `N18.30`, `N18.4`, `E11.9` and `M06.9`, and the table's codes are `M81.8`,
  `I95.9`, `N28.9`, `R73.9` and `D70.2`. Nothing matches either way, so the
  ICD-keyed reading returns an empty list on all five rows **and passes every
  behavioural test this corpus can produce**.
- **A refused write is checked against the file's `st_mtime_ns`, not only its
  bytes** *(T-104, D132)*. `stores/session.py` generates nothing, so a
  `store.save(session)` inserted before a refusal raises **reproduces the file
  exactly** — a byte comparison holds *nothing changed* where REQ-71 says *never
  recorded*, and the mutation survived the whole suite until the modification
  time joined the comparison. `tests/test_review_log.py`'s two refusal tests
  carry it, and since `T-135` so does `tests/test_session_verbs.py`'s *(D139)*.
- **Transmission is a table row, and the refusal is checked against the
  directory** *(REQ-76, T-105, D134)*. `advance()` raises **before** anything is
  constructed, so `_verb_submit` calls it **first** — ahead of the payer, the
  assembly and the write — and the refusal tests assert the **outbox is empty**
  rather than that the exit code is 1. Measured: moving the write ahead of the
  check returns exit 1 either way, because the advance still raises one statement
  later, so the exit-code assertion passes and only the empty directory (and
  `st_mtime_ns` beside the bytes, D132) sees it. The legality check is also ahead
  of the run-bounds check, because a `CREATED` session has no snapshot and the
  answer a reviewer needs is the lifecycle's.
- **A session's submission and decision are *iff* relations with its state, and
  the adapter re-validates every write** *(REQ-77, T-105, D134, T-138, D140)*. A submission exists iff
  the state is `AWAITING_DECISION` or `DECIDED`, a decision iff `DECIDED`, both
  directions on the contract — a one-way rule is satisfied by a `DETERMINED`
  session carrying a submission anyway, which is exactly the *no session in an
  earlier state has one* half of the statement. `model_copy` runs no validator
  (D132), and all four verbs that write a session hand the adapter one, so
  `LocalSessionStore._serialize` runs the session back through its own
  validators before any file is opened. Without that, a verb writes a session
  `get()` cannot read back, and the failure surfaces one verb later. **Do not
  add a second validator in the composition root**: D140 removed
  `cli._revalidated` because, once the adapter checks, it is a guard no input
  can fail, and `tests/test_session_store.py` parses `cli.py` to keep it gone.
- **`DECIDED` is terminal because its row is empty, and `INFORMATION_REQUESTED`
  is an outcome rather than a state** *(T-105, D134)*. Approved and denied have
  the same next action in this system — none — so `GapReason`'s discipline makes
  them two outcomes on one state; three terminal states would be three identical
  empty rows in `TRANSITIONS`, the copy D127 refused on the enum.
  Information-requested genuinely differs, and it is not a state **because this
  version builds no resubmission edge**: a state whose row is empty while it
  obviously wants an outgoing one lies about being terminal. It becomes a state
  in the version that gives it an edge. And the decision carries **two dates** —
  the payer's own and the clock this system was told at — because collapsing them
  makes a decision *recorded* late read as one *taken* late (D78's category).
- **`data/payers/` is committed and in no hashed manifest, on purpose** *(T-105,
  D134)*. `verify_sources.py` compares committed bytes to a **public
  re-download**, and this file is synthesized: no upstream, so its record would
  carry no URL and `--fetch` nothing to fetch — v2.1's synthetic rule arriving
  early. It also stays out because *nine policy documents* and *five FDA labels*
  are counts `tests/test_docs_consistency.py` re-derives from those two
  manifests, and a payer record able to raise either would make two corpora one.
  `tests/test_payers.py` holds it instead: every record declares itself
  simulated, every address ends in `.invalid` (RFC 2606's reserved TLD), the
  count is pinned as a literal (D51), and no payer id appears in either hashed
  manifest. A mutation making the directory answer `[]` is caught by **those two
  tests and nothing else**, because the committed file is never empty.
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
- **A knowledge-table row declares what it could not source; it never omits
  it and never fills it from memory** *(REQ-62, T-96, D118)*. A row is four
  claims — effect, ICD-10 code, the SNOMED codes meaning *already on the
  chart*, and the structured signal — and each names where it came from.
  `already_coded: []` is legal only with an `unsourced_reason`, and
  `tests/test_medication_effects.py` fails on an empty list without one. The
  failure this prevents is not a wrong verdict: it is a code in a packet that
  traces to nothing, which is the one thing a suggestion must never be.
- **A kept SPL section is never descended into** *(T-96, D118)*. Whether a
  label's numbered subsections carry codes of their own is a per-label
  formatting choice, so `findall(".//section")` collects Zestril's 5.1
  through 6.2 twice and warfarin's not at all — doubling a quote and moving
  every offset after it, in a document whose hash still verifies. No
  behavioural check on the committed corpus separates the two collectors
  except a quote happening to be non-unique, so it is pinned by a unit test
  on a hand-written nested document *(D65's shape)*.
- **A threshold in the knowledge table is a decision, not a span** *(T-96,
  D118)*. Synthea emits no `referenceRange` on any of these observations, so
  there is nothing on a chart to derive one from and no corpus document
  states one. Each carries a name, a unit, a comparator, a date and a
  rationale, and the count is pinned at five — D51's move, on a second file.
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
- **A tree compiling a national document declares its `national_floor` by hand,
  and nothing derives which trees should** *(T-129, REQ-73, D130)*. The
  relation itself cannot be broken quietly: a constant looser than its floor, a
  comparison that is not the bounded constant's, a floor naming a constant the
  criterion does not declare, and a floor whose value is absent from its own
  quote each refuse the tree at `model_validate`. What cannot raise is a tree
  that declares **no** floor — no field says a tree operationalizes an NCD, so
  `tests/test_criteria_tree.py`'s `EXPECTED_FLOORS` asserts the two that exist
  and a third tree omitting one passes every check in the repo. v2.0's
  national/regional scope and its regional-to-national pairing (`T-119`) is what
  would derive it; until then, a tree under a quantified NCD needs its floor
  written, and the missing one is invisible.
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
- **An eval row with a cited verdict is a verifier measurement** *(T-93,
  D113)*. A claim digest is the criterion, the verdict and the sliced quote,
  and `RecordedVerifierRunner` raises on one it has not got — so a new row
  whose criteria answer `MET` or `NOT_MET` cannot be graded until
  `run_verifier_measurement.py` has been run on **both** tiers, and the gate
  reports it as a criterion in `ERROR` rather than as a missing recording.
  Adding rows is therefore never free, whatever a version's plan says it
  spends; what stays free is every gate, which replays.
- **A bundle is held to the rule its cohort was selected by** *(T-93, D113)*.
  D35's BMI band is the bariatric six's and says nothing about a rheumatology
  chart, which was never selected on a BMI — so each record declares its
  cohort and `--verify` scopes the band to it. Applying one cohort's rule to
  the whole population fails on a chart that is fine; deleting the rule is
  worse, and the shape that avoids both is to say which rule each chart
  answers to.
- **The suite is guarded against the network, and the guard must stay
  inherited** *(T-141, D146)*. `tests/conftest.py` refuses non-loopback
  connections and lookups in process, and sets a placeholder `GOOGLE_API_KEY`,
  a nonexistent `GOOGLE_APPLICATION_CREDENTIALS` and refused proxies for every
  child. **Override, never unset:** `cli._load_env` only `setdefault`s from
  `pa_agent/agent/.env`, so an *absent* key is filled with the real one. A test
  that passes a child an `env=` built from scratch drops the guard. Every test
  that starts a live-capable child asserts the guard first, so a broken guard
  fails before it can spend.
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

- **A mutation run can edit a committed bundle.** Since T-97
  `select_patients.py` has a mode that **writes** one —
  `--declare-additions` — so a mutation in the append path that the harness
  executes leaves the corpus edited and every later result meaningless. Same
  class as `synthesize_notes.py --generate`'s `rmtree`, one file over. After any
  mutation run: `git checkout -- data/patients/bundles data/patients/manifest.json`
  as well as the notes, then `select_patients.py --verify` before trusting a
  result *(D119)*.

Related: **when a behavioural test cannot catch a mutation, parse the AST
instead.** A resolver that branches on an id's shape and *then* falls through to
the record answers identically on every input the corpus can produce *(D65)*; a
label check that falls through to the port answers identically on all eleven
notes *(D67)*. Both are pinned by parsing.

## Current state

**102 of 102 tasks closed, none open. All 10 gates green**
(`check_gates.py`; the suite collects 1766 tests across 57 files, 58 of
which skip — the skips are `test_criteria_tree.py`'s per-tree constant
matrix and its exclusion checks, which skip what a given tree does not
declare, D101's pattern and D114's).
IDs run to T-141 (every id above T-125 is off the path, above the roadmap's
reservations, D137), but numbering is not contiguous and D92 and D94
deleted six records between them, so the highest id is well above the count.
**Nothing is open**, and every row the board numbered has a record.
**`T-141` closed off the path** *(D146)*, the last of the ten rows cleared
before v1.6: `tests/conftest.py` refuses non-loopback connections and lookups
in process, and every child inherits a placeholder key, a missing credentials
file and a refused proxy, so a test that built a live runner fails locally.
**`T-131` closed off the path** *(D145)*: A2 (per criterion, as §7 states it),
A3 and A5's sweep anchor are recomputed and asserted by `tests/test_build_report.py`,
not only printed in the report.
**`T-140` closed off the path** *(D144)*: README's gate table and every current
*A1–AN* claim are held to the gates spec §7 and §11 say hold. This file's
*Delivered* sentence read A1–A10 at open.
**`T-139` closed off the path** *(D143)*: README's and this file's documented
flags are checked against `cli.py`'s parsers in both directions.
**`T-133` closed off the path** *(D142)*: every labeled-case count, every
enumerated eval range, both layouts' module lists and this file's bare test-file
count are re-derived from their owners. `H1-H4` above was live drift at open.
**`T-136` closed off the path** *(D141)*: `tests/test_form.py` fills an index
from `form.source_ids` alone and assembles every packet a green, yellow and red
review allows; dropping a kind of review span is a red test.
**`T-138` closed off the path** *(D140)*: the session adapter validates every
write, so the `run` and `review` verbs' `model_copy`s are checked like
`submit`'s and `decide`'s, and `cli._revalidated` is removed.
**`T-135` closed off the path** *(D139)*: `session run`'s refusal test holds
the file's `st_mtime_ns` beside its bytes, as `session review`'s did.
**`T-130` closed off the path** *(D138)*: `history.review()` cites the
expansion's system instead of a name bound in `candidate_rows`, and
`tests/test_history.py` parses every function under `pa_agent/` for a name
nothing binds — the branch was unreachable, because `admits` refuses a
prescription with no system, so no behavioural test could see it.
**`T-132` closed off the path** *(D137)*, the first of the numbered rows cleared
before v1.6 opens: the board's own task count, its highest id and its list of
rows numbered without a record — and this file's copies of the last two — are
re-derived from the board's records and its *Off the path* table, and spec §11's
opening from its own table. All of them were stale at open.
**v1.5 is closed and A13 holds** — `T-103`, `T-104`, `T-105` and `T-106` closed
its four rows *(D131, D132, D134, D136)*, with `T-134` and `T-137` off the path
behind them *(D133, D135)*. **No version is open**; v1.6 opens with `T-107`, and
closing v1.5 does not open it.

**`T-137` closed off the path** *(D135)*: `Packet.cited_documents` — renamed
from `supporting_documents` — is the **citation manifest's** documents,
deduplicated in first-cited order by the same traversal `form.citations` runs.
It claimed to be *every document id this packet cites* and was computed from the
determination's spans alone: measured, three spans over two documents cited and
one named, on the packet `T-134` made assemblable. What removed the other
reading — *the field is right and the comment is wrong, because a form's
supporting-documents box means clinical records* — is a second measurement: a
`NOT_COVERED` packet's list is `ncd_100_1` and `a53028`, two **policy**
documents and no chart, so it has never held clinical records. The name moved
because this packet **attaches nothing** (REQ-70's line), so it has no
supporting-documents box to fill, and the field is declared **non-form** beside
`provenance` while README's *Recent provider notes* row maps to `evidence`.
It minted nothing and found `T-139`.

**`T-105` closed row 3** *(D134)*: `session submit` writes the reviewed packet to
a payer outbox and `session decide` closes the session on the payer's answer. The
diff to `pa_agent/session.py` is **three rows of a dict** — `IN_REVIEW ->
AWAITING_DECISION`, `AWAITING_DECISION -> DECIDED`, and `DECIDED -> ()` — and
`DECIDED` is terminal **because its row is empty**, with `is_terminal()`, the enum
and every line outside the table untouched: D127's argument paid off a second time
in two rows, in the opposite direction to T-104's. Three edges are **absent on
purpose** and each is a decision: submitting without review is US-13's own
refusal, re-running after review orphans every `run_index`, and un-sending is a
claim the outbox cannot support. **One** closing state carries the payer's answer
as data — an outcome, the payer's **own** date and the clock this system was told
at — because approved and denied have the same next action here (none) and three
terminal states would be three identical empty rows. Two new ports, **not one**: a
shipped, synthesized payer directory that raises on an empty read and an output
outbox that answers `[]`. Seven mutations, zero survivors; the `LEGAL` literal in
`tests/test_session.py` is the only thing that catches an added edge, and the
write-before-the-check mutant returns **exit 1 either way** and is caught by the
empty-directory assertion. It minted **REQ-76** and **REQ-77** — the second in the
directional form D131 corrected — and found `T-138`.

**`T-134` closed off the path** *(D133)*: `cli._packet_index` consults the
**knowledge** port as well as the patient and policy ones, so a packet carrying
an accepted suggestion assembles — a suggestion's `effect` is a span into an FDA
label, and D131's *a packet's spans point into both* counted two corpora where
there are three. Sequenced ahead of `T-105` because `T-106`'s exit needs a
committed packet **carrying a justified red**, which could not be assembled at
all. The set of ports the index consults is derived from the store package
rather than listed, `tests/test_planes.py` gained `ALL_READ_PLANES` — the exact
set of modules reaching all three read corpora, which is `cli.py` and nothing
else, a fact this repository had never written down — and `tests/test_packet_index.py`
is where the verb that accepts a suggestion and the verb that packages one
finally meet. It minted nothing — REQ-74 already said *through the port that
serves its document*, and this close makes that true — and it found `T-136` and
`T-137`, the second of them a claim on the packet's own list of the documents
it cites — `supporting_documents` then, `cited_documents` since D135 — that only
the packet this close made assemblable could falsify.

**`T-127` and `T-128` are off the path** *(D120, D121)*. `T-127` re-read
*Where this system degrades*: the eight unclaimed criteria sort three ways
— pipeline-limited, fact-shaped, judgment-shaped — corpus growth rides with
v1.6's round, Palmetto's `d` note reads as D107 found it, and the measured
yellow's deferral is on the board. `T-128` wrote
`tests/test_readme_structure.py`, which `T-126`'s exit named and no commit
had added: the README's diagram is compared to `workflow.STEPS`, the
resolver's result types and their routing, the three ports and the store
adapters, and a renamed step is a red suite.

Delivered: **US-1 through US-7 and US-9 through US-13**, and **acceptance
gates A1–A13 all hold**. `python -m pa_agent.cli --patient
<uuid> --procedure 43775` prints a real determination — seven criterion
verdicts, spans that slice back, a gap list and Article X's counters — for zero
model calls, because the default extraction runner replays T-15's recording.
The eval set is full (T-21, D75): `eval/cases.json` holds 33 labeled
rows — spec §6's fifteen plus `NP1`, the `NO_POLICY_FOUND` row outside §6,
`J1`, the second-jurisdiction row *(D102)*, `RA1`–`RA3`, the second
practice's *(D113)*, `US1`–`US4`, the third's *(D114)*, and `H1`–`H9`, the
medical-history review's — `H5`–`H9` added by T-99 so every candidate the
knowledge table finds is graded *(D119, D122, D123)* — all `PASS`,
criterion-scoped,
with every cited span validated by the scorer (A3). Case rows may carry their own
`as_of`, and E2's does: sc2 fires only for nationally covered codes on
in-window evidence *(D41)*, so E2 runs 43644 at 2024-12-01 while E7 reads the
same chart at the harness clock. US-9 closed with T-29 and T-30 (D76, D77):
a fault is a criterion's `ERROR`, the abort is `DeterminationAborted`, and the
eval harness classifies it as its fourth status — never `FAIL`, never an
abstention; the reported abstention rate counts an `ERROR` in neither its
numerator nor its denominator (REQ-28). US-6 closed with T-17 (D78): every
cited verdict passes through Article V's blind verifier, every gate replays
the committed 38-claim recording for zero calls, and the measurement
history — two false-rejection rounds forcing the verdict-asymmetry rule, then
27/27 twice, then 30/30 when T-88's three new claims joined *(D102)*,
33/33 on both tiers when T-93's three did *(D113)*, and 38/38 on both under
`verifier-v6` when T-94's five did, after two further false-rejection rounds
forced the set-membership rule *(D115)* — is
D78's substance. US-7's measurements are in `eval/report.md`
(T-22, T-28, D85), generated and gate-verified: **A2 precision 1.000 on `MET`
against a 0.420 base rate** (the always-`MET` baseline scores exactly the base
rate, which is the comparison A2 asks for), **A3 zero invalid `MET` spans over
104 checked**, **A5 abstention 0.424** with the per-`gap_reason` account, its
per-tree split of the declared-unclaimed abstentions *(D113)* and D82's
tolerance sweep, and **A6 53 model calls / 55,585 input / 7,870 output /
52.4s across seventeen determinations** — replayed instrumentation, not the
replay's own clock. Read them from `eval/report.md`, which is generated; these are a
copy and the report is the source.

Open: **nothing. v1, v1.1, v1.2, v1.3, v1.4 and v1.5 are all complete** —
**A1–A13 all hold**, and US-13 is delivered. v1.6 is the next version and is
**not open**.

**`T-106` closed row 4 and the version** *(D136)*: two rendered packets are
**committed bytes** under `tests/fixtures/packets/` — one carrying an accepted,
justified red suggestion and one carrying none — each compared **three ways**,
because the three catch three different mutations. The render equals the
committed file; rendering **twice** is identical, in process and across the
process boundary the file already is; and each fixture session's determination is
**re-derived from the live engine** and compared field for field. The third is
what the row exists for: without it the fixture is a recording of an answer, and
D91 is the entry about a recording's free half drifting while every gate stays
green. Measured in the mutation pass — a fixture regenerated from a mutated
engine **passes every byte comparison** and fails only the re-derivation, and
nothing else in the repository catches it. The fixtures are **parsed**
(`Session.model_validate_json`) and their **count is a literal** (D51's move), so
a drifted fixture and a deleted one are both a red suite. It is D127's own named
reversal — *a committed fixture rather than a tracked live root* — so
`data/sessions/` and `data/outbox/` stay gitignored. **No new script and no
eleventh gate**: a `--verify` builder would need a `GATES` or `EXCLUDED` entry and
a `GATES` entry moves the count 10 → 11 to buy what `pytest` already runs, so the
regeneration recipe is the product's own verb, written in the test module. It
edited **no module under `pa_agent/`**, minted **nothing**, ran A13's five-clause
audit naming the command behind each, and found `T-140` (no check ties README's
acceptance-gate table to the spec's gate list — which is how A11 and A12 closed
without a row in it) and `T-141` (nothing holds *the suite as a whole spends no
model call*; the gate **list** and the packet path are held, the suite is held
only structurally).

**`T-104` closed row 2** *(D132)*: `session review` appends. The log is a field
on `Session`, `TRANSITIONS` gained the `IN_REVIEW` self-edge, and `IN_REVIEW`
stopped being terminal with nothing outside the table edited — D127's argument
paid off, one version after it was written and on a different edge than it
guessed. It minted **REQ-75**, moved no determination, and found two things.
`T-134`, **since closed** *(D133)*: `cli._packet_index` consulted the patient
and policy stores and never the knowledge store, so a packet carrying an
accepted suggestion was refused with `UNKNOWN_DOCUMENT`.
`T-135`: a refusal test that compares a session's **bytes** holds *nothing
changed* and not *nothing written*, because the adapter generates nothing — a
`save` of the object just read reproduces the file. This row's own refusal tests
compare `st_mtime_ns` beside the bytes; `T-102`'s does not.

**`T-103` opened v1.5** *(D131)*: `pa_agent/form.py` assembles the packet and
`session packet` prints it as an `.eml`. The module is **pure** — no path, no
clock, no renderer: `assemble` takes `cli._render`'s dict handed **down**,
because a second renderer is a second answer to one question (D129) and a
`form.py` importing the composition root would join `BOTH_PLANES`. **A
suggestion enters a packet only through a recorded `ACCEPT_SUGGESTION`** scoped
to the snapshot being packaged, and the **red justification lives in the review
log** — `IcdSuggestion` is frozen, red carries no citations by construction, and
`run_review` recomputes the review on every call, so a justification stored
there is recomputed away; putting it in `citations` would put a non-evidence
string where Article III validates spans. `contracts.py` gained `ReviewAction`,
`ReviewEntry`, `Packet`, `PacketSuggestion` and `PacketProvenance`; `Session`
gained `reviews` (T-100's precedent — the *home* lands with the row whose
statement reads it, the verb is `T-104`'s) and `Intake` gained
`requesting_provider` and `servicing_provider` as pass-through **text**. It
minted **REQ-74**. The entry also **rewrote A13** into five clauses before the
round, corrected §11's outbox statement into the directional form `decide` does
not falsify, and reworded spec §1 and §4 as D105 clause 3 assigns to it. Nine
mutations, zero survivors, and **two moved a check**: a blank-justification
guard in `accepted()` is a check no input can reach, because `ReviewEntry`
refuses a whitespace-only justification at construction — so the guard lives on
the contract and `accepted()` reads `is None`; and truncating the citation loop
to `[:1]` **survived the first pass**, because the empty-index test fails on the
first span and the count assertion re-validates in the test's own loop. The test
that catches it fills the index with only the first cited document.

**`T-129` closed the last row off the path** *(D130)*: `national_floor` is a
`NationalFloor` declaring the **constant** it bounds, its **value** and its
**comparison** beside the span it has carried since T-01, and a `Criterion`
validator refuses at load a constant looser than its floor, a comparison that
is not the bounded constant's, a floor naming a constant the criterion does not
declare, and a floor whose value cannot be read out of its own quote — that
last one raising rather than defaulting to satisfied, because a floor nobody
can recover from the corpus is a floor every tree passes (D31's shape). It
minted **REQ-73**. Both bariatric trees hold it at **equality**, so no verdict,
span, recording, baseline or verifier claim moved: the check landed while it was
free and before v1.6's nationally quantified NCD arrives *(D124)*. Eleven
mutations, zero survivors; the pass is in the task record.

**`T-102` closed v1.4** *(D129)*: `session create | list | show | run`. The
verbs dispatch on `argv[0]` **before** the bare parser is built, which is why
that parser block is not edited and *the bare invocation keeps working* is a
fact about the source — pinned three ways: `cli.py` parsed for `required=True`
on both flags, a **literal set of the bare document's top-level keys**, and
`session run`'s determination compared byte for byte to the bare form's
stdout. `_determine_or_report` and `_block_for` are shared by both surfaces so
a fault cannot map to two exit codes. An illegal order is exit 1 and the
session's bytes are unchanged after it. **A request no tree governs cannot
become a `SessionRun`** — `SessionRun.determination` is a `Determination` and
`determine()` returns one of three things — so it prints its answer, exits 0
and stays `CREATED`; widening the contract would make `contracts` import
`determination` and reproduce T-100's cycle. The byte-identity test **passed
while the bare form moved** — a key added to `_render` appears on both sides of
the comparison — which is what the pinned key set now catches. It minted
nothing.
**`T-99` closed v1.3** *(D126)*: `--suggest` emits the `icd_suggestions` block
beside verdicts byte-identical without it, a request no tree governs declines
by name, and `eval/report.md` carries A11's section. **`T-100` opened v1.4**
*(D127)*: the session plane is the **fourth** storage port and the first thing
under `pa_agent/` that writes a file, the lifecycle is a table and `terminal`
is read off it rather than declared on the enum — a property there is an
import cycle that makes every module reach the session plane. **`T-101` closed
row 2** *(D128)*: `pa_agent/intake.py` takes JSON **text**, one
`MalformedIntake` covers all five ways a request can be unusable, and
**normalisation lives on the contract** so the two routes cannot diverge even
in principle. `T-129` then closed the last row off the path *(D130)*.

**The plan was re-read whole on 2026-09-24, and three things moved**
*(D123, D124, D125)*. **A11** borrowed A2's number without A2's asymmetry:
it scored green, yellow and red on one scale, so a review colouring every
candidate red would have cleared it while suggesting nothing, and its two
data-bearing clauses would have closed on one green and no yellow — the
shape `T-95` refused for A10, in the other direction. Precision is now gated
on the colours that **assert**, red is reported beside an all-red baseline,
the measured yellow is **A14's**, and `T-99` grades the seven candidates on
the five charts no row touched — measured: 12 candidates on 9 of 14 bundles,
5 of them graded — for zero new verifier claims, plus the answer for what
`--suggest` emits when the request short-circuits and there is no tree.
**`T-129`** took the `national_floor` load check out of the payer round and
ahead of v1.6, because v1.6's candidate NCD is nationally quantified and
D112's premise — both trees declaring 35.0, the floor exactly — expires
there; it closed with D130 and minted REQ-73. **The last three versions were reordered**: the reviewer's UI runs
last, behind both engine rounds, since it renders every other version's
output and both rounds that used to follow it change what it renders. Gates,
stories and task ids stayed with their content, so A15 and `T-111`–`T-116`
are still the UI's and §11's gate table reads A16, A17, A15.

**`T-98` closed row 3** (D122): the review's one model turn exists, on
every runner and both tiers. `pa_agent/quotes.py` is a fourth port in the
shape of the first three, `pa_agent/agent/quote_agent.py` its ADK leaf, and
`history.run_review` consults it once per note for **every** table
condition by display, never by drug — then hands any yellow to Article V as
a `(candidate, quotes)` claim built by `build_history_claim_payload` under
`history-verifier-v1`, and demotes a rejection to red that says so. T-89's
re-ask core was parameterised rather than copied, and both direct extraction
recordings re-derive byte for byte under it. **Six recordings** in
`eval/history/` — direct, ADK inline and ADK tool-fetch on AI Studio and
Vertex, twelve notes each, the clone replayed by content — measured **0 of
60** `(note, condition)` pairs with any passage returned, so the figure the
round yields is a fabrication rate of zero and `H4` reads **red with both
notes consulted**, two turns, no verifier claim: the verifier recording
stays at 38 a tier because `H4` shares E8's claim key. It minted REQ-67 (a
refused quote is red, never yellow — checked through the real anchorer on a
hand-written note) and REQ-68 (the turn is recorded, replayed and counted,
budgeted by `max_review_model_calls` apart from A6). The measured yellow and
the history verifier's recording are `T-110`'s.

**`T-97` closed row 2** (D119): `pa_agent/history.py` reads the knowledge table
through a third port and **Python assigns the tri-state**. It minted REQ-63
through REQ-66 and moved no verdict, span, recording or baseline status — there
is no field through which a suggestion could reach one. Three things it found,
each of which changed the design. **No chart in the corpus could produce a
green**: every candidate whose signal crossed already coded the condition, and
every uncoded one had no observation of that analyte at all — so `455d3f7d`
carries a declared lisinopril order and a declared creatinine, appended as text
because re-serializing the bundle turned RA3 from `PASS` to `ERROR` in one run.
**An ingredient never matches a prescription**, so the match runs through a
pinned RxNav expansion of 274 concepts. And a candidate whose signal was
**measured and did not cross** is neither green nor red but **withheld**, which
is the third state the board's exit did not name and the one H2 turns on. Row
2's exit was rewritten at open: a yellow is a model measurement and belongs to
`T-98`, which adds the recording and the `H4` row — red with the quotes
consulted and zero verifier claims, because the measured yellow is v1.6's
*(D120)*.

**`T-96` opened v1.3** (D118): `data/knowledge/` is a **second hashed
corpus** — five FDA labels fetched from DailyMed as SPL XML, verified by the
same `verify_sources.py --offline` under a **separate manifest**, because
*nine policy documents* is a checked claim about what this system adjudicates
against and five drug labels are not that. `medication_effects.json` is five
rows, each naming a source for all four of its claims: the effect (a span
into a hashed label), the ICD-10 code (NLM Clinical Tables), the SNOMED codes
a chart already carrying the condition would hold (the pinned Synthea jar's
own modules), and the structured signal (a LOINC code with a declared
threshold). It minted REQ-62. Nothing under `pa_agent/` read the file at
that close — `history.py`, the tri-state and `--suggest` were T-97 through
T-99 — and since T-97 `stores/knowledge.py` serves it and `history.py` reads
it. **`T-91`
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
LCD states either — D97's Palmetto correction, one row later.

**`T-93` closed row 3** (D113): that practice's patients and their rows.
Synthea's own `rheumatoid_arthritis` module supplies the diagnosis (SNOMED
69896004) and the drug (RxNorm 105585) and **no biologic or JAK inhibitor at
all**, so a 1000-patient Alabama run under seed 1003 gave two charts — one
with an active methotrexate order, one with none — and the combination
limitation's chart is a **declared clone of the first carrying one declared
prescription** (D73's shape). Rows `RA1` (both claimed criteria `MET`, the
three unclaimed abstaining), `RA2` (`NOT_COVERED`, citing the prescription)
and `RA3` ((b) abstains and never denies). **The engine needed nothing** —
no predicate, step, contract or tree moved, and only `select_patients.py`,
which is tooling, changed. What it did cost is a **verifier measurement**:
an eval row with a cited verdict is a claim `RecordedVerifierRunner` must
hold, so 30 claims became 33 on both tiers and v1.2's *zero model calls*
column is corrected rather than worked around — A10's claim is zero model
calls **in any gate**, and every gate still replays.

**`T-94` closed row 4** (D114, D115): the third practice in one row —
`us-abdominal-visceral-j5-j8-v1`, compiled from WPS's **L35755** and
**A57591**, a third contractor. One predicate kind the engine lacked
(`procedure_value_set_interval`), three criteria unclaimed because of the
document, one Synthea chart in Iowa and two declared clones, and rows
`US1`–`US4`. It minted REQ-61 and re-measured the verifier to 38 claims a
tier under `verifier-v6`, after two false-rejection rounds forced the
set-membership rule (D115).

**`T-95` closed row 5 and the version** (D116). `eval/report.md` carries
`## Cross-practice compatibility`: **24 criteria across four trees and three
practices, zero omitted**, each classed as evaluated by a kind an earlier
practice earned, by a kind this practice earned, or declared unclaimed, with
every unclaimed criterion's own note quoted and the categorical exclusions
counted apart. A tree now declares its **`practice`** — the grouping key
D111 deferred to this row, because two bariatric trees under two contractors
are one practice and `policy_version_id` cannot say so — and `KIND_ORIGIN`
in `build_report.py` records which practice and task earned each kind,
pinned against the **enum** rather than against the corpus so a tree
revision cannot rewrite it. **It minted nothing**, which is the version's
closing argument: §11 predicted a lab threshold and a medication trial
duration, no committed document states either, and a kind is earned by a
document rather than by a task that was promised one. It also found **two of
A10's three clauses held by no command** — `run_eval.py` is a baseline diff,
so *every row `PASS`* survived `--update-baseline` adopting a `FAIL`, and
*zero model calls in any gate* was pinned against two of the three scripts
that spend them — and closed both, because a row cannot close on a gate
two-thirds of which nothing checks.

Row 8 of v1.1
closed with D107: P6's path for REQ-44/47 is written down and deliberately not
taken. The candidate that looks like it claims model adjudication does not —
Palmetto's `d` decomposes into extraction plus set membership plus a window,
so it adds an extractor and leaves Python deciding. Claiming REQ-44 needs a
predicate that cannot be compiled (the shape D97 rejected in Novitas's
"diligent effort"), a stability mechanism satisfying **Article II's own test**,
an oracle other than the deterministic path, and its own gate. None is met and
nothing on the roadmap schedules one; the unclaimed set stays closed at two.

T-90 (D106) took row 7: the whole corpus measured a second time
on **Vertex** and committed beside the AI Studio recordings, which did not
move. Fidelity is identical on both tiers, the verifier accepted the same 30
claims that round (38 since T-94) with no verdict moving, and 0 of 169 / 0 of
165 / 0 of 76 model offsets were usable — D18's fourth reproduction. D71's clause is answered *partly*:
the injected `set_model_response` round trip is an AI Studio artifact and
vanishes natively (tool calls 26 → 12, unescaped spans 4 → 0) while the token
overhead only halves, 4.12x → 2.14x against each tier's own direct runner.
Cost figures do not transfer between tiers and only within-tier comparisons
are quoted.

The §10 round was
opened as "v2" and renamed v1.1 by D105, which also fixed the versions after
it — v1.2 through v2.0, one story and one gate each — in spec §11, on the
board's `Roadmap after v1.1`, and in stories F3–F6. **D112 added two more**
after them: the payer axis (a request resolves by payer as well as by code
and state) and a **mimicked** commercial policy, synthesized and declared
synthetic because a real one cannot be committed or re-fetched by a gate —
which is what earns the predicate kinds Medicare's drug documents do not
state *(D111)*. **D125 then reordered the last three**, so v2.0 is the payer
axis, v2.1 the commercial mimic and v2.2 the reviewer's UI; the floor check
`national_floor` never had was `T-129`'s, closed ahead of v1.6 as REQ-73
*(D124, D130)*.
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

- **The knowledge corpus is five FDA labels, and it is not the policy
  corpus** *(T-96, D118)*. `data/knowledge/` holds what a *drug* is known to
  do; `data/policies/` holds what a *payer* covers. Separate manifests,
  because "nine policy documents" is a claim
  `tests/test_docs_consistency.py` checks and five drug labels must not be
  able to raise it. Both are verified by one
  `verify_sources.py --offline`. The labels are **DailyMed SPL XML fetched by
  setid** — measured byte-stable, credential-free, stdlib-parseable, and the
  extractor keeps six LOINC-coded sections and **never descends into a kept
  one**, because a label's numbered subsections may carry codes of their own
  (Zestril's do; warfarin's do not) and a flat collector doubles half the
  document. Two routes were tried and rejected on measurement:
  `accessdata.fda.gov` serves an abuse-detection page to any non-browser
  User-Agent, and **MED-RT has no `induces` relation** for any of these
  drugs — the RxClass query returns 11 KB of `may_treat` rows and looks like
  it worked.
- **Some clinical claims have no source this project can re-read, and the
  table says so** *(T-96, D118)*. D36 was re-checked a third time: SNOMED's
  browser and Snowstorm both answer *Access Denied* and NLM Clinical Tables
  carries no SNOMED identifier, so the route that works is reading the code
  out of the pinned Synthea jar's own modules — which is also the code a
  chart in this corpus actually carries. Hypotension is in none of them, so
  that row declares an empty `already_coded` with its reason rather than
  being dropped or invented. **Three of four module attributions written by
  hand were wrong** (the hyperglycemia code is in
  `metabolic_syndrome_care.json`, not a module named for it; the CKD stages
  are not in `chronic_kidney_disease.json`), which is why the provenance is
  derived from the jar rather than asserted.
- **The policy corpus is nine documents, three jurisdictions and three
  practices** *(D21, D29, D100, D101, D111, D114)*. The two added by T-94 are
  WPS's **L35755** (*Non-Invasive Abdominal / Visceral Vascular Studies*) and
  **A57591** (its billing and coding article) — a third contractor, and the
  first whose article names its CPT codes **in prose**: each ICD-10 group's
  paragraph states the procedures it supports and the codes that denote them,
  which is a stronger binding than the revision-history line J1745 rests on.
  Its own contractor table lists **Alabama** beside the J-5 and J-8 states, so
  Alabama is served by three trees from three practices and nothing collides —
  the collision that matters is a *code* bound for one state by two trees.
  **L35755 quantifies exactly one thing**: studies *"would not be performed
  more than once in a year, excluding inpatient hospital (21) and emergency
  room (23) places of services"*. It states no laboratory threshold, and
  neither does the rheumatology document, which is why v1.2's lab-threshold
  statement stays unminted *(D114)*.

  The two added by T-92 are
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
                     retrieval, runners, extraction, quotes, verifier,
                     reconcile, aggregate, determination, history, form,
                     intake, session, contracts, model_pin, tiers, cli
  agent/             ADK: extraction_agent, quote_agent, retrieval_agent,
                     patient_tools, policy_tools, tool_bounds, agent (adk web
                     entry point)
  stores/            the six ports and their file-backed adapters —
                     policy.py, patient.py, knowledge.py, session.py, payer.py
                     and outbox.py. __init__ imports none.
data/policies/
  source/            ncd_100_1, a53028, r931cp, l34576, a56852, l35677, a56432,
                     l35755, a57591 + sources.json, answers.json (q1–q13)
  value_sets/        obesity_comorbidities, rheumatoid_arthritis,
                     abdominal_visceral_vascular_indications and
                     abdominal_visceral_vascular_studies (SNOMED — the last two
                     T-94's, D114); methotrexate and
                     biologic_dmards_and_jak_inhibitors (RxNorm, expanded
                     through RxNav and pinned — T-92, D111).
                     Each declares the one system its membership is tested in
  ncd_100_1_jf.json  Noridian JF's tree, policy_version_id ncd-100.1-jf-v1
  ncd_100_1_jjm.json Palmetto JJ/JM's tree, ncd-100.1-jjm-v1 (T-87, D101)
  infliximab_ra_jjm.json
                     Palmetto JJ/JM's infliximab tree, infliximab-ra-jjm-v1
                     (T-92, D111) — the second practice, same seven states
  us_abdominal_visceral_j5_j8.json
                     WPS J-5/J-8's ultrasound tree,
                     us-abdominal-visceral-j5-j8-v1 (T-94, D114) — the third
                     practice, a third contractor, seven states of its own
data/patients/
  manifest.json      the corpus pin — every bundle's hash (D73). It is **here,
                     not under bundles/**; select_patients.py --verify reads it
  bundles/           fourteen Synthea v4.0.0 bundles — six from the base seed,
                     one carrying the declared synthetic BMI-35.0 observation
                     (T-41, D73; E12's patient is note-free by declaration),
                     one declared clone of E4's chart re-addressed into
                     Alabama (T-88, D102), and the rheumatology cohort —
                     two charts from seed 1003's Alabama run plus a declared
                     clone of one carrying a declared etanercept order
                     (T-93, D113), and the ultrasound cohort — one chart from
                     seed 1004's Iowa run plus two declared clones of it, each
                     carrying one of the patient's own procedures re-coded as
                     a prior study (T-94, D114). Every clone is recomputed by
                     --verify
  notes/             fourteen chart notes, two per note-bearing chart as
                     <patient_id>/chart_note_1.txt and chart_note_2.txt (T-81,
                     D104), the clone's byte-identical to its source's per
                     document; plus notes/manifest.json — a second, separate
                     manifest, one record per document
  work/              gitignored: the Synthea jar and whichever full run a
                     --generate mode last wrote (three are declared)
data/knowledge/      the knowledge corpus (T-96, D118) — **not** the policy
                     corpus, and a separate manifest for that reason
  sources.json       five FDA labels, hashed; verified by the same
                     verify_sources.py --offline that verifies the policy nine
  source/            the extracted SPL text, one file per RxNorm ingredient
  medication_effects.json
                     the reviewed table: five (ingredient, effect) rows, each
                     naming a source for all four of its claims
  rxnorm_ingredient_products.json
                     the pinned RxNav expansion (T-97, D119) — 274 concepts over
                     the five ingredients, which is how a row's ingredient
                     reaches the clinical-drug codes Synthea prescribes
data/payers/         the payer directory (T-105, D134) — **not** a hashed
                     corpus, because it is synthesized and has no upstream to
                     re-download, so verify_sources.py does not reach it and
                     tests/test_payers.py holds it instead
  payers.json        two simulated recipients, each declaring itself simulated
                     and addressed under RFC 2606's reserved .invalid TLD; the
                     count is pinned as a literal (D51)
data/sessions/       gitignored — the session plane's write root (T-100, D127)
data/outbox/         gitignored — the outbox's write root (T-105, D134), one
                     <payer_id>/<artifact_id>.eml per submitted packet. Output,
                     not evidence: no manifest pins it and nothing re-hashes it
eval/
  run_eval.py        the baseline diff (T-10). Drift in **either** direction
                     fails; a case that starts passing is adopted with
                     --update-baseline and a commit (D27)
  run_agentic_eval.py  the fixed-vs-agentic differential (T-61). Bare is the
                     gate; --measure spends model calls, --rescore re-derives
                     the free half from the recording (D64, D91)
  build_report.py    T-22/T-28/T-27's generator; --verify is the ninth gate (D85)
  cases.json         the eval set — 33 labeled rows (§6's 15 + NP1 + J1 +
                     RA1-RA3 + US1-US4 + H1-H9; D75, D102, D104, D113,
                     D114, D119, D122, D126)
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
  verifier/          results.json — T-17's recording, 38 claims since T-94,
                     re-measured whole by T-89, T-81, T-93 and T-94, and on
                     both tiers; `verifier-v6` since D115
                     (D78, D102, D103, D104, D113, D115). The history claim's
                     recording, history_results.json, is T-110's (D122)
  history/           T-98's six quote recordings (D122): results.json and
                     results_vertex.json (direct), adk_results_inline[_vertex]
                     .json and adk_results_tool_fetch[_vertex].json — twelve
                     notes × five conditions each, 0 of 60 pairs with a
                     passage; results.json is the one every gate replays
spike/spike_001/     notes/, labels.json, results.json, run.py — five notes,
                     no patient
scripts/             check_gates, check_env, check_skeleton,
                     check_req_coverage, verify_sources,
                     select_patients, synthesize_notes, run_extraction,
                     run_adk_extraction, run_verifier_measurement,
                     run_quote_measurement, run_adk_quote_measurement
tests/               57 files
  fixtures/packets/  T-106's two committed packets (D136) — accepted_red and
                     no_suggestion, each a session.json and the packet.eml it
                     renders to. The only tracked session bytes in the repo;
                     data/sessions/ and data/outbox/ stay gitignored (D127)
docs/                constitution, spec, stories, tasks, decisions — exactly
                     the five of the precedence table and nothing else (D93
                     deleted the sixth, a plan doc that governed nothing and
                     contradicted the board)
```
