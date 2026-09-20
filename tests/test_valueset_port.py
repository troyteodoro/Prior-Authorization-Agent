"""T-46 — the value set arrives through the policy port (REQ-41, D52).

T-13 built criterion (b) to take a value set as a parameter and left which port
serves it at runtime deliberately open. This closes it: the id comes from the
tree, the codes come from the policy store, and no module outside an adapter
names a path.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from pa_agent.contracts import CodedValueSet, CriterionVerdict
from pa_agent.criteria import evaluate_criterion_b
from pa_agent.stores.patient import LocalPatientStore
from pa_agent.stores.policy import LocalPolicyStore, PolicyStore

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "pa_agent"


def _patient_ids() -> list[str]:
    """From T-04's manifest. A test may read a fixture by path; the REQ-41 scan
    is scoped to `pa_agent/` for exactly this reason (D27)."""
    manifest = json.loads(
        (REPO_ROOT / "data" / "patients" / "manifest.json").read_text(encoding="utf-8")
    )
    return [b["patient_id"] for b in manifest["bundles"]]


@pytest.fixture(scope="module")
def store():
    return LocalPolicyStore()


@pytest.fixture(scope="module")
def tree(store):
    return store.get_tree("ncd-100.1-jf-v1")


@pytest.fixture(scope="module")
def value_set_id(tree) -> str:
    """The id the *tree* names. A literal here would test this file."""
    return tree.criterion("b").require("value_set_id")


def test_the_port_declares_it(store):
    assert isinstance(store, PolicyStore)
    assert hasattr(PolicyStore, "get_value_set")


def test_the_id_comes_from_criterion_b(value_set_id):
    assert value_set_id == "obesity_comorbidities"


def test_the_store_serves_the_set_the_tree_names(store, value_set_id):
    value_set = store.get_value_set(value_set_id)
    assert isinstance(value_set, CodedValueSet)
    assert value_set.value_set_id == value_set_id
    assert value_set.codes, "an empty set would deny every patient criterion (b)"
    assert all(isinstance(c, str) for c in value_set.codes)


def test_an_unknown_id_raises_rather_than_returning_empty(store):
    """D31's lesson restated: a plausible empty answer is worse than a raise,
    because every downstream test agrees with it."""
    with pytest.raises(KeyError, match="no value set"):
        store.get_value_set("comorbidities_that_do_not_exist")


def test_a_file_that_no_longer_matches_its_path_raises(tmp_path):
    root = tmp_path / "policies"
    (root / "value_sets").mkdir(parents=True)
    (root / "value_sets" / "renamed.json").write_text(
        json.dumps(
            {
                "value_set_id": "original",
                "system": "http://snomed.info/sct",
                "entries": [{"system": "http://snomed.info/sct", "code": "1"}],
            }
        )
    )
    with pytest.raises(ValueError, match="declares value_set_id"):
        LocalPolicyStore(root=root).get_value_set("renamed")


def _write_set(root: Path, payload: dict) -> LocalPolicyStore:
    (root / "value_sets").mkdir(parents=True, exist_ok=True)
    (root / "value_sets" / f"{payload['value_set_id']}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return LocalPolicyStore(root=root)


def test_a_value_set_with_no_declared_system_refuses_to_load(tmp_path):
    """REQ-59 (T-92, D111). An implied system is one a set can be wrong about
    silently: the codes load, compare, and match nobody."""
    store = _write_set(
        tmp_path / "policies",
        {"value_set_id": "unsystematic", "entries": [{"code": "44054006"}]},
    )
    with pytest.raises(ValueError, match="declares no `system`"):
        store.get_value_set("unsystematic")


def test_a_value_set_mixing_code_systems_refuses_to_load(tmp_path):
    """One set is one vocabulary. Half in SNOMED and half in ICD-10 is a
    well-formed object every predicate over it would agree with."""
    store = _write_set(
        tmp_path / "policies",
        {
            "value_set_id": "mixed",
            "system": "http://snomed.info/sct",
            "entries": [
                {"system": "http://snomed.info/sct", "code": "44054006"},
                {"system": "http://hl7.org/fhir/sid/icd-10", "code": "E11.9"},
            ],
        },
    )
    with pytest.raises(ValueError, match="carries entries in"):
        store.get_value_set("mixed")


def test_membership_is_refused_to_a_resource_that_declares_no_system(store, value_set_id):
    """An unstated system is not this one. Reading it as a match is how a code
    from another vocabulary that happens to collide gets counted."""
    value_set = store.get_value_set(value_set_id)
    code = sorted(value_set.codes)[0]
    assert value_set.admits(code, value_set.system)
    assert not value_set.admits(code, None)
    assert not value_set.admits(code, "http://hl7.org/fhir/sid/icd-10")


# --------------------------------------------------------------------------
# D52's actual failure mode: a well-formed set that matches nobody
# --------------------------------------------------------------------------


def test_the_codes_are_the_system_the_conditions_speak(store, value_set_id):
    """A set of ICD-10 codes would load cleanly, compare cleanly and find a
    comorbidity in nobody. Criterion (b) would abstain for every patient and
    every downstream test would agree with it (D52)."""
    value_set = store.get_value_set(value_set_id)
    patients = LocalPatientStore()
    matched = {
        c.code
        for pid in _patient_ids()
        for c in patients.get_conditions(pid)
        if value_set.admits(c.code, c.system)
    }
    assert matched, (
        f"no code in the value set {sorted(value_set.codes)} appears on any "
        "committed patient in the system it declares. The set is well-formed "
        "and matches nobody, which is the silent no-match D52 named."
    )


def test_criterion_b_answers_met_through_the_port(store, tree, value_set_id):
    """End to end: tree names the id, store serves the codes, predicate uses
    them, and some real patient comes back MET."""
    value_set = store.get_value_set(value_set_id)
    patients = LocalPatientStore()
    verdicts = []
    for pid in _patient_ids():
        result = evaluate_criterion_b(
            tree.criterion("b"), patients.get_conditions(pid), value_set
        )
        verdicts.append(result.verdict)
        assert result.verdict is not CriterionVerdict.NOT_MET, (
            "a chart cannot prove a comorbidity absent (REQ-36, T-13)"
        )
    assert CriterionVerdict.MET in verdicts


# --------------------------------------------------------------------------
# REQ-41: no path outside an adapter
# --------------------------------------------------------------------------


def test_no_module_outside_stores_names_the_value_set_path():
    """The reason this task exists: the only readers today are tests, by path.

    **Scans string literals rather than the file's text since T-92.** The
    needle was the substring `value_sets`, which was the directory's name and
    nothing else's until a tree declared three sets and `value_sets` became a
    field name on four objects (D111). A bare substring would now report five
    offenders that name no path at all, and the way to make it pass again would
    be to rename the field — the test grading spelling rather than layout.
    A path is a string literal; a field name is not.
    """
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.parent.name == "stores":
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for needle in ("value_sets", "obesity_comorbidities.json"):
                if needle in node.value:
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}: {node.value!r}"
                    )
    assert not offenders, (
        f"{offenders} names the value set's location. It reaches the system "
        "through PolicyStore.get_value_set (REQ-41, D52)."
    )


def test_criteria_module_holds_no_path_and_no_store():
    """Criterion (b) takes the set as a parameter and looks nothing up."""
    source = (PACKAGE / "criteria.py").read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not {m for m in imported if m.startswith(("pa_agent.stores", "google"))}, (
        f"criteria.py imports {sorted(imported)}"
    )
    assert "pathlib" not in imported and "open(" not in source
