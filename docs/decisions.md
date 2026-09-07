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

## D17 — Spike 001 scores on the event date, anchors spans by quote, and `--verify` re-reads rather than re-runs

*D10 decided what spike 001 measures. This decides how it counts, which is the
half that can quietly produce a flattering number.*

**Chosen, four parts.**

*The match key is the event date.* An extracted `wm_event` matches a labeled one
when the ISO dates are equal. Precision is matched over extracted, recall is
matched over labeled, per note.

**Rejected: match on span overlap.** It credits the model for citing the right
passage while reading the wrong date out of it. c3 buckets by calendar month
under REQ-14, so a correct span carrying a wrong date is a wrong event with a
citation that makes it look right — the worst failure this system has, scored as
a success.

**Rejected: require date *and* span overlap.** Stricter, and it collapses two
independent failures into one number. Date accuracy and span accuracy get
reported separately so a bad number says which half broke.

*Spans are anchored by quote, not by model-emitted offsets.* The model returns a
verbatim `quote` plus its own `char_start`/`char_end`. Python locates the quote
with `str.find` and the **located** offsets are what get scored, gated, and
carried forward. The model's own integers are recorded and their agreement rate
reported as a finding.

**Why:** Article III requires the span be verified by slicing, not that the model
produce the integers. A quote that does not occur in the document fails on
`find` returning -1 — the same string comparison, at the same place in the
pipeline. Character counting is the one part of this the model is known to be
bad at, and gating the spike on it would fail extraction for a reason with a
trivial fix, teaching us nothing about REQ-8.

**Rejected: gate on model-emitted offsets.** Closer to the literal REQ-10
reading, and it is the number T-11 will have to reject against. But if it is
near-zero the spike reports "extraction is unreliable" when extraction was fine
and arithmetic was not. Measuring both costs one field and tells T-15 which
mechanism to build.

*Three runs at temperature 0, per-run and aggregate both recorded.* Fifteen
calls total.

**Rejected: a single run.** D10 already concedes five notes catch "this does not
work at all," not a rate. A single run cannot distinguish a model that is wrong
from a model that is unstable, and T-15 plans to reuse these notes as regression
cases — which is only sound if the same note yields the same events twice.

*`run.py` measures; `run.py --verify` checks the recorded artifacts and spends
no model call.* `--verify` re-hashes every note file, re-slices every recorded
span against the note on disk, and asserts the decisions entry quotes the
precision that `results.json` holds.

**Rejected: `--verify` re-runs extraction.** Then the gate's answer varies
between invocations, and the number in `docs/decisions.md` can silently diverge
from the number the gate just measured. It also spends free-tier quota — D5
throttles this to eight to eleven runs a day — every time anyone checks whether
the task is closed.

**Rejected: `--verify` checks only that the files exist.** A stale
`results.json` would then pass forever against notes that had changed
underneath it. The note hashes recorded at measurement time are what make the
re-read honest; a hash mismatch fails the gate and demands a re-measure.

*Each note gets a fresh ADK session.* `Runner.run_async` with a new session per
note, not `Runner.run_debug`, whose shared default `session_id` would carry note
N−1 into note N's context and contaminate every number after the first.

**Cost:** The quote-anchoring choice pushes a real risk into T-15 rather than
retiring it — a quote appearing twice in a note anchors to the first occurrence,
which is the wrong span whenever the second was meant. The spike records
multi-occurrence quotes as a separate count so T-15 knows how often that is
live. Scoring on the date means an event carrying the right date and a span
pointing somewhere unrelated scores as a hit; the separately reported span
numbers are what catch that.

**Reverses if:** Model-emitted offsets turn out to agree with the located ones
at a high rate, in which case quote anchoring is unnecessary machinery and T-15
takes the offsets directly. Or multi-occurrence quotes are common, in which case
anchoring needs a disambiguating window and the schema gains a context field.

---

## D18 — A quote anchors modulo whitespace, and the offsets recorded are still raw

*Reverses the anchoring half of D17 in one respect. D17 held that `str.find`
returning -1 means the quote was fabricated. It also means the document was
line-wrapped, which is a different fact with a different remedy.*

**Chosen.** Anchoring collapses every run of whitespace to a single space in
both the document and the quote, locates the quote in that normalized text, and
maps the hit back through an index table to offsets **into the unmodified
document**. What gets recorded, gated and carried forward is still a raw
`(document_id, char_start, char_end)` whose slice of the real file is the cited
passage — newlines and all. The acceptance test becomes
`normalize(text[start:end]) == normalize(quote)`.

**Why:** Article III requires the span be verified by slicing the source before
acceptance. It does not require the model to reproduce the source's line
breaks. The spike's own notes hard-wrap at about 76 columns, as EHR-exported
text does, so a quote long enough to cross a wrap point fails exact `find` for a
property of the document rather than any property of the claim. The failure rate
therefore rises with quote length — the mechanism penalizes exactly the quotes
that carry the most context. Worse, it makes one signal mean two things: `-1`
currently reads as "the model invented this," and a fabricated citation is the
single worst failure available to this system. Conflating it with a formatting
artifact destroys the signal that matters.

The verification predicate stays exact string equality. Only the equivalence
class changes, and whitespace-insensitivity has no tuning surface: two runs of
whitespace either collapse to the same string or they do not. Nothing here is a
judgment, and no model participates.

**Rejected: keep exact `find`, and treat this as the model's problem.** The
literal reading of D17, and it is the stricter gate. But measured over the 80
spans already recorded, 77 anchor exactly and 3 need normalization — all three
being the same long assertion in `n03_assertion_only`, the note whose entire
purpose is to carry an unsubstantiated claim. Under exact matching that note's
only citable passage is unciteable, so REQ-9's assertion path goes untested by
the spike that exists to test it. Failing a real citation for the source
document's word-wrap is a false negative in the one direction this project
cannot afford to be sloppy about, because it makes an honest model look
fabricating.

**Rejected: fuzzy matching on edit distance or a similarity ratio.** This is the
alternative that looks equivalent and is not. Normalization admits quotes
differing only in whitespace; a similarity threshold admits quotes differing in
*words*. It introduces a knob, and once a knob exists the gate's answer depends
on where it is set rather than on what the document says — which is precisely
the property Article III buys. A threshold generous enough to absorb a line wrap
is generous enough to absorb a changed date.

**Rejected: unwrap the notes on disk so exact `find` works.** Repairs the corpus
instead of the mechanism, and lies about the production case, where the span has
to point into whatever the source document actually is. It would also break
every recorded note hash and force a re-measurement — 15 calls against D5's
free-tier budget — to fix a defect in scoring rather than in extraction.

**Rejected: re-prompt the model for a shorter or unique quote on a miss.** Spends
model calls to fix something Python fixes for free, and makes the failure path
model-dependent. D17 already put on record that character arithmetic is the part
the model is bad at; asking it to retry is asking it to do the thing it cannot.

**Changing the anchor rule does not cost a re-measurement.** `--rescore` gains
the ability to re-anchor from the quotes already recorded in `results.json`,
which is sound only because `--verify` re-hashes every note first: re-anchoring
against a note that changed underneath the recording would be scoring a quote
against a document it never came from. Each span records which mode anchored it,
so the rate of normalization-only hits is a reported finding rather than a
detail buried in the mechanism.

**Cost:** Whitespace-insensitivity admits more than line wrapping. A vitals
table whose column gaps the model collapses into single spaces will now anchor,
and the raw slice returned covers a layout the model may have misread — the
citation is honest about *where* it looked while the reading of that region goes
unchecked. Collapsing whitespace also enlarges the collision surface for D17's
known multi-occurrence risk, since two passages differing only in spacing become
one string. Neither has appeared yet: across 80 spans, normalization moved no
already-anchored span, produced no new multi-occurrence hit, and every
normalized span round-tripped. That is three observations of one phenomenon in
one note, which is enough to justify the mechanism and not enough to call it
characterized.

**Reverses if:** A normalized hit anchors a span whose raw slice a human reads as
a different claim than the quote — the tabular case above — in which case
anchoring has to preserve intra-line spacing and relax only across line breaks.
Or normalization-only hits stay this rare in a larger corpus and correlate with
quote length, in which case a maximum quote length in the prompt retires the
mechanism and exact `find` returns.

---

## D19 — Spike 001 result: the extraction holds, the model's own offsets do not

*A finding, not a choice, so it carries no rejected alternative. It is logged
here because D10 made a measured number T-00's exit condition and because D2 —
the assumption the entire design rests on — was until now untested.*

**Measured** 2026-09-07, `gemini-3.5-flash-lite` at temperature 0, three
complete runs over five hand-labeled notes. Fifteen calls, 24,645 tokens,
108.66 s wall.

| | |
|---|---|
| event precision | **1.000**, every run |
| event recall | **1.000**, every run |
| REQ-9 exclusion recall | **1.000** — 51/51 traps held out |
| identical event dates across runs | all five notes |
| field agreement on matched events | 23/23 for `bmi_documented`, `diet_documented`, `activity_documented` |
| spans anchored | 80/80 emitted |
| spans needing D18 normalization | 3 (3.75%) |
| **model-emitted offsets usable** | **0/80** |
| multi-occurrence quotes | 0 |

**D2 survives first contact.** One call per note returned correct weight
management encounters with citable spans, excluded what REQ-9 excludes, and
returned the same dates on every run. Zero false positives across 69 scored
events. The stability result is the one T-15 most needed: these notes are usable
as regression cases, which was only sound if a note yields the same events twice.
D12's distinction also held — n03's unsubstantiated claim was captured as a
`program_assertion` and never as an event, in all three runs.

**The offsets are the real finding.** Not one of the 80 model-emitted
`char_start`/`char_end` pairs was correct, and not one sliced back to its own
quote *even under D18's whitespace-insensitive comparison*. This is not
off-by-one arithmetic; it is arithmetic that does not work at all. D17's
reversal condition — offsets agreeing at a high rate, so T-15 takes them
directly — is dead. Quote anchoring is not optional machinery, it is the only
reason this spike produced a number, and T-15 must build it. The model's own
offsets are not worth carrying past this spike.

**What this does not establish.** The corpus is five notes Troy wrote, scored
against labels Troy wrote. A perfect score on it means the approach does not
obviously fail; it is not evidence that it works, and D10 conceded this before
the measurement rather than after.

Three specific limits, so the 1.000 is not quoted later as more than it is:

- **The traps are easier than the headline suggests.** Of the 51 trap instances,
  30 are `unrelated_section` — a colonoscopy date under HEALTH MAINTENANCE, an
  immunization, a date in family or past surgical history. Excluding those is
  not the hard part of REQ-9. The REQ-9-shaped traps are the other 21:
  `unsupervised_attempt` (9), `missed_visit` (6), `unsuccessful_contact` (6).
  **21/21 is the number to quote against the kill criterion**, and 21 is a small
  n. The missed-visit-inside-a-gap-month case that E10b turns on is 6 of them.
- **Precision is micro-averaged over four notes.** n03 labels zero events, so its
  per-note precision is undefined and it contributes only if the model invents
  an encounter. It did not.
- **One model, one temperature, one prompt.** Nothing here says the prompt
  survives real EHR text, whose section headers and wrapping differ from notes
  written to be read.

**Kill criterion cleared.** REQ-9 exclusion recall floor is 0.9; measured 1.000.
Retrieval work proceeds. No prompt tuning was spent — this is the first
formulation, which is itself a reason to distrust the number.

**What would change this:** a sixth note drawn from real chart text rather than
written for the spike. That is T-15's first job, not a reason to hold T-00 open.

---

## D20 — One module names the model, and the pin is checked against the record

**Chosen:** `pa_agent/model_pin.py` is the only tracked Python file permitted to
write a model identifier. It pins `gemini-3.5-flash-lite`. Every other module
imports it. `tests/test_model_pin.py` asserts two things:

1. the model a bare `python spike/spike_001/run.py` would measure on is the model
   `spike/spike_001/results.json` records;
2. no model identifier appears as a string literal in tracked Python outside the
   pin module.

**Rejected — edit `DEFAULT_MODEL` in `run.py` and stop.** That repairs today's
mismatch and leaves tomorrow's. The defect is not that the strings differ, it is
that nothing notices when they do: three model identifiers sat in two files
disagreeing with each other and with the recorded measurement for a whole commit,
and both gates returned zero the entire time, because each gate was internally
consistent with the file it read. A one-line fix restores that condition with the
strings temporarily equal.

**Rejected — carry the model in `.env`.** `.env` is gitignored (working rule 10),
so the identifier a recorded measurement ran against would live outside the repo,
absent from the diff and unrecoverable from history. D3's argument applies
unchanged: a value that changes what a determination says belongs in git.

**Why the pin is `gemini-3.5-flash-lite` and not `gemini-2.5-flash-lite`.**
3.5-flash-lite is the model that produced D19. Pinning `run.py`'s existing
`DEFAULT_MODEL` would make the code self-consistent and the finding false — no
measurement on 2.5 exists anywhere in this repo. Between a recorded number and an
unrecorded default, the number wins. The same reasoning retires the third
literal: `pa_agent/agent/agent.py` carried an undocumented `gemini-3.5-flash`,
which is `adk create` scaffolding T-15 replaces and was never measured on
anything.

**Tier, per D5.** D19 was measured against **AI Studio**, on a `GOOGLE_API_KEY`
— the retry loop in `run.py` exists because of that tier's 429/503 behavior under
load. The pin is one identifier with two credentials behind it: a Vertex run uses
the same model name with Vertex credentials, and D5 requires final evals and any
demo go that way. So **D19's numbers are AI Studio numbers**, and a Vertex run of
the same corpus is a new measurement, not a confirmation of this one. Conflating
the two is how a demo ends up submitting data to the tier that trains on it.

**The check does not forbid re-measurement.** A deliberate move to another model
updates `results.json` and the pin together and the test passes again. What it
forbids is the two diverging in silence, which is the only failure mode that
leaves a recorded finding attributed to a model that never produced it.

**Cost.** Two, both accepted. `run.py` gains a `sys.path` insertion of the repo
root to import the pin, matching what `scripts/check_skeleton.py` already does —
there is no installed package yet, and adding packaging to avoid three lines
would be infrastructure the project has not earned (working rule 9). And the
literal scan reads string constants but skips docstrings, so prose may still name
a model: a docstring cannot be passed to a model call, which makes a stale one a
documentation defect rather than a provenance defect. Scanning them too would
tax every honest sentence about D19 in this repo.

**Reverses if:** a second model is legitimately required — a cheaper verifier
model under Article V is the likely one. Then the pin module holds two named
constants and each is checked against the artifact that records it. It does not
go back to loose literals.

---

## D21 — The policy corpus is two documents, hashed as extracted text, and the jurisdiction is pinned

**Chosen:** `data/policies/source/` holds two documents, stored as extracted
plain text and content-hashed:

| `document_id` | Source | Supplies |
|---|---|---|
| `ncd_100_1` | NCD 100.1, MCD `ncdid=57` | national coverage, and what is *not* nationally decided |
| `a53028` | Article A53028, MCD `articleid=53028` | every operational constant in the criteria tree |

`scripts/verify_sources.py` fetches with `--fetch` and verifies by default:
documents present, hashes match, a live re-download re-extracts to the same
hash, and every answer's quote slices out of the named document at the recorded
offsets.

**Rejected — hash the raw HTML.** It cannot work. The MCD emits a fresh CSP
`nonce` on every response, so two fetches one second apart differ. Measured, not
assumed: the raw pages differ while the extracted text is byte-identical across
independent fetches. A gate on the raw bytes would fail on the first re-run and
teach us to stop running it.

**Rejected — store the raw HTML alongside the text.** An artifact whose hash
cannot be reproduced is an artifact nothing can verify, and keeping one in the
tree invites a span into it. Article III wants offsets into the text we cite.
The manifest records the URL and retrieval time instead, which is the part of
provenance that survives.

**Extraction is structural, not a scrape.** Text is taken from the MCD's
`document-view-section` containers and normalized — entities decoded, whitespace
collapsed per line, blank runs capped. Stdlib `html.parser` only; no new
dependency (working rule 9). The extractor version is recorded in the manifest,
because changing it changes every offset in `answers.json` and that must break
the gate rather than pass quietly.

**The jurisdiction is pinned and it is not national.** A53028 is published by
Noridian Healthcare Solutions, A/B MAC, **Jurisdiction F** — AK, AZ, ID, MT, ND,
OR, SD, UT, WA, WY. NCD 100.1 sets a national floor and quantifies nothing; every
number the criteria tree needs comes from Noridian. So the system determines
coverage *as Noridian would*, and saying otherwise in a review would be a
misstatement. A different MAC is a different criteria tree over the same NCD.

**Why two documents rather than one.** NCD 100.1 contains the word "month"
exactly once, in an unrelated passage about supplemented fasting, and contains
"supervis", "consecutive", "documented" and "duration" zero times. Its only
prior-treatment requirement is the unquantified "previously unsuccessful with
medical treatment for obesity". A criteria tree built from the NCD alone could
not express c2, c3, c4 or c5 at all. The spec anticipated this — open question 1
already named A53028 — and this entry records that it is load-bearing rather
than supplementary.

**Answers carry spans or they do not count.** Each of the three answers records
`(document_id, char_start, char_end)` plus the verbatim quote, and the gate
re-slices the document and compares. An answer whose quote does not appear
exactly once in its document fails the fetch, so a paraphrase cannot become a
citation.

**Reverses if:** the target jurisdiction changes, or a second MAC is added. Then
`document_id` stops being unique per question and the answer set grows a
jurisdiction key. It does not reverse by editing the constants in place —
D3's argument holds, a coverage-rule change arrives as a reviewable diff.

---

## D22 — 43775 is not nationally covered, and that is not `NOT_COVERED`

**Found while closing T-02**, against source text rather than memory.

NCD 100.1 places laparoscopic sleeve gastrectomy in **three** states, not two:

- nationally non-covered **only** "prior to June 27, 2012";
- on and after that date, "Medicare Administrative Contractors (MACs) acting
  within their respective jurisdictions **may determine coverage** of stand-alone
  laparoscopic sleeve gastrectomy";
- absent from section B, the nationally covered list, entirely.

And A53028 records that this MAC exercised that discretion: "This article is
revised to include contractor determined coverage for laparoscopic sleeve
gastrectomy (43775)". Under the document pair this project adjudicates against,
**43775 is covered** and runs the full criteria tree.

**Consequence:** T-25 and E3 are wrong as written. Both assume 43775 exits
through sc1 as `NOT_COVERED`, chosen from memory and explicitly flagged for T-02
to confirm. T-02 confirms the opposite. E3 needs a code that is nationally
non-covered for all beneficiaries — the NCD names open adjustable gastric
banding, open sleeve gastrectomy, open and laparoscopic vertical banded
gastroplasty, intestinal bypass surgery, and gastric balloon. Picking one and
re-pointing T-25 is **T-35**.

**Why this is not a small correction.** "Not nationally covered" and
"nationally non-covered" read alike and are opposite determinations — one
delegates, the other denies. Shipping 43775 as `NOT_COVERED` would have produced
a confidently wrong denial for a procedure the governing MAC covers, and US-1's
acceptance case would have certified it. That is Article IV's collapse in a new
place: the states that must not merge here are *denied* and *not decided
nationally*, and REQ-1's `NO_POLICY_FOUND` is the closer neighbor of the two.
Whether sc1 needs a third outcome is **T-36**, and it is a spec question, not an
implementation detail.

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
