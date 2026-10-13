"""One bounded public-client P0 geometry, package, rebuild and restore cycle."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import zipfile

GEOMETRY = {"segments": [{"collection": "component", "tag": "comp1"}, {"collection": "geom", "tag": "geom1"}]}
FEATURE = {"parent": GEOMETRY, "tag": "r1", "type_id": "Rectangle", "properties": [
    {"name": "size", "value": {"kind": "float64", "shape": [2], "data": [0.0025, 0.001]}}]}
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_TOTAL_OUTPUT_BYTES = 64 * 1024 * 1024
FEATURE_PATH = {"segments": [*GEOMETRY["segments"], {"collection": "feature", "tag": "r1"}]}


def size_readback(data: dict) -> dict:
    rows = data.get("properties", [])
    if (len(rows) != 1 or rows[0].get("name") != "size"
            or rows[0].get("value", {}).get("kind") != "float64"
            or rows[0]["value"].get("shape") != [2]
            or rows[0]["value"].get("data") != [0.0025, 0.001]):
        raise RuntimeError("exact native numeric Rectangle.size property readback unavailable")
    return rows[0]


def recipe(version: str) -> dict:
    return {"schema": "comsol-mcp-model-recipe", "version": 1,
            "runtime": {"comsol_versions": [version]}, "products": [], "steps": [
                {"operation_id": "parameter.set", "arguments": {"parameters": [{"name": "v3_marker", "expression": "2.5[mm]", "description": "V3 public MCP native smoke"}]}},
                {"operation_id": "geometry.feature_create", "arguments": FEATURE},
                {"operation_id": "geometry.build", "arguments": {"geometry": GEOMETRY}}]}


def geometry_readback(data: dict) -> dict:
    state = data.get("geometry_state", {})
    rows = [row for row in data.get("features", []) if row.get("tag") == "r1"]
    if (len(rows) != 1 or rows[0].get("type_id") != "Rectangle" or state.get("dimension") != 2
            or state.get("length_unit") != "m" or not state.get("object_names", {}).get("ok")
            or not state["object_names"].get("value")):
        raise RuntimeError("exact rectangle geometry readback unavailable")
    return {"feature": rows[0], "geometry_state": state}


def package_payload(path: Path, package: dict, ref: dict) -> tuple[dict, bytes]:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise RuntimeError("package exceeds 16 MiB client budget")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if (len(infos) != 2 or {row.filename for row in infos} != {"manifest.json", "model.mph"}
                or sum(row.file_size for row in infos) > MAX_ARTIFACT_BYTES
                or any(row.is_dir() or row.flag_bits & 1 for row in infos)):
            raise RuntimeError("minimal package must contain exactly bounded manifest and model")
        manifest = json.loads(archive.read("manifest.json"))
        mph = archive.read("model.mph")
    sha = hashlib.sha256(mph).hexdigest()
    if (manifest != package["manifest"] or manifest["model"]["model_ref"] != ref
            or manifest["contents"]["model.mph"] != {"sha256": sha, "size_bytes": len(mph)}
            or package["mph_sha256"] != sha or package["mph_size_bytes"] != len(mph)
            or manifest["resolved_project_dependencies"]):
        raise RuntimeError("package registered source/manifest/MPH relation differs")
    return manifest, mph


def preview_bytes(data: dict, source: dict) -> bytes:
    raw = base64.b64decode(data["preview_png_base64"], validate=True)
    if (data["artifact_id"] != source["artifact_id"] or data["source_sha256"] != source["sha256"]
            or hashlib.sha256(raw).hexdigest() != data["preview_sha256"]
            or len(raw) != data["preview_size_bytes"] or not raw.startswith(b"\x89PNG\r\n\x1a\n")
            or not data["provenance"].get("pinned_descriptor_sha256_verified_before_and_after")):
        raise RuntimeError("preview pixels do not bind to registered geometry render")
    return raw


async def run_cycle(call, *, workspace: Path, project_id: str, session_id: str,
                    model_ref: dict, revision: int, runtime_id: str,
                    attached: dict,
                    fixture: bytes, summary: dict, evidence: Path,
                    verify_artifact, verify_parameter, digest) -> tuple[dict, int]:
    original_ref = model_ref
    artifacts = []

    def local_budget():
        files = [p for p in workspace.rglob("*") if p.is_file()]
        if any(p.is_symlink() or p.stat().st_size > MAX_ARTIFACT_BYTES for p in files) or sum(p.stat().st_size for p in files) > MAX_TOTAL_OUTPUT_BYTES:
            raise RuntimeError("P0 package activity output budget exceeded")

    async def action(op, body, *, ref=None, rev=None):
        body = {"project_id": project_id, "session_id": session_id, **body}
        if ref is not None:
            body["model_ref"] = ref
        value = await call(op, body, ref=ref, revision=rev)
        local_budget()
        return value

    async def register(path: str, role: str):
        metadata = {"artifact_type": "application/json", "format_version": "comsol-mcp-model-recipe/1"} if role == "model_recipe" else {}
        value = await action("artifact.register", {"path": path, "role": role, "classification": "project_local", "idempotency_key": "new", **metadata})
        verify_artifact(workspace, value["data"])
        artifacts.append(value["data"])
        return value["data"]

    built = await action("geometry.feature_create", {**FEATURE, "expected_revision": revision, "idempotency_key": "new"}, ref=model_ref, rev=revision)
    revision = built["execution"]["revision"]
    built = await action("geometry.build", {"geometry": GEOMETRY, "idempotency_key": "new"}, ref=model_ref, rev=revision)
    revision = built["execution"]["revision"]
    observed = await action("geometry.inspect", {"path": GEOMETRY}, ref=model_ref)
    summary["package_original_geometry"] = geometry_readback(observed["data"])
    size = await action("node.property_get", {"path": FEATURE_PATH, "names": ["size"]}, ref=model_ref)
    summary["package_original_size"] = size_readback(size["data"])
    rendered = await action("plot.geometry_render", {"geometry": GEOMETRY, "mode": "geometry", "idempotency_key": "new",
        "options": {"width": 256, "height": 192, "format": "png", "destination": "p0-geometry.png", "allow_overwrite": False}}, ref=model_ref, rev=revision)
    revision = rendered["execution"]["revision"]
    rendered_path = workspace / "p0-geometry.png"
    if digest(rendered_path) != rendered["data"]["sha256"]:
        raise RuntimeError("native geometry image differs from returned SHA")
    image = await register("p0-geometry.png", "image")
    preview = await action("artifact.preview", {"artifact_id": image["artifact_id"], "options": {"schema_version": 1, "kind": "image", "max_width": 128, "max_height": 128}})
    with (evidence / "geometry-preview.png").open("xb") as output:
        output.write(preview_bytes(preview["data"], image))
    summary["package_preview"] = {"render": rendered["data"], "registered": image, "preview": {k: v for k, v in preview["data"].items() if k != "preview_png_base64"}}
    package = await action("model.package", {"destination": "p0-model-package.zip", "include_solution": False,
        "expected_revision": revision, "idempotency_key": "new"}, ref=model_ref, rev=revision)
    revision = package["execution"]["revision"]
    registration = package["data"]["artifact_registration"]
    package_path = verify_artifact(workspace, registration)
    artifacts.append(registration)
    manifest, mph = package_payload(package_path, package["data"], original_ref)
    verified = await action("artifact.verify", {"artifact_id": registration["artifact_id"]})
    if (verified["data"].get("format_verdict") not in {"INCOMPLETE", "VERIFIED_DECLARED_PACKAGE_CONTENT"}
            or verified["data"].get("member_integrity") != "PASS"
            or verified["data"].get("format_schema") != "comsol-mcp-model-package"
            or any(row.get("severity") == "ERROR" for row in verified["data"].get("findings", []))):
        raise RuntimeError("production registered package verifier did not verify declared content")
    summary["package_integrity"] = verified["data"]
    java_path = workspace / "P0ExportJava.java"
    with java_path.open("xb") as output:
        output.write(fixture)
    java = await register(java_path.name, "java-source")
    executed = await action("code.execute_java", {"source_artifact": java["path"], "entrypoint": "P0ExportJava", "arguments": {"workspace": str(workspace)},
        "mode": "trusted", "timeout_s": 30, "expected_revision": revision, "idempotency_key": "new"}, ref=model_ref, rev=revision)
    revision = executed["execution"]["revision"]
    exported = await register("P0Export.java", "java-source")
    witness = executed["data"]["readback"]["readback"]
    if (executed["data"].get("source_sha256") != java["sha256"] or witness.get("destination") != str(workspace / "P0Export.java")
            or witness.get("model_tag") != model_ref["model_tag"] or witness.get("bytes") != exported["size"]
            or witness.get("semantics") != "EXPORTED_SOURCE_ONLY_NOT_EXECUTED"):
        raise RuntimeError("exported Java source not bound to sole injected model and registered bytes")
    observation = executed["execution"].get("runtime_observation", {})
    expected_identity = {"runtime_id": runtime_id, "worker_instance_id": attached["worker_instance_id"],
        "connection_epoch": attached["worker_epoch"], "server_instance_id": attached["server_instance_id"],
        "endpoint": attached["endpoint"]["host"] + ":" + str(attached["endpoint"]["port"])}
    if (observation.get("status") != "OBSERVED" or observation.get("installation_runtime_id") != runtime_id
            or executed["execution"].get("model_ref") != original_ref
            or observation.get("runtime_identity") != expected_identity):
        raise RuntimeError("typed rebuild recipe requires exact code-producer observed runtime version")
    recipe_path = workspace / "p0-model-recipe.json"
    with recipe_path.open("x", encoding="utf-8") as output:
        json.dump(recipe(observation["comsol_version"]), output, sort_keys=True, allow_nan=False)
    recipe_source = await register(recipe_path.name, "model_recipe")
    created = await action("model.create", {"label": "p0-typed-rebuild", "dimension": 2, "idempotency_key": "new"})
    rebuilt_ref = created["data"]["model_ref"]
    if rebuilt_ref == original_ref or rebuilt_ref["model_tag"] == original_ref["model_tag"]:
        raise RuntimeError("rebuild model is not a new exact ModelRef")
    rebuilt = await action("model.rebuild_target", {"target_runtime_id": runtime_id, "recipe_artifact": recipe_source["artifact_id"],
        "expected_revision": created["execution"]["revision"], "idempotency_key": "new"}, ref=rebuilt_ref, rev=created["execution"]["revision"])
    if rebuilt["data"].get("status") != "APPLIED" or rebuilt["data"].get("completed_steps") != 3 or rebuilt["data"].get("model_ref") != rebuilt_ref:
        raise RuntimeError("typed recipe rebuild not fully observed on its new ModelRef")
    revision = rebuilt["execution"]["revision"]
    parameter = await action("parameter.get", {"names": ["v3_marker"], "evaluate": True}, ref=rebuilt_ref)
    verify_parameter(parameter, "2.5[mm]", "V3 public MCP native smoke", 0.0025)
    geometry = await action("geometry.inspect", {"path": GEOMETRY}, ref=rebuilt_ref)
    summary["package_rebuilt_geometry"] = geometry_readback(geometry["data"])
    size = await action("node.property_get", {"path": FEATURE_PATH, "names": ["size"]}, ref=rebuilt_ref)
    summary["package_rebuilt_size"] = size_readback(size["data"])
    saved = await action("model.save", {"destination": "p0-rebuilt.mph", "overwrite": False, "include_solution": False,
        "expected_revision": revision, "idempotency_key": "new"}, ref=rebuilt_ref, rev=revision)
    saved_path = workspace / saved["data"]["destination"]
    if digest(saved_path) != saved["data"]["sha256"] or saved_path.stat().st_size != saved["data"]["size_bytes"]:
        raise RuntimeError("rebuild saved MPH bytes differ")
    with (workspace / "p0-restored.mph").open("xb") as output:
        output.write(mph)
    restored = await register("p0-restored.mph", "restored_model")
    if restored["sha256"] != manifest["contents"]["model.mph"]["sha256"]:
        raise RuntimeError("restored MPH registered digest differs from complete package payload")
    loaded = await action("model.load", {"artifact_id": restored["artifact_id"], "idempotency_key": "new"})
    restored_ref = loaded["data"]["model_ref"]
    if restored_ref["model_tag"] in {original_ref["model_tag"], rebuilt_ref["model_tag"]}:
        raise RuntimeError("restoration did not bind a new exact ModelRef")
    parameter = await action("parameter.get", {"names": ["v3_marker"], "evaluate": True}, ref=restored_ref)
    verify_parameter(parameter, "2.5[mm]", "V3 public MCP native smoke", 0.0025)
    geometry = await action("geometry.inspect", {"path": GEOMETRY}, ref=restored_ref)
    summary["package_restored_geometry"] = geometry_readback(geometry["data"])
    size = await action("node.property_get", {"path": FEATURE_PATH, "names": ["size"]}, ref=restored_ref)
    summary["package_restored_size"] = size_readback(size["data"])
    for artifact in artifacts:
        verify_artifact(workspace, artifact)
    summary["package_cycle"] = {"status": "PASS_NATIVE_SCOPED_GEOMETRY_PREVIEW_PACKAGE_TYPED_REBUILD_RESTORE", "package": package["data"],
        "exported_java": exported, "recipe": recipe_source, "rebuild": rebuilt["data"], "rebuild_save": saved["data"],
        "restored": loaded["data"], "registered_artifacts": artifacts,
        "limits": ["exported Java is registered source evidence only, not executed reconstruction", "package declared-content integrity does not prove portability", "model package payload restoration only, not W26 database/system restore"]}
    return restored_ref, loaded["execution"]["revision"]
