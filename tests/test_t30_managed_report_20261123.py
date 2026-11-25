"""T30 project-scoped report through the real managed backend, synthetic IO."""
import json

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._project_authority import ProjectAuthority


@pytest.fixture
def project_lane(tmp_path, monkeypatch):
    store = OperationStore(tmp_path / "store.sqlite")
    grants = {"inspect", "project_write"}
    authority = ProjectAuthority(store, workspace_root=tmp_path,
                                 permission_provider=lambda: grants, grant_ceiling=set(grants))
    created = authority.dispatch("project.create", {
        "label": "test project", "workspace": "workspace", "policy": {"permissions": sorted(grants)},
    })
    project = created["data"]["project"]
    root = tmp_path / "workspace"
    service = ExecutionService(SessionLedger("report-session", "test-server"), object(), project_root=root)
    backend = ManagedBackend(tmp_path / "backend", store, service=service, worker=object(),
                             registry={}, project_root=root)
    backend.project_authorizer = authority.authorize_operation
    # The fake Worker has no owned server; this fixture verifies downstream
    # project authorization and publication, not the isolation proof itself.
    monkeypatch.setattr(backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
    try:
        yield backend, authority, project, root, grants
    finally:
        backend.close()
        store.close()


def call_report(lane, destination):
    backend, authority, project, root, _ = lane
    with backend.project_root_scope(authority.bind_project_workspace(project)):
        return backend._invoke_g3_model(
            "validate.report", None, {"destination": str(destination), "data": {"status": "PASS"}},
            {"project_id": project["project_id"], "session_id": "report-session", "request_id": "request"},
            "report-operation", "report-session")


def test_managed_project_report_writes_only_authorized_root(project_lane):
    _, _, _, root, _ = project_lane
    destination = root / "report.json"
    result = call_report(project_lane, destination)
    assert result["success"] is True, result
    assert result["data"]["numerical_verification_status"] == "UNVERIFIED"
    assert json.loads(destination.read_text())["report_evidence_provenance"] == "CALLER_SUPPLIED_NOT_AUTHENTICATED"
    assert (root / "report.md").is_file()


def test_managed_report_rejects_foreign_destination_without_write(project_lane):
    _, _, _, root, _ = project_lane
    outside = root.parent / "other-report.json"
    result = call_report(project_lane, outside)
    assert result["success"] is False, result
    assert result["error"]["code"] == "ACCESS_VIOLATION"
    assert not outside.exists()
    assert not outside.with_suffix(".md").exists()


def test_managed_report_rechecks_project_grant_before_write(project_lane):
    _, _, _, root, grants = project_lane
    destination = root / "report.json"
    grants.remove("project_write")
    result = call_report(project_lane, destination)
    assert result["success"] is False, result
    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert not destination.exists()
    assert not destination.with_suffix(".md").exists()


def test_managed_partial_publication_retains_unknown_and_receipt(project_lane, monkeypatch):
    from comsol_mcp import _g3_report_publish
    _, _, _, root, grants = project_lane
    real_publish = _g3_report_publish.publish_parameter_table_bytes_no_clobber

    def publish_then_revoke(*args, **kwargs):
        receipt = real_publish(*args, **kwargs)
        grants.remove("project_write")
        return receipt

    monkeypatch.setattr(_g3_report_publish, "publish_parameter_table_bytes_no_clobber", publish_then_revoke)
    with pytest.raises(ExecutionContractError) as exc:
        call_report(project_lane, root / "partial.json")
    assert exc.value.code == "EXECUTION_STATE_UNKNOWN"
    assert exc.value.details["cause_code"] == "ARTIFACT_PUBLISH_UNKNOWN"
    assert [item["path"] for item in exc.value.details["published"]] == ["partial.json"]
    assert (root / "partial.json").is_file()
    assert not (root / "partial.md").exists()
