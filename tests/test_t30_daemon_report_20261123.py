"""T30 daemon dispatch with new synthetic project, Worker, and SQLite state."""
import json
from contextlib import nullcontext
from pathlib import Path

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService


class SyntheticWorker:
    def operation_context(self, *_args, **_kwargs):
        return nullcontext()


class NoEngineSnapshot:
    def model_snapshot(self, model_tag: str) -> dict[str, object]:
        raise AssertionError(f"unbound synthetic report must not snapshot model {model_tag}")


def test_daemon_project_report_authorization_and_publication(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    service = ExecutionService(SessionLedger("report-session", "test-server"), NoEngineSnapshot(), project_root=root)
    daemon = ControlDaemon(tmp_path / "control", project_root=root, service=service,
                           registry={}, worker=SyntheticWorker())
    try:
        creation = daemon.dispatch({"operation": "project.create", "arguments": {
            "label": "synthetic-report", "workspace": "synthetic-report",
            "policy": {"permissions": ["inspect", "project_write"]},
        }, "execution": {"request_id": "project-create", "idempotency_key": "project-create"}})
        assert creation["success"] is True, creation
        project = creation["data"]["project"]
        project_root = Path(project["workspace"])
        result = daemon.dispatch({"operation": "validate.report", "arguments": {
            "destination": str(project_root / "report.json"), "data": {"status": "PASS"},
        }, "execution": {"project_id": project["project_id"], "session_id": "report-session",
                         "request_id": "report-request", "idempotency_key": "report-idempotency"}})
        assert result["success"] is True, result
        assert result["data"]["numerical_verification_status"] == "UNVERIFIED"
        assert (project_root / "report.json").is_file()
        assert (project_root / "report.md").is_file()
        job = daemon.store.job(result["execution"]["job_id"])
        assert job is not None
        assert job["status"] == "SUCCEEDED"

        unverified = daemon.dispatch({"operation": "validate.report", "arguments": {
            "destination": str(project_root / "unverified.json"), "data": {"status": "UNVERIFIED"},
        }, "execution": {"project_id": project["project_id"], "session_id": "report-session",
                         "request_id": "report-unverified", "idempotency_key": "report-unverified"}})
        assert unverified["success"] is True, unverified
        assert unverified["data"]["status"] == "INCOMPLETE"
        assert unverified["data"]["execution_status"] == "PASS"
        assert unverified["data"]["numerical_verification_status"] == "UNVERIFIED"
        unverified_job = daemon.store.job(unverified["execution"]["job_id"])
        assert unverified_job is not None
        assert unverified_job["status"] == "SUCCEEDED"

        mixed = daemon.dispatch({"operation": "validate.report", "arguments": {
            "destination": str(project_root / "mixed.json"),
            "data": {"status": "UNKNOWN", "numerical_verification_status": "UNVERIFIED"},
        }, "execution": {"project_id": project["project_id"], "session_id": "report-session",
                         "request_id": "report-mixed", "idempotency_key": "report-mixed"}})
        assert mixed["success"] is True, mixed
        assert mixed["data"]["status"] == "INCOMPLETE"
        assert mixed["data"]["execution_status"] == "PASS"
        assert mixed["data"]["numerical_verification_status"] == "UNVERIFIED"
        assert mixed["data"]["physical_validation_status"] == "UNVERIFIED"
        assert mixed["data"]["model_validated"] is False
        report_json = json.loads((project_root / "mixed.json").read_text())
        assert report_json["caller_status_claims"] == {
            "$.status": "UNKNOWN",
            "$.numerical_verification_status": "UNVERIFIED",
        }
        assert report_json["caller_status_claims_provenance"] == "CALLER_SUPPLIED_NOT_AUTHENTICATED"
        assert "Caller-Supplied Status Claims" in (project_root / "mixed.md").read_text()
        mixed_job = daemon.store.job(mixed["execution"]["job_id"])
        assert mixed_job is not None
        assert mixed_job["status"] == "SUCCEEDED"

        read_only = daemon.dispatch({"operation": "project.create", "arguments": {
            "label": "read-only", "workspace": "read-only", "policy": {"permissions": ["inspect"]},
        }, "execution": {"request_id": "read-create", "idempotency_key": "read-create"}})
        assert read_only["success"] is True, read_only
        denied_project = read_only["data"]["project"]
        denied_path = Path(denied_project["workspace"]) / "denied.json"
        denied = daemon.dispatch({"operation": "validate.report", "arguments": {
            "destination": str(denied_path), "data": {"status": "PASS"},
        }, "execution": {"project_id": denied_project["project_id"], "session_id": "report-session",
                         "request_id": "report-denied", "idempotency_key": "report-denied"}})
        assert denied["success"] is False, denied
        assert denied["error"]["code"] == "PERMISSION_DENIED"
        assert not denied_path.exists()
        assert not denied_path.with_suffix(".md").exists()

        from comsol_mcp import _g3_report_publish
        real_publish = _g3_report_publish.publish_parameter_table_bytes_no_clobber
        publication_calls = []

        def interrupt_second_publication(*args, **kwargs):
            publication_calls.append(args[1])
            if args[1] == "interrupted.md":
                raise OSError("synthetic publication interruption")
            return real_publish(*args, **kwargs)

        monkeypatch.setattr(
            _g3_report_publish,
            "publish_parameter_table_bytes_no_clobber",
            interrupt_second_publication,
        )
        interrupted_request = {"operation": "validate.report", "arguments": {
            "destination": str(project_root / "interrupted.json"),
            "data": {"status": "UNKNOWN", "numerical_verification_status": "UNVERIFIED"},
        }, "execution": {"project_id": project["project_id"], "session_id": "report-session",
                         "request_id": "report-interrupted", "idempotency_key": "report-interrupted"}}
        interrupted = daemon.dispatch(interrupted_request)
        assert interrupted["success"] is False, interrupted
        assert interrupted["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert interrupted["error"]["safe_retry"] is False
        assert (project_root / "interrupted.json").is_file()
        assert not (project_root / "interrupted.md").exists()
        interrupted_job = daemon.store.job(interrupted["execution"]["job_id"])
        assert interrupted_job is not None
        assert interrupted_job["status"] == "UNKNOWN"

        duplicate = daemon.dispatch(interrupted_request)
        assert duplicate["execution"]["job_id"] == interrupted["execution"]["job_id"]
        assert publication_calls == ["interrupted.json", "interrupted.md"]
    finally:
        daemon.close()
