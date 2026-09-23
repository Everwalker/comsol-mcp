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


def _resolve_plot_path(path: Any) -> tuple[str, ...]:
    if isinstance(path, str):
        cleaned = path.strip().strip("/")
        parts = [p for p in cleaned.split("/") if p]
        if parts and parts[0] == "result":
            parts = parts[1:]
        if not parts:
            raise ExecutionContractError("INVALID_REQUEST", "plot path cannot be empty")
        if len(parts) == 1:
            return (parts[0], None)
        return tuple(parts)
    if isinstance(path, Mapping) and "segments" in path:
        segs = [s for s in path["segments"] if isinstance(s, Mapping) and s.get("accessor") != "result"]
        tags = [str(s.get("tag", "")) for s in segs if s.get("tag")]
        if not tags:
            tag = _resolve_tag(path)
            return (tag, None)
        if len(tags) == 1:
            return (tags[0], None)
        return tuple(tags)
    tag = _resolve_tag(path)
    return (tag, None)


def _apply_properties(node: Any, props: Mapping[str, Any]) -> None:
    for key, value in props.items():
        try:
            if isinstance(value, bool):
                _call(node, "set", key, value)
            elif isinstance(value, int):
                _call(node, "set", key, value)
            elif isinstance(value, float):
                _call(node, "set", key, value)
            elif isinstance(value, str):
                _call(node, "set", key, value)
            elif isinstance(value, (list, tuple)):
                if value and all(isinstance(r, (list, tuple)) for r in value):
                    # 2D matrix: pass as list of lists, preserving numeric/string types
                    _call(node, "set", key, [list(r) for r in value])
                else:
                    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value):
                        _call(node, "set", key, list(value))
                    elif all(isinstance(x, str) for x in value):
                        _call(node, "set", key, list(value))
                    elif all(isinstance(x, bool) for x in value):
                        _call(node, "set", key, list(value))
                    else:
                        str_list = [str(x) for x in value]
                        _call(node, "set", key, str_list)
            elif isinstance(value, Mapping):
                for subkey, subval in value.items():
                    _call(node, "setEntry", key, str(subkey), str(subval))
            else:
                _call(node, "set", key, value)
        except Exception as exc:
            raise ExecutionContractError(
                "PROPERTY_SET_FAILED",
                f"Failed to set property {key!r}={value!r} on node: {exc}",
            ) from exc


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
        except Exception as exc:
            raise ExecutionContractError(
                "SCIENTIFIC_BINDING_FAILED",
                f"Failed to bind dataset {dataset_tag!r} to plot group {tag!r}: {exc}",
            ) from exc

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

    path_tuple = _resolve_plot_path(path_spec)
    pg_tag = path_tuple[0]
    feat_tag = path_tuple[1] if len(path_tuple) > 1 and path_tuple[1] is not None else None
    subfeat_tag = path_tuple[2] if len(path_tuple) > 2 else None
    if len(path_tuple) > 3:
        raise ExecutionContractError("UNSUPPORTED_PATH_DEPTH", f"Plot path depth {len(path_tuple)} exceeds supported depth 3: {path_spec}")

    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    try:
        pg = _call(results, "get", pg_tag)
    except Exception as exc:
        raise node_not_found(f"plot group {pg_tag!r} not found: {exc}") from exc

    if subfeat_tag:
        feat_list = _call(pg, "feature")
        try:
            feat_node = _call(feat_list, "get", feat_tag)
            subfeat_list = _call(feat_node, "feature")
            target_node = _call(subfeat_list, "get", subfeat_tag)
        except Exception as exc:
            raise node_not_found(f"plot subfeature {subfeat_tag!r} under {feat_tag!r} in {pg_tag!r} not found: {exc}") from exc
    elif feat_tag:
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
        "subfeature": subfeat_tag,
        "properties": dict(properties),
        "updated": True,
    }


def plot_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a plot group or a child plot feature."""
    path_spec = arguments.get("path")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.remove requires 'path'")

    path_tuple = _resolve_plot_path(path_spec)
    pg_tag = path_tuple[0]
    feat_tag = path_tuple[1] if len(path_tuple) > 1 and path_tuple[1] is not None else None
    subfeat_tag = path_tuple[2] if len(path_tuple) > 2 else None
    if len(path_tuple) > 3:
        raise ExecutionContractError("UNSUPPORTED_PATH_DEPTH", f"Plot path depth {len(path_tuple)} exceeds supported depth 3: {path_spec}")

    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    if subfeat_tag:
        try:
            pg = _call(results, "get", pg_tag)
            feat_node = _call(_call(pg, "feature"), "get", feat_tag)
            _call(_call(feat_node, "feature"), "remove", subfeat_tag)
        except Exception as exc:
            raise node_not_found(f"failed to remove plot subfeature {subfeat_tag!r}: {exc}") from exc
    elif feat_tag:
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
        "subfeature": subfeat_tag,
        "removed": True,
    }


def plot_render(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Render a plot group to an image artifact and return ImageContent and metadata."""
    path_spec = arguments.get("path")
    if not path_spec:
        raise ExecutionContractError("INVALID_REQUEST", "plot.render requires 'path'")
    path_tuple = _resolve_plot_path(path_spec)
    pg_tag = path_tuple[0]

    options = arguments.get("options") or {}
    width = int(options.get("width") or 800)
    height = int(options.get("height") or 600)
    fmt = str(options.get("format") or "png").lower()
    if fmt != "png":
        raise ExecutionContractError("INVALID_REQUEST", f"Unsupported image format {fmt!r}: only 'png' is supported")
    dest = options.get("destination")
    raw_ow = options.get("allow_overwrite", False)
    if isinstance(raw_ow, str):
        allow_overwrite = raw_ow.strip().lower() in ("true", "1", "yes")
    else:
        allow_overwrite = bool(raw_ow)

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
    if staging_path.exists():
        try: staging_path.unlink()
        except Exception: pass

    model = bound_model(worker, model_tag)
    results = _call(model, "result")

    try:
        pg = _call(results, "get", pg_tag)
    except Exception as exc:
        raise node_not_found(f"plot group {pg_tag!r} not found: {exc}") from exc

    # Apply solution / time / parameter options fail-closed
    for opt_key in ("data", "dataset", "solution", "solnum", "t", "looplevel", "innerinput", "outerinput"):
        if opt_key in options:
            prop_name = "data" if opt_key in ("data", "dataset", "solution") else opt_key
            try:
                _call(pg, "set", prop_name, str(options[opt_key]))
            except Exception as exc:
                raise ExecutionContractError(
                    "SCIENTIFIC_BINDING_FAILED",
                    f"Failed to set scientific option {opt_key}={options[opt_key]!r} on plot group {pg_tag!r}: {exc}",
                ) from exc

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
            except Exception as exc:
                raise ExecutionContractError(
                    "SCIENTIFIC_BINDING_FAILED",
                    f"Failed to bind plot group {pg_tag!r} to export node {exp_tag!r}: {exc}",
                ) from exc

    staging_bound = False
    bound_prop = None
    for prop in ("pngfilename", "filename"):
        try:
            _call(exp, "set", prop, str(staging_path))
            readback = str(_call(exp, "getString", prop) or "")
            if readback == str(staging_path):
                staging_bound = True
                bound_prop = prop
                break
        except Exception:
            pass

    if not staging_bound:
        raise ExecutionContractError(
            "RENDER_FAILED",
            f"Failed to bind and verify staging destination path on export feature {exp_tag!r}",
        )

    try:
        try:
            _call(exp, "set", "size", "manualweb")
        except Exception:
            try:
                _call(exp, "set", "size", "manual")
            except Exception:
                pass
        try:
            _call(exp, "set", "unit", "px")
            _call(exp, "set", "width", width)
            _call(exp, "set", "height", height)
        except Exception:
            pass
    except Exception:
        pass

    cleanup_failed = False
    cleanup_error = None
    try:
        _call(exp, "run")
    finally:
        try:
            _call(export_list, "remove", exp_tag)
        except Exception as exc:
            cleanup_failed = True
            cleanup_error = str(exc)

    if not staging_path.is_file():
        raise ExecutionContractError("RENDER_FAILED", f"COMSOL export failed to create image at {staging_path}")

    raw_bytes = staging_path.read_bytes()
    if len(raw_bytes) == 0:
        try: staging_path.unlink(missing_ok=True)
        except Exception: pass
        raise ExecutionContractError("RENDER_FAILED", f"Rendered image is empty (0 bytes): {staging_path}")
    if len(raw_bytes) < 8 or raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        try: staging_path.unlink(missing_ok=True)
        except Exception: pass
        raise ExecutionContractError("IMAGE_DECODE_ERROR", "Rendered file does not have valid PNG header")
    import struct
    actual_w, actual_h = struct.unpack(">II", raw_bytes[16:24])

    # Atomic publish (never fallback to existing target)
    if staging_path != target_path:
        if allow_overwrite:
            os.replace(staging_path, target_path)
        else:
            try:
                os.link(staging_path, target_path)
                staging_path.unlink()
            except FileExistsError as exc:
                try: staging_path.unlink(missing_ok=True)
                except Exception: pass
                raise ExecutionContractError(
                    "DESTINATION_EXISTS",
                    f"Destination appeared before atomic no-clobber publish: {target_path}",
                ) from exc
            except OSError:
                if target_path.exists():
                    try: staging_path.unlink(missing_ok=True)
                    except Exception: pass
                    raise ExecutionContractError(
                        "DESTINATION_EXISTS",
                        f"Destination already exists: {target_path}",
                    )
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

    time_val = ""
    try:
        time_val = str(_call(pg, "getString", "t") or "")
    except Exception:
        pass

    looplevel_val = ""
    try:
        looplevel_val = str(_call(pg, "getString", "looplevel") or "")
    except Exception:
        pass

    solution_tag = ""
    if dataset_tag:
        try:
            dset = _call(results, "dataset", dataset_tag)
            s_prop = str(_call(dset, "getString", "solution") or "")
            if s_prop:
                solution_tag = s_prop
            else:
                visited = {dataset_tag}
                curr = dset
                while True:
                    upstream = str(_call(curr, "getString", "data") or "")
                    if not upstream or upstream in visited:
                        break
                    visited.add(upstream)
                    try:
                        curr = _call(results, "dataset", upstream)
                        s = str(_call(curr, "getString", "solution") or "")
                        if s:
                            solution_tag = s
                            break
                    except Exception:
                        break
        except Exception:
            pass

        if not solution_tag:
            try:
                from ._dataset_binding import resolve_dataset_binding
                binding = resolve_dataset_binding(model, dataset_tag)
                if isinstance(binding, Mapping) and binding.get("solution"):
                    solution_tag = str(binding["solution"])
            except Exception:
                pass

    expressions: list[str] = []
    units: list[str] = []
    child_features: list[dict[str, Any]] = []
    try:
        feat_container = _call(pg, "feature")
        feat_tags = list(_call(feat_container, "tags") or [])
        for ftag in feat_tags:
            try:
                feat = _call(feat_container, "get", ftag)
                f_type = str(_call(feat, "getType") or "")
                f_expr = ""
                try:
                    f_expr = str(_call(feat, "getString", "expr") or "")
                except Exception:
                    pass
                f_unit = ""
                try:
                    f_unit = str(_call(feat, "getString", "unit") or "")
                except Exception:
                    pass
                if f_expr:
                    expressions.append(f_expr)
                if f_unit:
                    units.append(f_unit)
                child_features.append({
                    "tag": ftag,
                    "type_id": f_type,
                    "expression": f_expr,
                    "unit": f_unit,
                })
            except Exception:
                pass
    except Exception:
        pass

    primary_expr = expressions[0] if expressions else ""
    primary_unit = units[0] if units else ""

    provenance = {
        "model_tag": model_tag,
        "plot_group": pg_tag,
        "dataset": dataset_tag,
        "solution": solution_tag or None,
        "solnum": solnum_val,
        "time": time_val,
        "looplevel": looplevel_val,
        "expression": primary_expr,
        "unit": primary_unit,
        "expressions": expressions,
        "units": units,
        "features": child_features,
        "options": dict(options),
    }

    result = {
        "plot_group": pg_tag,
        "file_path": str(target_path),
        "format": fmt,
        "byte_size": len(raw_bytes),
        "sha256": sha256,
        "width": actual_w,
        "height": actual_h,
        "dataset": dataset_tag,
        "solution": solution_tag or None,
        "solnum": solnum_val,
        "time": time_val,
        "expression": primary_expr,
        "unit": primary_unit,
        "provenance": provenance,
        "image_base64": b64_str,
        "image_mime_type": f"image/{fmt}",
        "artifact_ref": str(target_path),
    }
    if cleanup_failed:
        result["cleanup_failed"] = True
        result["cleanup"] = {"cleanup_failed": True, "error": cleanup_error}
        result["execution_state_unknown"] = True
    return result


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
    if fmt != "png":
        raise ExecutionContractError("INVALID_REQUEST", f"Unsupported image format {fmt!r}: only 'png' is supported")
    dest = options.get("destination")
    raw_ow = options.get("allow_overwrite", False)
    if isinstance(raw_ow, str):
        allow_overwrite = raw_ow.strip().lower() in ("true", "1", "yes")
    else:
        allow_overwrite = bool(raw_ow)

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
    if staging_path.exists():
        try: staging_path.unlink()
        except Exception: pass

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

    if not staging_path.is_file():
        raise ExecutionContractError("RENDER_FAILED", f"COMSOL export failed to create geometry image at {staging_path}")

    raw_bytes = staging_path.read_bytes()
    if len(raw_bytes) == 0:
        try: staging_path.unlink(missing_ok=True)
        except Exception: pass
        raise ExecutionContractError("RENDER_FAILED", f"Rendered image is empty (0 bytes): {staging_path}")
    if len(raw_bytes) < 8 or raw_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        try: staging_path.unlink(missing_ok=True)
        except Exception: pass
        raise ExecutionContractError("IMAGE_DECODE_ERROR", "Rendered file does not have valid PNG header")
    import struct
    actual_w, actual_h = struct.unpack(">II", raw_bytes[16:24])

    # Atomic publish (never fallback to existing target)
    if staging_path != target_path:
        if allow_overwrite:
            os.replace(staging_path, target_path)
        else:
            try:
                os.link(staging_path, target_path)
                staging_path.unlink()
            except FileExistsError as exc:
                try: staging_path.unlink(missing_ok=True)
                except Exception: pass
                raise ExecutionContractError(
                    "DESTINATION_EXISTS",
                    f"Destination appeared before atomic no-clobber publish: {target_path}",
                ) from exc
            except OSError:
                if target_path.exists():
                    try: staging_path.unlink(missing_ok=True)
                    except Exception: pass
                    raise ExecutionContractError(
                        "DESTINATION_EXISTS",
                        f"Destination already exists: {target_path}",
                    )
                os.replace(staging_path, target_path)

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    store.register_artifact(target_path)
    b64_str = base64.b64encode(raw_bytes).decode("ascii")

    provenance = {
        "model_tag": model_tag,
        "geometry": geom_tag,
        "mode": mode,
        "width": actual_w,
        "height": actual_h,
        "format": fmt,
        "sha256": sha256,
        "options": dict(options),
    }

    return {
        "geometry": geom_tag,
        "mode": mode,
        "file_path": str(target_path),
        "format": fmt,
        "byte_size": len(raw_bytes),
        "sha256": sha256,
        "width": actual_w,
        "height": actual_h,
        "provenance": provenance,
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

    raw_ow = arguments.get("allow_overwrite", False)
    if isinstance(raw_ow, str):
        allow_overwrite = raw_ow.strip().lower() in ("true", "1", "yes")
    else:
        allow_overwrite = bool(raw_ow)

    model = bound_model(worker, model_tag)
    results = _call(model, "result")
    export_list_node = _call(results, "export")

    try:
        exp = _call(export_list_node, "get", tag)
    except Exception as exc:
        raise node_not_found(f"export node {tag!r} not found: {exc}") from exc

    # Attempt to read configured destination
    filename = None
    orig_prop_name = None
    for prop in ("filename", "pngfilename", "imagefilename", "datafilename", "txtfilename"):
        try:
            val = _call(exp, "getString", prop)
            if val:
                filename = val
                orig_prop_name = prop
                break
        except Exception:
            pass

    root = trusted_project_root(worker)
    store = ArtifactStore(project_root=root)

    if filename:
        target_path = store.resolve_safe_path(filename, allow_overwrite=True)
    else:
        target_path = store.resolve_safe_path(
            f"g2_artifacts/exports/{tag}_{uuid.uuid4().hex[:8]}.out",
            allow_overwrite=True,
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = target_path.with_name(f".staging_{uuid.uuid4().hex[:8]}_{target_path.name}")
    if staging_path.exists():
        try: staging_path.unlink()
        except Exception: pass

    # Save original property value for restoration in finally
    orig_val = None
    staging_bound = False
    bound_prop = None
    props_to_try = (orig_prop_name,) if orig_prop_name else ("filename", "pngfilename", "imagefilename", "datafilename", "txtfilename")
    for prop in props_to_try:
        if not prop:
            continue
        try:
            current_val = _call(exp, "getString", prop)
            _call(exp, "set", prop, str(staging_path))
            readback = str(_call(exp, "getString", prop) or "")
            if readback == str(staging_path):
                staging_bound = True
                bound_prop = prop
                orig_val = current_val
                break
        except Exception:
            pass

    if not staging_bound:
        raise ExecutionContractError(
            "EXPORT_BINDING_FAILED",
            f"Failed to bind and verify staging destination path on export feature {tag!r}",
        )

    restore_failed = False
    restore_error = None
    try:
        _call(exp, "run")
    finally:
        if bound_prop and orig_val is not None:
            try:
                _call(exp, "set", bound_prop, orig_val)
                rb = str(_call(exp, "getString", bound_prop) or "")
                if rb != str(orig_val):
                    restore_failed = True
                    restore_error = f"Property readback {rb!r} does not match original {orig_val!r}"
            except Exception as exc:
                restore_failed = True
                restore_error = str(exc)

    if not staging_path.is_file() or staging_path.stat().st_size == 0:
        if staging_path.exists():
            try: staging_path.unlink()
            except Exception: pass
        raise ExecutionContractError(
            "EXPORT_FAILED",
            f"Export run failed to produce output file at staging path: {staging_path}",
        )

    # Stream hash and byte count
    hasher = hashlib.sha256()
    file_size = 0
    with staging_path.open("rb") as f:
        while chunk := f.read(64 * 1024):
            hasher.update(chunk)
            file_size += len(chunk)
    sha256 = hasher.hexdigest()

    # Atomic publish
    if staging_path != target_path:
        if allow_overwrite:
            os.replace(staging_path, target_path)
        else:
            try:
                os.link(staging_path, target_path)
                staging_path.unlink()
            except FileExistsError as exc:
                try: staging_path.unlink(missing_ok=True)
                except Exception: pass
                raise ExecutionContractError(
                    "DESTINATION_EXISTS",
                    f"Destination appeared before atomic no-clobber publish: {target_path}",
                ) from exc
            except OSError:
                if target_path.exists():
                    try: staging_path.unlink(missing_ok=True)
                    except Exception: pass
                    raise ExecutionContractError(
                        "DESTINATION_EXISTS",
                        f"Destination already exists: {target_path}",
                    )
                os.replace(staging_path, target_path)

    store.register_artifact(target_path)

    result_payload: dict[str, Any] = {
        "path": path_spec,
        "tag": tag,
        "ran": True,
        "file_path": str(target_path),
        "byte_size": file_size,
        "sha256": sha256,
        "artifact_ref": str(target_path),
    }

    suffix = target_path.suffix.lower()
    if suffix == ".png":
        raw_image_bytes = target_path.read_bytes()
        result_payload["image_base64"] = base64.b64encode(raw_image_bytes).decode("ascii")
        result_payload["image_mime_type"] = "image/png"

    if restore_failed:
        result_payload["property_restore_failed"] = True
        result_payload["property_restore_error"] = restore_error
        result_payload["execution_state_unknown"] = True

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
