"""W18 domain operations: real plotting, geometry rendering, views, and exports.

Provides:
- plot.list: list plot groups and child features with dataset/solution bindings
- plot.group_create: create 1D, 2D, or 3D plot group
- plot.feature_create: create child plot features (Surface, Contour, Slice, Line, etc.)
- plot.update: update properties on plot group or feature
- plot.remove: remove plot feature or entire plot group
- plot.render: execute plot and export image artifact with ImageContent return
- plot.geometry_render: render geometry or mesh to image artifact
- plot.view_manage: list, inspect, create, update views and camera properties
- export.list: list export features under result/export
- export.create: create export feature (Image, Plot, Data, etc.)
- export.update: update properties on export feature
- export.run: execute export and verify artifact file, size, and hash
- export.remove: remove export feature without deleting produced file
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from ._artifact_store import ArtifactStore, trusted_project_root
from ._execution_contract import ExecutionContractError
from ._g2_engine import _call
from ._g3_common import (
    bound_model,
    node_not_found,
    require_bool,
    require_int,
    require_mapping,
    require_string,
)


def _resolve_tag(target: Any) -> str:
    if isinstance(target, str):
        cleaned = target.strip().strip("/")
        parts = cleaned.split("/")
        return parts[-1]
    if isinstance(target, Mapping) and "segments" in target:
        segs = target["segments"]
        if segs and isinstance(segs[-1], Mapping) and "tag" in segs[-1]:
            return str(segs[-1]["tag"])
    return str(target).strip()


def _resolve_plot_path(path: Any) -> tuple[str, str | None]:
    if isinstance(path, str):
        cleaned = path.strip().strip("/")
        parts = cleaned.split("/")
        if parts and parts[0] == "result":
            parts = parts[1:]
        if len(parts) >= 2:
            return parts[0], parts[1]
        elif len(parts) == 1:
            return parts[0], None
    if isinstance(path, Mapping) and "segments" in path:
        segs = [s for s in path["segments"] if isinstance(s, Mapping) and s.get("accessor") != "result"]
        if len(segs) >= 2:
            return str(segs[0].get("tag", "")), str(segs[1].get("tag", ""))
        elif len(segs) == 1:
            return str(segs[0].get("tag", "")), None
    tag = _resolve_tag(path)
    return tag, None


def _apply_properties(node: Any, props: Mapping[str, Any]) -> None:
    for key, value in props.items():
        if isinstance(value, bool):
            _call(node, "set", key, value)
        elif isinstance(value, int):
            _call(node, "set", key, value)
        elif isinstance(value, float):
            _call(node, "set", key, value)
        elif isinstance(value, str):
            _call(node, "set", key, value)
        elif isinstance(value, (list, tuple)):
            str_list = [str(x) for x in value]
            _call(node, "set", key, str_list)
        elif isinstance(value, Mapping):
            for subkey, subval in value.items():
                try:
                    _call(node, "setEntry", key, str(subkey), str(subval))
                except Exception:
                    pass


def _read_properties(node: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        prop_names = list(_call(node, "properties") or [])
    except Exception:
        prop_names = []
    for prop in prop_names[:25]:
        try:
            val = _call(node, "getString", prop)
            out[prop] = val
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# plot.* Operations
# ---------------------------------------------------------------------------

def plot_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List plot groups and their child features in the model."""
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    tags = list(_call(results, "tags") or [])
    plot_groups: list[dict[str, Any]] = []

    for tag in tags:
        try:
            pg = _call(results, "get", tag)
            is_pg = getattr(pg, "isPlotGroup", None)
            if callable(is_pg):
                try:
                    if not _call(pg, "isPlotGroup"):
                        continue
                except Exception:
                    pass

            pg_type = ""
            try:
                pg_type = str(_call(pg, "getType") or "")
            except Exception:
                pass

            dataset_tag = ""
            try:
                dataset_tag = str(_call(pg, "getString", "data") or "")
            except Exception:
                pass

            label = ""
            try:
                label = str(_call(pg, "label") or "")
            except Exception:
                pass

            features: list[dict[str, Any]] = []
            try:
                feat_container = _call(pg, "feature")
                feat_tags = list(_call(feat_container, "tags") or [])
                for f_tag in feat_tags:
                    feat = _call(feat_container, "get", f_tag)
                    f_type = ""
                    try:
                        f_type = str(_call(feat, "getType") or "")
                    except Exception:
                        pass
                    f_props = _read_properties(feat)
                    features.append({
                        "tag": f_tag,
                        "type_id": f_type,
                        "properties": f_props,
                    })
            except Exception:
                pass

            plot_groups.append({
                "tag": tag,
                "type_id": pg_type or "PlotGroup",
                "label": label,
                "dataset": dataset_tag,
                "features": features,
            })
        except Exception:
            continue

    return {
        "plot_groups": plot_groups,
        "total_count": len(plot_groups),
    }


def plot_group_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create a 1D/2D/3D plot group."""
    tag = require_string(arguments.get("tag"), "tag")
    dimension = arguments.get("dimension")
    if dimension is not None:
        dim_int = require_int(dimension, "dimension")
    else:
        dim_int = 2

    type_id = arguments.get("type_id")
    if not type_id:
        if dim_int == 1:
            type_id = "PlotGroup1D"
        elif dim_int == 2:
            type_id = "PlotGroup2D"
        elif dim_int == 3:
            type_id = "PlotGroup3D"
        else:
            type_id = f"PlotGroup{dim_int}D"

    dataset = arguments.get("dataset")
    dataset_tag = _resolve_tag(dataset) if dataset else None
    properties = arguments.get("properties") or {}

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    pg = _call(results, "create", tag, str(type_id))

    if dataset_tag:
        try:
            _call(pg, "set", "data", dataset_tag)
        except Exception:
            pass

    if properties:
        _apply_properties(pg, properties)

    return {
        "tag": tag,
        "type_id": str(type_id),
        "dimension": dim_int,
        "dataset": dataset_tag,
        "properties": dict(properties),
    }


def plot_feature_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create a feature (Surface, Contour, Slice, Line, Arrow, etc.) under a plot group."""
    group_spec = arguments.get("group")
    if not group_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.feature_create requires 'group'")
    group_tag = _resolve_tag(group_spec)
    tag = require_string(arguments.get("tag"), "tag")
    type_id = require_string(arguments.get("type_id"), "type_id")
    properties = arguments.get("properties") or {}

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    try:
        pg = _call(results, "get", group_tag)
    except Exception as exc:
        raise node_not_found(f"plot group {group_tag!r} not found: {exc}") from exc

    feat_list = _call(pg, "feature")
    feat = _call(feat_list, "create", tag, type_id)

    if properties:
        _apply_properties(feat, properties)

    return {
        "group": group_tag,
        "tag": tag,
        "type_id": type_id,
        "properties": dict(properties),
    }


def plot_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Update properties of a plot group or plot feature."""
    path_spec = arguments.get("path")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.update requires 'path'")
    properties = require_mapping(arguments.get("properties", {}), "properties")

    pg_tag, feat_tag = _resolve_plot_path(path_spec)
    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    try:
        pg = _call(results, "get", pg_tag)
    except Exception as exc:
        raise node_not_found(f"plot group {pg_tag!r} not found: {exc}") from exc

    if feat_tag:
        feat_list = _call(pg, "feature")
        try:
            target_node = _call(feat_list, "get", feat_tag)
        except Exception as exc:
            raise node_not_found(f"plot feature {feat_tag!r} under {pg_tag!r} not found: {exc}") from exc
    else:
        target_node = pg

    _apply_properties(target_node, properties)

    return {
        "path": path_spec,
        "group": pg_tag,
        "feature": feat_tag,
        "properties": dict(properties),
        "updated": True,
    }


def plot_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a plot group or a child plot feature."""
    path_spec = arguments.get("path")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.remove requires 'path'")

    pg_tag, feat_tag = _resolve_plot_path(path_spec)
    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    if feat_tag:
        try:
            pg = _call(results, "get", pg_tag)
            feat_list = _call(pg, "feature")
            _call(feat_list, "remove", feat_tag)
        except Exception as exc:
            raise node_not_found(f"failed to remove plot feature {feat_tag!r}: {exc}") from exc
    else:
        try:
            _call(results, "remove", pg_tag)
        except Exception as exc:
            raise node_not_found(f"failed to remove plot group {pg_tag!r}: {exc}") from exc

    return {
        "path": path_spec,
        "group": pg_tag,
        "feature": feat_tag,
        "removed": True,
    }


def plot_render(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Render a plot group to an image artifact and return ImageContent and metadata."""
    path_spec = arguments.get("path")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.render requires 'path'")
    pg_tag, _ = _resolve_plot_path(path_spec)

    options = arguments.get("options") or {}
    width = int(options.get("width") or 800)
    height = int(options.get("height") or 600)
    fmt = str(options.get("format") or "png").lower()
    if fmt not in ("png", "jpg", "jpeg", "bmp", "gif"):
        raise ExecutionContractError("INVALID_REQUEST", f"Unsupported image format: {fmt}")
    dest = options.get("destination")
    allow_overwrite = bool(options.get("allow_overwrite", True))

    root = trusted_project_root(worker)
    store = ArtifactStore(project_root=root)

    if dest:
        target_path = store.resolve_safe_path(dest, allow_overwrite=allow_overwrite)
        if target_path.exists() and not allow_overwrite:
            raise ExecutionContractError("ACCESS_VIOLATION", f"Destination file exists and allow_overwrite is False: {dest}")
    else:
        target_path = store.resolve_safe_path(
            f"g2_artifacts/plots/{pg_tag}_{uuid.uuid4().hex[:8]}.{fmt}",
            allow_overwrite=True,
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = target_path.with_name(f".staging_{uuid.uuid4().hex[:8]}_{target_path.name}")

    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    try:
        pg = _call(results, "get", pg_tag)
    except Exception as exc:
        raise node_not_found(f"plot group {pg_tag!r} not found: {exc}") from exc

    # Apply solution / time / parameter options if specified
    if "solnum" in options:
        try:
            _call(pg, "set", "solnum", str(options["solnum"]))
        except Exception:
            pass
    if "t" in options:
        try:
            _call(pg, "set", "t", str(options["t"]))
        except Exception:
            pass
    if "looplevel" in options:
        try:
            _call(pg, "set", "looplevel", str(options["looplevel"]))
        except Exception:
            pass

    try:
        _call(pg, "run")
    except Exception as exc:
        raise ExecutionContractError("RENDER_FAILED", f"plot group {pg_tag!r} could not be run: {exc}") from exc

    export_list = _call(results, "export")
    exp_tag = f"render_{uuid.uuid4().hex[:8]}"
    try:
        exp = _call(export_list, "create", exp_tag, pg_tag, "Image")
    except Exception:
        exp = _call(export_list, "create", exp_tag, "Image")
        try:
            _call(exp, "set", "sourceobject", pg_tag)
        except Exception:
            try:
                _call(exp, "set", "plotgroup", pg_tag)
            except Exception:
                pass

    try:
        try:
            _call(exp, "set", "pngfilename", str(staging_path))
        except Exception:
            pass
        try:
            _call(exp, "set", "filename", str(staging_path))
        except Exception:
            pass
        try:
            try:
                _call(exp, "set", "size", "manualweb")
            except Exception:
                _call(exp, "set", "size", "manual")
            _call(exp, "set", "unit", "px")
            _call(exp, "set", "width", width)
            _call(exp, "set", "height", height)
        except Exception:
            pass

        _call(exp, "run")
    finally:
        try:
            _call(export_list, "remove", exp_tag)
        except Exception:
            pass

    if not staging_path.is_file() and target_path.is_file():
        staging_path = target_path

    if not staging_path.is_file():
        raise ExecutionContractError("RENDER_FAILED", f"COMSOL export failed to create image at {staging_path}")

    raw_bytes = staging_path.read_bytes()
    if len(raw_bytes) == 0:
        raise ExecutionContractError("RENDER_FAILED", f"Rendered image is empty (0 bytes): {staging_path}")
    if fmt == "png":
        if len(raw_bytes) < 8 or raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise ExecutionContractError("IMAGE_DECODE_ERROR", "Rendered file does not have valid PNG header")
        import struct
        actual_w, actual_h = struct.unpack(">II", raw_bytes[16:24])
    else:
        actual_w, actual_h = width, height

    # Atomic publish
    if staging_path != target_path:
        os.replace(staging_path, target_path)

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    store.register_artifact(target_path)
    b64_str = base64.b64encode(raw_bytes).decode("ascii")

    dataset_tag = ""
    try:
        dataset_tag = str(_call(pg, "getString", "data") or "")
    except Exception:
        pass

    solnum_val = ""
    try:
        solnum_val = str(_call(pg, "getString", "solnum") or "")
    except Exception:
        pass

    return {
        "plot_group": pg_tag,
        "file_path": str(target_path),
        "format": fmt,
        "byte_size": len(raw_bytes),
        "sha256": sha256,
        "width": actual_w,
        "height": actual_h,
        "dataset": dataset_tag,
        "solnum": solnum_val,
        "image_base64": b64_str,
        "image_mime_type": f"image/{fmt}",
        "artifact_ref": str(target_path),
    }


def plot_geometry_render(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Render a geometry or mesh sequence to an image artifact."""
    geometry_spec = arguments.get("geometry")
    if not geometry_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.geometry_render requires 'geometry'")
    geom_tag = _resolve_tag(geometry_spec)
    mode = str(arguments.get("mode") or "geometry").lower()

    options = arguments.get("options") or {}
    width = int(options.get("width") or 800)
    height = int(options.get("height") or 600)
    fmt = str(options.get("format") or "png").lower()
    if fmt not in ("png", "jpg", "jpeg", "bmp", "gif"):
        raise ExecutionContractError("INVALID_REQUEST", f"Unsupported image format: {fmt}")
    dest = options.get("destination")
    allow_overwrite = bool(options.get("allow_overwrite", True))

    root = trusted_project_root(worker)
    store = ArtifactStore(project_root=root)

    if dest:
        target_path = store.resolve_safe_path(dest, allow_overwrite=allow_overwrite)
        if target_path.exists() and not allow_overwrite:
            raise ExecutionContractError("ACCESS_VIOLATION", f"Destination file exists and allow_overwrite is False: {dest}")
    else:
        target_path = store.resolve_safe_path(
            f"g2_artifacts/plots/{geom_tag}_{mode}_{uuid.uuid4().hex[:8]}.{fmt}",
            allow_overwrite=True,
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = target_path.with_name(f".staging_{uuid.uuid4().hex[:8]}_{target_path.name}")

    model = bound_model(worker, model_tag)
    try:
        if mode == "mesh":
            mesh_seq = _call(model, "mesh", geom_tag)
            img_obj = _call(mesh_seq, "image")
        else:
            geom_seq = _call(model, "geom", geom_tag)
            img_obj = _call(geom_seq, "image")
    except Exception as exc:
        raise node_not_found(f"{mode} {geom_tag!r} could not be resolved for image rendering: {exc}") from exc

    try:
        _call(img_obj, "set", "target", "file")
    except Exception:
        pass
    try:
        _call(img_obj, "set", "imagetype", fmt)
    except Exception:
        pass
    try:
        _call(img_obj, "set", f"{fmt}filename", str(staging_path))
    except Exception:
        pass
    try:
        _call(img_obj, "set", "size", "manualweb")
        _call(img_obj, "set", "width", width)
        _call(img_obj, "set", "height", height)
        _call(img_obj, "set", "antialias", "on")
    except Exception:
        pass

    try:
        _call(img_obj, "export")
    except Exception as exc:
        raise ExecutionContractError("RENDER_FAILED", f"Geometry image export failed: {exc}") from exc

    if not staging_path.is_file() and target_path.is_file():
        staging_path = target_path

    if not staging_path.is_file():
        raise ExecutionContractError("RENDER_FAILED", f"COMSOL export failed to create geometry image at {staging_path}")

    raw_bytes = staging_path.read_bytes()
    if len(raw_bytes) == 0:
        raise ExecutionContractError("RENDER_FAILED", f"Rendered image is empty (0 bytes): {staging_path}")
    if fmt == "png":
        if len(raw_bytes) < 8 or raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise ExecutionContractError("IMAGE_DECODE_ERROR", "Rendered file does not have valid PNG header")
        import struct
        actual_w, actual_h = struct.unpack(">II", raw_bytes[16:24])
    else:
        actual_w, actual_h = width, height

    # Atomic publish
    if staging_path != target_path:
        os.replace(staging_path, target_path)

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    store.register_artifact(target_path)
    b64_str = base64.b64encode(raw_bytes).decode("ascii")

    return {
        "geometry": geom_tag,
        "mode": mode,
        "file_path": str(target_path),
        "format": fmt,
        "byte_size": len(raw_bytes),
        "sha256": sha256,
        "width": actual_w,
        "height": actual_h,
        "image_base64": b64_str,
        "image_mime_type": f"image/{fmt}",
        "artifact_ref": str(target_path),
    }


def plot_view_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Inspect or manage model views, axes, and camera settings."""
    action_val = arguments.get("action")
    if not action_val:
        if "properties" in arguments or "definition" in arguments:
            action = "update"
        elif "tag" in arguments or "path" in arguments:
            action = "inspect"
        else:
            action = "list"
    else:
        action = require_string(action_val, "action").lower()

    path = arguments.get("path") or arguments.get("tag")
    view_tag = _resolve_tag(path) if path else "view1"
    definition = arguments.get("definition") or arguments.get("properties") or {}

    model = bound_model(worker, model_tag)
    views = _call(model, "view")

    if action == "list":
        tags = list(_call(views, "tags") or [])
        view_items: list[dict[str, Any]] = []
        for v_tag in tags:
            try:
                v = _call(views, "get", v_tag)
                props = _read_properties(v)
                view_items.append({"tag": v_tag, "properties": props})
            except Exception:
                view_items.append({"tag": v_tag})
        return {"views": view_items, "total_count": len(view_items)}

    if action == "inspect":
        try:
            v = _call(views, "get", view_tag)
        except Exception as exc:
            raise node_not_found(f"view {view_tag!r} not found: {exc}") from exc
        props = _read_properties(v)
        return {"view": view_tag, "view_tag": view_tag, "properties": props}

    if action == "create":
        dim = definition.get("dimension", 3)
        v = _call(views, "create", view_tag, dim)
        if "properties" in definition:
            _apply_properties(v, definition["properties"])
        elif isinstance(definition, Mapping):
            _apply_properties(v, definition)
        return {"view": view_tag, "view_tag": view_tag, "created": True, "definition": definition}

    if action == "update":
        try:
            v = _call(views, "get", view_tag)
        except Exception as exc:
            raise node_not_found(f"view {view_tag!r} not found: {exc}") from exc
        props = definition.get("properties") or definition
        if isinstance(props, Mapping) and props:
            _apply_properties(v, props)
        return {"view": view_tag, "view_tag": view_tag, "updated": True, "properties": props}

    if action == "remove":
        try:
            _call(views, "remove", view_tag)
        except Exception as exc:
            raise node_not_found(f"view {view_tag!r} could not be removed: {exc}") from exc
        return {"view": view_tag, "view_tag": view_tag, "removed": True}

    raise ExecutionContractError("API_UNSUPPORTED", f"unsupported view action {action!r}")


# ---------------------------------------------------------------------------
# export.* Operations
# ---------------------------------------------------------------------------

def export_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """List export features under result/export."""
    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")
    tags = list(_call(export_list_node, "tags") or [])
    items: list[dict[str, Any]] = []

    for tag in tags:
        try:
            exp = _call(export_list_node, "get", tag)
            t_id = ""
            try:
                t_id = str(_call(exp, "getType") or "")
            except Exception:
                pass
            props = _read_properties(exp)
            items.append({
                "tag": tag,
                "type_id": t_id,
                "properties": props,
            })
        except Exception:
            continue

    return {
        "exports": items,
        "total_count": len(items),
    }


def export_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Create an export feature (Image, Plot, Data, etc.)."""
    tag = require_string(arguments.get("tag") or arguments.get("path"), "tag")
    type_id = require_string(arguments.get("type_id"), "type_id")
    definition = arguments.get("definition") or arguments.get("properties") or {}

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")

    props = definition.get("properties") if isinstance(definition, Mapping) and "properties" in definition else definition
    source = props.get("plotgroup") or props.get("sourceobject") if isinstance(props, Mapping) else None

    if source:
        try:
            exp = _call(export_list_node, "create", tag, str(source), type_id)
        except Exception:
            exp = _call(export_list_node, "create", tag, type_id)
    else:
        exp = _call(export_list_node, "create", tag, type_id)

    if isinstance(props, Mapping) and props:
        _apply_properties(exp, props)

    return {
        "tag": tag,
        "type_id": type_id,
        "definition": dict(definition),
    }


def export_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Update properties on an existing export feature."""
    path_spec = arguments.get("path") or arguments.get("tag")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "export.update requires 'path' or 'tag'")
    tag = _resolve_tag(path_spec)
    definition = arguments.get("definition") or arguments.get("properties") or {}

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")

    try:
        exp = _call(export_list_node, "get", tag)
    except Exception as exc:
        raise node_not_found(f"export node {tag!r} not found: {exc}") from exc

    props = definition.get("properties") if isinstance(definition, Mapping) and "properties" in definition else definition
    if isinstance(props, Mapping) and props:
        _apply_properties(exp, props)

    return {
        "path": path_spec,
        "tag": tag,
        "definition": dict(definition) if isinstance(definition, Mapping) else {"properties": definition},
        "updated": True,
    }


def export_run(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run an export feature and check/register the produced file artifact."""
    path_spec = arguments.get("path") or arguments.get("tag")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "export.run requires 'path' or 'tag'")
    tag = _resolve_tag(path_spec)

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")

    try:
        exp = _call(export_list_node, "get", tag)
    except Exception as exc:
        raise node_not_found(f"export node {tag!r} not found: {exc}") from exc

    # Attempt to read configured destination
    filename = None
    try:
        filename = _call(exp, "getString", "filename")
    except Exception:
        pass
    if not filename:
        try:
            filename = _call(exp, "getString", "pngfilename")
        except Exception:
            pass

    root = trusted_project_root(worker)
    store = ArtifactStore(project_root=root)

    if filename:
        target_path = store.resolve_safe_path(filename, allow_overwrite=True)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _call(exp, "set", "filename", str(target_path))
        except Exception:
            pass
        try:
            _call(exp, "set", "pngfilename", str(target_path))
        except Exception:
            pass
    else:
        target_path = store.resolve_safe_path(
            f"g2_artifacts/exports/{tag}_{uuid.uuid4().hex[:8]}.out",
            allow_overwrite=True,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _call(exp, "set", "filename", str(target_path))
        except Exception:
            pass
        try:
            _call(exp, "set", "pngfilename", str(target_path))
        except Exception:
            pass

    _call(exp, "run")

    if not target_path.is_file():
        raise ExecutionContractError("EXPORT_FAILED", f"Export run failed to produce output file at {target_path}")

    data_bytes = target_path.read_bytes()
    sha256 = hashlib.sha256(data_bytes).hexdigest()
    store.register_artifact(target_path)

    result_payload: dict[str, Any] = {
        "path": path_spec,
        "tag": tag,
        "ran": True,
        "file_path": str(target_path),
        "byte_size": len(data_bytes),
        "sha256": sha256,
        "artifact_ref": str(target_path),
    }

    # If image, return base64 payload
    suffix = target_path.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"):
        fmt = suffix.lstrip(".")
        result_payload["image_base64"] = base64.b64encode(data_bytes).decode("ascii")
        result_payload["image_mime_type"] = f"image/{fmt}"

    return result_payload


def export_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove an export feature without deleting the exported file."""
    path_spec = arguments.get("path") or arguments.get("tag")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "export.remove requires 'path' or 'tag'")
    tag = _resolve_tag(path_spec)

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")

    try:
        _call(export_list_node, "remove", tag)
    except Exception as exc:
        raise node_not_found(f"failed to remove export node {tag!r}: {exc}") from exc

    return {
        "path": path_spec,
        "tag": tag,
        "removed": True,
    }


# ---------------------------------------------------------------------------
# Published Operations Mapping
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Any] = {
    "plot.list": plot_list,
    "plot.group_create": plot_group_create,
    "plot.feature_create": plot_feature_create,
    "plot.update": plot_update,
    "plot.remove": plot_remove,
    "plot.render": plot_render,
    "plot.geometry_render": plot_geometry_render,
    "plot.view_manage": plot_view_manage,
    "export.list": export_list,
    "export.create": export_create,
    "export.update": export_update,
    "export.run": export_run,
    "export.remove": export_remove,
}
