"""Bounded COMSOL 6.4 geometry/material/PDE/mesh actions.

Its public call shape is the G3 domain contract ``fn(worker, model_tag,
arguments) -> data``. The backend replaces the private ``_managed_context``
capability before wiring these functions; a wire caller cannot authorize an
artifact path, branch, or runner.

Verified local COMSOL 6.4.0.293 API evidence used by this module:

* GeomSequence.exportFinal(filename): COMSOL API, chunk 22895,
  SHA-256 047a6aeebc08362e81db383f1b758b688303a702b26a7d020e18146fa63ad661.
  The native final-geometry export supports MPHBIN/MPHTXT.  CAD format export
  is intentionally outside this subset because it is license-dependent.
* Geometry Repair create/selection/properties/runAll: CAD User's Guide,
  chunk 5803, SHA-256 0e05dee59b398991ef9b62284d8132a28abb450e4d5dc453c7784c45dae54f5c.
* MaterialList.insert(filename,tags) and its returned message/object/reference
  arrays: Programming Reference, chunk 10794, SHA-256
  5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105.
* Coefficient/General/Weak PDE definitions: Reference Manual chunks 18472,
  18473, 18474 (hashes recorded in ``API_SOURCES``); indexed PDE expressions
  use the G2 setter/readback adapter.  GlobalEquations uses the documented
  name/equation/initialValueU/initialValueUt indexed properties from chunk
  34349, SHA-256 2f4739f2ced80c609273b61a13b16a14428f0f1c8c4c71238183deee98d27c24.
* Mesh Import feature properties and importData(): Reference Manual/API chunk
  17133, SHA-256 88e1adaf0adfa948edf9f8420372bebd6acc28c1ba3d1585d78b57dedbe61ca4.
  Imported mesh files that declare a length unit are scaled to the owning
  component length unit (Reference Manual chunk 19116, SHA-256
  b03c82b739731235ed3edbcf3411df5ae0ac51c629483e98398a2129a326dab8).
* MeshSequence.export(filename): API chunk 22945, SHA-256
  0f46188b7694be0f1b7d188790218075aa6c831b58080f1d27ae04f3a141df23.
* Refine.numrefine integer property: Reference Manual chunk 17148,
  SHA-256 c91b23ba34df87758116d116af4e7be4c2425c4aa44180cb456bc3c89f880303.
* StudyFeature.mesh(geometry) getter / mesh(geometry, meshTag) setter: API
  chunk 23092, SHA-256 a9ffe9c3ac8a45d945eb51d23ab78277cc8970b14f5d5bddfd504147867ca674.
* Study.getSolverSequences(String), SolutionInfo.getSolverSequence(outer),
  getISol(outer, inner), and SolverSequence.getMesh(geometry, objectIndex)
  bind each selected result tuple to its actual solver and mesh; source hashes
  are recorded in the convergence executor and COMSOL 6.4 local API evidence.

These are source-level contracts, not proof that the active COMSOL license,
model, geometry, or numerical result supports an operation.  Native engine,
license, topology, and scientific acceptance remain separate evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._g2_contract import NodePath, property_schema_from_engine
from ._g2_engine import _call, property_index_set, property_set
from ._g3_common import (
    ENVELOPE_FIELDS,
    bound_model,
    call_probe,
    node_type,
    operation_arguments,
    reject_unknown_keys,
    require_bool,
    require_mapping,
    require_number,
    require_string,
    require_string_array,
    resolve_path,
    tag_list,
    validate_tag,
)


API_SOURCES = {
    "geometry_export": {"doc": 7599, "chunk": 22895,
                        "sha256": "047a6aeebc08362e81db383f1b758b688303a702b26a7d020e18146fa63ad661"},
    "geometry_repair": {"doc": 2230, "chunk": 5803,
                         "sha256": "0e05dee59b398991ef9b62284d8132a28abb450e4d5dc453c7784c45dae54f5c"},
    "material_import": {"doc": 3626, "chunk": 10794,
                         "sha256": "5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105"},
    "coefficient_pde": {"doc": 5290, "chunk": 18472,
                        "sha256": "f6b6600d887f17dd89b1808ae9206cc6241467982ce5f4e3971181b57930e162"},
    "general_pde": {"doc": 5291, "chunk": 18473,
                     "sha256": "27c8334b0ef63daa5c8bbf0e6a8db0ea9bdd25f7da641c5d3d5ac0da886ef602"},
    "weak_pde": {"doc": 5292, "chunk": 18474,
                 "sha256": "edf3c30a4d72002cbedc94c3c9ee188f106ddc1639f997934ebf5168a9f4b42d"},
    "global_equations": {"doc": 11800, "chunk": 34349,
                         "sha256": "2f4739f2ced80c609273b61a13b16a14428f0f1c8c4c71238183deee98d27c24"},
    "mesh_import": {"doc": 4381, "chunk": 17133,
                    "sha256": "88e1adaf0adfa948edf9f8420372bebd6acc28c1ba3d1585d78b57dedbe61ca4"},
    "mesh_length_unit": {"doc": 5764, "chunk": 19116,
                         "sha256": "b03c82b739731235ed3edbcf3411df5ae0ac51c629483e98398a2129a326dab8"},
    "mesh_export": {"doc": 7637, "chunk": 22945,
                    "sha256": "0f46188b7694be0f1b7d188790218075aa6c831b58080f1d27ae04f3a141df23"},
}

_OUTPUT_FORMATS = {
    "geometry.export": {"mphbin": ".mphbin", "mphtxt": ".mphtxt"},
    "mesh.export": {"mphbin": ".mphbin", "mphtxt": ".mphtxt", "stl": ".stl"},
}
_MESH_IMPORT_SOURCES = {
    ".mphbin": "native", ".mphtxt": "native", ".nas": "nastran",
    ".bdf": "nastran", ".dat": "nastran", ".stl": "stl", ".ply": "ply",
    ".3mf": "3mf", ".wrl": "vrml", ".vrml": "vrml", ".txt": "sectionwise",
}
_PDE_INTERFACE_TYPES = frozenset({"CoefficientFormPDE", "GeneralFormPDE", "WeakFormPDE"})
_COEFFICIENT_PROPERTIES = frozenset({"c", "a", "f", "ea", "da", "alpha", "beta", "gamma"})
_GLOBAL_EQUATION_PROPERTIES = frozenset({"name", "equation", "initialValueU", "initialValueUt"})
_REPAIR_PROPERTIES = frozenset({
    "check", "deletedetails", "fixerrors", "healedges", "minimizetol",
    "repairtol", "selresult", "selresultshow", "simplify", "repairfacetoface",
    "contributeto",
})
_REPAIR_ENUMS = {
    "check": {"on", "off"}, "deletedetails": {"on", "off"}, "fixerrors": {"on", "off"},
    "healedges": {"on", "off"}, "minimizetol": {"on", "off"},
    "selresult": {"on", "off"}, "selresultshow": {"all", "obj", "dom", "bnd", "edg", "pnt", "off"},
    "simplify": {"on", "off"}, "repairfacetoface": {"on", "off"},
}
_REPAIR_INPUT_OBJECTS = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_SAFE_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBS_REF_KEYS = frozenset({"observation_id", "sha256"})


def _body_context(arguments: Any, allowed: Sequence[str], required: Sequence[str] = ()) -> tuple[dict[str, Any], Mapping[str, Any]]:
    payload = require_mapping(arguments, "arguments")
    context = payload.get("_managed_context")
    if not isinstance(context, Mapping):
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "this model-domain operation requires backend-created managed context")
    body = {key: value for key, value in payload.items() if key != "_managed_context" and key not in ENVELOPE_FIELDS}
    reject_unknown_keys(body, allowed, "arguments")
    missing = [key for key in required if body.get(key) is None]
    if missing:
        raise PreWriteRefusal("INVALID_REQUEST", f"arguments are missing required fields: {sorted(missing)}")
    return body, context


def _managed_model(context: Mapping[str, Any], model_tag: str) -> tuple[Any, Any, Any]:
    from ._g3_managed_models import _model_state
    try:
        return _model_state(context, model_tag)
    except ExecutionContractError as exc:
        raise PreWriteRefusal(exc.code, str(exc), details=dict(exc.details)) from exc


def _require_context(context: Mapping[str, Any], *names: str) -> None:
    missing = [name for name in names if context.get(name) is None]
    if missing:
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "managed context lacks " + ", ".join(missing))


def _unknown(message: str, *, operation: str, cause: BaseException | None = None,
             data: Mapping[str, Any] | None = None, stage: str = "post_dispatch") -> dict[str, Any]:
    payload = dict(data or {})
    payload.update({"status": "UNKNOWN", "complete": False, "partial_change": True,
                    "execution_state_unknown": True, "safe_retry": False})
    if cause is not None:
        payload.setdefault("failure", {"type": type(cause).__name__, "message": str(cause)[:600]})
    payload.setdefault("evidence", {})
    payload["evidence"].update({"operation": operation, "stage": stage, "safe_retry": False})
    return payload


def _result(data: Mapping[str, Any], *, status: str = "APPLIED") -> dict[str, Any]:
    out = dict(data)
    out.update({"status": status, "complete": status == "APPLIED", "partial_change": status == "APPLIED",
                "execution_state_unknown": False, "safe_retry": False})
    return out


def _registered(context: Mapping[str, Any], artifact_id: Any, allowed_roles: set[str]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    from ._g3_managed_models import _resolve_registered
    resolved = _resolve_registered(context, artifact_id)
    record = context["operation_store"].get_metadata("artifacts", resolved["artifact_id"])
    if not isinstance(record, Mapping) or record.get("schema_version") != 2:
        raise PreWriteRefusal("ARTIFACT_IDENTITY_MISMATCH", "domain import requires an immutable schema-v2 registration")
    if record.get("role") not in allowed_roles:
        raise PreWriteRefusal("ARTIFACT_ROLE_MISMATCH", f"registered role must be one of {sorted(allowed_roles)}")
    if record.get("classification") != "project_local":
        raise PreWriteRefusal("ARTIFACT_CLASSIFICATION_MISMATCH", "domain import accepts only registered project-local artifacts")
    if resolved.get("sha256") != record.get("sha256") or resolved.get("size") != record.get("size"):
        raise PreWriteRefusal("ARTIFACT_IDENTITY_MISMATCH", "resolved artifact digest/size differs from its durable record")
    return resolved, record


def _project_store(context: Mapping[str, Any]):
    from ._artifact_store import ArtifactStore, _assert_no_symlink_components
    _require_context(context, "project_root", "project_id", "operation_store", "host_identity",
                     "engine_host_identity", "operation_id", "request_id")
    root = Path(context["project_root"]).expanduser().resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise PreWriteRefusal("RUNTIME_CONFIGURATION_REQUIRED", "managed project root is not a regular directory")
    _assert_no_symlink_components(root, field="domain project root")
    return root, ArtifactStore(root)


def _validate_destination(store: Any, destination: Any, suffix: str) -> str:
    value = require_string(destination, "destination", max_length=2048)
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in {"", ".", ".."} for part in pure.parts):
        raise PreWriteRefusal("ACCESS_VIOLATION", "destination must be canonical project-relative POSIX syntax")
    if Path(value).suffix.lower() != suffix:
        raise PreWriteRefusal("INVALID_REQUEST", f"destination suffix must be {suffix}")
    store.resolve_safe_path(value, allow_overwrite=False)
    return value


def _stage_path(root: Path, suffix: str) -> tuple[Path, Path]:
    from ._artifact_store import _assert_no_symlink_components
    staging = root / "g2_artifacts" / ".domain-export-staging"
    _assert_no_symlink_components(staging.parent, field="domain staging parent")
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    _assert_no_symlink_components(staging, field="domain staging directory")
    private_dir = Path(tempfile.mkdtemp(prefix="domain-", dir=staging))
    os.chmod(private_dir, 0o700)
    stage = private_dir / ("export" + suffix)
    return private_dir, stage


def _publish_export(context: Mapping[str, Any], private_dir: Path, stage: Path,
                    destination: str, *, role: str, artifact_type: str) -> dict[str, Any]:
    from ._artifact_store import _hash_regular_artifact, publish_project_file_no_clobber, register_project_artifact
    root, _store = _project_store(context)
    sha, identity = _hash_regular_artifact(stage)
    size = identity["size"]
    if size <= 0:
        raise ExecutionContractError("ARTIFACT_EMPTY", "COMSOL export created an empty file", stage="post_dispatch")
    published = publish_project_file_no_clobber(root, destination, stage,
                                                expected_sha256=sha, expected_size=size)
    registration = register_project_artifact(
        root, context["operation_store"], destination,
        project_id=str(context["project_id"]), role=role, classification="project_local",
        host_identity=str(context["host_identity"]), engine_host_identity=str(context["engine_host_identity"]),
        request_id=str(context["request_id"]), registering_operation_id=str(context["operation_id"]),
        artifact_type=artifact_type, format_version="comsol-domain-export/1",
        provenance_fields={"producer_model_ref": context["model_ref"],
                           "producer_revision": context["source_revision"],
                           "destination_sha256": sha, "destination_size_bytes": size},
    )
    return {"path": destination, "sha256": sha, "size_bytes": size,
            "artifact_id": registration["artifact_id"], "role": role,
            "published": bool(published), "registration_path": registration["path"]}


def _cleanup_stage(private_dir: Path) -> bool:
    try:
        shutil.rmtree(private_dir)
        return not private_dir.exists()
    except OSError:
        return False


def _require_format(operation: str, value: Any) -> tuple[str, str]:
    fmt = require_string(value, "format", max_length=16).lower()
    suffix = _OUTPUT_FORMATS[operation].get(fmt)
    if suffix is None:
        raise PreWriteRefusal("API_UNSUPPORTED", f"{operation} supports only {sorted(_OUTPUT_FORMATS[operation])} in this adapter")
    return fmt, suffix


def geometry_export(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("geometry", "format", "destination", "selection"),
                                 ("geometry", "format", "destination"))
    _managed_model(context, model_tag)
    if body.get("selection") is not None:
        raise PreWriteRefusal("API_UNSUPPORTED", "selected-object export is not in this adapter; use full finalized geometry export")
    fmt, suffix = _require_format("geometry.export", body["format"])
    root, store = _project_store(context)
    destination = _validate_destination(store, body["destination"], suffix)
    canonical, geom = resolve_path(worker, model_tag, body["geometry"], label="geometry")
    parsed = NodePath.from_wire(canonical)
    if len(parsed.segments) != 2 or [s.collection for s in parsed.segments] != ["component", "geom"]:
        raise PreWriteRefusal("INVALID_NODE_PATH", "geometry.export requires a component/geom sequence path")
    length_unit = call_probe(geom, "lengthUnit")
    object_names = call_probe(geom, "objectNames")
    if not length_unit["ok"] or not isinstance(length_unit.get("value"), str):
        raise PreWriteRefusal("API_UNSUPPORTED", "geometry length unit must be read before export")
    if not object_names["ok"] or not isinstance(object_names.get("value"), (list, tuple)):
        raise PreWriteRefusal("API_UNSUPPORTED", "geometry object names must be read before export")
    private_dir, stage = _stage_path(root, suffix)
    dispatched = False
    try:
        dispatched = True
        returned = _call(geom, "exportFinal", str(stage))
        if not stage.is_file() or stage.is_symlink():
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "exportFinal returned without a regular staged output")
        receipt = _publish_export(context, private_dir, stage, destination,
                                  role="geometry_export", artifact_type=f"application/vnd.comsol.geometry.{fmt}")
        return _result({"geometry": canonical, "format": fmt, "length_unit": length_unit["value"],
                        "object_names": [str(x) for x in object_names["value"]],
                        "engine_return_path": str(returned) if returned is not None else None,
                        "file": receipt,
                        "coverage": "finalized geometry export; selected objects are unsupported"})
    except Exception as exc:
        cleanup_ok = _cleanup_stage(private_dir)
        return _unknown("geometry export was dispatched but publication/readback did not fully verify",
                        operation="geometry.export", cause=exc,
                        data={"geometry": canonical, "format": fmt, "destination": destination,
                              "stage_cleanup_verified": cleanup_ok,
                              "dispatch_attempted": dispatched, "partial_change": not cleanup_ok})
    finally:
        if private_dir.exists():
            _cleanup_stage(private_dir)


def _verify_owned_checkpoint_branch(context: Mapping[str, Any], model_tag: str, ref: Any) -> dict[str, Any]:
    """Revalidate the durable checkpoint-branch binding before repair writes."""
    _require_context(context, "operation_store", "execution_service", "backend", "project_id", "model_ref", "source_revision")
    store = context["operation_store"]
    row = store.get_metadata("artifacts", "g2_owned_branch_" + model_tag)
    if not isinstance(row, Mapping) or row.get("kind") != "checkpoint_branch" or row.get("schema_version") != 1:
        raise PreWriteRefusal("OWNED_BRANCH_REQUIRED", "geometry.repair requires a durable checkpoint.branch model")
    if (row.get("model_ref") != context["model_ref"] or row.get("revision") != context["source_revision"]
            or ref.as_dict() != context["model_ref"]):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "checkpoint branch record differs from the admitted ModelRef/revision")
    if context["execution_service"].ledger.model_ownership.get(model_tag) != "mcp_owned":
        raise PreWriteRefusal("OWNED_BRANCH_REQUIRED", "checkpoint branch is not owned by the managed service")
    binding = context["backend"].model_project_binding(ref.as_dict())
    if binding != {"attribution": "PROJECT_BOUND", "project_id": context["project_id"]}:
        raise PreWriteRefusal("PROJECT_IDENTITY_MISMATCH", "checkpoint branch is not bound to the admitted project")
    source_binding = row.get("source_binding")
    admission = row.get("admission_source_identity")
    if not isinstance(source_binding, Mapping) or not isinstance(admission, Mapping):
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "checkpoint branch lacks durable source lineage")
    source_mapping = source_binding.get("model_ref")
    if source_mapping != admission.get("model_ref"):
        raise PreWriteRefusal("MODEL_IDENTITY_MISMATCH", "branch admission and checkpoint source identities disagree")
    from ._execution_contract import model_ref_from_mapping
    from ._g2_checkpoint_ops import checkpoint_input
    try:
        source_ref = model_ref_from_mapping(dict(source_mapping))
        checkpoint, checkpoint_path = checkpoint_input(context["backend"], source_ref,
                                                      row.get("checkpoint_id"), str(context["project_id"]))
    except Exception as exc:
        raise PreWriteRefusal(getattr(exc, "code", "CHECKPOINT_REQUIRED"),
                              "checkpoint branch source could not be revalidated") from exc
    if checkpoint.get("source_binding") != source_binding:
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "branch checkpoint source binding changed")
    clone_input = row.get("clone_input")
    private_path = row.get("private_path")
    if not isinstance(clone_input, Mapping) or not isinstance(private_path, str):
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "branch record lacks verified clone input metadata")
    if (clone_input.get("verified") is not True or clone_input.get("sha256") != checkpoint.get("sha256")
            or clone_input.get("source_sha256") != checkpoint.get("sha256")
            or isinstance(clone_input.get("bytes"), bool) or not isinstance(clone_input.get("bytes"), int)
            or clone_input["bytes"] <= 0):
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "branch clone receipt does not bind the verified checkpoint bytes")
    from ._artifact_store import _assert_no_symlink_components, _hash_regular_artifact
    project_root, _artifact_store = _project_store(context)
    branch_file = Path(os.path.abspath(private_path))
    try:
        branch_file.relative_to(project_root)
    except ValueError as exc:
        raise PreWriteRefusal("ACCESS_VIOLATION", "owned checkpoint branch file escapes the project root") from exc
    _assert_no_symlink_components(branch_file, field="owned checkpoint branch file")
    try:
        branch_sha256, branch_identity = _hash_regular_artifact(branch_file)
    except Exception as exc:
        if isinstance(exc, PreWriteRefusal):
            raise
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "owned checkpoint branch file could not be revalidated") from exc
    if branch_identity.get("size") != clone_input["bytes"] or branch_sha256 != checkpoint.get("sha256"):
        raise PreWriteRefusal("CHECKPOINT_REQUIRED", "owned checkpoint branch bytes differ from the source checkpoint")
    persisted = store.get_metadata("revisions", context["backend"]._model_project_key(ref.as_dict()))
    state = context["execution_service"].ledger._state_for(ref)
    expected = {"model_ref": state.ref.as_dict(), "revision": state.revision, "dirty": state.dirty,
                "fingerprint": state.fingerprint, "active_operation_id": state.active_operation_id,
                "project_id": context["project_id"], "attribution": "PROJECT_BOUND"}
    if persisted != expected or state.dirty or state.active_operation_id:
        raise PreWriteRefusal("EXECUTION_STATE_UNKNOWN", "branch revision record is dirty, busy, or not persisted exactly")
    return {"branch_model_ref": ref.as_dict(), "checkpoint_id": row["checkpoint_id"],
            "checkpoint_sha256": checkpoint.get("sha256"), "checkpoint_path": str(checkpoint_path),
            "branch_file_sha256": branch_sha256, "branch_file_size_bytes": branch_identity["size"],
            "branch_revision": state.revision, "source_model_ref": source_ref.as_dict(),
            "source_checkpoint_binding": dict(source_binding)}


def geometry_repair(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("geometry", "plan"), ("geometry", "plan"))
    _service, ref, _state = _managed_model(context, model_tag)
    branch = _verify_owned_checkpoint_branch(context, model_tag, ref)
    plan = require_mapping(body["plan"], "plan")
    reject_unknown_keys(plan, ("tag", "input_objects", "properties"), "plan")
    if set(plan) != {"tag", "input_objects", "properties"}:
        raise PreWriteRefusal("INVALID_REQUEST", "plan requires exactly tag, input_objects, and properties")
    feature_tag = validate_tag(plan["tag"], "plan.tag")
    input_objects = require_string_array(plan["input_objects"], "plan.input_objects")
    if any(not _REPAIR_INPUT_OBJECTS.fullmatch(name) for name in input_objects):
        raise PreWriteRefusal("INVALID_REQUEST", "input_objects must use the bounded COMSOL object-tag syntax")
    properties = require_mapping(plan["properties"], "plan.properties")
    reject_unknown_keys(properties, _REPAIR_PROPERTIES, "plan.properties")
    if not properties:
        raise PreWriteRefusal("INVALID_REQUEST", "repair plan properties must explicitly state at least one documented setting")
    normalized: dict[str, Any] = {}
    for name, raw in properties.items():
        if name in _REPAIR_ENUMS:
            value = require_string(raw, f"plan.properties.{name}", max_length=32).lower()
            if value not in _REPAIR_ENUMS[name]:
                raise PreWriteRefusal("INVALID_REQUEST", f"plan.properties.{name} must be one of {sorted(_REPAIR_ENUMS[name])}")
            normalized[name] = value
        elif name == "repairtol":
            number = require_number(raw, "plan.properties.repairtol")
            if number <= 0:
                raise PreWriteRefusal("INVALID_REQUEST", "repairtol must be finite and greater than zero")
            normalized[name] = number
        elif name == "contributeto":
            normalized[name] = require_string(raw, "plan.properties.contributeto", max_length=63)
    canonical, geom = resolve_path(worker, model_tag, body["geometry"], label="geometry")
    parsed = NodePath.from_wire(canonical)
    if len(parsed.segments) != 2 or [s.collection for s in parsed.segments] != ["component", "geom"]:
        raise PreWriteRefusal("INVALID_NODE_PATH", "geometry.repair requires a component/geom sequence path")
    object_probe = call_probe(geom, "objectNames")
    if not object_probe["ok"] or not isinstance(object_probe["value"], (list, tuple)):
        raise PreWriteRefusal("API_UNSUPPORTED", "repair requires actual geometry objectNames() readback")
    actual_objects = [str(v) for v in object_probe["value"]]
    if not set(input_objects).issubset(actual_objects):
        raise PreWriteRefusal("NODE_NOT_FOUND", "repair input_objects must be actual objects in the selected geometry",
                              details={"requested": input_objects, "observed": actual_objects})
    features = _call(geom, "feature")
    before_tags = tag_list(features)
    if feature_tag in before_tags:
        raise PreWriteRefusal("TAG_CONFLICT", "repair feature tag already exists; no overwrite is allowed")
    changed = False
    applied: list[dict[str, Any]] = []
    try:
        changed = True
        _call(features, "create", feature_tag, "Repair")
        after_tags = tag_list(features)
        if feature_tag not in after_tags:
            raise RuntimeError("Repair feature creation did not read back")
        repair = _call(geom, "feature", feature_tag)
        if node_type(repair) != "Repair":
            raise RuntimeError("created feature type did not read back as Repair")
        input_selection = _call(repair, "selection", "input")
        _call(input_selection, "set", list(input_objects))
        selected = _call(input_selection, "objects")
        if not isinstance(selected, (list, tuple)) or set(map(str, selected)) != set(input_objects):
            raise RuntimeError("Repair input selection did not read back exactly")
        applied.append({"step": "create", "type_id": "Repair", "tag": feature_tag,
                        "input_objects": input_objects, "selection_readback": list(map(str, selected))})
        schemas: dict[str, dict[str, Any]] = {}
        for name, value in normalized.items():
            schema = property_schema_from_engine(repair, name)
            expected_kind = "float64" if name == "repairtol" else "string"
            if (schema.get("metadata_status") != "KNOWN" or schema.get("kind") != expected_kind
                    or schema.get("shape_rank") != 0 or not schema.get("getter")):
                raise RuntimeError(f"Repair property {name} lacks a verified scalar {expected_kind} contract")
            allowed_values = schema.get("allowed_values")
            if isinstance(allowed_values, (list, tuple)) and allowed_values and value not in allowed_values:
                raise RuntimeError(f"Repair property {name} requested value is outside native allowed values")
            schemas[name] = schema
        for name, value in normalized.items():
            schema = schemas[name]
            _call(repair, "set", name, value)
            read = _call(repair, schema["getter"], name)
            if name == "repairtol":
                if isinstance(read, bool) or not isinstance(read, (int, float)) or not math.isclose(float(read), float(value), rel_tol=1e-12, abs_tol=0.0):
                    raise RuntimeError(f"Repair property {name} numeric readback differs")
            elif read != value:
                raise RuntimeError(f"Repair property {name} string readback differs")
            applied.append({"step": "property", "name": name, "requested": value,
                            "readback": read, "getter": schema["getter"], "readback_status": "MATCHED"})
        _call(geom, "runAll")
        after_names = _call(geom, "objectNames")
        after_tags = tag_list(features)
        if feature_tag not in after_tags or not isinstance(after_names, (list, tuple)):
            raise RuntimeError("post-repair geometry feature/object readback is incomplete")
        return _result({"geometry": canonical, "repair_feature": feature_tag,
                        "input_objects": input_objects, "properties": normalized,
                        "feature_tags_before": before_tags, "feature_tags_after": after_tags,
                        "object_names_after": [str(v) for v in after_names],
                        "checkpoint_branch": branch,
                        "acceptance": "geometry build returned and feature/object names read back; geometric validity is not certified",
                        "applied": applied})
    except Exception as exc:
        return _unknown("repair feature/build was dispatched; branch state requires reconciliation",
                        operation="geometry.repair", cause=exc,
                        data={"geometry": canonical, "repair_feature": feature_tag,
                              "checkpoint_branch": branch, "mutation_possible": changed,
                              "applied": applied})


def material_import(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("artifact_id", "selection", "provenance"),
                                 ("artifact_id", "provenance"))
    _managed_model(context, model_tag)
    if body.get("selection") is not None:
        raise PreWriteRefusal("API_UNSUPPORTED", "material.import selection binding requires a verified component branch adapter")
    provenance = require_mapping(body["provenance"], "provenance")
    reject_unknown_keys(provenance, ("source_sha256", "material_tags", "source_units", "topology", "notes"), "provenance")
    resolved, record = _registered(context, body["artifact_id"], {"material_source", "saved_model"})
    source_path = Path(resolved["path"])
    if source_path.suffix.lower() not in {".mph", ".xml"}:
        raise PreWriteRefusal("ARTIFACT_FORMAT_MISMATCH", "material.import supports registered MPH or XML sources")
    if provenance.get("source_sha256") != resolved["sha256"]:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "provenance.source_sha256 must equal the immutable registered source SHA-256")
    registered_provenance = record.get("provenance")
    if (not isinstance(registered_provenance, Mapping)
            or registered_provenance.get("source_sha256") != resolved["sha256"]
            or not isinstance(registered_provenance.get("source_units"), str)
            or registered_provenance.get("source_units") != provenance.get("source_units")
            or not isinstance(registered_provenance.get("topology"), Mapping)
            or dict(registered_provenance.get("topology", {})) != provenance.get("topology")):
        raise PreWriteRefusal("ARTIFACT_PROVENANCE_REQUIRED", "source units and topology must match the immutable registration provenance")
    if not isinstance(provenance.get("source_units"), str) or not provenance["source_units"].strip():
        raise PreWriteRefusal("ARTIFACT_PROVENANCE_REQUIRED", "provenance.source_units must be a registered nonempty declaration")
    if not isinstance(provenance.get("topology"), Mapping):
        raise PreWriteRefusal("ARTIFACT_PROVENANCE_REQUIRED", "provenance.topology must be a registered declaration object")
    expected_tags = require_string_array(provenance.get("material_tags"), "provenance.material_tags")
    if (not expected_tags or len(expected_tags) > 32 or len(set(expected_tags)) != len(expected_tags)
            or any(not _SAFE_TAG.fullmatch(tag) for tag in expected_tags)):
        raise PreWriteRefusal("INVALID_REQUEST", "provenance.material_tags must contain 1..32 unique COMSOL tags")
    model = bound_model(worker, model_tag)
    material_list = _call(model, "material")
    before = tag_list(material_list)
    conflicts = sorted(set(expected_tags) & set(before))
    if conflicts:
        raise PreWriteRefusal("TAG_CONFLICT", "material tags already exist; insert does not overwrite existing materials",
                              details={"conflicts": conflicts})
    # Only JSON provenance supplied by the caller is echoed as declared data.
    # The registration is the authority for bytes; these fields do not prove
    # material units or geometric applicability.
    try:
        raw_result = _call(material_list, "insert", str(source_path), list(expected_tags))
        after = tag_list(material_list)
        new_tags = sorted(set(after) - set(before))
        if not set(expected_tags).issubset(after) or set(new_tags) != set(expected_tags):
            raise RuntimeError("MaterialList.insert tag readback differs from requested material_tags")
        if not isinstance(raw_result, (list, tuple)) or len(raw_result) != 3:
            raise RuntimeError("MaterialList.insert returned an unexpected 3-array result")
        messages, inserted_paths, reference_paths = raw_result
        if not all(isinstance(items, (list, tuple)) for items in (messages, inserted_paths, reference_paths)):
            raise RuntimeError("MaterialList.insert result arrays are malformed")
        if len(inserted_paths) != len(expected_tags):
            raise RuntimeError("MaterialList.insert object paths do not enumerate every requested material")
        return _result({"artifact_id": resolved["artifact_id"], "source_sha256": resolved["sha256"],
                        "source_size_bytes": resolved["size"], "source_role": record["role"],
                        "source_path": record.get("path"), "source_format": source_path.suffix.lower(),
                        "material_tags_requested": expected_tags, "material_tags_before": before,
                        "material_tags_after": after, "inserted_paths": [str(v) for v in inserted_paths],
                        "reference_paths": [str(v) for v in reference_paths],
                        "engine_messages": [str(v) for v in messages],
                        "declared_provenance": {k: v for k, v in provenance.items() if k != "source_sha256"},
                        "registered_provenance": {k: v for k, v in registered_provenance.items()
                                                   if k in {"source_sha256", "source_units", "topology"}},
                        "provenance_status": "SOURCE_BYTES_AND_REGISTERED_UNITS_TOPOLOGY_VERIFIED; applicability remains unverified",
                        "scope": "model-global MaterialList"})
    except Exception as exc:
        return _unknown("MaterialList.insert was dispatched; inspect material tags before retry",
                        operation="material.import", cause=exc,
                        data={"artifact_id": resolved["artifact_id"], "source_sha256": resolved["sha256"],
                              "material_tags_before": before, "material_tags_after": _safe_tags(material_list),
                              "requested_tags": expected_tags})


def _safe_tags(container: Any) -> list[str] | None:
    try:
        return tag_list(container)
    except Exception:
        return None


def _typed_string(value: str) -> dict[str, Any]:
    return {"kind": "string", "shape": [], "data": value, "java_signature": "java.lang.String"}


def _read_property_value(node: Any, row: Mapping[str, Any], name: str) -> Any:
    getter = row.get("getter")
    if not isinstance(getter, str) or not getter:
        raise PreWriteRefusal("API_UNSUPPORTED", f"property {name!r} has no authoritative getter")
    return _call(node, getter, name)


def _normalize_property_assignment(node: Any, name: Any, raw: Any, allowed: set[str]) -> dict[str, Any]:
    prop = require_string(name, "definition.properties key", max_length=80)
    if prop not in allowed:
        raise PreWriteRefusal("API_UNSUPPORTED", f"PDE property {prop!r} is outside the documented allow-list")
    if isinstance(raw, str):
        raw = require_string(raw, f"definition.properties.{prop}", max_length=4096)
        rows = property_schema_from_engine(node, prop)
        if rows.get("metadata_status") != "KNOWN" or rows.get("shape_rank") != 0:
            raise PreWriteRefusal("API_UNSUPPORTED", f"PDE scalar property {prop!r} lacks authoritative scalar metadata")
        if rows.get("kind") != "string":
            raise PreWriteRefusal("API_UNSUPPORTED", f"PDE expression property {prop!r} is not exposed as a string")
        return {"name": prop, "rank": 0, "values": raw, "schema": rows}
    # Indexed coefficient arrays/matrices accept explicit strings only.  Their
    # exact shape must match the live value to prevent setIndex from extending
    # an unobserved COMSOL coefficient table.
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, Mapping)):
        rows = property_schema_from_engine(node, prop)
        if rows.get("metadata_status") != "KNOWN" or rows.get("kind") != "string":
            raise PreWriteRefusal("API_UNSUPPORTED", f"PDE indexed property {prop!r} lacks authoritative string metadata")
        rank = rows.get("shape_rank")
        if rank not in {1, 2}:
            raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", f"PDE indexed property {prop!r} is not an array or matrix")
        current = _read_property_value(node, rows, prop)
        if rank == 1:
            if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) and item.strip() and len(item) <= 4096 for item in raw):
                raise PreWriteRefusal("INVALID_REQUEST", f"definition.properties.{prop} must be a string array")
            if not isinstance(current, (list, tuple)) or len(current) != len(raw):
                raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", f"{prop} array length must match native readback")
            return {"name": prop, "rank": 1, "values": list(raw), "schema": rows}
        if (not isinstance(raw, (list, tuple)) or not raw
                or any(not isinstance(line, (list, tuple)) for line in raw)
                or any(not isinstance(item, str) or not item.strip() or len(item) > 4096 for line in raw for item in line)):
            raise PreWriteRefusal("INVALID_REQUEST", f"definition.properties.{prop} must be a rectangular string matrix")
        widths = {len(line) for line in raw}
        if len(widths) != 1 or not isinstance(current, (list, tuple)) or len(current) != len(raw):
            raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", f"{prop} matrix shape must match native readback")
        current_widths = {len(line) for line in current if isinstance(line, (list, tuple))}
        if len(current_widths) != 1 or current_widths != widths:
            raise PreWriteRefusal("PROPERTY_TYPE_MISMATCH", f"{prop} matrix column count must match native readback")
        return {"name": prop, "rank": 2, "values": [list(line) for line in raw], "schema": rows}
    raise PreWriteRefusal("INVALID_REQUEST", f"definition.properties.{prop} must be a string or string array/matrix")


def _set_property_assignments(worker: Any, model_tag: str, path: Mapping[str, Any], node: Any,
                              assignments: Mapping[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    if not assignments:
        raise PreWriteRefusal("INVALID_REQUEST", "at least one PDE property assignment is required")
    normalized = [_normalize_property_assignment(node, name, value, allowed)
                  for name, value in assignments.items()]
    applied: list[dict[str, Any]] = []
    for item in normalized:
        name, rank, values, schema = item["name"], item["rank"], item["values"], item["schema"]
        if rank == 0:
            typed = {"kind": "string", "shape": [], "data": values, "java_signature": schema["java_signature"]}
            result = property_set(worker, model_tag, path, [{"name": name, "value": typed}])
            if result.get("success") is not True:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"PDE property {name!r} write/readback failed",
                                             stage="post_dispatch", details={"result": result})
            rows = result.get("data", {}).get("applied", [])
            applied.extend({"property": name, "index": None, "readback": row.get("readback")}
                           for row in rows if isinstance(row, Mapping))
            continue
        indices = ([(i,) for i in range(len(values))] if rank == 1
                   else [(i, j) for i, line in enumerate(values) for j in range(len(line))])
        for index in indices:
            value = values[index[0]] if rank == 1 else values[index[0]][index[1]]
            result = property_index_set(worker, model_tag, path, name, list(index), _typed_string(value))
            if result.get("success") is not True:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"PDE indexed property {name!r}{index} write/readback failed",
                                             stage="post_dispatch", details={"result": result})
            verification = result.get("data", {}).get("verification")
            if not isinstance(verification, Mapping) or verification.get("status") != "PASS":
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"PDE property {name!r}{index} readback did not verify",
                                             stage="post_dispatch", details={"result": result})
            applied.append({"property": name, "index": list(index), "readback": result.get("data", {}).get("readback"),
                            "verification": dict(verification)})
    return applied


def _component_physics(worker: Any, model_tag: str, component: str) -> tuple[Any, Any]:
    model = bound_model(worker, model_tag)
    comp = _call(model, "component", component)
    physics = _call(comp, "physics")
    return comp, physics


def _global_equation_row(raw: Any, index: int) -> dict[str, str]:
    row = require_mapping(raw, f"definition.equations[{index}]")
    reject_unknown_keys(row, ("name", "equation", "initial_value_u", "initial_value_ut"),
                        f"definition.equations[{index}]")
    if "name" not in row or "equation" not in row or "initial_value_u" not in row:
        raise PreWriteRefusal("INVALID_REQUEST", f"definition.equations[{index}] requires name, equation, initial_value_u")
    result = {
        "name": require_string(row["name"], f"definition.equations[{index}].name", max_length=128),
        "equation": require_string(row["equation"], f"definition.equations[{index}].equation", max_length=2048),
        "initialValueU": require_string(row["initial_value_u"], f"definition.equations[{index}].initial_value_u", max_length=512),
    }
    if row.get("initial_value_ut") is not None:
        result["initialValueUt"] = require_string(row["initial_value_ut"], f"definition.equations[{index}].initial_value_ut", max_length=512)
    return result


def _write_global_equations(worker: Any, model_tag: str, path: Mapping[str, Any], node: Any,
                            equations: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not equations or len(equations) > 16:
        raise PreWriteRefusal("INVALID_REQUEST", "global equation list must contain 1..16 rows")
    # Check every property, getter and index capacity before the first setIndex.
    prepared: dict[str, dict[str, Any]] = {}
    for prop in sorted({key for row in equations for key in row}):
        schema = property_schema_from_engine(node, prop)
        if (schema.get("metadata_status") != "KNOWN" or schema.get("kind") != "string"
                or schema.get("shape_rank") != 1 or not schema.get("getter")):
            raise PreWriteRefusal("API_UNSUPPORTED", f"GlobalEquations.{prop} lacks an authoritative string-array contract")
        current = _read_property_value(node, schema, prop)
        if not isinstance(current, (list, tuple)) or len(current) < len(equations):
            raise PreWriteRefusal("API_UNSUPPORTED", f"GlobalEquations.{prop} native array has insufficient indexed capacity")
        prepared[prop] = schema
    applied: list[dict[str, Any]] = []
    for index, row in enumerate(equations):
        for prop, value in row.items():
            result = property_index_set(worker, model_tag, path, prop, [index], _typed_string(value))
            verify = result.get("data", {}).get("verification") if isinstance(result, Mapping) else None
            if result.get("success") is not True or not isinstance(verify, Mapping) or verify.get("status") != "PASS":
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", f"GlobalEquations {prop}[{index}] write/readback failed",
                                             stage="post_dispatch", details={"result": result})
            applied.append({"property": prop, "index": index, "value": value,
                            "readback": result.get("data", {}).get("readback"),
                            "verification": dict(verify)})
    return applied


def physics_pde_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("action", "path", "definition"), ("action", "definition"))
    _managed_model(context, model_tag)
    action = require_string(body["action"], "action", max_length=40)
    definition = require_mapping(body["definition"], "definition")
    if action == "configure":
        if body.get("path") is None:
            raise PreWriteRefusal("INVALID_REQUEST", "physics.pde_manage configure requires path to a PDE equation feature")
        path, node = resolve_path(worker, model_tag, body["path"], label="path")
        parsed = NodePath.from_wire(path)
        if (len(parsed.segments) != 3 or [s.collection for s in parsed.segments] != ["component", "physics", "feature"]):
            raise PreWriteRefusal("INVALID_NODE_PATH", "configure path must identify component/physics/feature")
        parent_path = NodePath(tuple(parsed.segments[:-1])).as_dict()
        _parent_path, interface = resolve_path(worker, model_tag, parent_path, label="path")
        interface_type = node_type(interface)
        reject_unknown_keys(definition, ("properties", "equations"), "definition")
        if interface_type == "GlobalEquations":
            if set(definition) != {"equations"}:
                raise PreWriteRefusal("INVALID_REQUEST", "GlobalEquations configure requires only definition.equations")
            rows_raw = definition["equations"]
            if not isinstance(rows_raw, (list, tuple)) or isinstance(rows_raw, (str, bytes)):
                raise PreWriteRefusal("INVALID_REQUEST", "definition.equations must be an array")
            rows = [_global_equation_row(value, i) for i, value in enumerate(rows_raw)]
            try:
                applied = _write_global_equations(worker, model_tag, path, node, rows)
                return _result({"action": action, "path": path, "physics_type": interface_type,
                                "equations_requested": rows, "applied": applied,
                                "unit_status": "expressions preserved as written; COMSOL dimensional consistency not checked"})
            except PreWriteRefusal:
                raise
            except Exception as exc:
                return _unknown("GlobalEquations indexed write was dispatched; inspect current feature values",
                                operation="physics.pde_manage", cause=exc,
                                data={"action": action, "path": path, "physics_type": interface_type,
                                      "applied": locals().get("applied", []), "requested": rows})
        if interface_type not in _PDE_INTERFACE_TYPES:
            raise PreWriteRefusal("API_UNSUPPORTED", f"configure only supports PDE interfaces; observed {interface_type!r}")
        if set(definition) != {"properties"}:
            raise PreWriteRefusal("INVALID_REQUEST", "PDE configure requires only definition.properties")
        props = require_mapping(definition["properties"], "definition.properties")
        if interface_type == "WeakFormPDE":
            allowed = {"weak"}
        else:
            allowed = set(_COEFFICIENT_PROPERTIES)
        try:
            applied = _set_property_assignments(worker, model_tag, path, node, props, allowed)
            return _result({"action": action, "path": path, "physics_type": interface_type,
                            "feature_type": node_type(node), "properties_requested": props,
                            "applied": applied,
                            "unit_status": "expression text and COMSOL unit syntax preserved; dimensional consistency not checked"})
        except PreWriteRefusal:
            raise
        except Exception as exc:
            return _unknown("PDE property setter/readback was dispatched; inspect feature values",
                            operation="physics.pde_manage", cause=exc,
                            data={"action": action, "path": path, "physics_type": interface_type,
                                  "applied": locals().get("applied", []), "requested": props})

    if action == "create_global_equations":
        if body.get("path") is not None:
            raise PreWriteRefusal("INVALID_REQUEST", "create_global_equations does not accept path")
        reject_unknown_keys(definition, ("component", "tag", "equations"), "definition")
        if set(definition) != {"component", "tag", "equations"}:
            raise PreWriteRefusal("INVALID_REQUEST", "create_global_equations requires component, tag, equations")
        component = validate_tag(definition["component"], "definition.component")
        tag = validate_tag(definition["tag"], "definition.tag")
        equations_raw = definition["equations"]
        if not isinstance(equations_raw, (list, tuple)) or isinstance(equations_raw, (str, bytes)):
            raise PreWriteRefusal("INVALID_REQUEST", "definition.equations must be an array")
        equations = [_global_equation_row(value, i) for i, value in enumerate(equations_raw)]
        if not equations or len(equations) > 16:
            raise PreWriteRefusal("INVALID_REQUEST", "global equation list must contain 1..16 rows")
        _comp, container = _component_physics(worker, model_tag, component)
        before = tag_list(container)
        if tag in before:
            raise PreWriteRefusal("TAG_CONFLICT", "physics tag already exists")
        changed = False
        try:
            changed = True
            _call(container, "create", tag, "GlobalEquations")
            after = tag_list(container)
            if tag not in after:
                raise RuntimeError("physics list did not read back the created GlobalEquations tag")
            interface = _call(container, "get", tag)
            if node_type(interface) != "GlobalEquations":
                raise RuntimeError("created physics interface type did not read back as GlobalEquations")
            features = _call(interface, "feature")
            feature_tags = tag_list(features)
            if len(feature_tags) != 1:
                raise RuntimeError("GlobalEquations interface did not expose exactly one equation feature")
            feature_tag = feature_tags[0]
            feature = _call(interface, "feature", feature_tag)
            feature_path = {"segments": [
                {"collection": "component", "tag": component},
                {"collection": "physics", "tag": tag},
                {"collection": "feature", "tag": feature_tag},
            ]}
            applied = _write_global_equations(worker, model_tag, feature_path, feature, equations)
            return _result({"action": action, "component": component, "physics_tag": tag,
                            "physics_type": "GlobalEquations", "physics_tags_before": before,
                            "physics_tags_after": after, "equation_feature": feature_tag,
                            "equations_requested": equations, "applied": applied,
                            "unit_status": "expressions preserved as written; COMSOL dimensional consistency not checked"})
        except Exception as exc:
            return _unknown("GlobalEquations create/configure was dispatched; inspect the new interface before retry",
                            operation="physics.pde_manage", cause=exc,
                            data={"action": action, "component": component, "physics_tag": tag,
                                  "physics_tags_before": before, "physics_tags_after": _safe_tags(container),
                                  "mutation_possible": changed, "requested": equations})
    raise PreWriteRefusal("API_UNSUPPORTED", "physics.pde_manage supports configure and create_global_equations only")


def _mesh_path_context(worker: Any, model_tag: str, path: Any) -> tuple[dict[str, Any], Any, str, str]:
    from ._g3_w16 import _mesh_sequence_context
    return _mesh_sequence_context(worker, model_tag, path, label="path")


def _mesh_metrics(sequence: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for method in ("getSDim", "getNumElem", "getNumVertex", "isEmpty", "isComplete", "getTypes", "lengthUnit"):
        probe = call_probe(sequence, method)
        if probe["ok"]:
            value = probe.get("value")
            values[method] = list(value) if isinstance(value, (list, tuple)) else value
        else:
            errors[method] = probe.get("error")
    return {"values": values, "errors": errors}


def mesh_import(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("path", "artifact_id", "options"), ("path", "artifact_id"))
    _managed_model(context, model_tag)
    options = require_mapping(body.get("options", {}), "options")
    if options:
        raise PreWriteRefusal("API_UNSUPPORTED", "mesh.import options are not in the verified default-import subset")
    canonical, sequence, component, mesh_tag = _mesh_path_context(worker, model_tag, body["path"])
    resolved, record = _registered(context, body["artifact_id"], {"mesh_source"})
    source_path = Path(resolved["path"])
    source_kind = _MESH_IMPORT_SOURCES.get(source_path.suffix.lower())
    if source_kind is None:
        raise PreWriteRefusal("ARTIFACT_FORMAT_MISMATCH", "registered artifact suffix is not a documented COMSOL 6.4 mesh import format")
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping) or not isinstance(provenance.get("source_units"), str) or not provenance.get("source_units"):
        raise PreWriteRefusal("ARTIFACT_PROVENANCE_REQUIRED", "mesh source registration must preserve declared source_units")
    if provenance.get("source_sha256") != resolved["sha256"]:
        raise PreWriteRefusal("ARTIFACT_HASH_MISMATCH", "mesh source registration provenance hash is inconsistent")
    if not isinstance(provenance.get("topology"), Mapping):
        raise PreWriteRefusal("ARTIFACT_PROVENANCE_REQUIRED", "mesh source registration must preserve a topology declaration object")
    feature_tag = "mcpImp_" + resolved["sha256"][:16]
    features = _call(sequence, "feature")
    before_tags = tag_list(features)
    if feature_tag in before_tags:
        raise PreWriteRefusal("TAG_CONFLICT", "deterministic mesh Import feature tag already exists")
    model = bound_model(worker, model_tag)
    component_node = _call(model, "component", component)
    geometries = _call(component_node, "geom")
    geom_tag = _call(sequence, "geom")
    geometry = _call(component_node, "geom", str(geom_tag))
    unit_probe = call_probe(geometry, "lengthUnit")
    if not unit_probe["ok"] or not isinstance(unit_probe.get("value"), str):
        raise PreWriteRefusal("API_UNSUPPORTED", "mesh.import requires a readable owning geometry length unit")
    feature_path = {"segments": [
        {"collection": "component", "tag": component},
        {"collection": "mesh", "tag": mesh_tag},
        {"collection": "feature", "tag": feature_tag},
    ]}
    changed = False
    applied: list[dict[str, Any]] = []
    try:
        changed = True
        _call(sequence, "create", feature_tag, "Import")
        after_tags = tag_list(features)
        if feature_tag not in after_tags:
            raise RuntimeError("mesh Import feature creation did not read back")
        feature = _call(sequence, "feature", feature_tag)
        if node_type(feature) != "Import":
            raise RuntimeError("mesh feature type did not read back as Import")
        current_props = property_schema_from_engine(feature)
        rows = current_props.get("properties", []) if isinstance(current_props, Mapping) else []
        names = {row.get("name") for row in rows if isinstance(row, Mapping)}
        if not {"source", "filename"}.issubset(names):
            raise RuntimeError("mesh Import feature lacks documented source/filename properties")
        payload = []
        for name, value in (("source", source_kind), ("filename", str(source_path))):
            schema = property_schema_from_engine(feature, name)
            if schema.get("metadata_status") != "KNOWN" or schema.get("kind") != "string" or schema.get("shape_rank") != 0:
                raise RuntimeError(f"mesh Import {name} property has no authoritative scalar string contract")
            payload.append({"name": name, "value": _typed_string(value)})
        written = property_set(worker, model_tag, feature_path, payload)
        if written.get("success") is not True:
            raise RuntimeError("mesh Import source/filename setter was not readback-verified")
        applied.extend(written.get("data", {}).get("applied", []))
        filename_read = _call(feature, "getString", "filename")
        source_read = _call(feature, "getString", "source")
        if filename_read != str(source_path) or source_read != source_kind:
            raise RuntimeError("mesh Import source/filename direct readback differs")
        _call(feature, "importData")
        imported = _mesh_metrics(sequence)
        if imported["values"].get("isComplete") is not True or imported["values"].get("isEmpty") is not False:
            _call(sequence, "run")
        post = _mesh_metrics(sequence)
        count = post["values"].get("getNumElem")
        is_empty = post["values"].get("isEmpty")
        if (post["values"].get("isComplete") is not True or is_empty is not False
                or isinstance(count, bool) or not isinstance(count, int) or count <= 0):
            raise RuntimeError("mesh import/run did not produce a complete, nonempty native mesh with positive element count")
        return _result({"path": canonical, "component": component, "mesh": mesh_tag,
                        "import_feature": feature_tag, "artifact_id": resolved["artifact_id"],
                        "source_sha256": resolved["sha256"], "source_size_bytes": resolved["size"],
                        "source_format": source_path.suffix.lower(), "source_kind": source_kind,
                        "source_units": provenance["source_units"], "source_units_status": "REGISTERED_DECLARATION",
                        "topology": dict(provenance["topology"]), "topology_status": "REGISTERED_DECLARATION",
                        "source_provenance": dict(provenance), "owning_geometry_length_unit": unit_probe["value"],
                        "unit_behavior": "COMSOL scales files that declare a length unit to the component mesh length unit; exact input unit text is registration provenance",
                        "state_before": {"feature_tags": before_tags},
                        "feature_tags_after": after_tags, "import_result": imported,
                        "mesh_readback": post, "applied": applied,
                        "coverage": "native mesh counts/types only; topology equivalence and physical validity are not certified"})
    except Exception as exc:
        return _unknown("mesh Import feature/data/run was dispatched; inspect mesh sequence before retry",
                        operation="mesh.import", cause=exc,
                        data={"path": canonical, "component": component, "mesh": mesh_tag,
                              "import_feature": feature_tag, "artifact_id": resolved["artifact_id"],
                              "source_sha256": resolved["sha256"], "source_units": provenance.get("source_units"),
                              "topology": provenance.get("topology"), "feature_tags_before": before_tags,
                              "feature_tags_after": _safe_tags(features), "applied": applied,
                              "mutation_possible": changed})


def mesh_export(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("path", "format", "destination"),
                                 ("path", "format", "destination"))
    _managed_model(context, model_tag)
    fmt, suffix = _require_format("mesh.export", body["format"])
    canonical, sequence, component, mesh_tag = _mesh_path_context(worker, model_tag, body["path"])
    before = _mesh_metrics(sequence)
    metrics = before["values"]
    sdim = metrics.get("getSDim")
    if metrics.get("isComplete") is not True or metrics.get("isEmpty") is not False:
        raise PreWriteRefusal("MESH_NOT_READY", "mesh.export requires native isComplete=true and isEmpty=false readbacks")
    if (isinstance(metrics.get("getNumElem"), bool) or not isinstance(metrics.get("getNumElem"), int)
            or metrics["getNumElem"] <= 0):
        raise PreWriteRefusal("MESH_EMPTY", "mesh.export requires a positive native element count")
    if fmt == "stl" and sdim != 3:
        raise PreWriteRefusal("API_UNSUPPORTED", "COMSOL 6.4 MeshSequence exports STL only for 3D meshes")
    root, store = _project_store(context)
    destination = _validate_destination(store, body["destination"], suffix)
    private_dir, stage = _stage_path(root, suffix)
    try:
        returned = _call(sequence, "export", str(stage))
        if not stage.is_file() or stage.is_symlink():
            raise RuntimeError("MeshSequence.export returned without a regular staged file")
        receipt = _publish_export(context, private_dir, stage, destination,
                                  role="mesh_export", artifact_type=f"application/vnd.comsol.mesh.{fmt}")
        after = _mesh_metrics(sequence)
        return _result({"path": canonical, "component": component, "mesh": mesh_tag,
                        "format": fmt, "state_before": before, "state_after": after,
                        "engine_return_path": str(returned) if returned is not None else None,
                        "file": receipt,
                        "coverage": "mesh file bytes and element/vertex readbacks; no physical acceptance claim"})
    except Exception as exc:
        cleanup_ok = _cleanup_stage(private_dir)
        return _unknown("mesh export was dispatched but publication/readback did not fully verify",
                        operation="mesh.export", cause=exc,
                        data={"path": canonical, "component": component, "mesh": mesh_tag,
                              "format": fmt, "destination": destination,
                              "stage_cleanup_verified": cleanup_ok, "state_before": before})
    finally:
        if private_dir.exists():
            _cleanup_stage(private_dir)


def _convergence_definition(value: Any, metrics: Any) -> tuple[dict[str, Any], list[str]]:
    definition = require_mapping(value, "definition")
    reject_unknown_keys(definition, ("mesh_path", "study_path", "levels", "source_checkpoint_id"), "definition")
    required = {"mesh_path", "study_path", "levels", "source_checkpoint_id"}
    if set(definition) != required:
        raise PreWriteRefusal("INVALID_REQUEST", "definition requires mesh_path, study_path, levels, and source_checkpoint_id")
    if not isinstance(definition["mesh_path"], Mapping) or not isinstance(definition["study_path"], Mapping):
        raise PreWriteRefusal("INVALID_REQUEST", "mesh_path and study_path must be NodePath objects")
    checkpoint_id = require_string(definition["source_checkpoint_id"], "definition.source_checkpoint_id", max_length=128)
    levels = definition["levels"]
    if not isinstance(levels, (list, tuple)) or isinstance(levels, (str, bytes)) or not 2 <= len(levels) <= 8:
        raise PreWriteRefusal("INVALID_REQUEST", "levels must contain 2..8 bounded mesh case definitions")
    normalized_levels = []
    case_ids = set()
    for index, raw in enumerate(levels):
        row = require_mapping(raw, f"definition.levels[{index}]")
        reject_unknown_keys(row, ("case_id", "refinement"), f"definition.levels[{index}]")
        if set(row) != {"case_id", "refinement"}:
            raise PreWriteRefusal("INVALID_REQUEST", f"levels[{index}] requires case_id and refinement")
        case_id = require_string(row["case_id"], f"levels[{index}].case_id", max_length=64)
        if not _SAFE_TAG.fullmatch(case_id) or case_id in case_ids:
            raise PreWriteRefusal("INVALID_REQUEST", "convergence case_id values must be unique bounded tags")
        case_ids.add(case_id)
        refinement = require_mapping(row["refinement"], f"levels[{index}].refinement")
        reject_unknown_keys(refinement, ("nrefine",), f"levels[{index}].refinement")
        if set(refinement) != {"nrefine"}:
            raise PreWriteRefusal("INVALID_REQUEST", f"levels[{index}].refinement requires only nrefine")
        count = refinement["nrefine"]
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 4:
            raise PreWriteRefusal("INVALID_REQUEST", "refinement.nrefine must be an integer from 1 to 4")
        normalized_levels.append({"case_id": case_id, "refinement": {"nrefine": count}})
    metric_ids = require_string_array(metrics, "metrics")
    if not metric_ids or len(metric_ids) > 32 or len(set(metric_ids)) != len(metric_ids):
        raise PreWriteRefusal("INVALID_REQUEST", "metrics must contain 1..32 unique registered metric definition SHA-256 IDs")
    if any(not _SHA256.fullmatch(metric_id) for metric_id in metric_ids):
        raise PreWriteRefusal("INVALID_REQUEST", "metrics entries must be lowercase metric-definition SHA-256 IDs")
    definition = {"mesh_path": NodePath.from_wire(definition["mesh_path"]).as_dict(),
                  "study_path": NodePath.from_wire(definition["study_path"]).as_dict(),
                  "levels": normalized_levels, "source_checkpoint_id": checkpoint_id}
    return definition, metric_ids


def _registered_metric_refs(context: Mapping[str, Any], metric_hashes: Sequence[str]) -> list[dict[str, Any]]:
    """Resolve requested metric-definition digests from this project's store."""
    _require_context(context, "operation_store", "project_id")
    store = context["operation_store"]
    try:
        records = store.read_metric_definitions(str(context["project_id"]))
    except Exception as exc:
        raise PreWriteRefusal("METRIC_REGISTRY_UNAVAILABLE", "project metric definitions could not be read before mesh cases") from exc
    by_hash: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        digest = record.get("definition_sha256")
        if isinstance(digest, str) and _SHA256.fullmatch(digest) and record.get("removed") is not True:
            by_hash[digest] = record
    missing = [digest for digest in metric_hashes if digest not in by_hash]
    if missing:
        raise PreWriteRefusal("METRIC_NOT_FOUND", "every requested metric SHA must name an active definition in this project",
                              details={"missing_metric_sha256": missing})
    resolved = []
    for digest in metric_hashes:
        record = by_hash[digest]
        metric_id = record.get("metric_id")
        version = record.get("version")
        definition = record.get("definition")
        if (not isinstance(metric_id, str) or not metric_id or isinstance(version, bool)
                or not isinstance(version, int) or version < 1 or not isinstance(definition, Mapping)):
            raise PreWriteRefusal("METRIC_REGISTRY_UNAVAILABLE", "registered metric version metadata is incomplete")
        from ._g3_metrics import definition_sha256
        if definition_sha256(definition) != digest:
            raise PreWriteRefusal("INTEGRITY_COMPROMISED", "registered metric definition does not match its requested SHA-256")
        resolved.append({"metric_id": metric_id, "version": version,
                         "definition_sha256": digest, "definition": dict(definition)})
    return resolved


def _verify_registered_observation(context: Mapping[str, Any], reference: Mapping[str, Any],
                                   branch_ref: Mapping[str, Any], branch_revision: int) -> dict[str, Any]:
    store = context["operation_store"]
    operation_id = context["operation_id"]
    try:
        record = store.get_metadata("artifacts", reference["observation_id"])
    except Exception as exc:
        raise ExecutionContractError("UNVERIFIED_OBSERVATION", "registered observation metadata could not be read", stage="post_dispatch") from exc
    if (not isinstance(record, Mapping) or record.get("kind") != "w17_observation"
            or record.get("observation_id") != reference["observation_id"]
            or record.get("project_id") != context.get("project_id")
            or record.get("model_ref") != dict(branch_ref)
            or record.get("revision") != branch_revision
            or record.get("producer") != operation_id
            or record.get("sha256") != reference["sha256"]):
        raise ExecutionContractError("UNVERIFIED_OBSERVATION", "ObservationRef does not bind to this project, branch revision, and running operation", stage="post_dispatch")
    artifact = record.get("artifact")
    artifact_sha = artifact.get("sha256") if isinstance(artifact, Mapping) else None
    artifact_size = artifact.get("byte_size") if isinstance(artifact, Mapping) else None
    if (not isinstance(artifact, Mapping) or artifact_sha != reference["sha256"]
            or isinstance(artifact_size, bool) or not isinstance(artifact_size, int) or artifact_size < 0):
        raise ExecutionContractError("INTEGRITY_COMPROMISED", "registered observation artifact descriptor is missing or mismatched", stage="post_dispatch")
    try:
        root, artifact_store = _project_store(context)
        path = artifact_store.resolve_safe_path(artifact.get("file_path"), allow_overwrite=True)
        from ._artifact_store import _hash_regular_artifact
        digest, identity = _hash_regular_artifact(path)
        if digest != reference["sha256"] or identity.get("size") != artifact_size:
            raise ExecutionContractError("INTEGRITY_COMPROMISED", "registered observation bytes differ from their immutable digest", stage="post_dispatch")
        operation = store.get_operation(operation_id)
    except ExecutionContractError:
        raise
    except Exception as exc:
        raise ExecutionContractError("UNVERIFIED_OBSERVATION", "observation artifact or producer operation could not be revalidated", stage="post_dispatch") from exc
    if not isinstance(operation, Mapping) or operation.get("project_id") != context.get("project_id") or operation.get("status") not in {"RUNNING", "SUCCEEDED"}:
        raise ExecutionContractError("UNVERIFIED_OBSERVATION", "observation producer is not the current project operation", stage="post_dispatch")
    return {"observation_id": reference["observation_id"], "sha256": reference["sha256"],
            "artifact_size_bytes": identity["size"], "producer_operation_id": operation_id,
            "project_id": context["project_id"], "model_ref": dict(branch_ref), "model_revision": branch_revision}


def _validate_case_result(case: Mapping[str, Any], case_id: str, metric_refs: Sequence[Mapping[str, Any]],
                          source_model_ref: Mapping[str, Any], seen_branches: set[str],
                          context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(case, Mapping) or case.get("status") != "SUCCEEDED":
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "internal convergence case did not return SUCCEEDED")
    required = {"case_id", "checkpoint_id", "branch_model_ref", "branch_revision", "mesh_readback",
                "study_readback", "observations", "source_model_ref"}
    if not required.issubset(case) or case.get("case_id") != case_id or case.get("source_model_ref") != source_model_ref:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "convergence case identity/provenance fields are incomplete")
    if not isinstance(case.get("checkpoint_id"), str) or not case["checkpoint_id"]:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "convergence case has no checkpoint lineage")
    branch = case.get("branch_model_ref")
    if not isinstance(branch, Mapping) or branch == source_model_ref:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "convergence case does not identify an owned branch ModelRef")
    identity_keys = {"schema_version", "session_id", "server_instance_id", "generation"}
    if (set(branch) != set(source_model_ref) or any(branch.get(key) != source_model_ref.get(key) for key in identity_keys)
            or branch.get("model_tag") == source_model_ref.get("model_tag")):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "convergence branch ModelRef is not a fresh tag in the same worker generation")
    branch_key = json.dumps(dict(branch), sort_keys=True, separators=(",", ":"))
    if branch_key in seen_branches:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "convergence cases must use distinct branch ModelRefs")
    seen_branches.add(branch_key)
    if isinstance(case.get("branch_revision"), bool) or not isinstance(case.get("branch_revision"), int) or case["branch_revision"] < 0:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "branch_revision is invalid")
    mesh = case.get("mesh_readback")
    study = case.get("study_readback")
    if not isinstance(mesh, Mapping) or mesh.get("is_complete") is not True or mesh.get("is_empty") is not False:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "native mesh sequence was not confirmed complete/nonempty")
    if isinstance(mesh.get("element_count"), bool) or not isinstance(mesh.get("element_count"), int) or mesh["element_count"] <= 0:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "mesh element count was not read back")
    if not isinstance(study, Mapping) or study.get("status") != "SUCCEEDED" or not study.get("solution"):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "actual Study.run solution readback is missing")
    observations = case.get("observations")
    metric_hashes = [str(row["definition_sha256"]) for row in metric_refs]
    if not isinstance(observations, Mapping) or set(observations) != set(metric_hashes):
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "one registered observation is required for each requested metric")
    checked: dict[str, Any] = {}
    for metric_ref in metric_refs:
        metric_hash = str(metric_ref["definition_sha256"])
        row = observations[metric_hash]
        if not isinstance(row, Mapping):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "observation row is malformed")
        ref = row.get("observation_ref")
        value = row.get("value")
        if (row.get("metric_definition_sha256") != metric_hash or not isinstance(ref, Mapping)
                or set(ref) != _OBS_REF_KEYS or not isinstance(ref.get("observation_id"), str)
                or re.fullmatch(r"obs_[0-9a-f]{32}", ref["observation_id"]) is None
                or not isinstance(ref.get("sha256"), str) or not _SHA256.fullmatch(ref["sha256"])):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "metric row does not carry an immutable ObservationRef")
        if row.get("model_ref") != branch or row.get("model_revision") != case["branch_revision"]:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "observation is not bound to this branch revision")
        if row.get("producer_operation_id") != context.get("operation_id"):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "metric observation producer does not match the active convergence operation")
        registered_observation = _verify_registered_observation(context, ref, branch, case["branch_revision"])
        if not isinstance(value, Mapping) or isinstance(value.get("value"), bool) or not isinstance(value.get("value"), (int, float)) or not math.isfinite(float(value["value"])):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "metric observation value is not finite numeric data")
        if not isinstance(value.get("unit"), str) or not value["unit"]:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "metric observation must preserve a nonempty unit")
        checked[metric_hash] = {"metric_id": metric_ref["metric_id"], "metric_definition_sha256": metric_hash,
                              "observation_ref": dict(ref), "registered_observation": registered_observation,
                              "model_ref": dict(branch),
                              "model_revision": case["branch_revision"],
                              "producer_operation_id": row.get("producer_operation_id"),
                              "value": {"value": float(value["value"]), "unit": value["unit"]}}
    result = {"case_id": case_id, "checkpoint_id": case["checkpoint_id"], "branch_model_ref": dict(branch),
            "branch_revision": case["branch_revision"], "mesh_readback": dict(mesh),
            "study_readback": dict(study), "observations": checked}
    if isinstance(case.get("raw"), Mapping):
        result["raw"] = dict(case["raw"])
    if isinstance(case.get("run_budget"), Mapping):
        result["run_budget"] = dict(case["run_budget"])
    return result


def mesh_convergence_study(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    body, context = _body_context(arguments, ("definition", "metrics"), ("definition", "metrics"))
    _managed_model(context, model_tag)
    definition, metric_hashes = _convergence_definition(body["definition"], body["metrics"])
    metric_refs = _registered_metric_refs(context, metric_hashes)
    executor = context.get("_mesh_convergence_case_executor")
    preflight = context.get("_mesh_convergence_preflight")
    if not callable(executor) or not callable(preflight):
        raise PreWriteRefusal("MANAGED_CONVERGENCE_EXECUTOR_REQUIRED",
                              "mesh.convergence_study requires backend-owned in-lane preflight and case callbacks; no request callback/path is accepted")
    # These capabilities are installed by the backend and run synchronously in
    # its current serial callback. Preflight must finish before any branch is
    # copied or mutated.
    _require_context(context, "model_ref", "source_revision", "operation_id", "execution_service", "backend")
    source_ref = dict(context["model_ref"])
    preflight_result = preflight(worker=worker, model_tag=model_tag, definition=definition,
                                 metric_refs=[dict(metric) for metric in metric_refs],
                                 source_checkpoint_id=definition["source_checkpoint_id"],
                                 level_count=len(definition["levels"]))
    if not isinstance(preflight_result, Mapping) or preflight_result.get("status") != "PREFLIGHTED":
        raise PreWriteRefusal("MANAGED_CONTEXT_REQUIRED", "backend convergence preflight did not return a verified plan")
    seen_branches: set[str] = set()
    cases: list[dict[str, Any]] = []
    for row in definition["levels"]:
        outcome: Any = None
        try:
            outcome = executor(worker=worker, source_model_ref=source_ref,
                               source_revision=context["source_revision"],
                               source_checkpoint_id=definition["source_checkpoint_id"],
                               mesh_path=definition["mesh_path"], study_path=definition["study_path"],
                               case_id=row["case_id"], refinement=dict(row["refinement"]),
                               metric_refs=[dict(metric) for metric in metric_refs],
                               metric_sha256=list(metric_hashes), operation_id=context["operation_id"])
            cases.append(_validate_case_result(outcome, row["case_id"], metric_refs, source_ref, seen_branches, context))
        except Exception as exc:
            executor_plan = getattr(executor, "plan", None)
            branch_started = isinstance(executor_plan, Mapping) and executor_plan.get("branch_started") is True
            if (isinstance(exc, PreWriteRefusal) and not cases and not branch_started
                    and not isinstance(getattr(exc, "details", {}).get("case_evidence"), Mapping)):
                raise
            error_details = getattr(exc, "details", {})
            stopped_evidence = error_details.get("case_evidence") if isinstance(error_details, Mapping) else None
            if not isinstance(stopped_evidence, Mapping) and isinstance(outcome, Mapping):
                stopped_evidence = dict(outcome)
            budget_callback = context.get("_mesh_convergence_budget_status")
            budget = budget_callback() if callable(budget_callback) else None
            return _unknown("convergence case stopped before the next level; preserve all branches and observations",
                            operation="mesh.convergence_study", cause=exc,
                            data={"source_model_ref": source_ref, "source_revision": context["source_revision"],
                                  "source_checkpoint_id": definition["source_checkpoint_id"],
                                  "completed_cases": cases, "stopped_case_id": row["case_id"],
                                  "stopped_case_evidence": dict(stopped_evidence) if isinstance(stopped_evidence, Mapping) else None,
                                  "budget": dict(budget) if isinstance(budget, Mapping) else None,
                                  "partial_change": bool(cases) or isinstance(stopped_evidence, Mapping),
                                  "execution_state_unknown": True,
                                  "next_case_not_executed": True})
    comparisons: dict[str, list[dict[str, Any]]] = {}
    for metric_id in metric_hashes:
        rows = []
        for left, right in zip(cases, cases[1:]):
            a = left["observations"][metric_id]["value"]
            b = right["observations"][metric_id]["value"]
            if a["unit"] != b["unit"]:
                rows.append({"from_case": left["case_id"], "to_case": right["case_id"],
                             "status": "UNIT_MISMATCH", "from": a, "to": b,
                             "absolute_delta": None, "relative_delta": None})
                continue
            delta = b["value"] - a["value"]
            relative = None if a["value"] == 0 else delta / abs(a["value"])
            rows.append({"from_case": left["case_id"], "to_case": right["case_id"],
                         "status": "COMPARED", "unit": a["unit"], "absolute_delta": delta,
                         "relative_delta": relative})
        comparisons[metric_id] = rows
    budget_callback = context.get("_mesh_convergence_budget_status")
    budget = budget_callback() if callable(budget_callback) else None
    return _result({"source_model_ref": source_ref, "source_revision": context["source_revision"],
                    "source_checkpoint_id": definition["source_checkpoint_id"], "cases": cases,
                    "comparisons": comparisons,
                    "preflight": dict(preflight_result),
                    "budget": dict(budget) if isinstance(budget, Mapping) else None,
                    "conclusion": "COMPARISON_ONLY; no tolerance or scientific convergence criterion was requested",
                    "execution_semantics": "sequential in the existing serial lane; one shared ComputationBudget covers Study.run attempts; one case failure stops all later levels; no budget reset"},
                   status="APPLIED")


OPERATIONS = {
    "geometry.export": geometry_export,
    "geometry.repair": geometry_repair,
    "material.import": material_import,
    "physics.pde_manage": physics_pde_manage,
    "mesh.import": mesh_import,
    "mesh.export": mesh_export,
    "mesh.convergence_study": mesh_convergence_study,
}
