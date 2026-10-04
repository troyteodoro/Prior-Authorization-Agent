"""REQ-81: every tree declares its payer, and every determination records it
(T-117, D161).

v2.0's first row. Resolution does not read the payer yet — that is `T-118`'s —
so this file holds the two things that can already go wrong silently: a tree
that names no payer, and a determination whose payer is not its tree's.

**The copy is checked against its source, not against a literal.** Every
committed tree is Medicare's, so a determination hardcoding `"medicare"` would
pass a comparison with the literal on every row this corpus has. The rows are
re-derived through the eval harness's own `_determine` and each payer is
compared with `store.get_tree(policy_version_id).jurisdiction.payer`, and one
test loads a tree whose payer is not `medicare` and runs a request through it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from pa_agent.contracts import CriteriaTree, Determination
from pa_agent.stores.policy import LocalPolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = REPO_ROOT / "data" / "policies"

#: Every payer the loaded trees declare, pinned as a literal (D51's move): a
#: second payer — or a typo of the first — is a visible diff and a red suite,
#: not a tree that quietly answers for nobody.
EXPECTED_PAYERS = {"medicare": 6}


def _tree_files() -> list[Path]:
    return sorted(
        path for path in POLICY_DIR.glob("*.json")
        if "jurisdiction" in json.loads(path.read_text(encoding="utf-8"))
    )


def _eval_module():
    path = REPO_ROOT / "eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval_for_payer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_tree_declares_its_payer_and_the_set_is_pinned():
    files = _tree_files()
    assert len(files) == sum(EXPECTED_PAYERS.values())
    declared: dict[str, int] = {}
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        payer = raw["jurisdiction"]["payer"]
        declared[payer] = declared.get(payer, 0) + 1
    assert declared == EXPECTED_PAYERS


def test_the_payer_is_not_the_authority():
    """D161: for a Medicare tree the authority is the MAC that wrote the rule.
    A tree collapsing the two would lose the distinction v2.0 exists to draw."""
    for path in _tree_files():
        jurisdiction = json.loads(path.read_text(encoding="utf-8"))["jurisdiction"]
        assert jurisdiction["payer"] != jurisdiction["authority"], path.name


def test_a_tree_without_a_payer_fails_to_load():
    raw = json.loads(_tree_files()[0].read_text(encoding="utf-8"))
    del raw["jurisdiction"]["payer"]
    with pytest.raises(ValidationError, match="payer"):
        CriteriaTree.model_validate(raw)


@pytest.mark.parametrize("payer", ["", "Medicare", "medicare advantage", "1medicare"])
def test_a_payer_that_is_not_a_slug_fails_to_load(payer):
    raw = json.loads(_tree_files()[0].read_text(encoding="utf-8"))
    raw["jurisdiction"]["payer"] = payer
    with pytest.raises(ValidationError, match="payer"):
        CriteriaTree.model_validate(raw)


def test_a_determination_without_a_payer_cannot_be_constructed():
    """Required on the contract, never defaulted: a default would turn *never
    recorded* into a value (D31, D161)."""
    with pytest.raises(ValidationError, match="payer"):
        Determination(
            patient_id=None,
            procedure_code="43842",
            policy_version_id="ncd-100.1-jf-v1",
            outcome="NOT_COVERED",
        )


def test_every_eval_determination_carries_its_trees_payer():
    """REQ-81 over the whole eval set, short circuits included: each row's
    determination re-derived through the harness, its payer compared with the
    tree its `policy_version_id` names."""
    module = _eval_module()
    cases = json.loads((REPO_ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
    from pa_agent.stores.patient import LocalPatientStore

    policy_store, patient_store = LocalPolicyStore(), LocalPatientStore()
    runner, verifier = module._recorded_runner(), module._recorded_verifier()
    cache: dict = {}
    seen_outcomes: set[str] = set()
    checked = 0
    for case in cases["cases"]:
        if case.get("procedure_code") is None:
            continue
        result = module._determine(
            case, policy_store, patient_store, runner, module.EVAL_AS_OF, cache, verifier
        )
        if not isinstance(result, Determination):
            continue
        tree = policy_store.get_tree(result.policy_version_id)
        assert result.payer == tree.jurisdiction.payer, case["case_id"]
        seen_outcomes.add(result.outcome.value)
        checked += 1
    assert checked >= 40, checked
    # The three construction sites: a criteria outcome, and NOT_COVERED (sc1
    # and sc2 both answer it). A corpus that stopped reaching one would leave
    # its copy unchecked.
    assert {"MET", "NOT_MET", "NOT_COVERED"} <= seen_outcomes


@pytest.fixture
def renamed_payer_store(tmp_path):
    """The committed corpus with one tree's payer changed. Every committed tree
    is Medicare's, so only a tree whose payer is something else can tell a copy
    from a hardcoded literal."""
    root = tmp_path / "policies"
    shutil.copytree(POLICY_DIR, root)
    path = root / "ncd_100_1_jf.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["jurisdiction"]["payer"] = "sim_payer_x"
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return LocalPolicyStore(root)


def test_a_short_circuit_copies_a_payer_that_is_not_medicare(renamed_payer_store):
    """sc1, through the real resolver and `determine`: 43842 is non-covered in
    Washington, and the answer names the tree's payer, whatever it is."""
    from pa_agent.determination import determine

    result = determine(renamed_payer_store, "43842", state="WA")
    assert isinstance(result, Determination)
    assert result.outcome.value == "NOT_COVERED"
    assert result.payer == "sim_payer_x"


@pytest.mark.parametrize(
    ("case_id", "outcome"),
    [("E1", "MET"), ("E2", "NOT_COVERED")],
    ids=["criteria-path", "sc2-exclusion"],
)
def test_every_patient_path_copies_a_payer_that_is_not_medicare(
    renamed_payer_store, case_id, outcome
):
    """`assemble` through the workflow (E1), and sc2's exclusion (E2), each
    under the renamed tree. With sc1 above, all three construction sites."""
    from pa_agent.stores.patient import LocalPatientStore

    module = _eval_module()
    cases = json.loads((REPO_ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
    case = copy.deepcopy(next(c for c in cases["cases"] if c["case_id"] == case_id))
    result = module._determine(
        case, renamed_payer_store, LocalPatientStore(), module._recorded_runner(),
        module.EVAL_AS_OF, None, module._recorded_verifier(),
    )
    assert isinstance(result, Determination)
    assert result.outcome.value == outcome
    assert result.payer == "sim_payer_x"


def test_the_rendered_determination_names_its_payer():
    """US-16's last bullet: the answer a reader sees names the payer."""
    from pa_agent import cli
    from pa_agent.determination import determine

    rendered = cli._render(determine(LocalPolicyStore(), "43842", state="WA"))
    assert rendered["payer"] == "medicare"


def test_the_payer_is_not_in_the_policy_tool_payload():
    """D45: `get_policy_context` is built field by field, so a new field cannot
    become a changed prompt. The jurisdiction it emits has exactly three keys."""
    from pa_agent.agent.policy_tools import build_policy_tools

    context = build_policy_tools(LocalPolicyStore()).tools["get_policy_context"](
        "ncd-100.1-jf-v1"
    )
    assert set(context["jurisdiction"]) == {"authority", "contractor", "states"}


@pytest.mark.parametrize("name", ["accepted_red", "no_suggestion"])
def test_both_packet_fixtures_name_their_trees_payer(name):
    """D161 clause 2: the fixtures were regenerated by the product's own verb
    and moved by this one field. The stored determination's payer is its
    tree's, and the rendered packet prints it exactly once."""
    from pa_agent.contracts import Session

    root = REPO_ROOT / "tests" / "fixtures" / "packets" / name
    session = Session.model_validate_json((root / "session.json").read_text(encoding="utf-8"))
    store = LocalPolicyStore()
    for run in session.runs:
        tree = store.get_tree(run.determination.policy_version_id)
        assert run.determination.payer == tree.jurisdiction.payer
    assert (root / "packet.eml").read_text(encoding="utf-8").count('"payer": "medicare"') == 1
