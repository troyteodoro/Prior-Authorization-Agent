# Decision log

One entry per non-obvious choice. What was chosen, what was rejected, and the
condition that would reverse it. A reversal is a new entry referencing the old
one, never an edit to what was decided.

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

**Reverses if:** Measured retrieval recall falls below the 0.85 threshold in the
kill criteria. A number, not a hunch.

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

**Chosen:** `python scripts/check_skeleton.py`, which returns zero only if the
layout exists, `google-adk` imports at 2.8.0, `pa_agent.agent` imports, and a
backgrounded `adk web` answers HTTP 200 before the script kills it.

**Rejected:** "`adk web` serves a hello-world agent." A blocking server never
returns, so it cannot return zero, and "serves" is settled by looking at a
browser — the "looks right" Article VIII forbids.

**Rejected:** Layout and import assertions only. Cheaper and never flakes, but it
skips the one risk the task exists to retire: whether ADK 2.8.0 stands up on this
machine. A green check that skips the risk reads as proof.

**Cost:** Process lifecycle management. Port 8000 contention is a real flake
mode, and a stale `adk web` fails the check rather than passing it.

**Reverses if:** The probe flakes more often than it catches. Then it splits —
layout and imports gate the task, the probe becomes a documented manual step.

---

## D7 — ERROR is a distinct terminal state, separate from INSUFFICIENT_EVIDENCE

*Numbered D6 when written; renumbered for the collision. Reasoning unchanged.*

**Chosen:** A criterion may resolve to `ERROR` when the pipeline could not
complete evaluation. `ERROR` is recorded per criterion, but a `Determination`
containing one cannot be emitted.

**Rejected:** Folding pipeline failures into `INSUFFICIENT_EVIDENCE`. A reviewer
reading "c3 is not supported by this chart" goes hunting for records that were
there all along. A broken system becomes indistinguishable from poor
documentation — the most expensive confusion this system can produce.

**Rejected:** Letting exceptions propagate with no `ERROR` state. Loses
per-criterion granularity; a batch eval should report which criterion failed and
why, not die on the first one.

**Reverses if:** Reviewers act usefully on partial determinations. If a chart
review can proceed with four of five criteria resolved, blocking emission costs
more than it saves.

**Corrected by D9,** which found this amendment applied to the wrong loop.

---

## D8 — Error codes are classified retryable or terminal

**Chosen:** `ErrorCode` carries a retryability class. Timeouts and rate limits
consume a REQ-18a attempt and resolve to `ERROR` only at exhaustion. Schema
failures, span validation failures, and predicate exceptions resolve on first
occurrence.

**Rejected:** Abort on the first timeout. Simplest and fail-closed, but it
discards a determination over one dropped connection. On a tier throttled to
eight to eleven runs a day, that is expensive out of all proportion.

**Rejected:** Every failure consumes a retry. Tolerant of transient failures, but
it retries things that cannot succeed twice — unparseable JSON parses no better
on attempt two, and a span past the end of a document stays past the end.

**Why:** Both rejected options are this one with the class hardcoded. The
distinction tracks something real: whether an identical second call could
plausibly return a different result.

**Cost:** Every new code needs triage. An unclassified code defaults to terminal
— fail-closed, but a genuinely retryable failure left unclassified will abort
determinations rather than recover.

**Reverses if:** Retried timeouts exhaust more often than they resolve. Then the
retry buys latency for nothing and every code collapses to terminal.

---

## D9 — A rejected verdict is not an error, and does not retry

**Chosen:** Split REQ-18 by trigger. A verifier rejection resolves the criterion
to `INSUFFICIENT_EVIDENCE` on first rejection, no retry, determination still
emitted. A retryable model-call failure keeps D8's loop unchanged.

**Corrects D7.** D7 and D8 were both reasoning about transport failure, but
REQ-18's retry is triggered by the verifier judging a span insufficient — nothing
has failed. Applied there, D7's amendment violates Article IV: a thin chart
produces `ERROR`, REQ-24 aborts, and E8 crashes instead of abstaining.

**Rejected:** Retry retrieval on a widening ladder. More likely to find a span
outside the first query's reach, but unscoped work with its own eval surface,
presuming a failure nobody has measured.

**Rejected:** Resample the adjudicator at nonzero temperature. Retries the
component the verifier did not implicate, and makes a verdict vary between runs
on identical input — the condition Article II names as computation in the wrong
place.

**Rejected:** Keep a retry that re-runs deterministic retrieval unchanged. Under
D4 attempt two returns the identical span, so N model calls buy a conclusion
available on attempt one.

**Cost:** A criterion whose span sits just outside the retrieval window abstains
where one more attempt might have found it. T-27's recall number makes that
visible.

**Reverses if:** Abstention is dominated by criteria where T-27 shows the
ground-truth span was reachable but not retrieved.

---

## D10 — Spike 001 tests extraction fidelity, and closes on a measured number

**Chosen:** Retitle T-00 to extraction fidelity and close it on
`spike/spike_001/run.py --verify`, which writes measured precision, recall and
exclusion counts, then asserts the decisions entry quotes the measured number.

**The title described a violation.** Under Article II and REQ-14, Python does the
consecutive-month reasoning. Measuring the model at it measures a capability the
system may not use. The risk that gates T-15 is REQ-8 and REQ-9 — correct events
with valid spans, non-encounters excluded.

**Rejected:** Keep the two greps. `grep -q "Decision:"` was already satisfied by
D7's heading; both halves test for prose, and either passes on an entry reading
"Spike 001: Decision: inconclusive."

**Rejected:** Gate on an accuracy threshold. It makes a spike answering "the
model cannot do this" unable to close — but that is a successful spike, since it
reroutes US-4 before four days go into it.

**Cost:** Five notes catches "this does not work at all," not a rate. The
hand-labeling duplicates T-06 in miniature, accepted because T-06 depends on T-04.

**Reverses if:** Extraction passes the spike and fails T-21. The notes were too
easy and get replaced from the failures.

---

## D11 — A source disagreement blocks only when it changes the verdict

**Chosen:** Structured wins and the discrepancy is flagged, unless the two values
fall on opposite sides of the threshold — then criterion (a) resolves
`INSUFFICIENT_EVIDENCE` with `SOURCE_CONFLICT`.

**Rejected:** Structured always wins. Lets a note reading 36.2 against a
structured 34.8 produce a `NOT_MET` Sam has no reason to question.

**Rejected:** Any disagreement blocks. Abstains on rounding noise that never
approaches the threshold, inflating abstention and burning time reconciling
values that agree about the only thing being asked.

**Why:** Criterion (a) asks "is it ≥ 35.0," not "what is the BMI." Two sources
disagreeing about the second while agreeing about the first have not disagreed
about anything the determination uses.

**Cost:** Moves E10 out of US-2. The note BMI only exists after extraction, so
reconciliation runs as a separate predicate that can downgrade a verdict
criterion (a) already produced.

**Reverses if:** Threshold-crossing conflicts are common enough that criterion
(a) abstains routinely. That is a data-quality finding, and the answer becomes
reconciling at ingestion.

---

## D12 — An unsubstantiated assertion is its own gap, and zero is not a short run

**Chosen:** `gap_reason` gains `UNSUBSTANTIATED_ASSERTION`, detected from zero
events plus at least one `program_assertions[]` span. REQ-14 stops treating zero
events as a short run: one to three months is `NOT_MET`, zero is
`INSUFFICIENT_EVIDENCE`.

**This was blocking the refusal test.** REQ-9 forbids counting a bare completion
claim as an encounter, so correct extraction on E8 produces zero events — a run
of zero, below four, returning `NOT_MET` where E8 expects abstention. The label
would have been "fixed" by weakening extraction.

**Rejected:** E8 is `VERIFIER_REJECTED`. Only reachable through an extraction
bug, so the refusal test would pass when extraction is broken.

**Rejected:** E8 is `NO_EVIDENCE_RETRIEVED`, same as E7. Collapses the
distinction the enum exists for: E7 means find documentation of a program, E8
means find the visit notes behind a completion statement.

**Cost:** REQ-35 widens the extraction schema — the risk D2 names. Scoped to
spans, never feeds a verdict.

**Reverses if:** Sam treats the two values identically in practice.

---

## D13 — c1 is an existence check, and c5's frequency lives in the policy file

**Chosen:** c1 is `MET` on at least one event; REQ-9 already excludes
unsupervised attempts, so membership is the supervision claim. c5 requires
`c5_min_documented_events` from the criteria tree, scoped to c3's run.

**Rejected:** Hardcode "once" pending open question 1. Converts an open clinical
question into a code change and buries a number Dr. Vance should approve inside a
predicate.

**Rejected:** Evaluate c5 over all events. Lets a dietary note from an unrelated
visit years earlier satisfy a criterion about this program — the scoping error
REQ-15 already forbids for c4.

**Cost:** Two more optional fields on `wm_event`, defaulting false so c5 fails
closed.

**Reverses if:** T-00 shows extraction cannot distinguish dietary counseling from
a passing mention of diet. Then c5 is not model-extractable at v1 quality.

---

## D14 — Discrepancies are a second list, and only material ones are recorded

**Chosen:** `Determination.discrepancies[]`, advisory, never on the gap list.
Recorded only at or beyond a `discrepancy_tolerance` declared in the criteria
tree.

**Rejected:** Put it on the gap list. The gap list answers one question — what
should Sam go collect — and a discrepancy is not a gap. Mixing them means the
list stops answering its question cleanly.

**Rejected:** Flag every disagreement. A structured 38.1 against a note 38.0 is a
rounding artifact, and a list reporting it beside 38.1 against 45 trains Sam to
ignore the list.

**Rejected:** Drop the flag. Loses the case that matters — a packet citing 38.1
reaches a payer whose reviewer reads 45, and Sam finds out when the denial
arrives.

**Cost:** A second list is a second thing to ignore. Its value depends on a
frequency nobody has measured; T-22 makes it visible.

**Reverses if:** Material discrepancies appear in a large fraction of cases —
then reconcile at ingestion instead.

---

## D15 — c4 requires a documented BMI, not a derivable one

**Chosen:** c4 counts a month only when a BMI is documented in that month's
encounter. Weight plus an on-file height does not satisfy it.

**Why:** A documented BMI has one span. A derived one has a weight span, a height
span possibly from another document and another year, and arithmetic between
them. REQ-5 requires a span and Article V hands the verifier the cited span,
singular. Supporting derivation means a span-of-spans and a verifier that checks
arithmetic. The height may also be years stale.

**Rejected:** Derive whenever height is on file. More forgiving and Article II is
satisfied, but it redefines "documented" inside a coverage criterion — a policy
judgment the system would be making for itself.

**Cost:** The largest false-`NOT_MET` source in the design. E6's shape — weight
monthly, BMI sporadic — may be the common real chart, in which case c4 is the
noisiest criterion and Sam absorbs the unnecessary reviews.

**Reverses if:** A53028 puts the requirement on the measurement rather than its
documentation, or weight-only months dominate c4 failures. The fix is a
`c4_accepts_derived_bmi` flag with a recency bound on the height, not a predicate
change.

---

## D16 — The probe hits `/list-apps`, and the ADK agent stays a subpackage

*Refines D6, which specified the probe before ADK 2.8.0 was on the machine. D6's
shape stands: layout, imports, and a live server, scripted.*

**Chosen, three parts.**

*The agent is `pa_agent/agent/`.* `adk create pa_agent` had written `agent.py`,
`.env` and a nested `.gitignore` into `pa_agent/` itself. They move down a level
into `pa_agent/agent/`, which is where the repo layout always said they go.
`adk web pa_agent` resolves the subdirectory as the app.

*The probe requests `/list-apps` and asserts the body names `agent`.* Measured on
2.8.0: `GET /` returns **307**, redirecting to `/dev-ui/`. The exit condition as
written in T-03 and D6 — "answers HTTP 200" against the root — fails on a
perfectly healthy server.

*Port 8000 stays, with a preflight and a `--port` override.* The script checks the
port before spawning and fails with a named error when it is occupied.

**Rejected: collapsing the package, leaving `agent.py` beside `resolver.py`.**
Zero work, and `adk web pa_agent` serves it either way. But Articles I and II are
boundary claims — the graph is fixed Python, the deterministic computations are
Python — and the boundary is easiest to defend when it is a directory someone can
point at. A reviewer asking "where does the model touch this" should get a path,
not a list of which files in a flat package happen to be the model-facing ones.

**Rejected: probing `/dev-ui/` or following the redirect from `/`.** Both return
200 and both are closer to the letter of D6. `/list-apps` returns the discovered
app names, so it fails when ADK starts but does not find the agent — a state the
static UI asset reports as healthy. The stronger assertion costs nothing.

**Rejected: an ephemeral port.** It removes the contention flake D6 anticipated,
but D6 wants a stale `adk web` to *fail* the check, and a fresh port every run
makes the check pass while a stale server holds 8000. Contention is a real
condition worth surfacing, not routing around. The preflight converts it from a
confusing HTTP failure into a legible one.

**Cost:** The `/list-apps` contract is 2.8.0-specific and undocumented as stable.
An ADK upgrade can rename it, and the failure will read as "the server is broken"
rather than "the endpoint moved." The pin to `google-adk==2.8.0` is what makes
this acceptable; the check and the pin move together.

**Reverses if:** `/list-apps` disappears or stops naming apps in a later ADK,
in which case the probe drops to `/dev-ui/` and loses the discovery assertion. Or
the preflight fires on runs where no stale server exists, which would mean port
8000 is contended by something else on this machine and the port becomes a
constant in the script rather than a default.

---

## Kill criteria — written before the work, not after

- c3 precision below 0.8 after two distinct retrieval strategies: the
  decomposition changes. Prompts do not get tuned a third time.
- REQ-9 exclusion recall below 0.9 on spike 001: retrieval work moves ahead of
  everything else. Held tighter than event recall because a missed exclusion
  flips c3 to `MET` silently, while a missed event only costs an abstention.
- Retrieval recall below 0.85 on any criterion after a second keyword strategy:
  D4 reverses and vector search returns to the table. Measured by T-27 against
  manifest ground truth, so a miss means the fact was there and retrieval did not
  reach it.
- A full eval run costing more than $2: the model tier drops before the eval set
  shrinks.

The 0.9 and 0.85 thresholds were set before any measurement existed, which is the
only window in which a kill criterion is honest. Revising either after seeing a
number is moving the goalpost, and the entry that does it has to say so.

---

## Open questions for Narayan

1. Does the criteria-tree shape match how the team models coverage policy, or is
   there an existing internal representation this should conform to?
2. Is a payer-specific policy layer over the CMS baseline in scope eventually, or
   is CMS national coverage the whole target?
3. For a real deployment, would the determination packet be the deliverable, or
   the gap list that tells the specialist what documentation to go collect?
