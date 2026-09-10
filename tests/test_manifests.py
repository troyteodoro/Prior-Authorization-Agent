"""T-06 — one manifest per patient, every edge case covered (D42).

Ground truth for notes that do not exist yet. Two kinds of check:

- structural: each case's defining shape actually holds in the manifest that
  claims it — E4's longest run really is three, E9's traps really sit in the
  gap month, E8 really has zero encounters. A manifest claiming a case whose
  shape it does not carry would hand T-07 a note that tests nothing.
- cross-source: where a manifest's story touches the committed bundles
  (criterion a and b facts, reconciliation values), the structured record is
  read through the port and must agree — the manifests follow the population,
  never the other way around (D42).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_DIR = REPO_ROOT / "eval" / "manifests"
POPULATION = REPO_ROOT / "data" / "patients" / "manifest.json"

AS_OF = date(2026, 9, 1)
LOOKBACK_MONTHS = 12  # D40; cross-checked against the tree below
SNOMED_T2DM = "44054006"
VALUE_SET_CODES = {"44054006", "59621000"}

EXPECTED_CASES = {
    "E1", "E2", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E10b", "E10c",
    "E11", "E12",
}
# E3 has no patient — sc1 is a fact about the procedure (D32). E12 has one
# since T-41: the note-free patient whose synthetic observation D73 declares.
DELIBERATELY_ABSENT = {"E3"}


@pytest.fixture(scope="module")
def manifests() -> dict[str, dict]:
    loaded = {}
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        loaded[body["patient_id"]] = body
    return loaded


@pytest.fixture(scope="module")
def population() -> dict[str, dict]:
    records = json.loads(POPULATION.read_text(encoding="utf-8"))["bundles"]
    return {r["patient_id"]: r for r in records}


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


def _by_case(manifests: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for body in manifests.values():
        for case in body["cases"]:
            assert case not in out, f"{case} claimed by two manifests"
            out[case] = body
    return out


def _encounters(manifest: dict, program_id: str | None = None) -> list[dict]:
    return [
        e
        for p in manifest["wm_programs"]
        if program_id is None or p["program_id"] == program_id
        for e in p["encounters"]
    ]


def _months(encounters: list[dict]) -> list[tuple[int, int]]:
    return sorted({
        (int(e["date"][:4]), int(e["date"][5:7])) for e in encounters
    })


def _longest_run(months: list[tuple[int, int]]) -> int:
    longest = run = 0
    previous = None
    for year, month in months:
        index = year * 12 + month
        run = run + 1 if previous is not None and index == previous + 1 else 1
        longest = max(longest, run)
        previous = index
    return longest


def _months_before_as_of(d: date) -> int:
    months = (AS_OF.year - d.year) * 12 + (AS_OF.month - d.month)
    if AS_OF.day < d.day:
        months -= 1
    return months


def _latest_structured_bmi(store, patient_id) -> tuple[float, date]:
    bmis = [o for o in store.get_observations(patient_id) if o.code == "39156-5"]
    latest = max(bmis, key=lambda o: o.effective_date)
    return latest.value, latest.effective_date


# --------------------------------------------------------------------------
# Coverage and shape
# --------------------------------------------------------------------------


def test_one_manifest_per_committed_patient(manifests, population):
    assert set(manifests) == set(population), (
        "manifests and the committed population must cover the same six patients"
    )
    for patient_id, body in manifests.items():
        assert body["bundle"] == population[patient_id]["filename"]
        assert body["as_of"] == AS_OF.isoformat(), (
            "every manifest pins D35's reference date; a floating as_of makes "
            "'recent' depend on when the suite runs"
        )


def test_every_edge_case_is_covered_exactly_once(manifests):
    claimed = set(_by_case(manifests))
    assert claimed == EXPECTED_CASES, (
        f"missing: {sorted(EXPECTED_CASES - claimed)}, "
        f"unexpected: {sorted(claimed - EXPECTED_CASES)}. "
        f"{sorted(DELIBERATELY_ABSENT)} are absent by decision (D42)."
    )


def test_every_date_is_on_or_before_as_of(manifests):
    for body in manifests.values():
        dates = [e["date"] for e in _encounters(body)]
        dates += [t["date"] for t in body["traps"]]
        dates += [a["date"] for a in body["program_assertions"]]
        assert all(d <= body["as_of"] for d in dates), body["patient_id"]


def test_the_lookback_constant_still_matches_the_tree():
    """The manifests' recency arithmetic assumes D40's 12; if T-33 or a
    revision moves it, these shapes need re-deriving, loudly."""
    tree = json.loads(
        (REPO_ROOT / "data" / "policies" / "ncd_100_1_jf.json").read_text()
    )
    a = next(c for c in tree["criteria"] if c["id"] == "a")
    assert a["constants"]["lookback_months"]["value"] == LOOKBACK_MONTHS


# --------------------------------------------------------------------------
# The cases, each verified in the manifest that claims it
# --------------------------------------------------------------------------


def test_e1_is_a_clean_approval_and_only_this_patient_could_carry_it(
    manifests, store
):
    body = _by_case(manifests)["E1"]
    programs = [
        p for p in body["wm_programs"]
        if _longest_run(_months(p["encounters"])) >= 4
    ]
    assert len(programs) == 1, "exactly one qualifying program (E11 shares this chart)"
    qualifying = programs[0]["encounters"]
    assert all(
        e["weight_documented"] and e["bmi_documented"]
        and e["diet_documented"] and e["activity_documented"]
        for e in qualifying
    ), "E1's run is fully documented in every month (c4, c5)"
    last = date.fromisoformat(max(e["date"] for e in qualifying))
    assert _months_before_as_of(last) < 12, "the run ends inside c2's window"

    value, when = _latest_structured_bmi(store, body["patient_id"])
    assert value >= 35.0 and _months_before_as_of(when) < LOOKBACK_MONTHS
    active = {
        c.code for c in store.get_conditions(body["patient_id"])
        if c.clinical_status == "active"
    }
    assert active & VALUE_SET_CODES, "criterion (b) needs an active comorbidity"


def test_e11_has_two_programs_and_one_qualifies(manifests):
    body = _by_case(manifests)["E11"]
    assert len(body["wm_programs"]) >= 2
    runs = [_longest_run(_months(p["encounters"])) for p in body["wm_programs"]]
    assert sum(1 for r in runs if r >= 4) == 1, "exactly one qualifying run"


def _latest_note_bmi(manifest: dict) -> float:
    """The note BMI reconciliation would compare against: the most recent one
    the chart documents, by date — not by position in the file."""
    documented = [e for e in _encounters(manifest) if "note_bmi" in e]
    assert documented, "no note BMI anywhere in this manifest"
    return max(documented, key=lambda e: e["date"])["note_bmi"]


def test_e10c_stays_below_any_sane_tolerance(manifests, store):
    body = _by_case(manifests)["E10c"]
    structured, _ = _latest_structured_bmi(store, body["patient_id"])
    recent = _latest_note_bmi(body)
    assert abs(structured - recent) <= 0.1
    assert (structured >= 35.0) == (recent >= 35.0), "same side of the threshold"


def test_e2_matches_the_structured_exclusion_facts(manifests, store):
    body = _by_case(manifests)["E2"]
    value, _ = _latest_structured_bmi(store, body["patient_id"])
    assert value < 35.0
    active = {
        c.code for c in store.get_conditions(body["patient_id"])
        if c.clinical_status == "active"
    }
    assert SNOMED_T2DM in active


def test_e7_has_no_weight_management_documentation_at_all(manifests):
    body = _by_case(manifests)["E7"]
    assert body["wm_programs"] == [] and body["program_assertions"] == []
    assert all(t["type"] == "unrelated_section_date" for t in body["traps"]), (
        "E7's chart is empty of WM content; its traps are unrelated dates that "
        "make extraction earn the zero"
    )


def test_e4_longest_run_is_exactly_three(manifests):
    body = _by_case(manifests)["E4"]
    assert _longest_run(_months(_encounters(body))) == 3


def test_e9_traps_sit_inside_the_gap_month(manifests):
    body = _by_case(manifests)["E9"]
    encounter_months = set(_months(_encounters(body)))
    missed = [t for t in body["traps"] if t["type"] == "missed_visit"]
    assert missed, "E9 requires missed-visit dates"
    for trap in missed + [
        t for t in body["traps"] if t["type"] == "unsuccessful_contact"
    ]:
        trap_month = (int(trap["date"][:4]), int(trap["date"][5:7]))
        assert trap_month not in encounter_months, "the trap is in a visit month"
        year, month = trap_month
        neighbours = {
            (year, month - 1) if month > 1 else (year - 1, 12),
            (year, month + 1) if month < 12 else (year + 1, 1),
        }
        assert neighbours <= encounter_months, (
            "the trap month must be the gap between two encounter months — "
            "counting it as a visit would wrongly bridge the run"
        )


def test_e5_qualifying_run_ended_outside_the_recency_window(manifests):
    body = _by_case(manifests)["E5"]
    encounters = _encounters(body)
    assert _longest_run(_months(encounters)) >= 4, "c3 holds; the case is c2's"
    assert all(
        e["weight_documented"] and e["bmi_documented"]
        and e["diet_documented"] and e["activity_documented"]
        for e in encounters
    )
    last = date.fromisoformat(max(e["date"] for e in encounters))
    months_ago = _months_before_as_of(last)
    assert months_ago >= 13, f"ended {months_ago} months ago; E5 says fourteen"


def test_e6_weight_every_month_bmi_in_two(manifests):
    body = _by_case(manifests)["E6"]
    encounters = _encounters(body)
    assert _longest_run(_months(encounters)) >= 4
    assert all(e["weight_documented"] for e in encounters)
    assert sum(1 for e in encounters if e["bmi_documented"]) == 2
    assert all(e["diet_documented"] and e["activity_documented"] for e in encounters), (
        "c5 holds so the case isolates c4"
    )


def test_e10_disagrees_beyond_tolerance_on_the_same_side(manifests, store):
    body = _by_case(manifests)["E10"]
    structured, _ = _latest_structured_bmi(store, body["patient_id"])
    recent = _latest_note_bmi(body)
    assert abs(structured - recent) >= 1.0
    assert (structured >= 35.0) == (recent >= 35.0)


def test_e8_asserts_completion_with_no_visits_behind_it(manifests):
    body = _by_case(manifests)["E8"]
    assert _encounters(body) == [], "zero wm_events is the whole point"
    assert body["program_assertions"], "and one assertion span is the other half"


def test_e12_latest_structured_bmi_is_exactly_35_in_window(manifests, store):
    """The boundary case, boundary inclusive: criterion (a) reads structured
    data, so the case holds iff the *served* most-recent BMI is exactly 35.0
    inside the lookback — and that observation is the synthetic one the
    population manifest declares (T-41, D73), not a coincidence."""
    body = _by_case(manifests)["E12"]
    value, when = _latest_structured_bmi(store, body["patient_id"])
    assert value == 35.0, "E12 is the inclusive boundary, exactly"
    assert 0 <= _months_before_as_of(when) < LOOKBACK_MONTHS

    declared = json.loads(POPULATION.read_text(encoding="utf-8"))[
        "synthetic_observations"
    ]
    assert len(declared) == 1, "exactly one synthetic observation (D73)"
    s = declared[0]
    assert s["patient_id"] == body["patient_id"]
    assert s["value"] == value
    assert date.fromisoformat(s["effective_date"][:10]) == when, (
        "the served latest observation must be the declared synthetic one"
    )
    # E2's shape must not bleed in: the boundary patient carries no active
    # T2DM, so nothing about E12 can trip sc2's national exclusion.
    active = {
        c.code for c in store.get_conditions(body["patient_id"])
        if c.clinical_status == "active"
    }
    assert SNOMED_T2DM not in active


def test_a_note_free_manifest_declares_no_note_dependent_facts(manifests):
    """`"note": false` (D73) is a claim about the whole manifest: a chart with
    no note cannot carry encounters, traps, or assertions, because every one
    of those is a fact extraction would need a note to find."""
    note_free = [m for m in manifests.values() if m.get("note") is False]
    assert note_free, "E12's manifest declares itself note-free"
    for body in note_free:
        assert body["wm_programs"] == []
        assert body["traps"] == []
        assert body["program_assertions"] == []


def test_e10b_crosses_the_threshold(manifests, store):
    body = _by_case(manifests)["E10b"]
    structured, _ = _latest_structured_bmi(store, body["patient_id"])
    note_bmi = body["program_assertions"][0]["note_bmi"]
    assert structured < 35.0 < note_bmi, (
        "the disagreement must cross 35.0 for SOURCE_CONFLICT to be the label"
    )
