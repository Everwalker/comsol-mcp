"""Software checks of P0 package client plus public metadata-gap reproduction."""
import base64
import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from tools import p0_package_cycle as cycle


def test_typed_recipe_validates_production_format_and_catalog():
    from comsol_mcp._g3_model_delivery import _parse_recipe_bytes
    from comsol_mcp._g2_registry import validate_call
    recipe = cycle.recipe("6.4.0.293")
    assert _parse_recipe_bytes(json.dumps(recipe).encode()) == recipe
    ref = {"schema_version": 1, "session_id": "s", "server_instance_id": "server", "model_tag": "m", "generation": 1}
    for row in recipe["steps"]:
        validate_call(row["operation_id"], {**row["arguments"], "project_id": "p", "session_id": "s", "model_ref": ref,
            "expected_revision": 0, "request_id": "r", "idempotency_key": "i"})
    assert all(row["operation_id"] not in {"study.run", "solver.run", "code.execute_java"} for row in recipe["steps"])


def test_package_extracts_exact_registered_payload_without_generic_unzip(tmp_path):
    mph = b"exact model bytes"
    sha = hashlib.sha256(mph).hexdigest()
    ref = {"model_tag": "m"}
    manifest = {"model": {"model_ref": ref}, "contents": {"model.mph": {"sha256": sha, "size_bytes": len(mph)}}, "resolved_project_dependencies": []}
    package = {"manifest": manifest, "mph_sha256": sha, "mph_size_bytes": len(mph)}
    path = tmp_path / "package.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("model.mph", mph)
    assert cycle.package_payload(path, package, ref) == (manifest, mph)
    package["mph_sha256"] = "0" * 64
    with pytest.raises(RuntimeError):
        cycle.package_payload(path, package, ref)


def test_package_refuses_extra_member_before_extraction(tmp_path):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("model.mph", "model")
        archive.writestr("../escape", "bad")
    with pytest.raises(RuntimeError):
        cycle.package_payload(path, {}, {})
    assert not (tmp_path.parent / "escape").exists()


def test_preview_client_requires_production_pinned_sha_relation():
    raw = b"\x89PNG\r\n\x1a\nsoftware-only"
    source = {"artifact_id": "source", "sha256": "source"}
    data = {**source, "source_sha256": "source", "preview_png_base64": base64.b64encode(raw).decode(),
        "preview_sha256": hashlib.sha256(raw).hexdigest(), "preview_size_bytes": len(raw),
        "provenance": {"pinned_descriptor_sha256_verified_before_and_after": True}}
    assert cycle.preview_bytes(data, source) == raw
    data["source_sha256"] = "other"
    with pytest.raises(RuntimeError):
        cycle.preview_bytes(data, source)


def test_rectangle_size_readback_requires_exact_typed_numeric_values():
    data = {"properties": [{"name": "size", "value": {"kind": "float64", "shape": [2], "data": [0.0025, 0.001]}}]}
    assert cycle.size_readback(data)["name"] == "size"
    data["properties"][0]["value"]["data"] = [0.025, 0.001]
    with pytest.raises(RuntimeError):
        cycle.size_readback(data)


def test_public_registered_recipe_gap_is_production_refusal(tmp_path, monkeypatch):
    from tests.test_g3_model_delivery import _environment, _execution
    daemon, worker, service, project, workspace, ref, install = _environment(tmp_path)
    monkeypatch.setattr("comsol_mcp._managed_backend.verify_owned_server", lambda *a, **k: {"owned": True})
    monkeypatch.setenv("COMSOL_MCP_ISOLATION_RECEIPT", str(tmp_path / "software-only-isolation.json"))
    try:
        path = workspace / "recipe.json"
        path.write_text(json.dumps(cycle.recipe("6.4.0.293")))
        register = daemon.dispatch({"operation": "operation_call", "arguments": {"operation_id": "artifact.register",
            "arguments": {"project_id": project["project_id"], "path": path.name, "role": "model_recipe", "classification": "project_local", "idempotency_key": "register"}},
            "execution": {"project_id": project["project_id"], "request_id": "register", "idempotency_key": "register"}})
        assert register["success"] is True, register
        stored = daemon.store.get_metadata("artifacts", register["data"]["artifact_id"])
        result = daemon.dispatch({"operation": "operation_call", "arguments": {"operation_id": "model.rebuild_target",
            "arguments": {"target_runtime_id": daemon.backend.worker_identity["runtime_id"], "recipe_artifact": register["data"]["artifact_id"]}},
            "execution": _execution(project, ref, request="rebuild")})
        assert stored.get("artifact_type") is None and stored.get("format_version") is None
        assert result["success"] is False
        assert result["error"]["code"] == "ARTIFACT_ROLE_MISMATCH", result
    finally:
        daemon.close()


def test_public_typed_recipe_registration_reaches_strict_rebuild_preflight(tmp_path, monkeypatch):
    from tests.test_g3_model_delivery import _environment, _execution
    daemon, worker, service, project, workspace, ref, install = _environment(tmp_path)
    monkeypatch.setattr("comsol_mcp._managed_backend.verify_owned_server", lambda *a, **k: {"owned": True})
    monkeypatch.setenv("COMSOL_MCP_ISOLATION_RECEIPT", str(tmp_path / "software-only-isolation.json"))
    try:
        # A mismatching exact build proves that real typed source parsing and
        # every step preflight succeeded, then the strict live-version gate
        # refused before any recipe dispatch. It never claims rebuild/native PASS.
        path = workspace / "typed-recipe.json"
        path.write_text(json.dumps(cycle.recipe("6.3.0.290")))
        registered = daemon.dispatch({"operation": "operation_call", "arguments": {"operation_id": "artifact.register",
            "arguments": {"project_id": project["project_id"], "path": path.name, "role": "model_recipe", "classification": "project_local",
                "artifact_type": "application/json", "format_version": "comsol-mcp-model-recipe/1", "idempotency_key": "typed-register"}},
            "execution": {"project_id": project["project_id"], "request_id": "typed-register", "idempotency_key": "typed-register"}})
        assert registered["success"] is True, registered
        stored = daemon.store.get_metadata("artifacts", registered["data"]["artifact_id"])
        assert stored["artifact_type"] == "application/json"
        assert stored["format_version"] == "comsol-mcp-model-recipe/1"
        result = daemon.dispatch({"operation": "operation_call", "arguments": {"operation_id": "model.rebuild_target",
            "arguments": {"target_runtime_id": daemon.backend.worker_identity["runtime_id"], "recipe_artifact": registered["data"]["artifact_id"]}},
            "execution": _execution(project, ref, request="typed-preflight")})
        assert result["success"] is False
        assert result["error"]["code"] == "RUNTIME_VERSION_MISMATCH", result
        assert worker.models["m"].calls == []
    finally:
        daemon.close()


@pytest.mark.parametrize("operation", ["artifact.preview", "artifact.verify"])
def test_package_project_only_hostreads_use_actual_public_admission(tmp_path, operation):
    from tests.test_artifact_preview import _new_daemon, _register, _png
    daemon, project_id, workspace = _new_daemon(tmp_path)
    try:
        path = workspace / "inputs" / "geometry.png"
        path.write_bytes(_png(1, 1, [bytes([255, 0, 0, 255])]))
        artifact_id = _register(daemon, project_id, "inputs/geometry.png")
        body = {"artifact_id": artifact_id}
        if operation == "artifact.preview":
            body["options"] = {"schema_version": 1, "kind": "image", "max_width": 128, "max_height": 128}
        arguments = cycle.action_arguments(operation, body, project_id, "must-not-be-dispatched")
        assert arguments == {"project_id": project_id, **body}
        request = {"operation": "operation_call", "arguments": {"operation_id": operation, "arguments": arguments},
                   "execution": {"project_id": project_id}}
        result = daemon.dispatch(request)
        assert result["success"] is True, result
        request["arguments"]["arguments"] = {**arguments, "session_id": "injected-session"}
        refused = daemon.dispatch(request)
        assert refused["success"] is False
        assert refused["error"]["code"] == "INVALID_REQUEST"
        assert "session_id" in refused["error"]["message"]
        assert cycle.action_arguments("artifact.register", body, project_id, "s")["session_id"] == "s"
    finally:
        daemon.close()
