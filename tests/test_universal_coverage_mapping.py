"""Frozen coverage progress must survive maintenance of static API metadata."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("universal_coverage", REPO / "tools/universal_coverage.py")
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)

API_FIXTURE = '''VERSION = "6.4.0.999"
SUPPORTED_RELEASES = frozenset({"6.3", "6.4"})
class Capability:
    receiver: str
    method: str
    parameters: tuple
    returns: str
    effect: str
def _cap(receiver, method, parameters, returns, effect):
    return Capability(receiver, method, parameters, returns, effect)
_CAPABILITY_ROWS = [_cap("FixtureReceiver", "label", (), "java.lang.String", "READ")]
CAPABILITIES = tuple(_CAPABILITY_ROWS)
def describe_api(): pass
def prepare_invoke(): pass
def execute_prepared(): pass
'''


@pytest.fixture
def stored(tmp_path, monkeypatch):
    data = json.loads((REPO / coverage.OUTPUT).read_text(encoding="utf-8"))
    for relative in (coverage.CATALOG, coverage.INITIAL_CATALOG, coverage.ACCEPTANCE, coverage.TARGETS):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO / relative).read_bytes())
    destination = tmp_path / coverage.OUTPUT
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(coverage.render(data), encoding="utf-8")
    api = tmp_path / coverage.API_SOURCE
    api.parent.mkdir(parents=True, exist_ok=True)
    api.write_text(API_FIXTURE, encoding="utf-8")
    monkeypatch.setattr(coverage, "ROOT", tmp_path)

    def forbidden_rebuild():
        pytest.fail("frozen maintenance must not call build_mapping")

    monkeypatch.setattr(coverage, "build_mapping", forbidden_rebuild)
    return data, destination


def add_progress(data):
    data["tests_mapping"]["assertions"][0].update(evidence_status="FAIL", evidence_refs=["fixture://native-failure"])
    data["tests_mapping"]["assertions"][1].update(evidence_status="PASS", evidence_refs=["fixture://native-success"])
    test = data["tests_mapping"]["tests"][0]
    test["current_case_status"] = "FAIL"
    test["target_states"][0].update(native_evidence_status="FAIL", evidence_refs=["fixture://native-target"])
    for row in data["actions"]:
        if row["operation_id"] in coverage.API_ACTION_IDS:
            row["actual_route"]["status"] = "FAIL"
            row["actual_route"]["version_constraints"]["verified_versions"] = ["6.3.0.290"]
            row["evidence"]["current_execution"] = {"status": "FAIL", "native_evidence": "fixture://api-run"}
        if row["operation_id"] == "desktop.shell_execute":
            row["actual_route"]["platform_adapter_routes"]["win32"].update(
                availability="NATIVE_VERIFIED", native_evidence="PASS", evidence_refs=["fixture://desktop-native"])
    next(g for g in data["gap_list_for_main_agent"] if g["id"] == "GAP-API-SURFACE").update(
        current_status="FAIL", evidence_refs=["fixture://api-gap-failure"])
    data["future_progress"] = {"status": "FAIL", "evidence_refs": ["fixture://preserve-unknown-field"]}


def test_write_refuses_frozen_mapping_before_build_and_preserves_exact_bytes(stored, capsys):
    data, destination = stored
    add_progress(data)
    destination.write_text(coverage.render(data), encoding="utf-8")
    original = destination.read_bytes()
    assert coverage.main(["--write"]) == 2
    assert "frozen mapping" in capsys.readouterr().err
    assert destination.read_bytes() == original


@pytest.mark.parametrize("has_evidence", [False, True])
def test_write_requires_untouched_init_state_template(stored, monkeypatch, has_evidence):
    data, destination = stored
    raw = (coverage.ROOT / coverage.INITIAL_CATALOG).read_bytes()
    operations = json.loads(raw)["operations"]
    initial = {
        "schema_version": 1, "original_catalog_sha256": coverage.sha256_bytes(raw),
        "operation_count": len(operations),
        "rows": [{"operation_id": row["operation_id"], "domain": row.get("domain"), "effect": row.get("effect"),
                  "classification": "UNASSESSED", "implementation_route": None,
                  "implementation_status": "NOT_REVIEWED", "evidence_status": "NOT_RUN", "evidence_refs": []}
                 for row in operations],
        "test_assertion_mapping": "TO_BE_REVIEWED_FROM_ORIGINAL_03_ACCEPTANCE_NO_OLD_STATE_OVERWRITE",
    }
    if has_evidence:
        initial["rows"][0]["evidence_refs"] = ["fixture://already-recorded"]
    destination.write_text(coverage.render(initial), encoding="utf-8")
    original = destination.read_bytes()
    calls = []
    monkeypatch.setattr(coverage, "build_mapping", lambda: calls.append(True) or copy.deepcopy(data))
    assert coverage.main(["--write"]) == (2 if has_evidence else 0)
    assert calls == ([] if has_evidence else [True])
    if has_evidence:
        assert destination.read_bytes() == original
    else:
        assert json.loads(destination.read_text()) == data


def test_api_refresh_preserves_progress_tests_and_every_unrelated_field(stored):
    data, destination = stored
    add_progress(data)
    destination.write_text(coverage.render(data), encoding="utf-8")
    before = copy.deepcopy(data)
    assert coverage.main(["--refresh-api-snapshot"]) == 0
    after = json.loads(destination.read_text(encoding="utf-8"))
    allowed = {"actions", "gap_list_for_main_agent", "source_fingerprints", "api_capability_snapshot_binding"}
    assert {k: v for k, v in after.items() if k not in allowed} == {k: v for k, v in before.items() if k not in allowed}
    for old, new in zip(before["actions"], after["actions"]):
        if old["operation_id"] not in coverage.API_ACTION_IDS:
            assert old == new
        else:
            assert old["evidence"]["current_execution"] == new["evidence"]["current_execution"]
            assert {k: v for k, v in old["evidence"].items() if k != "current_static_sources"} == {k: v for k, v in new["evidence"].items() if k != "current_static_sources"}
            assert old["actual_route"]["status"] == new["actual_route"]["status"] == "FAIL"
            assert new["actual_route"]["version_constraints"]["verified_versions"] == ["6.3.0.290"]
            assert new["actual_route"]["version_constraints"]["compatibility_version_constant"] == "6.4.0.999"
    assert [g for g in after["gap_list_for_main_agent"] if g["id"] != "GAP-API-SURFACE"] == [g for g in before["gap_list_for_main_agent"] if g["id"] != "GAP-API-SURFACE"]
    assert [f for f in after["source_fingerprints"] if f["path"] != str(coverage.API_SOURCE)] == [f for f in before["source_fingerprints"] if f["path"] != str(coverage.API_SOURCE)]
    gap = next(g for g in after["gap_list_for_main_agent"] if g["id"] == "GAP-API-SURFACE")
    assert gap["current_status"] == "FAIL" and gap["evidence_refs"] == ["fixture://api-gap-failure"]
    assert len(gap["capabilities"]) == 1  # Derived from this API snapshot, not hardcoded to the original 24.
    assert gap["capabilities"][0]["compatibility_version_constant"] == "6.4.0.999"
    assert "default_runtime_version" not in gap["capabilities"][0]
    assert coverage.main(["--check"]) == 0


def test_check_changed_api_is_read_only_and_requests_directed_refresh(stored, capsys):
    data, destination = stored
    add_progress(data)
    destination.write_text(coverage.render(data), encoding="utf-8")
    original = destination.read_bytes()
    assert coverage.main(["--check"]) == 2
    error = capsys.readouterr().err
    assert "API_SNAPSHOT_CHANGED" in error and "--refresh-api-snapshot" in error
    assert "--write" not in error
    assert destination.read_bytes() == original


@pytest.mark.parametrize("change_target_scope", [False, True])
def test_check_rejects_changed_frozen_scope_without_writing(stored, capsys, change_target_scope):
    data, destination = stored
    if change_target_scope:
        data["tests_mapping"]["tests"][0]["target_states"][0]["native_scope"] = "USER_REQUESTED_SKIP"
    else:
        data["tests_mapping"]["assertions"][0]["scope_class"] = "CASE_SPECIALIZED"
    destination.write_text(coverage.render(data), encoding="utf-8")
    original = destination.read_bytes()
    assert coverage.main(["--check"]) == 2
    assert ("frozen native/distribution scope" if change_target_scope else "frozen assertion") in capsys.readouterr().err
    assert destination.read_bytes() == original
