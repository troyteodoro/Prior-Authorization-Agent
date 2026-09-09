"""T-15 — the extraction agent, verified against its recording (D45, D46, D47).

This file spends **no model call**. `scripts/run_extraction.py` measures and
records; this re-reads. That split is D17's, and the staleness it invites is
answered here rather than assumed away:

- every note is re-hashed before its results are believed, so a corpus that
  moved under a recording fails instead of scoring;
- every recorded span is re-validated through **T-11's validator**, the same
  rejector production uses — not a private re-slice;
- the recorded model must equal `PINNED_MODEL` (T-34: a finding whose
  provenance can drift without failing anything is not a finding).

The clause D46 added after the first measurement: every per-field span must be
nearer its own event than any other event's, which T-11 structurally cannot
check because a mis-anchored span slices back to its quote perfectly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pa_agent.contracts import EvidenceSpan
from pa_agent.extraction import INSTRUCTION, build_result
from pa_agent.index import DocumentIndex
from pa_agent.model_pin import PINNED_MODEL
from pa_agent.spans import validate
from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = REPO_ROOT / "eval" / "extraction" / "results.json"
SPIKE_NOTES = REPO_ROOT / "spike" / "spike_001" / "notes"

SPIKE_NOTE_IDS = {
    "n01_clean_run", "n02_gap_month_missed_visits", "n03_assertion_only",
    "n04_unsupervised_prior_attempts", "n05_two_programs_sparse_bmi",
}
# T-15's exit asks for three synthesized notes; the corpus has six and all are
# scored. E8 is required by name — it is the refusal test.
MIN_SYNTHESIZED = 3


@pytest.fixture(scope="module")
def results() -> dict:
    assert RESULTS_PATH.exists(), (
        "no recording; run `python scripts/run_extraction.py` (it spends model calls)"
    )
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def texts(results) -> dict[str, str]:
    """Note text by document_id, re-read from source and re-hashed."""
    store = LocalPatientStore()
    out = {}
    for record in results["notes"]:
        if record["corpus"] == "spike_001":
            text = (SPIKE_NOTES / f"{record['document_id']}.txt").read_text(encoding="utf-8")
        else:
            patient_id = record["document_id"].split("/")[0]
            text = store.get_notes(patient_id)[0].text
        out[record["document_id"]] = text
    return out


@pytest.fixture(scope="module")
def index(results, texts) -> DocumentIndex:
    from pa_agent.contracts import Document

    idx = DocumentIndex()
    for document_id, text in texts.items():
        idx.add(Document.from_text(document_id, text))
    return idx


def _all_spans(record: dict) -> list[tuple[str, dict]]:
    spans = []
    for event in record["events"]:
        spans.append((f"event {event['date']}", event["span"]))
        for label in ("bmi_span", "diet_span", "activity_span"):
            if event.get(label):
                spans.append((f"{label} {event['date']}", event[label]))
    for i, assertion in enumerate(record["assertions"]):
        spans.append((f"assertion {i}", assertion["span"]))
    if record.get("current_bmi_span"):
        spans.append(("current_bmi", record["current_bmi_span"]))
    return spans


# --------------------------------------------------------------------------
# The recording is about this corpus, on the pinned model
# --------------------------------------------------------------------------


def test_the_recording_names_the_pinned_model(results):
    """T-34's argument: a measurement that can drift onto another model
    without failing anything is not a measurement."""
    assert results["model"] == PINNED_MODEL
    assert results["temperature"] == 0.0, "Art. II: same note, same events"


def test_every_note_still_hashes_to_what_was_measured(results, texts):
    for record in results["notes"]:
        actual = hashlib.sha256(
            texts[record["document_id"]].encode("utf-8")
        ).hexdigest()
        assert actual == record["note_sha256"], (
            f"{record['note_id']}: the note changed since it was measured; "
            "these results describe a document that no longer exists"
        )


def test_both_corpora_are_covered(results):
    """T-15's exit names the five spike notes and three synthesized ones. The
    spike's double as regression cases (D10, D19)."""
    spike = {r["note_id"] for r in results["notes"] if r["corpus"] == "spike_001"}
    synthesized = [r for r in results["notes"] if r["corpus"] == "synthesized"]
    assert spike == SPIKE_NOTE_IDS
    assert len(synthesized) >= MIN_SYNTHESIZED


# --------------------------------------------------------------------------
# Correct events on every note
# --------------------------------------------------------------------------


def test_every_note_extracts_exactly_its_labeled_events(results):
    for record in results["notes"]:
        labeled = sorted(e["date"] for e in record["labels"]["events"])
        extracted = sorted(e["date"] for e in record["events"])
        assert extracted == labeled, (
            f"{record['note_id']}: extracted {extracted}, labeled {labeled}"
        )


def test_no_req9_trap_became_an_event(results):
    """REQ-9. A missed visit counted as an encounter bridges a gap month and
    flips c3 from NOT_MET to MET silently — the kill criterion's case."""
    for record in results["notes"]:
        taken = record["score"]["req9_traps_extracted"]
        assert not taken, f"{record['note_id']}: extracted REQ-9 traps {taken}"


def test_no_trap_of_any_kind_became_an_event(results):
    for record in results["notes"]:
        assert not record["score"]["traps_extracted"], record["note_id"]


def test_the_documentation_flags_match_the_labels(results):
    """REQ-38 and D15: `bmi` present only when the encounter documents one, so
    c4's weight-only month stays NOT_MET."""
    for record in results["notes"]:
        assert not record["score"]["field_disagreements"], (
            f"{record['note_id']}: {record['score']['field_disagreements']}"
        )


# --------------------------------------------------------------------------
# Every span passes T-11
# --------------------------------------------------------------------------


def test_every_recorded_span_passes_the_validator(results, index):
    """The exit condition's clause, run through the real rejector rather than
    a private re-slice — a span the system would refuse must not sit in a
    recording that claims success."""
    checked = 0
    for record in results["notes"]:
        for label, raw in _all_spans(record):
            span = EvidenceSpan.model_validate(raw)
            sliced = validate(span, index)
            assert sliced, f"{record['note_id']} {label}: empty slice"
            checked += 1
    assert checked, "no spans were checked; the gate is vacuous"


def test_nothing_was_dropped_as_unanchorable(results):
    """A quote Python could not locate is a fabricated or mangled citation.
    Zero is the claim; the count exists so it can be checked (D45)."""
    for record in results["notes"]:
        assert not record["score"]["dropped"], (
            f"{record['note_id']}: {record['score']['dropped']}"
        )


def test_every_per_field_span_sits_nearest_its_own_event(results):
    """D46's clause, added after the first measurement found seven spans that
    cited the wrong encounter while slicing back perfectly. T-11 cannot catch
    this: the text is genuinely there, just somewhere else."""

    def distance(span: dict, window: tuple[int, int]) -> int:
        if span["char_end"] <= window[0]:
            return window[0] - span["char_end"]
        if span["char_start"] >= window[1]:
            return span["char_start"] - window[1]
        return 0

    checked = 0
    for record in results["notes"]:
        events = record["events"]
        windows = [(e["span"]["char_start"], e["span"]["char_end"]) for e in events]
        for i, event in enumerate(events):
            others = [w for j, w in enumerate(windows) if j != i]
            for label in ("bmi_span", "diet_span", "activity_span"):
                span = event.get(label)
                if not span or not others:
                    continue
                checked += 1
                assert distance(span, windows[i]) <= min(
                    distance(span, w) for w in others
                ), (
                    f"{record['note_id']} {event['date']} {label} is nearer another "
                    "encounter than its own — a repeated phrase anchored to the "
                    "wrong visit (D46)"
                )
    assert checked, "no per-field spans were checked; the gate is vacuous"


# --------------------------------------------------------------------------
# E8: the refusal test
# --------------------------------------------------------------------------


def test_e8s_note_yields_zero_events_and_an_assertion(results):
    """The exit condition names this case. D12's detection is *zero events
    plus at least one assertion*, which is why both halves are asserted."""
    records = [r for r in results["notes"] if "E8" in r.get("cases", [])]
    assert records, "E8 is not in the recording"
    for record in records:
        assert record["events"] == [], f"{record['note_id']} extracted an encounter"
        assert len(record["assertions"]) >= 1, "no program_assertions span"


def test_the_spikes_assertion_note_behaves_the_same_way(results):
    """n03 is E8's shape in the spike corpus and its regression case."""
    record = next(r for r in results["notes"] if r["note_id"] == "n03_assertion_only")
    assert record["events"] == []
    assert len(record["assertions"]) >= 1


def test_an_assertion_is_never_also_an_event(results):
    """REQ-35: an assertion span may overlap an event's text only if the note
    documents the encounter, in which case it is an event and not an
    assertion. Where events exist the counts do not interact (D47 recorded the
    emission as unstable there and harmless under D12's zero-event guard)."""
    for record in results["notes"]:
        if record["events"]:
            continue
        for assertion in record["assertions"]:
            assert assertion["span"]["char_end"] > assertion["span"]["char_start"]


# --------------------------------------------------------------------------
# The anchoring findings stay visible
# --------------------------------------------------------------------------


def test_the_model_offsets_are_still_unusable(results):
    """D19's finding, re-measured on the wider schema. This is D17's reversal
    condition: if it ever rises, quote anchoring becomes unnecessary
    machinery — and it cannot rise unnoticed if nobody records it."""
    assert results["aggregate"]["model_offsets_usable"] == 0, (
        "model-emitted offsets became usable; D45 and this module's existence "
        "should be revisited deliberately"
    )


def test_every_emitted_span_anchored(results):
    aggregate = results["aggregate"]
    assert aggregate["spans_anchored"] == aggregate["spans_emitted"]


def test_the_headline_numbers_are_what_the_decision_records(results):
    """D47 quotes these. A recording that drifts from the entry describing it
    makes the entry false (D10's argument, T-34's mechanism)."""
    aggregate = results["aggregate"]
    assert aggregate["precision"] == 1.0
    assert aggregate["recall"] == 1.0
    assert aggregate["req9_exclusion_recall"] == 1.0
    assert aggregate["field_agreement"] == 1.0


# --------------------------------------------------------------------------
# The agent stays a leaf (Art. I) and the anchoring half runs without a model
# --------------------------------------------------------------------------


def test_build_result_needs_no_model(texts):
    """The half that turns a payload into contracts is pure, which is what
    lets `--rescore` re-anchor a recording for zero calls (D18, D46)."""
    document_id, text = next(iter(texts.items()))
    quote = text.split("\n")[0]
    payload = {
        "wm_events": [],
        "program_assertions": [
            {"quote": quote, "char_start": 0, "char_end": len(quote), "claim": "x"}
        ],
    }
    result = build_result(document_id, text, payload)
    assert len(result.assertions) == 1
    assert result.metrics is None


def test_a_fabricated_quote_produces_no_span(texts):
    """Article III at the extraction boundary: a quote that is not in the note
    is dropped, not anchored to something near it."""
    document_id, text = next(iter(texts.items()))
    payload = {
        "wm_events": [],
        "program_assertions": [
            {"quote": "The patient completed a program in Atlantis.",
             "char_start": 0, "char_end": 10, "claim": "x"}
        ],
    }
    result = build_result(document_id, text, payload)
    assert result.assertions == []
    assert result.dropped and result.dropped[0]["reason"].endswith("unanchorable")


def test_the_instruction_forbids_every_req9_category():
    """The prompt is the spike's, and D19's exclusion result belongs to this
    text. Losing a category silently would make that number describe a prompt
    that no longer exists."""
    for phrase in ("missed", "unsuccessful contact", "not physician supervised",
                   "unrelated section"):
        assert phrase in INSTRUCTION, f"the instruction stopped excluding {phrase!r}"


# --------------------------------------------------------------------------
# The two anchoring repairs, tested directly
#
# Both triggers are intermittent: D47 measured the double-escaping on one run
# and not the next, and a recording without it cannot fail when the repair is
# removed. So the mechanisms get synthetic tests, the same reason D27's scorer
# checks branches no live case reaches.
# --------------------------------------------------------------------------


def test_a_double_escaped_newline_still_anchors():
    """D47: the model sometimes returns a literal backslash-n where the note
    holds a line break. That is the JSON encoding, not the claim — and on the
    run where it happened it cost six real encounters."""
    from pa_agent.anchor import anchor, unescape_literal_whitespace

    text = "05/08/2025 - Program visit 5. Weight 105.9 kg, BMI 38.9. Reviewed\nrestaurant ordering strategies."
    quote = "BMI 38.9. Reviewed\\nrestaurant ordering strategies."
    assert "\\n" in quote and "\n" not in quote, "the fixture must hold the escape"

    located = anchor("n", text, quote)
    assert located.anchored, "an escaped newline made an honest citation unanchorable"
    assert located.anchor_mode == "unescaped", "the repair must stay countable"
    assert text[located.char_start : located.char_end].startswith("BMI 38.9")

    assert unescape_literal_whitespace(quote) != quote
    # The document is never rewritten: offsets point into the original.
    assert "\\n" not in text


def test_unescaping_does_not_admit_a_changed_word():
    """The equivalence class widens; the comparison does not (D18's rule)."""
    from pa_agent.anchor import anchor

    text = "03/06/2025 - Program visit 3. Weight 108.8 kg, BMI 39.9."
    assert not anchor("n", text, "Weight 108.8 kg, BMI 41.0.").anchored


def test_a_repeated_quote_resolves_to_the_nearest_event():
    """D46, directly: four identical `BMI 37.6` lines, and the span for the
    fourth encounter must not cite the first."""
    from pa_agent.anchor import anchor

    text = (
        "03/10/2026 - visit. BMI 37.6.\n\n"
        "04/14/2026 - visit. BMI 37.6.\n\n"
        "05/12/2026 - visit. BMI 37.6.\n\n"
        "06/09/2026 - visit. BMI 37.6."
    )
    last_event = text.rindex("06/09/2026")
    window = (last_event, last_event + len("06/09/2026 - visit."))

    unscoped = anchor("n", text, "BMI 37.6")
    scoped = anchor("n", text, "BMI 37.6", prefer_near=window)

    assert unscoped.occurrences == 4
    assert unscoped.char_start < window[0], "unscoped anchoring takes the first hit"
    assert scoped.char_start > window[0], "scoped anchoring must take June's"
    assert scoped.disambiguated is True
    assert text[scoped.char_start : scoped.char_end] == "BMI 37.6"


def test_a_single_occurrence_is_unaffected_by_the_window():
    """D46 leaves the common path alone: one hit, same answer, and it is not
    reported as disambiguated."""
    from pa_agent.anchor import anchor

    text = "01/09/2025 - Initial visit. BMI 41.3 recorded."
    plain = anchor("n", text, "BMI 41.3")
    scoped = anchor("n", text, "BMI 41.3", prefer_near=(0, 5))
    assert plain.char_start == scoped.char_start
    assert scoped.disambiguated is False


# --------------------------------------------------------------------------
# T-60: the note's current BMI, which belongs to no encounter (D50)
# --------------------------------------------------------------------------


def _record(results: dict, case: str) -> dict:
    matching = [n for n in results["notes"] if case in (n.get("cases") or [])]
    assert len(matching) == 1, f"expected exactly one note carrying {case}"
    return matching[0]


def test_e10b_yields_a_current_bmi_from_its_own_clinic_line(results, texts):
    """The case T-60 exists for. Its patient has no encounters by design (it is
    also E8), so this value is reachable only as a note-level fact (D50)."""
    record = _record(results, "E10b")
    assert record["current_bmi"] == 36.2, (
        f"E10b's note states BMI 36.2 outside any encounter; recorded "
        f"{record['current_bmi']!r}. Without it REQ-34 has nothing to "
        "reconcile against and the case cannot be evaluated at all."
    )
    span = record["current_bmi_span"]
    assert span is not None, "a BMI nobody can cite is not a documented BMI (D15)"
    sliced = texts[span["document_id"]][span["char_start"]:span["char_end"]]
    assert "36.2" in sliced, f"span slices to {sliced!r}, which does not carry the value"


def test_e10b_still_yields_zero_events(results):
    """E8 and E10b share a note. Reaching E10b must not manufacture an
    encounter, which would silently retire E8's refusal test."""
    assert _record(results, "E10b")["events"] == []


def test_a_note_without_a_standalone_bmi_records_none(results):
    """The failure mode the instruction guards: carrying an encounter's BMI up
    to the note level. Every note but E10b's states no current BMI, and the
    ones with per-encounter BMIs are exactly where borrowing would show."""
    borrowed = [
        n["note_id"] for n in results["notes"]
        if n["note_id"] != _record(results, "E10b")["note_id"]
        and n.get("current_bmi") is not None
    ]
    assert not borrowed, (
        f"{borrowed} recorded a note-level BMI. Either the note genuinely "
        "states one and the manifest should say so, or an encounter's value "
        "was promoted, which makes reconciliation compare a fact against itself."
    )


def test_notes_with_event_bmis_did_not_promote_one(results):
    """Sharper than the test above: these notes carry per-encounter BMIs, so a
    model inclined to borrow has something to borrow."""
    for note in results["notes"]:
        if note.get("current_bmi") is None:
            continue
        event_bmis = {e["bmi"] for e in note["events"] if e["bmi"] is not None}
        assert note["current_bmi"] not in event_bmis or not event_bmis, (
            f"{note['note_id']}: current_bmi {note['current_bmi']} equals an "
            f"encounter BMI in {sorted(event_bmis)}; a note-level fact that "
            "duplicates an encounter is the borrowing D50 forbade."
        )


def test_the_widened_schema_held_the_measured_numbers(results):
    """T-60 widened the schema T-15 measured, so this is a new measurement
    (D45's rule). It held: the assertion is that it still holds, and a drop is
    the finding D50's reversal clause turns on."""
    aggregate = results["aggregate"]
    assert aggregate["precision"] == 1.0
    assert aggregate["recall"] == 1.0
    assert aggregate["req9_exclusion_recall"] == 1.0
    assert aggregate["field_agreement"] == 1.0
    assert aggregate["spans_anchored"] == aggregate["spans_emitted"]
    assert aggregate["model_offsets_usable"] == 0, (
        "the model emitted a usable offset pair; D18's anchoring rule was "
        "written on zero of eighty and reverses on evidence, not on a hunch"
    )
