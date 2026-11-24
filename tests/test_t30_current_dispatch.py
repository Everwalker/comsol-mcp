"""Current composite dispatch contract using only a temporary project and fakes."""
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._domain_outcome import STATE_SUCCEEDED, classify
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class SyntheticWorker:
    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


class NoEngineSnapshot:
    def model_snapshot(self, model_tag: str) -> dict[str, object]:
        raise AssertionError(f"report dispatch must not snapshot model {model_tag}")


def test_current_control_dispatch_separates_report_axes_and_project_write(tmp_path):
    workspace_root = tmp_path / "projects"
    workspace_root.mkdir()
    service = ExecutionService(
        SessionLedger("t30-session", "synthetic-server"),
        NoEngineSnapshot(),
        project_root=workspace_root,
    )
    daemon = ControlDaemon(
        tmp_path / "control",
        service=service,
        registry={},
        worker=SyntheticWorker(),
        project_root=workspace_root,
    )
    try:
        created = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "synthetic-report",
                "workspace": "synthetic-report",
                "policy": {"permissions": ["inspect", "project_write"]},
            },
            "execution": {"request_id": "create-writable", "idempotency_key": "create-writable"},
        })
        assert created["success"] is True, created
        project = created["data"]["project"]
        project_root = Path(project["workspace"])

        report = daemon.dispatch({
            "operation": "validate.report",
            "arguments": {
                "destination": str(project_root / "current.json"),
                "data": {
                    "source_identity": "caller-authored",
                    "runtime_version": "synthetic-runtime",
                    "status": "PASS",
                    "execution_status": "PASS",
                    "numerical_verification_status": "PASS",
                    "physical_validation_status": "PASS",
                    "raw_observations": {"temperature": 300.0},
                },
            },
            "execution": {
                "project_id": project["project_id"],
                "session_id": "t30-session",
                "request_id": "current-report",
                "idempotency_key": "current-report",
            },
        })
        assert report["success"] is True, report
        data = report["data"]
        assert data["status"] == "INCOMPLETE"
        assert data["execution_status"] == "PASS"
        assert data["numerical_verification_status"] == "UNVERIFIED"
        assert data["physical_validation_status"] == "UNVERIFIED"
        assert data["model_validated"] is False
        outcome = classify("validate.report", data)
        assert outcome.state == STATE_SUCCEEDED
        assert outcome.execution_status == "PASS"

        published = json.loads((project_root / "current.json").read_text())
        assert published["report_evidence_provenance"] == "CALLER_SUPPLIED_NOT_AUTHENTICATED"
        assert published["caller_status_claims"] == {
            "$.status": "PASS",
            "$.numerical_verification_status": "PASS",
            "$.physical_validation_status": "PASS",
        }
        job = daemon.store.job(report["execution"]["job_id"])
        assert job is not None and job["status"] == "SUCCEEDED"

        read_only = daemon.dispatch({
            "operation": "project.create",
            "arguments": {
                "label": "synthetic-read-only",
                "workspace": "synthetic-read-only",
                "policy": {"permissions": ["inspect"]},
            },
            "execution": {"request_id": "create-read-only", "idempotency_key": "create-read-only"},
        })
        assert read_only["success"] is True, read_only
        denied_project = read_only["data"]["project"]
        denied_path = Path(denied_project["workspace"]) / "denied.json"
        denied = daemon.dispatch({
            "operation": "validate.report",
            "arguments": {
                "destination": str(denied_path),
                "data": {"runtime_version": "synthetic-runtime", "status": "PASS"},
            },
            "execution": {
                "project_id": denied_project["project_id"],
                "session_id": "t30-session",
                "request_id": "denied-report",
                "idempotency_key": "denied-report",
            },
        })
        assert denied["success"] is False
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert not denied_path.exists()
        assert not denied_path.with_suffix(".md").exists()
    finally:
        daemon.close()
