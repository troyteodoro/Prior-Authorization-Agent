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
    "E11", "E12", "E13", "J1", "RA1", "RA2", "RA3",
    "US1", "US2", "US3", "US4",
}
# E3 has no patient — sc1 is a fact about the procedure (D32). E12 has one
# since T-41: the note-free patient whose synthetic observation D73 declares.
# J1 is the second jurisdiction (T-88, D102): a declared clone of E4's chart.
# E13 (T-81, D104) is E1's chart with its qualifying run split across two
# documents. RA1-RA3 are the second practice (T-93, D113): two Synthea charts
# in Palmetto's territory and a declared clone of one of them, all note-free,
# because v1.2 declares every note-only criterion unclaimed. US1-US4 are the
# third practice (T-94, D114): one Synthea chart in WPS's territory and two
# declared clones of it that differ by the date of one re-coded procedure,
# also note-free, and US3 and US4 are two rows over the one chart — the
# frequency criterion's abstention and an unlisted code.
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
        "manifests and the committed population must cover the same eight patients"
    )
    for patient_id, body in manifests.items():
        assert body["bundle"] == population[patient_id]["filename"]
        assert body["as_of"] == AS_OF.isoformat(), (
            "every manifest pins D35's reference date; a floating as_of makes "
            "'recent' depend on when the suite runs"
        )


def test_a_declared_clone_carries_its_source_facts_unchanged(manifests):
    """T-88 (D102), generalized by T-93 (D113). The clone's manifest copies its
    source's facts rather than pointing at them, so every reader stays simple —
    and this is what keeps the copy honest. Programs, traps and assertions must
    be equal, and so must whether the chart has a note at all; `cases` and
    `rationale` are the clone's own; and the manifests declare exactly the
    clones the population does, no more and no fewer.

    Two clones now, of different kinds: one chart re-addressed into another
    jurisdiction, one carrying a declared prescription. Neither edits a fact
    this file grades, which is why they are checked by the same rule — what
    differs between a clone and its source is declared in the population
    manifest and verified there, by re-deriving the bytes."""
    clones = {pid: body for pid, body in manifests.items() if body.get("cloned_from")}
    population = json.loads(POPULATION.read_text(encoding="utf-8"))
    declared = population["synthetic_patients"]
    assert sorted(clones) == sorted(d["patient_id"] for d in declared), (
        "the manifests and the population must declare the same clones"
    )
    by_patient = {d["patient_id"]: d for d in declared}
    claimed_cases = set()
    for patient_id, body in clones.items():
        source = manifests[body["cloned_from"]]
        assert source.get("cloned_from") is None, "a clone of a clone is not declared"
        for field in ("wm_programs", "traps", "program_assertions", "as_of"):
            assert body[field] == source[field], f"{field} drifted from the source's"
        assert body.get("note") == source.get("note"), (
            f"{patient_id}: a clone is note-free exactly when its source is"
        )
        assert body.get("documents") == source.get("documents")
        assert not set(body["cases"]) & set(source["cases"]), (
            f"{patient_id}: a clone and its source answer different rows"
        )
        assert not set(body["cases"]) & claimed_cases
        claimed_cases |= set(body["cases"])
        assert body["bundle"] != source["bundle"]
        assert by_patient[patient_id]["cloned_from"] == body["cloned_from"]


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


# --------------------------------------------------------------------------
# Two documents per chart (T-81, D104)
# --------------------------------------------------------------------------


def _facts(manifest: dict) -> list[dict]:
    return (
        _encounters(manifest) + manifest["traps"] + manifest["program_assertions"]
    )


def _note_bearing(manifests: dict[str, dict]) -> list[dict]:
    return [m for m in manifests.values() if m.get("note") is not False]


def test_every_note_bearing_manifest_declares_at_least_two_documents(manifests):
    """One document per chart is the corpus D91 measured recall against and
    found it could not fall; two is what T-81 exists to provide."""
    for body in _note_bearing(manifests):
        documents = body["documents"]
        assert len(documents) >= 2, body["patient_id"]
        assert len(set(documents)) == len(documents), "duplicate basename"
        assert all("/" not in d for d in documents), "a basename, not a path"


def test_every_declared_fact_is_assigned_to_a_declared_document(manifests):
    """A scalar `document` per fact makes 'exactly one document' structural;
    this makes it 'a declared one', so a misspelling cannot leave a fact
    unrendered."""
    for body in _note_bearing(manifests):
        for fact in _facts(body):
            assert fact.get("document") in body["documents"], (
                f"{body['patient_id']}: {fact['date']} is assigned to "
                f"{fact.get('document')!r}, not a declared document"
            )


def test_every_declared_document_carries_at_least_one_fact(manifests):
    for body in _note_bearing(manifests):
        carrying = {f["document"] for f in _facts(body)}
        assert carrying == set(body["documents"]), (
            f"{body['patient_id']}: {sorted(set(body['documents']) - carrying)} "
            "would render as a header and an assessment with nothing between"
        )


def test_the_longest_run_straddles_documents_wherever_a_run_exists(manifests):
    """REQ-25's mechanism: a skipped document must be able to shorten a run.
    A split that leaves the whole run in one file leaves the direct recall
    figure unable to fall for that patient."""
    checked = 0
    for body in _note_bearing(manifests):
        for program in body["wm_programs"]:
            months = _months(program["encounters"])
            if _longest_run(months) < 2:
                continue
            # The encounters of the longest consecutive run, by month.
            best: list[tuple[int, int]] = []
            run: list[tuple[int, int]] = []
            previous = None
            for year, month in months:
                index = year * 12 + month
                run = run + [(year, month)] if previous is not None and index == previous + 1 else [(year, month)]
                if len(run) > len(best):
                    best = run
                previous = index
            in_run = [
                e for e in program["encounters"]
                if (int(e["date"][:4]), int(e["date"][5:7])) in best
            ]
            spread = {e["document"] for e in in_run}
            if len(best) >= 4 or len(in_run) >= 3:
                assert len(spread) >= 2, (
                    f"{body['patient_id']} {program['program_id']}: the run "
                    f"{best[0]}..{best[-1]} sits entirely in {spread}"
                )
                checked += 1
    assert checked >= 4, "E1, E4, E5 and E6 each carry a run that must straddle"


def test_the_no_evidence_charts_extra_document_is_a_pure_distractor(manifests):
    """E7 and E8 cite nothing today (D91), and their second document must
    not change that: beyond the document carrying an assertion, only traps."""
    by_case = _by_case(manifests)
    for case in ("E7", "E8"):
        body = by_case[case]
        carrying = {a["document"] for a in body["program_assertions"]}
        for document in body["documents"]:
            if document in carrying:
                continue
            facts = [f for f in _facts(body) if f["document"] == document]
            assert facts, f"{case}: {document} carries nothing"
            assert all("type" in f for f in facts), (
                f"{case}: {document} carries an encounter or an assertion"
            )


def test_e13_is_e1s_run_split_across_two_documents(manifests):
    body = _by_case(manifests)["E13"]
    assert "E1" in body["cases"], "E13 shares E1's chart and determination"
    qualifying = next(
        p for p in body["wm_programs"]
        if _longest_run(_months(p["encounters"])) >= 4
    )
    assert {e["document"] for e in qualifying["encounters"]} == set(body["documents"]), (
        "c3's cited spans must name both documents"
    )


def test_a_note_free_manifest_declares_no_documents(manifests):
    for body in manifests.values():
        if body.get("note") is False:
            assert "documents" not in body, (
                f"{body['patient_id']}: note-free (D73) yet declares documents"
            )
