"""The intake contract's two routes and its one fault (T-101, D128, REQ-72).

REQ-72 has two halves. This file holds the **unit** half: the two routes
validate to the same object, and every malformed shape raises. The *exit-1*
half — a bad request that writes no session — is `T-102`'s, because nothing has
a CLI surface to exit from until the verbs land. REQ-67's shape (D122, D109).

**Two things here are written against the adversary rather than the feature.**

`test_the_two_routes_agree` feeds inputs that **need** normalising. An equality
test on already-normal values passes against two routes that normalise
differently, or against one that does not normalise at all — it would agree
with the bug it exists to find.

`MALFORMED` names the **reason** each shape is refused, not merely that it is.
A table where every case fails on a missing key passes against a validator that
checks key presence and nothing else, so the type cases and the shape cases
carry their own expected message.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from test_planes import STORAGE_NAMES, _storage_names

from pa_agent.contracts import Intake
from pa_agent.intake import ALLOWED_KEYS, MalformedIntake, from_flags, from_json

REPO_ROOT = Path(__file__).resolve().parent.parent
INTAKE = REPO_ROOT / "pa_agent" / "intake.py"


# --------------------------------------------------------------------------
# The two routes agree — on inputs that need normalising
# --------------------------------------------------------------------------

#: (label, json text, flag kwargs). Every row carries codes that are lowercase,
#: padded, duplicated, or all three — so a route that skipped normalisation
#: would produce a different object and this test would say so.
AGREEING = [
    (
        "codes needing case, stripping and de-duplication",
        '{"patient_id": "p1", "procedure_code": "43775", "state": "AL",'
        ' "icd10_codes": ["  e11.9 ", "E11.9", "z68.41"]}',
        {"patient": "p1", "procedure": "43775", "state": "AL",
         "icd10": ["  e11.9 ", "E11.9", "z68.41"]},
    ),
    (
        "no state — None means read it from the bundle (REQ-55)",
        '{"patient_id": "p2", "procedure_code": "J1745",'
        ' "icd10_codes": ["m06.9"]}',
        {"patient": "p2", "procedure": "J1745", "icd10": ["m06.9"]},
    ),
    (
        "no codes at all — their absence is not an error",
        '{"patient_id": "p3", "procedure_code": "93975", "state": "IA"}',
        {"patient": "p3", "procedure": "93975", "state": "IA"},
    ),
    (
        "codes given, state given, order preserved across duplicates",
        '{"patient_id": "p4", "procedure_code": "43775", "state": "WA",'
        ' "icd10_codes": ["z68.41", "e11.9", "Z68.41"]}',
        {"patient": "p4", "procedure": "43775", "state": "WA",
         "icd10": ["z68.41", "e11.9", "Z68.41"]},
    ),
]


@pytest.mark.parametrize("label,text,flags", AGREEING, ids=[r[0] for r in AGREEING])
def test_the_two_routes_agree(label, text, flags):
    """REQ-72's claim, on inputs that would expose an asymmetry."""
    assert from_json(text) == from_flags(**flags)


def test_the_agreement_is_tested_on_inputs_that_need_normalising():
    """The guard on the test above (D128).

    If every row of `AGREEING` carried codes already stripped, uppercase and
    unique, the equality would hold against a route that normalises and one
    that does not. At least one row must be un-normal in each of the three
    ways, or this file is agreeing with the bug.
    """
    codes = [c for _, _, flags in AGREEING for c in flags.get("icd10", [])]
    assert any(c != c.strip() for c in codes), "no row carries a padded code"
    assert any(c != c.upper() for c in codes), "no row carries a lowercase code"
    assert any(
        len({c.strip().upper() for c in flags.get("icd10", [])})
        < len(flags.get("icd10", []))
        for _, _, flags in AGREEING
    ), "no row carries a duplicate code"


def test_normalisation_happens_on_the_contract_and_not_in_this_module():
    """D128's structural claim, pinned by parsing.

    The two routes cannot diverge because neither touches a code — `Intake`
    normalises its own. A helper here that upper-cased or de-duplicated would
    restore the second place to normalise, and no behavioural test could tell
    the two designs apart while both happened to agree (D65's shape).
    """
    source = INTAKE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "upper" not in called, "intake.py upper-cases; that is the contract's"
    assert "lower" not in called, "intake.py lower-cases; that is the contract's"
    # `strip` likewise: the contract strips, and a second stripper here would
    # make the routes agree for a reason the contract does not own.
    assert "strip" not in called, "intake.py strips; that is the contract's"

    # And the contract really does it, so the assertions above are not vacuous.
    assert Intake(
        patient_id="p", procedure_code="x", icd10_codes=(" e11.9 ", "E11.9")
    ).icd10_codes == ("E11.9",)


def test_a_flag_route_cannot_transpose_the_patient_and_the_procedure():
    """`from_flags` is keyword-only. Two non-empty strings validate either way
    round, so positional arguments would let a caller swap them silently."""
    with pytest.raises(TypeError):
        from_flags("p1", "43775")  # type: ignore[misc]


# --------------------------------------------------------------------------
# Every malformed shape, and the reason it is refused
# --------------------------------------------------------------------------

#: (label, json text, a fragment of the message). The fragment is the point:
#: a table asserting only `raises` passes against a validator that checks key
#: presence and ignores every type.
MALFORMED = [
    ("empty text", "", "not valid JSON"),
    ("not JSON at all", "not json", "not valid JSON"),
    ("truncated JSON", '{"patient_id": "p"', "not valid JSON"),
    ("a JSON array", "[]", "must be a JSON object"),
    ("a JSON scalar", '"p1"', "must be a JSON object"),
    ("a JSON null", "null", "must be a JSON object"),
    ("no fields at all", "{}", "missing required field"),
    ("no procedure", '{"patient_id": "p1"}', "missing required field"),
    ("no patient", '{"procedure_code": "43775"}', "missing required field"),
    (
        "an unknown field",
        '{"patient_id": "p1", "procedure_code": "43775", "procedure": "x"}',
        "unknown field",
    ),
    (
        "an empty patient id",
        '{"patient_id": "", "procedure_code": "43775"}',
        "at least 1 character",
    ),
    (
        "an empty procedure code",
        '{"patient_id": "p1", "procedure_code": ""}',
        "at least 1 character",
    ),
    (
        "a numeric patient id",
        '{"patient_id": 1, "procedure_code": "43775"}',
        "valid string",
    ),
    (
        "codes as a bare string",
        '{"patient_id": "p1", "procedure_code": "43775", "icd10_codes": "E11.9"}',
        "list of codes",
    ),
    (
        "a blank code in the list",
        '{"patient_id": "p1", "procedure_code": "43775", "icd10_codes": ["  "]}',
        "blank",
    ),
    (
        "a numeric code in the list",
        '{"patient_id": "p1", "procedure_code": "43775", "icd10_codes": [119]}',
        "valid string",
    ),
    (
        "a state of the wrong type",
        '{"patient_id": "p1", "procedure_code": "43775", "state": 5}',
        "valid string",
    ),
]


@pytest.mark.parametrize(
    "label,text,fragment", MALFORMED, ids=[r[0] for r in MALFORMED]
)
def test_every_malformed_shape_raises_for_its_own_reason(label, text, fragment):
    """REQ-72: one fault type, and a message that says which field and why."""
    with pytest.raises(MalformedIntake, match=fragment):
        from_json(text)


def test_the_malformed_table_exercises_more_than_key_presence():
    """The guard on the table above (D128).

    A validator that checked only *are the required keys present* would pass
    every missing-field row. The table must also carry parse failures, shape
    failures, unknown fields and type failures, or it grades one rule and
    claims to grade four.
    """
    fragments = {fragment for _, _, fragment in MALFORMED}
    for required in (
        "not valid JSON",
        "must be a JSON object",
        "missing required field",
        "unknown field",
        "valid string",
        "at least 1 character",
        "list of codes",
        "blank",
    ):
        assert required in fragments, f"the table never exercises {required!r}"


def test_the_flag_route_refuses_the_same_shapes():
    """The two routes share `_build`, so the contract's refusals reach both."""
    with pytest.raises(MalformedIntake, match="at least 1 character"):
        from_flags(patient="", procedure="43775")
    with pytest.raises(MalformedIntake, match="list of codes"):
        from_flags(patient="p1", procedure="43775", icd10="E11.9")
    with pytest.raises(MalformedIntake, match="blank"):
        from_flags(patient="p1", procedure="43775", icd10=["  "])


def test_one_fault_type_covers_every_route_and_shape():
    """`MalformedIntake` is the only thing either constructor raises for bad
    input — a caller handling it handles all of them, which is why it is one
    type (D128). A leaked `ValidationError` or `JSONDecodeError` would be a
    second thing to catch and a second exit path."""
    for text, *_ in [(t,) for _, t, _ in MALFORMED]:
        try:
            from_json(text)
        except MalformedIntake:
            continue
        except Exception as exc:  # noqa: BLE001 — that is the assertion
            pytest.fail(f"{text!r} raised {type(exc).__name__}, not MalformedIntake")
        else:
            pytest.fail(f"{text!r} was accepted")


# --------------------------------------------------------------------------
# The module names no storage location
# --------------------------------------------------------------------------


def test_intake_names_no_storage_location():
    """D128's constraint, checked here as well as in the global walk.

    It imports `tests/test_planes.py`'s own `STORAGE_NAMES` and its scanner
    rather than re-implementing one: a local scan looking for the literal
    `"Path"` would pass against a module that called `open()` or reached for
    `pathlib`, and would drift from the rule it claims to enforce the moment
    that set grows. Failing here names the row; failing in the global walk
    names the repository.
    """
    assert "json" not in STORAGE_NAMES, (
        "json became a storage name; this module parses text and would now be "
        "in breach — re-read D128 before changing either side"
    )
    assert _storage_names(INTAKE) == set(), (
        "pa_agent/intake.py names a storage location; reading bytes is "
        "cli.py's (D25, REQ-41, D128)"
    )


def test_the_allowed_keys_are_the_contracts_fields():
    """A field added to `Intake` that this module will not accept is a field an
    upstream system cannot send — and one removed here is a field silently
    refused. Derived from the contract so the two cannot drift."""
    assert ALLOWED_KEYS == set(Intake.model_fields)
