"""T-97 — the medical-history review: candidates, the tri-state, and `would_affect`.

From the medications on a chart, the conditions the chart **supports and does
not carry**, each coloured by how much evidence it holds. Spec §11's design,
REQ-63 through REQ-66, D119.

**This module is pure.** It names no path, constructs no store, and returns its
own object; `Determination`, `workflow.STEPS` and `aggregate.assemble` know
nothing about it. That is why spec §11's *no verdict changes in v1.3* is
structural rather than measured: there is no field through which a suggestion
could reach a verdict. The caller supplies the facts — `cli.py` and the eval
harness both already construct the stores that serve them.

**Python assigns the colour.** The model is asked for a note quote and nothing
else — through a `QuoteRunner` (`pa_agent.quotes`, T-98, D122) that
`run_review` consults once per note and whose answers reach `review` as an
already-anchored mapping; it is never asked which colour and never asked which
code. Every comparison here is arithmetic over a declared constant:
`CodedValueSet.admits` for membership (REQ-59) and `EffectSignal.satisfied_by`
for the threshold, each done in one place. A yellow is then Article V's to
check: `run_review` hands each one to the verifier as a `(candidate, quotes)`
claim and demotes a rejected one to red that says so (REQ-67).

**Three things this module deliberately refuses to do:**

- **Colour a candidate red because nobody looked.** Red means *nothing on the
  chart supports it*. On a chart that carries notes, a candidate whose
  structured signal did not cross can only be separated from yellow by reading
  those notes, so with no quote source supplied the review **raises**. "The
  system did not look" and "the chart does not say" are the two things a fault
  exists to keep apart (D90). A chart with **no notes** needs no quote source:
  red is then a fact about the chart.
- **Treat a crossed threshold nobody can cite as red.** An observation that
  crosses but carries no span cannot support a green (REQ-5's shape), and
  reporting red for it would state the opposite of what the chart says. It
  raises; the patient port computes a span for every resource it serves, so this
  is a defect in a caller's facts rather than a state real data reaches.
- **Colour a candidate red because the chart measured it and answered no.** Red
  means *nothing on the chart*, and US-11's own criterion says so. A glucose of
  76 against a threshold of 126 is the chart answering, so the candidate is
  **withheld** with its measurement rather than suggested — the same shape as a
  candidate the chart already codes, and for the same reason: a suggestion the
  record refutes is a false suggestion, and *the lab says no* must not read as
  *nobody measured*.
- **Read the prescription as evidence of the condition.** The active order is
  what makes a row a *candidate*; it is carried on the suggestion as
  `medication` and never as a citation. That is why a red suggestion cites
  nothing on a chart that plainly documents the drug.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pa_agent.contracts import (
    CallMetrics,
    CodedConcept,
    CodedValueSet,
    Condition,
    Criterion,
    Document,
    EvidenceSpan,
    HistoryReview,
    IcdSuggestion,
    Medication,
    MedicationEffectRow,
    Observation,
    RunTrace,
    SuggestionColour,
    WithheldCandidate,
    WithholdReason,
)
from pa_agent.verifier import build_history_claim_payload

if TYPE_CHECKING:  # duck-typed at runtime; the module stays off both planes
    from pa_agent.quotes import QuoteRunner
    from pa_agent.verifier import VerifierRunner

#: The medication status a candidate is drawn from. The filter is the review's
#: judgment and not the adapter's, which is the rule `criteria.py` states for
#: the same comparison on the coverage path (D31, D39).
ACTIVE_STATUS = "active"

#: The condition status a suppression is drawn from. A resolved condition is not
#: *already carried*: the packet is about what is documented now.
ACTIVE_CONDITION_STATUS = "active"

#: The constant a criterion names its value set in. A string rather than a tree
#: field, because that is where `criteria.py` reads it from too (D111).
VALUE_SET_CONSTANT = "value_set_id"


class QuoteSourceNotConsulted(RuntimeError):
    """A candidate needed a note quote and no quote source was supplied.

    Raised rather than answered `RED`, because red is a claim about the chart
    and this is a fact about the run (D90, D119). The message names the way to
    consult one: `run_review` with a `QuoteRunner` (T-98, D122).
    """


class UnciteableSignal(RuntimeError):
    """An observation crossed the threshold and carries no span to cite.

    REQ-5's shape: a colour above red is a claim about the chart and carries its
    evidence. Demoting this to red would report *nothing on the chart* for a
    chart that measured it.
    """


class MixedAlreadyCodedSystems(ValueError):
    """A row's already-coded concepts are not all in one code system.

    REQ-59's rule, applied to the knowledge table: a set half in SNOMED and
    half in something else compares cleanly and matches the wrong things.
    """


def _active_medications(
    medications: Sequence[Medication], products: CodedValueSet
) -> list[Medication]:
    """The active prescriptions this ingredient's expansion admits.

    `criteria.py::_active_members`' filter, on the knowledge plane's sets. The
    expansion is why this matches at all: a row declares an ingredient and the
    corpus prescribes clinical drugs, so comparing the row's own code against
    `Medication.code` matches nothing in any committed bundle (D119).
    """
    return [
        m
        for m in medications
        if m.status == ACTIVE_STATUS and products.admits(m.code, m.system)
    ]


def candidate_rows(
    rows: Sequence[MedicationEffectRow],
    products: Mapping[str, CodedValueSet],
    medications: Sequence[Medication],
) -> list[MedicationEffectRow]:
    """The rows an active prescription makes candidates, in `row_id` order.

    The first thing `review` computes, and the thing `run_review` needs before
    it: a chart with no candidate has no quote to ask for. A row with no
    expansion raises for every row, matched or not — an unmatched row is one
    that silently never fires (REQ-63, D119).
    """
    candidates: list[MedicationEffectRow] = []
    for row in sorted(rows, key=lambda r: r.row_id):
        expansion = products.get(row.ingredient.code)
        if expansion is None:
            raise KeyError(
                f"row {row.row_id} declares ingredient {row.ingredient.code} and "
                "no expansion was supplied for it; an unmatched row is one that "
                "silently never fires (REQ-63, D119)"
            )
        if _active_medications(medications, expansion):
            candidates.append(row)
    return candidates


def _most_recent(medications: Sequence[Medication]) -> Medication:
    """The order a reviewer would be shown: latest `authored_on`, then code.

    Undated orders sort last rather than first — an order with no date is the
    weaker citation of the two, and the tie-break is the code so the choice is
    stable across store orders (D104's rule about position deciding anything).
    """
    return sorted(
        medications,
        key=lambda m: (m.authored_on is not None, m.authored_on or "", m.code),
        reverse=True,
    )[0]


def _already_coded_set(row: MedicationEffectRow) -> CodedValueSet | None:
    """The row's already-coded concepts as one set, or `None` when it has none.

    `None` is a real answer here and is the state T-96 made legal only with a
    declared `unsourced_reason`: hypotension has no reachable SNOMED code, so
    that row can never be suppressed and can never name a `would_affect`. Both
    consequences are stated in the row itself.
    """
    if not row.already_coded:
        return None
    systems = {concept.system for concept in row.already_coded}
    if len(systems) > 1:
        raise MixedAlreadyCodedSystems(
            f"row {row.row_id} lists already-coded concepts in {sorted(systems)}; "
            "one condition is one vocabulary (REQ-59)"
        )
    return CodedValueSet(
        value_set_id=f"already_coded_{row.row_id}",
        system=systems.pop(),
        codes=frozenset(concept.code for concept in row.already_coded),
    )


def _coded_conditions(
    conditions: Sequence[Condition], coded: CodedValueSet
) -> list[Condition]:
    """The active conditions that mean this chart already carries the effect."""
    return [
        c
        for c in conditions
        if c.clinical_status == ACTIVE_CONDITION_STATUS
        and coded.admits(c.code, c.system)
    ]


def _signal_reading(
    observations: Sequence[Observation], row: MedicationEffectRow
) -> Observation | None:
    """The most recent observation of the row's signal, crossing or not.

    **No lookback window.** Neither the knowledge table nor any document in
    either corpus states one, and inventing a default here is the mistake D40
    was written to avoid — so the most recent measurement is the one read, at
    whatever age, and its date rides on the suggestion for the reviewer to weigh.
    A window later is a declared constant with a name and a date.

    The comparison is by LOINC code alone because `Observation` carries no
    `system` field; D119 records that asymmetry with REQ-59 rather than defaulting
    a system the plane does not serve.
    """
    matching = [o for o in observations if o.code == row.signal.code]
    if not matching:
        return None
    latest = max(matching, key=lambda o: (o.effective_date, o.value))
    if not row.signal.satisfied_by(latest.value):
        return latest
    if latest.span is None:
        raise UnciteableSignal(
            f"row {row.row_id}: observation {latest.code} = {latest.value} on "
            f"{latest.effective_date} crosses its threshold and carries no span. "
            "A green suggestion cites the measurement that made it green; "
            "reporting red would say the chart holds nothing (REQ-5, D119)."
        )
    return latest


def _would_affect(
    row: MedicationEffectRow,
    criteria: Sequence[Criterion],
    value_sets: Mapping[str, CodedValueSet],
    policy_version_id: str | None,
) -> tuple[tuple[str, ...], str | None]:
    """The criteria whose value set admits a code this condition would carry.

    REQ-66. The comparison is over the row's **already-coded SNOMED codes**, not
    over its ICD-10 code: measured across every committed value set, the table's
    ICD-10 codes match nothing in either direction — the only ICD-10 codes any
    set carries are the `icd10_anchor`s on the ultrasound tree's indications,
    and they are `N18.x` and `I10` against a table holding `N28.9` (D119). An
    ICD-keyed comparison therefore returns an empty tuple on all five rows while
    passing every behavioural check this corpus can produce, which is why it is
    on the mutation list.

    Empty is a real answer, and the note says which kind of empty it is.
    """
    coded = _already_coded_set(row)
    if coded is None:
        return (), (
            f"the row declares no code for {row.effect_display}, so there is "
            "nothing to compare against a value set (REQ-62)"
        )
    if policy_version_id is None:
        # No tree governs this request, so there is no value set to compare
        # against. The review is still a fact about the chart, and an empty
        # `would_affect` never travels without its reason (T-99, D123).
        return (), (
            "no tree governs this request, so no value set was compared "
            f"against {', '.join(sorted(coded.codes))} (REQ-66)"
        )

    affected: list[str] = []
    for criterion in criteria:
        constant = criterion.constants.get(VALUE_SET_CONSTANT)
        if constant is None:
            continue
        value_set = value_sets.get(str(constant.value))
        if value_set is None:
            continue
        if any(value_set.admits(code, coded.system) for code in coded.codes):
            affected.append(criterion.id)
    if not affected:
        return (), (
            f"no value set {policy_version_id} names admits "
            f"{', '.join(sorted(coded.codes))}"
        )
    return tuple(affected), None


def review(
    *,
    patient_id: str,
    policy_version_id: str | None,
    rows: Sequence[MedicationEffectRow],
    products: Mapping[str, CodedValueSet],
    medications: Sequence[Medication],
    conditions: Sequence[Condition],
    observations: Sequence[Observation],
    criteria: Sequence[Criterion] = (),
    value_sets: Mapping[str, CodedValueSet] | None = None,
    notes: Sequence[Document] = (),
    quotes: Mapping[str, tuple[EvidenceSpan, ...]] | None = None,
) -> HistoryReview:
    """The `icd_suggestions` block for one chart under one tree.

    `products` maps a row's ingredient rxcui to its pinned expansion, and the
    set mapping resolves each `value_set_id` a criterion names. Both are
    passed in rather than fetched, which is what keeps this module off both
    planes and out of `tests/test_planes.py`'s both-planes list.

    `quotes` maps a `row_id` to the anchored note spans documenting its effect.
    `None` means **no quote source was consulted** — legal on a chart with no
    notes, and a raise on one with notes (see the module docstring). An empty
    tuple for a row means the source was consulted and found nothing, which is
    red.

    Rows are answered in a stable order — `row_id` — so the block does not
    reorder between runs on the same chart.
    """
    value_sets = value_sets or {}
    suggestions: list[IcdSuggestion] = []
    withheld: list[WithheldCandidate] = []

    for row in candidate_rows(rows, products, medications):
        matched = _active_medications(medications, products[row.ingredient.code])
        medication = _most_recent(matched)
        on_chart = CodedConcept(
            # Never `or expansion.system`: `_active_medications` admits a
            # medication only when `admits(code, system)` holds, and that is
            # `system == self.system` (REQ-59), so a matched medication's
            # system *is* the expansion's. A fallback here is unreachable,
            # and naming a local of `candidate_rows` made it a NameError
            # waiting on a loosened filter (T-99, D123).
            system=medication.system,
            code=medication.code,
            display=medication.display,
        )

        coded = _already_coded_set(row)
        carried = _coded_conditions(conditions, coded) if coded is not None else []
        if carried:
            withheld.append(
                WithheldCandidate(
                    row_id=row.row_id,
                    reason=WithholdReason.ALREADY_CODED,
                    icd10_code=row.icd10_code,
                    effect_display=row.effect_display,
                    medication=on_chart,
                    ingredient=row.ingredient,
                    suppressed_by=tuple(
                        CodedConcept(
                            system=c.system or coded.system,
                            code=c.code,
                            display=None,
                        )
                        for c in carried
                    ),
                    citations=tuple(c.span for c in carried if c.span is not None),
                )
            )
            continue

        affects, affects_note = _would_affect(
            row, criteria, value_sets, policy_version_id
        )
        reading = _signal_reading(observations, row)
        if reading is not None and not row.signal.satisfied_by(reading.value):
            # The chart measured the thing and answered no. Not red: red is
            # *nothing on the chart*, and a suggestion the record refutes is a
            # false one (D119).
            withheld.append(
                WithheldCandidate(
                    row_id=row.row_id,
                    reason=WithholdReason.SIGNAL_NOT_CROSSED,
                    icd10_code=row.icd10_code,
                    effect_display=row.effect_display,
                    medication=on_chart,
                    ingredient=row.ingredient,
                    signal=row.signal,
                    observed_value=reading.value,
                    observed_on=reading.effective_date,
                    citations=(
                        (reading.span,) if reading.span is not None else ()
                    ),
                )
            )
            continue

        crossing = reading
        if crossing is not None:
            suggestions.append(
                IcdSuggestion(
                    row_id=row.row_id,
                    colour=SuggestionColour.GREEN,
                    icd10_code=row.icd10_code,
                    icd10_title=row.icd10_title,
                    effect_display=row.effect_display,
                    effect=row.effect,
                    medication=on_chart,
                    ingredient=row.ingredient,
                    citations=(crossing.span,),
                    signal=row.signal,
                    observed_value=crossing.value,
                    observed_on=crossing.effective_date,
                    would_affect=affects,
                    would_affect_note=affects_note,
                )
            )
            continue

        if quotes is None:
            if notes:
                raise QuoteSourceNotConsulted(
                    f"row {row.row_id} on patient {patient_id} needs a note quote "
                    f"to separate yellow from red, the chart carries "
                    f"{len(notes)} note(s), and no quote source was consulted. "
                    "Answering red here would report 'the chart does not say' "
                    "for a chart nobody read (D90). Consult a QuoteRunner through "
                    "history.run_review; RecordedQuoteRunner replays "
                    "eval/history/results.json for zero calls (T-98, D122)."
                )
            anchored: tuple[EvidenceSpan, ...] = ()
        else:
            anchored = tuple(quotes.get(row.row_id) or ())

        colour = SuggestionColour.YELLOW if anchored else SuggestionColour.RED
        suggestions.append(
            IcdSuggestion(
                row_id=row.row_id,
                colour=colour,
                icd10_code=row.icd10_code,
                icd10_title=row.icd10_title,
                effect_display=row.effect_display,
                effect=row.effect,
                medication=on_chart,
                ingredient=row.ingredient,
                citations=anchored,
                would_affect=affects,
                would_affect_note=affects_note,
            )
        )

    return HistoryReview(
        patient_id=patient_id,
        policy_version_id=policy_version_id,
        suggestions=tuple(suggestions),
        withheld=tuple(sorted(withheld, key=lambda w: w.row_id)),
    )


# --------------------------------------------------------------------------
# The run: the review beside what it cost (T-98, D122)
# --------------------------------------------------------------------------


@dataclass
class HistoryRun:
    """The review plus how it was reached — `WorkflowRun`'s two-object shape.

    `review` is what a human reads; `traces` is one `RunTrace` per note the
    quote runner consulted, every turn's `CallMetrics` on it, the re-ask
    included (D71); `verifications` is what Article V's check of each yellow
    cost; `rejections` names the candidates the verifier demoted, with its
    reason. None of this touches `Determination.metrics`: the review's cost is
    reported beside the determination's and never inside it (D119, D122).
    """

    review: HistoryReview
    traces: list[RunTrace] = field(default_factory=list)
    verifications: list[CallMetrics] = field(default_factory=list)
    rejections: tuple[tuple[str, str], ...] = ()

    @property
    def notes_consulted(self) -> int:
        return len(self.traces)

    @property
    def metrics(self) -> list[CallMetrics]:
        return [m for trace in self.traces for m in trace.metrics] + list(self.verifications)

    @property
    def model_calls(self) -> int:
        return len(self.metrics)

    @property
    def total_input_tokens(self) -> int:
        return sum(m.input_tokens for m in self.metrics)

    @property
    def total_output_tokens(self) -> int:
        return sum(m.output_tokens for m in self.metrics)

    @property
    def total_wall_time_ms(self) -> float:
        return sum(m.wall_time_ms for m in self.metrics)


def run_review(
    *,
    quote_runner: "QuoteRunner | None",
    patient_id: str,
    policy_version_id: str | None,
    rows: Sequence[MedicationEffectRow],
    products: Mapping[str, CodedValueSet],
    medications: Sequence[Medication],
    conditions: Sequence[Condition],
    observations: Sequence[Observation],
    criteria: Sequence[Criterion] = (),
    value_sets: Mapping[str, CodedValueSet] | None = None,
    notes: Sequence[Document] = (),
    verifier: "VerifierRunner | None" = None,
) -> HistoryRun:
    """Consult the notes, then `review`, then Article V on every yellow.

    The runner is asked about **every** row on **every** note, in store order —
    the request is one fixed configuration a chart cannot vary (D45, D122) —
    and only when the chart has notes and at least one candidate; Python keeps
    what `review` needs. Every row is keyed in `quotes`, so a candidate that
    reaches the quote step reads `()` — consulted, nothing found — and never
    `None`. With no runner on a note-bearing chart the review still raises
    `QuoteSourceNotConsulted`, one layer up (D90).

    A yellow is a claim about the chart, so with a verifier supplied each one
    is handed over as a `(candidate, quotes)` claim — the passages sliced from
    the note, never taken from the model (D18) — and a rejection demotes it to
    red with `verifier_rejected` set, no retry (REQ-18's rule, REQ-67). A
    verifier fault propagates: it is not an answer, and neither colour is a
    default for it (D90). On the committed corpus no yellow exists, so
    `RecordedVerifierRunner` is never reached and holds no history claim;
    one that appeared would raise `NOT_RECORDED` rather than default to
    accepted (D122).
    """
    quotes: dict[str, tuple[EvidenceSpan, ...]] | None = None
    traces: list[RunTrace] = []
    if notes and quote_runner is not None and candidate_rows(rows, products, medications):
        gathered: dict[str, list[EvidenceSpan]] = {row.row_id: [] for row in rows}
        for note in notes:
            result = quote_runner.run(note.document_id, note.text, rows)
            for row_id, spans in result.spans.items():
                gathered.setdefault(row_id, []).extend(spans)
            if result.trace is not None:
                traces.append(result.trace)
        quotes = {row_id: tuple(spans) for row_id, spans in gathered.items()}

    reviewed = review(
        patient_id=patient_id,
        policy_version_id=policy_version_id,
        rows=rows,
        products=products,
        medications=medications,
        conditions=conditions,
        observations=observations,
        criteria=criteria,
        value_sets=value_sets,
        notes=notes,
        quotes=quotes,
    )

    verifications: list[CallMetrics] = []
    rejections: list[tuple[str, str]] = []
    if verifier is not None and any(
        s.colour is SuggestionColour.YELLOW for s in reviewed.suggestions
    ):
        by_id = {note.document_id: note for note in notes}
        suggestions: list[IcdSuggestion] = []
        for suggestion in reviewed.suggestions:
            if suggestion.colour is not SuggestionColour.YELLOW:
                suggestions.append(suggestion)
                continue
            passages = [by_id[span.document_id].slice(span) for span in suggestion.citations]
            answer = verifier.run(build_history_claim_payload(suggestion, passages))
            if answer.metrics is not None:
                verifications.append(answer.metrics)
            if answer.accept:
                suggestions.append(suggestion)
                continue
            rejections.append((suggestion.row_id, answer.reason))
            suggestions.append(
                IcdSuggestion(
                    **{
                        **suggestion.model_dump(),
                        "colour": SuggestionColour.RED,
                        "citations": (),
                        "verifier_rejected": True,
                    }
                )
            )
        reviewed = HistoryReview(
            patient_id=reviewed.patient_id,
            policy_version_id=reviewed.policy_version_id,
            suggestions=tuple(suggestions),
            withheld=reviewed.withheld,
        )

    return HistoryRun(
        review=reviewed,
        traces=traces,
        verifications=verifications,
        rejections=tuple(rejections),
    )
