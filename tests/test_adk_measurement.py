"""T-67 — the measurement corpus is two corpora, and only one has an address (D67).

**Spends no model call.** `scripts/run_adk_extraction.py` is T-63's measurement and
is in no gate; this file gates the part of it that is a design decision rather than
a number — which notes each mode can reach, how the three outcomes are counted, and
whether a comparison is allowed to pool two different note sets.

The finding T-67 registered: under `--tool-fetch` the model is handed a
`document_id` and `read_note` resolves it through `PatientStore.get_document`.
Spike 001's five notes have no patient, so the port raises before the model is
asked anything. D67's answer is that they should not be given one — extraction is
the one thing here that needs no patient — so the script skips them and says so.

Four claims:

1. **The split is asked of the port**, never read off `case["corpus"]`. Two
   behavioural tests and one AST guard, because a label test answers identically
   on every input this corpus can produce (D65's lesson about mutations that
   survive behavioural tests).
2. **`skipped` is not `failed`.** The model was never asked, so nothing about the
   runner is being reported.
3. **A skipped note reaches no figure.** It is not a zero in the numerator.
4. **`--compare` recomputes over the intersection** and refuses to print an
   eleven-note column beside a six-note one.

T-68 adds a fifth, on the same file for the same reason — it is a decision about
how the measurement is stored, not a number:

5. **One recording per mode**, at a path derived from the mode, and `--compare`
   names the file it read.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# The sibling test module, imported under the name pytest itself gives it (its own
# directory is on `sys.path` in prepend import mode), so this is the same module
# object and not a second copy. Copying the twenty-line fake would be one more
# place for the ADK's response shape to drift.
from test_adk_agent import _fake_llm

from pa_agent.contracts import Document
from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL
from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_adk_extraction.py"
EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load("run_adk_extraction", SCRIPT)


@pytest.fixture(scope="module")
def cases(script) -> list[dict]:
    base = script._load_run_extraction()
    return base.spike_cases() + base.synthesized_cases()


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


# --------------------------------------------------------------------------
# 1. The split, and where the answer comes from
# --------------------------------------------------------------------------


def test_the_seventeen_notes_split_twelve_addressable_and_five_not(cases, store, script):
    """D67's finding, pinned against the real store and the real corpus: the
    five spike notes have no address on the patient plane; the synthesized
    documents — twelve since T-81 gave each chart two (D104) — do."""
    measurable, skipped = script.partition(cases, store, tool_fetch=True)

    assert len(cases) == 17, "T-81 measures seventeen notes"
    assert len(measurable) == 12
    assert len(skipped) == 5
    assert {c["corpus"] for c in measurable} == {"synthesized"}
    assert {c["corpus"] for c, _ in skipped} == {"spike_001"}


def test_the_spike_notes_still_have_no_address_on_the_patient_plane(cases, store):
    """D67's chosen answer, stated as the fact it rests on.

    If this starts failing, spike 001 acquired patients and D67's reversal
    condition has fired: the split closes on its own because addressability is
    asked of the port, and this entry — not the script — is what needs updating.
    """
    spike = [c for c in cases if c["corpus"] == "spike_001"]
    assert spike, "the spike corpus is half of what T-63 measures"
    for case in spike:
        with pytest.raises(KeyError):
            store.get_document(case["document_id"])


def test_without_tool_fetch_every_note_is_measurable(cases, store, script):
    """The note text travels in the message there, so the id is a label and not a
    lookup key. The corpus split is a property of the *mode*, not of the runner."""
    measurable, skipped = script.partition(cases, store, tool_fetch=False)
    assert len(measurable) == len(cases)
    assert skipped == []


class _FixedStore:
    """A patient plane holding exactly the ids it was given."""

    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def get_document(self, document_id: str) -> Document:
        if document_id not in self._ids:
            raise KeyError(document_id)
        text = "x"
        return Document(
            document_id=document_id,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )


def test_a_spike_note_with_an_address_becomes_measurable(cases, script):
    """The first half of "asked of the port, not of the label".

    A test keyed on `case["corpus"] == "spike_001"` would keep skipping this note
    after the port learned to resolve it.
    """
    spike = next(c for c in cases if c["corpus"] == "spike_001")
    store = _FixedStore({c["document_id"] for c in cases})
    measurable, skipped = script.partition(cases, store, tool_fetch=True)

    assert skipped == []
    assert spike["note_id"] in {c["note_id"] for c in measurable}


def test_a_synthesized_note_without_an_address_is_skipped(cases, script):
    """The other half. A corpus-name test would keep measuring this one and the
    run would fail on it instead of skipping it."""
    synthesized = next(c for c in cases if c["corpus"] == "synthesized")
    store = _FixedStore(
        {c["document_id"] for c in cases} - {synthesized["document_id"]}
    )
    _, skipped = script.partition(cases, store, tool_fetch=True)

    assert [c["note_id"] for c, _ in skipped] == [synthesized["note_id"]]


def _function(module_path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"no function {name!r} in {module_path.name}")


@pytest.mark.parametrize("name", ["addressable", "partition"])
def test_the_split_reads_no_corpus_label(name):
    """The structural half, for D65's reason.

    A resolver that checks the label and *then* falls through to the port answers
    identically on every input this repo can produce, so the two behavioural tests
    above would pass over it. Parsing is what catches it. Docstrings are exempt —
    the argument for this rule has to be allowed to name the thing it forbids.
    """
    node = _function(SCRIPT, name)
    body = ast.Module(body=node.body[1:], type_ignores=[])  # drop the docstring

    for child in ast.walk(body):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            assert child.value not in ("spike_001", "synthesized"), (
                f"{name} names a corpus; addressability is asked of the port so "
                "that the split closes by itself if the corpus changes (D67)"
            )
        if isinstance(child, ast.Subscript):
            key = getattr(child.slice, "value", None)
            assert key != "corpus", f"{name} reads the corpus label off the case"


# --------------------------------------------------------------------------
# 2 and 3. Three outcomes, and a skip is in none of the figures
# --------------------------------------------------------------------------


def _record(note_id: str, corpus: str, **over) -> dict:
    record = {
        "note_id": note_id,
        "corpus": corpus,
        "document_id": f"{note_id}.txt",
        "score": None,
        "metrics": None,
    }
    record.update(over)
    return record


def _score(**over) -> dict:
    score = {
        "labeled_events": 2,
        "extracted_events": 2,
        "matched_events": 2,
        "traps_req9": 1,
        "req9_traps_extracted": [],
        "field_total": 6,
        "field_disagreements": [],
        "spans_emitted": 4,
        "spans_anchored": 4,
        "spans_normalized": 0,
        "spans_unescaped": 0,
        "spans_disambiguated": 0,
        "model_offsets_usable": 0,
    }
    score.update(over)
    return score


def test_a_skipped_note_is_counted_apart_from_a_failed_one(script):
    """REQ-28's rule at a third site. "Answered wrongly", "errored" and "was never
    asked" have three next actions and only two of them are about the runner."""
    aggregate = script._aggregate(
        [
            _record("a", "synthesized", score=_score()),
            _record("b", "synthesized", error="ExtractionOutputError: NO_PAYLOAD"),
            _record("c", "spike_001", skipped="KeyError: no address"),
            _record("d", "spike_001", skipped="KeyError: no address"),
        ]
    )
    assert aggregate["notes"] == 1
    assert aggregate["failed"] == 1
    assert aggregate["skipped"] == 2


def test_a_skipped_note_reaches_no_figure(script):
    """A skip is not a zero in a numerator. The aggregate over one scored note
    plus two skips is the aggregate over that one note."""
    scored = _record("a", "synthesized", score=_score())
    alone = script._aggregate([scored], nest=False)
    with_skips = script._aggregate(
        [
            scored,
            _record("c", "spike_001", skipped="KeyError: no address"),
            _record("d", "spike_001", skipped="KeyError: no address"),
        ],
        nest=False,
    )
    ignored = {"skipped"}
    assert {k: v for k, v in alone.items() if k not in ignored} == {
        k: v for k, v in with_skips.items() if k not in ignored
    }


def test_the_aggregate_reports_each_corpus_separately(script):
    """`by_corpus` in every mode, not only when something was skipped. A figure
    that appears with a problem and vanishes without one is a figure nobody
    learns to read (D67)."""
    aggregate = script._aggregate(
        [
            _record("a", "synthesized", score=_score()),
            _record("n01", "spike_001", score=_score(extracted_events=4,
                                                     matched_events=2)),
        ]
    )
    assert set(aggregate["by_corpus"]) == {"spike_001", "synthesized"}
    assert aggregate["by_corpus"]["synthesized"]["precision"] == 1.0
    assert aggregate["by_corpus"]["spike_001"]["precision"] == 0.5
    assert aggregate["precision"] == round(4 / 6, 4), "the pooled figure is still there"
    assert "by_corpus" not in aggregate["by_corpus"]["spike_001"], (
        "one level of nesting; a corpus does not contain itself"
    )


# --------------------------------------------------------------------------
# 4. A comparison is over the notes both runners scored, or it is not one
# --------------------------------------------------------------------------


def _recording(records: list[dict], **over) -> dict:
    payload = {
        # The pin, never a literal (D20). A second copy of a model identifier is
        # how three of them came to disagree with the recorded measurement; a
        # fake recording in a test is still tracked Python and still scanned.
        "model": PINNED_MODEL,
        "tier": MEASURED_TIER,
        "adk_version": "2.8.0",
        "tool_fetch": True,
        "aggregate": {},
        "notes": records,
    }
    payload.update(over)
    return payload


def _write_pair(
    tmp_path, monkeypatch, script, direct: dict, adk: dict, tool_fetch: bool = True
) -> None:
    """Both recordings on disk, the ADK one at the path its mode owns (T-68)."""
    direct_path = tmp_path / "direct.json"
    direct_path.write_text(json.dumps(direct), encoding="utf-8")
    monkeypatch.setattr(script, "DIRECT_PATH", direct_path)
    _patch_adk_paths(tmp_path, monkeypatch, script)
    script.adk_path(tool_fetch).write_text(json.dumps(adk), encoding="utf-8")


def _parse_comparison(out: str, script) -> dict[str, tuple[str, str]]:
    """`_column`'s rows, as cells (T-70, D89).

    The rendering is `  <key>  <direct>  <other>` in fixed fields. Asserting over
    the whole rendered string instead — which this test did until T-70 — fails on
    any figure anywhere that happens to contain the sentinel's digits, and T-63's
    own corrected tool-fetch input total is 22,969.
    """
    rows: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in script.COMPARED:
            rows[parts[0]] = (parts[1], parts[2])
    return rows


def test_compare_recomputes_over_the_intersection_and_never_pools(
    script, tmp_path, monkeypatch, capsys
):
    """The failure this closes: an eleven-note column beside a six-note column is
    twelve rows of numbers that look like a comparison. The difference in every
    row would be five notes, not the runner."""
    # One shared note carries a token total containing the sentinel's digits.
    # This is deliberate (D89): the old `"99" not in out` assertion fails on this
    # rendering for a reason unrelated to pooling, and the cell-parsed form does
    # not. The collision is demonstrated here rather than argued about.
    collide = {"input_tokens": 990, "output_tokens": 1, "wall_time_ms": 1.0}
    shared = [
        _record("a", "synthesized", score=_score(), metrics=collide),
        _record("b", "synthesized", score=_score()),
    ]
    _write_pair(
        tmp_path,
        monkeypatch,
        script,
        direct=_recording(
            shared + [_record("n01", "spike_001", score=_score(spans_emitted=99))]
        ),
        adk=_recording(
            shared + [_record("n01", "spike_001", skipped="KeyError: no address")]
        ),
    )

    assert script.compare(tool_fetch=True) == 0
    out = capsys.readouterr().out

    assert "comparing 2 note(s)" in out
    assert "n01 excluded (skipped:" in out
    assert "n01 excluded (scored here, not scored by the other runner)" in out

    rows = _parse_comparison(out, script)
    assert rows, "the comparison table did not parse; _column's format changed"

    # 99 spans_emitted on the direct side's spike note. Pooling would put 107 in
    # the direct column here, and this is the whole point of the test.
    assert rows["spans_emitted"] == ("8", "8"), rows["spans_emitted"]

    # The sentinel absent from **every parsed cell**, which is the claim the old
    # substring assertion was reaching for (T-70, D89).
    for key, (direct, other) in rows.items():
        assert "99" not in (direct, other), (
            f"the sentinel reached the {key} row as a value: {direct} / {other}"
        )

    # And the collision really is in the rendering, so this test would fail under
    # the assertion it replaced. Without this the fix is untested.
    assert "990" in out


def test_compare_refuses_when_the_two_recordings_share_no_scored_note(
    script, tmp_path, monkeypatch, capsys
):
    """Not a comparison, and not a zero exit. There is nothing to compare, which
    is different from two runners agreeing on nothing."""
    _write_pair(
        tmp_path,
        monkeypatch,
        script,
        direct=_recording([_record("n01", "spike_001", score=_score())]),
        adk=_recording([_record("n01", "spike_001", skipped="KeyError")]),
    )
    assert script.compare(tool_fetch=True) == 2
    assert "nothing to compare" in capsys.readouterr().err


def test_compare_breaks_the_figures_out_per_corpus(
    script, tmp_path, monkeypatch, capsys
):
    """Two measurement corpora — one the owner wrote for the spike, one T-07 rendered
    from T-06's manifests — so an aggregate over both hides which one moved."""
    records = [
        _record("a", "synthesized", score=_score()),
        _record("n01", "spike_001", score=_score()),
    ]
    _write_pair(
        tmp_path, monkeypatch, script,
        direct=_recording(records), adk=_recording(records),
    )
    assert script.compare(tool_fetch=True) == 0
    out = capsys.readouterr().out
    assert "spike_001" in out and "synthesized" in out


# --------------------------------------------------------------------------
# The same answer the runner gets, over the real store — for zero model calls
# --------------------------------------------------------------------------


def test_an_addressable_note_runs_end_to_end_through_the_scoped_reader(
    cases, store, script
):
    """`partition` and the runner have to agree, or the script would skip notes the
    model could read (or worse, keep ones it cannot).

    The whole ADK flow runs: real `FunctionTool` declaration, real dispatch, real
    `output_schema`, the real `LocalPatientStore` behind `read_note`. Only the
    network is faked.
    """
    from google.genai import types

    from pa_agent.agent.extraction_agent import AdkExtractionRunner

    measurable, _ = script.partition(cases, store, tool_fetch=True)
    case = next(c for c in measurable if c["note_id"] == "E1+E11+E10c+E13/1")

    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    payload = next(
        r["raw"] for r in recording["notes"]
        if r["document_id"] == case["document_id"]
    )
    fetch = types.Part(
        function_call=types.FunctionCall(
            name="read_note", args={"document_id": case["document_id"]}
        )
    )
    runner = AdkExtractionRunner(
        llm=_fake_llm([fetch, json.dumps(payload)]),
        patient_store=store,
        tool_fetch=True,
    )
    result = runner.run(case["document_id"], case["text"])

    assert [c.name for c in result.trace.tool_calls] == ["read_note"]
    assert result.trace.tool_calls[0].ok is True
    assert len(result.events) > 0


def test_an_unaddressable_note_faults_in_the_tool_and_not_in_the_model(
    cases, store
):
    """Why skipping is the right verb. The tool raises before any extraction
    happens, so the model contributes nothing to the outcome — recording it as a
    runner failure would attribute a corpus fact to the runner."""
    from pa_agent.agent.patient_tools import build_note_reader

    case = next(c for c in cases if c["corpus"] == "spike_001")
    reader = build_note_reader(store, case["document_id"])
    with pytest.raises(KeyError):
        reader.tools["read_note"](case["document_id"])
    assert [c.ok for c in reader.calls] == [False]


# --------------------------------------------------------------------------
# The record shape, which nothing had ever built
# --------------------------------------------------------------------------


def test_a_record_can_be_built_from_a_real_case(cases, script):
    """The defect this file found by existing.

    All three record sites inlined `case["labels"]`, a key neither
    `spike_cases()` nor `synthesized_cases()` produces — so the script raised
    `KeyError` on note one of every run, in the success path as well as the two
    failure paths. Nothing caught it because T-63 spends model calls and is in no
    gate, and a gate that will not run the script has to build its output instead.
    """
    for case in cases:
        record = script._record_base(case)
        assert record["note_id"] == case["note_id"]
        assert set(record["labels"]) == {"events", "traps", "assertion_required"}


def test_a_record_identifies_a_note_the_way_the_direct_recording_does(cases, script):
    """The two recordings are compared record by record, so they identify a note
    with the same fields or `--compare` is matching on a coincidence."""
    direct = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    shared = {"note_id", "corpus", "document_id", "note_sha256", "cases", "labels"}
    assert set(script._record_base(cases[0])) == shared
    for record in direct["notes"]:
        assert shared <= set(record)


def _patch_adk_paths(tmp_path, monkeypatch, script) -> None:
    """Both modes' recordings under `tmp_path`.

    Both, always, even when a test writes one: a test that only redirected the
    mode under test would let a defect in the *other* mode's path write into
    `eval/extraction/` and be invisible until someone read `git status`.
    """
    monkeypatch.setattr(script, "ADK_INLINE_PATH", tmp_path / "adk_results_inline.json")
    monkeypatch.setattr(
        script, "ADK_TOOL_FETCH_PATH", tmp_path / "adk_results_tool_fetch.json"
    )


def _install_stub_runner(script, monkeypatch) -> list[str]:
    """`measure()` with the model replaced and nothing else stubbed.

    Returns the document ids the stub was asked for, accumulated across every
    `measure()` call in the test. Each runner instance fails its own first note,
    so a two-mode test exercises the `failed` branch twice rather than once.
    """
    import pa_agent.agent.extraction_agent as agent_module

    from pa_agent.runners import ExtractionFailure, ExtractionOutputError

    recording = json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))
    payloads = {r["document_id"]: r["raw"] for r in recording["notes"]}
    asked: list[str] = []

    class _StubRunner:
        def __init__(self, **kwargs) -> None:
            assert kwargs["client"] is None, "no credential was read"
            self.tool_fetch = kwargs["tool_fetch"]
            self.seen: list[str] = []

        def run(self, document_id: str, text: str):
            self.seen.append(document_id)
            asked.append(document_id)
            if len(self.seen) == 1:
                # One failure per run, so the `failed` branch is exercised too.
                raise ExtractionOutputError(ExtractionFailure.NO_PAYLOAD, "stub")
            from pa_agent.extraction import build_result

            return build_result(document_id, text, payloads[document_id], None)

    monkeypatch.setattr(agent_module, "AdkExtractionRunner", _StubRunner)
    monkeypatch.setattr(script, "_client", lambda: None)
    return asked


@pytest.mark.parametrize(
    "tool_fetch, expected",
    [(False, (16, 1, 0)), (True, (11, 1, 5))],
    ids=["inline", "tool_fetch"],
)
def test_the_whole_measure_path_runs_without_a_model(
    script, tmp_path, monkeypatch, capsys, tool_fetch, expected
):
    """`measure()` end to end for zero model calls: partition, three outcomes,
    aggregate, file written. The only stub is the runner itself.

    This is the test that would have caught the `labels` KeyError, and it is the
    closest a gate can get to T-63 without spending T-63's budget.

    Both modes, since T-68: inline reaches all eleven notes because the text
    travels in the message, `--tool-fetch` reaches the six the port can address.
    """
    _install_stub_runner(script, monkeypatch)
    _patch_adk_paths(tmp_path, monkeypatch, script)

    assert script.measure(tool_fetch=tool_fetch) == 0

    written = json.loads(script.adk_path(tool_fetch).read_text(encoding="utf-8"))
    aggregate = written["aggregate"]
    assert (aggregate["notes"], aggregate["failed"], aggregate["skipped"]) == expected
    assert len(written["notes"]) == 17, "every note is recorded, whatever happened"
    assert written["tool_fetch"] is tool_fetch

    out = capsys.readouterr().out
    if tool_fetch:
        assert aggregate["by_corpus"]["spike_001"]["skipped"] == 5
        assert "SKIP n01_clean_run" in out
    else:
        assert aggregate["by_corpus"]["spike_001"]["skipped"] == 0
        assert "SKIP" not in out, "nothing is unaddressable when the text is inline"


# --------------------------------------------------------------------------
# 5. One recording per mode (T-68, D68)
# --------------------------------------------------------------------------


def test_the_two_modes_own_two_paths(script):
    """Derived from the mode, so no invocation can land on the other's file."""
    assert script.adk_path(False) == script.ADK_INLINE_PATH
    assert script.adk_path(True) == script.ADK_TOOL_FETCH_PATH
    assert script.ADK_INLINE_PATH != script.ADK_TOOL_FETCH_PATH
    assert script.DIRECT_PATH not in (script.ADK_INLINE_PATH, script.ADK_TOOL_FETCH_PATH)


def test_each_mode_writes_its_own_recording_and_the_pair_survives(
    script, tmp_path, monkeypatch
):
    """T-68's defect, stated as the run that exposed it.

    `ADK_PATH` was one module constant, so the second run of the pair overwrote
    the first and T-63 — whose exit asks for both modes' aggregates — had one
    file to quote from. Asserting the two filenames differ is not enough: a
    program that writes both and then truncates one passes that.
    """
    _install_stub_runner(script, monkeypatch)
    _patch_adk_paths(tmp_path, monkeypatch, script)
    inline, tool_fetch = script.adk_path(False), script.adk_path(True)

    assert script.measure(tool_fetch=False) == 0
    after_first = inline.read_bytes()
    assert script.measure(tool_fetch=True) == 0

    assert inline.read_bytes() == after_first, (
        "the --tool-fetch run overwrote the recording the plain run just made"
    )
    assert tool_fetch.exists()
    for path, mode, reached in ((inline, False, 17), (tool_fetch, True, 12)):
        payload = json.loads(path.read_text(encoding="utf-8"))
        aggregate = payload["aggregate"]
        assert payload["tool_fetch"] is mode, f"{path.name} records the other mode"
        assert aggregate["notes"] + aggregate["failed"] == reached


@pytest.mark.parametrize("tool_fetch", [False, True], ids=["inline", "tool_fetch"])
def test_compare_names_the_recording_it_read(
    script, tmp_path, monkeypatch, capsys, tool_fetch
):
    """T-68's exit condition. Two files exist; the output says which one is in
    the column, by path and in the column's own heading."""
    records = [_record("a", "synthesized", score=_score())]
    _write_pair(
        tmp_path,
        monkeypatch,
        script,
        direct=_recording(records),
        adk=_recording(records, tool_fetch=tool_fetch),
        tool_fetch=tool_fetch,
    )

    assert script.compare(tool_fetch=tool_fetch) == 0
    out = capsys.readouterr().out
    assert script.adk_path(tool_fetch).name in out
    assert script.adk_path(not tool_fetch).name not in out
    assert f"adk·{'tool_fetch' if tool_fetch else 'inline'}" in out


def test_compare_refuses_a_recording_whose_mode_contradicts_its_path(
    script, tmp_path, monkeypatch, capsys
):
    """The label comes off the payload, not off the flag — and when the two
    disagree the comparison is refused rather than captioned wrongly.

    Louder than the model and tier mismatches beside it, which warn and carry on,
    because those still print true numbers under a true caption. Here the
    caption would be false, and naming the recording is what this task is (D68).
    """
    records = [_record("a", "synthesized", score=_score())]
    _write_pair(
        tmp_path,
        monkeypatch,
        script,
        direct=_recording(records),
        adk=_recording(records, tool_fetch=False),
        tool_fetch=True,
    )

    assert script.compare(tool_fetch=True) == 2
    err = capsys.readouterr().err
    assert "tool_fetch=False" in err
    assert "inline" in err and "tool_fetch was asked for" in err


@pytest.mark.parametrize("tool_fetch", [False, True], ids=["inline", "tool_fetch"])
def test_a_missing_recording_names_the_command_that_makes_it(
    script, tmp_path, monkeypatch, capsys, tool_fetch
):
    """Two invocations make the two ADK recordings, so "run the script" is an
    instruction to guess which one."""
    monkeypatch.setattr(script, "DIRECT_PATH", tmp_path / "direct.json")
    _patch_adk_paths(tmp_path, monkeypatch, script)

    assert script.compare(tool_fetch=tool_fetch) == 2
    err = capsys.readouterr().err
    assert "python scripts/run_extraction.py" in err
    assert script.adk_path(tool_fetch).name in err
    assert ("--tool-fetch" in err) is tool_fetch


# --------------------------------------------------------------------------
# Every model turn is counted, not just the first (T-63, D71)
# --------------------------------------------------------------------------


def _turn(input_tokens: int, output_tokens: int, wall_time_ms: float) -> dict:
    return {
        "model": PINNED_MODEL,
        "purpose": "extraction",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "wall_time_ms": wall_time_ms,
    }


def test_a_two_turn_note_reports_both_turns(script):
    """The defect T-63's own measurement found, pinned.

    Under `--tool-fetch` a note costs two model turns: the model calls `read_note`,
    then answers. The record's singular `metrics` is turn one; `trace["metrics"]`
    holds both. Summing the singular field counted the tool-call turn and dropped
    the turn carrying the extraction, understating output tokens 12.1x on the real
    recording and inverting the comparison's sign.

    Article X: cost is measured, never estimated. A total that omits half the
    turns is an estimate wearing instrumentation's clothes.
    """
    record = _record(
        "a",
        "synthesized",
        score=_score(),
        metrics=_turn(1684, 54, 677.0),
        trace={
            "tool_calls": [{"name": "read_note"}],
            "metrics": [_turn(1684, 54, 677.0), _turn(2157, 757, 2266.0)],
        },
    )
    aggregate = script._aggregate([record], nest=False)

    assert aggregate["total_input_tokens"] == 1684 + 2157
    assert aggregate["total_output_tokens"] == 54 + 757
    assert aggregate["total_wall_time_ms"] == pytest.approx(677.0 + 2266.0)


def test_a_single_turn_note_is_unchanged_by_the_fix(script):
    """Inline mode is one turn per note, so the trace and the singular field agree.
    The real inline recording was byte-identical before and after this fix, which
    is what makes it a repair to the tool path rather than a change of units."""
    record = _record(
        "a",
        "synthesized",
        score=_score(),
        metrics=_turn(1262, 1244, 3501.0),
        trace={"tool_calls": [], "metrics": [_turn(1262, 1244, 3501.0)]},
    )
    aggregate = script._aggregate([record], nest=False)

    assert aggregate["total_input_tokens"] == 1262
    assert aggregate["total_output_tokens"] == 1244


def test_a_record_with_no_trace_still_aggregates_its_metrics(script):
    """The fallback is deliberate, not defensive. `results.json` and any recording
    written before traces existed carry a singular `metrics` and no trace; they
    must keep aggregating, and the only honest total is the one turn they hold."""
    record = _record(
        "a", "synthesized", score=_score(), metrics=_turn(900, 40, 800.0)
    )
    aggregate = script._aggregate([record], nest=False)

    assert aggregate["total_input_tokens"] == 900
    assert aggregate["total_output_tokens"] == 40


# --------------------------------------------------------------------------
# Assertion coverage (T-71, D88)
#
# T-63 lost E8's program_assertions[] span to a paraphrase the anchorer
# correctly refused, and the aggregate reported precision 1.000, recall 1.000,
# REQ-9 exclusion 1.000 and field agreement 1.000 — because a note with zero
# labeled *events* contributes to no fidelity ratio. Spike 001's documented trap
# wearing new clothes: a transport error scores as flawless precision.
# --------------------------------------------------------------------------


def test_a_lost_assertion_is_visible_from_the_aggregate_alone(script):
    """T-71's exit. The whole point is *from the aggregate alone* — the per-note
    record already said so exactly, and nobody opens per-note records."""
    aggregate = script._aggregate(
        [
            _record(
                "kept",
                "synthesized",
                score=_score(assertion_required=True, assertions=1),
            ),
            _record(
                "lost",
                "synthesized",
                score=_score(assertion_required=True, assertions=0),
            ),
        ],
        nest=False,
    )
    assert aggregate["assertion_notes"] == 2
    assert aggregate["assertion_notes_covered"] == 1
    assert aggregate["assertion_coverage"] == 0.5
    # The figures that stayed at 1.000 while a required citation was lost, which
    # is the reason this key had to be added rather than inferred from them.
    assert aggregate["precision"] == 1.0
    assert aggregate["recall"] == 1.0
    assert aggregate["field_agreement"] == 1.0


def test_the_denominator_is_notes_not_assertions(script):
    """D88's rejected alternative. Counting assertions extracted over assertions
    labeled averages a whole lost note away: three from one note and none from
    another reads as 0.75 rather than as "one note produced nothing"."""
    aggregate = script._aggregate(
        [
            _record(
                "rich",
                "synthesized",
                score=_score(assertion_required=True, assertions=3),
            ),
            _record(
                "lost",
                "synthesized",
                score=_score(assertion_required=True, assertions=0),
            ),
        ],
        nest=False,
    )
    assert aggregate["assertion_coverage"] == 0.5, (
        "an assertions-over-assertions ratio would report 0.75 here and hide "
        "that one note lost its citation entirely (D88)"
    )


def test_a_note_that_requires_no_assertion_is_not_in_the_denominator(script):
    """`assertion_required: false` is not a miss. Counting it would report the
    runner failing on notes that asked for nothing."""
    aggregate = script._aggregate(
        [
            _record(
                "needs",
                "synthesized",
                score=_score(assertion_required=True, assertions=1),
            ),
            _record(
                "does_not",
                "synthesized",
                score=_score(assertion_required=False, assertions=0),
            ),
        ],
        nest=False,
    )
    assert aggregate["assertion_notes"] == 1
    assert aggregate["assertion_coverage"] == 1.0


def test_no_required_assertion_reports_none_rather_than_one(script):
    """"Nothing required an assertion" and "everything required one and got it"
    are different facts, and only the second is a result (D88). The same rule
    every other ratio here follows."""
    aggregate = script._aggregate(
        [_record("plain", "synthesized", score=_score())], nest=False
    )
    assert aggregate["assertion_notes"] == 0
    assert aggregate["assertion_coverage"] is None


def test_the_committed_recordings_report_their_assertion_coverage(script):
    """Recomputed over what is committed — no measurement, no model call. This
    is the figure D71 could not see: T-63's tool_fetch recording reported
    **1.000 recall and 1.000 precision with an assertion it never anchored**.
    T-89's re-measurement (D103) anchored E8's assertion on the first turn, so
    the committed figure now reads 1/1 — and the column exists so that a
    future run that loses it again reads 0/1 beside its 1.000 recall."""
    import json

    covers = {}
    for name in ("adk_results_inline", "adk_results_tool_fetch"):
        path = REPO_ROOT / "eval" / "extraction" / f"{name}.json"
        recording = json.loads(path.read_text(encoding="utf-8"))
        aggregate = script._aggregate(recording["notes"])
        covers[name] = (
            aggregate["assertion_notes"],
            aggregate["assertion_notes_covered"],
            aggregate["assertion_coverage"],
            aggregate["recall"],
        )

    assert covers["adk_results_inline"] == (2, 2, 1.0, 1.0)
    assert covers["adk_results_tool_fetch"] == (1, 1, 1.0, 1.0), (
        "the tool_fetch recording's one required assertion is anchored since "
        "T-89's re-measurement; a 0 here is D71's loss come back, readable "
        "from the aggregate (T-71)"
    )


# --------------------------------------------------------------------------
# The verbatim re-ask in the recordings (T-89, REQ-56, D103)
# --------------------------------------------------------------------------


def _reask_block(*, targets: int, recovered: int) -> dict:
    paths = [f"wm_events[{i}].quote" for i in range(targets)]
    return {
        "targets": [{"path": p, "reason": "event_quote_unanchorable", "quote": "q"} for p in paths],
        "answers": [{"path": p, "verbatim": "v"} for p in paths],
        "recovered": paths[:recovered],
        "unrecovered": paths[recovered:],
        "error": None,
    }


def test_the_aggregate_reports_what_the_reask_asked_and_recovered(script):
    """Four figures from the per-note `reask` blocks: notes re-asked, quotes
    asked about, quotes recovered, and the re-ask turns spent — the last read
    from the trace's per-turn `purpose`, so it is a count of calls and not of
    blocks."""
    records = [
        _record(
            "a", "synthesized", score=_score(),
            metrics=_turn(1000, 100, 500.0),
            trace={"tool_calls": [], "metrics": [
                _turn(1000, 100, 500.0), {**_turn(1200, 20, 300.0), "purpose": "extraction_reask"},
            ]},
            reask=_reask_block(targets=2, recovered=1),
        ),
        _record("b", "synthesized", score=_score(), metrics=_turn(900, 90, 400.0),
                trace={"tool_calls": [], "metrics": [_turn(900, 90, 400.0)]}, reask=None),
    ]
    aggregate = script._aggregate(records, nest=False)

    assert aggregate["reask_notes"] == 1
    assert aggregate["reask_targets"] == 2
    assert aggregate["reask_recovered"] == 1
    assert aggregate["reask_calls"] == 1
    assert aggregate["model_calls"] == 3
    assert aggregate["total_input_tokens"] == 1000 + 1200 + 900


def test_a_recording_without_reask_blocks_reads_zero_not_missing(script):
    aggregate = script._aggregate([_record("a", "synthesized", score=_score())], nest=False)
    assert (aggregate["reask_notes"], aggregate["reask_targets"], aggregate["reask_recovered"],
            aggregate["reask_calls"]) == (0, 0, 0, 0)


def test_turn_metrics_is_one_rule_in_one_place(script):
    """D71's rule moved down to `run_extraction.py` when the direct runner
    learned to spend two turns (T-89); the ADK script delegates rather than
    keeping a copy that could drift. Both must sum every turn."""
    base = script._load_run_extraction()
    record = _record(
        "a", "synthesized", score=_score(), metrics=_turn(10, 1, 1.0),
        trace={"tool_calls": [], "metrics": [_turn(10, 1, 1.0), _turn(20, 2, 2.0)]},
    )
    assert [m["input_tokens"] for m in base.turn_metrics([record])] == [10, 20]
    assert script._turn_metrics([record]) == base.turn_metrics([record])
    assert base.turn_metrics([_record("b", "synthesized", score=_score(), metrics=_turn(7, 1, 1.0))]) == [
        _turn(7, 1, 1.0)
    ]


@pytest.mark.parametrize("name", ["adk_results_inline", "adk_results_tool_fetch"])
def test_each_adk_recording_was_measured_on_the_code_s_configuration(name):
    """The runnable half of "re-measured" (D103): a recording stamped with a
    prompt version the code no longer has was measured on a different call
    configuration, and D45 says its numbers may not be quoted for this one.
    Every record of such a recording also carries its trace, so the aggregate
    and the replay can count every turn."""
    from pa_agent.extraction import PROMPT_VERSION

    path = REPO_ROOT / "eval" / "extraction" / f"{name}.json"
    recording = json.loads(path.read_text(encoding="utf-8"))
    assert recording["prompt_version"] == PROMPT_VERSION, (
        f"{name}: measured on {recording['prompt_version']!r}; re-run "
        "scripts/run_adk_extraction.py (it spends model calls)"
    )
    assert recording["task"] == "T-81" and recording["decision"] == "D104"
    assert recording["reask_rounds"] == 1
    for record in recording["notes"]:
        if record.get("score"):
            assert record.get("trace"), f"{record['note_id']}: no trace"
            assert "reask" in record and "raw_first_turn" in record
