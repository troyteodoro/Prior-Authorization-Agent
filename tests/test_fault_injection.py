"""T-29 — fault injection and the no-silent-failure audit (D76).

One test per REQ-23 trigger: the model call raises, the model returns an
unparseable payload, a span points past the end of its document, a predicate
raises. Each asserts the criterion resolves `ERROR` with the mapped
`error_code`, that no `Determination` is emitted (the library raises
`DeterminationAborted`, the CLI prints nothing to stdout), and that the CLI
exits non-zero with the criterion id and code on stderr (REQ-24, REQ-29).
The retryable fault errors only after the budget and the attempt count is
checked; the three terminal faults error on first occurrence with one call
spent (REQ-18a, D8).

**Contracts are T-26's** — `tests/test_error_state.py` owns the vocabulary and
its shape validators. This file owns production: who maps what, and what the
caller sees.

The audit at the bottom is REQ-27, parsed rather than grepped (D72, on D65's
and D67's precedent that substring scans are the gameable form).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from pa_agent import cli
from pa_agent.contracts import (
    CriterionVerdict,
    DeterminationAborted,
    ErrorCode,
)
from pa_agent.runners import (
    ExtractionFailure,
    ExtractionOutputError,
    RecordedExtractionRunner,
)
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore
from conftest import AcceptAllVerifier
from pa_agent.workflow import (
    DEFAULT_MAX_ATTEMPTS,
    ERROR_CODE_FOR,
    EXTRACTION_CRITERIA,
    RETRIEVAL_CRITERIA,
    _RETRYABLE_FAILURES,
    run_criteria_workflow,
)

EXTRACTION_RESULTS = REPO_ROOT / "eval" / "extraction" / "results.json"
NOTES_MANIFEST = REPO_ROOT / "data" / "patients" / "notes" / "manifest.json"
CONTRACTOR_CODE = "43775"
AS_OF = date(2026, 9, 1)


# --------------------------------------------------------------------------
# Fixtures — the same replay-backed chain the rest of the suite runs on
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def policy_store() -> LocalPolicyStore:
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def patient_store() -> LocalPatientStore:
    return LocalPatientStore()


@pytest.fixture(scope="module")
def recording() -> dict:
    assert EXTRACTION_RESULTS.exists(), (
        "no recording; run `python scripts/run_extraction.py` (it spends model "
        "calls). This suite replays it and spends none."
    )
    return json.loads(EXTRACTION_RESULTS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def runner(recording) -> RecordedExtractionRunner:
    return RecordedExtractionRunner.from_records(
        recording["notes"], model=recording["model"]
    )


@pytest.fixture(scope="module")
def ref(policy_store):
    resolved = policy_store.resolve(CONTRACTOR_CODE, "WA")
    assert resolved is not None
    return resolved


@pytest.fixture(scope="module")
def e1_patient() -> str:
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return next(
        record["patient_id"]
        for record in manifest["notes"]
        if "E1" in record["cases"]
    )


def _run(policy_store, patient_store, runner, patient_id, ref, **kwargs):
    kwargs.setdefault("verifier", AcceptAllVerifier())
    return run_criteria_workflow(
        policy_store=policy_store,
        patient_store=patient_store,
        extraction_runner=runner,
        policy_ref=ref,
        patient_id=patient_id,
        as_of=AS_OF,
        **kwargs,
    )


# --------------------------------------------------------------------------
# The injected faults
# --------------------------------------------------------------------------


class _RaisingRunner:
    """Fails the same way every time, and counts how often it was asked."""

    name = "raising"

    def __init__(self, failure: ExtractionFailure) -> None:
        self.failure = failure
        self.attempts = 0

    def run(self, document_id: str, text: str):
        self.attempts += 1
        raise ExtractionOutputError(self.failure, "injected fault")


class _TamperingRunner:
    """Replays the recording, then pushes every event span past the note's end.

    The runner conforms to the `ExtractionRunner` protocol, which is the point:
    `build_result()`'s anchoring never sees these offsets, so only the
    workflow's own span pass stands between them and a citation a reviewer
    cannot slice (D76).
    """

    name = "tampering"

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0

    def run(self, document_id: str, text: str):
        self.calls += 1
        result = self._inner.run(document_id, text)
        result.events[:] = [
            event.model_copy(
                update={
                    "span": event.span.model_copy(
                        update={"char_end": len(text) + 64}
                    )
                }
            )
            for event in result.events
        ]
        return result


# --------------------------------------------------------------------------
# Failure point 1: the model call raises — retryable, errors at exhaustion
# --------------------------------------------------------------------------


def test_a_raising_model_call_errors_after_the_budget_and_the_count_is_checked(
    policy_store, patient_store, ref, e1_patient, monkeypatch, capsys
) -> None:
    raising = _RaisingRunner(ExtractionFailure.CALL_FAILED)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, raising, e1_patient, ref)

    assert raising.attempts == DEFAULT_MAX_ATTEMPTS, (
        "a retryable fault must consume the whole budget before it errors "
        "(REQ-18a)"
    )
    assert caught.value.attempts == DEFAULT_MAX_ATTEMPTS
    assert [r.criterion_id for r in caught.value.results] == list(
        EXTRACTION_CRITERIA
    ), "an extraction failure errors the extraction-consuming criteria (D76)"
    for result in caught.value.results:
        assert result.verdict is CriterionVerdict.ERROR
        assert result.error_code is ErrorCode.MODEL_CALL_FAILED
        assert result.error_detail

    # The same fault through the CLI's real handler chain, in process. The
    # workflow raised, so no `Determination` exists to print: stdout is empty
    # and stderr names every errored criterion with its code (REQ-29).
    fresh = _RaisingRunner(ExtractionFailure.CALL_FAILED)
    monkeypatch.setattr(cli, "_build_runner", lambda *args, **kwargs: fresh)
    rc = cli.main(["--patient", e1_patient, "--procedure", CONTRACTOR_CODE])
    out, err = capsys.readouterr()
    assert rc == 3
    assert out == "", "no Determination is emitted over an ERROR (REQ-24)"
    assert "MODEL_CALL_FAILED" in err
    for criterion_id in EXTRACTION_CRITERIA:
        assert f"criterion {criterion_id}" in err
    assert f"attempts: {DEFAULT_MAX_ATTEMPTS}" in err
    assert fresh.attempts == DEFAULT_MAX_ATTEMPTS


# --------------------------------------------------------------------------
# Failure point 2: an unparseable payload — terminal, one attempt, and the
# one fault reachable through the real CLI subprocess
# --------------------------------------------------------------------------


def test_an_unparseable_payload_errors_terminally_on_one_attempt(
    policy_store, patient_store, ref, e1_patient
) -> None:
    raising = _RaisingRunner(ExtractionFailure.UNPARSEABLE)
    with pytest.raises(DeterminationAborted) as caught:
        _run(
            policy_store, patient_store, raising, e1_patient, ref,
            max_attempts=5,
        )
    assert raising.attempts == 1, (
        "the same prompt returns the same invalid response; retrying it spends "
        "money to reach the same answer (D8)"
    )
    assert caught.value.attempts == 1
    assert {r.error_code for r in caught.value.results} == {
        ErrorCode.SCHEMA_INVALID
    }


def test_a_corrupted_payload_reaches_exit_three_through_the_real_cli(
    tmp_path, recording, e1_patient
) -> None:
    """The subprocess proof: `--recording` is the CLI's own injection port, so
    this one runs the shipped entry point end to end rather than an in-process
    stand-in. The corrupted note fails schema validation in `build_result`,
    which the recorded runner classifies terminal."""
    corrupted = json.loads(json.dumps(recording))
    target = next(
        record
        for record in corrupted["notes"]
        if record["document_id"].startswith(e1_patient)
    )
    target["raw"] = {"wm_events": "not a list", "program_assertions": None}
    path = tmp_path / "corrupted_recording.json"
    path.write_text(json.dumps(corrupted), encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable, "-m", "pa_agent.cli",
            "--patient", e1_patient,
            "--procedure", CONTRACTOR_CODE,
            "--recording", str(path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 3, proc.stderr
    assert proc.stdout == "", "no Determination is emitted over an ERROR (REQ-24)"
    assert "SCHEMA_INVALID" in proc.stderr
    for criterion_id in EXTRACTION_CRITERIA:
        assert f"criterion {criterion_id}" in proc.stderr


# --------------------------------------------------------------------------
# Failure point 3: a span past the end of the document — terminal
# --------------------------------------------------------------------------


def test_a_span_past_the_document_end_errors_on_first_occurrence(
    policy_store, patient_store, runner, ref, e1_patient, monkeypatch, capsys
) -> None:
    tampering = _TamperingRunner(runner)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, tampering, e1_patient, ref)

    assert tampering.calls == 1
    assert caught.value.attempts is None, (
        "no model call failed; the fault is downstream of extraction"
    )
    [errored] = caught.value.results
    assert errored.verdict is CriterionVerdict.ERROR
    assert errored.error_code is ErrorCode.SPAN_VALIDATION_FAILED
    assert errored.criterion_id in EXTRACTION_CRITERIA
    assert "OUT_OF_RANGE" in (errored.error_detail or "")

    monkeypatch.setattr(
        cli,
        "_build_runner",
        lambda *args, **kwargs: _TamperingRunner(runner),
    )
    rc = cli.main(["--patient", e1_patient, "--procedure", CONTRACTOR_CODE])
    out, err = capsys.readouterr()
    assert rc == 3
    assert out == ""
    assert "SPAN_VALIDATION_FAILED" in err


# --------------------------------------------------------------------------
# Failure point 4: a predicate raises — terminal, names its own criterion
# --------------------------------------------------------------------------


def test_a_raising_predicate_errors_its_criterion_and_only_its_criterion(
    policy_store, patient_store, runner, ref, e1_patient, monkeypatch, capsys
) -> None:
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("injected predicate fault")

    monkeypatch.setattr("pa_agent.workflow.evaluate_c1", boom)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, runner, e1_patient, ref)

    assert calls["n"] == 1, "a predicate exception is terminal (D8): no retry"
    assert caught.value.attempts is None
    [errored] = caught.value.results
    assert errored.criterion_id == "c1", (
        "the fault belongs to the criterion whose predicate raised, not to all "
        "of them (D76)"
    )
    assert errored.verdict is CriterionVerdict.ERROR
    assert errored.error_code is ErrorCode.PREDICATE_EXCEPTION
    assert "RuntimeError" in (errored.error_detail or "")
    assert "injected predicate fault" in (errored.error_detail or "")

    # Through the CLI: the default recorded runner replays for free, and the
    # monkeypatched predicate is the only fault in the chain.
    rc = cli.main(["--patient", e1_patient, "--procedure", CONTRACTOR_CODE])
    out, err = capsys.readouterr()
    assert rc == 3
    assert out == ""
    assert "criterion c1" in err
    assert "PREDICATE_EXCEPTION" in err


# --------------------------------------------------------------------------
# The mapping has one source of truth
# --------------------------------------------------------------------------


def test_the_mapping_is_total_and_retryability_follows_req30() -> None:
    """Every `ExtractionFailure` maps, and the retry loop's classification is
    the mapped code's own (REQ-30) — deleting a row or widening the retryable
    set is a visible diff here, not a KeyError three modules away."""
    assert set(ERROR_CODE_FOR) == set(ExtractionFailure), (
        "an ExtractionFailure without a mapped ErrorCode aborts with a "
        "KeyError instead of an ERROR"
    )
    assert _RETRYABLE_FAILURES == ("CALL_FAILED",)
    for failure, code in ERROR_CODE_FOR.items():
        assert (failure.value in _RETRYABLE_FAILURES) == code.retryable


# --------------------------------------------------------------------------
# REQ-27: the no-silent-failure audit, parsed rather than grepped
# --------------------------------------------------------------------------

#: The broad handlers allowed to catch without raising, compared exactly in
#: both directions — a stale entry fails the same as a missing one, which is
#: what keeps this list from absorbing whatever is convenient (D76). Keyed by
#: (path relative to the repo root, enclosing function).
_ALLOWED_BROAD_SWALLOWS = {
    ("pa_agent/agent/extraction_agent.py", "before_model_callback"):
        "recorder hook: notes its own failure on `failures`, never kills the run",
    ("pa_agent/agent/extraction_agent.py", "after_model_callback"):
        "recorder hook: notes its own failure on `failures`, never kills the run",
    ("pa_agent/agent/extraction_agent.py", "before_tool_callback"):
        "recorder hook: notes its own failure on `failures`, never kills the run",
    ("pa_agent/agent/extraction_agent.py", "after_tool_callback"):
        "recorder hook: notes its own failure on `failures`, never kills the run",
    ("pa_agent/agent/extraction_agent.py", "on_tool_error_callback"):
        "recorder hook: notes its own failure on `failures`, never kills the run",
    ("pa_agent/agent/extraction_agent.py", "_invoke"):
        "returns the failure classified; the first turn raises it, the re-ask "
        "records it (D103)",
    ("pa_agent/extraction.py", "reask_turn"):
        "the re-ask never raises: a transport fault is recorded classified on "
        "`reask.error` and the trace, and the first turn's result stands (D103)",
    ("pa_agent/agent/retrieval_agent.py", "_run"):
        "budget loop: stores the failure, re-raises classified at exhaustion",
}


def _except_handlers() -> list[tuple[str, str, int, list[str], bool, bool]]:
    """Every `except` under `pa_agent/`:
    (path, function, line, caught, raises, acts) — `acts` is whether the body
    contains any call at all, so a handler that neither raises nor does
    anything is distinguishable from one that records what it caught."""
    found: list[tuple[str, str, int, list[str], bool, bool]] = []
    for path in sorted((REPO_ROOT / "pa_agent").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(REPO_ROOT))
        stack: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def visit_FunctionDef(self, node) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ExceptHandler(self, node) -> None:
                if node.type is None:
                    caught = ["<bare>"]
                elif isinstance(node.type, ast.Tuple):
                    caught = [ast.unparse(e) for e in node.type.elts]
                else:
                    caught = [ast.unparse(node.type)]
                raises = any(
                    isinstance(inner, ast.Raise) for inner in ast.walk(node)
                )
                acts = any(
                    isinstance(inner, ast.Call) for inner in ast.walk(node)
                )
                found.append(
                    (
                        rel,
                        stack[-1] if stack else "<module>",
                        node.lineno,
                        caught,
                        raises,
                        acts,
                    )
                )
                self.generic_visit(node)

        _Visitor().visit(tree)
    return found


def test_no_except_handler_under_pa_agent_is_bare() -> None:
    bare = [
        f"{path}:{line} in {function}"
        for path, function, line, caught, _, _ in _except_handlers()
        if caught == ["<bare>"]
    ]
    assert bare == [], f"bare except clauses (REQ-27): {bare}"


def test_every_broad_handler_raises_or_is_allowlisted_with_a_reason() -> None:
    """REQ-27 as written: every handler either re-raises or maps to a named
    code. A handler catching `Exception` without a `raise` in its body is a
    swallow unless this file names it and says why. Parsed, not grepped: the
    exit was rewritten by D72 because a substring scan is satisfiable by a
    comment (D65, D67)."""
    handlers = _except_handlers()
    broad = {
        (path, function)
        for path, function, _, caught, raises, _ in handlers
        if not raises
        and any(name in ("Exception", "BaseException") for name in caught)
    }
    unexplained = broad - set(_ALLOWED_BROAD_SWALLOWS)
    stale = set(_ALLOWED_BROAD_SWALLOWS) - broad
    assert unexplained == set(), (
        f"broad except handlers that neither raise nor appear in the "
        f"allowlist: {sorted(unexplained)}"
    )
    assert stale == set(), (
        f"allowlist entries no code matches — remove them so the list stays "
        f"the count: {sorted(stale)}"
    )
    # An allowlist entry is a claim that the handler maps the failure to a
    # named form. A body that calls nothing cannot be doing that, so a hook
    # quietly reverted to `pass` fails here even though its name still appears
    # above.
    inert = [
        f"{path}:{line} in {function}"
        for path, function, line, caught, raises, acts in handlers
        if not raises
        and not acts
        and any(name in ("Exception", "BaseException") for name in caught)
        and (path, function) in _ALLOWED_BROAD_SWALLOWS
    ]
    assert inert == [], (
        f"allowlisted handlers whose bodies do nothing at all: {inert}"
    )


# --------------------------------------------------------------------------
# Failure point 5: the planner itself (T-77, D90)
#
# T-29 mapped every extraction fault and did not reach `step_gather`.
# `AgenticRetrievalPlanner` makes a model call and its `RetrievalError`
# propagated uncaught through `determine()` to the CLI, which crashed with
# Python's exit 1 — the code for a bad request, which is the collapse REQ-29 and
# Article IV forbid. The deterministic planner cannot raise it, which is exactly
# why nothing caught it.
# --------------------------------------------------------------------------


class _RaisingPlanner:
    """Fails the way `AgenticRetrievalPlanner` can, and counts the asks."""

    def __init__(self, message: str = "the model returned no usable bundle") -> None:
        self.message = message
        self.attempts = 0

    def gather(self, patient_id, tree, patient_store, policy_store):
        from pa_agent.retrieval import RetrievalError

        self.attempts += 1
        raise RetrievalError(self.message)


def test_retrieval_failure_errors_every_criterion_and_aborts(
    policy_store, patient_store, runner, e1_patient, ref
):
    """D90's ruling, asserted: **all seven**, not the extraction-consuming five.

    `step_gather` produces the entire evidentiary input. Criterion (a) has no
    observations to compare, (b) no conditions and no value set, c1–c5 no notes.
    Reporting the fault on five of seven would state that (a) and (b) were
    evaluated, and they were not.
    """
    planner = _RaisingPlanner()
    with pytest.raises(DeterminationAborted) as caught:
        _run(
            policy_store,
            patient_store,
            runner,
            e1_patient,
            ref,
            planner=planner,
        )

    assert planner.attempts == 1, (
        "step_gather has no retry loop and D90 did not add one: a second model "
        "call nothing has measured is not a fix"
    )
    assert caught.value.attempts == 1
    assert [r.criterion_id for r in caught.value.results] == list(
        RETRIEVAL_CRITERIA
    ), "nothing was gathered, so no criterion was evaluated (D90)"
    assert list(RETRIEVAL_CRITERIA) == ["a", "b", "c1", "c2", "c3", "c4", "c5"]

    for result in caught.value.results:
        assert result.verdict is CriterionVerdict.ERROR
        assert result.error_code is ErrorCode.SOURCE_UNAVAILABLE
        assert planner.message in result.error_detail


def test_a_retrieval_failure_is_an_error_and_never_an_abstention(
    policy_store, patient_store, runner, e1_patient, ref
):
    """Article IV, at the one place it is most tempting to collapse.

    `INSUFFICIENT_EVIDENCE` with `NO_EVIDENCE_RETRIEVED` would be a well-formed
    answer that reads almost right, and it says *the chart does not say* when
    what happened is *the system did not look*. `RetrievalError`'s own docstring
    refuses the mirror of this — a planner returning an empty bundle instead of
    raising — and answering it as an abstention downstream would undo that at
    the other end of the same wire.
    """
    with pytest.raises(DeterminationAborted) as caught:
        _run(
            policy_store,
            patient_store,
            runner,
            e1_patient,
            ref,
            planner=_RaisingPlanner(),
        )

    for result in caught.value.results:
        assert result.verdict is not CriterionVerdict.INSUFFICIENT_EVIDENCE
        assert result.gap_reason is None, (
            "an ERROR carries a code and no gap_reason; a gap_reason would make "
            "it read as an abstention (Article IV, D7, D9)"
        )
        assert result.spans == []


def test_a_retrieval_failure_reaches_the_cli_as_exit_three(
    monkeypatch, e1_patient, capsys
):
    """The defect, stated as it was found: before this the CLI crashed with
    Python's exit 1, indistinguishable from an unknown patient (REQ-29)."""
    from pa_agent import workflow as workflow_module

    planner = _RaisingPlanner()
    monkeypatch.setattr(workflow_module, "FixedRetrievalPlanner", lambda: planner)

    rc = cli.main(["--patient", e1_patient, "--procedure", CONTRACTOR_CODE])
    out, err = capsys.readouterr()

    assert rc == 3, "a fault is exit 3; exit 1 is a bad request (REQ-29, D76)"
    assert out == "", "no Determination is emitted over an ERROR (REQ-24)"
    assert "SOURCE_UNAVAILABLE" in err
    for criterion_id in RETRIEVAL_CRITERIA:
        assert f"criterion {criterion_id}" in err


def test_the_two_fault_mappings_share_one_builder(script_source=None):
    """D90: two independent fault-mapping sites are two things free to disagree
    about what an abort looks like. Parsed, because a second builder that
    happened to produce identical results would pass every test above."""
    source = (REPO_ROOT / "pa_agent" / "workflow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    builders = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.endswith("_error_results")
    ]
    assert builders == ["_error_results"], (
        f"{builders} build ERROR result lists; one builder, and which criteria "
        "it covers is the caller's ruling (D76, D90)"
    )


# --------------------------------------------------------------------------
# T-86 (D99): an under-cited NOT_MET is a fault, never an abstention
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def e6_patient() -> str:
    """E6: BMI documented in two of four months, so c4 is NOT_MET with two
    deficient months — the shape an under-citation can be injected into."""
    manifest = json.loads(NOTES_MANIFEST.read_text(encoding="utf-8"))
    return next(
        record["patient_id"]
        for record in manifest["notes"]
        if "E6" in record["cases"]
    )


def test_an_under_cited_not_met_errors_its_criterion_and_reaches_exit_three(
    policy_store, patient_store, runner, ref, e6_patient, monkeypatch, capsys
) -> None:
    """The predicate answers correctly and cites only the first deficient
    month. The verdict alone would re-derive from that one month; the
    shortfall says two were claimed. Article IV: the criterion is `ERROR`,
    not an abstention — the specialist has nothing to collect, the code
    cited less than it counted."""
    from pa_agent import criteria

    real = criteria.evaluate_c4

    def under_cite(*args, **kwargs):
        result = real(*args, **kwargs)
        if result.verdict is CriterionVerdict.NOT_MET:
            return result.model_copy(update={"spans": result.spans[:1]})
        return result

    monkeypatch.setattr("pa_agent.workflow.evaluate_c4", under_cite)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, runner, e6_patient, ref)

    [errored] = caught.value.results
    assert errored.criterion_id == "c4"
    assert errored.verdict is CriterionVerdict.ERROR
    assert errored.error_code is ErrorCode.PREDICATE_EXCEPTION
    assert "CitationInsufficient" in (errored.error_detail or "")
    assert errored.gap_reason is None, "a fault names no next action (Art. IV)"

    rc = cli.main(["--patient", e6_patient, "--procedure", CONTRACTOR_CODE])
    out, err = capsys.readouterr()
    assert rc == 3 and out == ""
    assert "criterion c4" in err and "PREDICATE_EXCEPTION" in err


def test_the_honest_citation_on_the_same_patient_passes(
    policy_store, patient_store, runner, ref, e6_patient
) -> None:
    run = _run(policy_store, patient_store, runner, e6_patient, ref)
    c4 = next(r for r in run.determination.criterion_results if r.criterion_id == "c4")
    assert c4.verdict is CriterionVerdict.NOT_MET
    assert c4.shortfall is not None and c4.shortfall.observed == 2
    assert "sufficiency" in run.steps


# --------------------------------------------------------------------------
# T-87 (D100, D101): faults under the second tree name what that tree declares
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def palmetto_ref(policy_store):
    resolved = policy_store.resolve(CONTRACTOR_CODE, "AL")
    assert resolved is not None and resolved.policy_version_id == "ncd-100.1-jjm-v1"
    return resolved


def test_a_retrieval_failure_under_palmetto_errors_every_declared_criterion(
    policy_store, patient_store, runner, e1_patient, palmetto_ref
):
    """D90 under D101: all seven of *this* tree's criteria, `d` included —
    the unclaimed criterion was not evaluated either, and nothing was."""
    planner = _RaisingPlanner()
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, runner, e1_patient, palmetto_ref, planner=planner)
    assert [r.criterion_id for r in caught.value.results] == ["a", "b", "c1", "c2", "c4", "c5", "d"]
    assert {r.error_code for r in caught.value.results} == {ErrorCode.SOURCE_UNAVAILABLE}


def test_an_extraction_failure_under_palmetto_errors_only_the_criteria_that_read_the_note(
    policy_store, patient_store, e1_patient, palmetto_ref
):
    """D76 under D101: the extraction-consuming criteria the tree declares
    for deterministic evaluation — c1, c2 and c5. Not c3, which this tree
    has no such criterion as; not c4 or d, which were never going to read
    the model's output; not (a) or (b), which read structured FHIR."""
    raising = _RaisingRunner(ExtractionFailure.UNPARSEABLE)
    with pytest.raises(DeterminationAborted) as caught:
        _run(policy_store, patient_store, raising, e1_patient, palmetto_ref)
    assert [r.criterion_id for r in caught.value.results] == ["c1", "c2", "c5"]
