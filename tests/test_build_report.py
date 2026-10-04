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

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from pa_agent.contracts import PredicateKind
from pa_agent.stores.policy import LocalPolicyStore

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
    results, cache, _reviews = script._run(
        __import__("pa_agent.stores.policy", fromlist=["LocalPolicyStore"]).LocalPolicyStore()
    )
    pairs = script._criterion_pairs(results, cache)
    emitted = sum(len(d.criterion_results) for d in script._determinations(cache))
    assert len(pairs) == labeled < emitted


# --------------------------------------------------------------------------
# The caveats are part of the deliverable, not decoration
# --------------------------------------------------------------------------


def test_the_corpus_size_is_the_corpus_s_size():
    """The caveat above asserts the sentence; this asserts its numbers.

    They were hardcoded prose until T-93 added three charts and the sentence
    said eight while eleven were scored (D113). The generator now derives them
    from the three artifacts that own them, and this is what fails if it stops
    (D108's rule, applied to the one figure in the report that is not measured
    from a run).
    """
    import json

    population = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
    )
    notes = json.loads(
        (REPO_ROOT / "data" / "patients" / "notes" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    sources = json.loads(
        (REPO_ROOT / "data" / "policies" / "source" / "sources.json").read_text(
            encoding="utf-8"
        )
    )
    sentence = (
        f"The corpus is {len(population['bundles'])} patients, "
        f"{len(notes['notes'])} chart notes and "
        f"{len(sources['documents'])} policy documents."
    )
    assert sentence in REPORT.read_text(encoding="utf-8"), (
        f"eval/report.md does not state: {sentence!r}"
    )


@pytest.mark.parametrize(
    "claim",
    [
        "first draft, drafted alongside the system it grades",  # D19/D42/D96
        "as one contractor would",                               # D21/D29
        "The corpus is",                                         # corpus size (D102)
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


def test_the_tier_section_renders_both_columns():
    """T-90's deliverable: §11 closes v1.1 on "the Vertex column renders in
    eval/report.md". A column, not extra rows in the anchoring table — pooling
    two tiers there would make a Vertex drop flip that section's
    no-claim-was-dropped branch and turn a finding into a red gate (D106)."""
    report_text = REPORT.read_text(encoding="utf-8")
    assert "## Tier (P8, D5, D62, D106)" in report_text
    section = report_text[report_text.index("## Tier (P8"):]
    section = section[: section.index("## Abstention")]

    assert "| Figure | AI Studio | Vertex | \u0394 |" in section
    # D71's clause, answered with a number rather than a paragraph.
    assert "D71's reversal condition, answered: partly." in section
    assert "| Ratio | **4.12x** | **2.14x** |" in section
    assert "Read the delta, not only the ratio" in section
    # The anchoring section above it still describes one tier only.
    anchoring = report_text[report_text.index("## Anchoring"):report_text.index("## Tier (P8")]
    assert "vertex" not in anchoring.lower(), (
        "the anchoring account pooled two tiers; its prose sums its own rows"
    )


def test_the_report_says_it_is_generated():
    assert "do not edit by hand" in REPORT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Planner recall (T-27, T-80, REQ-25, D86, D91)
# --------------------------------------------------------------------------


def _stubbed_recall(script, tmp_path, monkeypatch, mutate):
    """Run `_recall_section` against a recording `mutate` has edited.

    The committed recording may or may not carry a miss — since T-81 a planner
    can skip one of a chart's two notes (D104), and on the measured day it did
    not — so the way to show the section can *see* one, whatever the day
    produced, is to build it.
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

    _results, cache, _reviews = script._run(LocalPolicyStore())
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

    The measured recording gathered every note, so nothing in it exercises
    the comparison behind the direct column. Without this, `_recall_section`
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


def test_dropping_one_of_a_charts_two_notes_moves_the_direct_figure(
    script, tmp_path, monkeypatch
):
    """T-81 (D104): the behavioural twin of the parse pin below. E13's
    criterion cites both of its chart's notes, so dropping *one* of them from
    the gathered set must lower c3's direct figure — an intersection reading
    would keep it at 1.000 because the other note is still there."""

    def mutate(recording):
        victim = next(
            row for row in recording["patients"] if "E13" in (row.get("cases") or [])
        )
        notes = [
            d for d in victim["agentic"]["gathered"]["document_ids"]
            if d.endswith(".txt")
        ]
        assert len(notes) == 2, "E13's chart is two notes"
        victim["agentic"]["gathered"]["document_ids"] = [
            d for d in victim["agentic"]["gathered"]["document_ids"] if d != notes[-1]
        ]

    overall = _stubbed_recall(script, tmp_path, monkeypatch, mutate)
    direct = overall.rsplit("|", 3)[1]
    assert "1.000" not in direct, (
        f"direct recall stayed at 1.000 ({overall.strip()}) with one of E13's "
        "two notes un-gathered; a criterion cited from two documents is exactly "
        "the case `wanted <= gathered` and `wanted & gathered` disagree on"
    )


def test_the_oracle_gathered_every_note_on_file():
    """The fixed planner reads every note (D63). If its gathered count ever
    falls short of the store's, the oracle is no longer the regression oracle
    and the recall figures are against a shorter chart than the chart."""
    recording = json.loads(
        (REPO_ROOT / "eval" / "agentic" / "results.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (REPO_ROOT / "data" / "patients" / "notes" / "manifest.json").read_text(encoding="utf-8")
    )
    on_file: dict[str, int] = {}
    for record in manifest["notes"]:
        on_file[record["patient_id"]] = on_file.get(record["patient_id"], 0) + 1
    for row in recording["patients"]:
        assert row["oracle"]["gathered"]["notes"] == on_file[row["patient_id"]], row["patient_id"]
        assert on_file[row["patient_id"]] >= 2, "every measured chart is two notes (T-81)"


def test_the_recall_section_says_the_direct_figure_can_fall():
    """D91's reversal, taken by D104: the construction caveat comes out of the
    report because it stopped being true, and the report says instead what the
    planner did against a corpus where a skipped note is reachable."""
    text = REPORT.read_text(encoding="utf-8")
    section = text[text.index("## Planner recall") : text.index("## Cost and latency")]
    assert "by construction" not in section, (
        "the caveat D91 wrote for a one-note corpus is still in the report"
    )
    assert "the direct figure can fall" in section
    assert "gathered every note on file for" in section
    assert "| Patient (cases) | Notes on file | Gathered | Cited |" in section
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
    committed. T-81's three re-measurements on the two-note corpus (D104)
    anchored every span on the first turn in the direct and inline runs; the
    tool-fetch run paraphrased E8's assertion once more — *completed* for
    *completing*, the instance P2 was written from (D71, D98) — and the
    re-ask recovered it, so that column reads one asked, one recovered."""
    text = "\n".join(script._anchoring_section())
    assert (
        "| T-81 direct (`results.json`) | 17 | 175 | 175 | **0** | 0 | **0** "
        "| 2/2 = **1.000** |"
    ) in text
    assert (
        "| T-81 ADK inline (`adk_results_inline.json`) | 17 | 165 | 165 | **0** "
        "| 0 | **0** | 2/2 = **1.000** |"
    ) in text
    assert (
        "| T-81 ADK tool-fetch (`adk_results_tool_fetch.json`) | 12 (5 skipped) "
        "| 76 | 76 | **0** | 1 | **1** | 1/1 = **1.000** |"
    ) in text
    assert "**No claim was dropped in any recording.**" in text
    assert "asked about 1 quote and recovered 1 (D103)" in text
    assert "completing a six-month" in text, (
        "the recovered quote is named, per recording and per note"
    )


def test_anchoring_is_in_the_committed_report():
    text = REPORT.read_text(encoding="utf-8")
    assert "## Anchoring (Article III, D18, D88, D103)" in text
    assert "| Re-asked | Recovered |" in text


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
    assert script._recording_label(_recording(task="T-89", runner=None)) == "T-89 direct"
    assert script._recording_label(_recording(tool_fetch=False)) == "T-63 ADK inline"
    assert script._recording_label(_recording(tool_fetch=True)) == "T-63 ADK tool-fetch"


def test_anchoring_refuses_a_missing_recording(script, tmp_path, monkeypatch):
    """Three recordings are committed and the section reads all of them. A
    missing one is a broken checkout, never a shorter table."""
    (tmp_path / "extraction").mkdir()
    monkeypatch.setattr(script, "EVAL_DIR", tmp_path)
    with pytest.raises(SystemExit, match="results.json is missing"):
        script._anchoring_section()


# --------------------------------------------------------------------------
# T-95 (D116) — the compatibility account, and the rest of gate A10
# --------------------------------------------------------------------------

#: Every criterion of every committed tree and how it is evaluated, written
#: out. The account's whole claim is that nothing was omitted, so the
#: expectation cannot be derived from the trees the account itself reads —
#: that would assert the generator agrees with itself. Same rule as
#: `tests/test_predicate_kinds.py`'s `DECLARED`, one artifact along.
#:
#: `reused` means the kind's origin practice is not this criterion's; `earned`
#: means it is; `unclaimed` means the tree declared it so (REQ-58).
EXPECTED_ACCOUNT: dict[tuple[str, str], str] = {
    ("ncd-100.1-jf-v1", "a"): "earned",
    ("ncd-100.1-jf-v1", "b"): "earned",
    ("ncd-100.1-jf-v1", "c1"): "earned",
    ("ncd-100.1-jf-v1", "c2"): "earned",
    ("ncd-100.1-jf-v1", "c3"): "earned",
    ("ncd-100.1-jf-v1", "c4"): "earned",
    ("ncd-100.1-jf-v1", "c5"): "earned",
    ("ncd-100.1-jjm-v1", "a"): "earned",
    ("ncd-100.1-jjm-v1", "b"): "earned",
    ("ncd-100.1-jjm-v1", "c1"): "earned",
    ("ncd-100.1-jjm-v1", "c2"): "earned",
    ("ncd-100.1-jjm-v1", "c4"): "unclaimed",
    ("ncd-100.1-jjm-v1", "c5"): "earned",
    ("ncd-100.1-jjm-v1", "d"): "unclaimed",
    ("infliximab-ra-jjm-v1", "a"): "reused",
    ("infliximab-ra-jjm-v1", "b"): "earned",
    ("infliximab-ra-jjm-v1", "c"): "unclaimed",
    ("infliximab-ra-jjm-v1", "d"): "unclaimed",
    ("infliximab-ra-jjm-v1", "e"): "unclaimed",
    ("us-abdominal-visceral-j5-j8-v1", "a"): "reused",
    ("us-abdominal-visceral-j5-j8-v1", "b"): "earned",
    ("us-abdominal-visceral-j5-j8-v1", "c"): "unclaimed",
    ("us-abdominal-visceral-j5-j8-v1", "d"): "unclaimed",
    ("us-abdominal-visceral-j5-j8-v1", "e"): "unclaimed",
    # T-108 (D150): the fourth practice earned both of its kinds, the first
    # practice since the first to need two.
    ("pap-osa-dme-jd-v1", "a"): "earned",
    ("pap-osa-dme-jd-v1", "b"): "earned",
    ("pap-osa-dme-jd-v1", "c"): "unclaimed",
    ("pap-osa-dme-jd-v1", "d"): "unclaimed",
}


def _compatibility(text: str) -> str:
    """The rendered section, sliced out of the committed report."""
    start = text.index("## Cross-practice compatibility")
    return text[start : text.index("## Scope of these numbers", start)]


def _criterion_rows(section: str) -> dict[tuple[str, str], str]:
    """`(tree, criterion) -> the 'Evaluated by' cell`, from the rendered table."""
    body = section[section.index("### Every criterion of every loaded tree") :]
    body = body[: body.index("### Why each unclaimed")]
    rows = {}
    for line in body.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5 or cells[0] in ("Practice", "---"):
            continue
        rows[(cells[1].strip("`"), cells[2].strip("`"))] = cells[4]
    return rows


def test_the_account_covers_every_criterion_of_every_tree_on_disk(script):
    """A10's first clause: **zero omitted**.

    Set equality against the policy directory, not a row count — a count
    matches while naming the wrong twenty-four, and the one thing this
    account claims is that it left nothing out. The expected set is read
    straight from the JSON files here rather than through the generator's own
    `_trees()`, so a generator reading a hand-written list of trees fails.
    """
    on_disk = set()
    for path in sorted((REPO_ROOT / "data" / "policies").glob("*.json")):
        tree = json.loads(path.read_text(encoding="utf-8"))
        for criterion in tree["criteria"]:
            on_disk.add((tree["policy_version_id"], criterion["id"]))

    rendered = set(_criterion_rows(_compatibility(REPORT.read_text(encoding="utf-8"))))
    assert rendered == on_disk
    assert rendered == set(EXPECTED_ACCOUNT), (
        "the trees on disk no longer match the account this test asserts; a "
        "new tree or criterion is a deliberate diff here (D116)"
    )


def test_every_criterion_is_classed_the_way_the_table_above_says(script):
    """The classification itself, against the literal rather than the trees."""
    rows = _criterion_rows(_compatibility(REPORT.read_text(encoding="utf-8")))
    for key, expected in EXPECTED_ACCOUNT.items():
        cell = rows[key]
        if expected == "unclaimed":
            assert cell == "*declared unclaimed*", key
        else:
            assert expected in cell and "unclaimed" not in cell, key


def test_the_account_groups_by_practice_and_not_by_tree(script):
    """What the `practice` field is for (D111, D116).

    Four trees, three practices: the two bariatric trees are one practice
    under two contractors, and Palmetto's rheumatology tree is a different
    practice under one of those same contractors. Grouping by
    `policy_version_id` renders four rows and grouping by
    `jurisdiction.contractor` renders three *wrong* ones — this is the
    assertion that tells the three apart.
    """
    section = _compatibility(REPORT.read_text(encoding="utf-8"))
    per_practice = section[section.index("### Per practice") :]
    per_practice = per_practice[: per_practice.index("**28 criteria")]
    names = [
        line.strip().strip("|").split("|")[0].strip()
        for line in per_practice.splitlines()
        if line.startswith("| ") and not line.startswith("| Practice")
        and "---" not in line
    ]
    assert names == [
        "bariatric surgery", "diagnostic ultrasound", "rheumatology", "sleep medicine",
    ], (
        "the practice rows moved; the order is sorted and pinned, because an "
        "unsorted set iteration renders a report that differs between "
        "processes and `--verify` would fail on a later day rather than here"
    )

    counts = {
        tree: practice
        for tree, practice in (
            ("ncd-100.1-jf-v1", "bariatric surgery"),
            ("ncd-100.1-jjm-v1", "bariatric surgery"),
            ("infliximab-ra-jjm-v1", "rheumatology"),
            ("us-abdominal-visceral-j5-j8-v1", "diagnostic ultrasound"),
            ("pap-osa-dme-jd-v1", "sleep medicine"),
        )
    }
    body = section[section.index("### Every criterion") :]
    for tree, practice in counts.items():
        assert f"| {practice} | `{tree}` |" in body, (tree, practice)


def test_exclusions_are_counted_apart_from_criteria(script):
    """A10 counts criteria, and an exclusion is not one (REQ-60, D111).

    Folding the three committed exclusions in would make the total 27, and
    the zero-omitted check above rests on that arithmetic.
    """
    section = _compatibility(REPORT.read_text(encoding="utf-8"))
    assert "**28 criteria across 5 trees and 4 practices, zero omitted.**" in section

    exclusions = section[section.index("### Categorical exclusions") :]
    assert "`bmi_below_bound_with_active_condition`" in exclusions
    assert "`active_medication_value_set`" in exclusions
    assert exclusions.count("\n| ") == 3 + 1, (
        "three committed exclusions plus the header separator; an exclusion "
        "silently dropped from this table approves past a denial (D111)"
    )
    criteria_table = section[
        section.index("### Every criterion") : section.index("### Why each unclaimed")
    ]
    assert "bmi_below_bound_with_active_condition" not in criteria_table


def test_every_unclaimed_criterion_is_quoted_not_summarized(script):
    """The tree owns the reason and the report quotes it (D116).

    A paraphrase here would be this report deciding what a tree meant, which
    is the same move `_criterion_class` refuses when it indexes `KIND_ORIGIN`
    rather than guessing.
    """
    section = _compatibility(REPORT.read_text(encoding="utf-8"))
    reasons = section[section.index("### Why each unclaimed") :]
    reasons = reasons[: reasons.index("### Categorical exclusions")]

    unclaimed = [key for key, cls in EXPECTED_ACCOUNT.items() if cls == "unclaimed"]
    assert len(unclaimed) == 10, "eight through v1.2, and the PAP tree's c and d (D150)"
    for tree, criterion_id in unclaimed:
        assert f"- **`{tree}` `{criterion_id}`**" in reasons, (tree, criterion_id)

    store = LocalPolicyStore()
    for tree_id, criterion_id in unclaimed:
        note = store.get_tree(tree_id).criterion(criterion_id).note
        assert note in reasons, (
            f"{tree_id} {criterion_id}: the note was not rendered verbatim"
        )


def test_every_predicate_kind_has_a_recorded_origin(script):
    """Pinned against the **enum**, never against the corpus (D116).

    `set(PREDICATES) == set(PredicateKind)`'s shape. A kind added without an
    origin is a red suite here, and — this is the part a corpus-derived
    constant could not give — a criteria tree revision cannot move an origin,
    because no tree is consulted.
    """
    assert set(script.KIND_ORIGIN) == set(PredicateKind)
    practices = {practice for practice, _ in script.KIND_ORIGIN.values()}
    assert practices == {
        "bariatric_surgery", "rheumatology", "diagnostic_ultrasound", "sleep_medicine",
    }
    earned_after_the_first = {
        kind.value
        for kind, (practice, _) in script.KIND_ORIGIN.items()
        if practice != "bariatric_surgery"
    }
    assert earned_after_the_first == {
        "medication_value_set_active",
        "procedure_value_set_interval",
        "note_evaluation_before_sleep_test",
        "note_sleep_test_index",
    }, (
        "the second and third practices earned one kind each (T-92, T-94); the "
        "fourth earned two, both over the second fact kind (T-108, D150)"
    )


def test_kind_origin_is_a_literal_and_not_derived_from_the_trees(script):
    """Parsed, because the mutation is invisible (D116, D65's move).

    Replacing `KIND_ORIGIN` with a comprehension over the bariatric trees'
    declared kinds renders a byte-identical report today, satisfies
    `--verify`, and passes every assertion above. It is also the shape this
    task rejected: a historical claim derived from a present corpus can only
    be kept green by rewriting the history it records.
    """
    module = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    assigned = [
        node
        for node in module.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "KIND_ORIGIN"
    ]
    assert len(assigned) == 1, "KIND_ORIGIN is one module-level literal"
    assert isinstance(assigned[0].value, ast.Dict)
    # `PredicateKind.BMI_OBSERVATION_THRESHOLD` is an `Attribute` and is what
    # the keys are; a `Call` or a comprehension is a derivation.
    for node in ast.walk(assigned[0].value):
        assert not isinstance(node, (ast.Call, ast.comprehension, ast.Subscript)), (
            "KIND_ORIGIN is derived rather than written out; a constant "
            "computed from the corpus agrees with the corpus by construction "
            "(D116)"
        )


def test_the_account_computes_its_totals_rather_than_stating_them(script):
    """The mutation `--verify` cannot see (D116).

    Hard-coding the per-practice summary produces identical bytes, so the
    committed report is satisfied and every figure above still matches. This
    is `test_recall_reads_the_agentic_side_not_the_oracles`' guard one
    section along, and `eval/` is scanned by nothing else.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    section = source[source.index("def _compatibility_section") :]
    section = section[: section.index("def _caveats_section")]
    assert "KIND_ORIGIN[" in section, "the account must index the origin table"
    assert "_criterion_class(" in section
    assert ".criteria" in section, "the account must read each tree's criteria"
    for literal in ("bariatric_surgery", "rheumatology", "diagnostic_ultrasound"):
        assert f'"{literal}"' not in section, (
            f"{literal!r} is named in the section body; the practices come "
            "from the trees, and a literal here is a summary that agrees with "
            "itself"
        )


def test_the_account_reads_the_directory_rather_than_a_list_of_trees(script):
    """`_trees()` globs; a hand-written list would answer 'zero omitted'
    about the list rather than about `data/policies/`."""
    source = SCRIPT.read_text(encoding="utf-8")
    body = source[source.index("def _trees") : source.index("def _criterion_class")]
    assert '.glob("*.json")' in body
    assert "get_tree(" in body, "the objects come back through the policy port"


def test_no_committed_eval_row_is_anything_but_pass(script):
    """**A10's second clause**, which nothing checked (T-95, D116).

    `eval/run_eval.py` is a baseline *diff*: drift in either direction fails,
    and `--update-baseline` exists so a changed status is adopted as a
    reviewed diff (D27). That is right for a harness and it is not a check on
    the statuses themselves — a row that starts answering `FAIL`, adopted and
    committed, leaves all ten gates green. A10 says every row `PASS`, so
    something has to say it.
    """
    baseline = json.loads(
        (REPO_ROOT / "eval" / "baseline.json").read_text(encoding="utf-8")
    )
    failures = {
        case: row["status"]
        for case, row in baseline["cases"].items()
        if row["status"] != "PASS"
    }
    assert not failures, (
        f"{failures} — a non-`PASS` row may be committed only with a decision "
        "entry that reverses A10, never by `--update-baseline` alone"
    )

    outcomes = REPORT.read_text(encoding="utf-8")
    outcomes = outcomes[outcomes.index("## Outcomes") : outcomes.index("## Per-criterion")]
    for status in ("FAIL", "BLOCKED", "ERROR"):
        assert f"| `{status}` | 0 |" in outcomes, f"{status} is no longer zero"


# --------------------------------------------------------------------------
# T-98 (D122): the quote consultation section
# --------------------------------------------------------------------------


def _quote_note(note_id: str, returned: int, anchored: int, refused: int = 0,
                turns: int = 1, reask: dict | None = None) -> dict:
    fabricated = 1 if returned and not anchored else 0
    return {
        "note_id": note_id,
        "score": {
            "quotes_returned": returned, "quotes_anchored": anchored,
            "quotes_refused": refused, "pairs_with_quote": 1 if returned else 0,
            "pairs_anchored": 1 if anchored else 0, "pairs_fabricated": fabricated,
            "dropped": (
                [{"reason": "effect_quote_unanchorable", "quote": "a  passage\nnot there"}]
                if refused else []
            ),
        },
        "trace": {"metrics": [
            {"input_tokens": 10, "output_tokens": 2, "wall_time_ms": 1.0}
        ] * turns},
        "reask": reask,
    }


def _quote_recording(*notes: dict, lying: bool = True) -> dict:
    return {
        "rows_asked": [{"row_id": f"r{i}", "effect_display": f"c{i}"} for i in range(5)],
        # A lying aggregate: the rows must come from the records (T-71).
        "aggregate": {"pairs_fabricated": 99, "model_calls": 99} if lying else {},
        "notes": list(notes),
    }


def test_quote_rows_recompute_from_the_records_and_not_the_aggregate(script):
    rows = script._quote_rows([
        ("direct", "ai_studio", _quote_recording(
            _quote_note("a/1", 0, 0),
            _quote_note("a/2", 2, 1, refused=1, turns=2,
                        reask={"targets": [{"path": "p"}], "recovered": [], "unrecovered": ["p"]}),
        )),
        ("ADK inline", "vertex", _quote_recording(_quote_note("b/1", 0, 0))),
    ])
    first, second = rows
    assert first["notes"] == 2 and first["pairs"] == 10
    assert first["pairs_fabricated"] == 0 and first["pairs_anchored"] == 1
    assert first["quotes_returned"] == 2 and first["quotes_anchored"] == 1 and first["quotes_refused"] == 1
    assert first["reask_targets"] == 1 and first["reask_recovered"] == 0
    assert first["model_calls"] == 3, "every turn, not the lying aggregate"
    assert first["input_tokens"] == 30
    assert first["refused"] == [{"note_id": "a/2", "quote": "a passage not there"}]
    assert second["tier"] == "vertex" and second["pairs"] == 5 and second["model_calls"] == 1


def test_the_quote_section_reads_every_committed_recording(script):
    """The figures the section exists to carry, pinned to the six recordings
    as committed (D122, D152): twenty-four notes each, a hundred and twenty
    pairs, zero returned, zero fabricated; a turn per note on the direct and
    inline runs and two on the tool-fetch runs, where a tool round trip is two
    calls (D71). One note on AI Studio's tool-fetch run is recorded as failed,
    so that row reads twenty-three answered and a hundred and fifteen pairs."""
    text = "\n".join(script._quote_section())
    assert "## Quote consultation (T-98, REQ-67, REQ-68, D122)" in text
    for label, tier, filename, notes, turns, pairs in (
        ("direct", "ai_studio", "results.json", "24", 24, 120),
        ("direct", "vertex", "results_vertex.json", "24", 24, 120),
        ("ADK inline", "ai_studio", "adk_results_inline.json", "24", 24, 120),
        ("ADK inline", "vertex", "adk_results_inline_vertex.json", "24", 24, 120),
        ("ADK tool-fetch", "ai_studio", "adk_results_tool_fetch.json", "23 (1 failed)", 46, 115),
        ("ADK tool-fetch", "vertex", "adk_results_tool_fetch_vertex.json", "24", 48, 120),
    ):
        prefix = f"| {label} (`{filename}`) | {tier} | {notes} | {turns} | "
        row = next((line for line in text.splitlines() if line.startswith(prefix)), None)
        assert row is not None, f"no row for {filename}"
        assert row.endswith(f"| 0 | 0 | 0 | 0 | 0 | {pairs} | **0** |"), row
    assert "**No passage was returned for any pair in any recording.**" in text
    assert "Pairs anchored across all six recordings: 0." in text


def test_the_quote_section_is_in_the_committed_report():
    text = REPORT.read_text(encoding="utf-8")
    assert "## Quote consultation (T-98, REQ-67, REQ-68, D122)" in text
    assert "| Fabricated pairs |" in text
    assert "**Scope.** These totals are determination cost only." in text


def test_the_quote_section_names_a_refused_passage_beside_its_note(script, tmp_path, monkeypatch):
    """The per-note record always showed the refusal; the point of the
    section is that the *report* names it, flattened to one line, beside
    its note and its recording."""
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "x.json").write_text(
        json.dumps(_quote_recording(_quote_note("lossy/1", 1, 0, refused=1))),
        encoding="utf-8",
    )
    monkeypatch.setattr(script, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(script, "HISTORY_RECORDINGS", (("direct", "ai_studio", "history/x.json"),))
    text = "\n".join(script._quote_section())
    assert "**Refused passages, named.**" in text
    assert "- direct (ai_studio), note `lossy/1`: *a passage not there*" in text
    assert "| 5 | **1** |" in text
    assert "Pairs anchored across all six recordings: 0." in text


def test_the_quote_section_refuses_a_missing_recording(script, tmp_path, monkeypatch):
    monkeypatch.setattr(script, "EVAL_DIR", tmp_path)
    with pytest.raises(SystemExit, match="checkout problem"):
        script._quote_section()


def test_the_cost_section_states_its_scope(script):
    text = "\n".join(script._cost_section([], {}))
    assert "**Scope.** These totals are determination cost only." in text
    assert "Quote consultation" in text


# --------------------------------------------------------------------------
# A11's section (T-99, D123, D126)
#
# The adversarial half: each of these tries to make the section pass while
# describing a system that is wrong. A gate that only goes red when the
# implementation is broken is half a gate (T-95's finding about A10, D91's
# about the agentic recording's free half).
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def suggestion_text(script):
    return REPORT.read_text(encoding="utf-8")


def _suggestion_section(text: str) -> str:
    start = text.index("## Suggestion precision")
    return text[start : text.index("\n## ", start + 1)]


def test_a11_renders_its_denominator_beside_its_figure(suggestion_text):
    """A precision with no `n` is the figure D123 rewrote this gate to refuse.

    1.000 over one datapoint clears 0.90 and establishes almost nothing, so
    the denominator is not an optional detail of the rendering — it is the
    thing that makes the figure readable. Asserted on the *rendered* text
    because that is what a reviewer reads.
    """
    section = _suggestion_section(suggestion_text)
    assert re.search(r"\*\*Precision on green and yellow\*\* \| \*\*\d+/\d+ = ", section), (
        "the precision row no longer prints correct/total beside the rate"
    )
    assert re.search(r"Measured: \*\*[\d.]+\*\* over \*\*n = \d+\*\*", section), (
        "the threshold sentence no longer names its n"
    )
    assert "Read the denominator before the figure." in section


def test_a11s_trivial_baseline_cannot_be_read_as_a_pass(suggestion_text):
    """The all-red review has no precision, and the section must say so.

    A review that colours every candidate red emits no green and no yellow,
    so its precision has an empty denominator. Rendering that as `n/a` — or
    omitting the row — reads as *the baseline was beaten*, when what happened
    is that the baseline declined to play. The row says **no denominator**
    and the recall row carries the 0.000 that makes it legible.
    """
    section = _suggestion_section(suggestion_text)
    assert "| Precision of a trivial all-red review | **no denominator**" in section
    assert re.search(r"Recall of green-and-yellow, all-red review \| 0/\d+ = 0\.000", section)
    assert "the baseline declined to play" in section
    baseline_row = [
        line for line in section.splitlines()
        if "trivial all-red review" in line
    ]
    assert baseline_row and "n/a" not in baseline_row[0], (
        "the baseline's precision renders as `n/a`, which a reader takes for a "
        "score; it has no denominator and the row must say that"
    )


def test_a11s_graded_count_is_derived_from_the_corpus(script):
    """`12 of 12` is two derivations, not a literal.

    A hardcoded count keeps passing after a table row or a bundle is added,
    which is the whole shape of this failure. `_corpus_candidates` walks the
    manifest and the knowledge table; the graded figure walks the reviews the
    harness produced. This asserts they are computed, by moving one and
    watching the other stay put.
    """
    corpus = script._corpus_candidates()
    assert corpus > 0
    # Same walk, one bundle short: the derived figure must follow the corpus.
    manifest = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["bundles"]) > 1
    section = _suggestion_section(REPORT.read_text(encoding="utf-8"))
    assert f"finds **{corpus}** candidates" in section, (
        "the rendered corpus count is not the one `_corpus_candidates` derives"
    )
    assert f"grades **{corpus}** of them" in section, (
        "the graded count no longer equals the corpus count; if that is real, "
        "the section says so in prose and this test should read the new shape"
    )


def test_a11_reports_red_and_gates_only_the_asserting_colours(suggestion_text):
    """D123's central clause, in the rendering.

    Red is the abstention. It is counted and shown per row, and it is not in
    the gated denominator — a review that coloured everything red would have
    no precision at all rather than a perfect one.
    """
    section = _suggestion_section(suggestion_text)
    assert re.search(r"Suggestions emitted \| \d+ \(\d+ green, \d+ yellow, \d+ red\)", section)
    assert "Scoring an assertion and an abstention on one scale rewards a review" in section


def test_a11_names_the_yellow_clause_as_a14s(suggestion_text):
    """Clause 3 says whose the measured figure is rather than passing empty."""
    section = _suggestion_section(suggestion_text)
    assert "**A14's**" in section and "T-110" in section


def test_the_suggestion_section_sits_outside_every_heading_slice(suggestion_text):
    """The splice point, held by a test rather than by a comment.

    `test_the_recall_section_*` slices `## Planner recall` → `## Cost and
    latency` and asserts a phrase A11's section also uses. Several other tests
    slice between two headings the same way. This asserts A11 sits in the one
    gap none of them spans, so a future reordering that breaks that is red
    here rather than mysteriously red there.
    """
    text = suggestion_text
    assert text.index("## Cost and latency") < text.index("## Suggestion precision")
    assert text.index("## Suggestion precision") < text.index("## Cross-practice compatibility")
    recall = text[text.index("## Planner recall") : text.index("## Cost and latency")]
    assert "Suggestion precision" not in recall


def test_a11s_threshold_is_held_by_this_command_not_only_printed(script):
    """A11's gate, as a gate (D123: every clause held by a command).

    `--verify` byte-compares a re-render, so it catches *drift* and not a bad
    number: regenerate with precision at 0.5 and the report is internally
    coherent and every gate stays green. This recomputes the figure from the
    reviews the harness produced and asserts the bar.

    A2's, A3's and A5's are held the same way since `T-131`, below (D145).
    """
    _results, _cache, reviews = script._run(LocalPolicyStore())
    labels = script._labels()

    labelled = {}
    for case_id, case in labels.items():
        for entry in case["expect"].get("suggestions") or []:
            labelled[(case_id, entry["row_id"])] = entry["colour"]

    asserting = 0
    correct = 0
    for case_id, run in reviews.items():
        for suggestion in run.review.suggestions:
            if suggestion.colour.value not in ("green", "yellow"):
                continue
            asserting += 1
            if labelled.get((case_id, suggestion.row_id)) == suggestion.colour.value:
                correct += 1

    assert asserting > 0, (
        "no suggestion asserts, so A11's gated direction has no denominator; "
        "if the corpus really produces none, this test states the new shape"
    )
    precision = correct / asserting
    assert precision >= 0.90, (
        f"A11: precision on green and yellow is {precision:.3f} over "
        f"n = {asserting}; the threshold is A2's 0.90"
    )


# --------------------------------------------------------------------------
# A2, A3 and A5 held by a command, not only printed (T-131, D145)
# --------------------------------------------------------------------------

A2_THRESHOLD = 0.90


def _a2_failures(pairs) -> tuple[int, list[str]]:
    """(criteria with a `MET` call, the ones below A2's bar), plus `all`."""
    by_criterion: dict[str, list[tuple[str, str]]] = {}
    for _case, criterion_id, expected, actual in pairs:
        by_criterion.setdefault(criterion_id, []).append((expected, actual))
    failures = []
    called = 0
    total_calls = total_correct = 0
    for criterion_id, observed in sorted(by_criterion.items()):
        calls = [p for p in observed if p[1] == "MET"]
        if not calls:
            continue
        called += 1
        correct = sum(1 for p in calls if p[0] == "MET")
        total_calls += len(calls)
        total_correct += correct
        if correct / len(calls) < A2_THRESHOLD:
            failures.append(f"{criterion_id}: {correct}/{len(calls)}")
    if total_calls and total_correct / total_calls < A2_THRESHOLD:
        failures.append(f"all: {total_correct}/{total_calls}")
    return called, failures


def _a3_invalid(determinations) -> tuple[int, list[str]]:
    """(spans on `MET` verdicts, the ones that do not slice back)."""
    from pa_agent.contracts import CriterionVerdict
    from pa_agent.index import DocumentIndex
    from pa_agent.spans import SpanValidationError, validate
    from pa_agent.stores.patient import LocalPatientStore

    patients, policies = LocalPatientStore(), LocalPolicyStore()
    index = DocumentIndex()
    checked, invalid = 0, []
    for determination in determinations:
        for result in determination.criterion_results:
            if result.verdict is not CriterionVerdict.MET:
                continue
            for span in result.spans:
                checked += 1
                if span.document_id not in index:
                    try:
                        index.add(patients.get_document(span.document_id))
                    except KeyError:
                        index.add(policies.get_document(span.document_id))
                try:
                    validate(span, index)
                except (SpanValidationError, KeyError):
                    invalid.append(f"{determination.patient_id} {result.criterion_id} {span.document_id}")
    return checked, invalid


def _a5_errors(declared: dict[str, float], grid, rows) -> list[str]:
    values = set(declared.values())
    errors = []
    if not declared:
        errors.append("no loaded tree declares discrepancy_tolerance; the sweep is over nothing")
    if len(values) > 1:
        errors.append(f"trees declare different tolerances {declared}; one grid cannot pin both")
    pinned = [row for row in rows if "pinned" in row]
    for value in values:
        if value not in grid:
            errors.append(f"the declared tolerance {value} is not a grid point")
        if [row.split("|")[1].split()[0] for row in pinned] != [f"{value:g}"]:
            errors.append(f"the sweep marks {pinned} as pinned; the trees declare {value}")
    return errors


def _declared_tolerances(policies) -> dict[str, float]:
    declared = {}
    for path in sorted((REPO_ROOT / "data" / "policies").glob("*.json")):
        tree = policies.get_tree(json.loads(path.read_text(encoding="utf-8"))["policy_version_id"])
        for fact in tree.reconciled_facts:
            declared[tree.policy_version_id] = fact.discrepancy_tolerance.value
    return declared


def test_a2s_threshold_is_held_per_criterion_by_this_command(script):
    """§7 says *per-criterion* precision ≥ 0.90. `--verify` would pass a report
    regenerated at 0.5, so the figure is recomputed and the bar asserted (D145)."""
    results, cache, _reviews = script._run(LocalPolicyStore())
    called, failures = _a2_failures(script._criterion_pairs(results, cache))
    assert called > 0, "no criterion was called MET, so A2 has no denominator"
    assert failures == [], f"A2: below {A2_THRESHOLD}: {failures}"


def test_a3s_zero_is_held_by_this_command(script):
    results, cache, _reviews = script._run(LocalPolicyStore())
    checked, invalid = _a3_invalid(script._determinations(cache))
    assert checked > 0, "no MET verdict cites a span, so A3 holds vacuously"
    assert invalid == [], f"A3: MET verdicts with an invalid span: {invalid}"


def test_a5s_sweep_is_anchored_to_the_constant_the_trees_declare(script):
    """The pinned marker is a literal in `_sweep_rows`; a tree whose tolerance
    moved would leave the report pinning a point nothing declares (D145)."""
    declared = _declared_tolerances(LocalPolicyStore())
    assert _a5_errors(declared, script.TOLERANCE_GRID, script._sweep_rows()) == []


def test_the_a2_a3_a5_checks_follow_their_input(script):
    """Each check refuses a perturbed input (T-99's shape)."""
    results, cache, _reviews = script._run(LocalPolicyStore())
    pairs = script._criterion_pairs(results, cache)
    # Every MET call of one criterion relabelled NOT_MET, which drops that
    # criterion's precision to zero. One flipped pair stopped being enough at
    # T-108: criterion `a` carries fifteen MET calls, and fourteen of fifteen
    # is above A2's bar, so a single flip proved nothing about the check.
    target = next(c for _, c, e, a in pairs if e == a == "MET")
    perturbed = [
        (case, criterion_id, "NOT_MET" if criterion_id == target and actual == "MET" else expected, actual)
        for case, criterion_id, expected, actual in pairs
    ]
    assert perturbed != pairs and _a2_failures(perturbed)[1]

    determinations = script._determinations(cache)
    shifted = []
    moved = False
    for determination in determinations:
        results_out = []
        for result in determination.criterion_results:
            quoted = [i for i, sp in enumerate(result.spans) if sp.quote and sp.quote.strip()]
            if not moved and result.verdict.value == "MET" and quoted:
                # A quoted span, so the shifted slice disagrees with what it
                # claims; an unquoted one is only range-checked.
                i = quoted[0]
                span = result.spans[i]
                bad = span.model_copy(update={"char_start": span.char_start + 1, "char_end": span.char_end + 1})
                result = result.model_copy(update={"spans": (*result.spans[:i], bad, *result.spans[i + 1:])})
                moved = True
            results_out.append(result)
        shifted.append(determination.model_copy(update={"criterion_results": tuple(results_out)}))
    assert moved and _a3_invalid(shifted)[1]

    declared = _declared_tolerances(LocalPolicyStore())
    assert _a5_errors({k: 2.0 for k in declared}, script.TOLERANCE_GRID, script._sweep_rows())


def test_a11s_every_suggestion_traces_to_a_knowledge_table_row(script):
    """A11's second clause: zero suggestions without a source row (REQ-63)."""
    from pa_agent.stores.knowledge import LocalKnowledgeStore

    _results, _cache, reviews = script._run(LocalPolicyStore())
    rows = {row.row_id for row in LocalKnowledgeStore().get_medication_effect_rows()}
    assert rows

    seen = 0
    for run in reviews.values():
        for suggestion in run.review.suggestions:
            seen += 1
            assert suggestion.row_id in rows, (
                f"suggestion {suggestion.row_id} resolves to no table row"
            )
            assert suggestion.effect.document_id, "a suggestion with no effect span"
    assert seen, "no suggestion was graded; the clause would pass empty"


def test_a11s_third_clause_has_no_yellow_to_measure_and_says_whose_it_is(script):
    """Clause 3, held as the *absence* it is rather than passing silently.

    No committed note produces a yellow. That makes "zero yellow without a
    valid span" vacuously true, which is exactly what D123 rewrote A11 to
    stop happening quietly — so this asserts the set is empty **and** that
    the report names A14 as the owner of the measured figure. When `T-110`
    produces one, this test goes red and is rewritten to measure it.
    """
    _results, _cache, reviews = script._run(LocalPolicyStore())
    yellows = [
        suggestion
        for run in reviews.values()
        for suggestion in run.review.suggestions
        if suggestion.colour.value == "yellow"
    ]
    assert not yellows, (
        "a committed chart now produces a yellow; A11 clause 3 stops being a "
        "unit-level claim and this test measures the span instead (T-110)"
    )
    section = REPORT.read_text(encoding="utf-8")
    assert "**A14's**" in section


def test_a11s_gated_denominator_is_the_asserting_suggestions_and_no_others(script):
    """The mutation that survived the first pass (T-99's close).

    Computing A11's precision over **all three colours** — D123's explicitly
    rejected alternative — passed every other test in this file. On this
    corpus both readings render **1.000**: asserting-only is 1/1 and
    all-colours is 5/5, because every red is also correctly labelled. The
    *rate* cannot tell them apart; only the **denominator** can.

    So the denominator is the assertion. It must equal the number of green
    and yellow suggestions the harness produced, counted here from the
    reviews rather than read from the prose.
    """
    _results, _cache, reviews = script._run(LocalPolicyStore())

    asserting = sum(
        1
        for run in reviews.values()
        for suggestion in run.review.suggestions
        if suggestion.colour.value in ("green", "yellow")
    )
    emitted = sum(len(run.review.suggestions) for run in reviews.values())
    assert asserting < emitted, (
        "every suggestion asserts, so this corpus can no longer tell an "
        "asserting-only denominator from an all-colours one; the mutation "
        "this test exists for is undetectable again and needs a new grip"
    )

    section = _suggestion_section(REPORT.read_text(encoding="utf-8"))
    row = next(
        line for line in section.splitlines()
        if "**Precision on green and yellow**" in line
    )
    match = re.search(r"\*\*(\d+)/(\d+) = ", row)
    assert match, f"the precision row no longer prints correct/total: {row}"
    assert int(match.group(2)) == asserting, (
        f"A11's precision row has denominator {match.group(2)}; the harness "
        f"produced {asserting} asserting suggestions and {emitted} in total. "
        "A denominator of the total means precision is being scored over red "
        "as well, which is the alternative D123 rejected — it rewards a "
        "review that colours everything red."
    )
    threshold = next(
        line for line in section.splitlines() if "over **n =" in line
    )
    assert f"n = {asserting}**" in threshold, (
        "the threshold sentence's n disagrees with the precision row's "
        "denominator"
    )


def test_a11s_corpus_count_follows_the_table_rather_than_being_a_literal(script, monkeypatch):
    """The second mutation that survived (T-99's close).

    Replacing `_corpus_candidates()` with the literal `12` passed every test,
    because 12 is what the corpus holds today. A literal keeps passing until
    a table row or a bundle is added, and then reports a stale number with
    every gate green — the exact shape D91 found in the agentic recording's
    free half.

    So the assertion perturbs the input: drop one row from the knowledge
    table and the derived count must move. A literal cannot.
    """
    from pa_agent.stores.knowledge import LocalKnowledgeStore

    before = script._corpus_candidates()
    assert before > 0

    real = LocalKnowledgeStore.get_medication_effect_rows
    dropped = "lisinopril-renal-impairment"

    def fewer_rows(self):
        return [row for row in real(self) if row.row_id != dropped]

    monkeypatch.setattr(LocalKnowledgeStore, "get_medication_effect_rows", fewer_rows)
    after = script._corpus_candidates()

    assert after < before, (
        f"the corpus candidate count stayed {before} with the "
        f"{dropped!r} row removed from the table; it is a literal, not a "
        "derivation, and it will keep passing after the corpus grows"
    )
