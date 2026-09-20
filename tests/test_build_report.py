"""T-22 / T-28 — the metrics report's arithmetic and its `--verify` gate (D85).

The report is the document A2, A3, A5 and A6 are satisfied by. It is generated,
never hand-edited, and `--verify` is what makes that enforceable rather than
aspirational — so the first thing asserted here is that `--verify` actually
fails on a hand-edit. A verifier that cannot fail is a gate that asserts nothing.

The section computations are exercised against synthetic inputs where the right
answer is known by construction, because a test that only re-runs the generator
and compares to the generator's own output would pass on any arithmetic at all.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "eval" / "build_report.py"
REPORT = REPO_ROOT / "eval" / "report.md"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("build_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_report"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


def test_the_committed_report_matches_its_sources(script):
    """The gate, run the way `check_gates.py` runs it."""
    assert script.main(["--verify"]) == script.EXIT_OK


def test_verify_fails_on_a_hand_edited_figure(script, tmp_path, capsys):
    """The assertion the whole design rests on. A `--verify` that cannot fail
    turns `report.md` into a document that merely looks authoritative."""
    tampered = tmp_path / "report.md"
    tampered.write_text(
        REPORT.read_text(encoding="utf-8").replace("**1.000**", "**0.950**", 1),
        encoding="utf-8",
    )
    assert script.main(["--verify", "--out", str(tampered)]) == script.EXIT_DRIFT
    out = capsys.readouterr().out
    assert "DRIFT" in out and "hand-edited" in out


def test_verify_fails_when_the_report_is_missing(script, tmp_path, capsys):
    """A missing report is a broken checkout, not a passing gate — the D31
    shape: never answer a question you could not evaluate."""
    missing = tmp_path / "nothing.md"
    assert script.main(["--verify", "--out", str(missing)]) == script.EXIT_BROKEN


def test_the_report_is_reproducible(script):
    """An exact diff is only safe because every metric is replayed rather than
    measured live (D85). If this ever fails, `--verify` must move behind a
    recording — not be relaxed into an approximate comparison."""
    assert script.render() == script.render()


# --------------------------------------------------------------------------
# The sweep varies the constant without touching the corpus
# --------------------------------------------------------------------------


TREE_PATH = REPO_ROOT / "data" / "policies" / "ncd_100_1_jf.json"


def test_the_sweep_never_writes_the_policy_file(script):
    """D85 refused the patch-and-restore implementation: a gate that writes a
    tracked policy file leaves the corpus wrong if it is interrupted, and
    `verify_sources.py` is downstream of exactly that file."""
    before = TREE_PATH.read_bytes()
    script._sweep_section()
    assert TREE_PATH.read_bytes() == before


def test_the_override_changes_only_the_tolerance(script):
    from pa_agent.stores.policy import LocalPolicyStore

    plain = LocalPolicyStore().get_tree("ncd-100.1-jf-v1")
    wrapped = script._ToleranceOverride(LocalPolicyStore(), 7.5).get_tree(
        "ncd-100.1-jf-v1"
    )
    assert [f.tolerance() for f in wrapped.reconciled_facts] == [
        7.5 for _ in plain.reconciled_facts
    ]
    assert wrapped.model_copy(update={"reconciled_facts": plain.reconciled_facts}) == plain


def test_the_sweep_actually_moves_the_disclosure_count(script):
    """A sweep whose column is constant is a table nobody can learn from. The
    flat column in this report is the *abstention* one, deliberately (D82); the
    disclosure column has to move or the sweep measures nothing."""
    rows = [line for line in script._sweep_section() if line.startswith("| 0 |")
            or line.startswith("| 50 |")]
    assert len(rows) == 2
    assert rows[0] != rows[1], "the tolerance grid produces one disclosure count"


def test_the_abstention_column_is_reported_at_every_grid_point(script):
    """D82's point: "no constant moves abstention" is a measurement a future
    change can falsify, not a sentence someone must remember to re-check."""
    rows = [
        line
        for line in script._sweep_section()
        if line.startswith("| ") and line.count("|") == 4 and "tolerance" not in line
    ]
    assert len(rows) == len(script.TOLERANCE_GRID)
    for row in rows:
        assert row.rstrip("|").split("|")[-1].strip() not in ("", "n/a")


# --------------------------------------------------------------------------
# Arithmetic, against inputs whose answers are known by construction
# --------------------------------------------------------------------------


def _pairs(*rows):
    """(case, criterion, expected verdict, actual verdict)."""
    return list(rows)


def test_precision_counts_only_met_calls(script, monkeypatch):
    """Precision on `MET` is over what the system *said* `MET`, not over every
    labeled pair. A denominator of all pairs would make a cautious system look
    precise for abstaining."""
    pairs = _pairs(
        ("X1", "a", "MET", "MET"),          # true positive
        ("X2", "a", "NOT_MET", "MET"),      # false positive
        ("X3", "a", "MET", "NOT_MET"),      # miss — not a precision error
        ("X4", "a", "MET", "INSUFFICIENT_EVIDENCE"),  # abstention — likewise
    )
    monkeypatch.setattr(script, "_determinations", lambda cache: [])
    text = "\n".join(script._precision_section([], {}, pairs))
    assert "| `a` | 4 | 2 | 1 | 0.500 |" in text
    assert "**0.500**" in text


def test_the_base_rate_is_the_always_met_baselines_precision(script, monkeypatch):
    """A2's comparison. Asserted on a set where the two are *not* 1.000, so a
    generator that printed the measured figure twice would fail."""
    pairs = _pairs(
        ("X1", "a", "MET", "MET"),
        ("X2", "a", "MET", "MET"),
        ("X3", "a", "NOT_MET", "NOT_MET"),
        ("X4", "a", "NOT_MET", "NOT_MET"),
    )
    monkeypatch.setattr(script, "_determinations", lambda cache: [])
    text = "\n".join(script._precision_section([], {}, pairs))
    assert "2/4 = **0.500**" in text, "the base rate is labeled MET over labeled pairs"
    assert "| Precision of a trivial always-`MET` baseline | **0.500** |" in text
    assert "| Precision measured | **1.000** |" in text


def test_the_base_rate_reads_the_labels_not_the_systems_own_output(script, monkeypatch):
    """The circularity T-28's mutation list names, and it survived the first
    mutation pass.

    A base rate counted from the system's *actual* verdicts measures the system
    against itself, and A2's whole point is a denominator the system cannot
    move. On the committed corpus the two readings coincide — every labeled
    `MET` is answered `MET` — so neither `--verify` nor any real case can tell
    them apart. This set is deliberately asymmetric: three labeled `MET`, two
    answered `MET`.
    """
    pairs = _pairs(
        ("X1", "a", "MET", "MET"),
        ("X2", "a", "NOT_MET", "MET"),
        ("X3", "a", "MET", "NOT_MET"),
        ("X4", "a", "MET", "INSUFFICIENT_EVIDENCE"),
    )
    monkeypatch.setattr(script, "_determinations", lambda cache: [])
    text = "\n".join(script._precision_section([], {}, pairs))
    assert "3/4 = **0.750**" in text, (
        "the base rate must count labeled `MET` (3 of 4). Counting what the "
        "system answered `MET` gives 2 of 4 and grades the system against its "
        "own output (D42, A2)"
    )
    assert "2/4" not in text


def test_precision_is_na_rather_than_one_when_nothing_was_claimed(script, monkeypatch):
    """A criterion the system never called `MET` has no precision. Reporting
    1.000 for it would credit silence as accuracy."""
    pairs = _pairs(("X1", "c2", "NOT_MET", "NOT_MET"))
    monkeypatch.setattr(script, "_determinations", lambda cache: [])
    text = "\n".join(script._precision_section([], {}, pairs))
    assert "| `c2` | 1 | 0 | 0 | n/a |" in text


def test_only_labeled_pairs_are_scored(script):
    """D85: the system emits more verdicts than the set labels, and scoring the
    rest would need a ground truth that does not exist."""
    cases = json.loads((REPO_ROOT / "eval" / "cases.json").read_text())["cases"]
    labeled = sum(len((c.get("expect") or {}).get("criteria") or {}) for c in cases)
    results, cache = script._run(
        __import__("pa_agent.stores.policy", fromlist=["LocalPolicyStore"]).LocalPolicyStore()
    )
    pairs = script._criterion_pairs(results, cache)
    emitted = sum(len(d.criterion_results) for d in script._determinations(cache))
    assert len(pairs) == labeled < emitted


# --------------------------------------------------------------------------
# The caveats are part of the deliverable, not decoration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "first draft, drafted alongside the system it grades",  # D19/D42/D96
        "as one contractor would",                               # D21/D29
        "eight patients",                                        # corpus size (D102)
        "declared clone",                                        # J1's provenance
    ],
)
def test_the_report_carries_its_caveats(claim):
    """A figure whose caveat lives in another document is a figure quoted
    without it. These are asserted on the committed file, so deleting one is a
    red gate rather than a quiet omission."""
    text = REPORT.read_text(encoding="utf-8")
    assert claim.replace("\n", " ") in " ".join(text.split()), (
        f"eval/report.md no longer states: {claim!r}"
    )


def test_the_report_says_it_is_generated():
    assert "do not edit by hand" in REPORT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Planner recall (T-27, T-80, REQ-25, D86, D91)
# --------------------------------------------------------------------------


def _stubbed_recall(script, tmp_path, monkeypatch, mutate):
    """Run `_recall_section` against a recording `mutate` has edited.

    The live corpus cannot produce a miss on either figure — one note per
    patient, and `AgenticRetrievalPlanner` raises rather than returning less
    (D91) — so the only way to show the section can see one is to build it.
    """
    recording = json.loads(
        (REPO_ROOT / "eval" / "agentic" / "results.json").read_text(encoding="utf-8")
    )
    mutate(recording)

    stub = tmp_path / "agentic"
    stub.mkdir()
    (stub / "results.json").write_text(json.dumps(recording), encoding="utf-8")
    monkeypatch.setattr(script, "EVAL_DIR", tmp_path)
    # `cases.json` is read from EVAL_DIR too, so link the real one through.
    (tmp_path / "cases.json").write_text(
        (REPO_ROOT / "eval" / "cases.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    from pa_agent.stores.policy import LocalPolicyStore

    _results, cache = script._run(LocalPolicyStore())
    text = "\n".join(script._recall_section(cache))
    return text.split("| **all**")[1].split("\n")[0]


def test_a_skipped_document_resolves_below_one_on_the_cited_figure(
    script, tmp_path, monkeypatch
):
    """T-27's exit condition, and the reason the section exists at all.

    A recall figure that reads 1.000 whatever the planner did is the figure D70
    already threw out once. This drops one document from one patient's cited
    side and requires the number to move — if it does not, the section is
    measuring the oracle against itself.
    """

    def mutate(recording):
        # **Partial**, not total. Emptying the list is caught by any reading of
        # "contains", including a wrong one — an intersection test would fail it
        # too and survive undetected. Dropping one of two documents separates
        # them: containment fails, intersection still succeeds.
        victim = next(
            row
            for row in recording["patients"]
            if len(row["agentic"]["document_ids"]) > 1
        )
        kept = victim["agentic"]["document_ids"][:-1]
        assert kept, "the fixture patient would cite nothing after the drop"
        victim["agentic"]["document_ids"] = kept

    overall = _stubbed_recall(script, tmp_path, monkeypatch, mutate)
    cited = overall.rsplit("|", 2)[1]
    assert "1.000" not in cited, (
        f"cited recall stayed at 1.000 ({overall.strip()}) after one of a "
        "patient's two cited documents was dropped. The section cannot see a "
        "skipped document, which is the whole of T-27's exit — and a recall "
        "figure that is 1.000 by construction is what D70 already threw out."
    )


def test_a_skipped_document_resolves_below_one_on_the_direct_figure(
    script, tmp_path, monkeypatch
):
    """T-80's half of the same guard (D91).

    The direct figure cannot fall on this corpus, so nothing a real run does
    can exercise the comparison behind it. Without this, `_recall_section`
    could compute the direct column as a constant and every gate would agree.
    """

    def mutate(recording):
        victim = next(
            row
            for row in recording["patients"]
            if len(row["agentic"]["gathered"]["document_ids"]) > 1
        )
        kept = victim["agentic"]["gathered"]["document_ids"][:-1]
        assert kept, "the fixture patient would have gathered nothing after the drop"
        victim["agentic"]["gathered"]["document_ids"] = kept

    overall = _stubbed_recall(script, tmp_path, monkeypatch, mutate)
    direct = overall.rsplit("|", 3)[1]
    assert "1.000" not in direct, (
        f"direct recall stayed at 1.000 ({overall.strip()}) after one of a "
        "patient's two gathered documents was dropped. The column is then a "
        "constant, not a measurement — D70's figure with T-80's label on it."
    )


def test_the_two_figures_are_not_the_same_computation(script, tmp_path, monkeypatch):
    """They agree on this corpus, which is exactly why they could silently be
    one column printed twice. Dropping a document from `gathered` alone must
    move the direct figure and leave the cited one at 1.000."""

    def mutate(recording):
        victim = next(
            row
            for row in recording["patients"]
            if len(row["agentic"]["gathered"]["document_ids"]) > 1
        )
        victim["agentic"]["gathered"]["document_ids"] = victim["agentic"][
            "gathered"
        ]["document_ids"][:-1]

    overall = _stubbed_recall(script, tmp_path, monkeypatch, mutate)
    direct, cited = overall.rsplit("|", 3)[1], overall.rsplit("|", 2)[1]
    assert "1.000" not in direct and "1.000" in cited, (
        f"the two columns moved together ({overall.strip()}) when only the "
        "gathered set was touched. One of them is reading the other's data."
    )


def test_recall_reads_the_agentic_side_not_the_oracles(script):
    """The circularity guard. Comparing the oracle's cited documents against
    themselves is 1.000 by construction — exactly what D70 rejected — and on
    this corpus the two sides happen to be identical, so only a deliberate
    substitution can tell the two implementations apart."""
    source = SCRIPT.read_text(encoding="utf-8")
    recall = source[source.index("def _recall_section") : source.index("def _cost_section")]
    assert 'row["agentic"]["document_ids"]' in recall
    assert 'row["agentic"]["gathered"]["document_ids"]' in recall
    assert 'row["oracle"]' not in recall, (
        "the recall section reads the oracle's own recorded documents; that "
        "figure is 1.000 by construction (D70, D86). The oracle side of the "
        "denominator is re-derived in-process from the cache, not read back "
        "out of the recording it is supposed to be compared against."
    )


def test_recall_requires_every_cited_document_not_merely_one():
    """Pinned by parsing, because no behaviour can distinguish the two today.

    `wanted <= gathered` and `bool(wanted & gathered)` differ only when a
    criterion cites two or more documents, and none currently does — criterion
    (a) cites the bundle, the c-criteria cite the note, one document each. The
    weaker reading is therefore an *equivalent* mutant on this corpus rather
    than a surviving one, and it stops being equivalent the moment a criterion
    cites two sources. That is a real possibility — reconciliation already
    reads two — so the stricter semantics is pinned here rather than left to a
    future chart to discover. Same move as D65 and D67: when a behavioural test
    cannot catch a mutation, parse instead.

    Both columns are pinned, not just one. They are the same predicate over
    different sets, and a weakened direct column is the one no corpus-driven
    test could ever reach (D91).
    """
    source = SCRIPT.read_text(encoding="utf-8")
    recall = source[source.index("def _recall_section") : source.index("def _cost_section")]
    assert "wanted <= gathered" in recall, (
        "the direct figure must require *every* document the oracle cited to "
        "have been gathered; an intersection test passes a run that found one "
        "of two sources"
    )
    assert "wanted <= cited" in recall, (
        "the cited figure must require *every* document the oracle cited to be "
        "present; an intersection test passes a run that found one of two"
    )


def test_the_recall_section_names_what_the_direct_figure_cannot_do():
    """D91, and D85's rule about where a caveat lives.

    The direct figure is 1.000 by construction on this corpus. A reader who
    quotes it without that sentence is quoting something else, and a caveat
    that lives in a decisions entry is a caveat that does not travel.
    """
    text = REPORT.read_text(encoding="utf-8")
    assert "1.000 by construction on this corpus" in text
    assert "one note per patient" in text
    assert "T-81" in text, (
        "the report reports a figure that cannot fall without naming the task "
        "that would let it"
    )
    assert "D4's reversal condition now reads against a number" in text


def test_the_recall_section_reports_both_figures():
    """Reporting the direct figure alone replaces an informative number with an
    uninformative one, which is the rewrite D91 made to T-80's exit."""
    text = REPORT.read_text(encoding="utf-8")
    # Scoped to the recall section: the precision table also opens `| Criterion |`.
    section = text[text.index("## Planner recall") : text.index("## Cost and latency")]
    header = next(
        line for line in section.splitlines() if line.startswith("| Criterion |")
    )
    assert "Recall (direct)" in header and "Recall (cited)" in header


# --------------------------------------------------------------------------
# Anchoring (T-85, P2, D18, D88, D98)
# --------------------------------------------------------------------------


def _recording(*notes, task="T-63", runner="adk", tool_fetch=False):
    """A synthetic extraction recording in the shape the scorer writes."""
    payload = {"task": task, "notes": list(notes)}
    if runner:
        payload["runner"] = runner
        payload["tool_fetch"] = tool_fetch
    return payload


def _note(note_id, emitted, anchored, *, dropped=(), required=False, assertions=0, reask=None, raw=None):
    note = {
        "note_id": note_id,
        "score": {
            "spans_emitted": emitted,
            "spans_anchored": anchored,
            "dropped": list(dropped),
            "assertion_required": required,
            "assertions": assertions,
        },
    }
    if reask is not None:
        note["reask"] = reask
    if raw is not None:
        note["raw"] = raw
    return note


def test_anchoring_reports_the_reask_per_recording_from_the_note_blocks(script):
    """T-89's columns, recomputed from each note's `reask` block — never from
    the stored aggregate (T-71) — and the recovered quote named beside its
    note, read from the patched payload at the audited path."""
    rows = script._anchoring_rows(
        {
            "x.json": _recording(
                _note("plain", 4, 4),
                _note(
                    "asked", 3, 3,
                    reask={
                        "targets": [
                            {"path": "program_assertions[0].quote", "reason": "assertion_quote_unanchorable", "quote": "completed a"},
                            {"path": "wm_events[0].bmi_quote", "reason": "bmi_quote_unanchorable", "quote": "BMI 9"},
                        ],
                        "answers": [], "recovered": ["program_assertions[0].quote"],
                        "unrecovered": ["wm_events[0].bmi_quote"], "error": None,
                    },
                    raw={"wm_events": [{"bmi_quote": "BMI 9"}],
                         "program_assertions": [{"quote": "completing a\nsix-month program"}]},
                ),
            ),
            "y.json": _recording(_note("old", 2, 2)),
        }
    )
    by_file = {row["file"]: row for row in rows}
    assert by_file["x.json"]["reask_notes"] == 1
    assert by_file["x.json"]["reask_targets"] == 2
    assert by_file["x.json"]["reask_recovered"] == 1
    assert by_file["x.json"]["recovered"] == [
        {"note_id": "asked", "path": "program_assertions[0].quote", "quote": "completing a six-month program"}
    ]
    assert (by_file["y.json"]["reask_targets"], by_file["y.json"]["reask_recovered"]) == (0, 0)
    assert by_file["y.json"]["recovered"] == []


def test_anchoring_reads_every_committed_recording(script):
    """The figures the section exists to carry, pinned to the recordings as
    committed: T-15 and T-63 inline anchored everything; T-63 tool-fetch lost
    one assertion quote on E8 (D71, D88, D98)."""
    text = "\n".join(script._anchoring_section())
    assert "| T-15 direct (`results.json`) | 11 | 171 | 171 | **0** | 2/2 = **1.000** |" in text
    assert (
        "| T-63 ADK inline (`adk_results_inline.json`) | 11 | 165 | 165 | **0** "
        "| 2/2 = **1.000** |"
    ) in text
    assert (
        "| T-63 ADK tool-fetch (`adk_results_tool_fetch.json`) | 6 (5 skipped) "
        "| 76 | 75 | **1** | 0/1 = **0.000** |"
    ) in text
    assert "note `E8+E10b`: `assertion_quote_unanchorable`" in text
    assert "completed a six-month medically supervised" in text


def test_anchoring_is_in_the_committed_report():
    text = REPORT.read_text(encoding="utf-8")
    assert "## Anchoring (Article III, D18, D88)" in text
    assert "assertion_quote_unanchorable" in text


def test_anchoring_names_a_dropped_claim_beside_its_note(script):
    """The per-note record always showed the drop; the point of the section
    is that the *report* names it. A synthetic drop must surface with its
    note id, its reason, and the quote flattened to one line."""
    rows = script._anchoring_rows(
        {
            "x.json": _recording(
                _note("clean", 4, 4),
                _note(
                    "lossy",
                    5,
                    4,
                    dropped=[{"reason": "event_quote_unanchorable", "quote": "BMI\n 41.2"}],
                ),
            )
        }
    )
    (row,) = rows
    assert row["spans_emitted"] == 9 and row["spans_anchored"] == 8
    assert row["spans_not_anchored"] == 1
    assert row["dropped"] == [
        {"note_id": "lossy", "reason": "event_quote_unanchorable", "quote": "BMI 41.2"}
    ]


def test_anchoring_excludes_a_skipped_note_rather_than_counting_it_as_zero(script):
    """The tool-fetch mode has no address for the spike notes (T-67), so those
    records carry no `score`. They are absent from every figure, not present
    as a note that emitted and anchored nothing — the D31 shape."""
    rows = script._anchoring_rows(
        {
            "x.json": _recording(
                _note("scored", 3, 3),
                {"note_id": "spike", "skipped": "no address on the patient plane"},
            )
        }
    )
    (row,) = rows
    assert row["notes_scored"] == 1 and row["notes_skipped"] == 1
    assert row["spans_emitted"] == 3


def test_anchoring_assertion_coverage_follows_d88(script):
    """Denominator is notes that require an assertion; `None`, never 1.000,
    when no note does."""
    rows = script._anchoring_rows(
        {
            "a.json": _recording(
                _note("needs", 2, 2, required=True, assertions=0),
                _note("has", 2, 2, required=True, assertions=1),
                _note("free", 2, 2, required=False, assertions=0),
            ),
            "b.json": _recording(_note("free", 2, 2, required=False, assertions=3)),
        }
    )
    by_file = {row["file"]: row for row in rows}
    assert by_file["a.json"]["assertion_notes"] == 2
    assert by_file["a.json"]["assertion_notes_covered"] == 1
    assert by_file["a.json"]["assertion_coverage"] == 0.5
    assert by_file["b.json"]["assertion_notes"] == 0
    assert by_file["b.json"]["assertion_coverage"] is None


def test_anchoring_labels_each_recording_by_its_own_header(script):
    assert script._recording_label(_recording(task="T-15", runner=None)) == "T-15 direct"
    assert script._recording_label(_recording(tool_fetch=False)) == "T-63 ADK inline"
    assert script._recording_label(_recording(tool_fetch=True)) == "T-63 ADK tool-fetch"


def test_anchoring_refuses_a_missing_recording(script, tmp_path, monkeypatch):
    """Three recordings are committed and the section reads all of them. A
    missing one is a broken checkout, never a shorter table."""
    (tmp_path / "extraction").mkdir()
    monkeypatch.setattr(script, "EVAL_DIR", tmp_path)
    with pytest.raises(SystemExit, match="results.json is missing"):
        script._anchoring_section()
