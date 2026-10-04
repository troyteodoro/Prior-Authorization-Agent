"""REQ-82: a request names its payer, and resolution is by payer, code and state
(T-118, D162).

Every committed tree is Medicare's, so the committed corpus cannot tell
resolution keyed by payer from resolution that ignores it: one payer means
one answer either way. Each test below therefore runs on a **copied** corpus
with a second payer's tree beside Medicare's. The tree is Noridian's, copied
with `payer: sim_payer_x` and a new `policy_version_id`, so it binds the same
codes in the same states. That is the collision `_binding_index` raised on
before this row, which is the one the second payer arrives with.

**Load order is tested by reversing it, not by reading the index.** The store
loads `*.json` in sorted filename order, so two corpora are written with the
second payer's file sorting first in one and last in the other. Identical
answers from both are the claim. One corpus would pass a resolver that kept
the first tree it met.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pa_agent.contracts import Determination, Intake
from pa_agent.resolver import (
    NoJurisdictionTree,
    NoPayerTree,
    NotCovered,
    ResolvedByContractor,
    resolve_sc1,
)
from pa_agent.stores.policy import (
    LocalPolicyStore,
    UnknownJurisdiction,
    UnknownPayer,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = REPO_ROOT / "data" / "policies"
NORIDIAN = "ncd_100_1_jf.json"
MEDICARE_TREE = "ncd-100.1-jf-v1"
SECOND_PAYER = "sim_payer_x"
SECOND_TREE = "sim-payer-x-bariatric-v1"
#: Served by Noridian's bariatric tree and by no other tree of either payer's
#: that binds 43775 — Washington is also served by Medicare's DME tree, which
#: binds E0601 and not this code.
STATE = "WA"
CODE = "43775"
NON_COVERED = "43842"


def _second_payer_tree(*, payer: str = SECOND_PAYER, version: str = SECOND_TREE) -> dict:
    raw = json.loads((POLICY_DIR / NORIDIAN).read_text(encoding="utf-8"))
    raw["policy_version_id"] = version
    raw["jurisdiction"]["payer"] = payer
    return raw


def _corpus(root: Path, extra_name: str, extra: dict) -> LocalPolicyStore:
    shutil.copytree(POLICY_DIR, root)
    (root / extra_name).write_text(json.dumps(extra, indent=2), encoding="utf-8")
    return LocalPolicyStore(root)


@pytest.fixture
def two_payers(tmp_path) -> LocalPolicyStore:
    return _corpus(tmp_path / "policies", "zz_second_payer.json", _second_payer_tree())


# --------------------------------------------------------------------------
# Two payers, one code, one state: one tree each
# --------------------------------------------------------------------------


def test_each_payer_resolves_to_its_own_tree(two_payers):
    """A16's clause, through the port and through sc1. Before T-118 the
    second store raised `resolution would depend on load order` here."""
    assert two_payers.resolve(CODE, STATE, "medicare").policy_version_id == MEDICARE_TREE
    assert two_payers.resolve(CODE, STATE, SECOND_PAYER).policy_version_id == SECOND_TREE

    medicare = resolve_sc1(two_payers, CODE, STATE, "medicare")
    second = resolve_sc1(two_payers, CODE, STATE, SECOND_PAYER)
    assert isinstance(medicare, ResolvedByContractor)
    assert isinstance(second, ResolvedByContractor)
    assert medicare.policy_ref.policy_version_id == MEDICARE_TREE
    assert second.policy_ref.policy_version_id == SECOND_TREE


def test_neither_answer_depends_on_load_order(tmp_path):
    """The same corpus twice, with the second payer's file sorting first in one
    and last in the other. Every (payer, code) answers identically."""
    first = _corpus(tmp_path / "a", "00_second_payer.json", _second_payer_tree())
    last = _corpus(tmp_path / "b", "zz_second_payer.json", _second_payer_tree())
    assert next(iter(first._load_trees())) == SECOND_TREE
    assert next(iter(last._load_trees())) != SECOND_TREE

    for payer in ("medicare", SECOND_PAYER):
        for code in (CODE, NON_COVERED, "99213"):
            assert first.resolve(code, STATE, payer) == last.resolve(code, STATE, payer), (
                payer, code,
            )


def test_two_trees_of_one_payer_still_collide(tmp_path):
    """The load-order raise is scoped to a payer, not removed (D162 clause
    2). Two Medicare trees binding one code in one state is still a
    transcription error."""
    store = _corpus(
        tmp_path / "policies",
        "zz_second_medicare.json",
        _second_payer_tree(payer="medicare", version="ncd-100.1-jf-copy"),
    )
    with pytest.raises(ValueError, match="resolution would depend on load order"):
        store.resolve(CODE, STATE, "medicare")


def test_the_national_sets_are_compared_within_one_payer(tmp_path):
    """The national sets transcribe Medicare's NCD, and another payer's sets
    are not that NCD (D162 clause 2). A second payer may cover what the NCD
    non-covers, and the store answers each payer from its own tree."""
    raw = _second_payer_tree()
    sets = raw["procedure_sets"]
    moved = [e for e in sets["nationally_non_covered"]
             if any(c["code"] == NON_COVERED for c in e["codes"])]
    assert len(moved) == 1
    sets["nationally_non_covered"].remove(moved[0])
    sets["nationally_covered"].append(moved[0])
    store = _corpus(tmp_path / "policies", "zz_second_payer.json", raw)

    assert isinstance(resolve_sc1(store, NON_COVERED, STATE, "medicare"), NotCovered)
    covered = store.resolve(NON_COVERED, STATE, SECOND_PAYER)
    assert covered.policy_version_id == SECOND_TREE
    assert covered.coverage.value == "nationally_covered"


def test_the_national_check_still_holds_within_medicare(tmp_path):
    """The per-payer scoping must not drop the check: two Medicare trees that
    disagree about a national set still raise. The copy serves a state no
    other bariatric tree does, so only the national rule can fire."""
    raw = _second_payer_tree(payer="medicare", version="ncd-100.1-disagrees")
    raw["jurisdiction"]["states"] = ["TX"]
    sets = raw["procedure_sets"]
    moved = next(e for e in sets["nationally_non_covered"]
                 if any(c["code"] == NON_COVERED for c in e["codes"]))
    sets["nationally_non_covered"].remove(moved)
    sets["nationally_covered"].append(moved)
    store = _corpus(tmp_path / "policies", "zz_disagrees.json", raw)
    with pytest.raises(ValueError, match="cannot disagree"):
        store.resolve(NON_COVERED, STATE, "medicare")


# --------------------------------------------------------------------------
# An unserved payer is its own answer, and it is asked first
# --------------------------------------------------------------------------


def test_an_unserved_payer_is_no_payer_tree_naming_the_served_ones(two_payers):
    with pytest.raises(UnknownPayer) as raised:
        two_payers.resolve(CODE, STATE, "sim_payer_y")
    assert raised.value.known_payers == ["medicare", SECOND_PAYER]

    result = resolve_sc1(two_payers, CODE, STATE, "sim_payer_y")
    assert result == NoPayerTree(
        procedure_code=CODE, payer="sim_payer_y", known_payers=["medicare", SECOND_PAYER]
    )


def test_the_payer_is_asked_before_the_state():
    """For a payer no tree declares, *which states does it serve* has no
    answer, so an unserved payer in an unserved state is `NO_PAYER_TREE`.
    Committed corpus: no tree serves Texas."""
    store = LocalPolicyStore()
    with pytest.raises(UnknownJurisdiction):
        store.resolve(CODE, "TX", "medicare")
    assert isinstance(resolve_sc1(store, CODE, "TX", "sim_payer_y"), NoPayerTree)


def test_an_unserved_state_names_only_the_requested_payers_states(tmp_path):
    """A served payer in a state only another payer serves is
    `NO_JURISDICTION_TREE`, and its `known_states` are that payer's. Listing
    every payer's would name answers this request cannot get."""
    raw = _second_payer_tree()
    raw["jurisdiction"]["states"] = ["TX"]
    store = _corpus(tmp_path / "policies", "zz_second_payer.json", raw)

    medicare = resolve_sc1(store, CODE, "TX", "medicare")
    assert isinstance(medicare, NoJurisdictionTree)
    assert "TX" not in medicare.known_states
    assert STATE in medicare.known_states

    second = resolve_sc1(store, CODE, STATE, SECOND_PAYER)
    assert isinstance(second, NoJurisdictionTree)
    assert second.known_states == ["TX"]


# --------------------------------------------------------------------------
# The determination names the request's payer
# --------------------------------------------------------------------------


def test_every_eval_row_determination_names_the_requests_payer():
    """By construction the determination's payer (copied from the tree, D161)
    equals the request's (D162 clause 4). Checked over the whole eval set
    rather than guarded at runtime, because a guard would be a check no input
    can fail (D140)."""
    import importlib.util

    from pa_agent.stores.patient import LocalPatientStore

    spec = importlib.util.spec_from_file_location("run_eval", REPO_ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_eval", module)
    spec.loader.exec_module(module)

    cases = json.loads((REPO_ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert all("payer" in case for case in cases), "every row names its payer (REQ-82)"
    store, patients = LocalPolicyStore(), LocalPatientStore()
    runner, verifier, cache = module._recorded_runner(), module._recorded_verifier(), {}
    checked = 0
    for case in cases:
        result = module._determine(
            case, store, patients, runner, module.EVAL_AS_OF, cache, verifier
        )
        if isinstance(result, Determination):
            assert result.payer == case["payer"], case["case_id"]
            checked += 1
    assert checked >= 40, checked


def test_the_intake_refuses_a_payer_outside_the_grammar():
    """Refused, never normalised (D162 clause 3)."""
    from pydantic import ValidationError

    Intake(patient_id="p", procedure_code=CODE, payer="medicare")
    for bad in ("Medicare", "aetna plan", "", "1payer"):
        with pytest.raises(ValidationError):
            Intake(patient_id="p", procedure_code=CODE, payer=bad)


# --------------------------------------------------------------------------
# The CLI, as it is invoked
# --------------------------------------------------------------------------


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pa_agent.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_the_bare_cli_answers_an_unserved_payer():
    proc = _cli("--patient", "X", "--procedure", NON_COVERED, "--payer", "sim_payer_y",
                "--state", STATE)
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["result"] == "NO_PAYER_TREE"
    assert printed["payer"] == "sim_payer_y"
    assert printed["known_payers"] == ["medicare"]


def test_the_bare_cli_refuses_a_missing_or_malformed_payer():
    """A missing `--payer` is argparse's refusal, as a missing `--procedure`
    has been since D129. A malformed one is a bad request, exit 1, with
    nothing on stdout."""
    missing = _cli("--patient", "X", "--procedure", NON_COVERED, "--state", STATE)
    assert missing.returncode == 2
    assert "--payer" in missing.stderr

    malformed = _cli("--patient", "X", "--procedure", NON_COVERED, "--payer", "Medicare",
                     "--state", STATE)
    assert malformed.returncode == 1
    assert malformed.stdout == ""
    assert "bad request" in malformed.stderr


def test_session_create_refuses_a_missing_payer_and_writes_nothing(tmp_path):
    root = tmp_path / "sessions"
    proc = _cli("session", "--sessions-root", str(root), "create",
                "--patient", "X", "--procedure", CODE, "--state", STATE)
    assert proc.returncode == 1
    assert "--payer" in proc.stderr
    assert not root.exists() or not any(root.iterdir())


def test_a_session_on_an_unserved_payer_stays_created(tmp_path):
    root = tmp_path / "sessions"
    created = _cli("session", "--sessions-root", str(root), "create",
                   "--patient", "X", "--procedure", NON_COVERED,
                   "--payer", "sim_payer_y", "--state", STATE)
    assert created.returncode == 0, created.stderr
    session_id = json.loads(created.stdout)["session_id"]

    ran = _cli("session", "--sessions-root", str(root), "run", session_id)
    assert ran.returncode == 0, ran.stderr
    printed = json.loads(ran.stdout)
    assert printed["recorded"] is False
    assert printed["state"] == "CREATED"
    assert printed["determination"]["result"] == "NO_PAYER_TREE"


def test_the_contract_itself_requires_a_payer():
    """Both routes refuse a missing payer before `Intake` is built — the JSON
    route through `REQUIRED_KEYS`, the flags in `cli._intake_from` — so a
    default on the contract survives every route test (measured in T-118's
    mutation pass). The contract is still reachable without them: a stored
    session is validated straight into it, and one written before T-118
    names no payer. It must fail to load rather than read as whoever a
    default named (D31)."""
    from pydantic import ValidationError

    from pa_agent.contracts import Session

    with pytest.raises(ValidationError, match="payer"):
        Intake(patient_id="p", procedure_code=CODE)

    fixture = REPO_ROOT / "tests" / "fixtures" / "packets" / "no_suggestion" / "session.json"
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    Session.model_validate(raw)
    del raw["intake"]["payer"]
    with pytest.raises(ValidationError, match="payer"):
        Session.model_validate(raw)
