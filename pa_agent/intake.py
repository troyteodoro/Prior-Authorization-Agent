"""The two routes to an `Intake`, and the one fault they share (T-101, D128).

A request reaches this system two ways: as a JSON document an upstream system
produced, or as flags a person typed. Both validate to the **same object**, and
this module is where that claim is kept.

**It takes JSON text, never a parsed object.** If the caller parsed, the caller
has already decided what *not JSON* means, somewhere this module's tests cannot
reach — so two modules would decide what a bad intake is and the exit-1 mapping
would have two sources (D128). One fault type, `MalformedIntake`, covers *not
JSON*, *not an object*, *a missing field* and *a wrong type* alike, because the
caller's response to all four is identical: a bad request, exit 1, no session.

**It names no path and opens nothing.** `json` is not a storage name;
`Path` and `open` are, and `tests/test_planes.py` refuses them outside
`pa_agent/stores/`. Reading the bytes is `cli.py`'s, which is the composition
root that already names every location (D25, REQ-41).

**Normalisation is not here.** `Intake` normalises its own `icd10_codes`, so
the two constructors below cannot diverge even in principle — neither of them
touches a code. See `contracts.Intake._codes_are_normalised_here_and_nowhere_else`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from pa_agent.contracts import Intake

#: The keys a JSON intake may carry. A document naming anything else is
#: refused rather than ignored: a typo'd `procedure` silently dropped is a
#: request answered as though a field had not been sent, and the sender has no
#: way to find out (D31's shape, on a wire).
ALLOWED_KEYS = frozenset({"patient_id", "procedure_code", "state", "icd10_codes"})

#: The two a request cannot be without. `state` is optional because `None`
#: means *read it from the bundle*, which is the CLI's own default (REQ-55),
#: and `icd10_codes` because a request with no diagnosis codes is ordinary.
REQUIRED_KEYS = ("patient_id", "procedure_code")


class MalformedIntake(ValueError):
    """An intake this system will not act on (REQ-72).

    One type for every way a request can be unusable, because the caller's
    response to all of them is the same — a bad request, exit 1, and no
    session written. Splitting it would invite a caller to handle one kind and
    forget another, and the kinds are not distinguishable to the person who
    typed the request anyway.

    The message names the field and what was wrong with it, because it is what
    reaches stderr and the sender has nothing else to go on.
    """


def from_json(text: str) -> Intake:
    """An `Intake` from the JSON document an upstream system produced.

    Raises `MalformedIntake` on anything it cannot turn into one: text that is
    not JSON, JSON that is not an object, an object missing a required key, an
    unknown key, or a value of the wrong type.
    """
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MalformedIntake(f"intake is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise MalformedIntake(
            f"intake must be a JSON object; got {type(payload).__name__}. "
            "A list of requests is not an intake — send one."
        )

    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise MalformedIntake(
            f"intake carries unknown field(s) {unknown}; the fields are "
            f"{sorted(ALLOWED_KEYS)}. An ignored field is a request answered "
            "as though it had not been sent."
        )

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise MalformedIntake(
            f"intake is missing required field(s) {missing}; "
            f"{list(REQUIRED_KEYS)} are required."
        )

    return _build(
        patient_id=payload["patient_id"],
        procedure_code=payload["procedure_code"],
        state=payload.get("state"),
        icd10=payload.get("icd10_codes", ()),
    )


def from_flags(
    *,
    patient: str,
    procedure: str,
    state: str | None = None,
    icd10: Sequence[str] = (),
) -> Intake:
    """An `Intake` from the flags a person typed.

    The same object `from_json` produces for the equivalent document, which is
    REQ-72's claim. Keyword-only, so a caller cannot transpose the patient and
    the procedure — two non-empty strings that would validate either way round.
    """
    return _build(
        patient_id=patient, procedure_code=procedure, state=state, icd10=icd10
    )


def _build(
    *, patient_id: Any, procedure_code: Any, state: Any, icd10: Any
) -> Intake:
    """Construct the contract, and turn its refusal into this module's fault.

    The one construction site. Both routes arrive here with values they have
    not touched — normalisation is the contract's (D128) — so there is nowhere
    for the two to disagree.
    """
    if isinstance(icd10, (str, bytes)):
        raise MalformedIntake(
            f"icd10_codes must be a list of codes; got {type(icd10).__name__}. "
            "A bare string is one code spelled as many."
        )
    try:
        codes = tuple(icd10)
    except TypeError as exc:
        raise MalformedIntake(
            f"icd10_codes must be a list of codes; got {type(icd10).__name__}"
        ) from exc

    try:
        return Intake(
            patient_id=patient_id,
            procedure_code=procedure_code,
            state=state,
            icd10_codes=codes,
        )
    except ValidationError as exc:
        raise MalformedIntake(_explain(exc)) from exc


def _explain(exc: ValidationError) -> str:
    """A pydantic error as one sentence naming the field and the problem.

    The raw `ValidationError` is a multi-line report with a URL in it, which is
    not what belongs on stderr beside `bad request`.
    """
    parts = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"]) or "intake"
        parts.append(f"{field}: {error['msg']}")
    return "; ".join(parts)
