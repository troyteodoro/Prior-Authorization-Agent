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
  ⚠️ The `venv/` currently in the working tree is **Python 3.14.7** and does not
  match. T-03 rebuilds it on 3.12.
- Top-level API surface: `Agent`, `Context`, `Event`, `Runner`, `Workflow`.
- `Workflow` is a Pydantic model. Its `edges` field is a **static list of edges
  supplied at construction**. Other fields: `retry_config`, `max_concurrency`,
  `state_schema`, `input_schema`, `output_schema`, `timeout`.
- `edges` accepts `dict[bool|int|str, ...]` for conditional routing. **Keying a
  branch off model output violates Article I. Never do this.** Branch keys come
  from deterministic Python values only.
- CLI verbs: `adk web`, `run`, `create`, `eval`, `eval_set`, `test`,
  `conformance`, `migrate`, `api_server`, `deploy`.
- `adk create <name>` writes `__init__.py`, `agent.py`, `.env`, `.gitignore`.

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

Nothing is built. The tree holds `docs/`, `LICENSE`, `.gitignore`,
`requirements.txt`, and an unmatched `venv/`. No package, no tests, no data.
Spike 001 (T-00) has not run, so D2 — the assumption the whole design rests on —
is still untested.

Active task: **T-03 — repo skeleton and environment.**
Exit condition: `python scripts/check_skeleton.py` — target layout present,
`google-adk` imports at exactly 2.8.0, `pa_agent.agent` imports, and an `adk web`
subprocess answers HTTP 200 on `localhost:8000` within the timeout before the
script terminates it.
Timebox: two hours.
