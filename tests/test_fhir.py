"""T-12 — BMI observations and Conditions with dates, from all six bundles (D39).

The ground truth the reads are checked against is T-04's manifest: the
most-recent BMI and its date were recorded at selection time by independent
code, so the adapter reproducing them is two implementations agreeing rather
than one implementation agreeing with itself.
"""

from __future__ import annotations

import ast
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from pa_agent.stores.patient import LocalPatientStore

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENTS_ROOT = REPO_ROOT / "data" / "patients"
PATIENT_MODULE = REPO_ROOT / "pa_agent" / "stores" / "patient.py"

LOINC_BMI = "39156-5"
SNOMED_T2DM = "44054006"


@pytest.fixture(scope="module")
def manifest_records() -> list[dict]:
    manifest = json.loads((PATIENTS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return manifest["bundles"]


@pytest.fixture(scope="module")
def store() -> LocalPatientStore:
    return LocalPatientStore()


# --------------------------------------------------------------------------
# BMI observations, from all six bundles
# --------------------------------------------------------------------------


def test_every_patient_yields_bmi_observations_with_dates(store, manifest_records):
    assert len(manifest_records) == 7  # six generated + the E12 patient (D73)
    for record in manifest_records:
        observations = store.get_observations(record["patient_id"])
        bmis = [o for o in observations if o.code == LOINC_BMI]
        assert bmis, f"{record['filename']}: no BMI observations served"
        assert all(isinstance(o.effective_date, date) for o in bmis)


def test_the_most_recent_bmi_matches_the_manifest(store, manifest_records):
    """T-04 recorded each patient's latest BMI with independent parsing code;
    the adapter must land on the same value and the same day."""
    for record in manifest_records:
        bmis = [
            o for o in store.get_observations(record["patient_id"])
            if o.code == LOINC_BMI
        ]
        latest = max(bmis, key=lambda o: o.effective_date)
        assert latest.value == record["latest_bmi"], record["filename"]
        assert latest.effective_date.isoformat() == record["latest_bmi_date"][:10]


def test_observations_are_not_bmi_only(store, manifest_records):
    """The adapter reports facts; selecting LOINC 39156-5 is criterion (a)'s
    judgment (D31's split, D39). A BMI-only read would hide the structured
    values REQ-34's reconciliation compares against."""
    codes = {o.code for o in store.get_observations(manifest_records[0]["patient_id"])}
    assert LOINC_BMI in codes and len(codes) > 1


# --------------------------------------------------------------------------
# Conditions with dates and status, from all six bundles
# --------------------------------------------------------------------------


def test_every_patient_yields_dated_conditions(store, manifest_records):
    for record in manifest_records:
        conditions = store.get_conditions(record["patient_id"])
        assert conditions, f"{record['filename']}: no conditions served"
        assert any(c.onset_date is not None for c in conditions)


def test_the_t2dm_fact_survives_the_read(store, manifest_records):
    """The manifest recorded which patient carries active T2DM (E2's shape);
    the adapter must serve that condition as active."""
    flagged = [r for r in manifest_records if r["has_active_t2dm"]]
    assert flagged, "the population lost its E2 patient; regenerate per D35"
    for record in flagged:
        conditions = store.get_conditions(record["patient_id"])
        t2dm = [c for c in conditions if c.code == SNOMED_T2DM]
        assert t2dm and t2dm[0].clinical_status == "active"


def test_clinical_status_is_reported_not_filtered(store, manifest_records):
    """A read serving only active conditions would decide criterion (b)'s
    question inside the adapter (D39). The population carries resolved
    conditions; they must come through, labeled."""
    statuses = set()
    for record in manifest_records:
        statuses |= {c.clinical_status for c in store.get_conditions(record["patient_id"])}
    assert "active" in statuses
    assert statuses - {"active"}, (
        "only active conditions came through; either the adapter filters "
        "(D39 forbids) or the population has no resolved condition anywhere"
    )


# --------------------------------------------------------------------------
# The failure modes stay loud
# --------------------------------------------------------------------------


def test_an_unknown_patient_raises(store):
    with pytest.raises(KeyError, match="nobody-here"):
        store.get_observations("nobody-here")


def test_a_tampered_bundle_fails_on_read(tmp_path, manifest_records):
    """The get_document/sources.json pattern on the patient plane (REQ-7):
    the manifest hash is the record, the file is checked against it."""
    root = tmp_path / "patients"
    shutil.copytree(PATIENTS_ROOT / "bundles", root / "bundles")
    shutil.copy(PATIENTS_ROOT / "manifest.json", root / "manifest.json")
    victim = manifest_records[0]
    bundle_path = root / "bundles" / victim["filename"]
    bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
    store = LocalPatientStore(root=root)
    with pytest.raises(ValueError, match="REQ-7"):
        store.get_observations(victim["patient_id"])


def test_notes_are_served_hash_verified_and_never_synthea_generated(
    store, manifest_records
):
    """T-07 landed the corpus and the raise retired (D43). What must stay true
    is *which* notes are served: the manifest-driven ones, whose facts someone
    declared, never Synthea's auto-generated prose (D39). A patient whose
    eval manifest declares `"note": false` (D73: E12 reads structured data
    only) is the one legitimate empty answer, and only the declaration
    exempts it."""
    note_free = {
        body["patient_id"]
        for p in sorted((REPO_ROOT / "eval" / "manifests").glob("*.json"))
        if (body := json.loads(p.read_text(encoding="utf-8"))).get("note") is False
    }
    for record in manifest_records:
        documents = store.get_notes(record["patient_id"])
        if record["patient_id"] in note_free:
            assert documents == [], (
                f"{record['filename']}: declared note-free yet a note is served"
            )
            continue
        assert documents, f"{record['filename']}: no note served"
        for document in documents:
            # Document's validator re-hashes on construction, so reaching here
            # means the served text matches the notes manifest (REQ-7).
            assert document.text.strip()
            assert record["patient_id"] in document.document_id


# --------------------------------------------------------------------------
# T-64 — one document namespace over the plane (D65)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def note_records() -> list[dict]:
    manifest = json.loads(
        (PATIENTS_ROOT / "notes" / "manifest.json").read_text(encoding="utf-8")
    )
    return manifest["notes"]


def test_get_document_resolves_a_note_id(store, note_records):
    """The half that did not exist before T-64.

    A note id is a `document_id` a patient-plane span can carry — every c1–c5
    verdict cites one — and until now the only read that served it was
    `get_notes(patient_id)`. T-17's verifier holds a span and no patient id, so
    that route is not one it has (D65).
    """
    assert note_records, "no notes recorded; the corpus is the point of the test"
    for record in note_records:
        document = store.get_document(record["document_id"])
        assert document.document_id == record["document_id"]
        assert document.sha256 == record["sha256"]
        assert document.text.strip()


def test_get_document_and_get_notes_serve_the_same_bytes(store, manifest_records):
    """Two accessors, one document. If these ever diverge, a span validated
    through one read would be checkable against different text through the
    other, and Article III's guarantee would depend on the caller's route."""
    seen = 0
    for record in manifest_records:
        for note in store.get_notes(record["patient_id"]):
            assert store.get_document(note.document_id) == note
            seen += 1
    assert seen == 6, f"expected the six-note corpus, walked {seen}"


def test_the_namespace_is_the_union_of_both_records(
    store, manifest_records, note_records
):
    """Every id either manifest records resolves, and the two halves are
    disjoint — which is what makes "one namespace" a fact rather than a hope."""
    bundles = {r["filename"] for r in manifest_records}
    notes = {r["document_id"] for r in note_records}
    assert not bundles & notes
    for document_id in bundles | notes:
        assert store.get_document(document_id).document_id == document_id


def test_resolution_is_by_record_not_by_the_shape_of_the_id(store, note_records):
    """D65's actual decision, asserted.

    The adapter must not branch on `.json` or on a slash: those are facts about
    how T-04 and T-07 happened to name things, not guarantees of the port. A
    plausible-looking id that no manifest records has to raise, and the raise
    has to name the plane rather than one of the two manifests — a caller
    holding a span does not know which one it should have been in.
    """
    plausible = [
        "Rayford811_Sanford861_00000000-0000-0000-0000-000000000000.json",
        note_records[0]["document_id"].replace("chart_note", "progress_note"),
        "00000000-0000-0000-0000-000000000000/chart_note.txt",
    ]
    for document_id in plausible:
        with pytest.raises(KeyError, match="patient plane"):
            store.get_document(document_id)


def test_a_colliding_id_raises_instead_of_picking_a_winner(tmp_path, manifest_records):
    """One namespace is a claim about the id space, so it is enforced.

    Today the two id shapes cannot collide. The guard is for the day a note is
    named after a bundle: an id meaning two documents makes every span into it
    ambiguous, and silently preferring whichever manifest loaded second would
    hide that behind a passing suite (D65).
    """
    root = _mirror(tmp_path, manifest_records)
    victim = manifest_records[0]["filename"]
    notes_manifest = root / "notes" / "manifest.json"
    payload = json.loads(notes_manifest.read_text(encoding="utf-8"))
    payload["notes"][0]["document_id"] = victim
    notes_manifest.write_text(json.dumps(payload), encoding="utf-8")

    store = LocalPatientStore(root=root)
    with pytest.raises(ValueError, match="claimed by two records"):
        store.get_document(victim)


@pytest.mark.parametrize("half", ["note", "bundle"])
def test_a_tampered_document_fails_get_document(
    tmp_path, manifest_records, note_records, half
):
    """REQ-7 reaches the whole namespace, not the half it started with.

    `get_notes` already re-hashes and so does the bundle read; the accessor that
    now serves both has to, on both, or a span validated through `get_document`
    would be checked against text nobody recorded. Parametrized because the two
    halves reach their hash by different routes — the bundle through `_bundle`,
    the note through `Document`'s own validator — and one of those could be
    dropped without the other noticing.
    """
    root = _mirror(tmp_path, manifest_records)
    victim, path = (
        (note_records[0]["document_id"], root / "notes" / note_records[0]["document_id"])
        if half == "note"
        else (manifest_records[0]["filename"], root / "bundles" / manifest_records[0]["filename"])
    )
    path.write_bytes(path.read_bytes() + b" ")

    store = LocalPatientStore(root=root)
    with pytest.raises(ValueError, match="REQ-7"):
        store.get_document(victim)


def test_the_resolver_reads_no_structure_out_of_an_id():
    """D65's decision as a structural fact, because it is not a behavioural one.

    A resolver that branches on `.json` or on a slash and *then* falls through to
    the record answers identically on every input this corpus can produce — the
    fast path is redundant with the lookup behind it. It is still the convention
    REQ-41's ports exist to remove, and it is still wrong the first time a note is
    named something else, so it is pinned here rather than hoped for.

    Scoped to the two functions that do resolution. Everything else in the module
    is free to know that bundles live in `bundles/`.
    """
    tree = ast.parse(PATIENT_MODULE.read_text(encoding="utf-8"))
    adapter = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "LocalPatientStore"
    )
    resolvers = [
        node
        for node in adapter.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_namespace", "get_document"}
    ]
    assert len(resolvers) == 2, [n.name for n in resolvers]

    for node in resolvers:
        # Prose is exempt: the docstrings argue about `bundles/` and
        # `notes/manifest.json` on purpose. The check is about executable code.
        body = node.body[1:] if ast.get_docstring(node) else node.body
        for child in (c for stmt in body for c in ast.walk(stmt)):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                assert "/" not in child.value and ".json" not in child.value, (
                    f"{node.name} names a path shape ({child.value!r}); an id's "
                    "shape is a fact about T-04 and T-07, not a port guarantee "
                    "(D65)"
                )
            if isinstance(child, ast.Attribute):
                assert child.attr not in {
                    "startswith", "endswith", "split", "rsplit", "partition",
                }, f"{node.name} takes {child.attr} to an identifier (D65)"


def _mirror(tmp_path: Path, manifest_records: list[dict]) -> Path:
    """A writable copy of the committed patient root, for the tamper cases."""
    root = tmp_path / "patients"
    shutil.copytree(PATIENTS_ROOT / "bundles", root / "bundles")
    shutil.copytree(PATIENTS_ROOT / "notes", root / "notes")
    shutil.copy(PATIENTS_ROOT / "manifest.json", root / "manifest.json")
    return root


# --------------------------------------------------------------------------
# The plane stays clean
# --------------------------------------------------------------------------


def test_the_patient_module_imports_no_policy_and_no_model():
    """REQ-33 on this module's own imports: the patient plane holds no policy
    corpus, and nothing model-shaped parses a chart (Art. II, VI)."""
    tree = ast.parse(PATIENT_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "__future__", "hashlib", "json", "datetime", "pathlib", "typing",
        "pa_agent.contracts",
    }, (
        f"pa_agent/stores/patient.py imports {sorted(imported)}; no policy "
        "module, no index over the corpus, no model (REQ-33, D39)"
    )
