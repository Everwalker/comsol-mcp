from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sqlite3
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from tools.run_native_artifact_geometry import (
    _action_count_receipt,
    _bounded_rpc_timeout,
    _java_action_readback,
    _validate_project_id,
    _verify_registration_reuse,
)


RAW_EXPORT = Path(__file__).parent.parent / "docs/full_project_execution/evidence/luna_f10_artifact_import/native_geometry/run_20260926T090417Z_macos64/action_02_native_geometry_export.json"
RECOVERY_FREEZE = Path(__file__).parent.parent / "docs/full_project_execution/evidence/luna_f10_artifact_import/native_geometry/F10_RECOVERY_FIXTURE_FREEZE.md"


def _raw_response() -> dict:
    return json.loads(RAW_EXPORT.read_text(encoding="utf-8"))


def test_unwraps_actual_successful_native_export_action() -> None:
    response = _raw_response()
    assert _java_action_readback(response, "fixture") == {
        "phase": "export",
        "exported_path": "/private/tmp/comsol-mcp-native-artifact-geometry-20260926T090417Z-02/project/inputs/rectangle_2x1.mphtxt",
        "source_dimension": 2,
        "source_domains": 1,
        "source_bounding_box": [0.0, 2.0, 0.0, 1.0],
        "source_length_unit": "m",
    }


def test_rejects_missing_nested_java_readback_layer() -> None:
    response = _raw_response()
    del response["data"]["readback"]["readback"]
    with pytest.raises(AssertionError, match="nested Java fixture readback"):
        _java_action_readback(response, "fixture")


def test_rejects_flattened_wrong_response_hierarchy() -> None:
    response = _raw_response()
    response["data"]["readback"] = response["data"]["readback"]["readback"]
    with pytest.raises(AssertionError, match="Worker execution wrapper"):
        _java_action_readback(response, "fixture")


def test_rejects_worker_and_managed_readback_disagreement() -> None:
    response = _raw_response()
    response["data"]["worker"]["result"]["readback"]["source_domains"] = 9
    with pytest.raises(AssertionError, match="layers disagree"):
        _java_action_readback(response, "fixture")


def test_rejects_failed_outer_action_even_if_nested_receipt_is_positive() -> None:
    response = _raw_response()
    response["success"] = False
    with pytest.raises(RuntimeError, match="not successful"):
        _java_action_readback(response, "fixture")


def test_artifact_register_body_identity_matches_outer_execution_identity() -> None:
    from tools.run_native_artifact_geometry import _execution_identity_arguments

    body = {"operation_id": "artifact.register", "arguments": {"path": "inputs/rectangle.mphtxt"}}
    result = _execution_identity_arguments("registry_call", body, "same-key", "same-request")
    assert result["arguments"]["idempotency_key"] == "same-key"
    assert result["arguments"]["request_id"] == "same-request"
    assert "idempotency_key" not in body["arguments"]


def test_does_not_rewrite_other_registry_operation_identity() -> None:
    from tools.run_native_artifact_geometry import _execution_identity_arguments

    body = {"operation_id": "geometry.import", "arguments": {"artifact_id": "sha"}}
    assert _execution_identity_arguments("registry_call", body, "key", "request") is body


@pytest.mark.parametrize("project_id", [
    "native-artifact-geometry-probe",
    "native-artifact-geometry-recovery-20260926t2215z",
    "project7",
])
def test_accepts_scoped_project_ids(project_id: str) -> None:
    assert _validate_project_id(project_id) == project_id


@pytest.mark.parametrize("project_id", ["", "Uppercase", "bad_id", "../outside", "-starts-wrong", "x" * 64])
def test_rejects_unscoped_project_ids(project_id: str) -> None:
    with pytest.raises(ValueError, match="--project-id"):
        _validate_project_id(project_id)


def test_rpc_timeout_preserves_native_cleanup_window() -> None:
    assert _bounded_rpc_timeout(180.0, 700.0, now_unix=600.0, cleanup_reserve_s=20.0) == 80.0
    assert _bounded_rpc_timeout(180.0, 700.0, now_unix=100.0, cleanup_reserve_s=20.0) == 180.0


def test_rpc_is_not_started_inside_native_cleanup_window() -> None:
    with pytest.raises(TimeoutError, match="reserved cleanup window"):
        _bounded_rpc_timeout(10.0, 700.0, now_unix=680.0, cleanup_reserve_s=20.0)


def test_preflight_failure_receipt_does_not_report_planned_register_as_actual() -> None:
    assert _action_count_receipt() == {
        "native_export_attempt_count_this_round": 0,
        "native_export_count_this_round": 0,
        "artifact_registration_attempt_count_this_round": 0,
        "artifact_registration_count_this_round": 0,
    }


def test_action_count_receipt_separates_attempted_from_successful_dispatch() -> None:
    receipt = _action_count_receipt(registration_attempts=1, registration_successes=0)
    assert receipt["artifact_registration_attempt_count_this_round"] == 1
    assert receipt["artifact_registration_count_this_round"] == 0
    with pytest.raises(ValueError, match="cannot exceed attempted dispatches"):
        _action_count_receipt(native_export_attempts=0, native_export_successes=1)


def test_recovery_freeze_matches_runner_geometry_guard_and_limits() -> None:
    freeze = RECOVERY_FREEZE.read_text(encoding="utf-8")
    assert "size `(2 m, 1 m)`" in freeze
    assert "at most 600 seconds" in freeze
    assert "zero native exports" in freeze
    assert "exactly one new" in freeze and "production registration" in freeze


def test_unwraps_actual_successful_target_prepare_action() -> None:
    path = RAW_EXPORT.parents[1] / "run_20260926T0913Z_macos64" / "action_03_target_geometry_prepare.json"
    response = json.loads(path.read_text(encoding="utf-8"))
    assert _java_action_readback(response, "target") == {
        "phase": "target",
        "target_dimension": 2,
        "target_length_unit": "m",
    }


def _temporary_registration_reuse(tmp_path: Path) -> tuple[dict, Path]:
    from comsol_mcp._artifact_store import local_artifact_host_identity, project_root_identity

    task_root = tmp_path / "comsol-mcp-native-artifact-geometry-fixture"
    project_root = task_root / "project"
    control_root = task_root / "control"
    managed_path = project_root / "g2_artifacts/registered"
    source_path = project_root / "inputs/rectangle_2x1.mphtxt"
    managed_path.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    payload = b"test geometry source"
    artifact_id = hashlib.sha256(payload).hexdigest()
    (managed_path / f"{artifact_id}.mphtxt").write_bytes(payload)
    source_path.write_bytes(payload)
    registered_file = managed_path / f"{artifact_id}.mphtxt"
    stat = registered_file.stat()
    file_identity = {"device": stat.st_dev, "inode": stat.st_ino,
                     "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
    project_id = "native-artifact-geometry-fixture"
    relative_artifact = f"g2_artifacts/registered/{artifact_id}.mphtxt"
    record = {
        "schema_version": 2,
        "artifact_id": artifact_id,
        "sha256": artifact_id,
        "path": relative_artifact,
        "size": len(payload),
        "file_identity": file_identity,
        "project_id": project_id,
        "project_root_identity": project_root_identity(project_root),
        "host_identity": local_artifact_host_identity(),
        "engine_host_identity": local_artifact_host_identity(),
        "role": "geometry_source",
        "classification": "internal",
        "provenance": {"source_project_relative_path": "inputs/rectangle_2x1.mphtxt",
                       "request_id": "test-request", "registering_operation_id": "artifact.register",
                       "source_sha256": artifact_id},
    }
    control_root.mkdir(parents=True)
    store_path = control_root / "operations.sqlite3"
    with sqlite3.connect(store_path) as connection:
        connection.execute("CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        connection.execute("INSERT INTO artifacts VALUES (?, ?)",
                           (artifact_id, json.dumps(record, sort_keys=True)))
    receipt = {
        "project_id": project_id,
        "artifact_id": artifact_id,
        "registration": {"artifact_id": artifact_id, "sha256": artifact_id,
                         "path": relative_artifact, "size": len(payload),
                         "role": "geometry_source", "classification": "internal"},
        "source": {"path": str(source_path), "relative_path": "inputs/rectangle_2x1.mphtxt",
                   "size_bytes": len(payload), "sha256": artifact_id},
        "managed_copy": {"path": str(registered_file), "size_bytes": len(payload), "sha256": artifact_id},
        "durable_store_record": record,
        "new_registration_count": 1,
        "new_project_root": str(project_root),
        "new_operation_store_path": str(store_path),
    }
    return receipt, task_root


def test_reuses_existing_project_store_and_artifact_with_read_only_receipt_check(tmp_path: Path) -> None:
    receipt, task_root = _temporary_registration_reuse(tmp_path)
    proof = _verify_registration_reuse(receipt, allowed_task_prefix=str(task_root))

    assert proof["status"] == "VERIFIED_EXISTING_PROJECT_STORE_ARTIFACT_READ_ONLY"
    assert proof["project_root"] == receipt["new_project_root"]
    assert proof["operation_store_path"] == receipt["new_operation_store_path"]
    assert proof["managed_copy"]["sha256"] == receipt["artifact_id"]
    assert proof["project_input"]["sha256"] == receipt["artifact_id"]
    assert proof["durable_store_record"] == receipt["durable_store_record"]


def test_cli_reuse_without_native_export_receipt_fails_before_birth_or_export(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="comsol-mcp-native-artifact-geometry-test-",
                                     dir="/private/tmp") as fixture_root:
        receipt, task_root = _temporary_registration_reuse(Path(fixture_root))
        registration_receipt = tmp_path / "registration_receipt.json"
        registration_receipt.write_text(json.dumps(receipt), encoding="utf-8")
        _verify_registration_reuse(receipt)

        work = Path("/private/tmp") / f"comsol-mcp-native-artifact-geometry-cli-guard-{uuid4().hex}"
        evidence = tmp_path / "evidence"
        command = [
            sys.executable,
            str(Path(__file__).parent.parent / "tools/run_native_artifact_geometry.py"),
            "--work", str(work),
            "--evidence", str(evidence),
            "--project-id", receipt["project_id"],
            "--registration-receipt", str(registration_receipt),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)

        assert completed.returncode == 2, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)
        assert "requires a verified --native-export-receipt" in result["error"]
        assert result["engine_birth_count_this_round"] == 0
        assert result["native_export_attempt_count_this_round"] == 0
        assert result["native_export_count_this_round"] == 0
        assert result["artifact_registration_attempt_count_this_round"] == 0
        assert result["artifact_registration_count_this_round"] == 0
        assert not work.exists()
        assert not evidence.exists()
        assert task_root.exists()


def test_registration_reuse_fails_closed_on_sqlite_sidecar_or_changed_artifact(tmp_path: Path) -> None:
    receipt, _ = _temporary_registration_reuse(tmp_path)
    managed = Path(receipt["new_project_root"]) / receipt["registration"]["path"]
    managed.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash or filesystem identity"):
        _verify_registration_reuse(receipt, allowed_task_prefix=str(Path(receipt["new_project_root"]).parent))

    receipt, task_root = _temporary_registration_reuse(tmp_path / "second")
    Path(receipt["new_operation_store_path"] + "-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="sidecars"):
        _verify_registration_reuse(receipt, allowed_task_prefix=str(task_root))
