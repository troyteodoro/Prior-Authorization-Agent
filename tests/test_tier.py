"""T-90 — a tier is an environment as well as a credential (D106).

**Spends no model call and touches no network.** Every assertion here runs
without a credential, which is the point: the two failures this file exists to
catch are both *silent*, and both would have been discovered only by reading a
Vertex recording carefully after paying for it.

1. **The ADK capability that D62 is about is ambient.**
   `output_schema_and_tools` is decided by `GOOGLE_GENAI_USE_ENTERPRISE` in the
   process environment, not by the `genai.Client` the runner is handed. A
   Vertex client alone routes requests to Vertex and leaves the injected
   `SetModelResponseTool` in place — so the tool-fetch recording would carry
   the AI Studio prompt under a `"tier": "vertex"` stamp and would answer
   D71's open reversal clause *wrongly*, with every gate green over it.

2. **A Vertex client built without a project keeps the AI Studio key.** The SDK
   falls back to Vertex express mode, so the run succeeds, spends money, and
   records `vertex` over a free-tier credential — defeating the reason D5 named
   Vertex at all.

Both are well-formed wrong answers of the kind D31 is about: nothing downstream
disagrees with them. So neither is reachable by omission.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from pa_agent.model_pin import MEASURED_TIER, PINNED_MODEL, SECOND_TIER
from pa_agent.tiers import (
    ENTERPRISE_ENV,
    LEGACY_ENV,
    TIERS,
    TierNotConfigured,
    UnknownTier,
    client_for,
    configure_tier_env,
    tier_of,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TIERS_MODULE = REPO_ROOT / "pa_agent" / "tiers.py"


@pytest.fixture
def clean_env(monkeypatch):
    """A process environment with nothing tier-shaped left in it."""
    for name in (
        ENTERPRISE_ENV,
        LEGACY_ENV,
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------
# 1. The capability D62 is about, asked of ADK
# --------------------------------------------------------------------------


def test_the_native_schema_capability_follows_the_tier(clean_env):
    """**The assertion that would have caught the whole round being wrong.**

    `output_schema` with `tools` is native on Vertex only (D62). If this ever
    reads `True` on the development tier, the two tiers stopped running
    different prompts and D71's reversal clause is settled by the SDK rather
    than by a measurement — which is a finding, and this test is where it
    surfaces.
    """
    from pa_agent.agent.extraction_agent import native_schema_enabled

    configure_tier_env(MEASURED_TIER)
    assert native_schema_enabled(PINNED_MODEL) is False

    configure_tier_env(SECOND_TIER)
    assert native_schema_enabled(PINNED_MODEL) is True


def test_the_env_file_value_loses_to_the_tier(clean_env):
    """`pa_agent/agent/.env` carries `GOOGLE_GENAI_USE_ENTERPRISE=0` and every
    loader in the repo `setdefault`s it into the process. A tier that merely
    defaulted would lose to that file, and the Vertex run would silently take
    the AI Studio prompt (D106)."""
    clean_env.setenv(ENTERPRISE_ENV, "0")  # what the .env leaves behind
    assert configure_tier_env(SECOND_TIER) is True


def test_a_contradicting_legacy_switch_is_removed(clean_env):
    """Both switches set resolves in favour of the enterprise one, silently.
    The loser is removed rather than left to mislead the next reader (D106)."""
    import os

    clean_env.setenv(LEGACY_ENV, "true")
    configure_tier_env(MEASURED_TIER)
    assert LEGACY_ENV not in os.environ


def test_configure_assigns_rather_than_defaulting():
    """The structural half, for D65's reason: `setdefault` in place of the
    assignment answers identically on a machine whose environment happens to be
    empty, so every test above would pass over the mutation and only a measured
    Vertex run would disagree."""
    tree = ast.parse(TIERS_MODULE.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "configure_tier_env"
    )
    body = ast.Module(body=fn.body[1:], type_ignores=[])  # docstrings are exempt
    calls = [
        n.func.attr
        for n in ast.walk(body)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    ]
    assert "setdefault" not in calls, (
        "configure_tier_env must assign; a default loses to pa_agent/agent/.env"
    )
    assert any(
        isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
        for n in ast.walk(body)
    ), "the environment is set by assignment"


# --------------------------------------------------------------------------
# 2. A tier is never reached by omission
# --------------------------------------------------------------------------


def test_an_unknown_tier_raises_and_names_the_task(clean_env):
    with pytest.raises(UnknownTier) as excinfo:
        client_for("aistudio")  # a plausible typo
    assert "T-90" in str(excinfo.value)


def test_vertex_without_a_project_raises_rather_than_using_the_key(clean_env):
    """Verified fact 2. With the project absent the SDK keeps `GOOGLE_API_KEY`
    and targets Vertex express mode, so the run would succeed and record
    `vertex` over a free-tier credential (D5, D106)."""
    clean_env.setenv("GOOGLE_API_KEY", "not-a-real-key")
    with pytest.raises(TierNotConfigured) as excinfo:
        client_for(SECOND_TIER)
    assert "GOOGLE_CLOUD_PROJECT" in str(excinfo.value)


def test_ai_studio_without_a_key_raises(clean_env):
    with pytest.raises(TierNotConfigured):
        client_for(MEASURED_TIER)


# --------------------------------------------------------------------------
# 3. What comes back is what was asked for
# --------------------------------------------------------------------------


def test_a_vertex_client_drops_the_key_and_reaches_vertex(clean_env):
    """Constructing a Vertex client needs no credential — the failure surfaces
    on the first request — which is exactly why this is checkable for free and
    why B1's smoke test is still not optional."""
    clean_env.setenv("GOOGLE_API_KEY", "not-a-real-key")
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "a-project")
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    client = client_for(SECOND_TIER)

    assert client._api_client.api_key is None, "the free-tier key must not ride along"
    assert client._api_client.vertexai is True
    assert tier_of(client) == SECOND_TIER


def test_an_ai_studio_client_survives_a_hostile_environment(clean_env):
    """The two halves are coupled: `BaseApiClient` reads the enterprise switch
    too, so with it left at `1` even a client built with `api_key=` comes back
    claiming `vertexai=True`. `client_for` sets both halves so they cannot
    disagree — measured while writing D106."""
    clean_env.setenv("GOOGLE_API_KEY", "not-a-real-key")
    clean_env.setenv(ENTERPRISE_ENV, "1")

    client = client_for(MEASURED_TIER)

    assert client._api_client.vertexai is False
    assert tier_of(client) == MEASURED_TIER


def test_the_ai_studio_read_back_is_a_real_guard(clean_env, monkeypatch):
    """The second guard, exercised on its own.

    `client_for` sets the environment before it builds, so on the ordinary path
    this check is unreachable and dropping it changes no observable behaviour —
    the shape T-89 recorded as an equivalent survivor. It is kept because the
    thing it guards against is a *silent* AI Studio recording measured on
    Vertex, so here the environment step is disabled and the guard is required
    to catch what gets built.
    """
    import pa_agent.tiers as module

    clean_env.setenv("GOOGLE_API_KEY", "not-a-real-key")
    clean_env.setenv(ENTERPRISE_ENV, "1")
    monkeypatch.setattr(module, "configure_tier_env", lambda tier: True)

    with pytest.raises(TierNotConfigured) as excinfo:
        module.client_for(MEASURED_TIER)
    assert "Vertex client" in str(excinfo.value)


# --------------------------------------------------------------------------
# 4. One construction site, and every recording states its tier
# --------------------------------------------------------------------------


def _tracked_python() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [REPO_ROOT / p for p in out.stdout.split("\0") if p]


def test_only_tiers_constructs_a_client():
    """Parsed, not grepped: a docstring has to be allowed to name the thing it
    forbids. A second construction site answers identically on every input —
    right up to the run where it builds the wrong tier — so no behavioural test
    catches it (D65, D67)."""
    offenders = []
    for path in _tracked_python():
        if path == TIERS_MODULE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Client"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "genai"
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"{offenders} build a client outside pa_agent/tiers.py; one site sets the "
        "environment and the credential together (D106)"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "eval/extraction/results.json",
        "eval/extraction/adk_results_inline.json",
        "eval/extraction/adk_results_tool_fetch.json",
        "eval/verifier/results.json",
        "eval/agentic/results.json",
        "eval/extraction/results_vertex.json",
        "eval/extraction/adk_results_inline_vertex.json",
        "eval/extraction/adk_results_tool_fetch_vertex.json",
        "eval/verifier/results_vertex.json",
        "eval/agentic/results_vertex.json",
    ],
)
def test_every_committed_recording_states_a_tier_this_project_measures_on(relative):
    """A recording that misstates its tier is D19's failure: numbers from the
    tier that may train on submitted data, quoted as the tier that does not."""
    payload = json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))
    stamped = payload["tier"]
    if isinstance(stamped, str):
        assert stamped in TIERS, f"{relative}: tier {stamped!r}"
        return
    # The agentic recording stamps per component, because it measures retrieval
    # on one tier while **replaying** extraction and verification from
    # recordings made on another. A scalar there would be false (D106).
    assert stamped["retrieval"] in TIERS, f"{relative}: {stamped['retrieval']!r}"
    for component in ("extraction", "verifier"):
        assert stamped[component].startswith("replayed from "), (
            f"{relative}: {component} claims to have been measured, not replayed"
        )


# --------------------------------------------------------------------------
# 5. The Vertex recordings: provenance, then the figures they measured
# --------------------------------------------------------------------------
#
# D106 splits these deliberately. The provenance half was written before the
# round was run and holds whatever Vertex did; the fidelity half carries the
# values it actually produced, in the shape `test_the_headline_numbers_are_
# what_the_decision_records` pins D47's. Mirroring `tests/test_extraction.py`'s
# perfect-score assertions onto a tier nobody had measured would have asserted
# the answer in advance, and one missed event — the whole reason a second
# measurement is a measurement — would have made the task unclosable.

VERTEX_EXTRACTIONS = (
    "eval/extraction/results_vertex.json",
    "eval/extraction/adk_results_inline_vertex.json",
    "eval/extraction/adk_results_tool_fetch_vertex.json",
)


def _load(relative: str) -> dict:
    path = REPO_ROOT / relative
    assert path.exists(), f"no {relative}; T-90's measurement has not been run"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", VERTEX_EXTRACTIONS)
def test_a_vertex_recording_names_the_pin_and_its_own_tier(relative):
    payload = _load(relative)
    assert payload["model"] == PINNED_MODEL
    assert payload["tier"] == SECOND_TIER
    assert payload["task"] == "T-90", "a Vertex run is its own measurement (D45)"


@pytest.mark.parametrize("relative", VERTEX_EXTRACTIONS)
def test_every_vertex_note_still_hashes_to_what_was_measured(relative):
    """Article III does not bend for a tier: a recording whose notes have moved
    describes documents that no longer exist."""
    import hashlib

    from pa_agent.stores.patient import LocalPatientStore

    store = LocalPatientStore()
    spike_dir = REPO_ROOT / "spike" / "spike_001" / "notes"
    checked = 0
    for record in _load(relative)["notes"]:
        if record.get("score") is None and record.get("skipped"):
            continue
        if record["corpus"] == "spike_001":
            text = (spike_dir / f"{record['document_id']}.txt").read_text("utf-8")
        else:
            text = store.get_document(record["document_id"]).text
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == record["note_sha256"]
        checked += 1
    assert checked, "no notes were checked; the gate is vacuous"


@pytest.mark.parametrize("relative", VERTEX_EXTRACTIONS)
def test_every_vertex_span_passes_the_validator(relative):
    """Run through the real rejector, not a private re-slice — a span the
    system would refuse must not sit in a recording that claims success."""
    from pa_agent.contracts import Document, EvidenceSpan
    from pa_agent.index import DocumentIndex
    from pa_agent.spans import validate
    from pa_agent.stores.patient import LocalPatientStore

    payload = _load(relative)
    store = LocalPatientStore()
    spike_dir = REPO_ROOT / "spike" / "spike_001" / "notes"
    index = DocumentIndex()
    for record in payload["notes"]:
        if record["corpus"] == "spike_001":
            text = (spike_dir / f"{record['document_id']}.txt").read_text("utf-8")
        else:
            text = store.get_document(record["document_id"]).text
        index.add(Document.from_text(record["document_id"], text))

    checked = 0
    for record in payload["notes"]:
        if not record.get("score"):
            continue
        spans = []
        for event in record["events"]:
            spans.append(event["span"])
            spans += [
                event[label]
                for label in ("bmi_span", "diet_span", "activity_span")
                if event.get(label)
            ]
        spans += [a["span"] for a in record["assertions"]]
        if record.get("current_bmi_span"):
            spans.append(record["current_bmi_span"])
        for raw in spans:
            assert validate(EvidenceSpan.model_validate(raw), index)
            checked += 1
    assert checked, "no spans were checked; the gate is vacuous"


@pytest.mark.parametrize(
    "relative, tool_fetch",
    [
        ("eval/extraction/adk_results_inline_vertex.json", False),
        ("eval/extraction/adk_results_tool_fetch_vertex.json", True),
    ],
)
def test_a_vertex_adk_recording_records_the_capability_that_ran(relative, tool_fetch):
    """The boolean that separates the two tiers' prompts, present and true.

    On Vertex the native path is available, so a recording claiming otherwise
    would mean the environment never reached ADK — the silent failure D106 was
    written about.
    """
    payload = _load(relative)
    assert payload["tool_fetch"] is tool_fetch
    assert payload["output_schema_and_tools"] is True


def test_the_vertex_verifier_answered_the_same_claims():
    """D106's choice, checked: the Vertex claims were enumerated from the AI
    Studio extraction, so the two recordings are keyed alike and the per-claim
    join the report prints is exact rather than approximate."""
    from pa_agent.verifier import claim_digest

    ai = {c["digest"] for c in _load("eval/verifier/results.json")["claims"]}
    vx_payload = _load("eval/verifier/results_vertex.json")
    vx = {c["digest"] for c in vx_payload["claims"]}
    assert vx == ai, "the tiers answered different claim sets; no join is possible"
    for record in vx_payload["claims"]:
        assert claim_digest(record["payload"]) == record["digest"]


# --- what Vertex actually measured (written after the round, D106) ---------


def test_the_vertex_extraction_figures_are_what_the_entry_records():
    """D106's result block, pinned to the artifacts. These are measurements,
    not thresholds: if a re-measurement moves them, this test and the entry
    move together or neither does (T-34's rule)."""
    expected = {
        "eval/extraction/results_vertex.json": (17, 169, 169),
        "eval/extraction/adk_results_inline_vertex.json": (17, 165, 165),
        "eval/extraction/adk_results_tool_fetch_vertex.json": (12, 76, 76),
    }
    for relative, (notes, emitted, anchored) in expected.items():
        scored = [n for n in _load(relative)["notes"] if n.get("score")]
        assert len(scored) == notes, relative
        assert sum(n["score"]["spans_emitted"] for n in scored) == emitted, relative
        assert sum(n["score"]["spans_anchored"] for n in scored) == anchored, relative


def test_the_model_offsets_are_still_unusable_on_the_second_tier():
    """D18's finding, reproduced a fourth time on a tier it had never been
    measured on. The model's own character offsets located nothing; every span
    in this repo is found by searching the verbatim quote."""
    for relative in VERTEX_EXTRACTIONS:
        scored = [n for n in _load(relative)["notes"] if n.get("score")]
        usable = sum(n["score"]["model_offsets_usable"] for n in scored)
        assert usable == 0, f"{relative}: {usable} usable offsets — D18 reverses"


def test_the_native_path_removed_the_injected_tool_round_trip():
    """D71's reversal clause, in the artifact rather than in prose.

    On AI Studio the tool-fetch run made ~2 tool calls per note — `read_note`
    plus the injected `set_model_response` — and emitted 4 unescaped spans. On
    Vertex the native path leaves exactly one `read_note` per note and no
    unescaped spans. That half of D71 reversed; the token overhead did not,
    and `eval/report.md` carries both halves.
    """
    ai = _load("eval/extraction/adk_results_tool_fetch.json")
    vx = _load("eval/extraction/adk_results_tool_fetch_vertex.json")
    scored = lambda p: [n for n in p["notes"] if n.get("score")]  # noqa: E731
    tools = lambda p: sum(  # noqa: E731
        len((n.get("trace") or {}).get("tool_calls") or []) for n in scored(p)
    )
    assert tools(ai) == 26 and tools(vx) == 12
    assert tools(vx) == len(scored(vx)), "one read_note per note, no second turn"
    assert sum(n["score"]["spans_unescaped"] for n in scored(ai)) == 4
    assert sum(n["score"]["spans_unescaped"] for n in scored(vx)) == 0
