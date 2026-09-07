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

**The policy corpus is two documents and one jurisdiction (D21).**
`data/policies/source/` holds `ncd_100_1` (national) and `a53028` (Noridian
Healthcare Solutions, A/B MAC, **Jurisdiction F**), stored as extracted text —
the MCD emits a fresh CSP nonce per response, so raw HTML has no reproducible
hash. NCD 100.1 quantifies **nothing**: no months, no visit counts, no recency.
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

**43775 is not the non-covered case (D22).** NCD 100.1 non-covers laparoscopic
sleeve gastrectomy only *"prior to June 27, 2012"*; after that it is delegated to
the MACs, and A53028 records this MAC covering it. E3 and T-25 assume the
opposite. **T-35** re-points them at a genuinely non-covered code; **T-36** asks
whether "left to the contractor" is a third sc1 outcome. Do not close US-1 on the
current E3 code.

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

Active task: **none. Pick the next one before writing code.**

T-15 is **not** unblocked: it depends on T-00, T-07 and T-11, and only T-00 is
closed. T-11 sits behind T-08. The ready set is **T-04, T-08, T-09 and T-35**.
T-09's schemas are what most other work sits behind. T-35 blocks US-1's close and
edits `docs/spec.md`, so it is the one to do before any US-1 work is trusted.
