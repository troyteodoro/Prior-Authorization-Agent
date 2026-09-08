"""T-31 — `gap_reason` on `INSUFFICIENT_EVIDENCE` (REQ-31, D44).

The field that tells Sam what to go collect. This file gates the
**vocabulary** — the closed enum, the validator that makes it non-optional
exactly where it belongs, and its propagation onto the gap list Sam reads.

It deliberately does **not** gate the predicates that emit each value: E8's
`UNSUBSTANTIATED_ASSERTION` comes from extraction (T-15) plus c3 (T-16), and
E10b's `SOURCE_CONFLICT` from reconciliation (T-33). What can be asserted
honestly today is that the three values are distinct and reachable, and that
each case's manifest carries the shape that justifies its reason (D44).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import (
    CriterionResult,
    CriterionVerdict,
    Determination,
    DeterminationOutcome,
    EvidenceSpan,
    GapReason,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
SPEC_PATH = REPO_ROOT / "docs" / "spec.md"

SPAN = EvidenceSpan(document_id="d", char_start=0, char_end=4)


@pytest.fixture(scope="module")
def manifests() -> dict[str, dict]:
    """case id -> the manifest claiming it."""
    by_case: dict[str, dict] = {}
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        for case in body["cases"]:
            by_case[case] = body
    return by_case


# --------------------------------------------------------------------------
# The enum
# --------------------------------------------------------------------------


def test_the_enum_holds_exactly_the_four_reasons():
    """Closed on purpose: REQ-31 says two values producing the same next
    action are one value, so a fifth member has to name a fifth action."""
    assert {r.value for r in GapReason} == {
        "NO_EVIDENCE_RETRIEVED",
        "UNSUBSTANTIATED_ASSERTION",
        "VERIFIER_REJECTED",
        "SOURCE_CONFLICT",
    }


def test_every_reason_the_spec_tabulates_exists_in_the_enum():
    """The spec's REQ-31 table is the source; drift in either direction is a
    gap the code can produce that nobody documented, or the reverse."""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    for reason in GapReason:
        assert f"`{reason.value}`" in spec, f"{reason.value} is undocumented"


# --------------------------------------------------------------------------
# The validator: required here, refused everywhere else
# --------------------------------------------------------------------------


def test_an_abstention_without_a_reason_is_refused():
    with pytest.raises(ValidationError, match="without a gap_reason"):
        CriterionResult(
            criterion_id="c1", verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE
        )


@pytest.mark.parametrize(
    "verdict", [CriterionVerdict.MET, CriterionVerdict.NOT_MET]
)
def test_a_substantiated_verdict_carrying_a_reason_is_refused(verdict):
    with pytest.raises(ValidationError, match="Only an abstention has a gap"):
        CriterionResult(
            criterion_id="c1",
            verdict=verdict,
            spans=[SPAN],
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
        )


def test_an_abstention_carries_a_reason_and_no_spans():
    """The mirror of REQ-5's rule (D44): a result is either evidence or an
    explanation of its absence, never a mix."""
    result = CriterionResult(
        criterion_id="c1",
        verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
        gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
    )
    assert result.spans == []
    with pytest.raises(ValidationError, match="An abstention cites nothing"):
        CriterionResult(
            criterion_id="c1",
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
            spans=[SPAN],
        )


# --------------------------------------------------------------------------
# It reaches the gap list, where Sam reads it
# --------------------------------------------------------------------------


def test_the_gap_list_carries_each_reason():
    """US-5: two gaps with different reasons must read differently, and the
    gap list is the artifact that shows them."""
    results = [
        CriterionResult(criterion_id="a", verdict=CriterionVerdict.MET, spans=[SPAN]),
        CriterionResult(
            criterion_id="c1",
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.NO_EVIDENCE_RETRIEVED,
        ),
        CriterionResult(
            criterion_id="c3",
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=GapReason.UNSUBSTANTIATED_ASSERTION,
        ),
        CriterionResult(
            criterion_id="c2", verdict=CriterionVerdict.NOT_MET, spans=[SPAN]
        ),
    ]
    determination = Determination(
        patient_id="p",
        procedure_code="43644",
        policy_version_id="ncd-100.1-jf-v1",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
        criterion_results=results,
    )
    gaps = {g.criterion_id: g for g in determination.gap_list}
    assert set(gaps) == {"c1", "c2", "c3"}, "the gap list names every non-MET"
    assert gaps["c1"].gap_reason is GapReason.NO_EVIDENCE_RETRIEVED
    assert gaps["c3"].gap_reason is GapReason.UNSUBSTANTIATED_ASSERTION
    assert gaps["c1"].gap_reason != gaps["c3"].gap_reason, (
        "two abstentions with different next actions read identically"
    )
    assert gaps["c2"].gap_reason is None, "a NOT_MET is a failure, not a gap in the record"


def test_the_reason_survives_serialization():
    """The determination is a JSON artifact a human reads (T-25's CLI); a
    reason that only exists in memory is not on the gap list."""
    determination = Determination(
        patient_id="p",
        procedure_code="43644",
        policy_version_id="ncd-100.1-jf-v1",
        outcome=DeterminationOutcome.INSUFFICIENT_EVIDENCE,
        criterion_results=[
            CriterionResult(
                criterion_id="c1",
                verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
                gap_reason=GapReason.SOURCE_CONFLICT,
            )
        ],
    )
    rendered = [g.model_dump(mode="json") for g in determination.gap_list]
    assert rendered[0]["gap_reason"] == "SOURCE_CONFLICT"


# --------------------------------------------------------------------------
# E7, E8 and E10b carry three different values
# --------------------------------------------------------------------------

# The value REQ-31's table assigns each case, and the manifest shape that
# justifies it. The predicates that will emit them are T-16's and T-33's; what
# is asserted here is that the three are distinct and that each case's ground
# truth carries the shape its reason describes (D44).
CASE_REASONS = {
    "E7": GapReason.NO_EVIDENCE_RETRIEVED,
    "E8": GapReason.UNSUBSTANTIATED_ASSERTION,
    "E10b": GapReason.SOURCE_CONFLICT,
}


def test_the_three_cases_carry_three_different_reasons():
    assert len(set(CASE_REASONS.values())) == 3, (
        "E7, E8 and E10b must be distinguishable by reason alone — they send "
        "Sam to three different places"
    )
    results = [
        CriterionResult(
            criterion_id=f"c-{case}",
            verdict=CriterionVerdict.INSUFFICIENT_EVIDENCE,
            gap_reason=reason,
        )
        for case, reason in CASE_REASONS.items()
    ]
    assert len({r.gap_reason for r in results}) == 3


def test_e7s_manifest_carries_nothing_to_retrieve(manifests):
    """`NO_EVIDENCE_RETRIEVED` means nothing was found; E7's chart has no
    weight-management content at all for anything to be found in."""
    manifest = manifests["E7"]
    assert manifest["wm_programs"] == []
    assert manifest["program_assertions"] == []


def test_e8s_manifest_carries_a_claim_with_no_encounter_behind_it(manifests):
    """`UNSUBSTANTIATED_ASSERTION` means a claim was found and no encounter
    stands behind it — which is exactly zero encounters plus one assertion,
    and is why it is not E7's reason (D12)."""
    manifest = manifests["E8"]
    assert manifest["program_assertions"], "no claim to be unsubstantiated"
    assert not any(p["encounters"] for p in manifest["wm_programs"])


def test_e10bs_manifest_carries_two_sources_across_a_threshold(manifests):
    """`SOURCE_CONFLICT` means two sources disagreed across a threshold; the
    next action is to reconcile them, not to go collect anything."""
    manifest = manifests["E10b"]
    note_bmi = manifest["program_assertions"][0]["note_bmi"]
    bundle_record = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
    )
    structured = next(
        r["latest_bmi"] for r in bundle_record["bundles"]
        if r["patient_id"] == manifest["patient_id"]
    )
    assert structured < 35.0 < note_bmi, (
        "the two sources must straddle the threshold, or the conflict is "
        "immaterial and REQ-34 records a discrepancy instead"
    )
