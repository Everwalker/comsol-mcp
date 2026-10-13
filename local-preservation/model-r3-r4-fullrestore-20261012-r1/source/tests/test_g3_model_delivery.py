"""Software-path tests for the three managed model-delivery actions.

These tests exercise the real ControlDaemon -> serialized ManagedBackend ->
ExecutionService path with a synthetic Worker. They do not claim COMSOL native
execution or scientific acceptance.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from comsol_mcp import _g3_model_delivery as delivery
from comsol_mcp._artifact_store import (
    local_artifact_host_identity,
    local_engine_host_identity,
    register_project_artifact,
)
from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import SessionLedger
from comsol_mcp._execution_service import ExecutionService
from comsol_mcp._runtime_installation import runtime_id_for_root


class _Model:
    def __init__(self, tag: str, root: Path):
        self._handle = "model-handle:" + tag
        self._generation = 1
        self.tag_value = tag
        self.live_path = str(root / "live.mph")
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.file_resources: list[dict[str, str]] = []
        self.java = self

    def _call(self, method: str, *args):
        self.calls.append((method, args))
        if method == "saveModelPackage":
            path, _include_solution = args
            with zipfile.ZipFile(path, "w") as mph:
                mph.writestr("model/fixture.txt", "synthetic saved MPH bytes")
            return {"saved": True, "live_path_preserved": True}
        raise AssertionError(f"unexpected typed Worker method: {method}")

    def getFileResourceInventory(self):
        return list(self.file_resources)

    def getFilePath(self):
        return self.live_path


class _Worker:
    generation = 1

    def __init__(self, install_root: Path, endpoint: str):
        self.paths = type("Paths", (), {"comsol_root": install_root, "project_root": None})()
        self.endpoint = endpoint
        self.models: dict[str, _Model] = {}
        self.context_calls: list[str] = []

    def client(self):
        return self

    def model(self, tag: str):
        return self.models[tag]

    def operation_context(self, operation_id, on_request_event=None):
        self.context_calls.append(operation_id)
        return nullcontext()

    def runtime_metadata(self):
        return {"connected": True, "server": self.endpoint, "instance_id": "software-worker", "generation": 1}

    def model_snapshot(self, tag):
        return {"model_tag": tag, "fingerprint": "fixed-software-fixture", "external_event_counter": 0}

    backend_snapshot = model_snapshot

    def getComsolVersion(self):
        return "COMSOL Multiphysics 6.4 (开发版本: 293)"


def _runtime_fixture(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "readme.txt").write_text("COMSOL 6.4.0.293 README\n", encoding="utf-8")
    launcher = path / "bin" / "macarm64" / "comsollauncher"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"synthetic launcher marker")
    return path


def _environment(tmp_path: Path, *, project_permissions=None):
    project_root = tmp_path / "projects"
    project_root.mkdir()
    install_root = _runtime_fixture(tmp_path / "comsol-install")
    endpoint = "127.0.0.1:24567"
    worker = _Worker(install_root, endpoint)
    worker.models["m"] = _Model("m", tmp_path)
    service = ExecutionService(SessionLedger("session", "server"), worker, project_root=project_root)
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root, service=service, worker=worker)
    daemon.backend.endpoint_key = endpoint
    daemon.backend.worker_identity = {
        "runtime_id": runtime_id_for_root(install_root), "worker_instance_id": "software-worker",
        "connection_epoch": 1, "server_instance_id": "server",
    }
    policy = list(project_permissions or ["inspect", "project_write", "compute"])
    created = daemon.dispatch({
        "operation": "project.create",
        "arguments": {"label": "model delivery fixture", "workspace": "work",
                      "policy": {"permissions": policy}},
        "execution": {"request_id": "project-create", "idempotency_key": "project-create"},
    })
    assert created["success"] is True, created
    project = created["data"]["project"]
    workspace = Path(project["workspace"])
    ref = service.bind_model("m")["execution"]["model_ref"]
    daemon.backend._bind_model_project(ref, project["project_id"])
    daemon.backend.persist()
    return daemon, worker, service, project, workspace, ref, install_root


def _execution(project, ref, *, revision=0, request="request", key=None):
    return {"project_id": project["project_id"], "session_id": ref["session_id"],
            "model_ref": ref, "expected_revision": revision, "request_id": request,
            "idempotency_key": key or request, "rpc_timeout_s": 20}


def _assert_delivery_output_schema(operation: str, data: dict):
    from jsonschema import validate
    from comsol_mcp._g2_registry import BY_ID

    validate(instance=data, schema=BY_ID[operation].as_dict()["data_schema"])


def _register_model_package_fixture(daemon, project, workspace: Path, relative_path: str,
                                   payload: bytes, model_ref: dict, source_revision: int,
                                   request_id: str) -> str:
    target = workspace / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    record = register_project_artifact(
        workspace, daemon.backend.store, relative_path,
        project_id=project["project_id"], role="model_package", classification="project_local",
        host_identity=local_artifact_host_identity(),
        engine_host_identity=local_engine_host_identity(daemon.backend.endpoint_key),
        request_id=request_id, registering_operation_id="model.package",
        artifact_type="application/zip", format_version=delivery.PACKAGE_FORMAT_VERSION,
        provenance_fields={"source_model_ref": model_ref, "source_revision": source_revision,
                           "include_solution": True},
    )
    return record["artifact_id"]


def _recipe_steps():
    comp = {"segments": [{"collection": "component", "tag": "comp1"}]}
    geom = {"segments": [{"collection": "component", "tag": "comp1"},
                          {"collection": "geom", "tag": "geom1"}]}
    study = {"segments": [{"collection": "study", "tag": "std1"}]}
    return [
        {"operation_id": "parameter.set", "arguments": {"parameters": [{"name": "radius", "expression": "1[mm]"}]}},
        {"operation_id": "definition.component_manage", "arguments": {"action": "create", "tag": "comp1", "definition": {"type": "Component"}}},
        {"operation_id": "geometry.sequence_create", "arguments": {"component": "comp1", "tag": "geom1", "dimension": 3}},
        {"operation_id": "geometry.feature_create", "arguments": {"parent": geom, "tag": "blk1", "type_id": "Block", "properties": []}},
        {"operation_id": "selection.create", "arguments": {"component": "comp1", "tag": "sel1", "type_id": "Explicit", "definition": {}}},
        {"operation_id": "function.create", "arguments": {"scope": comp, "tag": "f1", "type_id": "Analytic", "definition": {}}},
        {"operation_id": "material.create", "arguments": {"component": "comp1", "tag": "mat1", "type_id": "Common"}},
        {"operation_id": "physics.create", "arguments": {"component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1"}},
        {"operation_id": "mesh.create", "arguments": {"component": "comp1", "tag": "mesh1", "geometry": "geom1"}},
        {"operation_id": "study.create", "arguments": {"tag": "std1"}},
        {"operation_id": "solver.create", "arguments": {"tag": "sol1", "study": study}},
        {"operation_id": "study.run", "arguments": {"study": study}},
    ]


def _actual_recipe_engine_model():
    """Compose the existing W13/W16 software engine fixtures for real dispatch."""
    from tests.test_g3_w13 import param_model
    from tests.test_g3_w16 import FList, FModel, FNode

    model = FModel()
    component = FNode(tag="comp1", type_id="Component")

    def geometry_factory(tag, dimension="3"):
        sequence = FNode(tag=tag, type_id="GeomSequence", state={
            "getSDim": int(dimension), "lengthUnit": "m", "isAxisymmetric": False,
        })
        sequence.declare("feature", FList(arity=2))
        return sequence

    component.declare("geom", FList(arity=2, node_type="GeomSequence", factory=geometry_factory))
    component.declare("mesh", FList(arity=2, node_type="MeshSequence", factory=lambda tag, geom: FNode(
        tag=tag, type_id="MeshSequence", state={"geom": geom, "isAutomatic": True,
            "current": "size", "getSDim": 3},
    )))
    component.declare("material", FList(arity=1, node_type="Material",
        factory=lambda tag, type_id: FNode(tag=tag, type_id=type_id)))
    component.declare("physics", FList(arity=2, node_type="Physics",
        factory=lambda tag, type_id, *_: FNode(tag=tag, type_id=type_id, state={"geom": "geom1"})))
    model.declare("component", FList(arity=1, node_type="Component"))
    model.collections["component"].items["comp1"] = component

    def study_factory(tag, *_):
        study = FNode(tag=tag, type_id="Study", state={
            "getLastComputationTime": None, "getLastComputationDate": None,
            "getLastComputationVersion": None,
        })
        study.declare("feature", FList(arity=2))
        return study

    model.declare("study", FList(arity=2, node_type="Study", factory=study_factory))
    model.declare("sol", FList(arity=2, node_type="SolverSequence", factory=lambda tag, study_tag: FNode(
        tag=tag, type_id="SolverSequence", state={"study": study_tag, "isEmpty": True,
            "isInitialized": False, "getSequenceType": "General"},
    )))
    _, parameter_collection = param_model()
    model.state["param"] = lambda: parameter_collection
    model.java = model
    return model, component, parameter_collection


def _register_recipe(daemon, project, workspace: Path, endpoint: str, recipe: dict):
    recipe_path = workspace / "recipes" / "basic.json"
    recipe_path.parent.mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(json.dumps(recipe, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return register_project_artifact(
        workspace, daemon.store, "recipes/basic.json", project_id=project["project_id"],
        role="model_recipe", classification="project_local", host_identity=local_artifact_host_identity(),
        engine_host_identity=local_engine_host_identity(endpoint), request_id="recipe-register",
        registering_operation_id="artifact.register", artifact_type="application/json",
        format_version=delivery.RECIPE_FORMAT_VERSION,
    )


def test_rebuild_recipe_runs_through_control_queue_with_full_preflight_and_idempotency(tmp_path, monkeypatch):
    daemon, worker, service, project, workspace, ref, install_root = _environment(tmp_path)
    # Model rebuild is catalogued COMPUTE and therefore requires the normal
    # owned-server isolation proof. The software-path fixture supplies only a
    # test proof; this does not claim native isolation or COMSOL execution.
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    called = []
    from comsol_mcp._g3_ops import DISPATCH
    for step in _recipe_steps():
        operation = step["operation_id"]
        monkeypatch.setitem(DISPATCH, operation,
                            lambda _worker, _tag, args, op=operation: called.append((op, dict(args)))
                            or {"operation": op, "status": "APPLIED", "ok": True, "readback": {"operation": op}})
    recipe = {"schema": delivery.RECIPE_SCHEMA, "version": 1,
              "runtime": {"comsol_versions": ["6.4.0.293"]}, "products": [], "steps": _recipe_steps()}
    registration = _register_recipe(daemon, project, workspace, worker.endpoint, recipe)
    request = {"operation": "model.rebuild_target", "arguments": {
        "target_runtime_id": runtime_id_for_root(install_root), "recipe_artifact": registration["artifact_id"]},
        "execution": _execution(project, ref, request="rebuild-1", key="rebuild-key")}
    result = daemon.dispatch(request)
    assert result["success"] is True, result
    _assert_delivery_output_schema("model.rebuild_target", result["data"])
    assert result["data"]["status"] == "APPLIED"
    assert result["data"]["completed_steps"] == len(_recipe_steps())
    assert result["data"]["final_revision"] == result["execution"]["revision"]
    assert result["data"]["runtime"]["comsol_version"] == "6.4.0.293"
    assert result["data"]["runtime"]["attachment"] == "EXACT_CURRENT_WORKER"
    assert [row[0] for row in called] == [row["operation_id"] for row in _recipe_steps()]
    # The actual operation runs once inside the normal serialized Worker
    # context; that context is keyed by the managed operation UUID.
    assert len(worker.context_calls) == 1
    assert worker.context_calls[0] == result["execution"]["operation_id"]
    replay = daemon.dispatch(request)
    assert replay["success"] is True, replay
    assert len(called) == len(_recipe_steps())
    daemon.close()


def test_rebuild_dispatches_real_parameter_geometry_physics_mesh_study_and_solve_adapters(tmp_path, monkeypatch):
    daemon, worker, _service, project, workspace, ref, install_root = _environment(tmp_path)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    engine_model, component, parameters = _actual_recipe_engine_model()
    worker.models["m"] = engine_model
    study = {"segments": [{"collection": "study", "tag": "std1"}]}
    geometry = {"segments": [{"collection": "component", "tag": "comp1"},
                              {"collection": "geom", "tag": "geom1"}]}
    steps = [
        {"operation_id": "parameter.set", "arguments": {"parameters": [
            {"name": "radius", "expression": "1[mm]"}]}},
        {"operation_id": "geometry.sequence_create", "arguments": {
            "component": "comp1", "tag": "geom1", "dimension": 3}},
        {"operation_id": "geometry.feature_create", "arguments": {
            "parent": geometry, "tag": "blk1", "type_id": "Block", "properties": []}},
        {"operation_id": "material.create", "arguments": {
            "component": "comp1", "tag": "mat1", "type_id": "Common"}},
        {"operation_id": "physics.create", "arguments": {
            "component": "comp1", "tag": "ht", "type_id": "HeatTransferInSolids", "geometry": "geom1"}},
        {"operation_id": "mesh.create", "arguments": {
            "component": "comp1", "tag": "mesh1", "geometry": "geom1"}},
        {"operation_id": "study.create", "arguments": {"tag": "std1"}},
        {"operation_id": "study.step_create", "arguments": {
            "study": study, "tag": "stat", "type_id": "Stationary", "properties": {}}},
        {"operation_id": "solver.create", "arguments": {"tag": "sol1", "study": study}},
        {"operation_id": "study.run", "arguments": {"study": study}},
    ]
    recipe = {"schema": delivery.RECIPE_SCHEMA, "version": 1,
              "runtime": {"comsol_versions": ["6.4.0.293"]}, "products": [], "steps": steps}
    registration = _register_recipe(daemon, project, workspace, worker.endpoint, recipe)
    result = daemon.dispatch({"operation": "model.rebuild_target", "arguments": {
        "target_runtime_id": runtime_id_for_root(install_root), "recipe_artifact": registration["artifact_id"]},
        "execution": _execution(project, ref, request="real-rebuild", key="real-rebuild")})
    assert result["success"] is True, result
    _assert_delivery_output_schema("model.rebuild_target", result["data"])
    assert result["data"]["completed_steps"] == len(steps)
    assert parameters.expressions["radius"]["expression"] == "1[mm]"
    assert [row[0] for row in component.collections["geom"].calls if row[0] == "create"] == ["create"]
    sequence = component.collections["geom"].items["geom1"]
    assert ("create", ("blk1", "Block")) in sequence.collections["feature"].calls
    assert ("create", ("mat1", "Common")) in component.collections["material"].calls
    assert component.collections["physics"].items["ht"].type_id == "HeatTransferInSolids"
    assert component.collections["mesh"].items["mesh1"].state["geom"] == "geom1"
    actual_study = engine_model.collections["study"].items["std1"]
    assert ("create", ("stat", "Stationary")) in actual_study.calls
    assert ("run", ()) in actual_study.calls
    assert engine_model.collections["sol"].items["sol1"].state["study"] == "std1"
    assert len(worker.context_calls) == 1
    daemon.close()


def test_rebuild_preflights_version_permission_artifact_scope_and_stale_model_before_steps(tmp_path, monkeypatch):
    daemon, worker, _service, project, workspace, ref, install_root = _environment(tmp_path)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    from comsol_mcp._g3_ops import DISPATCH
    called = []
    monkeypatch.setitem(DISPATCH, "study.run", lambda *_: called.append("run") or {"status": "APPLIED", "ok": True})

    def submit(recipe, *, request, key, project_row=project, model_ref=ref):
        reg = _register_recipe(daemon, project_row, workspace, worker.endpoint, recipe)
        return daemon.dispatch({"operation": "model.rebuild_target", "arguments": {
            "target_runtime_id": runtime_id_for_root(install_root), "recipe_artifact": reg["artifact_id"]},
            "execution": _execution(project_row, model_ref, request=request, key=key)})

    base = {"schema": delivery.RECIPE_SCHEMA, "version": 1,
            "runtime": {"comsol_versions": ["6.4.0.293"]}, "products": [],
            "steps": [{"operation_id": "study.run", "arguments": {"study": {"segments": [{"collection": "study", "tag": "s1"}]}}}]}
    mismatch = json.loads(json.dumps(base))
    mismatch["runtime"]["comsol_versions"] = ["6.3.0.290"]
    denied = submit(mismatch, request="version-mismatch", key="version-mismatch")
    assert denied["success"] is False and denied["error"]["code"] == "RUNTIME_VERSION_MISMATCH", denied
    assert called == []

    # A registered recipe from a different project is refused by the exact
    # schema-v2 resolver, even though its bytes are present under the root.
    other_project = {**project, "project_id": "other-project"}
    foreign = _register_recipe(daemon, other_project, workspace, worker.endpoint, base)
    foreign_result = daemon.dispatch({"operation": "model.rebuild_target", "arguments": {
        "target_runtime_id": runtime_id_for_root(install_root), "recipe_artifact": foreign["artifact_id"]},
        "execution": _execution(project, ref, request="foreign-recipe", key="foreign-recipe")})
    assert foreign_result["success"] is False
    assert foreign_result["error"]["code"] == "ARTIFACT_SCOPE_MISMATCH"
    assert called == []

    stale_ref = dict(ref, generation=ref["generation"] + 1)
    stale_recipe = json.loads(json.dumps(base))
    stale_recipe["steps"][0]["arguments"]["study"]["segments"][0]["tag"] = "s2"
    stale = submit(stale_recipe, request="stale-model-ref", key="stale-model-ref", model_ref=stale_ref)
    assert stale["success"] is False
    assert stale["error"]["code"] in {"MODEL_IDENTITY_MISMATCH", "PROJECT_IDENTITY_MISMATCH"}
    assert called == []
    daemon.close()


def test_rebuild_step_failure_is_unknown_nonreplayable_and_marks_first_attempt(tmp_path, monkeypatch):
    daemon, worker, service, project, workspace, ref, install_root = _environment(tmp_path)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    from comsol_mcp._g3_ops import DISPATCH
    calls = []
    monkeypatch.setitem(DISPATCH, "study.create", lambda *_: calls.append("study.create") or {"status": "APPLIED", "ok": True})
    def fail_first(*_):
        calls.append("study.run")
        raise RuntimeError("worker reply lost after compute dispatch")
    monkeypatch.setitem(DISPATCH, "study.run", fail_first)
    recipe = {"schema": delivery.RECIPE_SCHEMA, "version": 1,
              "runtime": {"comsol_versions": ["6.4.0.293"]}, "products": [],
              "steps": [{"operation_id": "study.create", "arguments": {"tag": "std1"}},
                        {"operation_id": "study.run", "arguments": {"study": {"segments": [{"collection": "study", "tag": "std1"}]}}},
                        {"operation_id": "study.create", "arguments": {"tag": "must_not_run"}}]}
    registration = _register_recipe(daemon, project, workspace, worker.endpoint, recipe)
    result = daemon.dispatch({"operation": "model.rebuild_target", "arguments": {
        "target_runtime_id": runtime_id_for_root(install_root), "recipe_artifact": registration["artifact_id"]},
        "execution": _execution(project, ref, request="partial-rebuild", key="partial-rebuild")})
    assert result["success"] is False, result
    _assert_delivery_output_schema("model.rebuild_target", result["data"])
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
    assert result["data"]["partial_change"] is True, result
    assert result["data"]["execution_state_unknown"] is True
    assert result["data"]["safe_retry"] is False
    assert result["data"]["completed_steps"] == 1
    assert result["data"]["attempted_steps"] == 2
    assert calls == ["study.create", "study.run"]
    from comsol_mcp._execution_contract import model_ref_from_mapping
    assert service.ledger._state_for(model_ref_from_mapping(ref)).dirty is True
    daemon.close()


def test_package_publishes_mph_manifest_and_pinned_dependency_no_clobber(tmp_path, monkeypatch):
    daemon, worker, _service, project, workspace, ref, _install_root = _environment(tmp_path)
    # The software fixture cannot provide a real owned-server isolation proof.
    # Keep this test focused on the package route and publisher contract.
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    dependency_bytes = b"synthetic CAD/function input bytes\x00"
    dependency = workspace / "input" / "table.bin"
    dependency.parent.mkdir(parents=True)
    dependency.write_bytes(dependency_bytes)
    digest = hashlib.sha256(dependency_bytes).hexdigest()
    monkeypatch.setattr(delivery, "_dependencies_report", lambda _worker, _tag, context, *, verify_hashes: {
        "operation": "model.dependencies", "status": "INCOMPLETE", "verify_hashes": verify_hashes,
        "model_ref": context["model_ref"], "source_revision": context["source_revision"],
        "coverage": {"model_file_resource_api": "PASS_SCOPED", "functions": "PASS_SCOPED",
                     "geometry_import_features": "PASS_SCOPED", "external_code": "UNVERIFIED",
                     "external_libraries_services": "UNVERIFIED"},
        "file_resources": [{"tag": "res1", "archive_members": ["model/fixture.txt"]}],
        "file_references": [{"kind": "function_data", "tag": "func1",
            "function_type": "Interpolation", "authorized_source": {"state": "VERIFIED",
                "project_relative_path": "input/table.bin", "sha256": digest, "size_bytes": len(dependency_bytes)},
            "verification": "VERIFIED_PROJECT_LOCAL_SHA256"}], "unresolved_items": [], "inventory_errors": [],
    })
    execution = _execution(project, ref, request="package-1", key="package-key")
    body = {"destination": "packages/model-package.zip"}
    package_dir = workspace / "packages"
    package_dir.mkdir()
    request = {"operation": "model.package", "arguments": body, "execution": execution}
    result = daemon.dispatch(request)
    assert result["success"] is True, result
    _assert_delivery_output_schema("model.package", result["data"])
    assert result["data"]["include_solution"] is True
    assert result["data"]["portability_status"] == "UNVERIFIED"
    assert result["data"]["resolved_project_dependencies"][0]["sha256"] == digest
    published = workspace / "packages" / "model-package.zip"
    with zipfile.ZipFile(published) as package:
        members = package.namelist()
        assert "manifest.json" in members and "model.mph" in members
        dep_name = result["data"]["resolved_project_dependencies"][0]["package_path"]
        assert package.read(dep_name) == dependency_bytes
        manifest_raw = package.read("manifest.json")
        assert str(workspace) not in manifest_raw.decode("utf-8")
        assert json.loads(manifest_raw)["portability_status"] == "UNVERIFIED"
    artifact_id = result["data"]["artifact_registration"]["artifact_id"]
    verified = daemon.dispatch({"operation": "artifact.verify",
        "arguments": {"project_id": project["project_id"], "artifact_id": artifact_id},
        "execution": {"project_id": project["project_id"], "request_id": "verify-model-package"}})
    assert verified["success"] is True, verified
    _assert_delivery_output_schema("artifact.verify", verified["data"])
    verify_data = verified["data"]
    assert verify_data["format"] == "COMSOL_MODEL_PACKAGE"
    assert verify_data["format_schema"] == "comsol-mcp-model-package"
    assert verify_data["format_verdict"] == "INCOMPLETE", verify_data["findings"]
    assert verify_data["member_integrity"] == "PASS"
    assert verify_data["dependency_closure"] == "INCOMPLETE"
    assert verify_data["declared_comsol_version"] == worker.getComsolVersion()
    assert verify_data["target_runtime_compatibility"] == "UNVERIFIED"
    assert verify_data["portability_status"] == "UNVERIFIED"
    assert verify_data["native_compatibility"] == "NOT_RUN"

    with zipfile.ZipFile(published, "r") as original:
        package_members = {info.filename: original.read(info) for info in original.infolist()}
    wrong_hash_members = dict(package_members)
    wrong_manifest = json.loads(wrong_hash_members["manifest.json"])
    dependency_row = wrong_manifest["resolved_project_dependencies"][0]
    old_dependency_path = dependency_row["package_path"]
    wrong_sha256 = "0" * 64
    new_dependency_path = (f"dependencies/{wrong_sha256}/"
                            f"{hashlib.sha256(dependency_row['project_relative_path'].encode('utf-8')).hexdigest()[:16]}.bin")
    dependency_row["sha256"] = wrong_sha256
    dependency_row["package_path"] = new_dependency_path
    for file_reference in wrong_manifest["file_references"]:
        if file_reference["authorized_source"].get("project_relative_path") == dependency_row["project_relative_path"]:
            file_reference["authorized_source"]["sha256"] = wrong_sha256
            file_reference["package_path"] = new_dependency_path
    wrong_hash_members[new_dependency_path] = wrong_hash_members.pop(old_dependency_path)
    wrong_hash_members["manifest.json"] = json.dumps(wrong_manifest, sort_keys=True, separators=(",", ":")).encode()
    wrong_hash_bytes = io.BytesIO()
    with zipfile.ZipFile(wrong_hash_bytes, "w", compression=zipfile.ZIP_DEFLATED) as altered:
        for name, payload in wrong_hash_members.items():
            altered.writestr(name, payload)
    wrong_hash_artifact = _register_model_package_fixture(
        daemon, project, workspace, "packages/wrong-dependency-hash.zip", wrong_hash_bytes.getvalue(),
        ref, result["data"]["source_revision"], "wrong-dependency-hash",
    )
    wrong_hash_result = daemon.dispatch({"operation": "artifact.verify",
        "arguments": {"project_id": project["project_id"], "artifact_id": wrong_hash_artifact},
        "execution": {"project_id": project["project_id"], "request_id": "verify-wrong-dependency-hash"}})
    assert wrong_hash_result["success"] is True, wrong_hash_result
    _assert_delivery_output_schema("artifact.verify", wrong_hash_result["data"])
    assert wrong_hash_result["data"]["format_verdict"] == "INVALID"
    assert wrong_hash_result["data"]["member_integrity"] == "FAIL"
    assert any(row["code"] == "MODEL_PACKAGE_MEMBER_HASH_MISMATCH"
               for row in wrong_hash_result["data"]["findings"])

    wrong_embedded_members = dict(package_members)
    wrong_embedded_manifest = json.loads(wrong_embedded_members["manifest.json"])
    wrong_embedded_manifest["embedded_dependencies"][0]["sha256"] = "0" * 64
    wrong_embedded_members["manifest.json"] = json.dumps(
        wrong_embedded_manifest, sort_keys=True, separators=(",", ":"),
    ).encode()
    wrong_embedded_bytes = io.BytesIO()
    with zipfile.ZipFile(wrong_embedded_bytes, "w", compression=zipfile.ZIP_DEFLATED) as altered:
        for name, payload in wrong_embedded_members.items():
            altered.writestr(name, payload)
    wrong_embedded_artifact = _register_model_package_fixture(
        daemon, project, workspace, "packages/wrong-embedded-hash.zip", wrong_embedded_bytes.getvalue(),
        ref, result["data"]["source_revision"], "wrong-embedded-dependency-hash",
    )
    wrong_embedded_result = daemon.dispatch({"operation": "artifact.verify",
        "arguments": {"project_id": project["project_id"], "artifact_id": wrong_embedded_artifact},
        "execution": {"project_id": project["project_id"], "request_id": "verify-wrong-embedded-dependency-hash"}})
    assert wrong_embedded_result["success"] is True, wrong_embedded_result
    assert wrong_embedded_result["data"]["format_verdict"] == "INVALID"
    assert any(row["code"] == "EMBEDDED_DEPENDENCY_HASH_MISMATCH"
               for row in wrong_embedded_result["data"]["findings"])

    extra_members = dict(package_members)
    extra_members["unlisted.txt"] = b"not declared by manifest"
    extra_bytes = io.BytesIO()
    with zipfile.ZipFile(extra_bytes, "w", compression=zipfile.ZIP_DEFLATED) as altered:
        for name, payload in extra_members.items():
            altered.writestr(name, payload)
    extra_artifact = _register_model_package_fixture(
        daemon, project, workspace, "packages/extra-member.zip", extra_bytes.getvalue(),
        ref, result["data"]["source_revision"], "extra-model-package-member",
    )
    extra_result = daemon.dispatch({"operation": "artifact.verify",
        "arguments": {"project_id": project["project_id"], "artifact_id": extra_artifact},
        "execution": {"project_id": project["project_id"], "request_id": "verify-extra-model-package-member"}})
    assert extra_result["success"] is True, extra_result
    assert extra_result["data"]["format_verdict"] == "INVALID"
    assert any(row["code"] == "UNDECLARED_MODEL_PACKAGE_MEMBER"
               for row in extra_result["data"]["findings"])

    save_calls = [call for call in worker.models["m"].calls if call[0] == "saveModelPackage"]
    assert len(save_calls) == 1 and save_calls[0][1][1] is True
    assert worker.models["m"].live_path.endswith("live.mph")

    replay = daemon.dispatch(request)
    assert replay["success"] is True
    assert len([call for call in worker.models["m"].calls if call[0] == "saveModelPackage"]) == 1
    overwrite = daemon.dispatch({"operation": "model.package", "arguments": body,
                                  "execution": _execution(project, ref, revision=1,
                                      request="package-overwrite", key="package-overwrite")})
    assert overwrite["success"] is False
    assert overwrite["error"]["code"] == "DESTINATION_EXISTS"
    assert len([call for call in worker.models["m"].calls if call[0] == "saveModelPackage"]) == 1

    without_solution = daemon.dispatch({"operation": "model.package",
        "arguments": {"destination": "packages/model-package-no-solution.zip", "include_solution": False},
        "execution": _execution(project, ref, revision=1, request="package-no-solution", key="package-no-solution")})
    assert without_solution["success"] is True, without_solution
    _assert_delivery_output_schema("model.package", without_solution["data"])
    assert without_solution["data"]["include_solution"] is False
    save_calls = [call for call in worker.models["m"].calls if call[0] == "saveModelPackage"]
    assert len(save_calls) == 2 and save_calls[1][1][1] is False
    daemon.close()


def test_package_refuses_missing_and_outside_dependencies_without_touching_them(tmp_path, monkeypatch):
    daemon, worker, _service, project, workspace, ref, _install_root = _environment(tmp_path)
    # The software fixture cannot provide a real owned-server isolation proof.
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    outside = tmp_path.parent / "never-read-outside.bin"
    calls = []
    source = {"state": "UNVERIFIED", "project_relative_path": None, "reason": "outside_project_root"}
    def report(_worker, _tag, context, *, verify_hashes):
        return {"file_references": [{"kind": "cad_geometry", "authorized_source": source}],
                "coverage": {}, "unresolved_items": [], "file_resources": []}
    monkeypatch.setattr(delivery, "_dependencies_report", report)
    original = _Model._call
    monkeypatch.setattr(_Model, "_call", lambda self, method, *args: calls.append(method) or original(self, method, *args))
    workspace.joinpath("packages").mkdir()
    for request, state in (("package-outside", {"state": "UNVERIFIED", "project_relative_path": None,
                                                  "reason": "outside_project_root"}),
                           ("package-missing", {"state": "MISSING", "project_relative_path": "cad/missing.step",
                                                  "reason": "project_local_file_missing"})):
        source.clear()
        source.update(state)
        result = daemon.dispatch({"operation": "model.package", "arguments": {"destination": "packages/out.zip"},
            "execution": _execution(project, ref, request=request, key=request)})
        assert result["success"] is False
        assert result["error"]["code"] == "DEPENDENCY_NOT_AUTHORIZED"
    assert calls == []
    assert not outside.exists()
    daemon.close()


def test_package_post_save_publication_failure_retains_model_package_and_unknown_receipt(tmp_path, monkeypatch):
    from comsol_mcp._execution_contract import ExecutionContractError

    daemon, _worker, _service, project, workspace, ref, _install_root = _environment(tmp_path)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    monkeypatch.setattr(delivery, "_dependencies_report", lambda _worker, _tag, context, *, verify_hashes: {
        "operation": "model.dependencies", "status": "INCOMPLETE", "verify_hashes": verify_hashes,
        "model_ref": context["model_ref"], "source_revision": context["source_revision"],
        "coverage": {"model_file_resource_api": "PASS_SCOPED", "functions": "PASS_SCOPED",
                     "geometry_import_features": "PASS_SCOPED", "external_code": "UNVERIFIED",
                     "external_libraries_services": "UNVERIFIED"},
        "file_resources": [], "file_references": [], "unresolved_items": [], "inventory_errors": [],
    })
    def fail_publication(*_args, **_kwargs):
        raise ExecutionContractError("ARTIFACT_PUBLISH_FAILED", "synthetic atomic publisher failure",
                                     stage="post_dispatch", details={"safe_retry": False})
    from comsol_mcp import _artifact_store
    monkeypatch.setattr(_artifact_store, "publish_project_file_no_clobber", fail_publication)
    (workspace / "packages").mkdir()
    result = daemon.dispatch({"operation": "model.package",
        "arguments": {"destination": "packages/never-published.zip"},
        "execution": _execution(project, ref, request="package-publish-failure", key="package-publish-failure")})
    assert result["success"] is False, result
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN", result
    data = result["error"].get("details", {})
    assert data.get("partial_change") is True, result
    assert data.get("execution_state_unknown") is True, result
    assert data.get("safe_retry") is False, result
    recovery = data.get("recovery_artifact")
    assert isinstance(recovery, dict), result
    scratch = workspace / recovery["directory"]
    receipt_path = workspace / recovery["receipt"]
    assert scratch.is_dir() and receipt_path.is_file()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["safe_retry"] is False
    assert receipt["stage"] == "publish_package"
    assert {row["path"].rsplit("/", 1)[-1] for row in receipt["files"] if row["state"] == "RETAINED"} == {
        "model.mph", "package.zip",
    }
    for row in receipt["files"]:
        if row["state"] == "RETAINED":
            retained = workspace / row["path"]
            assert hashlib.sha256(retained.read_bytes()).hexdigest() == row["sha256"]
            assert retained.stat().st_size == row["size_bytes"]
    daemon.close()


def test_package_marks_missing_embedded_resource_unverified_in_both_manifest_sections(tmp_path, monkeypatch):
    daemon, _worker, _service, project, workspace, ref, _install_root = _environment(tmp_path)
    monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"test_stub": True})
    monkeypatch.setattr(delivery, "_dependencies_report", lambda _worker, _tag, context, *, verify_hashes: {
        "operation": "model.dependencies", "status": "INCOMPLETE", "verify_hashes": verify_hashes,
        "model_ref": context["model_ref"], "source_revision": context["source_revision"],
        "coverage": {"model_file_resource_api": "PASS_SCOPED", "functions": "PASS_SCOPED",
                     "geometry_import_features": "PASS_SCOPED", "external_code": "UNVERIFIED",
                     "external_libraries_services": "UNVERIFIED"},
        "file_resources": [{"tag": "missing_resource", "archive_members": ["resources/not-saved.bin"]}],
        "file_references": [], "unresolved_items": [], "inventory_errors": [],
    })
    (workspace / "packages").mkdir()
    packaged = daemon.dispatch({"operation": "model.package",
        "arguments": {"destination": "packages/missing-resource.zip"},
        "execution": _execution(project, ref, request="package-missing-resource", key="package-missing-resource")})
    assert packaged["success"] is True, packaged
    _assert_delivery_output_schema("model.package", packaged["data"])
    manifest = packaged["data"]["manifest"]
    assert manifest["embedded_dependencies"] == [{
        "resource_tag": "missing_resource", "state": "UNVERIFIED",
        "archive_members": ["resources/not-saved.bin"],
        "reason": "FileResource.archive names were not present as exact MPH entries",
    }]
    assert manifest["unresolved_items"] == [{
        "kind": "model_file_resource", "resource_tag": "missing_resource", "state": "UNVERIFIED",
        "archive_members": ["resources/not-saved.bin"],
        "reason": "FileResource.archive names were not present as exact MPH entries",
    }]
    verified = daemon.dispatch({"operation": "artifact.verify",
        "arguments": {"project_id": project["project_id"],
                      "artifact_id": packaged["data"]["artifact_registration"]["artifact_id"]},
        "execution": {"project_id": project["project_id"], "request_id": "verify-missing-resource-package"}})
    assert verified["success"] is True, verified
    assert verified["data"]["format_verdict"] == "INCOMPLETE"
    assert verified["data"]["member_integrity"] == "PASS"
    assert verified["data"]["dependency_closure"] == "INCOMPLETE"
    assert any(row["code"] == "EMBEDDED_DEPENDENCY_UNVERIFIED" for row in verified["data"]["findings"])
    daemon.close()


def test_dependencies_hashes_bound_saved_mph_and_all_scoped_function_cad_inputs(tmp_path, monkeypatch):
    daemon, worker, _service, project, workspace, ref, _install_root = _environment(tmp_path)
    model = worker.models["m"]
    embedded = b"MPH embedded resource bytes"
    mph_path = workspace / "source.mph"
    with zipfile.ZipFile(mph_path, "w") as mph:
        mph.writestr("Model/resources/table.dat", embedded)
    model.live_path = str(mph_path)
    model.file_resources = [{"tag": "res1", "archive": "Model/resources/table.dat"}]

    global_bytes = b"global interpolation table\n"
    component_bytes = b"component interpolation table\n"
    cad_bytes = b"synthetic CAD geometry\n"
    global_file = workspace / "inputs" / "global.csv"
    component_file = workspace / "inputs" / "component.csv"
    cad_file = workspace / "cad" / "part.step"
    for path, payload in ((global_file, global_bytes), (component_file, component_bytes), (cad_file, cad_bytes)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    monkeypatch.setattr(delivery, "_model_component_details", lambda *_: ([{"tag": "comp1", "geometries": ["geom1"]}], []))
    monkeypatch.setattr(delivery, "_feature_filename_refs", lambda *_: ([{
        "kind": "cad_geometry", "component": "comp1", "geometry": "geom1", "feature": "imp1",
        "source_path": str(cad_file),
    }], []))
    from comsol_mcp import _g3_w13
    def list_functions(_worker, _tag, arguments):
        component = arguments.get("component")
        if component is None:
            return {"functions": [{"tag": "global_fun", "type_id": "Interpolation",
                                   "path": {"segments": [{"collection": "function", "tag": "global_fun"}]}}]}
        return {"functions": [{"tag": "local_fun", "type_id": "Interpolation",
                               "path": {"segments": [{"collection": "component", "tag": component},
                                                       {"collection": "func", "tag": "local_fun"}]}}]}
    def inspect_function(_worker, _tag, arguments):
        path = arguments["path"]["segments"]
        value = global_file if path[0]["collection"] == "function" else component_file
        return {"type_id": "Interpolation", "type_properties_verified": True,
                "properties": {"filename": {"value": str(value)}}}
    monkeypatch.setattr(_g3_w13, "function_list", list_functions)
    monkeypatch.setattr(_g3_w13, "function_inspect", inspect_function)

    result = daemon.dispatch({"operation": "model.dependencies", "arguments": {"verify_hashes": True},
                              "execution": _execution(project, ref, request="dependencies-hash")})
    assert result["success"] is True, result
    _assert_delivery_output_schema("model.dependencies", result["data"])
    data = result["data"]
    assert data["coverage"]["functions"] == "PASS_SCOPED"
    assert data["coverage"]["geometry_import_features"] == "PASS_SCOPED"
    by_kind = {row["kind"] + ":" + str(row.get("tag", row.get("feature"))): row for row in data["file_references"]}
    for key, path, payload in (("function_data:global_fun", global_file, global_bytes),
                               ("function_data:local_fun", component_file, component_bytes),
                               ("cad_geometry:imp1", cad_file, cad_bytes)):
        source = by_kind[key]["authorized_source"]
        assert source["state"] == "VERIFIED"
        assert source["sha256"] == hashlib.sha256(payload).hexdigest()
        assert source["project_relative_path"] == path.relative_to(workspace).as_posix()
    resource = data["file_resources"][0]
    assert resource["verification"] == "VERIFIED_IN_SAVED_MPH"
    assert resource["embedded_sha256"] == hashlib.sha256(embedded).hexdigest()
    assert resource["embedded_size_bytes"] == len(embedded)
    assert any(row["kind"] == "external_code" and row["state"] == "UNVERIFIED" for row in data["unresolved_items"])
    assert any(row["kind"] == "external_library_or_service" and row["state"] == "UNVERIFIED"
               for row in data["unresolved_items"])
    daemon.close()


def test_dependencies_without_hash_request_does_not_open_saved_or_project_files(tmp_path, monkeypatch):
    daemon, worker, _service, project, _workspace, ref, _install_root = _environment(tmp_path)
    from comsol_mcp import _g3_w13
    monkeypatch.setattr(delivery, "_model_component_details", lambda *_: ([], []))
    monkeypatch.setattr(delivery, "_feature_filename_refs", lambda *_: ([], []))
    monkeypatch.setattr(_g3_w13, "function_list", lambda *_: {"functions": []})
    monkeypatch.setattr(delivery, "_stat_digest", lambda *_: (_ for _ in ()).throw(AssertionError("hash not requested")))
    # A bad saved path is irrelevant when verify_hashes=false: do not probe it.
    monkeypatch.setattr(worker.models["m"], "getFilePath", lambda: (_ for _ in ()).throw(AssertionError("saved MPH should not be opened")))
    result = daemon.dispatch({"operation": "model.dependencies", "arguments": {"verify_hashes": False},
                              "execution": _execution(project, ref, request="dependencies-nohash")})
    assert result["success"] is True, result
    _assert_delivery_output_schema("model.dependencies", result["data"])
    assert result["data"]["verify_hashes"] is False
    assert result["data"]["file_resources"] == []
    daemon.close()


def test_dependency_path_hashing_is_opt_in_and_never_reinterprets_external_absolute_path(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    project_root.mkdir()
    local = project_root / "input.csv"
    local.write_text("x,y\n1,2\n", encoding="utf-8")
    outside = tmp_path / "outside.csv"
    outside.write_text("secret", encoding="utf-8")
    assert delivery._project_file(project_root, "input.csv", hash_file=False)["state"] == "AUTHORIZED_PROJECT_LOCAL"
    assert delivery._project_file(project_root, "input.csv", hash_file=True)["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
    result = delivery._project_file(project_root, str(outside), hash_file=True)
    assert result["state"] == "UNVERIFIED"
    assert result["reason"] == "outside_project_root"


@pytest.mark.parametrize("reported", [
    "COMSOL Multiphysics 6.4 (开发版本: 293)",
    "COMSOL Multiphysics 6.4.0 (Build 293)",
    "COMSOL Multiphysics 6.4.0.293",
])
def test_runtime_release_normalizes_localized_and_english_build_strings(tmp_path, monkeypatch, reported):
    install = _runtime_fixture(tmp_path / "comsol-install")
    worker = _Worker(install, "127.0.0.1:24567")
    monkeypatch.setattr(worker, "getComsolVersion", lambda: reported)
    assert delivery._current_version(worker) == "6.4.0.293"
    assert delivery._parse_version_declaration("COMSOL 6.4 (开发版本: 293)") == ((6, 4, 0), 293)
    with pytest.raises(Exception):
        delivery._parse_version_declaration("COMSOL 6.4")
