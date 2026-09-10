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

## D33 — Contractor-determined is a third resolver outcome, and it proceeds to the tree

**T-36's decision**, closing open question 3's second half. The question D22
opened: REQ-1 returns `NO_POLICY_FOUND`, REQ-2 returns `NOT_COVERED`, and
neither describes "no national determination, delegated to the contractor."

### Chosen — a distinct result type that continues to the criteria tree

`resolve_sc1` returns `ResolvedByContractor` — a frozen model carrying the
`PolicyRef` — for a code bound in the contractor-determined set. Under this
corpus it proceeds exactly as a covered code does: A53028 records the MAC
exercising the delegation and covering 43775, so abstaining would contradict
the source, and the criteria the code runs against are the MAC's own. But the
type is not `Resolved`, so every caller has to acknowledge the delegation
before treating the code as covered. Zero model calls, deterministic, fixed
control flow (Arts. I, II). The spec learns this as **REQ-42**.

Article IV is the argument. "Covered because CMS says so" and "covered because
Noridian chose to" read alike under a single-jurisdiction corpus and diverge
the moment a second jurisdiction exists — a different MAC may non-cover LSG,
and if both states already flow through one type, the divergence lands as a
silent behavior change instead of a type error. D22 is the record of what it
costs when states that read alike merge before anyone notices; this entry
spends one class to make that merge impossible to rebuild by accident.

**Rejected — map contractor-determined to `Resolved`.** D31's pre-written
reversal path, and the smallest diff. But the distinction then survives only as
`PolicyRef.coverage`, a field no caller is forced to read, which is D26's
defect one layer up: two different answers expressed as one type, told apart
only by whoever remembers to check.

**Rejected — gate on whether the MAC exercised the delegation.** A third type
that proceeds only when the corpus records the MAC's exercise, with a separate
`DelegatedUndetermined` result otherwise. Most faithful to the three-state
reality, but the second branch has no exemplar under D21's single-jurisdiction
corpus — every contractor-determined entry in this tree records a disposition —
so the branch would ship untested, keyed off the presence of an optional
`corroborating_quote` field. When a jurisdiction without an exercise actually
lands, this shape becomes testable and the reversal clause below picks it up.

### The obligation this hands T-19

A determination for a contractor-determined code must cite **both** the NCD's
delegation and the MAC's exercise of it — the tree already records both halves
(the delegation quote as the entry's `coverage_claim`, A53028's exercise as its
`corroborating_quote`). An approval that cites only the NCD would be citing a
document that deliberately does not answer. T-19's exit condition gains that
line; per rule 5, this entry is the decision that edit records.

**Reverses if:** a jurisdiction enters the corpus whose MAC has *not*
exercised the delegation — the gated shape becomes testable and
`ResolvedByContractor` grows that branch. Or T-19 proves the covered and
contractor citation shapes identical in the artifact and no second jurisdiction
ever lands — then D31's fold-back clause applies and the type collapses into
`Resolved` with `coverage` as the recorded fact.

---

## D34 — A question's status is the subsection it sits under, and the gate reads only "Still open"

T-39's decision. `_open_questions()` in `tests/test_criteria_tree.py` collected
every `^(\d+)\.\s` under spec §9, so a provisional constant citing a resolved
question passed as readily as one citing an open question. The gate held today
only because questions 4 and 5 are genuinely open — it would have started lying
the moment T-13 or T-33 closed one.

### Chosen — spec §9 splits into `### Still open` and `### Resolved`, and status is membership

A question's status is which subsection it sits under, stated once, in the
document that owns the questions. The test parses the `Still open` subsection
only, and it refuses to parse a section it cannot read honestly: both headings
must exist, no question number may appear under both, and none may float
outside either. `~~strike-through~~` becomes styling with no semantic load —
the markup the exit condition forbids inferring from.

Question numbers never change when a question moves between subsections; IDs
are load-bearing and the tree's `open_question` fields keep resolving.

**Rejected — a per-question status line** (`*Status: open — awaits T-13.*` on
every entry). Keeps §9's narrative order intact, which is its real advantage.
But per-item markers go stale individually: a question whose answer lands in
its body while its marker still reads `open` makes two statements, and the gate
believes the marker. Subsection membership cannot disagree with itself — a
question is where it is. This was put to Troy alongside the chosen shape and
the subsections were chosen.

**Rejected — a structured sidecar file** (`docs/open_questions.json`). Easiest
to parse and it rebuilds T-39's defect class as a second file: the moment the
spec's prose and the sidecar disagree, something must decide which one lies,
and the gate would be reading the copy rather than the document that outranks
it. The spec states; nothing shadows it.

**Rejected — parsing the existing prose** (`~~` markup, or the "Closed by"
phrasing). Zero spec edits, and it is inference from formatting, which is the
defect with better regexes: the resolved entries close with different wording
each time ("Closed by D24", "**Monthly.**", "closed by T-36"), so the parser
either grows a phrase list that goes stale or matches loosely enough to
misread. The exit condition names `~~` inference as the thing to remove.

### The mutations that close it

Per the exit condition, asserted the way T-01's eight cases were — apply,
watch the named test fail, revert:

1. question 4 moved under `### Resolved` →
   `test_provisional_constants_name_an_open_question_that_exists` fails, the
   exact staleness the old gate accepted;
2. `a.lookback_months.open_question` pointed at 6, a resolved question → same
   test fails — under the old parser this passed;
3. the `### Still open` heading deleted → the gate fails loudly on structure,
   never returns an empty set that would vacuously pass an unrelated
   assertion.

The parser is also exercised by permanent synthetic tests (D27's
scorer-self-check pattern), because the branch that matters — a resolved
question being cited — has no live exemplar while the spec is healthy.

**Reverses if:** §9's narrative order proves load-bearing — prose that
cross-references between adjacent questions of different statuses and stops
making sense regrouped. Then the per-question status line returns, with a test
requiring exactly one status statement per numbered entry so a marker cannot
be absent or doubled.

---

## D35 — The population is a pinned Synthea release, six committed bundles, and a verify that never generates

T-04's design. The exit asks for six bundles, a recorded seed, and BMI spanning
33 to 45; this entry decides where the bundles come from, what "spanning" means
mechanically, and what the gate re-checks.

### Chosen — Synthea v4.0.0, tagged release jar, pinned by version and measured hash

`synthea-with-dependencies.jar` from the **v4.0.0** tagged release
(2026-03-05), the newest stable tag. The jar is downloaded into a gitignored
work directory, its sha256 measured at download and recorded in
`data/patients/manifest.json`; the jar itself is ~197 MB and is not committed.
Generation runs `java -jar` with a recorded seed, clinician seed, reference
date, population size and state, and the full command line is recorded in the
manifest. The state is **Washington — inside Noridian Jurisdiction F** — so the
population is plausible for the jurisdiction D21 pinned rather than
contradicting it.

**Rejected — `master-branch-latest`.** The newest asset on the releases page
and a moving nightly: the same URL serves different bytes after every merge.
That is the Claims Processing Manual problem D29 already rejected — a pin to a
thing that moves is not a pin.

**Rejected — building from source.** A gradle build resolves its own
dependency tree at build time and is a second build system in the repo;
working rule 9 twice over. The released fat jar is one file with one hash.

### Chosen — six selected bundles and the manifest are committed; the population is not

`scripts/select_patients.py` generates a population into
`data/patients/work/` (gitignored), extracts each patient's **most recent BMI
observation (LOINC 39156-5)**, selects six patients, copies their bundles to
`data/patients/bundles/`, and writes `data/patients/manifest.json` recording
seed, Synthea version, jar sha256, command line, and per-bundle: filename,
sha256, patient id, most-recent BMI and its date. Everything downstream (T-05
value set, T-06 manifests, T-12 extractor) reads the six committed bundles, so
they are corpus, committed and hashed exactly as T-02's documents are.

**Rejected — committing the whole generated population.** Only six bundles
have consumers. Two hundred uncommitted-to-anything JSON files make every
`data/` diff unreviewable, which is the argument D23 used against inlining 543
value-set codes.

**"Spanning 33 to 45", made testable:** all six most-recent BMIs lie in
[33, 45]; the minimum is **below 35** (a sub-threshold patient — E2's shape);
the maximum is **at least 40** (well above threshold, headroom for E10-family
cases); six distinct patients. The manifest also records whether the sub-35
patient carries an active type 2 diabetes condition, because E2 needs that
combination — recorded as a fact, not gated, since labeling E2 is T-06's work.
If the generated population cannot supply the combination, that is discovered
work for T-06 to name, not a reason to widen this gate.

### Chosen — `--verify` re-reads the disk and nothing else

It re-hashes the six bundles against the manifest, re-extracts each most-recent
BMI from the bundle on disk, re-asserts the span conditions and that a seed is
recorded. No network, no Java, no generation, no model. Same shape as
`verify_sources.py` and D17's `--verify`.

**Rejected — `--verify` regenerates and compares.** Synthea's cross-machine
determinism is unmeasured here, and a gate that depends on it flakes on the
first machine that disagrees; it would also demand Java and ~200 MB on every
check of whether the task is closed. The committed hashes are the ground
truth; regeneration is provenance, recorded but not gated.

**Rejected — hand-authored FHIR bundles.** Spec §3 names Synthea. Bundles
written by the author of the extractor encode the author's assumptions about
FHIR shape, and T-12 would then be tested against its own expectations.

### The patient-store raise moves to T-12

`LocalPatientStore` raises citing T-04 — "T-04 has not selected the Synthea
population." After this task that sentence is false, and D31 already recorded
what a stale task citation does to a gate. The bundles exist after T-04; the
adapter that reads them into `Observation`/`Condition` contracts is the FHIR
work T-12's exit names ("BMI observations and Conditions with dates, from all
six bundles"). The raise and `tests/test_schemas.py`'s `match="T-04"` both
move to cite T-12 in this close.

**Reverses if:** the v4.0.0 jar stops re-downloading to its recorded hash
(fall back to v3.4.0 and record the move), or a few hundred generated patients
cannot produce the BMI span (population size and age band are manifest-recorded
knobs; failing that, the selection criteria themselves are wrong and this entry
is superseded).

---

## D36 — The value set speaks SNOMED because the patients do, and every entry anchors to Group 1 in-corpus

T-05's design. Two facts force the shape, both measured before this entry was
written: A53028's Group 1 — the 543 "ICD-10-CM Codes that Support Medical
Necessity" — survives in the extracted corpus as `Code`/`Description` pairs,
so an ICD-10 code and its condition name can be spanned together; and every
Condition in the six committed bundles carries **SNOMED CT only**, no ICD-10-CM
coding at all.

### Chosen — `data/policies/value_sets/obesity_comorbidities.json`, keyed on SNOMED, anchored to ICD-10-CM

Criterion (b) is a set intersection over the codes the patient plane actually
supplies (REQ-12), so the lookup keys are SNOMED. Each entry carries three
things:

- the **SNOMED code and display**, verified to appear verbatim as an *active*
  Condition in at least one committed bundle — T-05's "every code appears in
  the population";
- an **`icd10_anchor`**: a `(document_id, char_start, char_end)` into A53028
  whose quote names the ICD-10-CM code *and* its description together
  (`"E11.9\n\nType 2 diabetes mellitus without complications"`), gated to fall
  inside Group 1's list rather than merely inside the document — the r931cp
  binding pattern and T-38's containment gate, reused;
- a **mapping claim marked `in_corpus: false`,
  `source_class: "external_code_system"`** — the D28 posture — because the
  claim "this SNOMED concept and this ICD-10-CM code denote the same
  condition" has no source in this corpus, and the one authoritative external
  source (NLM's SNOMED-to-ICD-10-CM map) sits behind UMLS licensing, which
  fails the credential-free re-download requirement D21 and T-40 established
  for corpus documents.

The file lives in a `value_sets/` subdirectory, not beside the tree:
`LocalPolicyStore._load_trees` treats every top-level `data/policies/*.json`
as a `CriteriaTree`, and a value set in that glob fails tree validation at
load — measured, not guessed, when the first draft landed beside the tree and
eight store tests errored. `source/` already models the pattern. How criterion
(b) reads the set at runtime is a port-shape question for T-13, deliberately
not answered here.

The file records A53028's sha256 the way the criteria tree does (D23), and
`status: "VERIFIED"` is a recorded claim the test re-derives on every run —
population presence, slice-back, containment — never a declaration.

**Two entries**, both face-unambiguous: SNOMED 44054006 (diabetes mellitus
type 2) anchored to E11.9, and SNOMED 59621000 (essential hypertension)
anchored to I10, whose Group 1 description — "Essential (primary)
hypertension" — is the same condition name.

### Rejected

**All 543 ICD-10-CM codes, inlined.** The literal reading of "rebuild the
value set," and it intersects with nothing: no patient carries an ICD-10 code,
so criterion (b) would fail silently for every patient — the exact defect
T-05's own description warns about. D23 already rejected the inlining for
diff-reviewability; this adds that it would not even work.

**Mapping the diabetic-complication conditions.** The population carries
nonproliferative diabetic retinopathy due to T2DM (1551000119108) and
microalbuminuria due to T2DM (90781000119102), and Group 1 carries E11.3x and
E11.29 — but the leaf-level mappings turn on severity and macular-edema
qualifiers the SNOMED displays do not state. A wrong entry produces a false
`MET` on criterion (b), the direction this design forbids; they enter later as
a reviewed diff if a manifest needs them (Art. VII).

**Hypertriglyceridemia, metabolic syndrome, CKD, emphysema.** Present in the
population and absent from Group 1 under their codes (E78.1, E88.81, N18.x,
J43.9 — all grepped, all missing). The source decides membership, not clinical
intuition; recording the exclusion is the point of this paragraph.

**A wider population scan to grow the set.** The set serves the eval cases
T-06 will author over these six patients; entries without a consumer are
surface area for the false-`MET` direction with no test that would catch them.

**Reverses if:** a redistributable SNOMED-to-ICD-10-CM mapping source lands —
the mapping claims gain spans and `in_corpus` flips, T-40's pattern exactly —
or T-06's manifests need a comorbidity the set lacks, which arrives as a
reviewed diff adding an entry, never as a predicate special-case.

---

## D37 — The document index is a plane-agnostic registry over verified Documents, and it opens no files

T-08's design. What sits between `Document` (hash-verified on construction,
T-09) and the span validator (T-11) is small on purpose: one place that
resolves `document_id` to text, slices spans mechanically, and makes REQ-7's
immutability a raised exception instead of a convention.

### Chosen — `pa_agent/index.py`, `DocumentIndex`, in-memory, fed by whoever holds a store

- `add(document)` registers a `Document`. Re-adding the identical document is
  idempotent; **rebinding an id to different content raises** — REQ-7 says a
  changed document invalidates every span into it, and the index is where that
  stops being prose. The conflict names both hashes.
- `get(document_id)` returns the document or raises naming the ids it holds —
  the `LocalPolicyStore.get_document` error shape, kept consistent.
- `slice(span)` takes an `EvidenceSpan` — the system's vocabulary, not a bare
  offset pair — and returns `text[char_start:char_end]`. Unknown document and
  out-of-range offsets raise. It does **not** judge the result: deciding that
  a span is fabricated or off-by-one is T-11's job (REQ-6); the index supplies
  the primitive T-11 rejects against. Reversed and empty spans cannot reach it
  because `EvidenceSpan`'s validator refuses to construct them.
- The module **opens no files and imports no store** (REQ-41). The caller
  reads documents through a port and hands them over; both planes get their
  own instance of the same class, and no instance ever holds both planes'
  documents — Article VI restricts what data an instance holds, not what
  vocabulary the two sides share (the `Document` docstring's argument).

**Rejected — the index reads `data/policies/` itself.** The obvious
convenience constructor, and REQ-41 names it: a module outside `pa_agent/
stores/` opening a file path. T-32's scan would catch it; better not to write
it.

**Rejected — an index per plane (`PolicyIndex`, `PatientIndex`).** Twice the
machinery for the same four methods, and the plane separation it advertises is
fake: the classes would differ in name only, and Article VI is enforced at the
store boundary and by REQ-33's import graph, not by duplicating a dict.

**Rejected — indexing normalized text.** D18 settled this for anchoring:
normalization is the *anchorer's* concern, raw offsets are what get recorded,
and an index that stored collapsed whitespace would return slices from a
document that does not exist. The index serves the unmodified text or nothing.

**Rejected — `slice` returning `None` on a bad span.** A `None` is exactly the
silent failure REQ-6 exists to prevent: the caller that forgets to check
propagates an absent citation as an acceptable one. Raising forces T-11 to
convert the failure into an explicit rejection.

**Reverses if:** documents stop fitting in memory — production notes at real
chart volume — in which case the index becomes a façade over the store's
`get_document` with the same interface and the immutability check moves to
comparison against the recorded hash rather than a held instance.

---

## D38 — The span validator raises a classified error, and its quote check is D18's predicate verbatim

T-11's design. The validator is Article III's gate: nothing downstream accepts
a claim whose span it has not passed. Three choices settle here.

### Chosen — one function, `validate(span, index) -> str`, raising `SpanValidationError` with a closed reason

`pa_agent/spans.py` exposes `validate`, which returns the verified raw slice
or raises `SpanValidationError` carrying a `SpanRejection` reason:
`UNKNOWN_DOCUMENT`, `OUT_OF_RANGE`, or `QUOTE_MISMATCH`. A closed enum, not
free text, because REQ-30 will fold these into `SPAN_VALIDATION_FAILED`
errors and T-29's fault injection has to assert which rejection fired —
prose reasons would give it nothing to match.

**Rejected — returning `None` or a bool.** The D37 argument, one layer up: a
falsy return is exactly the silent pass-through REQ-6 exists to prevent, and
every caller becomes a place the check can be forgotten.

**Rejected — a rejected span resolving anything here.** Mapping a rejection
to `INSUFFICIENT_EVIDENCE` or to `ERROR` is criterion-level judgment
(REQ-18's and REQ-23's, T-17's and T-26's); the validator reports what the
string comparison found and nothing else.

### Chosen — the quote check is `normalize(slice) == normalize(quote)`, exactly D18

Runs of whitespace collapse to one space on both sides, then exact equality.
D18 chose that equivalence class for anchoring and this validator inherits it
unchanged: the spike's notes hard-wrap at ~76 columns, and a validator
stricter than the anchorer would reject at acceptance the very spans the
anchorer legitimately produced. No similarity ratio, no edit distance — a
knob generous enough to absorb a line wrap absorbs a changed date (D18's
words), and this is the gate where that would be fatal.

A span with no quote is validated for mechanical existence only (non-empty
slice inside a known document) — REQ-6's floor. `EvidenceSpan` already
refuses reversed and empty offsets at construction, so the validator never
sees them; the test asserts that refusal rather than re-implementing it.

### Chosen — the validator takes a `DocumentIndex` and imports no store, no file API, no model

Same AST assertion as D37's, same reason. The exit condition's own grep —
"no model imported in the module" — is subsumed by asserting the import list
is exactly `__future__`, `enum`, and the two `pa_agent` modules it needs.

**Reverses if:** D18's reversal fires — a normalized hit whose raw slice
reads as a different claim — in which case the normalization tightens to
preserve intra-line spacing in both places at once, anchorer and validator
together, never one without the other.

---

## D39 — The patient adapter reports what the bundle says, and clinical status is a fact the contract must carry

T-12's design. The FHIR fact extractor *is* `LocalPatientStore`'s read side:
D25 put `get_observations` and `get_conditions` on the port, D35 re-cited the
raise to T-12, and building the parsing anywhere else would be a second module
that opens patient files (REQ-41).

### Chosen — the adapter resolves patients through T-04's manifest and verifies hashes on read

`LocalPatientStore` reads `data/patients/manifest.json`, maps `patient_id` to
its bundle file, and **verifies the bundle's sha256 against the manifest
before parsing** — the `get_document`/`sources.json` pattern (REQ-7): a
bundle edited on disk raises instead of silently feeding a different patient
to every criterion. Parsed bundles are cached per instance; an unknown
patient raises `KeyError`, because an empty chart for a patient who does not
exist would manufacture E7 (T-09's argument, kept).

### Chosen — observations and conditions are served whole; filtering is judgment and lives upstream

`get_observations` returns every observation carrying a top-level
`valueQuantity` and an `effectiveDateTime` — not just BMI. `get_conditions`
returns every coded condition. D31 drew this line for the policy store: a
port that refuses to report data it holds is a port whose adapter is making
policy judgments. Criterion (a) selects LOINC 39156-5, criterion (b)
intersects active conditions with the value set; both selections are the
predicates' judgment (T-13), not the adapter's.

**`Condition` gains `clinical_status: str | None`.** The contract could not
say whether a condition is active, and criterion (b) counting a *resolved*
condition is a false `MET` — the direction this design forbids. The adapter
reporting only active conditions was rejected for the same reason as
BMI-only filtering: it hides the fact instead of carrying it, and the
predicate that needs the distinction could never see it. An optional field
on a frozen model breaks no existing constructor.

**Scope, stated:** observations whose values live in `component[]` (blood
pressure) have no top-level `valueQuantity` and are not served. Nothing in
v1 reads them; the day something does, the contract grows a component shape
deliberately.

### Chosen — `get_notes` keeps raising, now citing T-07

Synthea bundles carry `DocumentReference` notes, and serving them is one
`base64.b64decode` away — rejected. The note corpus this system adjudicates
is T-07's: manifest-driven, gap months and traps placed on purpose, labeled
before extraction ever runs (T-06). Synthea's auto-generated notes assert
none of that, and a store serving them would hand T-15 a corpus with no
ground truth — eval cases graded against labels that do not exist. The raise
message names T-07 and deliberately does not contain "T-12" (D31's
stale-substring lesson, applied the same way D35 applied it).

**Reverses if:** a consumer needs an observation class the top-level-value
rule excludes (the component shape lands then), or T-07 chooses to embed its
synthesized notes as bundle `DocumentReference`s rather than sidecar files —
`get_notes` then reads bundles and the T-07 citation retires.

---

## D40 — The lookback is 12 months by decision, structured claims cite the bundle itself, and criterion (b) cannot be NOT_MET

T-13's design, in four parts. The first closes spec open question 4.

### Chosen — `a.lookback_months` is 12, decided by Troy (2026-09-08), not sourced

Neither corpus document defines a BMI recency window; question 4 was opened so
no task would invent one, and T-39 rebuilt the gate so resolving it here is
checked. Troy chose **12 months by analogy to A53028's program-participation
window**: the measurement must be contemporaneous with the 12 months of
evidence the rest of the determination examines. The constant carries a
`note` naming this entry and no `source` — writing a span for it would
fabricate a citation for a judgment, the exact move D28 refused for a code.

**Rejected — 6 months**, the tighter in-source analogy. The multidisciplinary
evaluation is a pre-surgical workup requirement, not an evidence-recency
rule, so its analogy is weaker, and doubling staleness abstention on a rule
the policy does not state trades one unsourced number for another.
**Rejected — unbounded**: a four-years-stale 36 adjudicates a patient who no
longer exists. **Reverses if** a source lands that actually governs BMI
recency (the constant gains a span, T-40's pattern), or eval shows staleness
`NOT_MET`s dominating criterion (a) — a data-quality finding, not a predicate
bug.

### Chosen — a structured fact's span points into the bundle document

REQ-5 forces the question: criterion (a)'s `MET` must carry a span, and a
FHIR observation lives in no prose note. The span points into **the bundle
file itself**: `PatientStore` gains `get_document(document_id)` — the
symmetry D25's reversal note anticipated when it demanded production stores
expose stable document identity — with `document_id` the bundle filename and
the hash the manifest's (REQ-7, same record `_bundle` already verifies). The
adapter computes each resource's exact extent in the raw text (the entry's
unique `fullUrl` anchors the search; `json.JSONDecoder.raw_decode` finds the
object's end) and serves `Observation`/`Condition` with an optional `span`,
so slicing the source at the offsets yields the resource JSON — Article III,
mechanical, against the file that was actually read.

**Rejected — a derived "structured facts" document.** A rendering the system
writes and then cites is the system reviewing its own homework; Article III
says source. **Rejected — spanning only the value or id line**: a reviewer
slicing it sees a fragment with no dates and no units; the resource object
is the observation.

### Chosen — criterion (a) has three verdicts and REQ-16 decides the stale case

Most recent BMI **within** the window: `>= 35.0` is `MET` (inclusive — E12),
below is `NOT_MET`, both citing that observation. BMI observations exist but
**only outside** the window: `NOT_MET` citing the most recent stale one —
REQ-16's rule, evidence present but outside a required window. No BMI
anywhere: `INSUFFICIENT_EVIDENCE`, no span. `as_of` is an explicit parameter;
a hidden `now()` would make the same chart answer differently on two days
without either input changing (Art. II).

### Chosen — criterion (b) is MET or INSUFFICIENT_EVIDENCE, never NOT_MET

At least `min_comorbidity_count` **active** conditions intersecting the value
set: `MET`, citing each contributing condition's resource. Otherwise
`INSUFFICIENT_EVIDENCE`: a chart cannot prove the absence of a comorbidity,
only fail to document one, and Article IV files undocumented under "cannot be
found". `NOT_MET` is unreachable for (b) and this entry says so on purpose —
the alternative reading (no qualifying diagnosis on file = proven absent)
would deny patients for thin documentation with a confident verdict.

The value-set codes arrive as a parameter; which port method serves them at
runtime is T-18's wiring question, deliberately open. The active-status
judgment lives here in the predicate, on the `clinical_status` D39 carried
through for exactly this consumer.

---

## D41 — sc2 is a spanned exclusion in the tree, it cites the patient's evidence too, and it never fires on stale data

T-14's design, settling the citation shape D32 deferred here.

### The source finding first

The NCD's *body* never states the exclusion. §B covers the three procedures
at BMI ≥ 35; §C's non-covered list is procedures, not populations. The
T2DM-with-BMI-under-35 determination lives in the document's transmittal
history — the 04/2009 entry: RYGBP, LAGB and BPD/DS "in Medicare
beneficiaries who have type 2 diabetes mellitus (T2DM) and a BMI less than 35
are not reasonable and necessary … and therefore are not covered." It is a
real CMS determination, it is in the hashed document, and it is
self-scoping: the sentence names the procedures, the population, and the
denial in one breath. That is where sc2's span points.

### Chosen — the tree gains `categorical_exclusions`, in the same evidence discipline as everything else

One entry, `t2dm_bmi_under_35`: a `claim` (a `CoverageClaim` — the history
sentence, no separate scoping quote because the sentence scopes itself), a
`bmi_upper_bound` constant of 35.0 (`lt`) **sourced to the NCD** — the one
place a numeric constant legitimately cites `ncd_100_1`, because this
exclusion is national and quantified by CMS itself, which is why it gets its
own gate rather than joining `_all_constants` under the every-number-is-
Noridian's rule — and a T2DM condition binding (SNOMED 44054006) in D28's
posture: `in_corpus: false`, `external_code_system`, because the NCD names
the diagnosis in prose and no corpus document binds it to a code.

**`procedure_scope: "nationally_covered"`.** The sentence names exactly the
three §B procedures. The exclusion does not touch the contractor-determined
set — it predates the 2012 delegation and never names LSG — so
`ResolvedByContractor` requests skip sc2 and proceed to the MAC's criteria.

### Chosen — the determination cites both sides, via `exclusion_evidence`

D32 left sc2's citation shape open. The policy side reuses
`Determination.coverage_claim` (the exclusion claim is a `CoverageClaim` and
the existing validator — only with `NOT_COVERED` — is exactly right). The
patient side is new: `exclusion_evidence`, spans citing the BMI observation
and the T2DM condition **in the bundle document** (T-13's machinery), valid
only alongside `NOT_COVERED`. A categorical denial that cites the rule but
not the facts it applied to would be half an Article III artifact — the
reviewer could check what the policy says and not what the patient's chart
says.

### Chosen — sc2 fires only on an in-window BMI, borrowing criterion (a)'s lookback

A most-recent BMI outside the 12-month window does not fire the exclusion;
the request falls through to the criteria path (which handles staleness as
`NOT_MET`, REQ-16). A categorical **denial** issued on evidence the criteria
path would refuse to *approve* on is confidence asymmetry in the wrong
direction. Borrowed rather than duplicated: one window, one constant, one
place to change it. The live population exercises both branches — the T2DM
patient fires at an `as_of` inside his BMI's window and correctly does not
fire today, when the same BMI is stale.

**Rejected — sc2 keyed on prose or on the value set.** The value set answers
"is this a qualifying comorbidity" (criterion b); sc2 asks "is this T2DM
specifically." Sharing the artifact would let a value-set edit silently
widen a national exclusion.

**Rejected — firing on any historical sub-35 BMI.** The most recent BMI is
the patient's state; an old low reading under a newer high one is not.

**Rejected — a separate `sc2_claim` field.** `coverage_claim` already means
"the spanned statement this NOT_COVERED denies under," and a second field
for the same meaning is D26's split-vocabulary defect inverted.

**Reverses if:** CMS restates the exclusion in the NCD body (the claim
re-anchors there), or a request arrives for a procedure outside the covered
set carrying T2DM below 35 — the scope rule then gets a live counterexample
to adjudicate instead of a note.

---

## D42 — Manifests are facts about notes that do not exist yet, assigned to the patients whose structured data can carry each case

T-06's design. The manifests are US-4's ground truth: written before the
notes, consumed by T-07 (which must synthesize notes honoring them), T-15's
extraction scoring, T-21's labels and T-27's recall measurement. Three
decisions and one discovered task.

### Chosen — one manifest per committed patient, in `eval/manifests/`, facts only

A manifest records what the patient's notes will contain: supervised
weight-management programs with dated encounters (each flagged for weight,
BMI, diet and activity documentation, with the note's BMI value where one is
documented), program assertions (claims without visits behind them), and
traps typed the way spike 001 typed them (`missed_visit`,
`unsuccessful_contact`, `unsupervised_attempt`, `unrelated_section_date`).
It records **no expected verdicts**: labels live in `eval/cases.json`
(T-21), and a manifest that stated its own expected outcome would be a
second label source that can disagree with the first. `as_of` is pinned to
**2026-09-01** — D35's population reference date — so "recent" and "stale"
are properties of arithmetic, not of when the suite runs.

Manifests live in `eval/`, not `data/patients/`: they are the grader's
ground truth in D27's sense — the system under test must never read them,
and the notes T-07 writes into the patient store are the only path a fact
takes from manifest to model.

### Chosen — the case-to-patient assignment follows the structured data

The committed bundles constrain who can carry what, and the assignment
records the constraint rather than fighting it:

| Patient | BMI (date) | Active VS comorbidity | Cases |
|---|---|---|---|
| Felipe | 37.65 (2025-10) | HTN | **E1** clean approval, **E11** two programs, **E10c** below-tolerance note BMI |
| Rayford | 34.26 (2024-03, stale) | T2DM, HTN | **E2** exclusion, **E7** no WM documentation |
| Georgette | 35.89 (2025-09) | — | **E4** three-month run, **E9** missed visits in the gap month |
| Christeen | 42.5 (2026-03) | — | **E5** program ended fourteen months ago |
| Linn | 39.23 (2026-04) | — | **E6** weight monthly BMI sporadic, **E10** same-side discrepancy |
| Tressie | 34.6 (2026-04) | HTN | **E8** assertion only, **E10b** threshold-crossing conflict |

Felipe is the **only** possible E1: the clean approval needs criterion (a)
and (b) both `MET`, and he is the one patient with an in-window BMI ≥ 35
*and* an active value-set comorbidity. E11 and E10c overlay him because
their expectations are compatible with approval (a second, non-qualifying
program; a note BMI within tolerance). E9 overlays E4 by construction — it
is E4's chart with the traps in the gap month. E10 overlays E6 and E10b
overlays E8 the same way: criterion-scoped expectations that do not collide.

**Rejected — synthetic patients shaped to order.** The bundles are the
population D35 committed; manifests that contradict a patient's structured
facts would make T-33's reconciliation cases meaningless, because the two
sources would disagree by authoring accident rather than by design.

**Rejected — expected verdicts in the manifest.** Stated above; it is D27's
"the baseline is the record" argument applied to labels.

### Discovered — E12 has no patient, and that is T-41

No committed patient carries a most-recent BMI of exactly 35.0, and Synthea
cannot be seeded to produce one to order. E12 is pinned today at the
criterion level (`tests/test_criteria_ab.py`); its harness row needs a
patient whose *structured* record holds the boundary value. That is
discovered work — **T-41**, before T-21 labels E12 — not a manifest's
problem to paper over with a note BMI, because criterion (a) reads
structured data and a note-only 35.0 tests reconciliation instead of the
boundary.

### Recorded — the authorship caveat propagates

D19 flagged that the spike corpus was five notes Troy wrote scored against
labels Troy wrote. These manifests are authored by the agent that is
building the system they will grade, which is the same epistemic position
with a different author. The mitigations are structural — every manifest
fact is mechanically cross-checked against the committed bundles where the
two overlap, T-07's exit forces the notes to honor the manifest by
assertion, and the diff lands under review — but the caveat belongs in the
record, not in a comment: **a perfect score on self-authored ground truth
means the approach does not obviously fail, nothing more.**

**Reverses if:** T-07 cannot synthesize a note honoring a manifest without
contradicting the bundle's structured story (the manifest moves, never the
bundle), or a real-chart corpus lands and replaces synthesis wholesale.

---

## D43 — No model writes the notes, every date in the corpus is accounted for, and the wrap is deliberate

T-07's design. The synthesizer turns T-06's manifests into the chart text
T-15's extraction reads.

### Chosen — deterministic Python templating from a seeded phrase bank, no model call

The notes are the **input to the model being measured**. A model-written
corpus would be phrased the way models phrase things, and extraction scored
on it measures model-to-model agreement rather than extraction fidelity — the
number would come out high and mean nothing. D19 already flagged the adjacent
limit ("nothing here says the prompt survives real EHR text"); generating the
text with the same family of model that reads it would make that gap
invisible instead of merely unmeasured. Spike 001's notes were hand-written
for this reason, and this is the mechanical version of the same choice.

It also buys reproducibility Article II would otherwise have to argue for: a
seeded generator emits byte-identical notes on every run, so `--verify` can
re-check hashes and T-15's regression cases cannot drift under a re-run.

**Rejected — a model synthesizes the notes from the manifest.** Faster, more
varied prose, and it forecloses the only measurement T-15 exists to make.
**Rejected — hand-writing six more notes.** What Troy did for the spike; it
does not scale to T-21's labeled set and puts the manifests and the prose in
two places that drift.

### Chosen — the corpus mimics EHR export, wrapped hard at 78 columns

Section headers in caps, `MM/DD/YYYY - ` encounter entries, metric units,
dates spelled the way the spike's notes spell them — so T-15's single prompt
covers both corpora, which its exit condition requires. The **hard wrap is
load-bearing, not cosmetic**: D18 exists because a quote crossing a wrap point
fails exact `find`, and it chose whitespace-insensitive anchoring on the
strength of three observations in one spike note. A corpus of clean single
lines would leave that path untested here and let it fail first in production.

Weight is derived from the note's BMI and the patient's **structured height**
(LOINC 8302-2, read through the port), so a note is internally consistent and
grounded in the bundle it belongs to. E10 and E10b disagree with the
structured BMI on purpose — that is the manifest's instruction — and the
derived weight follows the note, keeping the disagreement to the one fact the
case is about.

### Chosen — every date in the corpus is a manifest date, asserted

The synthesizer emits no date the manifest does not declare, and the gate
scans each note for date-shaped strings and refuses any it cannot account
for. This is what makes REQ-9 scoring sound: an invented date would be
extracted, scored against ground truth that never mentioned it, and counted
as a model failure that was really a corpus defect. The same scan refuses
trap **type names** in the prose — a chart that says "missed visit" where a
real one says "did not attend" is a test of keyword matching, not of REQ-9.

### Chosen — notes land in the patient store and the T-07 raise retires

`data/patients/notes/<patient_id>/`, committed and hashed in their own
manifest, served by `LocalPatientStore.get_notes` as `Document`s so spans
into them are checkable (Art. III). The raise citing T-07 goes away the
moment the notes exist, per the D35/D39 pattern; nothing downstream should
keep naming a task that has landed (D31's lesson).

**Reverses if:** T-15 scores near-perfectly on these notes and poorly on real
chart text, which would mean the templating encodes an easier problem than
the one that matters — the fix is real de-identified text, not a richer
phrase bank.

---

## D44 — `gap_reason` is required by a validator, propagates onto the gap list, and T-31 gates the vocabulary rather than the predicates

T-31's design. REQ-31's enum is the field that tells Sam what to go collect,
and it blocks T-16, T-17, T-19 and T-33 — so what it costs those tasks is
settled here rather than four times.

### Chosen — required on `INSUFFICIENT_EVIDENCE`, refused on every other verdict

A `model_validator` on `CriterionResult`, the same shape REQ-5's span rule
already has and for the same reason: "enforced by a validator, not by
convention" is what makes an invariant survive four consumers. An abstention
without a reason is a gap Sam cannot act on, and a `MET` carrying one is a
sentence that parses and means nothing.

The symmetry with REQ-5 is exact and worth stating: an abstention carries a
`gap_reason` and **no spans**; a substantiated verdict carries spans and
**no gap_reason**. The two validators together mean a `CriterionResult` is
either evidence or an explanation of its absence, never a mix.

**Rejected — an optional field the predicates fill in when they remember.**
T-16 writes eight abstention branches, T-17 one, T-33 three; an optional
field is eight chances to ship a gap that says nothing, and the eval harness
would score them as correct abstentions.

**Rejected — deriving the reason from the detail string.** Free text is what
REQ-30 already refuses for `error_code`, for the identical reason.

### Chosen — it propagates onto `GapEntry`, because that is where Sam reads it

`Determination.gap_list` is REQ-21's deliverable and US-5's whole argument is
that two gaps with different reasons must *read* differently. Carrying the
verdict but dropping the reason would put the distinction one dereference
away from the artifact that exists to show it.

### Chosen — T-31 gates the vocabulary; the predicates that emit it stay T-16's and T-33's

The exit asks for E7, E8 and E10b carrying three different values. Their
predicates do not exist yet — E8's `UNSUBSTANTIATED_ASSERTION` comes from
extraction (T-15) plus c3 (T-16), E10b's `SOURCE_CONFLICT` from
reconciliation (T-33). So this task asserts two things it can honestly
assert: that the three values are **distinct and reachable** through the
contract, and that each case's **manifest carries the shape** that justifies
its reason — E7 zero programs and zero assertions, E8 zero encounters with
an assertion, E10b a BMI pair straddling 35.0. When T-16 and T-33 land, they
assert the predicates emit them.

Putting the expected values in the manifests instead was rejected: D42 made
manifests carry facts and never expected verdicts, and a `gap_reason` is a
verdict about the evidence.

### What this costs the two live abstentions

Criterion (a) with no BMI anywhere and criterion (b) below its minimum both
become `NO_EVIDENCE_RETRIEVED` — the value REQ-31's table assigns to "nothing
found for this criterion", and the correct one for both: the next action is
to go find documentation. Criterion (b)'s abstention keeps D40's meaning
exactly (a chart cannot prove a comorbidity absent), now with the reason
attached.

**Reverses if:** a fifth reason turns out to name a genuinely different next
action. REQ-31's rule is that two values producing the same action are one
value, and the enum grows only against that test.

---

## D45 — The spike's prompt is promoted unchanged, anchoring becomes a module, and the gate spends no model call

T-15's design. The one place in the system where a model exercises judgment
(Art. I: it is a declared leaf, it routes nothing).

### Chosen — the prompt and schema move out of the spike essentially verbatim

D19 measured event precision 1.000, recall 1.000 and REQ-9 exclusion 21/21 on
**that exact formulation, with no tuning spent**. Rewriting it here would
throw the measurement away and start the prompt's history over at zero. So
`pa_agent/extraction.py` carries the spike's instruction text and schema, and
the spike's notes stay regression cases against them.

**One deliberate widening, and it is a change to the thing measured.** REQ-38
requires `diet_documented` and `activity_documented` to carry their own spans
when true — `WmEvent`'s validator refuses the flag without the span — and the
spike explicitly deferred those to T-15 to avoid testing something other than
event fidelity. Adding them means **this is not D19's measurement re-run**:
it is a new measurement on a wider schema, and any number from it is quoted
as such. D19's numbers stay attached to the narrow schema that produced them.

### Chosen — anchoring is promoted to `pa_agent/anchor.py`, not folded into `spans.py`

D19 killed D17's reversal clause: 0 of 80 model-emitted offset pairs were
usable, even modulo whitespace. Quote anchoring is therefore not optional
machinery and T-15 must ship it. It lands as its own module because
**locating and validating are different operations**: `anchor` answers "where
is this quote", `validate` answers "should this span be accepted", and the
validator must not depend on the locator — otherwise a bug in location could
launder itself through the check that exists to catch it. `spans.py`'s import
assertion (D38) stays exactly as tight as it was.

The model's own offsets are still recorded and their agreement rate still
reported, because that is the number that would reverse this choice, and it
cannot reverse if nobody keeps measuring it.

### Chosen — `pytest` verifies recorded artifacts; a separate script spends the calls

D17's split, for D17's reasons, now load-bearing rather than convenient: a
gate that calls a model is slow, rate-limited (D5 throttles to eight to eleven
runs a day), non-deterministic under an outage, and answers differently on two
invocations. `scripts/run_extraction.py` measures and writes
`eval/extraction/results.json`; `pytest tests/test_extraction.py` re-reads it
and spends nothing.

The staleness that split invites is answered exactly as D17 answered it, and
one step further:

- every note is **re-hashed** before its results are believed, so a corpus
  that moved under a recording fails rather than scoring;
- every recorded span is **re-validated through T-11**, not merely re-sliced —
  the gate uses the same rejector production will;
- the recorded model identifier must equal `PINNED_MODEL` (T-34's argument:
  a finding whose provenance can drift without failing anything is not a
  finding).

**Rejected — the gate calls the model live.** Honest in the sense that it
tests the real path, and it makes `pytest` a network-dependent, quota-consuming
coin flip. The measurement is a measurement; the gate is a gate.

### Chosen — labels come from T-06's manifests for the synthesized notes

The manifests already declare every encounter, its documentation flags, and
every trap with its type (D42), which is exactly the label shape the spike's
`labels.json` uses. Deriving labels from them rather than writing a second
label file keeps one source of truth — a second one would be free to disagree,
and the disagreement would look like a model error.

**Reverses if:** a note's correct extraction turns out to depend on something
the manifest does not declare, which would mean the manifest is not ground
truth for that fact and T-06's shape needs the field, not the labels a
workaround.

---

## D46 — A per-field span is located inside its own encounter, and T-15's exit gains the check T-11 structurally cannot make

**Found by running T-15's first measurement**, not by review. D17 named this
risk and the spike measured it at zero; the first real corpus put it at
**7 of 162 event-scoped spans (4.3%)**.

### The finding

`spans_multi_occurrence` jumped from 0 in spike 001 to 12 of 169 here, and
seven of those anchored to the wrong encounter: a `bmi_span` for the June
visit pointing at March's identical `BMI 37.6`, an `activity_span` for one
month landing on another month's identical phrasing. Every one of them
**slices back to its quote exactly and passes T-11**. Article III's mechanical
check cannot catch it, because the cited text is genuinely there — it is just
somewhere else.

The spike's zero was a property of its corpus, not of the mechanism: notes
written by hand gave the patient a different BMI every month, so no short
quote repeated. Real charts repeat. A plateaued weight produces four
identical `BMI 37.6` lines, and that is the normal case, not a synthetic one.

**Why it matters more than its rate suggests.** c4 asks whether *this month's*
encounter documented a BMI. A span citing another month's identical value
substantiates the claim with the wrong evidence, and the verifier (Art. V)
receives that span and confirms it, because the passage does say `BMI 37.6`.
It is a citation that is true about the document and false about the claim —
the failure mode this project exists to argue against, arriving through the
one door the span validator cannot watch.

### Chosen — sub-spans anchor near their event, not near the top of the document

`anchor` takes an optional `prefer_near` window. When a quote occurs more than
once, the occurrence **closest to that window** wins; ties keep the first, so
the single-occurrence path is unchanged. The event's own quote is anchored
first (it contains the date, so it is unique in practice), and its offsets
become the window for the encounter's `bmi_quote`, `diet_quote` and
`activity_quote`.

The rule is a statement about what those fields mean: a per-field span
documents *this* encounter, so searching the whole document for it was the
bug. Distance to the event span is the criterion rather than membership in a
blank-line block, because blocks are this corpus's formatting and a real chart
export may have none.

**Rejected — a longer quote from the model.** D17's own reversal clause
("anchoring needs a disambiguating window and the schema gains a context
field"). It spends a prompt change and a re-measurement to buy what Python
already has: the event's location. It also degrades under exactly the
condition that produces the problem, since a longer quote around a repeated
line is itself more likely to repeat.

**Rejected — reject multi-occurrence spans outright.** Fail-closed and
appealing, and it would drop 12 spans of which 5 were correctly anchored,
demoting honest REQ-38 flags for a formatting property of the note.

**Rejected — accept it and record the rate.** What the spike did, correctly,
for a number it measured at zero. At 4.3% with a known mechanism and a
deterministic fix available, recording it instead of fixing it would be
choosing a caveat over a correction.

### Chosen — T-15's exit condition gains a check, because "every span passes T-11" does not cover this

The exit reads *every span passes T-11*. All seven defective spans pass T-11.
An exit condition that a known defect satisfies is not an exit condition
(working rule 4, D10's precedent), so it gains a clause:

> every event-scoped span is nearer to its own event's span than to any other
> event's span in the same note

Format-independent, and it is precisely the property that was violated.
Rewriting an exit condition is a design decision and is logged before the code
(working rule 5, D28's precedent). This stays inside T-15 rather than becoming
a new task: D18 and D19 already assigned the span-location mechanism to T-15,
and a locator that cites the wrong encounter is that mechanism unbuilt.

**Reverses if:** the nearest-occurrence rule picks wrongly on a chart whose
encounters interleave rather than run in blocks — a note where March's
follow-up text sits physically closer to June's entry than to March's. Then
anchoring needs the model to return one contiguous quote per encounter and
locate the fields inside it, which is a schema change and a re-measurement.

---

## D47 — T-15 result: the facts are stable, the encoding is not, and a double-escaped newline is a transport artifact

Two findings from running T-15, both measured rather than reasoned about.
Logged here because D19's stability claim was made on the narrow schema and
this is the first evidence about the wider one.

### Measured — 2026-09-08, `gemini-3.5-flash-lite`, temperature 0, two complete runs over 11 notes

Five spike notes and six synthesized ones, 22 model calls total.

| | run A | run B |
|---|---|---|
| event precision | **1.000** | **1.000** |
| event recall | **1.000** | **1.000** |
| matched events | 42/42 | 42/42 |
| REQ-9 exclusion | **11/11** | **11/11** |
| REQ-38 field agreement | 1.000 (126) | 1.000 (126) |
| spans anchored | 170/170 | 168/168 |
| **model-emitted offsets usable** | **0** | **0** |

**Stable across both runs:** every event date on all 11 notes, and every
`bmi`/`diet_documented`/`activity_documented` value on every matched event.
That is the property T-15 needed — these notes are usable as regression cases
only if a note yields the same events twice — and it now holds on the wider
schema, not only on D19's narrow one.

**Not stable, and worth naming:**

- *Quote encoding.* Run A double-escaped newlines inside its JSON strings
  (15 spans arrived carrying a literal backslash and an `n` where the note has
  a line break); run B emitted none and needed D18 normalization for 16 spans
  instead. Same information, different encoding, identical temperature.
- *Assertion emission on notes that also document encounters.* `n05` and `E5`
  each produced one `program_assertion` in run A and none in run B. **E8 and
  n03 — the refusal cases — produced exactly one in both**, which is the count
  that matters: D12 detects `UNSUBSTANTIATED_ASSERTION` from *zero events plus
  at least one assertion*, so an extra assertion on a note that has events
  changes no verdict. Harmless today, and recorded because it stops being
  harmless if a later consumer reads assertions without D12's zero-event
  guard.

**D19's exclusion caveat carries over unchanged.** The 11 REQ-9-shaped traps
here are a small n, and the corpus is still notes written for this project
scored against labels written for it — T-07's are the agent's own, which D42
already put on the record.

### Chosen — a literal `\n` inside a quote is unescaped before anchoring

The model returned a JSON string holding backslash-`n` where the note holds a
newline, so nothing matched and six real encounters on `n01` were dropped as
unanchorable. That is a property of the encoding, not of the claim — the same
class of fact as D18's line wrap — and `pa_agent.anchor.unescape_literal_whitespace`
repairs it with no threshold to tune: the two characters either are there or
are not. It applies to the **quote only**; the document is never rewritten, so
the offsets produced still point into the unmodified note, and the recorded
`anchor_mode` says `unescaped` so the rate stays visible instead of being
absorbed.

**Rejected — treat it as the model's problem and drop the events.** The
literal reading, and it is what happened: run A scored recall 0.857 because
one note's six encounters were unanchorable for a reason that has nothing to
do with whether the model read the note correctly. D18 rejected the identical
argument for line wrapping — failing an honest citation for an encoding
property makes a correct model look fabricating.

**Rejected — repair it in the prompt.** Asking the model not to double-escape
spends a prompt change and a re-measurement on something Python fixes exactly,
and it would fail silently the next time the model chose the other encoding.

### `--rescore` paid for itself immediately

D18 built the re-anchoring path so that changing the anchor rule would not
cost a re-measurement, and D46 changed the rule the same afternoon. Both
repairs — proximity disambiguation and unescaping — were verified by replaying
the recorded payloads for **zero model calls**, against notes re-hashed first.
The one re-run this work did spend was the run that happened before payloads
were being recorded at all, which is the argument for recording them.

### Both repairs are tested directly, because both triggers are intermittent

Removing the unescape repair broke nothing in the gate, because the recording
it runs against happens to be the run that did not double-escape. A gate whose
coverage depends on which way a model felt that afternoon is not coverage, so
`anchor` gets synthetic tests for both mechanisms — the D27 scorer-self-check
pattern, applied to a branch that appears and disappears rather than one that
has not arrived yet.

**Reverses if:** the escaping variance disappears across a longer series (then
the unescape path is dead code kept for a fault nobody sees, and its rate is
already reported so that is checkable), or assertion emission becomes
load-bearing for a consumer without D12's zero-event guard — in which case the
instability above is a defect rather than a note, and it needs a prompt
change and its own measurement.

---

## D48 — The qualifying run is c3's and everyone scopes to it, a zero-event abstention reads the assertions, and REQ-14's run selection has a defect that becomes T-42

T-16's design: c1 through c5 as deterministic predicates over `wm_events`.
No model call after extraction (REQ-13), and the same list yields the same
verdicts on every run (Art. II).

### Chosen — one `qualifying_run` helper, computed by c3 and passed to c2, c4 and c5

The tree already says so: c2, c4 and c5 declare `scoped_to: "c3"`. So c3
identifies the run once — a contiguous block of calendar months, each holding
at least one event — and the other three take it as an argument rather than
recomputing it. Three independent implementations of "the qualifying run"
would be three chances to disagree about which months are in it, and REQ-15's
whole point is that c4 and c5 answer about *that* period.

### Chosen — a zero-event abstention reads `program_assertions` for its reason

With no events, c1 and c3 both abstain, and D12 settled which reason: zero
events **plus at least one assertion** is `UNSUBSTANTIATED_ASSERTION` (E8 —
find the visit notes behind the claim); zero events and no assertion is
`NO_EVIDENCE_RETRIEVED` (E7 — find documentation of a program). One rule,
applied identically at both criteria, because the next action is a property of
the chart and not of which criterion asked.

This is why the predicates take the assertions alongside the events. They are
never counted (REQ-35) and never produce a verdict — their only consumer is
the gap reason, exactly as D12 scoped them.

### Chosen — what each `NOT_MET` cites

REQ-5 wants a span on every substantiated verdict, and for a failure the
honest citation is the evidence that falls short, not the absence:

- **c3 `NOT_MET`** (a run of one to three months): the events of the longest
  run — "this is the program, and it is this long."
- **c2 `NOT_MET`** (run outside the window): the run's last event — "this is
  when it ended."
- **c4 `NOT_MET`**: the encounters in the run's months that documented no BMI.
- **c5 `NOT_MET`**: the encounters in the months missing diet or activity.

A month with *no* encounter cannot be cited, but such a month cannot occur
inside a run — a run is made of populated months — so every c4 and c5 failure
has an encounter to point at.

### The defect: REQ-14 picks the longest run, and the longest run is not always the qualifying one

REQ-14 says c3 "returns the longest run of consecutive populated months", and
REQ-32 then asks whether *that* run ended inside c2's window. Consider a chart
with a six-month run three years ago and a four-month run last month. The
longest is the old one, so c2 reports `NOT_MET` — for a patient who completed
four consecutive supervised months within the window and plainly qualifies.

That is a **false `NOT_MET`**: the cheaper direction (an unnecessary chart
review rather than a wrong denial), but wrong, and produced by the mechanics
rather than by the evidence.

**Chosen: implement REQ-14 as written, and put the defect on the board as
T-42.** Silently selecting "the run that best satisfies c2 and c3 together"
would be this task rewriting the requirement it implements — the move working
rule 5 exists to prevent and that D24, D26 and D28 each spent an entry
refusing. No case in the eval set distinguishes the two readings (E5 has one
run; E11's longest run is also its most recent), so nothing is being papered
over to make a labeled case pass.

Ties are broken toward the **most recent** run, which is inside REQ-14's
wording (it does not say which longest run) and is the reading that can only
help a patient.

**Reverses if:** T-42 rewrites REQ-14 and REQ-32 to select jointly. The
predicate then takes the window into account and this entry is superseded
rather than edited.

---
## D49 — The pins follow the installed set, not the file, and every direct import is declared

T-43's design. The smallest task on the board and the one that decides whether
any number already recorded in this log still means something.

### The defect

`requirements.txt` pins `pydantic==2.12.3` and `pytest==8.4.2`. The virtualenv
holds **pydantic 2.13.5 and pytest 9.1.1**. Nothing in this repository has ever
been measured on the pinned versions: D19's spike numbers, D45 and D47's T-15
extraction figures, D48's predicate results and all 289 tests were produced by
the interpreter and libraries currently installed.

Separately, `scripts/run_extraction.py` and `spike/spike_001/run.py` both
import `google.genai`, and `scripts/verify_sources.py` imports `pypdf`. Only
`pypdf` is declared. **`google-genai` arrives transitively through
`google-adk`** and is named nowhere. `pa_agent/extraction.py` is deliberately
not among the importers — D5 keeps the client injected so no module in the
package reads a credential — but the distribution it runs on is undeclared all
the same.

### Chosen — pin *up* to what is installed

The file is the artifact that drifted; the venv is the artifact that produced
the findings. Editing the file costs nothing and invalidates nothing.
Downgrading the venv to match the file would invalidate every recorded
measurement in this log to satisfy a line that was never measured on.

**Rejected — pin down, reinstall to match the file.** It is the reading that
treats the committed file as authoritative, which is normally correct. Here it
inverts the evidence: the recorded numbers are the durable artifact and the
pin is a claim *about* them. A pin that disagrees with the environment that
produced the measurement is not a stricter pin, it is a false one.

**Rejected — a version range, or `pip-compile` and a lockfile.** A lockfile is
the right answer for a team or for CI, and this project has neither (working
rule 9). A range would let the set drift again silently, which is the defect.
Exact pins in one readable file is the smallest thing that closes it.

### Chosen — `google-genai` becomes a declared direct dependency

An undeclared direct import is a provenance hole of exactly the kind D20 and
T-34 exist to close for the model identifier. `google-genai` is the SDK that
issues **every model call the project has ever measured**. Today a
`google-adk` upgrade is free to move it underneath `pa_agent/extraction.py`,
and no gate in the repo would notice — the recorded model name would still
match `PINNED_MODEL`, the notes would still re-hash, and the transport would be
a different piece of software. D47 already established that a transport detail
can change the payload: a double-escaped newline cost six encounters.

### Chosen — the check is a scan, not a list

`scripts/check_env.py` parses every tracked `.py` with `ast`, collects
third-party top-level imports, maps them to distributions, and fails when one
is missing from `requirements.txt` or when a pin disagrees with the installed
version. A hand-maintained list would need remembering; this needs nothing.

The scan resolves `from google import genai` to `google.genai` rather than to
`google`, because the namespace package is shared by two distributions that
this project pins independently. An import it cannot map fails the check
loudly rather than being skipped — a dependency the checker does not
understand is the case the checker exists for.

**Cost:** the check reads the *installed* environment, so it passes on a
machine whose venv is wrong in the same way the file is wrong. It closes drift
between file and environment, not the correctness of either. Article X's
provenance still rests on `PINNED_MODEL` and the recorded measurements.

**Reverses if:** the project gains CI or a second developer, where a lockfile's
exact transitive closure outweighs one readable file — or if an installed
version is found to carry a defect the older pin did not, in which case the
downgrade is a measurement event and every affected number is re-recorded, not
inherited.

## D50 — The note states a current BMI that belongs to no encounter, and reconciliation needs it

T-60's design. Discovered in T-33: E10b cannot be evaluated at all under the
schema T-15 measured.

### The defect

REQ-34 reconciles "a structured value and a note-extracted value for the same
fact." For BMI the structured side is criterion (a)'s: the most recent
`Observation`. The note side was assumed to be `WmEvent.bmi`, and for E10 and
E10c it is — 45.0 and 37.6 both sit on documented encounters.

**E10b has no encounters.** Its patient is also E8, the unsubstantiated
assertion case, and the note documents a claim with no visit records behind it
by design. The BMI the case turns on is a standalone line —
`Measured in clinic today: weight 105.4 kg, BMI 36.2.` — and
`ExtractedAssertion` carries no BMI, so nothing in the system can reach it.

This is structural rather than an oversight in the fixture. The cross-threshold
case needs a structured BMI below 35.0, and **both sub-35 patients in the
population are deliberately encounter-free**: 49092fd9 is E7 (nothing
documented) and bc6748d3 is E8. A patient with a qualifying run *and* a sub-35
structured BMI is a seventh fixture that does not exist, and manufacturing one
would be inventing data to make a case reachable.

### Chosen — `Extraction` gains a note-level `current_bmi` with its own span

The note's *current* BMI is a different fact from an encounter's BMI, and
conflating them was the actual modelling error. Criterion (a) asks what the
patient's BMI is now; c4 asks whether each month of a supervised run documented
one. A BMI recorded at a supervised visit eight months ago is evidence for c4
and is not the note's answer to criterion (a).

So the note side of BMI reconciliation is **the most recent BMI the note
states**, taken over the union of `current_bmi` and the event BMIs. E10 and
E10c continue to resolve to their encounter values because those are the most
recent BMIs their notes state; E10b resolves to the clinic line because it is
the only one its note has.

**Rejected — put a `bmi` field on `ExtractedAssertion`.** It reaches E10b and
it is wrong: the 36.2 is not part of the claim the assertion makes. The
assertion is "completed a six-month program"; the BMI is a measurement taken
today that happens to sit in the same note. Attaching it to the assertion would
make a discrepancy inherit the assertion's unsubstantiated status.

**Rejected — let reconciliation fall back to any BMI-looking number in the
note, found in Python.** That is a regex over prose deciding what a clinical
value means, which is the model's job under Article II's division and the
project's own D2. It would also silently pick up a target weight or a
historical figure.

**Rejected — re-point E10b at another patient.** No patient can host it, per
the structural argument above.

### Chosen — this is a new measurement, and it is its own task

D45 established that widening the schema means the numbers belong to the wider
schema; D47 showed a transport detail changing the payload. T-60 therefore
re-runs `scripts/run_extraction.py` and records a fresh
`eval/extraction/results.json`, rather than folding a model run into T-33,
whose exit condition says *no model call*.

The instruction gains one paragraph and the schema two fields. Everything D19
and T-15 measured stays in place; what is reported afterward is the new run's
numbers, quoted as such.

**Cost:** every prompt edit is a chance to move the numbers that already hold.
The paragraph is additive and scoped to a fact the current instruction never
mentions, which is the smallest change that reaches the case — but "smallest"
is not "none", and if event precision or recall moves, that is the finding and
it gets recorded rather than retried.

**Reverses if:** the re-measurement shows event extraction degrading. Then the
note-level BMI moves to a second, separate call over the same note rather than
sharing the extraction prompt, at the cost of one more call per note.
## D51 — The tolerance is 1.0 by Troy's decision, `reconciled_facts` becomes typed, and closing the last open question retires two guards on purpose

T-33's design. It closes open question 5, the last provisional constant in the
tree, and that has consequences beyond the constant.

### Chosen — `discrepancy_tolerance` is **1.0 BMI points**, decided rather than sourced

No source text bounds it. Like D40's lookback it is recorded as a judgment with
a name and a date attached, never as a span: **Troy, 2026-09-08.**

A full BMI point is beyond rounding and beyond the variance of two measurements
taken weeks apart — roughly six pounds for an average-height adult — so a
disagreement at or above it is a documentation problem rather than noise. D14's
two poles are both handled: the rounding artifact stays silent and the case the
list exists for is listed.

The eval set does **not** decide this. Measured against the committed data, not
the spec's illustrative numbers: E10 is 39.23 against 45.0, a gap of 5.77;
E10c is 37.65 against 37.6, a gap of 0.05. Any tolerance in that range passes
all three cases, so choosing 1.0 over 0.5 or 2.0 is a materiality judgment the
cases cannot make. Saying so is the point — a constant that three different
values would satisfy is not being validated by the suite that passes.

**Rejected — 0.5.** Catches more, and lists differences that are two
measurements taken weeks apart. D14 already argued that a list reporting noise
beside signal trains Sam to ignore it.

**Rejected — 2.0.** Quietest, and closest to D14's own example. It would let a
1.5-point disagreement reach a payer unremarked, which is the failure the list
exists to prevent.

**Reverses if:** T-22 shows material discrepancies in a large fraction of cases
(D14's own reversal condition), or if a discrepancy that mattered fell below
1.0 — either is a measurement, and the constant moves with a new entry rather
than by edit.

### Chosen — `reconciled_facts` stops being `list[dict[str, Any]]`

It becomes a `ReconciledFact` model whose `discrepancy_tolerance` is a
`PolicyConstant`. That is not tidying: `PolicyConstant`'s validator refuses a
provisional constant carrying a value and refuses a non-provisional one that is
null, and `require()` raises rather than returning `None`. Typing the field is
what makes "tolerance read from the criteria tree" in T-33's exit a guarantee
instead of a `dict.get` that returns `None` and compares as zero.

**Rejected — read the dict directly in `reconcile()`.** One line shorter, and
it puts the one constant this task exists to consume outside every guard the
repo already built for exactly this.

### Chosen — reconciliation is `pa_agent/reconcile.py`, not a function in `criteria.py`

`criteria.py` holds predicates that answer a criterion from evidence.
Reconciliation is a different operation: it takes an already-produced
`CriterionResult` and two sources, and may downgrade it. Folding it in would
mean criterion (a) is evaluated in two places, and REQ-34's "may downgrade a
verdict criterion (a) already produced" is precisely a statement that these are
two steps. It runs after extraction, so it cannot live inside T-13's path at
all (D11).

### Chosen — closing the last open question retires two guards, deliberately

`tests/test_criteria_tree.py` currently asserts that at least one provisional
constant exists and that §9 lists at least one open question. Both were
correct while a provisional constant existed, and both were written with an
instruction attached: *"If every value is now sourced, this test and D23's
third section should be retired deliberately, not by accident."* T-33 is that
moment.

They are replaced rather than deleted. The per-constant rules stay and become
vacuously true; what changes is that the count of provisional constants is now
**pinned at zero**, so a new provisional constant appearing is a visible diff
against a stated expectation rather than a silent return to the old state. §9's
`### Still open` heading stays with an explicit "none" line, because
`_question_statuses` requires both subsections to exist and a missing heading is
supposed to fail.

**Rejected — deleting the two assertions.** The test would still pass over an
empty list and would never say so. A guard that silently stops guarding is the
failure D34 wrote this parser to end.

### Chosen — spec §6's numbers are corrected to the committed data, inside this task

§6 says E10b is "Structured 34.8" and E10c is "Structured 38.1, note 38.0". The
bundles hold **34.6** and **37.65 against 37.6**. No behavior changes — 34.6 and
34.8 are both below 35.0, and 0.05 and 0.1 are both below any tolerance under
consideration — but T-33's exit condition cites "34.8 against 36.2" and a task
cannot close honestly against a number the data does not carry.

Corrected here rather than deferred to a new task because the current task's own
exit text is one of the wrong numbers. Working rule 6 sends *discovered work* to
a new task; this is a defect in the specification of the task in hand.

**How it happened, since it is worth recording:** §6 was written in T-01 before
any patient existed, and T-06 assigned cases to the bundles the population
actually produced (D42). The illustrative numbers were never revisited. Nothing
read them, so nothing failed — which is the argument for T-55's ledger.

## D52 — The value set arrives through the policy port, and the codes it yields are the ones the conditions speak

T-46's design. Discovered work, opened in the T-18 architecture review: T-13
built criterion (b) to take a value set as a parameter and left "which port
serves it at runtime" explicitly to T-18. Nothing in `pa_agent/` loads the file
today — only `tests/test_criteria_ab.py` and `tests/test_valueset.py`, both by
path.

### Chosen — `PolicyStore.get_value_set(value_set_id) -> frozenset[str]`

It belongs to the **policy** port, not the patient one. A value set is a
compiled fragment of the policy: A53028's Group 1 decides which comorbidities
count, and T-05 anchored each entry to the article. It travels with the tree
and is versioned with it (Art. VII).

The id comes from criterion (b)'s `value_set_id` constant, so the tree names
what it needs and the caller never writes a literal. That is the same shape
`policy_version_id` already has, and it is what keeps REQ-41 honest: no module
outside an adapter names a storage location.

**Rejected — returning the full entries.** Callers would then be free to read
the ICD-10 anchors and the mapping provenance, and criterion (b) needs neither.
A `frozenset[str]` is the whole interface a set-membership predicate has, and a
narrower return is a smaller thing to keep stable when the production adapter
lands (D25).

**Rejected — inlining the codes into the criteria tree.** D3 and D23 already
refused this: 543 codes would make every diff of the tree unreviewable, and
Article VII wants a reviewer for every clinical rule change.

### Chosen — the set yields SNOMED codes, because that is what the conditions carry

T-05 built the entries around SNOMED concepts with ICD-10 anchors spanned into
A53028, and admitted in writing that the SNOMED-to-ICD-10 hop is unsourced
(`in_corpus: false`, D36). `LocalPatientStore` reports `Condition.code` as
SNOMED, and `evaluate_criterion_b` tests `code in value_set`. So the port
returns the codes the predicate will actually be handed.

Stating it because the alternative is a silent no-match: a set of ICD-10 codes
would be well-formed, load cleanly, compare cleanly, and find a comorbidity in
nobody. Criterion (b) would answer `INSUFFICIENT_EVIDENCE` for every patient
and every downstream test would agree with it — the failure mode D31 named
about `resolve` returning `None`.

**Rejected — returning both systems in one set.** It would make the predicate
pass under either encoding, which sounds robust and is how a mapping defect
stops being visible.

### Chosen — the adapter verifies the file names the set it was asked for

Cheap, and it catches the case a rename creates: a file whose contents no
longer match its path. An unknown id raises rather than returning an empty
set — an empty comorbidity list is a claim about the policy, and manufacturing
it here would deny every patient a criterion they might meet.

**Reverses if:** a second jurisdiction needs the same fact under a different
code system, at which point the port returns a richer object and the predicate
is told which system to read.


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

## D61 — Model-adjudicated agentic orchestration

**Chosen:** Add a model-directed orchestration layer with tool calling. The model
may select tools and workflow steps, interpret extracted clinical evidence,
evaluate criteria, and determine criterion and overall outcomes.

**Rejected:** Keep the model limited to extraction while Python performs every
adjudicative judgment.

**Why:** The project must demonstrate a genuine agentic architecture rather than
only manual Python plumbing. Tool selection and evidence-directed reasoning are
the capabilities being measured.

**Safety boundary:** Python still owns validation, policy-version selection,
date arithmetic, numeric comparisons, set membership, span verification, retry
budgets, timeouts, step limits, and output validation. The model cannot invent
policy, tools, evidence, or citations.

**Verification:** Every model outcome must be supported by mechanically verified
spans or become `INSUFFICIENT_EVIDENCE`. Invalid or contradictory output becomes
`ERROR` or human review. The deterministic implementation remains the
differential oracle.

**Reverses if:** Agentic evaluation shows unacceptable unsupported-outcome,
citation, non-determinism, or safety-failure rates, or if bounded orchestration
cannot satisfy the acceptance thresholds added to the specification.

## D62 — The ADK lands at the extraction boundary, the graph stays Python, and Amendment 1 is an amendment rather than a rewrite

T-18's, T-19's, T-62's, T-20's and T-26's design, written before any of them. It
narrows D61 rather than reversing it: D61 chose a model-adjudicated agentic path
and is still the charter for T-61. This entry is about the layer underneath it,
which has to exist first and does not.

### The problem D61 left open

D61 said "add a model-directed orchestration layer with tool calling" and the
working tree implemented that by **rewriting Articles I, II, IV, V, VII and X of
the constitution in place.** Article I became "Permit the model to select tools,
sequence steps, retry, and terminate within bounded limits." Article II became a
licence for the model to "evaluate criteria and determine criterion and overall
outcomes." Four `Superseded for the agentic path` notes were stapled under REQ-2,
REQ-11, REQ-12 and REQ-13.

Two things were wrong with that, and neither is about whether the direction is
good.

The constitution's preamble says *"Amendments are appended with a date and a
reason. Articles are never silently edited."* Those articles were edited. The
amendment procedure existed and was not used.

And the substantive problem: **D61 named the deterministic implementation as the
differential oracle in the same breath as deleting the articles that make it
one.** An oracle is only an oracle because it is constrained differently from the
thing it grades. If Article II permits the model to do arithmetic everywhere, there
is no second implementation to compare against — there is one implementation with
a model in more of it, and the comparison D61 promised has nothing on the other
side.

### Chosen — restore the articles, keep Amendment 1, scope it to a path

Articles I, II, IV, V, VII and X go back to their committed text. Amendment 1 stays,
appended, dated, and scoped: it opens a second path rather than loosening the
first. The deterministic modules — `workflow.py`, `aggregate.py`, `criteria.py`,
`reconcile.py`, `resolver.py` — are named in the amendment as governed by Articles I
and II as written.

The four supersession notes come out of the spec. Nothing in this work supersedes
REQ-11, REQ-12, REQ-13 or REQ-2: criterion (a) is still a numeric comparison with no
model call, and E2 and E3 still complete with zero. A requirement marked superseded
by a path that does not exist yet is a requirement nobody has to satisfy, and A7
maps every REQ to a passing check.

**Rejected — leaving the rewritten articles in place.** It is the cheaper move and
it costs the project the only thing that makes an agentic result credible in a
review: a constraint the agentic path is measured against. Also, a constitution
whose articles get edited whenever a task finds them inconvenient is not a
constitution, and the reviewer will notice that faster than they notice the ADK.

**Rejected — reverting D61, REQ-43 through REQ-51 and T-61 entirely.** T-61 is
wanted and is next. Deleting its charter to build its foundation is backwards.

### Chosen — the ADK connects at the extraction boundary and nowhere else

The model leaf is note extraction. It was already the only model leaf (D45), and
the ADK now implements it as a second runner behind a port.

This is less of a change than it looks, and the reason is worth stating: **the ADK
is where D19 was measured.** `spike/spike_001/run.py` builds an
`Agent(..., output_schema=Extraction, output_key="extraction")`, runs it through
`Runner` with an `InMemorySessionService`, and reads the payload off
`event.actions.state_delta["extraction"]`. T-15 then wrote `pa_agent/extraction.py`
against raw `google-genai` instead, so the production path and the measured path
have been different SDKs since D45. Putting ADK back at that seam returns the
system to the configuration its founding measurement came from.

**Rejected — an ADK coordinator above policy resolution.** The short circuits are
where the system's cheapest correct answers live: E2 and E3 must complete with zero
model calls (A4), and a coordinator that decides whether to consult the policy store
has already spent a call deciding. sc1 and sc2 run before the runner is touched,
and the T-18 gate proves it with a runner whose `run()` raises.

### Chosen — the workflow graph is plain Python, not an ADK `Workflow`

`Workflow` in 2.8.0 is real and genuinely local: a Pydantic model whose `edges`
compile to a `Graph` at construction, nodes are `BaseNode`s reading `ctx.state`, and
`SequentialAgent`/`ParallelAgent`/`LoopAgent` are all deprecated in its favour. It
would have made Article I literal in a satisfying way — "the graph is an ADK object
with a static edge list."

It is still the wrong tool for this graph.

The decisive reason is the import boundary. `pa_agent/__init__.py` exists to keep
`google.adk` out of every import of the deterministic core, and three tests assert
it: `tests/test_schemas.py`, `tests/test_reconciliation.py`, and
`tests/test_resolver.py`, the last in a fresh interpreter precisely because
`sys.modules` pollution is order-dependent. An ADK `Workflow` in `pa_agent/workflow.py`
puts the ADK on the import path of every deterministic test in the repo, and the
three assertions that would have caught that are the ones that would have to be
deleted to allow it.

The second reason is that `Workflow`'s value is routable edges, and this graph has
one conditional: whether a short circuit fired. That is a `return`, not an edge.
Trading a straight line of typed calls for an async scheduler, a state dict and
inter-step event plumbing buys nothing Article I needs and adds a layer where a
`ctx.state` key typo is a silent `None`.

So `workflow.py` is a module-level tuple of named steps and a driver over it. The
step names are data, which is what lets the gate assert the visited sequence rather
than trusting a comment.

*(Correction while here: CLAUDE.md says `Workflow.edges` "accepts
`dict[bool|int|str, ...]` for conditional routing". In the installed 2.8.0 the type
is `EdgeItem = Edge | tuple[ChainElement, ...]`, with the routing map inside
`ChainElement`. Fixed there.)*

### Chosen — `ExtractionRunner` is a port, and `build_result()` stays the trust boundary

```
ExtractionRunner: run(document_id, text) -> ExtractionResult
    DirectExtractionRunner     raw google-genai; D45's measured configuration
    AdkExtractionRunner        google-adk 2.8.0 Agent + Runner + declared tools
    RecordedExtractionRunner   replays a recorded payload; zero model calls
```

Three implementations, one interface, and the deterministic chain below cannot tell
them apart. Every one of them returns through `build_result()`, which is where each
quote is anchored and where a quote Python cannot locate is counted in `dropped[]`
instead of accepted. That is REQ-52, and it is the single most important line in
this entry: **ADK output is untrusted model output**, and the trust boundary is a
function that already exists and already has a gate.

`RecordedExtractionRunner` is the reason T-18's and T-19's exits are runnable at all.
The recording already holds every raw payload keyed by `document_id`
(`eval/extraction/results.json`, `notes[].raw`), so the entire chain — extraction,
anchoring, span validation, reconciliation, seven criteria, aggregation — evaluates
end to end for zero model calls and zero non-determinism. It refuses a note whose
sha256 has moved, which is `--rescore`'s existing rule: re-anchoring against a
changed document scores a quote against a document it never came from (D18).

**Rejected — `RecordedExtractionRunner(path)`.** REQ-41 says no module outside a
store adapter names a storage location. It takes payloads; the file read stays in
the CLI, the harness and the tests, all of which already read that file.

**Rejected — making the ADK runner the default and retiring the direct one.**
D45's numbers were measured through raw `google-genai` with a native
`response_schema`. The ADK path is a different call configuration and therefore a
new measurement, not a confirmation — the rule D45 set for itself. The direct runner
stays, unchanged, as the thing the ADK runner will be compared against in T-63.

### Chosen — the extraction agent's allowlist is narrower than the toolset

The declared patient tools are `get_patient_notes`, `get_patient_observations`,
`get_patient_conditions` and `get_patient_document`, each a closure over an injected
`PatientStore`. The extraction agent is given **only the two that return note text.**

Not an oversight. T-33 and T-60 exist because the structured BMI and the note BMI
are two independent readings of one fact, and REQ-34 turns a disagreement across
35.0 into `SOURCE_CONFLICT`. Hand the extraction model `get_patient_observations`
and the two sources stop being independent: the cheapest way for a model to report a
note BMI is to report the one it just looked up, and E10b — the case where the two
disagree by 1.6 across the threshold — quietly starts agreeing. The system would
still pass every test it has, because the tests compare the note value to the
structured value and would now find them equal.

That is REQ-53, and it is why the allowlist is a literal list per agent rather than
"the tools." `policy_tools.py` is a separate module for the same reason at the plane
level (Art. VI): a note extractor that can read the policy's thresholds is a
threshold leaking into the model's judgment.

**Rejected — one `tools.py` holding both planes.** REQ-41's closing line is that a
module able to read both planes would have to hold both handles. Two modules, and a
test asserting no third module imports both, keeps that literally true.

### Chosen — Article I is enforced by construction, not by comment

Four properties of how the agent is built, each assertable:

- `include_contents="none"` — one shot per note, no accumulated context to steer
  with, and no note N-1 bleeding into note N (the spike's D17 finding).
- `disallow_transfer_to_parent=True`, `disallow_transfer_to_peers=True`, no
  `sub_agents` — ADK's `_llm_flow` property then selects `SingleFlow`, so the
  `transfer_to_agent` tool is never injected. The model has no mechanism to hand
  control anywhere. The gate asserts the flow type and the tool's absence.
- `tools=` a literal list, per REQ-53.
- `RunConfig(max_llm_calls=N)` — a hard ceiling that raises
  `LlmCallsLimitExceededError`. It is ADK's only budget knob, and it counts LLM
  calls, not tool calls, so one tool round trip costs two.

Retry is a Python budget with the spike's classifier (429/500/502/503/504,
`UNAVAILABLE`, `RESOURCE_EXHAUSTED`), a constructor argument, recorded per run, and
exhaustion raises. **The model is never asked whether to retry.**

### Chosen — malformed output raises, and never becomes an empty extraction

`ExtractionOutputError` with a closed reason enum, the shape `SpanValidationError`
already uses. Raised when the run yields no payload, unparseable JSON, or a payload
`Extraction.model_validate` rejects.

The alternative is the trap the spike already documents in writing: a note that
extracts nothing leaks no REQ-9 traps, so it scores perfect exclusion, and
contributes no false positives, so it scores perfect precision. **A transport
failure reads as flawless extraction.** Returning a zero-event `ExtractionResult` on
a malformed response is that bug with a different cause.

Mapping the exception onto a verdict is deliberately not the runner's job — D38's
posture for span rejection, for the same reason: the runner knows the response was
malformed, and only the criterion knows what that means for the criterion.

### Chosen — T-26 lands here, contracts only, and one guard is retired on purpose

T-61's exit requires malformed and contradictory output to resolve to `ERROR`, and
`ERROR` does not exist: `CriterionVerdict` carries two of Article IV's three states.
**That dependency is not on T-61's `Depends` line and it should be** — added, per
working rule 6.

So T-26 builds now: `CriterionVerdict.ERROR`, an `ErrorCode` enum whose members each
declare retryable or terminal with an unclassified code defaulting to terminal
(REQ-30), `error_code` and `error_detail` on `CriterionResult`, validators refusing
spans (REQ-5) and a `gap_reason` on an `ERROR`, and a `Determination` validator
raising on any `ERROR` criterion (REQ-26, gating A9).

`ERROR` carries no `gap_reason`, and that is the sharp edge. A gap reason says what
to go collect. An `ERROR` means the system could not evaluate — there is nothing for
Sam to collect, and offering a next action for a fault is precisely the collapse
Article IV forbids between `ERROR` and `INSUFFICIENT_EVIDENCE`. D9's three states
stay three.

**One guard is retired deliberately.** `tests/test_schemas.py` asserted
`not hasattr(CriterionVerdict, "ERROR")` so that adding the state without the enum
and the validator would fail loudly. It has now done exactly its job and is
replaced by the real thing it was standing in for. Recording the retirement follows
D51, where two guards asserting a provisional constant were retired and replaced by
a count pinned at zero, so a regression is a visible diff rather than a silent
return.

**T-26 is contracts only.** Wiring `ExtractionOutputError` or a span rejection
*onto* an `ERROR` verdict, and REQ-24's abort, stay **T-29's**. Building the state
without the mapping is the task as written; building the mapping here would make
T-29 close on work it did not do.

### Chosen — the trace rides on the workflow result, not on `Determination`

`ToolCall` and `RunTrace` are new contracts. `Determination.metrics:
list[CallMetrics]` is untouched, so `Determination.model_calls` keeps meaning what
A4 asserts and `tests/test_schemas.py` needs no change to its shape.

`ToolCall` records a digest of its arguments rather than the arguments. A tool-call
log holding `patient_id` verbatim is patient data sitting in an instrumentation
record that will end up in a report, and Article VI is mostly about not letting that
happen by accident.

The recorder is a `BasePlugin` — `after_model_callback` for `usage_metadata`,
`before_tool_callback`/`after_tool_callback` for the ordered log. Every hook body is
wrapped, because ADK re-raises a plugin exception as a `RuntimeError` that aborts the
run: an observability bug must not be able to kill a determination.

### A finding that changes what T-63 has to measure

`output_schema` and `tools` are documented as usable together in 2.8.0. They are —
but the mechanism differs by tier. `flows/llm_flows/basic.py` sets a native response
schema only when `model.capabilities.output_schema_and_tools`, and
`models/_capabilities.py` makes that **Vertex-only**. On AI Studio with tools
present, ADK instead injects a `SetModelResponseTool` and appends an instruction
telling the model to deliver its answer by calling `set_model_response`.

D5 develops on AI Studio and runs evals on Vertex. So **the tool-calling extraction
path runs a measurably different prompt on the two tiers**, and a number measured on
one is not a number for the other. `MEASURED_TIER` already exists to record which
tier produced a result; this is the first case where the tier changes the prompt and
not just the endpoint.

Consequence: `AdkExtractionRunner` carries a `tool_fetch` boolean.
`tool_fetch=False` is the spike's exact configuration — note text in the message, no
tools — so a fidelity comparison against D19 is apples to apples. `tool_fetch=True`
puts a real tool call on the critical path. T-63 measures both and names the tier.
Neither may claim D45's numbers until it does.

### Chosen — T-20 now, because Article X says from the first model call

`eval/run_eval.py` prints per-run token counts and wall time. `Determination`
already computes the totals; `CaseResult` carries them and the report prints them.
The baseline diffs on `(status, reason_class)` alone, so printing more does not move
it — checked, not assumed.

It lands here rather than on day five because the first model call reaches a
determination in this pass, and Article X says the numbers are recorded from that
call, not retrofitted after there are enough of them to be interesting.

### What this does not fix

**Policy #2 still costs a developer.** c1 through c5 are hand-written Python for one
policy's shape. A port for the model leaf does nothing about that; the predicate DSL
is still the answer and still deliberately not open.

**`eval/cases.json` still holds one case.** T-18 and T-19 close on their own pytest
gates over the recorded extraction. **US-4 and US-5 do not close**, because their
closing conditions are E4–E11 and E1/E8 passing in the harness, and those rows are
T-21's. A determination that works and an eval set that grades it are two different
deliverables and only the first is in this pass.

**T-42 is still open.** REQ-14 picks the longest run and c2 then tests that run's
recency, so a long stale run beats a short recent one. T-18 runs the chain over that
defect unchanged, and
`test_the_longest_run_wins_even_when_an_older_one_is_stale` still pins it.

### Discovered by building it: three order-dependent guards

Adding a test file that legitimately imports `google.adk` broke three assertions
that had nothing to do with the ADK:

```
tests/test_schemas.py           assert "google.adk" not in sys.modules
tests/test_reconciliation.py    assert "google.adk" not in sys.modules
tests/test_error_state.py       (the same, written this pass)
```

They were never testing what they read as. Each imports the module under test and
then checks `sys.modules`, which asserts *"nothing in this process has loaded the
ADK"* — a property of the whole pytest session, not of `contracts.py`. They passed
because no test file imported the ADK. `tests/test_adk_agent.py` does, by design,
and the three failed by collection order.

**`tests/test_resolver.py` had already found this and written the fix down**: it
uses a fresh-interpreter subprocess probe *"because this suite itself imports
`pa_agent.model_pin` elsewhere and a `sys.modules` check here would inherit that
pollution and pass or fail by test ordering."* All three are now that shape, which
is strictly stronger — they now fail when the module under test gains the import,
which is the thing they were supposed to catch and previously could not.

Recorded rather than quietly fixed because it is a check that was weaker than it
looked for four tasks, and the next `sys.modules` assertion somebody writes should
find this paragraph.

### Found by reading an abandoned parallel attempt: cost was under-reported

An uncommitted worktree (`update-google-genai-to-adk-logic`) held an earlier,
unfinished pass at this same change, carrying its own **D53**. It replaced
`extract()` in place rather than putting a port in front of it. Superseded, and
one thing in it was right that this entry's implementation had got wrong.

It summed `usage_metadata` across **every** event in the run. This
implementation records one `CallMetrics` per model turn on the `RunTrace` and
then attached only `trace.metrics[0]` to the `ExtractionResult`, because
`build_result()` takes a single `CallMetrics`. On the no-tools path that is
correct — one turn, one measurement. **Under `tool_fetch` it halves the reported
cost**: a tool-calling extraction spends one LLM call requesting the tool and
another answering, and the determination reported the first.

Measured: 22 input tokens spent, 11 reported. `max_llm_calls` counts LLM calls
rather than tool invocations for exactly this reason, and the boundary lost what
the plugin had correctly recorded.

Fixed at the workflow boundary — every turn on the trace reaches the
determination, with a fallback to the single measurement for a runner that
carries no trace, so the replay path is not zeroed. Pinned by
`test_every_model_turn_reaches_the_determination_not_just_the_first`.

Worth recording as more than a bug. A number that is quietly half is worse than
no number, because nothing looks wrong and nobody goes checking — and A6 asks for
cost *reported from instrumentation*, which is a claim about the number being
real. This is the second time this pass that a check looked stronger than it was
(the `sys.modules` guards were the first).

**Rejected — D53's shape, for the record.** Replacing `extract()` deletes the
configuration D45 measured, so T-63's comparison has nothing on the other side;
its own "reverses if" names a regression in the recorded gate, which re-reads a
recording and cannot observe one. It also drops the injected client, making the
tier ambient when D5 has the caller choose it, and sets neither
`include_contents` nor the transfer flags, so ADK selects `AutoFlow` and injects
`transfer_to_agent` — a live Article I hole.

**Reverses if:** the ADK runner measures materially worse than the direct one in
T-63, in which case the port stays and the ADK runner stops being the default
without anything above it changing — which is the point of putting a port there. Or:
a second model leaf appears that genuinely needs to route (T-17's verifier does not;
it is one call with a fixed input), at which case Article I gets a real amendment
rather than this one's scoped path.

## D63 — The agentic path is model-directed retrieval, and the graph gains a second port

T-61's design, written before it. It narrows Amendment 1 the way D62 narrowed
D61: the amendment *permits* model adjudication, and this task deliberately does
not exercise that permission.

### The tension that decided it

T-61's prose says the model "evaluates criteria and determines criterion-level
and overall outcomes". Amendment 1 — the thing that makes T-61 legal — reserves
to Python, **on both paths**:

> date arithmetic and window calculation; numeric comparisons; counting,
> sorting, and set membership

That reserved list is the entire decision procedure for all seven criteria.
Criterion (a) is a numeric comparison. Criterion (b) is set membership. c1 is a
count, c2 is a date window, c3 is the longest run of consecutive months, c4 and
c5 are per-month counts over that run. **There is no criterion whose verdict the
model could decide without doing something the amendment reserves.**

So T-61 as written and the amendment it depends on disagree, and one of them had
to give.

### Chosen — the model directs retrieval; Python still adjudicates

The model chooses which tools to call, in what order, how many times, and when it
has gathered enough. It terminates its own run. Then the evidence it gathered
goes through the *existing* deterministic criteria, unchanged.

The measurable question becomes: **does model-directed retrieval find what fixed
retrieval finds?** That is a real question with a real failure mode. A planner
that skips a note leaves c3 measuring a shorter run. One that forgets the
observations makes criterion (a) abstain. One that reads the same document four
times costs four times as much for the same answer. Each of those produces a
determination that is entirely well-formed and quietly wrong, which is the class
of failure this project exists to catch.

**Rejected — the model decides all seven verdicts and Python only validates
spans.** It is what T-61's prose asks for and it is the fuller demonstration. It
needs Amendment 2 to the reserved list, and that amendment would take the oracle
with it: the deterministic implementation is only a regression oracle because it
is constrained differently from the thing it grades. D62 fixed exactly that
problem eight commits ago; re-creating it to make a task's prose come true is the
wrong trade.

**Rejected — the model proposes verdicts and Python overrides the reserved
arithmetic.** Legal under the amendment, and it produces the sharper finding —
where does model judgment diverge from arithmetic. Refused because a proposal
that is always overridden on every criterion is not adjudication, it is a second
opinion nobody acts on, and building the machinery to collect one is expensive
theatre. If model judgment is wanted, T-17's blind verifier is the place it earns
its keep: there the model's disagreement *changes an outcome*.

### What this costs, stated plainly

**REQ-44 is not exercised by T-61.** "The model may use tool results and verified
evidence to evaluate criteria and determine criterion-level and overall outcomes"
describes something this task does not build. It is dropped from T-61's REQ list
rather than left there to be counted as covered — A7 maps every REQ to a passing
check, and a requirement listed against a task that does not implement it is how
that mapping starts lying.

REQ-44 stays in the spec, unclaimed, which is the honest state: a permission the
constitution grants and no task has yet taken up.

### Chosen — a `RetrievalPlanner` port, symmetric with `ExtractionRunner`

T-18's graph already has a port at its model half. It gains one at its retrieval
half, the same shape:

```
RetrievalPlanner: gather(...) -> RetrievalResult
    FixedRetrievalPlanner     the three load steps, verbatim
    AgenticRetrievalPlanner   the model chooses; in pa_agent/agent/
```

Everything downstream — extraction, reconciliation, seven criteria, aggregation —
is untouched and cannot tell which planner ran. That is what makes the comparison
a comparison: one variable changes.

`STEPS` loses `load_structured_facts`, `load_value_set` and `load_notes` and gains
`gather`. T-18's gate is self-consistent about the step list, so this is a visible
diff rather than a broken check, and the ordering assertions still hold.

**Rejected — a separate agentic workflow module.** Two graphs that are supposed to
differ in one step would drift in others, and every drift would show up in the
differential as a finding about the model. The whole value of the measurement is
that the paths are identical everywhere else.

### Chosen — the gate is free to run, and the measurement is a recording

T-61's exit as written is `python eval/run_agentic_eval.py`, which compares the two
paths on the same cases — and therefore spends model calls on every invocation.

Every gate in this repo is free and reproducible: `pytest` re-reads T-15's
recording, `eval/run_eval.py` replays it, `spike/spike_001/run.py --verify` spends
nothing. **A gate that costs money is a gate that gets skipped**, and a task whose
exit condition nobody runs is a task with no exit condition (Art. VIII).

So `run_agentic_eval.py` takes the shape `scripts/run_extraction.py` already has:
a measuring mode that spends calls and writes `eval/agentic/results.json`, and a
scoring mode that reads the recording and spends nothing. **The exit condition is
the scoring mode.** Rewriting an exit condition is a design decision, which is why
it is here (working rule 5, and the same move D62 made for T-18's grep).

### Chosen — the comparison needs no labels, so T-21 does not block this

REQ-50 compares the agentic path against the deterministic one, not against ground
truth. Disagreement, unsupported-outcome rate, citation validity, error rate,
tokens, latency, tool-call count and termination reason are all label-free. So the
differential runs over the six committed patients directly and does not wait for
T-21's labelled case set.

Worth stating because the obvious reading of "completes the full evaluation set"
is `eval/cases.json`, which holds one case and is T-21's to fill. A differential
oracle needs the same *input* through both paths, not a label.

### A defect in T-61's own text

It ends **"US-7 closes when: the agentic path completes the full evaluation
set…"**. T-61 sits under US-5.5 Orchestration; US-7 is "Show me where the system
stops being reliable" and closes on T-21, T-22, T-23 and T-28. T-61 closes none of
those. Corrected to name US-5.5.

### Article VI, and the check that was measuring the wrong thing

T-62 asserted that **no module imports both toolsets**. T-61's gatherer does — it
needs the patient plane to read the chart and the policy plane to know what the
rule requires — so the check fired immediately, which is what a good check does.

It was measuring the wrong property. Article VI states its own smaller, true
claim: *"The criterion text does cross. The policy corpus and its index do not."*
So the question is not whether a module holds two handles — `pa_agent/workflow.py`
has held both store handles since T-18 and its gate says so explicitly — but
whether a model holding both can reach the **source documents**.

It cannot. `get_policy_context` returns compiled criteria: ids, labels, constants,
the decision expression. No document, no span, no corpus text.
`PolicyStore.get_document` is not wrapped as a tool at all. That is exactly the
object the article permits to cross.

So the count is pinned at one named module, and a **second check now carries the
article's actual content**: no model-facing policy tool can reach the corpus.
Without it, a later commit could add `get_policy_document` to the toolset, the
count would still read one, and the corpus would be sitting in a context window
next to patient data with every test green.

Third time this pass a check has looked stronger than it was — after the
order-dependent `sys.modules` guards and the halved token count. The pattern is
worth naming: **each was a check on a proxy rather than on the property**, and
each survived because the proxy happened to track the property until something
changed.

**Reverses if:** model-directed retrieval turns out to agree with fixed retrieval
on every case and every budget — at which point the interesting question moves to
where the model's *judgment* diverges, and the refused alternative above becomes
worth its cost. Or: T-17's verifier lands and gives model judgment a place where
it changes an outcome, making a separate adjudication path redundant.

## D64 — T-61 result: model-directed retrieval is exactly as correct and 73× the cost

The measurement D63 designed, run on 2026-09-09 against `gemini-3.5-flash-lite`
on AI Studio. `eval/agentic/results.json`, six committed patients, extraction
held constant on both sides so the only variable is which evidence reached the
criteria.

### The numbers

| | agentic | oracle |
|---|---|---|
| outcomes agreeing | **6 / 6** | — |
| criteria agreeing | **42 / 42** | — |
| spans valid | **80 / 80** | — |
| errors | **0** | — |
| model calls | 28 | 6 |
| input tokens | **463,124** | 6,311 |
| output tokens | 6,186 | 4,530 |
| wall time | 48.2s | — |

**Input-token ratio 73.4×. Model-call ratio 4.7×.** Unsupported-outcome rate
0.4048 on both sides, which makes it a property of the corpus rather than of
either planner.

Every bound held: no run hit `max_steps`, `max_llm_calls` or the timeout, and no
planner invented a document id or returned an empty bundle.

### What it means

**The agentic path is correct and pointless on this corpus.** It reached
identical answers with identical citations and spent seventy-three times the
input tokens to do it. That is the finding, and it is more useful than a
disagreement would have been: a divergence would have started an argument about
which path was right, and this ends one.

Reported as a *ratio* rather than as a token count because the absolute number is
a property of six synthetic patients and the ratio is a property of the design.

### Where the cost actually comes from, which is not where it looks

The per-patient spread is 10× to **446×**, and the outlier is not a chatty model:

| case | agentic in | oracle in | ratio | observations |
|---|---|---|---|---|
| E1+E11+E10c | 12,361 | 1,244 | 10× | 116 |
| E4+E9 | 16,870 | 1,200 | 14× | 102 |
| E8+E10b | 19,937 | 921 | 22× | 103 |
| E6+E10 | 20,666 | 1,068 | 19× | 128 |
| E5 | 32,180 | 1,068 | 30× | 244 |
| **E2+E7** | **361,110** | 810 | **446×** | **3,780** |

E2+E7's patient carries 3,780 observations — fifteen to thirty-seven times anyone
else's. `get_patient_observations` returns all of them, that payload enters the
context window, and `include_contents="default"` re-sends it on every subsequent
turn. **Cost is tool-payload size × turns, and it scales with the patient's chart
rather than with the question being asked.**

The deterministic path reads the same 3,780 observations through the same port.
They never enter a context window; `most_recent_bmi` picks one and the rest cost
nothing. So the gap is not that the model is verbose — **it is that the model pays
to look at data Python filters for free.**

That generalizes past this project. Any agentic system whose tools return
unbounded collections has this cost curve, and it is invisible on a small fixture
and ruinous on a real chart. A production patient with twenty years of labs is
E2+E7, not E1.

### What this does not license

**It does not say agentic retrieval is worthless.** It says it bought nothing
*here*, on a corpus where every patient has exactly one note and the fixed planner
already reads everything. The fixed planner is optimal when "everything" is small
and knowable, which is precisely the condition a six-patient fixture guarantees
and a real deployment does not. The honest scope of this result is one corpus, one
model, one tier.

**It does not close the question REQ-44 asks.** Nothing here measures model
*judgment* — D63 deliberately built retrieval rather than adjudication, and the
model never evaluated a criterion. A system where the model interprets evidence
could diverge from the oracle in ways this measurement cannot see.

### Discovered work

**T-65** — bound what a tool may return. `get_patient_observations` returning
3,780 rows is the whole of the 446× outlier, and the fix is a tool contract
question rather than a model question: a filtered or paged view, or keeping
structured facts out of the model's reach entirely the way REQ-53 already keeps
them from the extractor. Registered rather than fixed, because T-61's deliverable
is the measurement and changing the tool would invalidate the number just taken.

**Reverses if:** a corpus arrives where the fixed planner cannot read everything —
many notes per patient, or a chart large enough that "read it all" stops being an
option. That is the condition under which model-directed retrieval has something
to buy, and the ratio above becomes a price rather than a waste.

---

## D65 — One document namespace per plane, resolved by record and not by id shape

T-64's design, written before it. The wart is real and three files carry it:
`tests/test_determination.py`, `eval/run_agentic_eval.py` and
`pa_agent/agent/patient_tools.py` each hold their own answer to "which read
serves this `document_id`", and no two of them are the same answer.

### The problem, stated as a caller sees it

A validated `EvidenceSpan` carries `(document_id, char_start, char_end)` and
nothing else. That is Article III's whole point — a span is checkable by anyone
holding the corpus, with no context from whoever produced it. On the policy plane
that holds: `PolicyStore.get_document(document_id)` resolves anything a policy
span can name.

On the patient plane it does not. `get_document` resolves bundle filenames;
notes arrive through `get_notes(patient_id)`. So a caller holding a span must
already know which of the two reads produced it, and the only cue available is
the shape of the id — bundle filenames end in `.json`, note ids contain a slash.
**Branching on the shape of an identifier is a convention, and REQ-41's ports
exist to remove conventions.** It is also silently wrong the first time a note is
named anything else.

**T-17 is why this is now rather than later.** The blind verifier receives a span
and *deliberately* nothing else — no reasoning trace, no criterion, and therefore
no patient id. It cannot call `get_notes`. A verifier that had to be handed a
patient id would not be blind.

### Chosen — `get_document` resolves the whole plane, by lookup in the manifests

`PatientStore.get_document(document_id)` returns any patient-plane document: a
bundle or a note. The adapter resolves it by looking the id up in the records it
already loads — `manifest.json` for bundles, `notes/manifest.json` for notes —
and never by inspecting the id.

That distinction is the decision. A resolver that parsed the id would be the same
convention moved one layer down, where it would be harder to see. A resolver that
consults the record fails loudly on an id nobody recorded, which is the behaviour
REQ-7 wants anyway.

**Uniqueness is enforced, not assumed.** "One namespace" is a claim about the id
space, so the adapter builds the map once and raises if two records claim the same
id — across the two manifests or within either. Today the two id shapes cannot
collide; the guard exists so that the day a note is named after a bundle, the
system says so instead of serving whichever record loaded second.

### Rejected — a `kind` or `provenance` field on `Document`

T-64 asks how a single namespace keeps two provenances distinguishable, and the
obvious answer is to tag them. Refused: no consumer needs the tag. A span is
validated by slicing text, and the text is the same type either way. The
distinguishing facts that *do* matter — different hashes, different records,
different immutability guarantees — already live in the manifests, and the
uniqueness guard is what keeps one id from meaning two of them. Adding a field to
a frozen contract that both planes share, for a discriminator nobody reads, is
vocabulary a later task would have to justify keeping.

Reverses if a caller appears that must treat the two differently — a retention
policy, or a production note store whose documents are mutable while bundles are
not. Then the tag is a real requirement and not decoration.

### Rejected — widening the `get_patient_document` **tool** to match the port

This is the tempting half, and it is wrong. `_patient_of()` in
`patient_tools.py` names T-64 as the task that deletes it, and T-64 does not
delete it.

The tool is not doing resolution, it is doing **scoping**: it answers only for
notes, and only for the patient whose id the caller derived. Point it at the
widened port and the extraction agent — whose allowlist is
`get_patient_document` and `get_patient_notes` — can read a FHIR bundle by
filename. That bundle contains the structured BMI. T-62's sharpest decision was
withholding `get_patient_observations` from the extractor precisely so the note
reading and the structured reading stay two independent readings (T-33, T-60,
E10b). A widened tool hands the model the same data through a different door, and
every existing test keeps passing, because the tests compare the two values and
would now find them equal.

So the port widens and the tool does not. `_patient_of` stays, its docstring
corrected to name what actually removes it.

**Registered as T-66**, not fixed here: the honest fix is
`get_patient_document(patient_id, document_id)` — explicit scope, no id parsing,
and no route to a bundle. That changes a tool's function declaration, which
changes the prompt, which invalidates D64's measurement. D64 refused to change a
tool for exactly that reason and registered T-65; this follows the same rule, and
T-65 and T-66 should be batched so one re-measurement covers both.

### The check

`pytest tests/test_fhir.py`. What it has to prove:

- every note `get_notes` serves is returned identically by `get_document`, and
  every bundle filename still is;
- resolution is by record: an id that matches no record raises, and the raise
  names the plane rather than one manifest;
- a colliding id — a notes manifest naming a bundle filename — raises at
  resolution rather than silently preferring one;
- a tampered note fails `get_document` the way a tampered bundle already does
  (REQ-7 reaches both halves of the namespace, not just the half it started
  with);
- the adapter's import set is unchanged, so the plane is still clean (REQ-33).

The two call sites that hand-rolled the union — `tests/test_determination.py` and
`eval/run_agentic_eval.py` — collapse to one `get_document` per wanted id. That
they get shorter is the point: the wart was theirs to carry.

**Reverses if:** a production patient store cannot expose one id space over notes
and structured records — two systems, two id schemes, no authority that owns
both. D25 already flags stable document identity as the port's reversal
condition; this is that condition read one level finer, and the answer would be
a plane-qualified id, decided once in the adapter, never at the call sites.

---

## D66 — A tool's scope and size are the caller's to fix, never parsed out of an id and never the chart's

T-65 and T-66's design, written before the code. They are batched because each one
changes a function declaration, a declaration change is a prompt change, and a
prompt change is a new measurement (D45's rule, applied by D64 to registering T-65
rather than fixing it inside T-61). One re-measurement covers both.

Both tasks are the same sentence read twice. **A declared tool answers within a
scope and within a size, and both are supplied by the code that built it or by an
argument the model legitimately holds — never derived from the shape of an
identifier, and never left to whatever the patient's chart happens to contain.**

### T-65 — the model pays to look at data Python filters for free

D64 measured E2+E7's 446× to one cause: `get_patient_observations` returns every
row, the patient has 3,780 of them, and `include_contents="default"` re-sends the
payload on every subsequent turn. Cost is payload × turns and it scales with the
chart rather than with the question.

**Chosen — one declared ceiling, `MAX_ROWS`, and a truncated read says so.**
Every collection-returning tool returns at most `MAX_ROWS` rows plus `total`,
`returned` and `truncated`. Observations and conditions come back most-recent-first,
because if rows must be dropped the only defensible ones to drop are the oldest —
criterion (a)'s lookback is twelve months and c2 asks about recency.

**Rejected — a paging parameter.** `get_patient_observations(patient_id, offset)`
bounds the payload and not the run: the model pages until it has the chart, and the
cost returns as turns × payload, which is the curve D64 named. A soft bound on the
thing that caused a 446× is not a bound.

**Rejected — a summary instead of rows** (counts by code, a date range). It is the
cheapest possible answer and it is cheap because it forecloses REQ-44, which is
still unclaimed and still in the spec: a model that adjudicates needs values, not
a histogram. A cap keeps the door REQ-44 walks through; a summary nails it shut for
a token count.

**Rejected — removing the structured tools from the model's reach**, the way REQ-53
already removes them from the extractor's. That is the cheapest answer of all and it
answers a different question. REQ-53's exclusion is a *correctness* argument — two
independent readings of the BMI must stay two — and it does not reach the retrieval
agent, which reports neither reading. Borrowing a correctness rule to solve a cost
problem would leave the real rule harder to state later.

### Why truncating a tool's answer cannot change a verdict, and where it would

This is the part that has to be right, because "the model saw less" is normally a
correctness change wearing a cost change's clothes.

It is safe **here** because the tool payload is not the evidence path.
`AgenticRetrievalPlanner.gather` re-reads observations, conditions and the value set
from the port and puts *those* in the `RetrievalResult`. The model's copy informs
its plan and reaches no criterion. That is not a happy accident of today's code —
it is D63's design, and `test_the_bundle_is_the_ports_full_read_not_the_models_view`
pins it, so a future change that started adjudicating on the tool payload fails
here rather than silently shipping a chart truncated at fifty rows.

It is **not** safe for the note list, and the note list is treated differently for
that reason. `get_patient_notes` is the model's action space: every `document_id` it
can name comes from there, and those ids *are* the evidence path. A truncated list
is a shorter chart with a flag nobody can act on — there is no page two. So
exceeding the cap there **raises** rather than truncating. REQ-46 already says a
bound exceeded produces `ERROR` or human review, and a patient with more notes than
the system will read is exactly that: today no patient has more than one, so the
raise is a ceiling made visible rather than a behaviour change.

So the rule is: **truncate where the payload is informational, fault where the
payload is the model's action space.** One document is not a collection and
`get_patient_document` is exempt — the extraction instruction says read the note in
full, and a note truncated at a character count is a clinical claim the system did
not mean to make.

On the policy plane the value set is capped and truncated rather than faulted.
Amendment 1 reserves set membership to Python on both paths, so no verdict can turn
on which codes the model saw; criterion (b) reads the port's full set.

**D39 is not being reversed.** Its rule is that the *adapter* reports everything and
filtering is the predicates' judgment, and that stands — `LocalPatientStore` still
returns all 3,780 rows and `tests/test_fhir.py` still says so. The cap is on the
model's view, one layer above the port, and it is not a second filter free to
disagree with criterion (a) because nothing downstream of it decides anything.

**Rejected — a per-tool cap.** Four numbers to justify instead of one, and the
justification for each would be the same sentence. One constant, one place to argue
with it.

### T-66 — scope by argument where the model holds it, by construction where it does not

`_patient_of(document_id)` recovers the owning patient by splitting on `/`, which
works only because T-07 named every note `<patient_id>/chart_note.txt`. D65 refused
to fix that by pointing the tool at T-64's widened port, and was right: the widened
port resolves bundles, and the extraction agent's allowlist is exactly this tool
plus `get_patient_notes`, so a widened tool hands it the structured BMI through a
different door and every existing test keeps passing because the tests compare the
two readings and would now find them equal.

**Chosen for the retrieval agent — `get_patient_document(patient_id, document_id)`.**
The model called `get_patient_notes(patient_id)` to get the id, so it holds the
patient id already and passing it costs nothing. The tool scopes to that patient's
notes and a bundle filename is simply not in them.

**T-66's stated rationale does not cover the extraction agent, and that is a defect
in the task's own text.** Under `tool_fetch` the extraction runner hands the model a
`document_id` and nothing else — `ExtractionRunner.run(document_id, text)` has no
patient id to give, and the model never called `get_patient_notes`. So the argument
"the model already holds it" is false there, and deleting `_patient_of` breaks the
variant T-63 exists to measure.

**Chosen for the extraction agent — the scope is a captured constant, not an
argument.** `build_note_reader(patient_store, document_id)` declares one tool,
`read_note(document_id)`, closed over the single id the runner is about to extract
from; any other id is refused by an equality check in Python against a cell the
model cannot see or name. `EXTRACTION_ALLOWLIST` becomes `("read_note",)` —
`get_patient_notes` was there only so the model could resolve an id it was given,
which a tool that takes that id does not need.

This is strictly narrower than T-66 asked for. The task wanted no route to a bundle;
this leaves no route to any document but the one in scope, including another
patient's note. It also lets the reader use T-64's widened `PatientStore.get_document`
safely, because the scope check runs before the resolution — which is D65's refusal
honoured rather than contradicted: D65 refused an *unscoped* tool over the widened
port.

**Rejected — widening `ExtractionRunner.run` to carry a patient id.** It changes
REQ-52's port and all three runners so that one measurement variant can pass an
argument, and it is wrong on its own terms: spike 001's notes belong to no patient
and there is no id to pass.

**Rejected — deriving the patient id inside the extraction runner.** That is
`_patient_of` with a different caller.

### The checks

`pytest tests/test_agentic_workflow.py tests/test_adk_agent.py`, both spending zero
model calls. What they have to prove:

- no declared tool returns an unbounded collection: a store with `MAX_ROWS + 1`
  observations yields `MAX_ROWS` rows, `total` naming the true count, and
  `truncated` true; the same for conditions and for the policy value set;
- the note list faults instead of truncating, and the fault names the cap;
- the evidence bundle a run produces is the port's full read and not the model's
  view — the mutation that assembles it from the tool payload fails;
- `get_patient_document(patient_id, document_id)` refuses a real bundle filename
  from `data/patients/manifest.json`, and `_patient_of` does not exist;
- `read_note` refuses every id but the one it was built for, and the extraction
  agent's allowlist is that tool alone;
- no tool module parses an identifier — asserted on the AST the way
  `test_the_resolver_reads_no_structure_out_of_an_id` does for the adapter (D65).

And the number: `python eval/run_agentic_eval.py --measure` re-run, with E2+E7's
input-token ratio quoted against D64's 446×. It spends calls and stays out of every
gate.

**Reverses if:** a consumer appears that adjudicates on a tool payload rather than
on the port's read. Then the cap is a correctness risk rather than a cost control,
and the answer is a filtered view the criterion agrees with by construction — not a
larger `MAX_ROWS`, which would only move the cliff.

### Result — the outlier is gone and the correctness did not move

Re-measured 2026-09-09, `gemini-3.5-flash-lite` on AI Studio, same six patients,
extraction held constant on both sides. `eval/agentic/results.json`,
`prompt_version` `t61-retrieval-v2`.

| | D64 | after T-65 + T-66 |
|---|---|---|
| outcomes agreeing | 6 / 6 | **6 / 6** |
| criteria agreeing | 42 / 42 | **42 / 42** |
| spans valid | 80 / 80 | **80 / 80** |
| errors | 0 | **0** |
| unsupported rate | 0.4048 | 0.4048 |
| input tokens | 463,124 | **87,964** |
| **input-token ratio** | **73.4x** | **13.9x** |
| model calls | 28 | 30 |
| wall time | 48.2s | 30.8s |

**5.3x fewer input tokens for identical answers with identical citations.** The
agentic path is still as correct as the oracle and still costs an order of
magnitude more than it, which is D64's finding intact — what changed is that the
price no longer scales with the patient's chart.

| case | oracle in | D64 | after |
|---|---|---|---|
| E5 | 1,068 | 30x | **9.5x** |
| E4+E9 | 1,200 | 14x | **10.8x** |
| E1+E11+E10c | 1,244 | 10x | **13.4x** |
| E6+E10 | 1,068 | 19x | **15.5x** |
| E8+E10b | 921 | 22x | **16.5x** |
| **E2+E7** | 810 | **446x** | **20.3x** |

**E2+E7 went from 446x to 20.3x**, and the spread across six patients collapsed
from 10x–446x to 9.5x–20.3x. That is the whole claim: the 3,780-observation
patient now costs about what a 100-observation patient costs, because the term
that scaled with the chart is gone.

**What is left is turn variance, not chart size, and it moves in both
directions.** E5 fell 30x→9.5x on six turns becoming four; E1+E11+E10c *rose*
10x→13.4x on four turns becoming six. Same instruction, same temperature, a
different number of tool round trips — so a per-patient ratio should be read as
one sample, and the aggregate and the spread are the numbers that mean something.
Nothing here says the cap made E1 more expensive.

**Total model calls went 28→30 and that is not a regression the cap caused.**
Turn counts moved per patient in both directions and the sum drifted by two; no
run hit `max_steps`, `max_llm_calls` or the timeout, and no planner invented a
document id or returned an empty bundle.

The prompt changed twice over — `get_patient_document` gained a parameter, and the
instruction gained a paragraph saying a truncated structured read is expected and
not a failure to retrieve. That second sentence is why no planner reported
`gathered=false` on a capped chart. `PROMPT_VERSION` moved to `t61-retrieval-v2`
so the recording says which prompt produced it (D20's argument, applied to the
prompt).

### Discovered work

**T-67** — `--tool-fetch` cannot reach spike 001's notes. Their `document_id`s
(`n01_clean_run` and the rest) are in no patient manifest, so the scoped reader's
`PatientStore.get_document` raises and five of T-63's eleven notes fail before the
model sees anything. **Pre-existing and untouched by T-66**: `_patient_of` derived
the patient `n01_clean_run` from those same ids and `get_notes` raised on it just
as loudly. It surfaces now because T-63 is the next thing to run and will hit it.
Registered rather than fixed, because "where do the spike notes live on the patient
plane" is D42's question about what the corpus is, not a tool signature.

---

## D67 — The spike notes have no patient, so `--tool-fetch` measures the addressable corpus and the script never pools two of them

T-67's design, written before the code.

### The finding, reproduced before it was designed against

Under `--tool-fetch` the model is handed a `document_id` and nothing else, and
`read_note` resolves it through `PatientStore.get_document`. Five of T-63's eleven
notes are spike 001's, whose ids (`n01_clean_run` and the rest) are in no patient
manifest, so the port raises before the model is asked anything:

    spike_001    n01_clean_run                   RAISES KeyError
    ... (five)
    synthesized  E5                              RESOLVES
    ... (six)

The split is exactly corpus-shaped, 5 and 6, and it is **pre-existing** — D66
recorded that `_patient_of("n01_clean_run")` derived the patient `n01_clean_run`
and `get_notes` raised on it just as loudly.

### The question is D42's, not a tool signature's

T-67 registered it as "do the spike notes belong to the patient plane at all."

**Chosen — they do not, and the script stops pretending one runner reads both
corpora.**

The positive reason first, because it is the whole argument: the spike notes
genuinely have no patient. There is no bundle behind them, no structured
observation, no `as_of`, nothing for REQ-34 to reconcile against. They were
written to measure extraction, and extraction is the one thing in this system
that needs no patient — `ExtractionRunner.run(document_id, text)` has no patient
id in its signature, which is precisely why T-66 could not reuse
`get_patient_document` and had to build `build_note_reader`. The address is not
missing; it was never a coherent thing for them to have.

**Rejected — a notes-manifest entry that admits they have no patient.** T-67
named this as one of the two honest answers and it is the weaker one, for three
reasons.

1. `LocalPatientStore` would serve documents that no patient owns. `_namespace()`
   would resolve `n01_clean_run` while `get_notes(p)` returned it for no `p`, so
   the patient plane would carry documents reachable only by an id the caller
   already knows. That is the shape D65 spent a task removing — an id that
   carries a guarantee the port does not make — reintroduced one layer up.
2. It puts a measurement fixture inside the data plane. D42 put T-06's manifests
   in `eval/` because the system under test must never read them. The spike notes
   are graded by `spike/spike_001/labels.json`, and giving them a patient-plane
   address makes the corpus that grades the extractor addressable by the system
   being graded. Nothing exploits that today; the line is the point.
3. `data/patients/notes/manifest.json` is T-07's artifact. It records
   `"task": "T-07"`, `"decision": "D43"` and `"seed": 7`, and every entry in it
   was rendered from a T-06 manifest by seeded templating. Hand-adding five
   entries with no manifest behind them makes that file two things and voids the
   provenance claim D43 rests on — that no note contains a date the manifest does
   not declare.

**Rejected — a second document port so `read_note` could read either corpus.**
Working rule 9: infrastructure the project has not earned, built for a
measurement script. It also defeats its own purpose. The two halves would then be
read through two different adapters, so `--tool-fetch` would be measuring the
runner *and* the adapter, and the corpus split would still be there — moved
inside the thing that was supposed to remove it, where no reader of the numbers
would find it.

**Rejected — dropping spike 001 from T-63 entirely.** It carries the REQ-9 traps
D19's kill criterion was measured against, and 21/21 is the honest number D19
insists on. Losing those notes from the *no-tool* comparison, where they work
perfectly well, would cost the one ADK measurement that is directly comparable to
D19's own configuration, in order to fix a problem that only exists in the other
mode.

### What the script does instead — three changes, the last two with teeth

**1. Addressability is asked of the port, never inferred from the corpus name.**
`addressable(store, case)` calls `store.get_document(...)` and reports whether it
resolved. A `case["corpus"] == "spike_001"` test answers identically on every
input this repo can produce today, and it is the read-structure-out-of-an-
identifier mutation D65 and T-66 spent two tasks deleting, rewritten in a script
where nobody would look for it. Asking the port also means that if the split is
ever closed — a real patient corpus, a second measurement set — the script
follows with no edit.

**2. A note the model was never asked about is `skipped`, not `failed`.** Three
outcomes now: scored, failed, skipped. `failed` means the model was asked and
produced nothing; `skipped` means the corpus has no address for that note under
this mode. Folding a skip into `failed` would report the ADK runner failing five
of eleven notes, which is false — it never ran on them. This is D27's
`BLOCKED`-is-not-`FAIL` line and REQ-28's rule about faults and findings, read
once more at a third site: "answered wrongly," "errored," and "was never asked"
have three different next actions and only one of them is about the runner.

**3. Every mode reports per-corpus aggregates, and `--compare` refuses to compare
two different note sets.** The failure this closes is specific. An eleven-note
direct aggregate printed beside a six-note ADK aggregate is two columns of
numbers that look like a comparison and are not one, and the old `compare()`
would have printed it without comment — `notes` was one row among twelve, and a
reader would have had to notice `11` against `6` and work out for themselves what
it did to the eleven rows below. `compare()` now intersects the two recordings by
`note_id`, recomputes both aggregates over that intersection with the same
`_aggregate`, names what it dropped from each side, and prints the full-corpus
columns only when the two note sets are identical.

`by_corpus` is reported in **both** modes, not only the one that needs it. A
figure that appears when there is a problem and vanishes when there is not is a
figure nobody learns to read, and the six synthesized notes should be comparable
across all four runner × `tool_fetch` combinations without the spike five
diluting one side of it.

### A defect the gate found by existing: the script had never run

Building the record-shape test turned one up. All three record sites in
`run_adk_extraction.py` — the scored path, the failed path, and the skipped path
this task added — wrote `"labels": case["labels"]`, and neither `spike_cases()`
nor `synthesized_cases()` produces a `labels` key. `run_extraction.py` assembles
it at record time from `case["events"]`, `case["traps"]` and
`case["assertion_required"]`.

So `python scripts/run_adk_extraction.py` raised `KeyError: 'labels'` on note one
of every run, in **both** modes, and had done since T-62 wrote it. Nothing caught
it, and nothing could have: the script spends model calls, T-63 says it is in no
gate, and no test had ever built one of its records.

Fixed here rather than registered as a task, and that is a judgment call worth
naming. Working rule 6 exists so scope does not grow silently; this is a
three-line typo inside the two record shapes T-67 is already rewriting, it blocks
T-63 in every mode rather than only under `--tool-fetch`, and registering a task
to repair a `KeyError` in a file this task is editing would be process for its own
sake. Recorded here so it is not silent. The three copies are now one
`_record_base()`, and `test_the_whole_measure_path_runs_without_a_model` drives
`measure()` with a stub runner so the record shapes are built on every `pytest`
run — the closest a gate can get to T-63 without spending T-63's budget.

**The general lesson is about what "in no gate" costs.** A script excluded from
the gate because it spends money is still code, and the parts of it that are not
the measurement — argument handling, record assembly, aggregation, comparison —
are ordinary code that ordinary tests can reach. T-63 not being gated was read as
this file not being gated. Splitting the measurement from the bookkeeping is what
D17 and D45 already did for `run_extraction.py` with `--rescore`; this is the same
split arriving late for the ADK script.

### T-67's exit condition was rewritten, and that is this entry's other half

As registered, T-67 closed on `python scripts/run_adk_extraction.py --tool-fetch`
"reaching all eleven notes." That command spends model calls. T-63 says in its own
text that it is in no gate for exactly that reason, and Article VIII needs a check
that is repeatable at zero cost — a measurement that costs money and varies run to
run is not a gate, it is the thing a gate protects. A defect in the task's own
text, of the same kind D28 found in T-35's, and rewritten here rather than
worked around.

**New exit:** `pytest tests/test_adk_measurement.py` returns zero. It asks the
real `LocalPatientStore` which of the eleven notes the tool path can address and
asserts the split is 6/5 and corpus-aligned; asserts the partition is computed
from the port and not from the corpus label, by moving a spike note's id into the
addressable set and watching the answer change; drives the `tool_fetch` path end
to end on a synthesized note through a `BaseLlm` fake for zero model calls;
asserts a skipped note is counted apart from a failed one and reaches no
aggregate; asserts `compare()` refuses to pool two recordings covering different
notes; and drives `measure()` itself with a stub runner. The measurement — the
model calls and the numbers — stays T-63's, unchanged, and still in no gate.

Thirteen mutations, each caught by the check that should catch it. The one worth
naming is D65's shape again: `addressable` checking `case["corpus"] == "spike_001"`
*and then* falling through to the port answers identically on all eleven notes, so
both behavioural tests pass over it. `test_the_split_reads_no_corpus_label` parses
the two functions instead, which is why it exists alongside tests that look like
they already cover it.

### Discovered work

**T-68** — the two `tool_fetch` modes write one path. `ADK_PATH` is a module
constant, so `--tool-fetch` overwrites the recording the plain run just made, and
T-63's exit asks for both modes' aggregates. Registered rather than folded in here:
"one recording per mode" is a question about how a measurement is stored and
compared, T-67's was about which notes each mode can reach, and the fix has a real
choice in it (a mode-suffixed filename, an `--out` argument, or one file holding
both) that `--compare` then has to reflect.

**Reversal condition.** If the spike notes ever acquire real patients — a T-06-
style manifest with bundles behind them — the split closes on its own, because
addressability is asked of the port. If instead a future corpus is deliberately
patient-less, this entry is the precedent: the corpus is not made to fit the
plane, the measurement is made to say which corpus it covers.

---

## D68 — One recording per `tool_fetch` mode, and the path is derived from the mode rather than chosen by the caller

T-68's design, written before the code.

### The finding

`scripts/run_adk_extraction.py` writes to `ADK_PATH`, a module constant. Both
modes use it, so the second run of the pair overwrites the first:

    python scripts/run_adk_extraction.py               -> eval/extraction/adk_results.json
    python scripts/run_adk_extraction.py --tool-fetch  -> eval/extraction/adk_results.json

T-63's exit condition asks for **both** modes' aggregates quoted beside
`eval/extraction/results.json`'s, so T-63 cannot close over one file. Registered
by D67 rather than folded into T-67 because "one recording per mode" is a
question about how a measurement is stored and compared, and T-67's was about
which notes each mode can reach.

Nothing exists on disk yet — `eval/extraction/` holds only `results.json`, since
T-63 has never run — and no document outside the script and its test names the
file. The fix is therefore free of migration, which is the only reason renaming
both files is on the table at all.

### The choice: the mode determines the path

    ADK_INLINE_PATH     = OUT_DIR / "adk_results_inline.json"
    ADK_TOOL_FETCH_PATH = OUT_DIR / "adk_results_tool_fetch.json"

    def adk_path(tool_fetch: bool) -> Path

There is no invocation of either mode that can land on the other's recording.
The overwrite stops being a thing a careful operator avoids and becomes a thing
the program cannot do, which is the same posture T-65 took with `MAX_ROWS`: a
bound the caller cannot forget to apply.

**`--out` was rejected.** It moves the destination into the caller's hands while
keeping one default, so the run that forgets the flag clobbers exactly as before —
avoidable, not impossible. It also puts a measurement's identity in shell history
instead of in the repo, and T-63's aggregates are quoted in a decisions entry that
has to say which file each column came from.

**One file holding both runs was rejected.** It needs read-modify-write on every
run, so a crash between read and write leaves a file whose two halves were
produced by different code; it carries two `measured_at` values in one payload;
and it breaks the record-for-record diff against `results.json`, which is flat.
The comparison this script exists to make is against the direct recording's
shape, so the ADK recordings keep that shape.

### Both names state their mode

`adk_results_inline.json` and `adk_results_tool_fetch.json`. The alternative —
keeping `adk_results.json` for the mode without the flag — leaves one file
unmarked, and an unmarked file is one a reader has to open to identify. The
payload's `tool_fetch` key would tell them, which is an argument for the name
being redundant, not for it being absent: the redundancy is what lets a reader
identify a recording from a directory listing or a `git log --stat`.

`inline` names what the mode does — the note text travels inline in the message —
rather than what it lacks. A file named for the absence of a flag (`no_tool_fetch`)
describes the command that produced it instead of the measurement it holds.

### `--compare` takes the same flag, and says what it read

One flag names the mode in both verbs. `--compare` gains three things, and the
third is the one T-68's exit is actually about:

- it **prints the path** it read for each side;
- the mode it labels the ADK column with comes from **the payload's own
  `tool_fetch` key**, never from the flag. The label is a fact about the file, not
  a restatement of the request — a label read off the request is true by
  construction and therefore reports nothing;
- it **refuses, exit 2**, when the payload's mode disagrees with the mode
  requested.

That last one is a harder response than the model and tier mismatches beside it,
which print `!!` and carry on, and the difference is worth naming. A comparison of
two tiers is still a true comparison of two recordings — loudly caveated, but the
numbers printed are the numbers in the files. A recording whose mode contradicts
the path it was loaded from means the output's own label is false, and this task's
exit condition is that `--compare` names which recording it is reading. Printing a
warning above a wrong label is not naming it.

Only reachable by hand-copying a file today, which is the honest scope of it: it
costs one comparison and it is the check that makes the exit condition true rather
than approximately true.

### The gate

`pytest tests/test_adk_measurement.py`, extended, still spending nothing. The
regression that matters is a single test that runs `measure()` in **both** modes
against one directory and asserts the first file's bytes are unchanged after the
second run — the defect stated as a test rather than as a filename assertion,
since a filename assertion passes on a program that writes both files and then
truncates one.

The `measure()` walk is parametrized over both modes: inline reaches all eleven
notes (10 scored, 1 failed, 0 skipped, with the stub failing note one to exercise
the `failed` branch) and `--tool-fetch` stays at T-67's 5/1/5.

Six mutations, each caught by the check that should catch it: `adk_path` ignoring
its argument, the two constants naming one file, `measure()` writing a constant
path (the original defect), `compare()` reading one recording whatever the mode,
the mismatch guard deleted, and the missing-file message dropping `--tool-fetch`
from the command it names.

**A seventh survived, and it is worth writing down.** Reading the column label off
`_mode(tool_fetch)` instead of off the payload changes no output at all — the
mismatch guard has already established the two are equal by the time the label is
built, so the flag and the payload cannot disagree at that line. The label source
is therefore *not* what makes the caption honest; the guard is. Keeping the label
on the payload is a claim about where the fact lives rather than a behaviour under
test, and it becomes load-bearing the moment the guard is softened to a warning —
which is the change this note exists to catch.

The mutation harness needed `--color=no` before it told the truth: `pytest -q`
writes `FAILED` lines prefixed with an ANSI escape, so a `^FAILED` scan reports
every mutation as surviving. Six false survivors, all of them caught, is the same
class of error as the stale `__pycache__` the method note in `CLAUDE.md` warns
about — a mutation harness that cannot see failures says the suite is worthless
when the suite is fine.

### A defect this task found by running the whole suite: T-67 closed red

`pytest` — the whole suite, not this task's file — fails at the commit before
this one, and has since T-67 landed:

    tests/test_adk_measurement.py:288  'gemini-3.5'
    D20: model identifiers written as string literals outside pa_agent/model_pin.py

`_recording()`, the helper T-67 added to build a fake payload for `--compare`,
wrote the model name out as a literal. `tests/test_model_pin.py` scans **tracked
Python**, and a test file is tracked Python — the scan makes no exception for a
fixture, and should not: a stale identifier in a fake recording is exactly the
shape of the defect D20 exists for, since the comparison it feeds asserts
`direct["model"] != adk["model"]`.

It went unnoticed because T-67's exit condition names
`pytest tests/test_adk_measurement.py`, which passes. **A task's exit command is
not a substitute for the suite**, and this is the second D67-shaped finding in two
tasks — the first was a script no gate ran, this is a gate no close ran.

Fixed here rather than registered, on D67's own precedent and for its reasons: it
is one line, in a file this task is already rewriting, and it is the difference
between "`pytest` returns zero" being true and being a claim nobody checked.
`_recording()` imports `PINNED_MODEL` and `MEASURED_TIER` like every other module
does. Recorded here so it is not silent.

### Reversal condition

A third mode, or a mode whose measurement is not one file, turns the two
constants into a dict and `adk_path` into its lookup. That is a mechanical change
and it is the shape this entry declines to build today: two modes exist, two
constants name them, and a registry of one-per-mode paths with two entries is
indirection bought before it is needed.

If the spike notes ever acquire patients (D67's reversal condition), the two
recordings converge on eleven notes each and `--compare`'s intersection logic
closes the gap by itself. Neither the filenames nor this entry needs to change.

---

## D69 — A task's exit condition is not the whole gate, so the close ritual becomes one command

T-69's design, written before the code. Discovered in the T-68 build.

### The finding, and where the incident is already recorded

T-68's close found that **T-67 had closed with `pytest` red**, and had been red
for a commit. The incident, its cause and its one-line repair are written up in
D68 under *"A defect this task found by running the whole suite: T-67 closed
red"* — `_recording()` wrote a model identifier as a string literal, and
`tests/test_model_pin.py` scans tracked Python with no exception for test files
(D20). This entry does not restate it; it fixes the gap it exposed.

**The gap is the close ritual, not the defect.** Every task on the board closes
on an exit condition naming one file or one script — T-67's was
`pytest tests/test_adk_measurement.py`, which passed throughout — and nothing
anywhere says the rest of the repo has to still be green when a task closes. So
a task can break a gate two doors down and close honestly by its own terms.

Two consecutive tasks have now produced a finding of this shape. T-67's close
found `scripts/run_adk_extraction.py` had never run at all — a script in no gate.
T-68's found a gate that no close ran. Both are the same sentence read from
different ends: **the set of checks a task runs is smaller than the set of checks
the repo has**, and nothing measures the difference.

### The change: working rule 4 gains a second command

> **Every task closes on a command that returns zero** — its own exit condition
> **and** `python scripts/check_gates.py`.

Amending a working rule is a design decision, which is why this entry exists
before the script does (working rule 5). Article VIII is unchanged and does not
need changing: it requires a task to close on a command that returns zero, and
this adds a second such command rather than weakening the first.

### Why a script and not just the rule

A rule with no command behind it is what D10 already refused: *a grep for a string
in a doc is not a check*. "Run everything before you close" is exactly that kind
of rule — it depends on the closer remembering what *everything* currently is,
which is the thing that drifts. The list belongs in a file that fails when it is
wrong.

Measured before writing this: the six script gates cost about four seconds
together and `pytest` about eight, so the whole ritual is roughly twelve seconds.
There is no cost argument for skipping it, which means there is no reason to
design a partial version.

**Working rule 9 was checked and does not bar this.** Its examples are external
systems — GCP, Terraform, containers, CI, vector search — and the rule is about
infrastructure the project has not earned. This is an eighty-line script that
runs commands the repo already has, and two consecutive closes with something red
or unrun is the earning.

### The membership rule, and why it is not my judgment

A command is in `GATES` iff **some task's exit condition names it** and it
**spends no model call and touches no network**. Both halves are checkable
against `docs/tasks.md` and neither is a taste call, so the list has an argument
behind every entry and a reader can audit it against the board.

That rule is what keeps plausible non-gates out. `scripts/run_extraction.py
--rescore` spends nothing and re-scores a recording, so a "costs nothing" rule
alone would admit it — but no task's exit names it, because it is the bookkeeping
half of a measurement rather than a check on the repo. `scripts/synthesize_notes.py
--verify` is the same shape: T-07 closes on `pytest tests/test_notes.py`, and the
suite already covers the corpus.

`pytest` bare is the first entry and subsumes the thirty-odd per-file `pytest`
exits on the board. That subsumption is the whole point — the per-file exit stays
the task's exit, and the suite is what the task is additionally answerable to.

**Deliberately excluded, each with its reason in the file:**
`scripts/verify_sources.py` without `--offline` (re-downloads; the `--offline`
variant is in, and D-note: it does *not* close T-02), `spike/spike_001/run.py`
bare, `scripts/run_extraction.py`, `scripts/run_adk_extraction.py` (all spend
model calls), `scripts/select_patients.py --generate` (Java and network),
`scripts/synthesize_notes.py` (regenerates a committed corpus),
`scripts/check_req_coverage.py` (**named by an exit condition but not yet
written** — A7's, unclaimed), `python -m pa_agent.cli` (T-25's exit takes a
patient argument the board leaves as `X`; `tests/test_determination.py` covers it
inside the suite), and `scripts/check_gates.py` itself.

### Two structural details

**The runner refuses to run inside pytest.** `check_gates` runs `pytest`, and
`pytest` collects `tests/test_check_gates.py`. A test that drove the real runner
end to end would recurse until something ran out. The guard reads
`PYTEST_CURRENT_TEST` and exits non-zero naming why, which turns a footgun into a
check the test asserts, and the test drives the runner with **stub commands**
instead. That split — the expensive real thing on one side, the bookkeeping
tested on the other — is D67's `--rescore` lesson arriving at a third site.

**A new script has to be classified.** The test walks tracked `scripts/*.py`,
`eval/*.py` and `spike/**/run.py` and asserts each is in `GATES` or in `EXCLUDED`
with a reason. Adding a script and forgetting the list fails the suite, rather
than silently shrinking the ritual — the same "adding a provider is one entry
here" pattern `pa_agent/model_pin.py` uses for `_MODEL_FAMILIES`. Without it the
list rots exactly the way the unwritten rule would have.

### The test that had to be deleted: a check whose failure mode is a fork bomb

The obvious test for the recursion guard is to spawn
`python scripts/check_gates.py` from inside pytest and assert it exits 2. It was
written, it passed, and the mutation pass then deleted the guard to see whether
anything caught it. **Nothing caught it — the harness hung for two minutes and
had to be killed.** With the guard gone the child ran the suite, the suite reached
that test, and it spawned another child.

Technically the mutation was "caught", in that the suite never finished. That is
not a catch worth having: a check whose failure mode is an unbounded process tree
is worse than the defect it looks for, and the mutation harness reported nothing
at all because it never got an exit code.

So the end-to-end spawn is gone and the guard is asserted by parsing `main` — the
`PYTEST_CURRENT_TEST` read exists and its line precedes every `run_gates` call.
That is T-64's AST shape used for a new reason: not "a behavioural test would pass
over the mutation" but "a behavioural test would not terminate". The behavioural
half still exists at the level that is safe — `main()` with `run_gates`
monkeypatched to fail if it is ever reached.

Seven mutations, each caught by the check that should catch it: a gate dropped
from the list, a failing gate reported but not counted, `main()` exiting zero
whatever the count, the guard deleted, the suite demoted from first, `--offline`
dropped from `verify_sources.py`, and an exclusion emptied of its reason.

### Rejected

**A git pre-commit hook.** It is the only option here that could *enforce* rather
than remind, and it is still wrong: `.git/hooks` is untracked, so the enforcement
would not survive a clone and would not appear in any diff a reviewer reads; it
is bypassable with `--no-verify`; and working rule 9 keeps the repo free of
machinery a reader has to know about to trust it. A tracked hook plus an install
step is CI with extra steps.

**CI.** Working rule 9 names it explicitly.

**Deriving the list by parsing `docs/tasks.md`.** Attractive, since the
membership rule is stated in terms of the board — but a parser cannot tell that
`verify_sources.py` needs `--offline` here and not there, that `pytest
tests/x.py` is subsumed by `pytest`, or that `check_req_coverage.py` does not
exist yet. Each of those is a judgment with a reason, and reasons are what the
`EXCLUDED` mapping records. The classification test gets the drift protection
without pretending the classification is mechanical.

### What this does not fix, stated plainly

It makes "everything is green" one command. **It cannot make anyone type it.**
The residual is a habit, and the honest version of that sentence is that this
lowers the cost of the habit to twelve seconds and removes the excuse of not
knowing what to run. If a third close-time finding of this shape appears, the
answer is not a longer rule — it is that the ritual needs enforcement the repo
has so far declined to buy, and this entry is the record of that price being
weighed once.

### Reversal condition

If the ritual grows past the point where a closer will run it — the plausible
trigger is `pytest` itself getting slow, not the script list getting longer — the
answer is to make the suite fast, not to split the gate. A `check_gates --fast`
that runs a subset would recreate exactly the gap this entry closes.
