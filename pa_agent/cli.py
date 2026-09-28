"""T-25 — the CLI: request in, determination out. *Extended by T-18/T-62 (D62).*

    python -m pa_agent.cli --patient X --procedure 43842
    python -m pa_agent.cli --patient <uuid> --procedure 43775
    python -m pa_agent.cli --patient <uuid> --procedure 43775 --extraction adk
    python -m pa_agent.cli session packet <session-id>

Prints one JSON document to stdout. For a determination it carries the outcome,
the `policy_version_id` (REQ-4), the coverage claim it cites where there is one
(Art. III), the gap list (REQ-21), REQ-39's discrepancies, and the model-call
counter — computed properties, added explicitly because `model_dump` only
serializes fields. For a code no policy governs it prints a `NO_POLICY_FOUND`
result and still exits zero: a deterministic answer is not an error (D32).

**This module is where every path is named and every port is constructed** — the
four stores, the extraction runner, the verifier, the quote runner and the
document index a packet's citations are validated against — built over **all
three** hashed corpora, because a criterion cites a chart, a coverage claim
cites the policy corpus and an accepted suggestion cites an FDA label (REQ-41,
REQ-52, REQ-74, D133). Nothing below it holds a path or a credential, which is
what makes the production adapters D25 wants a second implementation rather than
a rewrite. It
is also where every value that cannot be derived comes from: the clock (`_now`)
and, until `T-105` builds the payer directory, the packet's recipient
(`PLACEHOLDER_PAYER`) — D127's rule, D131's application of it.

`--extraction` picks the model leaf, and the default is the honest one:

    recorded  replay `eval/extraction/results.json`. Zero model calls, so the
              same request answers the same way twice, which is what makes this
              a demo path rather than a bill.
    direct    `google-genai` with a native response schema — D45's measured
              configuration.
    adk       `google-adk` 2.8.0, with declared tools. A different call
              configuration and therefore a different measurement (T-63).

Exit codes: 0 for an answer, 1 for a request the stores cannot resolve (an
unknown patient), 2 for a path the system has not built yet — the
`NotImplementedError` message, which names what is missing, goes to stderr —
and 3 for a determination aborted over a criterion in `ERROR` (REQ-24, T-29,
D76): one stderr line per errored criterion carrying the criterion id and its
`error_code` (REQ-29), and nothing on stdout.

*Exit 2 currently has no reachable route from this entry point*: T-18 and T-19
built the criteria path, and T-17 built the verifier this module now always
supplies beside the extraction runner. The handler is kept because it is how
the next unbuilt path reports itself instead of crashing.
`tests/test_determination.py` asserts the mapping directly rather than through
a subprocess that can no longer trigger it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from pa_agent import (
    form,
    history,
    intake as intake_module,
    session as session_machine,
)
from pa_agent.contracts import (
    Determination,
    DeterminationAborted,
    ReviewAction,
    ReviewEntry,
    Session,
    SessionRun,
    SessionState,
)
from pa_agent.determination import NoJurisdictionResult, NoPolicyResult, determine
from pa_agent.index import DocumentIndex
from pa_agent.stores.session import (
    DEFAULT_SESSION_ROOT,
    LocalSessionStore,
    SessionExists,
    SessionNotFound,
)
from pa_agent.quotes import RecordedQuoteRunner
from pa_agent.runners import RecordedExtractionRunner
from pa_agent.verifier import RecordedVerifierRunner
from pa_agent.stores.knowledge import LocalKnowledgeStore
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.model_pin import MEASURED_TIER
from pa_agent.stores.policy import LocalPolicyStore
from pa_agent.tiers import TIERS

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RECORDING = REPO_ROOT / "eval" / "extraction" / "results.json"
DEFAULT_VERIFIER_RECORDING = REPO_ROOT / "eval" / "verifier" / "results.json"
DEFAULT_QUOTE_RECORDING = REPO_ROOT / "eval" / "history" / "results.json"
ENV_PATH = REPO_ROOT / "pa_agent" / "agent" / ".env"

#: What `--suggest` emits where no tree governs the request. Never a
#: `suggestions` key: an empty list is a claim about the chart, and the true
#: statement is that nothing selected a tree to look under (REQ-64, D90, D123).
NO_GOVERNING_TREE = {
    "computed": False,
    "reason": "NO_GOVERNING_TREE",
    "note": "the request resolved before a criteria tree was selected, so there "
            "is no policy version to review under and no value set for "
            "would_affect to test membership in. This is not an empty review "
            "(D123)",
}


def _suggestion_block(result, review) -> dict:
    """What `--suggest` emits for one result. The only place it is decided.

    A review where a tree governs the request, and a **declared decline**
    where none does — never an empty suggestion list, because an empty list
    says *the chart implies nothing*, a claim about the chart, where the true
    statement is that nothing selected a tree to look under (D90, D123).

    `history.review_scope` is the predicate, shared with `eval/run_eval.py`
    so the two cannot disagree about a chart (D126).
    """
    if history.review_scope(result) is None:
        return dict(NO_GOVERNING_TREE)
    return review.model_dump(mode="json")


def _render(
    result: Determination | NoPolicyResult | NoJurisdictionResult,
    block: dict | None = None,
) -> dict:
    """The one JSON document. A **pure renderer**: it is handed the
    suggestion block and never decides one (D126).

    Kept pure because `session run` prints a determination through this same
    function in v1.4 (T-102), and a renderer that builds its own side content
    is one the second caller has to reimplement or work around.
    """
    if isinstance(result, NoJurisdictionResult):
        rendered = {
            "result": "NO_JURISDICTION_TREE",
            "procedure_code": result.procedure_code,
            "state": result.state,
            "known_states": list(result.known_states),
            "note": "no criteria tree in the store governs this state; this is "
                    "not a denial and not a bad request (REQ-55, D100)",
        }
        if block is not None:
            rendered["icd_suggestions"] = block
        return rendered
    if isinstance(result, NoPolicyResult):
        rendered = {
            "result": "NO_POLICY_FOUND",
            "procedure_code": result.procedure_code,
            "note": "no policy in the store governs this code; this is not a "
                    "denial (REQ-1, D26)",
        }
        if block is not None:
            rendered["icd_suggestions"] = block
        return rendered
    rendered = result.model_dump(mode="json")
    rendered["model_calls"] = result.model_calls
    rendered["gap_list"] = [g.model_dump(mode="json") for g in result.gap_list]
    # REQ-39: advisory, and a separate list from the gap list on purpose — a
    # disagreement between two recorded values is not something to go collect,
    # so it must not read as a gap (D14).
    rendered["discrepancies"] = [
        d.model_dump(mode="json") for d in result.discrepancies
    ]
    rendered["total_input_tokens"] = result.total_input_tokens
    rendered["total_output_tokens"] = result.total_output_tokens
    rendered["total_wall_time_ms"] = round(result.total_wall_time_ms, 1)
    # REQ-65: a suggestion is never a code assignment and changes no verdict.
    # It rides beside them, in its own block, and every key above is
    # byte-identical to the run without --suggest (D119, D123).
    if block is not None:
        rendered["icd_suggestions"] = block
    return rendered


def _load_env() -> None:
    """Read `pa_agent/agent/.env` for a live run. Gitignored, placeholder-only
    in the tracked tree (working rule 10)."""
    if not ENV_PATH.exists():
        raise SystemExit(
            f"no {ENV_PATH.relative_to(REPO_ROOT)}; a live extraction run needs "
            "GOOGLE_API_KEY. Use --extraction recorded to answer from T-15's "
            "recording for nothing."
        )
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_runner(
    mode: str, recording: Path, tool_fetch: bool, patient_store, tier: str
):
    """Construct the model leaf. The only place a runner is chosen (REQ-52)."""
    if mode == "recorded":
        if not recording.exists():
            raise SystemExit(
                f"no recording at {recording}; run "
                "`python scripts/run_extraction.py` (it spends model calls) or "
                "pass --extraction direct"
            )
        payload = json.loads(recording.read_text(encoding="utf-8"))
        return RecordedExtractionRunner.from_records(
            payload["notes"], model=payload.get("model")
        )

    _load_env()
    from pa_agent.tiers import client_for

    client = client_for(tier)

    if mode == "direct":
        from pa_agent.runners import DirectExtractionRunner

        return DirectExtractionRunner(client)

    # Imported here, not at module scope: `pa_agent.agent` is the subpackage that
    # holds the ADK, and pulling it into every CLI invocation would put
    # `google.adk` on the import path of a request that answers E3 with no model
    # at all (D16's boundary, D62).
    from pa_agent.agent.extraction_agent import AdkExtractionRunner

    return AdkExtractionRunner(
        client=client, patient_store=patient_store, tool_fetch=tool_fetch
    )



def _build_verifier(mode: str, recording: Path, tier: str):
    """Construct Article V's checker beside the model leaf (T-17, D78).

    It follows `--extraction`: the recorded leaf gets the recorded verifier —
    zero calls, same answer twice — and a live leaf gets a live verifier,
    because live extraction produces claims no recording has seen and a replay
    would refuse them (D31: a miss raises, never defaults).
    """
    if mode == "recorded":
        if not recording.exists():
            raise SystemExit(
                f"no verifier recording at {recording}; run "
                "`python scripts/run_verifier_measurement.py` (it spends model "
                "calls) or pass --extraction direct"
            )
        payload = json.loads(recording.read_text(encoding="utf-8"))
        return RecordedVerifierRunner.from_records(
            payload["claims"], model=payload.get("model")
        )

    _load_env()
    from pa_agent.tiers import client_for

    from pa_agent.verifier import LiveVerifierRunner

    return LiveVerifierRunner(client_for(tier))


def _build_quote_runner(recording: Path):
    """Construct the review's model leaf (T-98, D122; wired here by D126).

    Recorded only. `LocalPatientStore` serves the fourteen committed bundles
    and T-98 measured every one of their notes, so no chart this entry point
    can be given has a note the recording lacks — `--suggest` spends nothing,
    always. A missing recording is a `SystemExit` naming the script rather than
    a `None` that would make `run_review` raise on a note-bearing chart, and
    rather than a live runner that would silently spend money (D31, D90).
    """
    if not recording.exists():
        raise SystemExit(
            f"no quote recording at {recording}; run "
            "`python scripts/run_quote_measurement.py` (it spends model calls). "
            "--suggest replays it and never calls a model."
        )
    payload = json.loads(recording.read_text(encoding="utf-8"))
    return RecordedQuoteRunner.from_records(
        payload["notes"], payload["rows_asked"], model=payload.get("model")
    )


def _review(result, patient_id: str, policy_store, patient_store, knowledge_store):
    """The medical-history review beside the determination (T-97, T-98).

    Built here and not inside `determine()`: a suggestion enters no verdict,
    no span and no gap entry, so there is no field through which it could
    reach one (REQ-65, D119). The value sets are the governing tree's, which
    is what `would_affect` tests membership in (REQ-66).
    """
    tree = policy_store.get_tree(result.policy_version_id)
    value_sets = {}
    for criterion in tree.criteria:
        constant = criterion.constants.get(history.VALUE_SET_CONSTANT)
        if constant is not None:
            value_sets[str(constant.value)] = policy_store.get_value_set(
                str(constant.value)
            )
    rows = knowledge_store.get_medication_effect_rows()
    return history.run_review(
        quote_runner=_build_quote_runner(DEFAULT_QUOTE_RECORDING),
        verifier=None,
        patient_id=patient_id,
        policy_version_id=tree.policy_version_id,
        rows=rows,
        products={
            row.ingredient.code: knowledge_store.get_ingredient_products(
                row.ingredient.code
            )
            for row in rows
        },
        medications=patient_store.get_medications(patient_id),
        conditions=patient_store.get_conditions(patient_id),
        observations=patient_store.get_observations(patient_id),
        criteria=tree.criteria,
        value_sets=value_sets,
        notes=patient_store.get_notes(patient_id),
    )


def _determine_or_report(
    policy_store, procedure, *, patient_id, patient_store, as_of, runner,
    verifier, state,
):
    """One determination, or the exit code its fault maps to.

    Returns `(code, result)` — `result` is `None` exactly when `code` is
    non-zero. Shared by the bare form and `session run` (D129): two copies of
    this block would be two answers to *what does a retrieval fault exit with*,
    and the four codes are a contract this module publishes.
    """
    try:
        result = determine(
            policy_store,
            procedure,
            patient_id=patient_id,
            patient_store=patient_store,
            as_of=as_of,
            extraction_runner=runner,
            verifier=verifier,
            state=state,
        )
    except NotImplementedError as exc:
        # The message names what is missing (D27's pattern).
        print(str(exc), file=sys.stderr)
        return 2, None
    except DeterminationAborted as exc:
        # REQ-29 (T-29, D76): the criterion id and its `error_code` go to
        # stderr, one line per errored criterion, and nothing goes to stdout —
        # a partial answer printed anyway would be a determination emitted
        # over an `ERROR` with extra steps (REQ-24, REQ-26).
        for errored in exc.results:
            code = (
                errored.error_code.value if errored.error_code else "UNCLASSIFIED"
            )
            print(
                f"criterion {errored.criterion_id}: {code}: "
                f"{errored.error_detail}",
                file=sys.stderr,
            )
        if exc.attempts is not None:
            print(f"attempts: {exc.attempts}", file=sys.stderr)
        return 3, None
    except KeyError as exc:
        # A request the stores cannot resolve — an unknown patient, most
        # likely. A bad request is not an answer and not an unbuilt path.
        print(f"bad request: {exc.args[0]}", file=sys.stderr)
        return 1, None
    return 0, result


def _block_for(result, patient_id, policy_store, patient_store, knowledge_store,
               suggest: bool):
    """The `--suggest` block, or `None` when the flag was not passed.

    Shared for `_render`'s sake: the flag-off document must be byte-identical
    to the one this module printed before `--suggest` existed (REQ-65, D126).
    """
    if not suggest:
        return None
    review = None
    if history.review_scope(result) is not None:
        review = _review(
            result, patient_id, policy_store, patient_store, knowledge_store
        ).review
    return _suggestion_block(result, review)


#: The verbs `session` takes. A literal set, tested against the subparsers the
#: session parser declares, because dispatching on "any first positional" would
#: make a typo a session command (D129).
SESSION_VERBS = ("create", "list", "show", "run", "review", "packet")


def _now() -> str:
    """The clock, named once and only here.

    `pa_agent/session.py` has no clock and `stores/session.py` generates
    nothing — a `datetime.now()` inside a serializer makes a round trip pass on
    the first write and fail on the second (D127). The composition root is
    where a value that cannot be derived comes from.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_store(root: Path | None) -> LocalSessionStore:
    """The fourth store, constructed here like the other three (REQ-41)."""
    return LocalSessionStore(root)


def _intake_from(args) -> "intake_module.Intake":
    """One `Intake` from whichever route the caller used.

    Reading the file is **this module's**: `pa_agent/intake.py` names no path
    and opens nothing, because `tests/test_planes.py` refuses storage names
    outside `pa_agent/stores/` and the composition root is where every location
    is named (D128, REQ-41).
    """
    if args.intake is not None:
        try:
            text = args.intake.read_text(encoding="utf-8")
        except OSError as exc:
            raise intake_module.MalformedIntake(
                f"cannot read intake {args.intake}: {exc}"
            ) from exc
        return intake_module.from_json(text)
    if args.patient is None or args.procedure is None:
        raise intake_module.MalformedIntake(
            "session create needs --intake, or --patient and --procedure "
            "together; a session with no request is not a session"
        )
    return intake_module.from_flags(
        patient=args.patient,
        procedure=args.procedure,
        state=args.state,
        icd10=args.icd10,
        requesting_provider=args.requesting_provider,
        servicing_provider=args.servicing_provider,
    )


def _summarise(session: Session) -> dict:
    """One row of `session list`: ids and counts, never a determination.

    The checklist v2.2's dashboard draws (D125), as text. It carries what the
    specialist scans by — what was asked, where it got to, how many snapshots —
    and `session show` is what carries the answer itself.
    """
    return {
        "session_id": session.session_id,
        "state": session.state.value,
        "created_at": session.created_at,
        "patient_id": session.intake.patient_id,
        "procedure_code": session.intake.procedure_code,
        "runs": len(session.runs),
    }


def _render_session(session: Session) -> dict:
    """A whole session, with every stored determination rendered by `_render`.

    Through the same renderer the bare form uses, so the computed properties a
    `model_dump` drops — `model_calls`, the gap list, the token counts — are
    present here exactly as they are there. Two renderers would be two answers
    to one question, and v2.2's `T-116` compares these surfaces (D129).
    """
    rendered = session.model_dump(mode="json")
    rendered["runs"] = [
        {
            "ran_at": run.ran_at,
            "as_of": run.as_of.isoformat(),
            "policy_version_id": run.policy_version_id,
            "determination": _render(run.determination),
        }
        for run in session.runs
    ]
    return rendered


def _verb_create(args) -> int:
    store = _session_store(args.sessions_root)
    try:
        intake = _intake_from(args)
    except intake_module.MalformedIntake as exc:
        # REQ-72's other half, measured here: a malformed intake is a bad
        # request and **no session is written**. The store is not touched on
        # this path at all, which is what makes "never a session" structural
        # rather than a cleanup step (D128).
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    session = Session(
        session_id=uuid.uuid4().hex,
        created_at=_now(),
        intake=intake,
        state=SessionState.CREATED,
    )
    try:
        store.create(session)
    except SessionExists as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_summarise(session), indent=2, ensure_ascii=False))
    return 0


def _verb_list(args) -> int:
    store = _session_store(args.sessions_root)
    sessions = store.list_sessions()
    print(
        json.dumps(
            {
                "root": str(store.root),
                "count": len(sessions),
                "sessions": [_summarise(s) for s in sessions],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _verb_show(args) -> int:
    store = _session_store(args.sessions_root)
    try:
        session = store.get(args.session_id)
    except SessionNotFound as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_render_session(session), indent=2, ensure_ascii=False))
    return 0


def _verb_run(args) -> int:
    """Determine this session's request, and append the snapshot.

    The determination is computed exactly as the bare form computes it and
    rendered by the same function, so the document under `determination` is
    byte-identical to what `--patient/--procedure` prints for the same request
    (D129). What differs is only that it is kept.
    """
    store = _session_store(args.sessions_root)
    try:
        session = store.get(args.session_id)
    except SessionNotFound as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    as_of = args.as_of or date.today()
    policy_store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    knowledge_store = LocalKnowledgeStore() if args.suggest else None
    runner = _build_runner(
        args.extraction, args.recording, args.tool_fetch, patient_store, args.tier
    )
    verifier = _build_verifier(
        args.extraction, DEFAULT_VERIFIER_RECORDING, args.tier
    )

    code, result = _determine_or_report(
        policy_store,
        session.intake.procedure_code,
        patient_id=session.intake.patient_id,
        patient_store=patient_store,
        as_of=as_of,
        runner=runner,
        verifier=verifier,
        state=session.intake.state,
    )
    if result is None:
        return code

    block = _block_for(result, session.intake.patient_id, policy_store,
                       patient_store, knowledge_store, args.suggest)
    rendered = _render(result, block)

    if not isinstance(result, Determination):
        # A code no policy governs, or a state no tree serves. There is no
        # `Determination` to snapshot, `SessionRun` is typed as one, and
        # widening it would make `contracts` import `determination` — the cycle
        # T-100 measured (D129). So the answer is printed, exit is 0 because a
        # deterministic non-answer is an answer (D32), and the session stays
        # CREATED because nothing was determined.
        print(
            f"session {session.session_id} is unchanged and stays "
            f"{session.state.value}: this request resolved to "
            f"{rendered.get('result')}, so there is no determination to record",
            file=sys.stderr,
        )
        print(json.dumps(
            {
                "session_id": session.session_id,
                "state": session.state.value,
                "recorded": False,
                "determination": rendered,
            },
            indent=2,
            ensure_ascii=False,
        ))
        return 0

    run = SessionRun(
        ran_at=_now(),
        as_of=as_of,
        policy_version_id=result.policy_version_id,
        determination=result,
    )
    try:
        advanced = session_machine.advance(
            session, SessionState.DETERMINED, run=run
        )
    except session_machine.IllegalTransition as exc:
        # Raised before anything is constructed and before the store is
        # touched, so "an illegal transition is never recorded" holds because
        # there is no write on this path (REQ-71, D127).
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    store.save(advanced)
    print(json.dumps(
        {
            "session_id": advanced.session_id,
            "state": advanced.state.value,
            "recorded": True,
            "runs": len(advanced.runs),
            "determination": rendered,
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


#: Each review action, and the flag that asks for it. A literal mapping rather
#: than a `--action` choice plus an id, because the four actions do not take the
#: same arguments and one flag per action is what lets argparse refuse two at
#: once (T-104, D132).
REVIEW_ACTIONS = {
    "accept": ReviewAction.ACCEPT_SUGGESTION,
    "reject": ReviewAction.REJECT_SUGGESTION,
    "justify": ReviewAction.JUSTIFY_SUGGESTION,
    "note": ReviewAction.NOTE,
}


def _review_entry(args, run_index: int) -> ReviewEntry:
    """One `ReviewEntry` from the flags, validated by the contract and nowhere else.

    argparse decides **which** action was asked for and nothing else. That a row
    action names a code, that a `NOTE` names neither a row nor a code, and that
    a justification is not whitespace are all `ReviewEntry`'s own validator
    (T-103, D131): a `required=True` on `--code` would be a second rule about
    one field, and two rules about one field eventually disagree — `Intake`'s
    normalisation argument (D128), one contract over.
    """
    asked = [name for name in REVIEW_ACTIONS if getattr(args, name) is not None]
    # The parser's mutually exclusive group is `required=True`, so exactly one
    # is set; this is the assertion that says so rather than an `if`.
    (name,) = asked
    action = REVIEW_ACTIONS[name]
    return ReviewEntry(
        at=_now(),
        reviewer=args.reviewer,
        action=action,
        run_index=run_index,
        row_id=None if action is ReviewAction.NOTE else getattr(args, name),
        icd10_code=args.code,
        justification=args.justification,
        note=args.note,
    )


def _verb_review(args) -> int:
    """Append one entry to a session's review log (REQ-75).

    **Appends, never edits.** The machine returns a new session carrying one
    more entry and the store writes it; nothing on this path can reach a
    `SessionRun`, so the determination's bytes are unchanged by construction
    rather than by care (D132).

    Three refusals, all exit 1 with nothing on stdout and nothing written: an
    unknown session, an order the table forbids — naming the current state and
    its legal successors, D129's contract, and there is no fifth exit code — and
    an entry that is not a review, whether because the flags do not make one or
    because it names a snapshot the session does not hold.
    """
    store = _session_store(args.sessions_root)
    try:
        session = store.get(args.session_id)
    except SessionNotFound as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    # `max(..., 0)` rather than a bounds check here: on a `CREATED` session
    # there is no snapshot *because nothing has been determined*, and the
    # lifecycle is the answer a reviewer needs — so the entry is built and
    # `review()` refuses the move, rather than this line reporting a run count
    # for a session that was never going to be reviewable.
    run_index = args.run if args.run is not None else max(len(session.runs) - 1, 0)
    try:
        entry = _review_entry(args, run_index)
    except ValidationError as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    try:
        reviewed = session_machine.review(session, entry)
    except (session_machine.IllegalTransition, session_machine.NoSuchSnapshot) as exc:
        # Raised before the copy is made and before the store is touched, so
        # "a refused review is never recorded" holds because there is no write
        # on this path (REQ-71's shape, REQ-75).
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    store.save(reviewed)
    print(json.dumps(
        {
            "session_id": reviewed.session_id,
            "state": reviewed.state.value,
            "reviews": len(reviewed.reviews),
            "entry": entry.model_dump(mode="json"),
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


#: The recipient a packet is addressed to until `T-105` builds the payer
#: directory. **A declared placeholder, not a contact**: no committed artifact
#: names a payer's prior-authorization mailbox, and an address written from
#: memory is a claim enforced as though it had a source (D118's rule, D128's on
#: ICD-10 grammar). It lives here because the composition root is where a value
#: that cannot be derived comes from — `_now()`'s rule (D127) — and `T-105`
#: replaces the *source* of the string with `payers.json`, not its shape (D131).
PLACEHOLDER_PAYER = "Simulated Payer <prior-auth@payer.invalid>"


def _packet_index(
    document_ids, policy_store, patient_store, knowledge_store
) -> DocumentIndex:
    """The documents a packet's citations slice back through (REQ-74).

    Built **here** because only the composition root may name a port (REQ-41),
    and a packet's citations point into **all three** hashed corpora: a
    criterion cites the patient's bundle and its notes, a coverage claim cites
    the policy corpus, and an accepted suggestion's `effect` cites an FDA
    label. That is the scorer's rule (D75) and `workflow._document_for`'s, one
    surface over — and the third corpus is the one D131 did not count, so no
    packet carrying an accepted suggestion could be assembled at all until
    `T-134` (D133).

    **Three ports because three declare `get_document`.** The session store does
    not: it records what the system answered and holds no citable document.
    `tests/test_packet_index.py` derives that set from the store package rather
    than listing it, so a fourth hashed corpus cannot reintroduce this defect by
    being forgotten here.

    The order of the stores is **not** a precedence rule — the three id spaces
    are disjoint, measured, and that disjointness is asserted rather than
    assumed, because a reorder is otherwise a mutation no committed chart can
    catch (D133).

    An id **no** port serves is left out, and `form.assemble` then refuses the
    packet with `UncitedPacket`/`UNKNOWN_DOCUMENT`. Classifying it is the span
    validator's job, and deciding here what a missing document *means* would be
    the composition root answering a question `pa_agent.spans` exists to answer
    (D38).
    """
    index = DocumentIndex()
    for document_id in document_ids:
        for store in (patient_store, policy_store, knowledge_store):
            try:
                index.add(store.get_document(document_id))
            except KeyError:
                continue
            break
    return index


def _verb_packet(args) -> int:
    """Assemble the packet for one snapshot and print it.

    It prints the **rendered `.eml`** rather than a JSON record, because a packet
    is a document and the other four verbs print records; `--json` prints the
    structured `Packet` for a reader who wants the fields. `--run N` picks the
    snapshot and defaults to the latest, because a session may hold several and
    a packet is over one (D131).

    The review is computed for every session whose determination carries a tree,
    and it costs nothing: `--suggest` has replayed `T-98`'s quote recording since
    `T-99` and every committed chart's notes are in it (D126).
    """
    store = _session_store(args.sessions_root)
    try:
        session = store.get(args.session_id)
    except SessionNotFound as exc:
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    if not session.runs:
        # Not an unbuilt path and not an error: the session exists and has
        # determined nothing, so there is no snapshot to assemble a packet over.
        # Exit 1 is *the request cannot be acted on as sent* (D129).
        print(
            f"bad request: session {session.session_id} is "
            f"{session.state.value} and carries no determination; run it before "
            "asking for a packet",
            file=sys.stderr,
        )
        return 1

    run_index = len(session.runs) - 1 if args.run is None else args.run
    if not 0 <= run_index < len(session.runs):
        # Checked here rather than left to `form.assemble`'s own refusal, because
        # reading the snapshot to render it comes first and an out-of-range index
        # would be an `IndexError` before the refusal could be raised.
        print(
            f"bad request: session {session.session_id} holds "
            f"{len(session.runs)} run(s); there is no run {run_index}",
            file=sys.stderr,
        )
        return 1

    determination = session.runs[run_index].determination
    policy_store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    knowledge_store = LocalKnowledgeStore()

    review = None
    if history.review_scope(determination) is not None:
        review = _review(
            determination,
            session.intake.patient_id,
            policy_store,
            patient_store,
            knowledge_store,
        ).review

    try:
        packet = form.assemble(
            session=session,
            run_index=run_index,
            rendered_determination=_render(determination),
            review=review,
            payer=args.payer,
            index=_packet_index(
                form.source_ids(determination=determination, review=review),
                policy_store,
                patient_store,
                knowledge_store,
            ),
        )
    except form.PacketRefused as exc:
        # Every refusal is a bad request: a missing justification, a row the
        # review does not hold and a citation that no longer slices are all
        # *this cannot be sent as it stands*, and the message is what the
        # reviewer acts on (D129's rule for the verbs).
        print(f"bad request: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(packet.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(form.render(packet))
    return 0


VERB_HANDLERS = {
    "create": _verb_create,
    "list": _verb_list,
    "show": _verb_show,
    "run": _verb_run,
    "review": _verb_review,
    "packet": _verb_packet,
}


def _add_model_flags(parser: argparse.ArgumentParser) -> None:
    """The flags `session run` shares with the bare form, declared once."""
    parser.add_argument(
        "--extraction",
        choices=("recorded", "direct", "adk"),
        default="recorded",
        help="which model leaf reads the notes (default: recorded, zero calls)",
    )
    parser.add_argument("--recording", type=Path, default=DEFAULT_RECORDING)
    parser.add_argument("--tool-fetch", action="store_true")
    parser.add_argument("--tier", choices=TIERS, default=MEASURED_TIER)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="emit the medical-history review beside the verdicts (REQ-65)",
    )


def _session_main(argv: list[str]) -> int:
    """`session create | list | show | run`.

    A **separate** parser, which is the whole point: the bare form's parser is
    untouched, so `--patient` and `--procedure` keep `required=True` and every
    exit code is what it was (D129).
    """
    parser = argparse.ArgumentParser(
        prog="python -m pa_agent.cli session",
        description="Keep a determination as a session you can come back to.",
    )
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=None,
        help=f"where sessions are written (default: {DEFAULT_SESSION_ROOT})",
    )
    verbs = parser.add_subparsers(dest="verb", required=True)

    create = verbs.add_parser("create", help="record a request as a session")
    create.add_argument("--intake", type=Path, default=None,
                        help="a JSON intake an upstream system produced")
    create.add_argument("--patient", default=None)
    create.add_argument("--procedure", default=None)
    create.add_argument("--state", default=None)
    create.add_argument("--icd10", nargs="*", default=(),
                        help="ICD-10 codes the request carries (recorded, and "
                             "read by no predicate in this version)")
    # T-103 (D131): the flag half of the two identity fields, so REQ-72's *a
    # JSON intake and the equivalent flags validate to the same object* stays
    # true of the whole contract rather than of the four fields it had.
    create.add_argument("--requesting-provider", default=None,
                        help="identity pass-through, reproduced on the packet "
                             "and read by no predicate")
    create.add_argument("--servicing-provider", default=None,
                        help="identity pass-through, reproduced on the packet "
                             "and read by no predicate")

    verbs.add_parser("list", help="every session, newest first")

    show = verbs.add_parser("show", help="one session and its snapshots")
    show.add_argument("session_id")

    run = verbs.add_parser("run", help="determine this session's request")
    run.add_argument("session_id")
    _add_model_flags(run)

    review = verbs.add_parser(
        "review", help="append one entry to a session's review log"
    )
    review.add_argument("session_id")
    review.add_argument(
        "--reviewer",
        required=True,
        help="who is signing off. Required: the packet that leaves is one a "
             "named person reviewed, and an entry with no reviewer cannot "
             "support that (US-13, D132)",
    )
    # Exactly one action per entry, and argparse is the only thing that says so.
    # What each action must carry is `ReviewEntry`'s validator (D131, D132).
    action = review.add_mutually_exclusive_group(required=True)
    action.add_argument("--accept", metavar="ROW_ID",
                        help="accept the suggestion this row_id names")
    action.add_argument("--reject", metavar="ROW_ID",
                        help="keep the suggestion this row_id names out")
    action.add_argument("--justify", metavar="ROW_ID",
                        help="write the justification a red suggestion needs "
                             "before it may enter a packet (REQ-65)")
    action.add_argument("--note", metavar="TEXT",
                        help="prose about the chart, touching no code")
    review.add_argument("--code", default=None,
                        help="the ICD-10 code the entry names. Required by the "
                             "three row actions and refused by --note; the "
                             "packet reads the row's code and never this one")
    review.add_argument("--justification", default=None,
                        help="the text --justify records. Whitespace is refused")
    review.add_argument(
        "--run",
        type=int,
        default=None,
        help="which snapshot this entry reviews (default: the latest)",
    )

    packet = verbs.add_parser(
        "packet", help="assemble the prior-authorization packet for one snapshot"
    )
    packet.add_argument("session_id")
    packet.add_argument(
        "--run",
        type=int,
        default=None,
        help="which snapshot to package (default: the latest)",
    )
    packet.add_argument(
        "--payer",
        default=PLACEHOLDER_PAYER,
        help=f"the recipient the packet is addressed to (default: "
             f"{PLACEHOLDER_PAYER!r}, a declared placeholder until T-105 builds "
             f"the payer directory)",
    )
    packet.add_argument(
        "--json",
        action="store_true",
        help="print the structured Packet instead of the rendered .eml",
    )

    args = parser.parse_args(argv)
    return VERB_HANDLERS[args.verb](args)


def main(argv: list[str] | None = None) -> int:
    # The verbs are dispatched **before the parser is built**, which is what
    # leaves the block below byte-identical: `--patient` and `--procedure` keep
    # `required=True` and every exit code is what it was (D129). Dispatch is on
    # the literal word, not on "any first positional", so a typo is an unknown
    # argument rather than a silently accepted session command.
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] == "session":
        return _session_main(args_in[1:])

    parser = argparse.ArgumentParser(
        prog="python -m pa_agent.cli",
        description="Prior authorization determination for one request.",
    )
    parser.add_argument("--patient", required=True, help="patient identifier")
    parser.add_argument("--procedure", required=True, help="procedure code")
    parser.add_argument(
        "--extraction",
        choices=("recorded", "direct", "adk"),
        default="recorded",
        help="which model leaf reads the notes (default: recorded, zero calls)",
    )
    parser.add_argument(
        "--recording",
        type=Path,
        default=DEFAULT_RECORDING,
        help="recording to replay under --extraction recorded",
    )
    parser.add_argument(
        "--tool-fetch",
        action="store_true",
        help="under --extraction adk, let the agent fetch the note through its "
             "declared tool instead of receiving it in the message",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="two-letter state the request is resolved under (REQ-55). Default: "
             "read from the patient's bundle. An explicit value wins, so the same "
             "chart can be adjudicated under another MAC's tree (D100).",
    )
    parser.add_argument(
        "--tier",
        choices=TIERS,
        default=MEASURED_TIER,
        help="which tier a live leaf calls (default: the development tier). "
             "Ignored by --extraction recorded, which spends nothing. D5 runs "
             "any demo on the tier that does not train on submitted data.",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="the date windows are measured from (default: today). Pin it for a "
             "reproducible answer — every recency verdict moves with this.",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="emit the medical-history review beside the verdicts, which are "
             "unchanged (REQ-65). Replays T-98's quote recording, so it spends "
             "nothing. Where no tree governs the request the block declines by "
             "name rather than reporting an empty review (D123).",
    )
    args = parser.parse_args(argv)

    store = LocalPolicyStore()
    patient_store = LocalPatientStore()
    knowledge_store = LocalKnowledgeStore() if args.suggest else None
    runner = _build_runner(
        args.extraction, args.recording, args.tool_fetch, patient_store, args.tier
    )
    verifier = _build_verifier(
        args.extraction, DEFAULT_VERIFIER_RECORDING, args.tier
    )

    code, result = _determine_or_report(
        store,
        args.procedure,
        patient_id=args.patient,
        patient_store=patient_store,
        as_of=args.as_of or date.today(),
        runner=runner,
        verifier=verifier,
        state=args.state,
    )
    if result is None:
        return code

    block = _block_for(
        result, args.patient, store, patient_store, knowledge_store, args.suggest
    )

    print(json.dumps(_render(result, block), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
