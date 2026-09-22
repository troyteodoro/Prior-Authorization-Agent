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
else, and even then only from T-98; it is never asked which colour and never
asked which code. Every comparison here is arithmetic over a declared constant:
`CodedValueSet.admits` for membership (REQ-59) and `EffectSignal.satisfied_by`
for the threshold, each done in one place.

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

from pa_agent.contracts import (
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
    SuggestionColour,
    WithheldCandidate,
    WithholdReason,
)

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
    and this is a fact about the run (D90, D119). The message names T-98, which
    is the row that supplies the runner.
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
    policy_version_id: str,
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
    policy_version_id: str,
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

    for row in sorted(rows, key=lambda r: r.row_id):
        expansion = products.get(row.ingredient.code)
        if expansion is None:
            raise KeyError(
                f"row {row.row_id} declares ingredient {row.ingredient.code} and "
                "no expansion was supplied for it; an unmatched row is one that "
                "silently never fires (REQ-63, D119)"
            )
        matched = _active_medications(medications, expansion)
        if not matched:
            continue

        medication = _most_recent(matched)
        on_chart = CodedConcept(
            system=medication.system or expansion.system,
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
                    "for a chart nobody read (D90). The runner is T-98's."
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
