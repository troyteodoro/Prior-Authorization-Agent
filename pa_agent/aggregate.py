"""T-19 — the aggregator: criterion verdicts become a determination (D62).

REQ-19 says the overall result is computed by evaluating **the policy's** boolean
expression over the criterion verdicts, in Python. Two words in that sentence are
load-bearing and this module is built around both.

*The policy's.* The expression is read from the criteria tree, parsed, and
evaluated. It is not hardcoded as `all(...)`. Today's tree says
`a AND b AND c1 AND c2 AND c3 AND c4 AND c5`, and a conjunction over the seven
verdicts would produce identical answers for it — which is exactly why the
shortcut is tempting and exactly why it is wrong. A tree carrying an `OR`, which
is what a second jurisdiction's "one of these comorbidities *or* a documented
attempt" looks like, would be silently ignored by the shortcut and the system
would keep passing every test it has. Article VII wants the clinical rule to live
in the reviewed file; a rule the code does not read is not in the file.

*In Python.* Nothing here consults a model, and this module imports only the
contracts — no store, no index, no runner, no ADK. A model that could see the
verdicts could recombine them, and REQ-19 exists because that recombination is
the last place a hallucination can flip an outcome after every span has been
checked.

**Three-valued, and asymmetric on purpose.** `INSUFFICIENT_EVIDENCE` is not
false. A criterion nobody could substantiate propagates to an overall
`INSUFFICIENT_EVIDENCE` (REQ-20), because "we could not tell" and "the chart says
no" send Sam to two different places, and only the first has a gap list worth
reading. A false `NOT_MET` costs an unnecessary chart review; a false `MET`
produces a denial the specialist did not expect (A2's asymmetry), and unsupported
never becomes met.

`ERROR` is not handled here and that is deliberate: `Determination` refuses to be
constructed over one (REQ-26), so an errored criterion cannot reach an outcome to
be aggregated into. Mapping a fault to `ERROR` in the first place is T-29's.
"""

from __future__ import annotations

from collections import Counter

from pa_agent.contracts import (
    CoverageClaim,
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
)

#: The operators a `decision_expression` may use. Closed, and small on purpose:
#: an operator this module does not implement must fail loudly rather than be
#: approximated, because approximating a clinical rule is worse than refusing it.
_OPERATORS = ("AND", "OR")

#: Words that look like criterion ids and are not. Without this, a tree written
#: with `NOT` fails as "no verdict for criterion 'NOT'", which sends the policy
#: author looking for a missing criterion instead of an unimplemented operator.
_RESERVED = frozenset({"NOT", "XOR", "IMPLIES", "AT_LEAST", "ANY", "ALL"})


class DecisionExpressionError(ValueError):
    """The tree's `decision_expression` could not be evaluated as written.

    Raised, never defaulted. A tree whose expression names a criterion that did
    not produce a verdict, or uses an operator this module does not implement, is
    a policy the system cannot apply — and answering anyway would be answering
    from a rule nobody wrote.
    """


def _tokenize(expression: str) -> list[str]:
    tokens = expression.replace("(", " ( ").replace(")", " ) ").split()
    if not tokens:
        raise DecisionExpressionError(
            "the criteria tree's decision_expression is empty; REQ-19 has "
            "nothing to evaluate and an empty rule is not an approval"
        )
    return tokens


def evaluate_decision_expression(
    expression: str, verdicts: dict[str, CriterionVerdict]
) -> CriterionVerdict:
    """Evaluate the policy's expression over criterion verdicts, three-valued.

    Returns `MET`, `NOT_MET`, or `INSUFFICIENT_EVIDENCE`.

    The three-valued truth tables are the interesting part, and they are not
    Kleene's by accident:

    - `AND`: any `INSUFFICIENT_EVIDENCE` wins over `MET`, and `NOT_MET` wins over
      everything. A conjunction with one leg unknown and another leg false *is*
      false — the chart has a substantive failure and Sam does not need to go
      collect anything to learn that.
    - `OR`: any `MET` wins outright, because one satisfied leg satisfies the
      disjunction whatever the others did. Otherwise an unknown leg dominates.

    Parentheses are supported, precedence is `AND` over `OR` as in Python. No
    `NOT`: no tree has needed one, and a negated criterion is a criterion whose
    verdict semantics need thinking about rather than an operator to add.
    """
    tokens = _tokenize(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def take() -> str:
        nonlocal position
        token = tokens[position]
        position += 1
        return token

    def atom() -> CriterionVerdict:
        token = peek()
        if token is None:
            raise DecisionExpressionError(
                f"decision_expression {expression!r} ends where a criterion was "
                "expected"
            )
        if token == "(":
            take()
            value = disjunction()
            if peek() != ")":
                raise DecisionExpressionError(
                    f"decision_expression {expression!r} has an unclosed group"
                )
            take()
            return value
        if token in _OPERATORS or token == ")":
            raise DecisionExpressionError(
                f"decision_expression {expression!r}: expected a criterion id, "
                f"found {token!r}"
            )
        criterion_id = take()
        if criterion_id.upper() in _RESERVED:
            raise DecisionExpressionError(
                f"decision_expression {expression!r} uses {criterion_id!r}, which "
                f"this evaluator does not implement (it knows {list(_OPERATORS)}). "
                "An operator that is approximated is a clinical rule that is "
                "approximated — REQ-19 wants the tree's rule or an error."
            )
        if criterion_id not in verdicts:
            raise DecisionExpressionError(
                f"decision_expression names criterion {criterion_id!r}, which "
                f"produced no verdict. Known: {sorted(verdicts)}. A criterion "
                "the tree requires and nobody evaluated cannot be defaulted — "
                "defaulting it true approves on a rule nobody applied, and "
                "defaulting it false denies on one."
            )
        return verdicts[criterion_id]

    def conjunction() -> CriterionVerdict:
        value = atom()
        while peek() == "AND":
            take()
            value = _and(value, atom())
        return value

    def disjunction() -> CriterionVerdict:
        value = conjunction()
        while peek() == "OR":
            take()
            value = _or(value, conjunction())
        return value

    result = disjunction()
    if position != len(tokens):
        trailing = tokens[position]
        if trailing.upper() in _RESERVED:
            # An unimplemented operator stops where an operator was expected, not
            # where a criterion was, so it lands here rather than in `atom()`.
            # Saying so beats "trailing 'NOT'", which reads like a typo.
            raise DecisionExpressionError(
                f"decision_expression {expression!r} uses {trailing!r}, which "
                f"this evaluator does not implement (it knows {list(_OPERATORS)}). "
                "An operator that is approximated is a clinical rule that is "
                "approximated — REQ-19 wants the tree's rule or an error."
            )
        raise DecisionExpressionError(
            f"decision_expression {expression!r}: trailing {trailing!r}. "
            "A rule that does not parse cleanly is not applied partially."
        )
    return result


def _and(left: CriterionVerdict, right: CriterionVerdict) -> CriterionVerdict:
    if CriterionVerdict.NOT_MET in (left, right):
        return CriterionVerdict.NOT_MET
    if CriterionVerdict.INSUFFICIENT_EVIDENCE in (left, right):
        return CriterionVerdict.INSUFFICIENT_EVIDENCE
    return CriterionVerdict.MET


def _or(left: CriterionVerdict, right: CriterionVerdict) -> CriterionVerdict:
    if CriterionVerdict.MET in (left, right):
        return CriterionVerdict.MET
    if CriterionVerdict.INSUFFICIENT_EVIDENCE in (left, right):
        return CriterionVerdict.INSUFFICIENT_EVIDENCE
    return CriterionVerdict.NOT_MET


_OUTCOME_FOR = {
    CriterionVerdict.MET: DeterminationOutcome.MET,
    CriterionVerdict.NOT_MET: DeterminationOutcome.NOT_MET,
    CriterionVerdict.INSUFFICIENT_EVIDENCE: DeterminationOutcome.INSUFFICIENT_EVIDENCE,
}


def aggregate_outcome(
    expression: str, results: list[CriterionResult]
) -> DeterminationOutcome:
    """The overall outcome for a set of criterion results (REQ-19, REQ-20).

    REQ-20 is enforced independently of the expression, not as a consequence of
    it. Under today's conjunction the two agree, but a tree with an `OR` could
    reach `MET` through one satisfied leg while another leg is unsubstantiated —
    and REQ-20 says an unsupported criterion never sits under an approval,
    whatever the boolean says. `Determination` refuses that combination too, so
    this is belt and braces on the one rule Article IV states unconditionally.
    """
    if not results:
        raise DecisionExpressionError(
            "no criterion results to aggregate; a determination over zero "
            "criteria would be an outcome nobody evaluated"
        )

    counts = Counter(r.criterion_id for r in results)
    duplicated = sorted(cid for cid, n in counts.items() if n > 1)
    if duplicated:
        raise DecisionExpressionError(
            f"two results for criterion(s) {duplicated}; one criterion has one "
            "verdict, and silently keeping the last would make the outcome "
            "depend on evaluation order"
        )
    verdicts = {r.criterion_id: r.verdict for r in results}

    combined = evaluate_decision_expression(expression, verdicts)

    if combined is CriterionVerdict.MET and any(
        v is CriterionVerdict.INSUFFICIENT_EVIDENCE for v in verdicts.values()
    ):
        return DeterminationOutcome.INSUFFICIENT_EVIDENCE

    return _OUTCOME_FOR[combined]


def assemble(
    patient_id: str,
    procedure_code: str,
    policy_version_id: str,
    decision_expression: str,
    results: list[CriterionResult],
    metrics: list | None = None,
) -> Determination:
    """Build the reviewable artifact (REQ-19, REQ-21, REQ-39, REQ-4).

    `gap_list` and `discrepancies` are computed properties on `Determination`,
    not arguments: REQ-21's list must name every criterion not resolved `MET`,
    and a stored copy is a second source of truth free to disagree with the
    results it summarizes. So there is nothing to pass and nothing to get wrong.

    `coverage_claim` is deliberately **not** set here. It is the denial's
    citation and valid only with `NOT_COVERED` (D32); a criteria-tree outcome is
    not a denial under a coverage rule, it is an adjudication of the patient's
    evidence, and the citations for that live on the criterion results.
    """
    return Determination(
        patient_id=patient_id,
        procedure_code=procedure_code,
        policy_version_id=policy_version_id,
        outcome=aggregate_outcome(decision_expression, results),
        criterion_results=results,
        metrics=list(metrics or []),
    )


def contractor_citations(claim: CoverageClaim) -> list[EvidenceSpan]:
    """The two spans a contractor-determined determination must carry (REQ-42).

    D33's obligation, written into T-19's exit by T-24: a determination for a
    procedure CMS delegated cites **both** the delegation and the governing MAC's
    exercise of it, never the NCD alone. The NCD deliberately does not answer for
    43775 — it non-covers laparoscopic sleeve gastrectomy only *"prior to June 27,
    2012"* and hands the question to the contractors after that — so a packet
    citing the NCD alone cites a document that declines to decide.

    **Which field holds which half differs by coverage status**, and getting that
    wrong is easy enough that it is worth writing down. For a non-covered
    procedure (E3) the claim's own quote is the bullet naming the procedure and
    `scoping_quote` is the sentence that makes the bullet a denial — the bullet
    alone would slice back perfectly and mean nothing. For a contractor-determined
    procedure there is nothing to scope: **the delegation sentence is the claim
    itself**, and `corroborating_quote` carries the MAC's exercise of it.

    Returns spans rather than strings so the caller can validate both through
    T-11. Raises when the second half is missing, because a contractor
    determination that can only cite the delegation is the artifact D33 refused.
    """
    if claim.corroborating_quote is None:
        raise DecisionExpressionError(
            "a contractor-determined claim carries no corroborating_quote, so "
            "this determination could only cite the NCD's delegation — which "
            "declines to decide the question (REQ-42, D33). The governing MAC's "
            "exercise of the delegation is the other half of the answer."
        )
    return [
        EvidenceSpan(
            document_id=claim.document_id,
            char_start=claim.char_start,
            char_end=claim.char_end,
            quote=claim.quote,
        ),
        claim.corroborating_quote,
    ]
