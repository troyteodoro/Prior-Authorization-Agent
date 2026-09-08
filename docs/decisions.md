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
| **REQ-9-shaped traps excluded** | **21/21** — `unsupervised_attempt` 9, `missed_visit` 6, `unsuccessful_contact` 6. **Quote this against the kill criterion**, and 21 is a small n |
| unrelated-section traps excluded | 30/30 — a colonoscopy date, an immunization, a surgical-history date. Not the hard part of REQ-9 |
| REQ-9 exclusion recall | 1.000 across all 51 traps, of which only the 21 above are REQ-9-shaped |
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

## D23 — Every constant in the criteria tree carries a span, or carries `provisional`

**Chosen:** `data/policies/ncd_100_1_jf.json` is the criteria tree. Each
criterion declares its constants and a `source` citation —
`(document_id, quote, char_start, char_end)` into the T-02 corpus — and the tree
binds to that corpus by recording each document's `sha256`. A constant with no
source text is not written as a bare number: it carries `provisional: true` and
the `open_question` it awaits.

**Why a span per constant.** T-02 established that an answer without a span does
not count. A criteria-tree constant is the same kind of claim: "the recency
window is 12 months" is either in the source or it is someone's memory. Article
III's argument does not stop at patient facts — the reason it exists is that an
unverifiable citation is worse than none, and a threshold nobody can trace is
exactly that. The gate slices every quote back out of the hashed document, so a
paraphrased policy constant fails.

**Why bind to the source hashes.** A tree citing offsets into a document that has
since changed is citing nothing. The binding makes that a test failure rather
than a silent mis-citation, and it is the same guard T-34 put on the model pin.

**Rejected — inline the comorbidity value set.** A53028's Group 1 holds 543
ICD-10-CM codes. The tree names the value set `obesity_comorbidities`; T-05 owns
building and verifying it against real codes. A 543-entry literal in the tree
would make every diff unreviewable, which defeats D3's whole reason for the tree
being a file.

**Rejected — omit the provisional constants until they are answered.** A tree
missing `discrepancy_tolerance` would let T-33 supply its own default, which is
how an unreviewed number ends up in a determination. Present-and-flagged beats
absent: the flag is visible in the diff and in the test output, an absence is not.

### Three constants are provisional, and none of them is in the source

- **Criterion (a)'s lookback window.** REQ-11 evaluates (a) against "the most
  recent BMI observation within its lookback window". Neither document defines
  one. A53028's only windows are the 12 months for program participation and six
  months for the multidisciplinary evaluation, and neither governs the BMI
  measurement. Open question 4.
- **`discrepancy_tolerance`.** D14 requires a materiality threshold so that a
  structured 38.1 against a note 38.0 is not reported beside 38.1 against 45.
  That is a judgment about what wastes Sam's attention, not a coverage rule, and
  no source text bounds it. Open question 5.
- **`c5_min_documented_events`.** See below — the value is derivable, the shape
  is not.

### The c5 shape does not match the source, and the value hides it

A53028 governs c4 and c5 in **one sentence**: "The weight-management program must
include monthly documentation of patient's weight and BMI, current dietary
regimen and physical activity". REQ-40 renders the weight/BMI half as *every
month of the qualifying run*. REQ-37 renders the diet/activity half as *at least
`c5_min_documented_events` events in the run*. One sentence, two shapes.

The count shape is wrong in the permissive direction. With
`c5_min_documented_events` fixed at 4, a seven-month qualifying run documenting
diet and activity in only four of its months passes c5, while the source requires
it monthly. That is a false `MET` — the direction that matters, and the one an
abstention-first design is supposed to be built against.

**Chosen:** land `c5_min_documented_events: 4` — monthly documentation across
c3's four-month minimum run — and mark it `provisional: true` against open
question 6. At the minimum run length the constant is exactly right; above it the
constant is a floor where the source states a rate.

**Rejected — set it to a large number.** Trades a false `MET` for a false
`NOT_MET` and misreports the policy in the other direction.

**Rejected — quietly change c5 to c4's per-month shape.** REQ-37 is spec and
outranks a prompt or a preference. Rewriting it while implementing it is the
move working rule 5 exists to prevent, and the resulting tree would satisfy a
requirement nobody had agreed to. **T-37** carries the reconciliation as its own
task with its own decision.

**Reverses if:** T-37 resolves open question 6 toward the per-month shape. Then
`c5_min_documented_events` stops being a count and becomes a rate, and this entry
is superseded rather than edited.

---

## D24 — c5 is a rate, not a count, and REQ-37 is rewritten to say so

Supersedes D23's third section, on the condition D23 named. Answers spec open
question 6.

**Chosen:** c5 takes c4's shape. `c5_min_documented_events` is removed from the
criteria tree and replaced by `documentation_rate: "every_month_of_run"` — the
same constant name and the same value c4 carries, cited to the same span. c5 is
`MET` when **every** month of the qualifying run contains an event documenting
both diet and activity. REQ-37 is rewritten in `docs/spec.md` to match.

**Why.** The sentence is one requirement, not two:

> The weight-management program must include monthly documentation of patient's
> weight and BMI, current dietary regimen and physical activity (e.g. exercise
> program).

`monthly` modifies `documentation`, and `documentation` takes a three-item
coordinate object — weight and BMI, dietary regimen, physical activity. There is
no reading of that sentence in which the adverbial governs the first item and
lapses for the other two. The split was an artifact of drafting order: REQ-37 and
REQ-40 were written before T-02 had read A53028, so each guessed a shape and the
guesses disagreed. T-02 then answered it independently without anyone noticing —
open question 1 closed as **"Monthly"** on `a53028[6339:6503]`, which is the
identical span both constants already cite.

So this is not a judgment call between two defensible readings. One shape matches
the source and the other does not.

**What changes in behavior.** The case T-37's exit condition names: a seven-month
qualifying run documenting diet and activity in four of its months. Under the
count it was `MET` — four events clears a floor of four. Under the rate it is
`NOT_MET`, because three months of that run are undocumented. That is the whole
substance of the reconciliation, and it moves in the conservative direction: a
determination this system previously would have approved it now declines to.

**Rejected — keep the count, and set it equal to the run length at evaluation
time.** Arithmetically identical on the pass/fail bit, and worse everywhere else.
It puts a computation in the policy file, where Article II wants it in code. It
also destroys the gap list: a rate knows *which* months are missing and can tell
Sam what to go collect, while a count that came up short knows only that it did.
A count that must equal the run length is a rate wearing a disguise.

**Rejected — keep `4` as a floor and accept the permissiveness.** This is the
status quo D23 landed under protest. It is a false `MET`, and the entire argument
for this system is that it abstains rather than approves on evidence it does not
have. A design that tolerates a known false `MET` because fixing it costs a spec
edit has lost the argument it was built to make.

**Rejected — raise the count to a large number.** Carried forward from D23,
rejected for the same reason: it trades a false `MET` for a false `NOT_MET` and
misreports the policy in the other direction.

**Rejected — merge c5 into c4.** They now share a rate and a source span, so the
merge is tempting. They do not share a predicate: c4 asks for a documented BMI in
the month, c5 asks for diet **and** activity in the month, and a real chart fails
one without the other constantly. Merging them would report a single gap where
Sam needs two different things collected, which is Article IV's argument applied
one level down — states that read alike must not merge before anyone notices.
`requires_both_diet_and_activity` stays exactly as it is.

**Why this needed its own task.** REQ-37 is spec, and spec outranks a prompt.
D23 could have quietly written the per-month shape while implementing the tree
and been right on the merits; that is precisely the move working rule 5 exists to
prevent, because the resulting tree would satisfy a requirement nobody had
agreed to. The reconciliation gets a task, an entry, and a spec edit that is
visible in the diff.

**Reverses if:** a source turns up quantifying diet and activity documentation
separately from weight and BMI — a second jurisdiction's article stating a count,
or an A53028 revision splitting the sentence. Then c5 stops sharing c4's shape
and the count returns, sourced this time rather than provisional.

---

## D25 — Storage is two ports defined before T-09, and git stays the source of truth for policy

**Target set by Troy:** the end state is a production deployment reading a
database of insurance codes and a separate database of patient data. This entry
decides what that requires of the code being written now, and what it is not
allowed to change.

**Chosen:** T-09 defines two storage protocols and nothing else changes shape.

```
PolicyStore   resolve(code) -> PolicyRef | None
              get_tree(policy_version_id) -> CriteriaTree
              get_document(document_id) -> Document        # text + sha256

PatientStore  get_observations(patient_id) -> [Observation]
              get_conditions(patient_id) -> [Condition]
              get_notes(patient_id) -> [Document]
```

Local implementations read the repository as it stands today —
`data/policies/ncd_100_1_jf.json`, the hashed corpus in
`data/policies/source/`, and the Synthea bundles T-04 lands. Production
implementations are a policy database and a FHIR-backed patient store. No module
outside an adapter opens a file or holds a connection. That requirement is
**REQ-41**, added to `docs/spec.md` by this entry, and T-32 grows a second
assertion to enforce it.

**Why the seam lands now.** T-09 is what most other work sits behind, and T-24
and T-12 are the two tasks that would otherwise reach for storage directly.
Defined before T-09, production is a second adapter. Defined after T-25, it is a
rewrite of the resolver, the FHIR extractor and determination assembly — the
whole of US-1 and US-2. The cost is a few hours now against a task that has not
been started, and it is the last moment at which that price holds.

**Two ports, not one.** Article VI is the reason. A single `Store` handle is a
module that can reach both planes, and REQ-33's import-graph assertion stops
being provable the moment such a type exists. Two protocols, two adapters, two
connections, and the only object crossing between them is still the compiled
`Criterion`. The production shape is *more* constitutional than the local one:
plane separation becomes a connection boundary rather than a lint.

**What the database is not allowed to hold: criteria trees.** Article VII —
"criteria trees live in the repo. A change to a clinical rule arrives as a diff
with a reviewer. No policy change reaches a determination without human
approval." An `UPDATE` on a criteria-tree row is a clinical rule change with no
diff and no reviewer, which is precisely the event the article exists to prevent.
The split is therefore:

| In the database | In git |
|---|---|
| code → policy mapping | criteria trees |
| source documents, content-hashed, immutable, versioned | predicates |
| emitted determinations, audit trail | |

If trees must be queryable at runtime, the database holds a **read-only
projection synced at deploy time** with git as the source of truth. Same data at
the query, approval gate intact. `get_tree()` is on `PolicyStore` either way, so
this choice is invisible above the adapter.

**A defect this fixes.** REQ-7 says a document whose content hash changes
invalidates every span into it. Under files that is an outage every time a MAC
revises an article. Under immutable versioned document rows it is a no-op: an
emitted determination stays pinned to the document version it cited, which is
what REQ-4 already promises when it requires a determination be replayable.

**Rejected — read files directly and retrofit the seam later.** The retrofit
touches every module that consumes data, which by then is all of them, and it
lands after the eval set is labeled against their behavior. The current
`file_path` reads are three or four call sites that do not exist yet.

**Rejected — put the criteria trees in the database.** Article VII, stated
above. Not a tradeoff and not negotiable at the prompt level; a task that cannot
close without it is a wrong task.

**Rejected — build an actual database now.** Working rule 9: no infrastructure
the project has not earned. A Protocol and a file-backed implementation are not
infrastructure — no GCP, no Terraform, no container, no new dependency. The
production adapter is written when there is a production environment to point it
at, and spec §3's "local execution, no deployment" survives this entry unchanged.

**Rejected — a generic repository or ORM layer.** The ports are narrow on
purpose. Six methods that name exactly what this system reads are auditable
against Article VI by reading them; a generic query interface is not, because
what crosses the boundary becomes a runtime property of the caller.

**What this does not fix.** Code → policy lookup was never the part that did not
scale. c1 through c5 are hand-written Python for bariatric surgery's shape, so
policy #2 needs a developer and not a row. Two databases and ten thousand codes
produce a system that can resolve ten thousand policies and adjudicate one. The
predicate DSL that would change that is a separate problem, deliberately not
opened here, and this entry should not be read as having addressed it.

**Reverses if:** the production patient store turns out not to expose stable
document identity — notes that cannot be addressed and re-fetched byte-identical.
Article III's spans have nowhere to anchor in that case, and the failure is
upstream of the port design. Measure it against one real store before writing the
production adapter.

---

## D26 — REQ-2 names a field no artifact carries, and its absence test collapses three outcomes into one

**Found in a design walkthrough, not while closing a task.** Recorded that way
because the provenance is the point: nothing on the board would have caught this
until T-24 tried to run, and T-24's exit condition would have been rewritten to
fit whatever it found.

Two defects, one root.

**1. The field does not exist.** `covered_procedures` appears exactly once in the
repository — in REQ-2 — and in no data file.
`data/policies/ncd_100_1_jf.json` carries `policy_version_id`, `jurisdiction`,
`sources`, `decision_expression`, `criteria` and `reconciled_facts`, and no
procedure list of any kind. T-24's exit asserts that E3 returns `NOT_COVERED` and
an unknown code returns `NO_POLICY_FOUND`. There is no artifact either assertion
can read.

**2. Absence carries three meanings and REQ-2 returns one verdict for all
three.** A code can be missing from a covered list because:

- NCD 100.1 names it **nationally non-covered** for all beneficiaries — open
  adjustable gastric banding, open sleeve gastrectomy, open and laparoscopic
  vertical banded gastroplasty, intestinal bypass, gastric balloon. A denial.
- CMS **delegated it to the MACs** and made no national determination. D22's
  finding about 43775, and D22 is explicit that this is not `NOT_COVERED`.
- It is **not a bariatric procedure at all** and no bariatric policy governs it.
  REQ-1's `NO_POLICY_FOUND`.

REQ-2 as written answers `NOT_COVERED` to all three. That is Article IV's
collapse sitting in the resolver, one layer beneath where D22 found it: D22
caught a single code filed in the wrong bucket, and this is the bucket structure
having too few buckets. The consequence is the one D22 already named — a
confidently wrong denial for a procedure the governing MAC covers, or for a
procedure this policy was never about.

**Chosen:** **T-38.** The criteria tree carries the procedure sets explicitly,
every code spanned to source the way D23 requires of every other constant, and
REQ-2 is rewritten to test membership in the non-covered set rather than absence
from the covered set.

**Rejected — fold it into T-24.** T-24 implements REQ-2. A task that also
supplies the data REQ-2 names is a task deciding what REQ-2 means, which is the
move working rule 5 exists to prevent and which D24 spent an entry arguing
against. It also edits `docs/spec.md`, and T-35's note already establishes that a
task editing a higher-precedence document is its own task.

**Rejected — add a bare `covered_procedures: [...]` and move on.** It clears the
only failing grep and preserves defect 2 intact. The resolver would still deny a
code no bariatric policy governs, and no acceptance case would see it: E3 is the
only case exercising sc1, and E3 is a genuinely non-covered code under either
design. A defect the eval set structurally cannot catch is the kind that has to
be caught in the tree.

**Rejected — merge it into T-36.** T-36 decides what the resolver *returns* for a
delegated procedure; T-38 decides what the tree *records*. Splitting them lets
T-38 land the source faithfully without pre-empting T-36 — the tree records a
contractor-determined set as a fact about the corpus whether or not the resolver
gives that set its own outcome. The exit conditions come apart cleanly too: T-38
runs against `tests/test_criteria_tree.py`, which has existed since T-01, while
T-36 runs against `tests/test_resolver.py`, which does not exist until T-24.
Merged, the task could not close until the thing it blocks was built.

**Ordering:** T-35 first, so the non-covered code it picks and spans is settled
before T-38 builds the list around it; then T-38; then T-24, which can finally
read something. T-36 adds its case to the resolver suite afterwards. T-38 is on
US-1's critical path — T-24 cannot close without it.

**Reverses if:** T-36 rules that a delegated procedure is indistinguishable from
a covered one and no second jurisdiction is ever added. The contractor-determined
set then has no consumer and folds into the covered set. It stays in the tree
either way, because it is what the source says, but it stops being load-bearing.

---

## D27 — The eval harness gates on baseline drift, and a case that cannot run is `BLOCKED`, not `FAIL`

**The exit condition forces the first half.** T-10 closes on
`python eval/run_eval.py` "runs and reports E3 failing", and Article VIII
requires that command return zero. So the exit code cannot mean "every case
passed" — on the day the harness is written, nothing it grades exists. Spec §8
already says as much: the harness is written before the components it grades and
a failing harness is the correct state on day two.

**Chosen.** Three case statuses — `PASS`, `FAIL`, `BLOCKED` — and a gate that
compares the observed status of every case against `eval/baseline.json` and
returns non-zero on any difference, in either direction. A case that starts
passing fails the gate exactly as loudly as a case that stops passing; the
baseline is then updated as a tracked diff. E3 lands labeled and **without a
procedure code**, pending T-35.

This makes US-1's stated close — "`python eval/run_eval.py` reports E3 passing"
— a bare command rather than a table someone reads. When T-35, T-38, T-24 and
T-25 land, E3 flips to `PASS`, the gate fails on drift, and the baseline update
is the commit that records US-1 closing.

**Rejected — exit non-zero on any failing case.** The literal reading of "a test
harness," and it contradicts T-10's own exit condition. A gate that cannot
return zero until the system is finished is a gate nobody runs while building
the system, which is the entire window in which it has value.

**Rejected — the exit code means only "the harness ran".** Simplest, and about
forty lines shorter; US-1 would close on `--require E3` instead. It was rejected
for the reason T-34 and T-39 both exist: a check that cannot detect its own
subject changing is not a check. Under it, E3 could begin passing — or a case
could silently stop passing — and the command returns zero throughout.

**Rejected — folding `BLOCKED` into `FAIL`.** Both read as "not passing," which
is precisely why they must not merge. "The system answered and answered wrongly"
and "the component that would answer does not exist yet" have different next
actions, and only the second names a task. This is Article IV's argument one
level above where the article states it, and REQ-28 already forces the same
distinction on this harness at T-30, where an `ERROR` must not be counted as an
abstention. The vocabulary is cheaper to get right now than to retrofit under a
labeled eval set.

**Blocking is discovered, never declared.** A `blocked_by: ["T-35", "T-38"]`
field on the case would be the T-39 defect with different spelling: it keeps
naming a task after that task lands, and nothing notices. The harness instead
catches `NotImplementedError` and records the message verbatim, and those
messages already name their own task — `LocalPolicyStore.resolve` cites T-38,
`LocalPatientStore` cites T-04. The only declared block is the absent
`procedure_code`, which is an absence rather than an assertion and so cannot go
stale.

**The baseline compares a status and a reason class, not a message.** Six pairs:
`PASS`; `FAIL` with `WRONG_OUTCOME`, `MODEL_CALLS_EXCEEDED` or
`UNEXPECTED_EXCEPTION`; `BLOCKED` with `CASE_UNSPECIFIED` or `NOT_IMPLEMENTED`.
Diffing the free-text reason would fail the gate when someone rewords an
exception, which trains a reader to update the baseline without looking at it —
the one habit that makes the whole mechanism worthless.

### E3 lands without a procedure code

**Rejected — land 43775 with a `known_wrong: T-35` marker.** Keeps the case
runnable end to end today. It also puts an acceptance case in the eval set that
asserts the opposite of what the source says: D22 established that NCD 100.1
non-covers stand-alone laparoscopic sleeve gastrectomy *only* "prior to June 27,
2012", and that A53028 records this MAC covering it. The failure D22 names is a
confidently wrong denial for a procedure the governing MAC covers; certifying
one in the labeled set is worse than having no code yet, because from that point
on the eval set agrees with the defect.

**Rejected — pick the non-covered code here.** Ten minutes of work and T-35
closes as a side effect. It is also the task choosing the answer it will be
graded against, which is the move working rules 5 and 6 exist to prevent and
which D24 and D26 each spent an entry arguing against.

So the case exists, carries its label (`NOT_COVERED`, zero model calls) and its
spec reference, and reports `BLOCKED/CASE_UNSPECIFIED` until T-35 supplies the
code. A1 asks that every case in §6 be present and labeled; this one is, and the
one field it lacks is the one field no task has yet earned the right to fill.

### Two smaller choices

**The scorer checks itself on every run.** Before scoring a real case the
harness scores four synthetic ones — a matching determination, a mismatched
outcome, a determination carrying a `CallMetrics` against a zero-call budget,
and a raised `NotImplementedError` — and asserts the classification of each. It
is the scoring branch that no real case can exercise until T-25 produces a
determination, so without it the code that decides whether US-1 passed would sit
unrun until the moment it decides whether US-1 passed. D19's caveats are the
precedent: an instrument that produces a number nobody can check produces a
number nobody should quote.

**The harness opens two files, and that is not a REQ-41 violation.**
`eval/cases.json` and `eval/baseline.json` are the grader's own ground truth,
not data the system under test reads; every system read goes through
`LocalPolicyStore`. T-32's second assertion is worded "no module outside
`pa_agent/stores/`," and its scan must therefore be scoped to `pa_agent/` — a
note for T-32, not a change here.

**Cost.** Every deliberate behavior change now needs a baseline update, and
`--update-baseline` is one command away from papering over a regression. The
baseline is a tracked file, so the update arrives as a diff with a reviewer,
which is D3's argument unchanged — but a diff nobody reads is not a reviewer.

**Reverses if:** baseline updates become frequent enough that they stop being
read. Then the baseline shrinks to the cases whose status is load-bearing —
today that is E3 — and the rest report without gating.

---

## D28 — E3 is open vertical banded gastroplasty, and a procedure code carries two citations because the corpus binds none

Closes the first half of spec open question 3 — E3's code. The second half, whether
sc1 needs a third outcome, stays open and stays T-36's.

### The finding that shaped this: neither document binds a non-covered procedure to a code

T-35's exit condition asked that E3's *procedure code* "cite `ncd_100_1` with a
span the way T-02's answers do." That cannot be done. `ncd_100_1` contains no
procedure codes at all — three five-digit numbers in 30,926 characters, all years
or identifiers — and the document says why outright:

> NCDs do not contain claims processing information like diagnosis or procedure
> codes nor do they give instructions to the provider on how to bill Medicare for
> the service or item.

A53028 is a billing and coding article and does no better for this purpose. It
reproduces the same six non-covered procedure **names** and adds exactly one fact
about their coding — that open adjustable gastric banding is "Billed with a Not
Otherwise Classified (NOC) code." The only CPT code anywhere in the corpus is
43775, which is the code D22 disproved.

So the coverage claim is spannable and the code binding is not, and the exit
condition as written demanded both from one span. This entry decides what to do
about that rather than quietly writing a code and calling it sourced — which is
exactly how 43775 reached the spec in the first place.

### Chosen — E3 is open vertical banded gastroplasty, CPT 43842

NCD 100.1 §C names it in a list scoped unconditionally: no date qualifier, no
delegation clause, no "may determine coverage." Both spans verified unique in the
hashed document:

| span | text |
|---|---|
| `ncd_100_1[7076:7166]` | "The following bariatric surgery procedures are non-covered for all Medicare beneficiaries:" |
| `ncd_100_1[7287:7338]` | "Open and laparoscopic vertical banded gastroplasty;" |

The bullet alone does not say *non-covered* — it is six words naming a procedure.
The header is what makes it a denial, so both are cited and the gate asserts the
bullet falls inside the header's list rather than somewhere else in the document.
A span pointing at §D's "may determine coverage" paragraph would slice back
perfectly well and mean the opposite; that is the D22 failure, and one span cannot
exclude it.

A53028 mirrors the same list at `a53028[9483:9629]` and `a53028[9848:9898]`, cited
as corroboration. It matters that the MAC reproduced the national non-coverage
without exercising discretion over it — that reproduction is precisely what
A53028 *did* do for 43775, and D22 is the entry about missing it.

**Rejected — gastric balloon.** The NCD names it and it is clinically the cleanest
denial available. Its billing is `43999` or an era-dependent C-code, and an
unlisted-procedure code is not a procedure identity: a resolver keyed on 43999
denies an unbounded set of unrelated stomach procedures that share only the
absence of a specific code. That is D26's bucket collapse in a new place, and it
would be certified by an acceptance case.

**Rejected — intestinal bypass surgery.** Named by the NCD, historic, and its code
binding is the least stable of the six. Its near neighbour 43847 — gastric bypass
with small-intestine reconstruction — is **covered**. A mis-binding here is D22's
failure running in reverse: a wrong approval rather than a wrong denial, and the
one this system has no verifier for.

**Rejected — open adjustable gastric banding.** First on the NCD's list, and
A53028 disqualifies it in writing: billed with a Not Otherwise Classified code, so
it has no code of its own to serve as a `procedure_code`. Recording why it was
rejected is worth more than the rejection, because the disqualifying fact is
itself spanned and someone will otherwise re-propose it.

### Chosen — two citation classes, and the second one admits it is not sourced

E3's code carries two records that must not be conflated:

- **`coverage_claim`** — the Article III claim, that this procedure is non-covered
  for all Medicare beneficiaries. A `(document_id, char_start, char_end)` into the
  hashed corpus, sliced back and compared by the gate exactly as every T-02 answer
  and every D23 tree constant is.
- **`code_binding`** — the claim that CPT 43842 *is* that procedure.
  `source_class: "external_code_system"`, `system: "CPT"`, `in_corpus: false`,
  `open_question: 7`. Not spanned, because there is nothing in this corpus to span
  it to.

The distinction is not bookkeeping. "This procedure is non-covered" and "this code
denotes that procedure" are different claims with different sources and different
failure modes, and this repository has a verification mechanism for the first and
none for the second. Merging them into one `source` field would let the second
inherit the first's credibility, and the artifact would read as fully sourced
while half of it rested on recall.

The NCD names both the open and laparoscopic approaches; 43842 is the open one, so
the binding claims a subset of the named non-covered procedure. If the binding is
wrong it is wrong in the conservative direction — a code that is not this
procedure, rather than a procedure that is not non-covered.

**Rejected — add a third source document.** A CMS transmittal or coding article
carrying the name-to-code mapping would give the binding a real span and close the
gap completely. It also reverses D21's two-document corpus, re-runs
`verify_sources.py` against a live fetch, re-hashes, and grows `answers.json` —
which is a task, not a code choice, and folding it in here would blow T-35's box.
It becomes **open question 7** and **T-40**, so the gap sits on the board rather
than in a comment nobody greps for.

**Rejected — mark the code `provisional: true`** against question 7, reusing D23's
vocabulary. It looks consistent and it is fatal: `Criterion.require()` raises on a
provisional constant by design (T-09), so E3 could never reach `PASS` and US-1
could never close. The flag exists to stop an unreviewed number reaching a
predicate; used here it would stop a reviewed code reaching a determination
forever. A blocker traded for a permanent one.

**Rejected — write `43842` as a bare string and move on.** It clears the failing
case in one line and silently promotes recall to sourced fact. That is the move
that put 43775 in the spec, and D22 is the entry that had to undo it. The cost of
being wrong is asymmetric and known: a confidently wrong denial, certified by an
acceptance case, which is the exact failure this project exists to argue against.

### Chosen — T-35's exit condition moves to two commands that run today

As written it was `pytest tests/test_resolver.py`, a file **T-24 creates**. T-24
depends on T-38, which depends on T-35. So T-35 could not close until the thing it
blocks was built — the defect D26 named when it refused to merge T-38 into T-36,
here in T-35's own text. The new exit:

```
pytest tests/test_e3_code.py
python eval/run_eval.py
```

The first proves the choice is cited and the citation slices back out of the
hashed document. The second proves E3 actually moved: adding the code advances it
from `BLOCKED/CASE_UNSPECIFIED` to `BLOCKED/NOT_IMPLEMENTED`, because `run_case`
now reaches `LocalPolicyStore.resolve` and gets the `NotImplementedError` that
cites T-38. That is baseline drift, the gate fails, and the acknowledgement is a
tracked diff — D27's mechanism doing the job it was built for, on the first
occasion there has been one.

Rewriting an exit condition is a design decision and gets logged before the code
(working rule 5, D10's precedent). Both defects were in T-35's text, not in the
work, which is why the rewrite is recorded here rather than in a new task.

**Reverses if:** T-40 lands a source that binds these codes, in which case
`code_binding` gains a span like everything else and `in_corpus` becomes true. Or
CPT 43842 turns out not to denote open vertical banded gastroplasty — which
nothing in this repository can currently detect, and which is the entire reason
the field says so.

---

## D29 — A third document enters the corpus for code bindings only, and it is a 2006 transmittal on purpose

Closes open question 7 by T-40's first branch: the code-to-procedure binding
gets a source, a hash, and spans, instead of an entry stating it rests on
recall forever.

### Chosen — CMS Pub. 100-04 Transmittal 931 (CR 5013, April 28, 2006), scoped to code bindings only

`https://www.cms.gov/Regulations-and-Guidance/Guidance/Transmittals/downloads/R931CP.pdf`,
document id `r931cp`. It is the claims-processing transmittal that implemented
NCD 100.1's February 21, 2006 reconsideration, and it does the one thing neither
corpus document does: bind procedure names to HCPCS codes, in CMS's own words.
It carries full descriptors for 43770, 43644, 43645, 43845, 43846 and 43847, and
for E3 it carries the binding in a single phrase — "Open vertical banded
gastroplasty ( HCPCS code 43842)". Measured before this entry was written:

- The PDF downloaded twice minutes apart is byte-identical
  (`4d2814b6…e24cee`), so D21's CSP-nonce problem does not apply — transmittals
  are static archival files, not MCD page renders.
- `pypdf` 6.18.0 extracts it to identical text across runs and across separate
  downloads. The corpus stores that extracted text, hashed, exactly like the
  other two documents; the manifest also records the raw PDF hash so a
  byte-level change upstream is distinguishable from an extractor change.
- Page 1 carries no rescission notice. Later bariatric transmittals (R2641CP's
  2013 LSG update among them) supersede its *coverage* content, which is
  exactly why the scope rule below exists.

**The scope rule: `r931cp` is citable for code bindings and for nothing else.**
It predates the June 27, 2012 LSG delegation, so its non-covered list names
laparoscopic sleeve gastrectomy unconditionally — stale coverage that A53028
and the current NCD text both contradict. A coverage claim spanned into this
document would be D22 rebuilt with a citation attached. Coverage claims span
`ncd_100_1` and `a53028` only; `r931cp` answers "which code denotes this
procedure named in prose," never "is this procedure covered." The gate for
T-38's sets must enforce the split, not just this entry.

What changes downstream: E3's `code_binding` gains a
`(document_id, char_start, char_end)` into the hashed corpus and `in_corpus`
becomes `true`; `tests/test_e3_code.py` flips its binding assertions from "the
artifact admits it is unsourced" to "the span slices back to a quote naming both
the code and the procedure" — the assertion D28 refused to write while it would
have been certifying recall. T-38 can now write every binding it needs as an
in-corpus span instead of several unsourced ones, which is the ordering clause
in T-40's own text and the reason this ran first.

**Rejected — the stay-unsourced branch.** T-40's second option, and the cheaper
one today. It leaves every covered code T-38 writes resting on recall — the
43775 mechanism, six more times — and T-38 was deliberately re-ordered behind
this task to avoid exactly that.

**Rejected — the current Claims Processing Manual, chapter 32.** The living
document carrying the same descriptors. It is mutable by design: the next
revision changes the hash, and D21's re-download check would read a routine
manual update as corpus corruption. The archival transmittal is frozen.

**Rejected — MLN Matters MM5013.** A provider-education rendering of the same
change request; citing the derivative when the instrument itself is stable
adds a hop for no provenance.

**Cost.** `pypdf==6.18.0` enters `requirements.txt` — the first dependency the
corpus machinery has beyond the stdlib. The extracted-text hash is hostage to
that exact version, so the manifest records the extractor per document and the
verifier refuses to check a PDF document under a different pypdf than the one
recorded, failing with "wrong extractor" instead of the misleading "document
changed."

**Reverses if:** the PDF stops re-downloading to the same bytes, or any binding
it carries is shown not to denote the named procedure. Either way the affected
bindings fall back to `in_corpus: false` against a reopened question 7 — the
D28 posture, which remains the honest one when there is nothing to span.

---

## D30 — The tree carries three procedure sets of procedures, not codes, and a facility billing list is not an identity

Implements D26's chosen fix. The tree gains `procedure_sets` with three keys —
`nationally_covered`, `nationally_non_covered`, `contractor_determined` — and
REQ-2 is rewritten to read membership in the non-covered set, with a code in no
set falling to REQ-1's `NO_POLICY_FOUND`.

### Chosen — set members are procedures in D28's two-citation shape

Each member is a **procedure**: a spanned `coverage_claim` into `ncd_100_1`
(scoped and corroborated the way E3's is), and a `codes` list of identity
bindings, each spanned into the document that names code and procedure together
— `r931cp` for the seven CPT bindings, `a53028` for 43775 and 0DV64CZ. After
D29 every binding written here is `in_corpus: true`.

T-38's exit condition asked for a span "naming that code" for every code, which
D28 had already proven impossible for a corpus that names one code — and the
same exit demanded 43842 in the non-covered set. The exit is rewritten (working
rule 5): coverage claims are spanned to quotes naming the *procedure*; code
bindings are spanned to quotes naming code and procedure in the binding
document. The non-covered set records all six §C procedures, four of which have
no code to carry — which is the fact the source states, not a gap.

**Rejected — code-keyed flat sets.** Simpler lookup, but open adjustable
gastric banding is "Billed with a Not Otherwise Classified (NOC) code" (A53028
says so, spanned) and gastric balloon has no specific code either. A set that
can only hold codes cannot record two-sixths of the national non-covered list.

### The finding that shaped the shape: A53028's facility lists overlap

Recording the ICD-10-PCS codes surfaced a fact that decides their role.
`0D160ZB` sits in both the open Roux-en-Y list and BPD/DS Group B. `0DV64CZ`
and `0DB64Z3` — the codes A53028's own revision history assigns to laparoscopic
sleeve gastrectomy, a *contractor-determined* procedure — also sit inside the
lap Roux-en-Y list under a *nationally covered* heading. In this source, a
facility code does not denote one procedure, and two of them cross coverage
categories.

So: **facility lists are recorded as spanned transcriptions, `identity: false`,
and are not lookup keys.** The pairwise-disjointness gate runs over identity
bindings only. A resolver keyed on facility-list membership would answer two
ways for 0DV64CZ, which is D26's collapse rebuilt out of billing data.

**Rejected — PCS codes as identity bindings.** The premise on which they were
approved ("real, sourced members for the covered set") half-survives contact:
the spans are real, the identities are not. 0DV64CZ keeps identity — two
separate A53028 passages bind it to LSG by name and the lap-RYGB appearance is
a documented anomaly of the R9 revision — but the RYGB/BPD-DS lists do not.

**Rejected — dropping the PCS data entirely.** The transcriptions are what the
source says, they are cheap to span, and the overlap they document is the
argument for this entry. Deleting the evidence of why identity fails would
leave the rule looking arbitrary.

### Two smaller choices

**43645 and 43847 hang under the RYGBP entry, and the grouping is declared
presentational.** Their descriptors say "gastric bypass … small intestine
reconstruction" without naming Roux-en-Y or BPD/DS; the transmittal's own
cross-references chain them to 43846/43644. Both candidate groupings put them
in the same set, so the set-level answer — the only thing the resolver reads —
is identical either way, and the entry notes say so. The rejected alternative
was a fourth covered entry named by descriptor alone, which would put a
procedure in the tree that the NCD's covered sentence does not name.

**The contractor-determined set is a fact about the corpus, not an outcome.**
Its resolver behavior is T-36's question and stays open; the set exists because
NCD §D and A53028's exercise of the delegation are both spanned facts (D26
already made this argument when it split T-38 from T-36).

**Reverses if:** D26's reversal condition — T-36 rules a delegated procedure
indistinguishable from a covered one and no second jurisdiction ever lands —
folds the contractor set into the covered set as a distinction without a
consumer. A disproved binding falls back to `in_corpus: false` per D29.

---

## D31 — The store reports membership, the resolver judges it, and the undecided branch raises

T-24's design. Two layers where the task's name suggests one, because the two
things a resolver does have different owners.

### Chosen — facts in the port, judgment in `pa_agent/resolver.py`

`LocalPolicyStore.resolve` answers only "which policy governs this code, and in
which of its three sets is it bound" — a `PolicyRef` extended with the
membership (`CoverageStatus`) and the entry's spanned `coverage_claim`, or
`None`. It returns the fact for **all three sets, contractor-determined
included**, because membership is a corpus fact T-38 landed and spanned, and a
port that refuses to report data it holds is a port whose adapter is making
policy judgments.

`pa_agent/resolver.py` owns REQ-1 and REQ-2: nationally non-covered membership
maps to `NotCovered` carrying the `policy_version_id` (REQ-4) and the claim to
cite; a covered code maps to `Resolved` and the caller proceeds to the tree; a
code the store returns `None` for maps to `NoPolicyFound`. The three results
are three types, not one type with a status string, so D26's "two different
lookups, not one absence" holds in the type system where a match statement has
to acknowledge it.

**The contractor-determined branch raises `NotImplementedError` citing T-36.**
Whether "left to the contractor" is a third sc1 outcome or resolves like a
covered code is T-36's question, and this repo's convention is that an
undecided half raises and names its task (T-09's stores, D27's discovered
blocking). No caller can observe a behavior T-36 has not chosen; T-24's exit
needs only E3 and the unknown code, so nothing blocks on the raise.

**Rejected — everything in `resolve()`.** One less module, and the production
database adapter would then have to reimplement coverage judgment instead of
just data access — every adapter a re-statement of REQ-2, each drifting
separately. T-36 would also land its decision inside an adapter, which is the
one place a clinical-behavior change should never live (Art. VII's spirit).

**Rejected — the store raises for contractor codes.** Simplest guard, but it
hides a spanned corpus fact behind an exception, and T-36 would then edit the
adapter rather than the judgment layer it is actually deciding about.

**Rejected — treat 43775 as covered now.** Source-faithful for Jurisdiction F
today — A53028 covers it — and it silently decides T-36's question in the task
that builds the thing T-36's exit tests. D24, D26 and D28 each spent an entry
rejecting that move under a different name.

### The contracts learn the sets, and the invariants move into validators

`CoverageStatus`, `CoverageClaim`, `CodeBinding`, `ProcedureEntry` and
`ProcedureSets` enter `pa_agent/contracts.py`; `CriteriaTree` gains
`procedure_sets`. Two D30 invariants become load-time validators the way
`Document` verifies its hash: an identity binding must be in-corpus with a
quote naming its code, and the three sets must be pairwise disjoint over
identity codes. `tests/test_criteria_tree.py` already gates the artifact; the
validators gate every *adapter*, so a production projection that collides
cannot construct a `CriteriaTree` at all.

### Recorded while building: a gate was passing by accident

`test_resolve_refuses_to_answer_until_t38` asserts
`pytest.raises(NotImplementedError, match="T-38")`. T-38's close re-cited the
message to T-24 — and the test kept passing, because the new message contains
"T-38" in a parenthetical. An accidental substring match in the gate guarding
the resolver, which is T-39's defect class in a fourth spelling. T-24 replaces
it with tests of actual resolution; the schema suite also stops pairing 43775
with `NOT_COVERED` in a synthetic (the combination D22 disproved — any code
string works there, so it costs nothing to use one that is true).

**Reverses if:** T-36 decides contractor-determined is not distinct and no
second jurisdiction lands — the raising branch then maps like covered and the
`CoverageStatus` value stays as a recorded fact. Or the production adapter
proves unable to supply claims and membership efficiently, in which case the
`PolicyRef` shape is renegotiated at the port, not bypassed around it.

---

## D32 — A short-circuit determination cites its denial and may have no patient, and NO_POLICY_FOUND is not a determination

T-25's design. Three shapes settle here, each the smallest honest one.

### Chosen — `patient_id` becomes `str | None`, and `None` means no patient was consulted

sc1 is a fact about the procedure: E3's eval case carries `patient_id: null`
because no patient data enters the answer, and the artifact should say the same
thing. A validator refuses `None` on any determination carrying
`criterion_results` — criterion verdicts are claims about a patient's evidence,
and a patient-less one would be adjudicating nobody.

**Rejected — a placeholder string.** `"unspecified"` in a required field keeps
the contract unchanged and puts a fabricated value in a reviewable artifact,
which is the move D28 refused for a code and D22 spent an entry undoing.

### Chosen — `coverage_claim` on the `Determination`, allowed only with `NOT_COVERED`

US-1 asks for the result "with the reason," and Article III says a reason is a
span. The assembly copies the resolver's claim — bullet plus scoping sentence —
onto the artifact, so the reviewable output cites its own denial rather than
deferring to whoever still has the resolver result in hand. The validator
refuses the field on any other outcome: an approval carrying a non-coverage
citation is a sentence that parses and means nothing.

Optional rather than required, in writing: sc2's `NOT_COVERED` (REQ-3, T-14)
may cite a criterion constant instead of a procedure-set claim, and that shape
is T-14's decision. If T-14 lands a different citation field, the validator
tightens then.

**Rejected — reason as prose.** A `reason: str` reads well in a demo and is
unverifiable by construction.

### Chosen — `NO_POLICY_FOUND` is `NoPolicyResult`, a type that is not a `Determination`

REQ-4 requires every determination to record the `policy_version_id` it was
evaluated against, and a code no policy governs has none — the impossibility is
the argument. US-1's second bullet ("rather than a denial") is enforced by the
type system the way D31 enforced the resolver's three answers: a caller cannot
mistake `NoPolicyResult` for a denial without noticing it holds no outcome at
all.

**Rejected — a fifth `DeterminationOutcome`.** It would let every downstream
consumer treat "no policy" as one more verdict in the same envelope, REQ-4
permanently unsatisfiable for one enum member, and D26's collapse waiting one
`elif` away.

### The rest, briefly

Assembly lives in `pa_agent/determination.py`; its tests create
`tests/test_determination.py`, the file T-19's exit already names — created by
the task that needs it first, extended by T-19, the same pattern as T-24 and
`tests/test_resolver.py`. A covered code raises `NotImplementedError` citing
**T-19**, the aggregator that turns criterion results into a determination;
the contractor-determined raise from D31 propagates untouched, still naming
T-36. The CLI constructs the one `LocalPolicyStore` (REQ-41) and prints JSON;
`NO_POLICY_FOUND` exits zero because a deterministic answer is not an error,
while the unbuilt paths exit non-zero naming their task.

Filling the harness's `_determine` seam flips E3 to `PASS`, which fails the
gate on drift; the `--update-baseline` diff rides in the close commit, and per
the board that commit is US-1 closing — D27's mechanism recording a story
delivered rather than a table quietly improving.

**Reverses if:** T-14 lands a unified citation shape for both short-circuits
(the `coverage_claim` validator then tightens or the field generalizes), or a
caller emerges that genuinely needs `NO_POLICY_FOUND` inside a determination
envelope — in which case the argument to reopen is REQ-4's, not convenience.

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
