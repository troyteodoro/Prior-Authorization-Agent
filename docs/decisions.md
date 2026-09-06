# Decision log

One entry per non-obvious choice. What was chosen, what was rejected, and the
condition that would reverse it. Entries are appended, never edited — a reversal
is a new entry referencing the old one.

---

## D1 — The criterion is the unit of adjudication, not the document

**Chosen:** Decompose NCD 100.1 into a criteria tree and evaluate each leaf
independently against evidence retrieved only for it.

**Rejected:** Hand the model the full policy and the full chart and ask for a
determination.

**Why:** Per-criterion evaluation is independently measurable, so failures
localize to a criterion instead of to "the system." Context per call stays small.
The fan-out is parallel.

**Cost:** Criteria are not independent. NCD 100.1 needs a boolean tree, and c4
is scoped to the qualifying run that c3 identifies. The aggregator evaluates that
tree in Python; no model combines verdicts.

**Reverses if:** A policy appears whose criteria cannot be expressed as a tree
over independently-checkable leaves.

---

## D2 — Model extracts, Python decides

**Chosen:** The model's job is reading and structuring. Every date comparison,
aggregation, threshold, and boolean combination is code.

**Rejected:** Asking the model to answer criteria directly.

**Why:** Criteria c1 through c5 all reduce to predicates over one extracted event
list. One model call, five deterministic checks, and the checks cannot vary
between runs.

**Cost:** The extraction schema has to be right up front, and a fact the schema
does not capture is invisible downstream.

**Status:** Being tested by spike 001 before anything is built on it.

---

## D3 — The criteria tree is a file in git, not a database row

**Chosen:** `data/policies/*.json`, versioned in the repo.

**Rejected:** Firestore or Cloud SQL.

**Why:** A change to a clinical coverage rule should arrive as a pull request
with a diff and a reviewer. "c3 changed from four months to six" must be visible
before it affects a determination. Git is the audit trail and human review is the
deploy gate.

**Cost:** Does not scale past what a human can review.

**Reverses if:** Policy count exceeds what a reviewer can diff per refresh cycle,
which is roughly when the criteria compiler lands.

---

## D4 — Deterministic retrieval before vector search

**Chosen:** Code and date filters over FHIR, keyword matching over notes.

**Rejected:** Vertex AI Vector Search or RAG Engine.

**Why:** The queries are structured — did this happen, in this window,
documented this often. Embeddings are not obviously better at that, and Vector
Search adds an always-on billed endpoint plus a new evaluation surface.

**Reverses if:** Measured retrieval recall on the eval set falls below the
threshold set in the kill criteria. A number, not a hunch.

---

## D5 — Vertex for recorded runs, AI Studio free tier for iteration

**Chosen:** Develop against the AI Studio free tier. Final eval runs and any
demo go through Vertex.

**Why:** Free-tier inputs may be used to improve Google's models; Vertex does
not train on submitted data. Even on entirely synthetic patients, the tier that
does not train on inputs is the correct default for anything PHI-adjacent.

**Cost:** Free-tier rate limits throttle the eval loop to roughly eight to eleven
full runs per day.

---

## D6 — T-03 closes on a scripted probe, not on a served page

**Chosen:** Rewrite T-03's exit condition as `python scripts/check_skeleton.py`.
The script returns zero only if the target layout exists, `google-adk` imports
at exactly 2.8.0, the `pa_agent.agent` package imports, and an `adk web`
subprocess answers HTTP 200 on `localhost:8000` within a fixed timeout — after
which the script terminates it.

**Rejected — keep `adk web` serves a hello-world agent on localhost:8000`.**
`adk web` is a blocking server. It does not return, so it cannot return zero,
and "serves a hello-world agent" is settled by looking at a browser. That is the
"looks right" Article VIII exists to forbid. Per working rule 3, a task that
cannot close without violating an article is a wrong task, so the task changed
and the constitution did not.

**Rejected — assert only the layout and the imports, drop the server probe.**
Cheaper, faster, and it never flakes. It also does not test the one thing T-03
exists to find out. The risk this task retires is *does ADK 2.8.0 actually stand
up and serve on this machine*, and no import assertion answers that. A green
check that skips the risk is worse than no check, because it reads as proof.

**Why:** *(Article VIII)* The exit condition has to be a command that returns
zero, and it has to bind to the claim the task is making. Backgrounding the
server, polling the port, and killing it converts an eyeball check into an exit
code without weakening what is being checked.

**Cost:** The check is now process lifecycle management — spawn, poll, timeout,
terminate — so it can fail for reasons that have nothing to do with the repo.
The port stays fixed at 8000 to keep fidelity with the original condition, which
makes contention on 8000 a real flake mode. A stale `adk web` from a previous
run will fail the check rather than pass it, which is the correct direction to
fail but will be confusing the first time it happens.

**Reverses if:** The server probe flakes often enough that it costs more time
than it catches. The fix then is to split it: the layout and import assertions
gate the task, and the server probe drops to a documented manual smoke step.
Not to delete the probe silently.

---

## Kill criteria — written before the work, not after

- If criterion c3 precision stays below 0.8 after two distinct retrieval
  strategies, the decomposition changes. Prompts do not get tuned a third time.
- If extraction of `wm_events` is unreliable on hand-written notes, retrieval
  work moves ahead of everything else in the schedule.
- If a full eval run costs more than $2, the model tier drops before the eval
  set shrinks.

---

## Open questions for Narayan

1. Does the criteria-tree shape match how the team models coverage policy, or is
   there an existing internal representation this should conform to?
2. Is a payer-specific policy layer over the CMS baseline in scope eventually, or
   is CMS national coverage the whole target?
3. For a real deployment, would the determination packet be the deliverable, or
   the gap list that tells the specialist what documentation to go collect?
