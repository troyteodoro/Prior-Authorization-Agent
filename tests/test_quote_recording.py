"""T-98 — the six quote recordings, verified for zero model calls (REQ-68, D122).

`scripts/run_quote_measurement.py` and `scripts/run_adk_quote_measurement.py`
spend the calls; this file re-reads what they wrote. D45's split, and D17's
answer to the staleness it invites, applied to a third recording family:

- every note is **re-hashed** against the store before its record is believed;
- every recorded quote is **re-anchored** from the raw payload through
  `build_quote_result` and must reproduce the recorded spans;
- every recorded span is **re-validated** through the same rejector production
  uses (`spans.validate`);
- the recorded model is the pin, the prompt version is the code's, the tier is
  one of the two, the table's rows are the ones asked, and **every turn is
  counted** — the singular `metrics` is turn one and the trace holds them all.

The fabrication figure is pinned to what D122 records, in
`test_extraction.py`'s shape for headline numbers: a recording that says
something else is a new measurement that has not been written down.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pa_agent.contracts import Document, EvidenceSpan
from pa_agent.extraction import REASK_ROUNDS
from pa_agent.index import DocumentIndex
from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL, SECOND_TIER
from pa_agent.quotes import (
    DROP_REASONS,
    QUOTE_PROMPT_VERSION,
    RecordedQuoteRunner,
    build_quote_result,
)
from pa_agent.spans import validate
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "eval" / "history"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"

#: (file, runner, tool_fetch, tier) — the six recordings T-98 committed.
RECORDINGS = [
    ("results.json", "direct", None, MEASURED_TIER),
    ("results_vertex.json", "direct", None, SECOND_TIER),
    ("adk_results_inline.json", "adk", False, MEASURED_TIER),
    ("adk_results_inline_vertex.json", "adk", False, SECOND_TIER),
    ("adk_results_tool_fetch.json", "adk", True, MEASURED_TIER),
    ("adk_results_tool_fetch_vertex.json", "adk", True, SECOND_TIER),
]
FILES = [r[0] for r in RECORDINGS]

#: What D122 records per recording: `(note, condition)` pairs for which the
#: model returned a passage that did not anchor. Filled at the close from the
#: measurements; a recording that disagrees is a measurement nobody wrote down.
DECIDED_FABRICATED: dict[str, int] = {
    "results.json": 0,
    "results_vertex.json": 0,
    "adk_results_inline.json": 0,
    "adk_results_inline_vertex.json": 0,
    "adk_results_tool_fetch.json": 0,
    "adk_results_tool_fetch_vertex.json": 0,
}


def _load(filename: str) -> dict:
    path = HISTORY_DIR / filename
    assert path.exists(), (
        f"no eval/history/{filename}; run the quote measurement for it "
        "(it spends model calls)"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def recordings() -> dict[str, dict]:
    return {filename: _load(filename) for filename in FILES}


@pytest.fixture(scope="module")
def rows():
    return LocalKnowledgeStore().get_medication_effect_rows()


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.mark.parametrize("filename, runner, tool_fetch, tier", RECORDINGS)
def test_the_recording_names_the_pin_the_version_the_tier_and_the_task(
    recordings, filename, runner, tool_fetch, tier
) -> None:
    payload = recordings[filename]
    assert payload["model"] == PINNED_MODEL
    assert payload["prompt_version"] == QUOTE_PROMPT_VERSION
    assert payload["temperature"] == 0.0
    assert payload["reask_rounds"] == REASK_ROUNDS
    assert payload["task"] == "T-98" and payload["decision"] == "D122"
    assert payload["tier"] == tier
    assert payload["runner"] == runner
    if runner == "adk":
        assert payload["tool_fetch"] is tool_fetch
        assert payload["adk_version"]
        if tool_fetch:
            # The boolean that separates the two tiers' prompts (D62, D106).
            assert payload["output_schema_and_tools"] is (tier == SECOND_TIER)
    for record in payload["notes"]:
        assert record["trace"]["prompt_version"] == QUOTE_PROMPT_VERSION
        assert record["trace"]["runner_name"] == runner


@pytest.mark.parametrize("filename", FILES)
def test_the_recording_asked_about_the_tables_rows(recordings, filename, rows) -> None:
    asked = recordings[filename]["rows_asked"]
    assert asked == [{"row_id": r.row_id, "effect_display": r.effect_display} for r in rows]


@pytest.mark.parametrize("filename", FILES)
def test_every_note_still_hashes_to_what_was_measured(recordings, filename, store) -> None:
    checked = 0
    for record in recordings[filename]["notes"]:
        text = store.get_document(record["document_id"]).text
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == record["note_sha256"], (
            f"{record['note_id']} moved since it was measured (D18)"
        )
        checked += 1
    assert checked == 12, f"{checked} notes checked; twelve were measured (D122)"


@pytest.mark.parametrize("filename", FILES)
def test_no_note_failed_and_every_turn_is_counted(recordings, filename) -> None:
    """REQ-68: the singular `metrics` is turn one, the trace holds every turn,
    and the aggregate sums the trace (D71)."""
    payload = recordings[filename]
    assert payload["aggregate"]["failed"] == 0
    assert payload["aggregate"]["notes"] == 12
    turns = 0
    for record in payload["notes"]:
        assert record["raw"] is not None and record["error"] is None
        assert record["trace"] and record["trace"]["metrics"]
        assert record["metrics"] == record["trace"]["metrics"][0]
        turns += len(record["trace"]["metrics"])
        assert len(record["trace"]["metrics"]) <= (1 + REASK_ROUNDS) * (
            1 if payload["runner"] == "direct" else 6
        )
    assert payload["aggregate"]["model_calls"] == turns
    assert payload["aggregate"]["model_calls"] >= 12


@pytest.mark.parametrize("filename", FILES)
def test_re_anchoring_the_raw_payload_reproduces_the_recorded_quotes(
    recordings, filename, rows, store
) -> None:
    for record in recordings[filename]["notes"]:
        text = store.get_document(record["document_id"]).text
        result = build_quote_result(record["document_id"], text, record["raw"], rows=rows)
        assert {
            row_id: [s.model_dump(mode="json") for s in spans]
            for row_id, spans in result.spans.items()
        } == record["quotes"], record["note_id"]
        assert result.dropped == record["score"]["dropped"]
        assert {d["reason"] for d in result.dropped} <= DROP_REASONS


@pytest.mark.parametrize("filename", FILES)
def test_every_recorded_span_passes_the_validator(recordings, filename, store) -> None:
    index = DocumentIndex()
    checked = 0
    for record in recordings[filename]["notes"]:
        for spans in record["quotes"].values():
            for raw in spans:
                span = EvidenceSpan.model_validate(raw)
                if span.document_id not in index:
                    index.add(store.get_document(span.document_id))
                validate(span, index)
                checked += 1
    # Zero is the expected count on this corpus; the assertion is that none
    # that exists fails, and the fabrication test below pins how many exist.
    assert checked == sum(
        r["score"]["quotes_anchored"] for r in recordings[filename]["notes"]
    )


@pytest.mark.parametrize("filename", FILES)
def test_the_fabrication_figure_recomputes_and_is_what_the_entry_records(
    recordings, filename
) -> None:
    payload = recordings[filename]
    recomputed = sum(r["score"]["pairs_fabricated"] for r in payload["notes"])
    assert payload["aggregate"]["pairs_fabricated"] == recomputed
    assert payload["aggregate"]["pairs"] == 12 * 5
    assert filename in DECIDED_FABRICATED, f"D122 does not yet record {filename}'s figure"
    assert recomputed == DECIDED_FABRICATED[filename], (
        f"{filename}: {recomputed} fabricated pairs; D122 records "
        f"{DECIDED_FABRICATED[filename]} — a new measurement nobody wrote down"
    )
    assert payload["aggregate"]["pairs_anchored"] == 0, (
        "a passage anchored for a condition no committed note documents: a "
        "yellow this corpus was not supposed to produce (D120, D122) — stop and read it"
    )


@pytest.mark.parametrize("filename", FILES)
def test_the_model_offsets_are_still_unusable(recordings, filename) -> None:
    """D18's rule, on a sixth recording family: offsets are recorded and never
    trusted, and the count of usable ones is reported so the reversal clause
    can fire if it ever should."""
    payload = recordings[filename]
    emitted = sum(r["score"]["spans_emitted"] for r in payload["notes"])
    usable = sum(r["score"]["model_offsets_usable"] for r in payload["notes"])
    assert payload["aggregate"]["model_offsets_usable"] == usable
    assert payload["aggregate"]["spans_emitted"] == emitted


def test_the_direct_recording_answers_every_manifest_note_by_content(recordings, rows, store) -> None:
    """Fourteen notes on file, twelve measured: the declared clone's two are
    byte-identical to its source's and replay under their own ids (T-88,
    D102). This is the runner every gate hands `run_review`."""
    payload = recordings["results.json"]
    runner = RecordedQuoteRunner.from_records(payload["notes"], payload["rows_asked"], model=payload["model"])
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    assert len(manifest["notes"]) == 14
    for entry in manifest["notes"]:
        document = store.get_document(entry["document_id"])
        result = runner.run(document.document_id, document.text, rows)
        assert set(result.spans) == {r.row_id for r in rows}
        assert result.trace is not None and result.trace.document_id == document.document_id
        for spans in result.spans.values():
            for span in spans:
                assert span.document_id == document.document_id


def test_the_six_recordings_are_the_six_the_entry_names() -> None:
    present = sorted(p.name for p in HISTORY_DIR.glob("*.json"))
    assert present == sorted(FILES), present
