"""Managed dependencies, package publication, and declarative rebuild routes.

These operations stay inside the existing ManagedBackend write ticket.  A
recipe is a content-addressed registered JSON artifact, preflighted as a whole,
then each declared G3 operation is called directly on the same bound Worker
callback.  No nested queue, generic Java command, or caller-provided context is
used here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import tempfile
import zipfile
from typing import Any, Mapping, Sequence

from ._execution_contract import ExecutionContractError, PreWriteRefusal

RECIPE_SCHEMA = "comsol-mcp-model-recipe"
RECIPE_FORMAT_VERSION = "comsol-mcp-model-recipe/1"
PACKAGE_SCHEMA = "comsol-mcp-model-package"
PACKAGE_FORMAT_VERSION = "comsol-mcp-model-package/1"
MAX_RECIPE_BYTES = 1024 * 1024
MAX_RECIPE_STEPS = 128
MAX_RECIPE_DEPTH = 32
MAX_RECIPE_PRODUCTS = 32
MAX_INVENTORY_ROWS = 512
MAX_MPH_ARCHIVE_ENTRIES = 20_000
MAX_EMBEDDED_RESOURCE_MEMBERS = 512
MAX_EMBEDDED_RESOURCE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_EMBEDDED_RESOURCE_TOTAL_BYTES = 512 * 1024 * 1024
_FILE_OPERATIONS = frozenset({
    "geometry.import", "function.data_import", "function.data_reload",
    "parameter.import", "parameter.export", "material.import",
})
_FILE_KEYS = frozenset({
    "file_path", "local_path", "resolved_path", "filename", "artifact_path",
    "source_path", "output_path", "working_directory", "directory",
    "java_code", "python_code", "script", "source_code", "command",
})
_PATH_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _body_and_context(arguments: Mapping[str, Any], allowed: set[str]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    if not isinstance(arguments, Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "model delivery arguments must be an object")
    context = arguments.get("_managed_context")
    if not isinstance(context, Mapping):
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "model delivery requires server-created managed context")
    body = {key: value for key, value in arguments.items() if key != "_managed_context" and key not in {
        "project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id",
    }}
    unknown = set(body) - allowed
    if unknown:
        raise PreWriteRefusal("INVALID_REQUEST", f"unsupported model delivery fields: {sorted(unknown)}")
    return body, context


def _require_context(context: Mapping[str, Any], *keys: str) -> None:
    missing = [key for key in keys if context.get(key) is None]
    if missing:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", f"managed context lacks {', '.join(missing)}")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _stat_digest(path: Path) -> tuple[str, int, dict[str, int]]:
    from ._artifact_store import _hash_regular_artifact
    digest, identity = _hash_regular_artifact(path)
    return digest, int(identity["size"]), identity


def _project_file(root: Path, raw: Any, *, hash_file: bool) -> dict[str, Any]:
    """Resolve only an authorized, project-local regular path; never probe outside."""
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components

    if not isinstance(raw, str) or not raw.strip():
        return {"state": "UNVERIFIED", "project_relative_path": None, "reason": "filename_not_a_nonempty_string"}
    # A Windows absolute path is external on every POSIX host and must not be
    # reinterpreted as a relative filename.
    if (_PATH_DRIVE.match(raw) or PureWindowsPath(raw).drive) and os.name != "nt":
        return {"state": "UNVERIFIED", "project_relative_path": None, "reason": "outside_project_root"}
    root_real = root.resolve(strict=True)
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            relative = path.relative_to(root_real)
        except ValueError:
            # Do not stat or open an outside absolute path.
            return {"state": "UNVERIFIED", "project_relative_path": None, "reason": "outside_project_root"}
    else:
        relative = Path(raw)
        if any(part in {"", ".", ".."} for part in relative.parts):
            return {"state": "UNVERIFIED", "project_relative_path": None, "reason": "path_traversal_or_noncanonical"}
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return {"state": "UNVERIFIED", "project_relative_path": None, "reason": "path_traversal_or_noncanonical"}
    rel = PurePosixPath(*relative.parts).as_posix()
    try:
        authorized = ArtifactStore(root_real).resolve_safe_path(rel, allow_overwrite=True)
        _assert_no_symlink_components(authorized, field="model dependency")
        if not authorized.exists():
            return {"state": "MISSING", "project_relative_path": rel, "reason": "project_local_file_missing"}
        if not authorized.is_file():
            return {"state": "UNVERIFIED", "project_relative_path": rel, "reason": "not_a_regular_file"}
        import stat
        identity = authorized.stat(follow_symlinks=False)
        if not stat.S_ISREG(identity.st_mode):
            return {"state": "UNVERIFIED", "project_relative_path": rel, "reason": "not_a_regular_file"}
        if not hash_file:
            return {"state": "AUTHORIZED_PROJECT_LOCAL", "project_relative_path": rel,
                    "size_bytes": int(identity.st_size)}
        digest, size, _ = _stat_digest(authorized)
        return {"state": "VERIFIED", "project_relative_path": rel, "sha256": digest, "size_bytes": size}
    except ExecutionContractError as exc:
        return {"state": "UNVERIFIED", "project_relative_path": rel, "reason": exc.code}
    except OSError as exc:
        return {"state": "UNVERIFIED", "project_relative_path": rel, "reason": type(exc).__name__}


def _archive_names(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else list(value) if isinstance(value, (tuple, list)) else []
    safe = []
    for item in values:
        if not isinstance(item, str) or not item:
            continue
        posix = PurePosixPath(item.replace("\\", "/"))
        if posix.is_absolute() or _PATH_DRIVE.match(item) or any(part in {"", ".", ".."} for part in posix.parts):
            continue
        safe.append(posix.as_posix())
    return safe


def _resource_inventory(worker: Any, model_tag: str) -> list[dict[str, Any]]:
    model = worker.client().model(model_tag)
    rows = model.java.getFileResourceInventory()
    if not isinstance(rows, (list, tuple)):
        raise PreWriteRefusal("DEPENDENCY_INVENTORY_UNAVAILABLE", "COMSOL FileResource inventory returned an invalid shape")
    if len(rows) > MAX_INVENTORY_ROWS:
        raise PreWriteRefusal("DEPENDENCY_INVENTORY_LIMIT", "COMSOL FileResource inventory exceeds the 512-entry bound")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping):
            raise PreWriteRefusal("DEPENDENCY_INVENTORY_UNAVAILABLE", "COMSOL FileResource inventory row is malformed")
        tag = item.get("tag")
        if not isinstance(tag, str) or not tag or tag in seen:
            raise PreWriteRefusal("DEPENDENCY_INVENTORY_UNAVAILABLE", "COMSOL FileResource tags are empty or duplicated")
        seen.add(tag)
        archives = _archive_names(item.get("archive"))
        if len(archives) > MAX_EMBEDDED_RESOURCE_MEMBERS:
            raise PreWriteRefusal("DEPENDENCY_INVENTORY_LIMIT", "one FileResource lists more than 512 archive members")
        result.append({"tag": tag, "archive_members": archives, "archive_status": "ENUMERATED" if archives else "UNAVAILABLE"})
    if sum(len(row["archive_members"]) for row in result) > MAX_EMBEDDED_RESOURCE_MEMBERS:
        raise PreWriteRefusal("DEPENDENCY_INVENTORY_LIMIT", "FileResource archive-member inventory exceeds the 512-entry bound")
    return result


def _path_values_from_function(data: Mapping[str, Any]) -> list[str]:
    def value_of(row: Any) -> Any:
        return row.get("value") if isinstance(row, Mapping) else row

    out: list[str] = []
    settings = data.get("settings")
    if isinstance(settings, Mapping):
        for row in settings.values():
            if isinstance(row, Mapping):
                name = str(row.get("property", row.get("name", ""))).casefold()
                value = value_of(row.get("value"))
                if name == "filename" and isinstance(value, str) and value:
                    out.append(value)
    properties = data.get("properties")
    if isinstance(properties, Mapping):
        value = value_of(properties.get("filename"))
        if isinstance(value, str) and value:
            out.append(value)
    return out


def _model_component_details(worker: Any, model_tag: str) -> tuple[list[Mapping[str, Any]], list[str]]:
    from ._model_ops import _model_tree_data

    try:
        tree = _model_tree_data(worker.client().model(model_tag))
    except Exception as exc:
        return [], [f"model_structure:{type(exc).__name__}"]
    components = tree.get("component_details") if isinstance(tree, Mapping) else None
    if not isinstance(components, list):
        return [], ["model_structure:component_inventory_malformed"]
    rows: list[Mapping[str, Any]] = []
    errors: list[str] = []
    for index, row in enumerate(components):
        if not isinstance(row, Mapping) or not isinstance(row.get("tag"), str) or not row.get("tag"):
            errors.append(f"model_structure:component_row_{index}_malformed")
            continue
        rows.append(row)
    return rows, errors


def _feature_filename_refs(
    worker: Any, model_tag: str, components: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    from . import _g3_w14
    refs: list[dict[str, Any]] = []
    errors: list[str] = []
    visited = 0
    for component_row in components:
        component = component_row.get("tag")
        geometries = component_row.get("geometries")
        if not isinstance(geometries, list):
            errors.append(f"geometry:{component}/geometry_inventory_malformed")
            continue
        for geom in geometries:
            if not isinstance(geom, str) or not geom:
                errors.append(f"geometry:{component}/geometry_tag_malformed")
                continue
            path = {"segments": [{"collection": "component", "tag": component}, {"collection": "geom", "tag": geom}]}
            try:
                inv = _g3_w14.geometry_inspect(worker, model_tag, {"path": path})
                for feature in inv.get("features", []):
                    visited += 1
                    if visited > MAX_INVENTORY_ROWS:
                        raise PreWriteRefusal("DEPENDENCY_INVENTORY_LIMIT", "geometry dependency scan exceeded 512 features")
                    if not isinstance(feature, Mapping) or feature.get("type_id") != "Import":
                        continue
                    tag = feature.get("tag")
                    if not isinstance(tag, str) or not tag:
                        continue
                    details = _g3_w14.geometry_inspect(worker, model_tag, {
                        "path": path, "feature_properties": {tag: ["filename"]},
                    })
                    row = next((candidate for candidate in details.get("features", [])
                                if isinstance(candidate, Mapping) and candidate.get("tag") == tag), {})
                    values = row.get("property_values") if isinstance(row, Mapping) else None
                    if isinstance(values, list):
                        for value in values:
                            if isinstance(value, Mapping) and value.get("property") == "filename":
                                raw = value.get("value")
                                if isinstance(raw, str) and raw:
                                    refs.append({"kind": "cad_geometry", "component": component,
                                                 "geometry": geom, "feature": tag, "source_path": raw})
            except PreWriteRefusal:
                raise
            except Exception as exc:
                errors.append(f"geometry:{component}/{geom}:{type(exc).__name__}")
    return refs, errors


def _dependencies_report(worker: Any, model_tag: str, context: Mapping[str, Any], *, verify_hashes: bool) -> dict[str, Any]:
    from . import _g3_w13

    _require_context(context, "project_root", "project_id", "model_ref")
    root = Path(str(context["project_root"])).resolve(strict=True)
    resources = _resource_inventory(worker, model_tag)
    file_refs: list[dict[str, Any]] = []
    coverage = {
        "model_file_resource_api": "PASS_SCOPED",
        "functions": "PASS_SCOPED",
        "geometry_import_features": "PASS_SCOPED",
        "external_code": "UNVERIFIED",
        "external_libraries_services": "UNVERIFIED",
    }
    errors: list[str] = []
    components, structure_errors = _model_component_details(worker, model_tag)
    errors.extend(structure_errors)
    function_scopes: list[tuple[str, dict[str, Any]]] = [("global", {})]
    function_scopes.extend((f"component:{row['tag']}", {"component": row["tag"]}) for row in components)
    function_count = 0
    for scope_name, scope_args in function_scopes:
        try:
            functions = _g3_w13.function_list(worker, model_tag, scope_args)
        except Exception as exc:
            errors.append(f"function_inventory:{scope_name}:{type(exc).__name__}")
            continue
        rows = functions.get("functions") if isinstance(functions, Mapping) else None
        if not isinstance(rows, list):
            errors.append(f"function_inventory:{scope_name}:malformed")
            continue
        function_count += len(rows)
        if function_count > MAX_INVENTORY_ROWS:
            raise PreWriteRefusal("DEPENDENCY_INVENTORY_LIMIT", "function inventory exceeds 512 entries")
        for index, row in enumerate(rows):
            if (not isinstance(row, Mapping) or not isinstance(row.get("path"), Mapping)
                    or not isinstance(row.get("tag"), str) or not row.get("tag")):
                errors.append(f"function:{scope_name}:row_{index}_malformed")
                continue
            if not isinstance(row.get("type_id"), str) or not row.get("type_id"):
                errors.append(f"function:{scope_name}:{row['tag']}:type_unavailable")
                continue
            try:
                inspected = _g3_w13.function_inspect(worker, model_tag, {"path": row["path"]})
                if not isinstance(inspected, Mapping) or inspected.get("type_properties_verified") is not True:
                    errors.append(f"function:{scope_name}:{row['tag']}:properties_unverified")
                    continue
                kind = str(inspected.get("type_id") or row["type_id"])
                for raw in _path_values_from_function(inspected):
                    file_refs.append({"kind": "function_data", "tag": row["tag"],
                                      "function_type": kind, "source_path": raw})
            except Exception as exc:
                errors.append(f"function:{scope_name}:{row['tag']}:{type(exc).__name__}")
    cad_refs, cad_errors = _feature_filename_refs(worker, model_tag, components)
    file_refs.extend(cad_refs)
    errors.extend(cad_errors)
    if any(item.startswith(("function:", "function_inventory:", "model_structure:")) for item in errors):
        coverage["functions"] = "PARTIAL"
    if any(item.startswith(("geometry:", "model_structure:")) for item in errors):
        coverage["geometry_import_features"] = "PARTIAL"
    if any(resource.get("archive_status") != "ENUMERATED" for resource in resources):
        coverage["model_file_resource_api"] = "PARTIAL"
    for row in file_refs:
        row["authorized_source"] = _project_file(root, row.pop("source_path"), hash_file=verify_hashes)
    for resource in resources:
        resource["embedded_sha256"] = None
        resource["embedded_size_bytes"] = None
        resource["verification"] = "UNVERIFIED"
    if verify_hashes:
        from ._artifact_store import ArtifactStore, _assert_no_symlink_components, _open_pinned_artifact, _assert_pinned_artifact_stable
        model = worker.client().model(model_tag)
        try:
            saved_path = str(model.java.getFilePath() or "")
        except Exception:
            saved_path = ""
        saved = (_project_file(root, saved_path, hash_file=True) if saved_path
                 else {"state": "UNVERIFIED", "reason": "model_not_saved"})
        if saved.get("state") == "VERIFIED":
            mph = ArtifactStore(root).resolve_safe_path(saved["project_relative_path"], allow_overwrite=True)
            handle, before = _open_pinned_artifact(mph)
            try:
                with zipfile.ZipFile(handle, "r") as archive:
                    names = set(archive.namelist())
                    for resource in resources:
                        found = False
                        for name in resource["archive_members"]:
                            if name not in names:
                                continue
                            digest = hashlib.sha256()
                            size = 0
                            with archive.open(name, "r") as member:
                                for chunk in iter(lambda: member.read(1024 * 1024), b""):
                                    size += len(chunk)
                                    digest.update(chunk)
                            resource.update({"embedded_sha256": digest.hexdigest(),
                                             "embedded_size_bytes": size,
                                             "verification": "VERIFIED_IN_SAVED_MPH"})
                            found = True
                            break
                        if not found:
                            resource["verification"] = "UNVERIFIED_ARCHIVE_MEMBER_NOT_FOUND"
                _assert_pinned_artifact_stable(mph, before, os.fstat(handle.fileno()))
            except (OSError, zipfile.BadZipFile):
                for resource in resources:
                    resource["verification"] = "UNVERIFIED_MPH_READ_FAILED"
            finally:
                handle.close()
        else:
            for resource in resources:
                resource["verification"] = "UNVERIFIED_MODEL_MPH_NOT_AUTHORIZED"
        for row in file_refs:
            if row["authorized_source"].get("state") in {"MISSING", "UNVERIFIED"}:
                row["verification"] = row["authorized_source"]["state"]
            elif row["authorized_source"].get("state") == "AUTHORIZED_PROJECT_LOCAL":
                row["verification"] = "NOT_REQUESTED"
            else:
                row["verification"] = "VERIFIED_PROJECT_LOCAL_SHA256"
    else:
        for row in file_refs:
            row["verification"] = "NOT_REQUESTED"
    unresolved = []
    for row in file_refs:
        if row.get("verification") in {"MISSING", "UNVERIFIED", "UNVERIFIED_ARCHIVE_MEMBER_NOT_FOUND", "NOT_REQUESTED"}:
            unresolved.append({"kind": row["kind"], "tag": row.get("tag"),
                               "component": row.get("component"), "geometry": row.get("geometry"),
                               "feature": row.get("feature"), "state": row.get("verification"),
                               "reason": row.get("authorized_source", {}).get("reason")})
    for resource in resources:
        if resource.get("archive_status") != "ENUMERATED":
            unresolved.append({"kind": "model_file_resource", "tag": resource["tag"],
                               "state": "UNVERIFIED", "reason": "FileResource.archive returned no safe archive member"})
        elif resource.get("verification") != "VERIFIED_IN_SAVED_MPH":
            unresolved.append({"kind": "model_file_resource", "tag": resource["tag"],
                               "state": resource.get("verification", "UNVERIFIED"),
                               "reason": "embedded archive member was not verified"})
    unresolved.extend({"kind": "inventory_scope", "state": "UNVERIFIED", "reason": error}
                      for error in errors)
    unresolved.append({"kind": "external_code", "state": "UNVERIFIED",
                       "reason": "COMSOL worker does not provide a complete external-code inventory"})
    unresolved.append({"kind": "external_library_or_service", "state": "UNVERIFIED",
                       "reason": "linked libraries and external services are outside Model.FileResourceList"})
    model_ref = context.get("model_ref")
    revision = context.get("source_revision")
    return {
        "data_schema_version": 1,
        "operation": "model.dependencies",
        # INCOMPLETE is a successful bounded inventory result token. Closure
        # status remains explicit in coverage/unresolved_items and is never
        # promoted to a full dependency PASS.
        "status": "INCOMPLETE",
        "verify_hashes": verify_hashes,
        "model_ref": model_ref,
        "source_revision": revision,
        "coverage": coverage,
        "file_resources": resources,
        "file_references": file_refs,
        "unresolved_items": unresolved,
        "inventory_errors": errors,
        "scope_note": "This reports the public Model.file()/file(tag).archive() inventory and implemented function/CAD filename readers. It is not a complete external dependency closure; resource() temporary paths are never read.",
    }


def dependencies(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_and_context(arguments, {"verify_hashes"})
    verify_hashes = body.get("verify_hashes", False)
    if type(verify_hashes) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "verify_hashes must be a boolean")
    return _dependencies_report(worker, model_tag, context, verify_hashes=verify_hashes)


def _hash_embedded_resources(mph_path: Path, resources: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    from ._artifact_store import _open_pinned_artifact, _assert_pinned_artifact_stable
    handle, before = _open_pinned_artifact(mph_path)
    try:
        with zipfile.ZipFile(handle, "r") as mph:
            infos = mph.infolist()
            if len(infos) > MAX_MPH_ARCHIVE_ENTRIES:
                raise ExecutionContractError("MODEL_PACKAGE_FORMAT_UNVERIFIED", "saved MPH exceeds the nested archive entry bound",
                                             stage="post_dispatch")
            members: dict[str, zipfile.ZipInfo] = {}
            seen: set[str] = set()
            for info in infos:
                name = info.filename
                parts = name.split("/")
                normalized = name[:-1] if info.is_dir() and name.endswith("/") else name
                if (not normalized or name.startswith("/") or "\\" in name
                        or any(part in {"", ".", ".."} for part in normalized.split("/"))):
                    raise ExecutionContractError("MODEL_PACKAGE_FORMAT_UNVERIFIED", "saved MPH contains an unsafe archive member",
                                                 stage="post_dispatch")
                mode = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
                if (name in seen or (info.is_dir() and mode not in (0, stat.S_IFDIR))
                        or (not info.is_dir() and mode not in (0, stat.S_IFREG))):
                    raise ExecutionContractError("MODEL_PACKAGE_FORMAT_UNVERIFIED", "saved MPH contains a duplicate or special archive member",
                                                 stage="post_dispatch")
                seen.add(name)
                if not info.is_dir():
                    members[normalized] = info
            total_hashed = 0
            for resource in resources:
                missing: list[str] = []
                unresolved_reason: str | None = None
                for member_name in resource.get("archive_members", []):
                    info = members.get(member_name)
                    if info is None:
                        missing.append(member_name)
                        continue
                    if info.file_size > MAX_EMBEDDED_RESOURCE_MEMBER_BYTES:
                        missing.append(member_name)
                        unresolved_reason = "embedded FileResource member exceeds the 64 MiB hash limit"
                        continue
                    if total_hashed + info.file_size > MAX_EMBEDDED_RESOURCE_TOTAL_BYTES:
                        missing.append(member_name)
                        unresolved_reason = "embedded FileResource members exceed the 512 MiB aggregate hash limit"
                        continue
                    digest = hashlib.sha256()
                    size = 0
                    with mph.open(info, "r") as member:
                        for block in iter(lambda: member.read(1024 * 1024), b""):
                            size += len(block)
                            if size > MAX_EMBEDDED_RESOURCE_MEMBER_BYTES:
                                raise ExecutionContractError("MODEL_PACKAGE_FORMAT_UNVERIFIED", "embedded FileResource exceeded its hash limit while streaming",
                                                             stage="post_dispatch")
                            digest.update(block)
                    if size != info.file_size:
                        raise ExecutionContractError("MODEL_PACKAGE_FORMAT_UNVERIFIED", "embedded FileResource size changed while hashing",
                                                     stage="post_dispatch")
                    total_hashed += size
                    rows.append({"resource_tag": resource["tag"], "archive_member": member_name,
                                 "sha256": digest.hexdigest(), "size_bytes": size,
                                 "resolution": "VERIFIED_EMBEDDED_MPH_MEMBER"})
                if missing:
                    unresolved.append({"resource_tag": resource["tag"], "state": "UNVERIFIED",
                                       "archive_members": missing,
                                       "reason": unresolved_reason or "FileResource.archive names were not present as exact MPH entries"})
        _assert_pinned_artifact_stable(mph_path, before, os.fstat(handle.fileno()))
    except (OSError, zipfile.BadZipFile) as exc:
        raise PreWriteRefusal("MODEL_PACKAGE_FORMAT_UNVERIFIED", "COMSOL saveCopy output is not a readable MPH archive",
                              details={"cause_type": type(exc).__name__}) from exc
    finally:
        handle.close()
    return rows, unresolved


def _source_refusal(report: Mapping[str, Any]) -> None:
    blocked = []
    for row in report.get("file_references", []):
        source = row.get("authorized_source") if isinstance(row, Mapping) else None
        if isinstance(source, Mapping) and source.get("state") in {"MISSING", "UNVERIFIED"}:
            blocked.append({"kind": row.get("kind"), "state": source.get("state"), "reason": source.get("reason")})
    if blocked:
        raise PreWriteRefusal("DEPENDENCY_NOT_AUTHORIZED", "model package has missing or unauthorized referenced files",
                              details={"unresolved_file_references": blocked})


def _resolved_project_dependencies(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for row in report.get("file_references", []):
        source = row.get("authorized_source") if isinstance(row, Mapping) else None
        if not isinstance(source, Mapping) or source.get("state") != "VERIFIED":
            continue
        relative = source.get("project_relative_path")
        digest = source.get("sha256")
        size = source.get("size_bytes")
        if not isinstance(relative, str) or not isinstance(digest, str) or type(size) is not int:
            raise PreWriteRefusal("DEPENDENCY_INVENTORY_UNAVAILABLE", "verified dependency has an incomplete pinned receipt")
        item = by_path.get(relative)
        if item is None:
            path_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
            item = {"project_relative_path": relative,
                    "package_path": f"dependencies/{digest}/{path_key}.bin",
                    "sha256": digest, "size_bytes": size}
            by_path[relative] = item
        elif item["sha256"] != digest or item["size_bytes"] != size:
            raise PreWriteRefusal("DEPENDENCY_CHANGED", "one project path resolved to inconsistent dependency identities")
        if isinstance(row, dict):
            row["package_path"] = item["package_path"]
    return list(by_path.values())


def _write_project_dependencies(root: Path, archive: zipfile.ZipFile,
                                dependencies: Sequence[Mapping[str, Any]]) -> None:
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components, _open_pinned_artifact, _assert_pinned_artifact_stable
    store = ArtifactStore(root)
    for item in dependencies:
        source_path = store.resolve_safe_path(item["project_relative_path"], allow_overwrite=True)
        _assert_no_symlink_components(source_path, field="model package dependency")
        source, before = _open_pinned_artifact(source_path)
        info = zipfile.ZipInfo(item["package_path"])
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o600 << 16
        digest = hashlib.sha256()
        copied = 0
        try:
            with archive.open(info, "w") as out:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
                    copied += len(block)
                    out.write(block)
            _assert_pinned_artifact_stable(source_path, before, os.fstat(source.fileno()))
        finally:
            source.close()
        if copied != item["size_bytes"] or digest.hexdigest() != item["sha256"]:
            raise ExecutionContractError("ARTIFACT_CHANGED", "a project dependency changed while packaging",
                                         stage="post_dispatch", details={"project_relative_path": item["project_relative_path"]})


def _retain_package_failure(root: Path, scratch: Path, context: Mapping[str, Any], *,
                            destination: str, stage: str, cause: BaseException,
                            save_dispatched: bool, save_receipt: Any,
                            publication_dispatched: bool, registered: bool) -> dict[str, Any]:
    """Write a project-relative recovery receipt and retain all generated bytes."""
    scratch_relative = scratch.relative_to(root).as_posix()
    files = []
    for name in ("model.mph", "package.zip"):
        path = scratch / name
        relative = f"{scratch_relative}/{name}"
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                files.append({"path": relative, "state": "NOT_REGULAR_FILE"})
                continue
            digest, size, _identity = _stat_digest(path)
            files.append({"path": relative, "state": "RETAINED", "sha256": digest,
                          "size_bytes": size})
        except FileNotFoundError:
            files.append({"path": relative, "state": "NOT_CREATED"})
        except Exception as exc:
            files.append({"path": relative, "state": "RETAINED_HASH_UNAVAILABLE",
                          "cause_type": type(exc).__name__})
    payload = {
        "schema": "comsol-mcp-model-package-failure",
        "version": 1,
        "request_id": str(context.get("request_id", "")),
        "model_ref": context.get("model_ref"),
        "source_revision": context.get("source_revision"),
        "destination": destination,
        "stage": stage,
        "safe_retry": False,
        "save_model_package": "DISPATCHED" if save_dispatched else "NOT_DISPATCHED",
        "save_receipt_type": type(save_receipt).__name__ if save_receipt is not None else None,
        "publication": "DISPATCHED" if publication_dispatched else "NOT_DISPATCHED",
        "artifact_registration": "REGISTERED" if registered else "NOT_CONFIRMED",
        "cause_type": type(cause).__name__,
        "cause_code": getattr(cause, "code", None),
        "files": files,
    }
    receipt_relative = f"{scratch_relative}/failure_receipt.json"
    receipt_path = scratch / "failure_receipt.json"
    try:
        with receipt_path.open("xb") as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        receipt_state = "WRITTEN"
    except Exception as exc:
        receipt_state = f"WRITE_FAILED:{type(exc).__name__}"
    return {
        "recovery_artifact": {
            "directory": scratch_relative,
            "receipt": receipt_relative,
            "receipt_status": receipt_state,
            "files": files,
        },
        "partial_change": True,
        "execution_state_unknown": True,
        "safe_retry": False,
    }


def package(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_and_context(arguments, {"destination", "include_solution"})
    _require_context(context, "project_root", "project_id", "host_identity", "engine_host_identity",
                     "operation_store", "operation_id", "request_id", "source_revision", "model_ref")
    destination = body.get("destination")
    include_solution = body.get("include_solution", True)
    if not isinstance(destination, str) or not destination.strip():
        raise PreWriteRefusal("INVALID_REQUEST", "destination must be a nonempty project-relative path")
    if type(include_solution) is not bool:
        raise PreWriteRefusal("INVALID_REQUEST", "include_solution must be a boolean")
    root = Path(str(context["project_root"])).resolve(strict=True)
    from ._artifact_store import ArtifactStore, publish_project_file_no_clobber, register_project_artifact
    store = ArtifactStore(root)
    try:
        dest = store.resolve_safe_path(destination, allow_overwrite=False)
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=exc.details) from exc
    relative_dest = dest.relative_to(root).as_posix()
    report = _dependencies_report(worker, model_tag, context, verify_hashes=True)
    _source_refusal(report)
    project_dependencies = _resolved_project_dependencies(report)
    scratch = Path(tempfile.mkdtemp(prefix="model-delivery-", dir=root))
    mph_path = scratch / "model.mph"
    zip_path = scratch / "package.zip"
    model = worker.client().model(model_tag).java
    save_dispatched = False
    save_receipt = None
    publication_dispatched = False
    registered = False
    stage = "prepared"
    retain_scratch = False
    try:
        try:
            stage = "save_model_package"
            save_dispatched = True
            save_receipt = model._call("saveModelPackage", str(mph_path), include_solution)
        except Exception as exc:
            error_type = type(exc).__name__
            uncertain = bool(getattr(exc, "execution_state_unknown", False)) or "unknown" in str(exc).casefold()
            if uncertain:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "model package save state is unknown; do not retry automatically",
                    stage="post_dispatch", details={"safe_retry": False, "save_receipt": "UNKNOWN",
                                                      "cause_type": error_type},
                ) from exc
            raise ExecutionContractError("MODEL_PACKAGE_SAVE_FAILED", "COMSOL refused the typed saveCopy package operation",
                                         stage="post_dispatch", details={"cause_type": error_type}) from exc
        if not mph_path.is_file() or mph_path.stat().st_size <= 0:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "COMSOL saveCopy did not produce a nonempty MPH file",
                                         stage="post_dispatch", details={"safe_retry": False, "save_receipt": save_receipt})
        stage = "hash_saved_model"
        mph_sha, mph_size, mph_identity = _stat_digest(mph_path)
        resources = report.get("file_resources", [])
        embedded, embedded_unknown = _hash_embedded_resources(mph_path, resources)
        manifest = {
            "schema": PACKAGE_SCHEMA,
            "version": 1,
            "format_version": PACKAGE_FORMAT_VERSION,
            "model": {"model_ref": context["model_ref"], "source_revision": context["source_revision"],
                      "comsol_version": _reported_comsol_version(worker),
                      "include_solution": include_solution},
            "contents": {"model.mph": {"sha256": mph_sha, "size_bytes": mph_size}},
            "embedded_dependencies": embedded + embedded_unknown,
            "resolved_project_dependencies": project_dependencies,
            "file_references": report["file_references"],
            "dependency_coverage": report["coverage"],
            "unresolved_items": list(report["unresolved_items"]) + [
                {"kind": "model_file_resource", "resource_tag": item["resource_tag"],
                 "state": "UNVERIFIED", "archive_members": list(item["archive_members"]),
                 "reason": item["reason"]}
                for item in embedded_unknown
            ],
            "external_code": {"status": "UNVERIFIED", "reason": "complete external-code inventory is unavailable"},
            "external_libraries_services": {"status": "UNVERIFIED", "reason": "runtime libraries/services are not represented by Model.FileResourceList"},
            "portability_status": "UNVERIFIED",
            "absolute_paths_in_manifest": False,
            "save_semantics": "Model.save(path,true) copied the bound live Model; original live filepath was read before/after by Worker",
        }
        manifest_bytes = _json_bytes(manifest)
        stage = "write_package_archive"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            info = zipfile.ZipInfo("model.mph")
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o600 << 16
            with archive.open(info, "w") as out:
                from ._artifact_store import _open_pinned_artifact, _assert_pinned_artifact_stable
                source, before = _open_pinned_artifact(mph_path)
                digest = hashlib.sha256()
                copied = 0
                try:
                    if {"device": int(before.st_dev), "inode": int(before.st_ino), "size": int(before.st_size),
                        "mtime_ns": int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)))} != mph_identity:
                        raise ExecutionContractError("ARTIFACT_CHANGED", "temporary MPH changed before package streaming", stage="post_dispatch")
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                        copied += len(block)
                        out.write(block)
                    _assert_pinned_artifact_stable(mph_path, before, os.fstat(source.fileno()))
                finally:
                    source.close()
                if copied != mph_size or digest.hexdigest() != mph_sha:
                    raise ExecutionContractError("ARTIFACT_CHANGED", "temporary MPH failed streaming hash readback", stage="post_dispatch")
            _write_project_dependencies(root, archive, project_dependencies)
        stage = "hash_package_archive"
        package_sha, package_size, _ = _stat_digest(zip_path)
        stage = "publish_package"
        publication_dispatched = True
        receipt = publish_project_file_no_clobber(
            root, relative_dest, zip_path, expected_sha256=package_sha, expected_size=package_size,
        )
        registration = register_project_artifact(
            root, context["operation_store"], relative_dest,
            project_id=str(context["project_id"]), role="model_package", classification="project_local",
            host_identity=str(context["host_identity"]), engine_host_identity=str(context["engine_host_identity"]),
            request_id=str(context["request_id"]), registering_operation_id="model.package",
            artifact_type="application/zip", format_version=PACKAGE_FORMAT_VERSION,
            provenance_fields={"source_model_ref": context["model_ref"],
                               "source_revision": context["source_revision"],
                               "include_solution": include_solution},
        )
        registered = True
        stage = "registered"
        registration_public = {key: value for key, value in registration.items() if key != "resolved_path"}
        return {
            "data_schema_version": 1,
            "operation": "model.package",
            "status": "APPLIED",
            "ok": True,
            "source_revision": context["source_revision"],
            "destination": relative_dest,
            "sha256": package_sha,
            "size_bytes": package_size,
            "mph_sha256": mph_sha,
            "mph_size_bytes": mph_size,
            "include_solution": include_solution,
            "resolved_project_dependencies": project_dependencies,
            "artifact_registration": registration_public,
            "publication_receipt": receipt,
            "manifest": manifest,
            "portability_status": "UNVERIFIED",
            "safe_retry": False,
        }
    except BaseException as exc:
        if save_dispatched:
            retain_scratch = True
            recovery = _retain_package_failure(
                root, scratch, context, destination=relative_dest, stage=stage,
                cause=exc, save_dispatched=save_dispatched, save_receipt=save_receipt,
                publication_dispatched=publication_dispatched, registered=registered,
            )
            if isinstance(exc, ExecutionContractError):
                exc.details.update(recovery)
                exc.details["safe_retry"] = False
                exc.safe_retry = False
            else:
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN",
                    "model package pipeline failed after save dispatch; inspect retained recovery artifacts before retrying",
                    stage="post_dispatch", details={**recovery, "cause_type": type(exc).__name__},
                ).with_cause(exc) from exc
        raise
    finally:
        if not retain_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_recipe_bytes(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_RECIPE_BYTES:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe JSON must be 1 byte to 1 MiB")
    try:
        recipe = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PreWriteRefusal("RECIPE_INVALID", "registered recipe is not strict UTF-8 JSON",
                              details={"cause_type": type(exc).__name__}) from exc
    if not isinstance(recipe, dict) or set(recipe) != {"schema", "version", "runtime", "products", "steps"}:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe root must contain exactly schema, version, runtime, products and steps")
    if recipe.get("schema") != RECIPE_SCHEMA or type(recipe.get("version")) is not int or recipe["version"] != 1:
        raise PreWriteRefusal("RECIPE_VERSION_UNSUPPORTED", "recipe schema/version is unsupported")
    runtime = recipe.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {"comsol_versions"}:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe.runtime must contain exactly comsol_versions")
    versions = runtime.get("comsol_versions")
    if (not isinstance(versions, list) or not versions or len(versions) > 16
            or any(not isinstance(item, str) or not item.strip() or len(item) > 64 for item in versions)):
        raise PreWriteRefusal("RECIPE_INVALID", "runtime.comsol_versions must list 1 to 16 exact nonempty version strings")
    try:
        for item in versions:
            _parse_version_declaration(item)
    except ValueError as exc:
        raise PreWriteRefusal("RECIPE_INVALID", "every runtime version must include an explicit COMSOL build number") from exc
    products = recipe.get("products")
    if (not isinstance(products, list) or len(products) > MAX_RECIPE_PRODUCTS
            or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", item) for item in products)
            or len(set(products)) != len(products)):
        raise PreWriteRefusal("RECIPE_INVALID", "products must be up to 32 unique COMSOL product tokens")
    steps = recipe.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_RECIPE_STEPS:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe.steps must contain 1 to 128 steps")
    _check_json_depth(recipe)
    return recipe


def _check_json_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_RECIPE_DEPTH:
        raise PreWriteRefusal("RECIPE_INVALID", "recipe nesting exceeds 32 levels")
    if isinstance(value, Mapping):
        for child in value.values():
            _check_json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _check_json_depth(child, depth + 1)


def _reject_unsafe_step(operation: str, arguments: Mapping[str, Any]) -> None:
    if operation in _FILE_OPERATIONS:
        reason = "requires backend registered-file resolver and is not admitted in model_recipe"
        raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"{operation}: {reason}", details={"operation_id": operation, "reason": reason})
    if operation.startswith(("session.", "host.", "desktop.", "project.", "code.", "model.")):
        raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"{operation}: host/session/control actions are not recipe steps",
                              details={"operation_id": operation, "reason": "host_session_or_control_effect"})
    if _has_unsafe_fields(arguments):
        raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"{operation}: raw file paths or executable code are not recipe inputs",
                              details={"operation_id": operation, "reason": "raw_path_or_trusted_code_field"})
    # File-capable feature constructors are excluded even when their surface
    # operation is a generic create/update. A future registered artifact
    # adapter can admit them without making raw paths recipe authority.
    feature = arguments.get("feature")
    kind = arguments.get("type_id")
    definition = arguments.get("definition")
    if ((operation.startswith("geometry.feature_") and (feature == "Import" or kind == "Import"))
            or (operation in {"function.create", "function.update"}
                and isinstance(definition, Mapping) and "filename" in definition)):
        raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"{operation}: file-backed feature requires registered artifact resolution",
                              details={"operation_id": operation, "reason": "registered_artifact_adapter_missing"})


def _resolved_recipe_effect(operation: str, arguments: Mapping[str, Any]) -> str | None:
    """Resolve only dynamic model operations whose exact action is explicit."""
    action = arguments.get("action")
    read_actions = {
        "definition.component_manage": {"list", "inspect"},
        "definition.coordinate_manage": {"list"},
        "definition.coupling_manage": {"list"},
        "definition.pair_manage": {"list"},
        "physics.multiphysics_manage": {"inspect"},
        "plot.view_manage": {"list", "inspect"},
        "result.numerical_manage": {"list", "get", "inspect"},
        "result.table_manage": {"list", "get", "inspect"},
    }
    write_actions = {
        "definition.component_manage": {"create", "remove"},
        "definition.coordinate_manage": {"create", "remove"},
        "definition.coupling_manage": {"create", "remove"},
        "definition.pair_manage": {"create", "remove"},
        "physics.multiphysics_manage": {"create", "update", "remove"},
        "plot.view_manage": {"create", "update", "remove"},
        "result.numerical_manage": {"create", "update", "set", "remove"},
        "result.table_manage": {"create", "set", "clear", "remove"},
    }
    evaluate_actions = {
        "result.numerical_manage": {"run", "evaluate"},
    }
    if action in read_actions.get(operation, set()):
        return "READ"
    if action in write_actions.get(operation, set()):
        return "WRITE"
    if action in evaluate_actions.get(operation, set()):
        return "EVALUATE"
    return None


def _has_unsafe_fields(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FILE_KEYS or str(key).casefold() in {"java", "python", "script_text", "trusted_code"}:
                return True
            # A string `path` is ambiguous with an external filename; a typed
            # NodePath object is allowed for ordinary model entities.
            if str(key).casefold() == "path" and isinstance(child, str):
                return True
            if _has_unsafe_fields(child):
                return True
    elif isinstance(value, list):
        return any(_has_unsafe_fields(child) for child in value)
    return False


def _read_registered_recipe(context: Mapping[str, Any], artifact_id: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    from ._artifact_store import (
        _assert_pinned_artifact_stable, _open_pinned_artifact,
        local_artifact_host_identity, local_engine_host_identity,
        resolve_registered_artifact,
    )
    _require_context(context, "project_root", "project_id", "operation_store", "model_ref", "backend")
    resolved = resolve_registered_artifact(
        context["project_root"], context["operation_store"], artifact_id,
        project_id=str(context["project_id"]),
        current_host_identity=local_artifact_host_identity(),
        current_engine_host_identity=local_engine_host_identity(context["backend"].endpoint_key),
        current_server_instance_id=str(context["model_ref"].get("server_instance_id", "")),
        max_size_bytes=MAX_RECIPE_BYTES,
    )
    record = context["operation_store"].get_metadata("artifacts", artifact_id)
    if not isinstance(record, Mapping) or record.get("schema_version") != 2:
        raise PreWriteRefusal("ARTIFACT_SCOPE_MISMATCH", "model_recipe requires a schema-v2 registered artifact")
    if record.get("role") != "model_recipe" or record.get("artifact_type") != "application/json" or record.get("format_version") != RECIPE_FORMAT_VERSION:
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", "recipe artifact must have role=model_recipe, application/json and model-recipe/1 format")
    handle, before = _open_pinned_artifact(Path(resolved["path"]))
    try:
        expected = resolved["file_identity"]
        actual = {"device": int(before.st_dev), "inode": int(before.st_ino), "size": int(before.st_size),
                  "mtime_ns": int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)))}
        if actual != expected:
            raise PreWriteRefusal("ARTIFACT_CHANGED", "registered recipe identity changed between resolution and pinned read")
        raw = handle.read(MAX_RECIPE_BYTES + 1)
        _assert_pinned_artifact_stable(Path(resolved["path"]), before, os.fstat(handle.fileno()))
    finally:
        handle.close()
    if len(raw) != resolved["size"] or hashlib.sha256(raw).hexdigest() != artifact_id:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "recipe content changed during its bound read")
    return _parse_recipe_bytes(raw), resolved


def _validate_recipe_steps(recipe: Mapping[str, Any], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    from ._g2_registry import BY_ID, validate_call
    from ._g3_ops import DISPATCH, EFFECTS
    ref = context["model_ref"]
    project_id = str(context["project_id"])
    revision = context.get("source_revision")
    execution_id = str(context.get("operation_id") or "model.rebuild_target")
    rows = []
    required_permissions: set[str] = {"compute"}
    for index, step in enumerate(recipe["steps"]):
        if not isinstance(step, Mapping) or set(step) != {"operation_id", "arguments"}:
            raise PreWriteRefusal("RECIPE_INVALID", f"step {index} must contain exactly operation_id and arguments")
        operation = step.get("operation_id")
        args = step.get("arguments")
        if not isinstance(operation, str) or not isinstance(args, Mapping):
            raise PreWriteRefusal("RECIPE_INVALID", f"step {index} operation_id and arguments have invalid types")
        if set(args) & {"project_id", "session_id", "model_ref", "expected_revision", "idempotency_key", "request_id"}:
            raise PreWriteRefusal("RECIPE_INVALID", f"step {index} carries identity fields that belong to the outer request")
        _reject_unsafe_step(operation, args)
        function = DISPATCH.get(operation)
        entry = BY_ID.get(operation)
        effect = str(EFFECTS.get(operation, "")).upper()
        if function is None or entry is None:
            raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"step {index}: operation is not implemented by the registered G3 dispatcher",
                                  details={"operation_id": operation, "reason": "not_in_g3_dispatch"})
        if effect == "DYNAMIC":
            effect = _resolved_recipe_effect(operation, args) or "DYNAMIC"
        if entry.scope != "model" or effect not in {"READ", "WRITE", "EVALUATE", "COMPUTE"}:
            raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"step {index}: only classified model-scope READ/WRITE/EVALUATE/COMPUTE steps are admitted",
                                  details={"operation_id": operation, "scope": entry.scope, "effect": effect})
        # Dynamic effects have no reviewable recipe contract. Exact effects for
        # all admitted operations come from the registered catalog/dispatcher.
        if effect in {"DYNAMIC", "FILE_WRITE", "TRUSTED_CODE", "HOST_CONTROL", "SESSION_CONTROL"}:
            raise PreWriteRefusal("RECIPE_STEP_UNSUPPORTED", f"step {index}: unsafe effect {effect}")
        synthetic = dict(args)
        synthetic.update({
            "project_id": project_id,
            "session_id": str(ref["session_id"]),
            "model_ref": dict(ref),
            "expected_revision": revision,
            "idempotency_key": f"{execution_id}:{index}",
            "request_id": f"{context.get('request_id', execution_id)}:{index}",
        })
        try:
            validate_call(operation, synthetic)
        except ExecutionContractError as exc:
            raise PreWriteRefusal(exc.code, f"step {index} {operation} failed strict schema validation: {exc}",
                                  details={"operation_id": operation, "step_index": index}) from exc
        permission = {"READ": "inspect", "WRITE": "project_write", "EVALUATE": "project_write", "COMPUTE": "compute"}[effect]
        required_permissions.add(permission)
        rows.append({"operation_id": operation, "arguments": dict(args), "dispatch": function,
                     "effect": effect, "permission": permission, "synthetic_arguments": synthetic})
    actual_permissions = set(getattr(context.get("execution_service").ledger, "permissions", set()))
    missing = sorted(required_permissions - actual_permissions)
    if missing:
        raise PreWriteRefusal("PERMISSION_DENIED", f"recipe requires permissions not granted to the bound session: {missing}",
                              details={"required_permissions": sorted(required_permissions), "missing_permissions": missing})
    authorize = context.get("project_authorizer")
    if not callable(authorize):
        raise PreWriteRefusal("PROJECT_AUTHORITY_UNAVAILABLE", "project policy cannot authorize every recipe step")
    for permission in sorted(required_permissions):
        try:
            authorize(project_id, permission)
        except ExecutionContractError as exc:
            raise PreWriteRefusal(exc.code, str(exc), details={"permission": permission,
                                                               "cause_details": dict(exc.details)}) from exc
    return rows


def _version_parts(value: str) -> tuple[int, ...] | None:
    match = re.search(r"[0-9]+(?:[.][0-9]+){1,3}", value)
    if match is None:
        return None
    try:
        return tuple(int(part) for part in match.group(0).split("."))
    except ValueError:
        return None


def _explicit_build(value: str) -> int | None:
    dotted = _version_parts(value)
    if dotted is not None and len(dotted) == 4:
        return dotted[3]
    match = re.search(
        r"(?:build(?:[ ]+(?:number|no[.]?))?|development[ ]+build|开发版本|版本)[ ]*[:：#]?[ ]*([0-9]+)",
        value, re.IGNORECASE,
    )
    if match is None:
        return None
    return int(match.group(1))


def _pad_product_version(parts: Sequence[int]) -> tuple[int, int, int]:
    if not 2 <= len(parts) <= 3:
        raise ValueError("COMSOL product version must have two or three components")
    return tuple([*parts, *([0] * (3 - len(parts)))])  # type: ignore[return-value]


def _parse_version_declaration(value: str) -> tuple[tuple[int, int, int], int]:
    parts = _version_parts(value)
    build = _explicit_build(value)
    if parts is None or build is None:
        raise ValueError("COMSOL version declaration has no explicit build")
    if len(parts) == 4:
        if build != parts[3]:
            raise ValueError("COMSOL version declaration contains conflicting builds")
        family = parts[:3]
    else:
        family = parts
    return _pad_product_version(family), build


def _reported_comsol_version(worker: Any) -> str:
    """Read ModelUtil version through the bound Worker facade."""
    getter = getattr(worker, "getComsolVersion", None)
    if callable(getter):
        return getter()
    # Compatibility for older fixtures/Worker facades. Production
    # PersistentJavaWorker exposes the verified ModelUtil call directly.
    return worker.client().getComsolVersion()


def _current_version(worker: Any) -> str:
    try:
        version = _reported_comsol_version(worker)
    except Exception as exc:
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "the attached Worker did not report its COMSOL version",
                              details={"cause_type": type(exc).__name__}) from exc
    if not isinstance(version, str) or not version.strip():
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "the attached Worker returned no COMSOL version")
    reported = version.strip()
    reported_parts = _version_parts(reported)
    if reported_parts is None:
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "Worker version did not expose a COMSOL release token")
    reported_family = _pad_product_version(reported_parts[:3] if len(reported_parts) == 4 else reported_parts)
    reported_build = _explicit_build(reported)
    paths = getattr(worker, "paths", None)
    root = getattr(paths, "comsol_root", None)
    if root is None:
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "the attached Worker installation path is unavailable")
    try:
        from ._runtime_installation import inspect_installation, runtime_id_for_root
        inspected = inspect_installation(runtime_id_for_root(root))
        installation = inspected.get("installation") if isinstance(inspected, Mapping) else None
        release = installation.get("version") if isinstance(installation, Mapping) else None
        build_row = installation.get("build") if isinstance(installation, Mapping) else None
        if (inspected.get("status") != "OBSERVED" or not isinstance(release, Mapping)
                or release.get("status") != "OBSERVED" or not isinstance(release.get("value"), str)
                or not isinstance(build_row, Mapping) or build_row.get("status") != "OBSERVED"
                or type(build_row.get("value")) is not int):
            raise ValueError("attached installation release/build identity is not observed")
        family = _pad_product_version(tuple(int(part) for part in release["value"].split(".")))
        build = int(build_row["value"])
    except Exception as exc:
        raise PreWriteRefusal("RUNTIME_VERSION_UNAVAILABLE", "the attached Worker install lacks an exact observed release and build",
                              details={"cause_type": type(exc).__name__}) from exc
    if reported_family != family or (reported_build is not None and reported_build != build):
        raise PreWriteRefusal("RUNTIME_VERSION_MISMATCH", "Worker version readback conflicts with its bound installation identity",
                              details={"worker_version": reported, "installation_version": release.get("value"),
                                       "installation_build": build})
    return f"{family[0]}.{family[1]}.{family[2]}.{build}"


def rebuild_target(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_and_context(arguments, {"target_runtime_id", "recipe_artifact"})
    _require_context(context, "target_runtime_id", "project_root", "project_id", "operation_store",
                     "model_ref", "backend", "runtime_identity", "execution_service", "source_revision")
    recipe, resolved = _read_registered_recipe(context, body.get("recipe_artifact"))
    steps = _validate_recipe_steps(recipe, context)
    actual_version = _current_version(worker)
    expected_versions = recipe["runtime"]["comsol_versions"]
    actual_release = _parse_version_declaration(actual_version)
    admitted_releases = [_parse_version_declaration(item) for item in expected_versions]
    if actual_release not in admitted_releases:
        raise PreWriteRefusal("RUNTIME_VERSION_MISMATCH", "recipe does not admit the exact attached COMSOL version",
                              details={"actual_comsol_version": actual_version, "admitted_comsol_versions": expected_versions})
    products = recipe["products"]
    product_evidence: list[dict[str, Any]] = []
    if products:
        from ._g3_runtime import _product_rows
        try:
            product_evidence, unresolved = _product_rows(worker, products)
        except ExecutionContractError as exc:
            raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc
        if unresolved or any(row.get("hasProduct") is not True for row in product_evidence):
            raise PreWriteRefusal("BLOCKED_LICENSE", "recipe product requirements were not all verified on the attached runtime",
                                  details={"products": product_evidence, "unresolved": unresolved})
    completed: list[dict[str, Any]] = []
    for index, row in enumerate(steps):
        try:
            data = row["dispatch"](worker, model_tag, row["synthetic_arguments"])
            if not isinstance(data, Mapping):
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "recipe operation returned no readback mapping", stage="post_dispatch")
            result_row = {"index": index, "operation_id": row["operation_id"], "effect": row["effect"],
                          "readback": dict(data)}
            completed.append(result_row)
            status = str(data.get("status") or "").upper()
            if (data.get("execution_state_unknown") is True or data.get("ok") is False
                    or status in {"EXECUTION_STATE_UNKNOWN", "PARTIAL_FAILURE", "FAILED", "BLOCKED", "UNAVAILABLE"}
                    or (data.get("partial_change") is True and data.get("ok") is not True
                        and status not in {"APPLIED", "OK", "SUCCEEDED"})):
                return _partial_recipe_result(context, resolved, actual_version, product_evidence, completed,
                                              index, row["operation_id"], "step_reported_failure")
        except Exception as exc:
            return _partial_recipe_result(context, resolved, actual_version, product_evidence, completed,
                                          index, row["operation_id"], type(exc).__name__)
    ref = context["model_ref"]
    revision = context["execution_service"].ledger._state_for(context["_model_ref_object"]).revision
    return {
        "data_schema_version": 1,
        "operation": "model.rebuild_target",
        "status": "APPLIED",
        "ok": True,
        "partial_change": False,
        "execution_state_unknown": False,
        "safe_retry": False,
        "completed_steps": len(completed),
        "step_readbacks": completed,
        "source_revision": context["source_revision"],
        "final_revision": revision,
        "runtime": _runtime_receipt(context, actual_version),
        "recipe_artifact": {"artifact_id": resolved["artifact_id"], "sha256": resolved["sha256"],
                             "size_bytes": resolved["size"], "role": "model_recipe"},
        "products": product_evidence,
        "model_ref": ref,
    }


def _runtime_receipt(context: Mapping[str, Any], version: str) -> dict[str, Any]:
    ref = context["model_ref"]
    return {
        "runtime_id": context["target_runtime_id"],
        "worker_install_identity": context["runtime_identity"].get("runtime_id", context["target_runtime_id"]),
        "endpoint": context["backend"].endpoint_key,
        "server_instance_id": ref.get("server_instance_id"),
        "comsol_version": version,
        "attachment": "EXACT_CURRENT_WORKER",
    }


def _partial_recipe_result(context: Mapping[str, Any], resolved: Mapping[str, Any], version: str,
                           products: Sequence[Mapping[str, Any]], completed: Sequence[Mapping[str, Any]],
                           failed_index: int, failed_operation: str, failure_type: str) -> dict[str, Any]:
    revision = context["execution_service"].ledger._state_for(context["_model_ref_object"]).revision
    return {
        "data_schema_version": 1,
        "operation": "model.rebuild_target",
        "status": "EXECUTION_STATE_UNKNOWN",
        "ok": False,
        # The current step was dispatched even when it produced no readback.
        # Its model effect is therefore unknown; retain UNKNOWN and forbid replay.
        "partial_change": True,
        "execution_state_unknown": True,
        "safe_retry": False,
        "completed_steps": len(completed),
        "attempted_steps": failed_index + 1,
        "step_readbacks": list(completed),
        "failed_step": {"index": failed_index, "operation_id": failed_operation, "failure_type": failure_type},
        "not_executed_steps": "steps after failed_step were not dispatched",
        "source_revision": context["source_revision"],
        "final_revision": revision,
        "runtime": _runtime_receipt(context, version),
        "recipe_artifact": {"artifact_id": resolved["artifact_id"], "sha256": resolved["sha256"], "size_bytes": resolved["size"]},
        "products": list(products),
        "model_ref": context["model_ref"],
    }


OPERATIONS = {
    "model.dependencies": dependencies,
    "model.package": package,
    "model.rebuild_target": rebuild_target,
}
