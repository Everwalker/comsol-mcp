#!/usr/bin/env python3
"""Pure model operation helpers: parameters, expressions, metrics, tree, geometry."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from typing import Any


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------
def _parameter_rows(model: Any) -> list[dict[str, str]]:
    names = list(model.java.param().varnames())
    rows = []
    for name in names:
        key = str(name)
        rows.append(
            {
                "name": key,
                "expression": str(model.java.param().get(key)),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Expression evaluation helpers
# ---------------------------------------------------------------------------
def _coerce_eval_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _coerce_eval_value(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_eval_value(item) for item in value]
    # JPype Java arrays are iterable but are neither Python lists nor tuples.
    # Keep strings/scalars above so an arbitrary Java object is not mistaken
    # for a collection merely because it has an iterator.
    try:
        return [_coerce_eval_value(item) for item in value]
    except (TypeError, AttributeError):
        pass
    return str(value)


def _last_scalar(value: Any) -> Any:
    current = value
    while isinstance(current, list) and current:
        current = current[-1]
    return current


class NumericalCleanupError(RuntimeError):
    """Evaluation must stop after incomplete temporary-node cleanup."""


def _raise_with_cleanup_error(primary: BaseException | None, cleanup: BaseException | None) -> None:
    """Never turn a failed cleanup into a successful evaluation."""
    if primary is not None and cleanup is not None:
        raise NumericalCleanupError(f"{primary}; additionally, temporary numerical cleanup failed: {cleanup}") from primary
    if primary is not None:
        raise primary
    if cleanup is not None:
        raise NumericalCleanupError(f"Temporary numerical cleanup failed: {cleanup}") from cleanup


@contextmanager
def _temporary_numerical_feature(model: Any, feature_type: str):
    """Create and remove exactly one MCP-owned numerical feature.

    Result numerical collections can contain user Derived Values, tables, and
    plots.  Tags are deliberately random, and cleanup removes only this tag.
    """
    numerical = model.java.result().numerical()
    existing = {str(item) for item in list(numerical.tags())}
    tag = ""
    for _ in range(8):
        candidate = f"mcp_eval_{uuid.uuid4().hex}"
        if candidate not in existing:
            tag = candidate
            break
    if not tag:
        raise RuntimeError("Could not allocate an unused MCP temporary numerical tag.")
    primary: BaseException | None = None
    created = False
    try:
        numerical.create(tag, feature_type)
        created = True
        yield model.java.result().numerical(tag)
    except BaseException as exc:
        primary = exc
    cleanup: BaseException | None = None
    if created:
        try:
            numerical.remove(tag)
        except BaseException as exc:
            cleanup = exc
    _raise_with_cleanup_error(primary, cleanup)


def _feature_value(feature: Any, *, data_fallback: bool = False) -> Any:
    """Return a non-lossy real/imag structure when the API identifies complex data."""
    getter = getattr(feature, "getData", None) if data_fallback else getattr(feature, "getReal", None)
    if not callable(getter):
        raise RuntimeError("Numerical feature does not expose the requested result accessor.")
    real = _coerce_eval_value(getter())
    complex_flag = False
    checker = getattr(feature, "isComplex", None)
    if callable(checker):
        complex_flag = bool(checker())
    if complex_flag:
        imag_getter = getattr(feature, "getImagData" if data_fallback else "getImag", None)
        if not callable(imag_getter):
            raise RuntimeError("Complex numerical result cannot be returned safely: getImag is unavailable (W17 typed complex support pending).")
        try:
            return {"real": real, "imag": _coerce_eval_value(imag_getter())}
        except Exception as exc:
            raise RuntimeError(f"Complex numerical result cannot be returned safely: getImag failed: {exc}") from exc
    return real


def _select_inner(values: Any, time_point: str) -> Any:
    """Select the solution axis, retaining expression and spatial dimensions."""
    if isinstance(values, dict):
        return {key: _select_inner(value, time_point) for key,value in values.items()}
    if time_point in ("", "all"):
        return values
    if time_point in ("first", "last"):
        index = 0 if time_point == "first" else -1
    elif time_point.isdigit() and int(time_point)>0:
        index = int(time_point)-1
    else:
        raise ValueError("time_point must be first, last, all, or a positive solution index.")
    return [[row[index]] for row in values]


def _configure_eval(feature: Any, expression: str, time_point: str) -> None:
    feature.set("expr", [expression])
    # Eval lacks looplevelinput; stationary datasets have no loop level.
    # Fetch the API's complete shape and select the solution axis explicitly.


def _evaluate_expression_safely(model: Any, expression: str, time_point: str = "last") -> Any:
    """Evaluate via owned result nodes, never MPh ``model.evaluate``.

    MPh 1.4 can leak result nodes on both successful and failing evaluation.
    The returned Java result is deliberately kept intact (rather than sliced)
    so this safety fix does not silently discard time/result dimensions.
    """
    try:
        with _temporary_numerical_feature(model, "EvalGlobal") as feature:
            _configure_eval(feature, expression, time_point)
            feature.run()
            return _select_inner(_feature_value(feature), time_point)
    except NumericalCleanupError:
        raise
    except Exception as global_exc:
        # EvalGlobal is insufficient for spatial fields. Eval/getData is an
        # owned fallback and its independent failure is retained for diagnosis.
        try:
            with _temporary_numerical_feature(model, "Eval") as feature:
                _configure_eval(feature, expression, time_point)
                feature.run()
                return _select_inner(_feature_value(feature, data_fallback=True), time_point)
        except Exception as eval_exc:
            raise RuntimeError(f"EvalGlobal failed: {global_exc}; Eval/getData fallback failed: {eval_exc}") from eval_exc


def _evaluate_named_expressions(model: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each expression entry must be an object.")
        name = str(item.get("name", "")).strip()
        expression = str(item.get("expression", "")).strip()
        if not name:
            raise ValueError("Expression name is required.")
        if not expression:
            raise ValueError(f'Expression is required for "{name}".')
        row: dict[str, Any] = {"name": name, "expression": expression}
        try:
            raw = _evaluate_expression_safely(model, expression)
            value = _coerce_eval_value(raw)
            row["value"] = value
            row["last_value"] = _coerce_eval_value(_last_scalar(value))
            row["ok"] = True
        except Exception as exc:
            row["ok"] = False
            row["error"] = str(exc)
        results.append(row)
    return results


def _numeric_result(name: str, expression: str, value: Any = None, *, ok: bool = True, error: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "expression": expression, "ok": ok}
    if ok:
        row["value"] = value
        row["last_value"] = _coerce_eval_value(_last_scalar(value))
    else:
        row["error"] = error or "NA"
    return row


# ---------------------------------------------------------------------------
# Core metrics helpers
# ---------------------------------------------------------------------------
def _find_initialized_solution_tag(model: Any) -> str:
    for tag in list(model.java.sol().tags()):
        tag_str = str(tag)
        try:
            if model.java.sol(tag_str).isInitialized():
                return tag_str
        except Exception:
            continue
    return ""


def _read_last_time_day(model: Any, sol_tag: str) -> float | None:
    if not sol_tag:
        return None
    try:
        values = model.java.sol(sol_tag).getPVals()
        if values is not None and len(values) > 0:
            last_value = float(values[-1])
            return last_value / 86400.0 if abs(last_value) > 1e3 else last_value
    except Exception:
        return None
    return None


def _eval_global_last(model: Any, expression: str) -> float | None:
    try:
        return float(_last_scalar(_evaluate_expression_safely(model, expression, "last")))
    except Exception:
        return None


def _eval_domain_average_last(model: Any, expression: str, domains: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, "IntSurface") as feature:
            feature.selection().geom("geom1", 2)
            feature.selection().set(domains)
            feature.set("intvolume", True)
            feature.set("expr", [expression, "1"])
            feature.set("looplevelinput", ["last"])
            feature.run()
            values = feature.getReal()
            return float(values[0][0] / values[1][0])
    except Exception:
        return None


def _eval_boundary_average_last(model: Any, expression: str, boundaries: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, "IntLine") as feature:
            feature.selection().geom("geom1", 1)
            feature.selection().set(boundaries)
            feature.set("intsurface", True)
            feature.set("expr", [expression, "1"])
            feature.set("looplevelinput", ["last"])
            feature.run()
            values = feature.getReal()
            return float(values[0][0] / values[1][0])
    except Exception:
        return None


def _eval_extremum_last(model: Any, feature_type: str, expression: str, domains: list[int]) -> float | None:
    try:
        with _temporary_numerical_feature(model, feature_type) as feature:
            feature.selection().geom("geom1", 2)
            feature.selection().set(domains)
            feature.set("expr", [expression])
            feature.set("looplevelinput", ["last"])
            feature.run()
            return float(feature.getReal()[0][0])
    except Exception:
        return None


def _get_model_dimension(model: Any) -> int:
    """Get the configured or auto-detected spatial dimension of the model.

    Priority:
    1. Workflow state `model_dimension` (set by configure_single_main_workflow)
    2. Auto-detect from existing physics interfaces
    3. Default to 0 (unknown)
    """
    # Check workflow state first
    try:
        from comsol_mcp._state import _read_workflow_state
        state = _read_workflow_state()
        dim = int(state.get("model_dimension", 0))
        if dim in (1, 2, 3):
            return dim
    except Exception:
        pass

    # Auto-detect from existing physics interfaces
    try:
        comps = list(model.java.component().tags())
        if comps:
            comp = model.java.component(comps[0])
            phys_tags = list(comp.physics().tags())
            if phys_tags:
                phys = comp.physics(phys_tags[0])
                # Try common API methods for getting spatial dimension from physics
                for method_name in ("getNDim", "getSpatialDim", "sdim", "getDim"):
                    try:
                        val = getattr(phys, method_name)
                        if callable(val):
                            d = int(val())
                            if d in (1, 2, 3):
                                return d
                        else:
                            d = int(val)
                            if d in (1, 2, 3):
                                return d
                    except Exception:
                        continue
                # Try reading the physics selection dimension
                try:
                    sel = phys.feature().tags()
                    if sel:
                        feat = phys.feature(sel[0])
                        # The selection geom dimension reveals spatial dim
                        for attr_name in ("_geomDim", "dim"):
                            try:
                                d = int(getattr(feat.selection(), attr_name))
                                if d in (1, 2, 3):
                                    return d
                            except Exception:
                                continue
                except Exception:
                    pass
    except Exception:
        pass

    return 0


def _evaluate_aggregate(
    model: Any,
    expression: str,
    aggregate: str,
    domains: list[int] | None = None,
    boundaries: list[int] | None = None,
    time_point: str = "last",
) -> Any:
    """Evaluate an expression with aggregation. Returns scalar for aggregated, raw for 'none'.

    Automatically selects correct COMSOL result feature type based on model dimension:
    - 3D: domains→Volume, boundaries→Surface
    - 2D: domains→Surface, boundaries→Line
    - 1D: domains→Line, boundaries→Point
    """
    aggregate = (aggregate or "none").lower().strip()
    if aggregate == "none":
        return _evaluate_expression_safely(model, expression, time_point)

    # Get spatial dimension from workflow config or auto-detect
    dim = _get_model_dimension(model)
    if dim == 0:
        raise ValueError("Cannot aggregate without an explicit model dimension; configure the workflow or model geometry.")

    # Map: (dim, entity) → suffix for max/min/int
    # 3D: domains=Volume, boundaries=Surface
    # 2D: domains=Surface, boundaries=Line
    # 1D: domains=Line, boundaries=Point (rare)
    if boundaries:
        geom_dim = max(dim - 1, 1)  # boundary is (dim-1) entity
    elif domains:
        geom_dim = dim
    else:
        geom_dim = dim

    dim_suffix = {3: "Volume", 2: "Surface", 1: "Line"}.get(geom_dim, "Surface")

    # Choose correct COMSOL result feature type
    if aggregate == "max":
        if domains or boundaries:
            ftype = f"Max{dim_suffix}"
        else:
            ftype = f"Max{dim_suffix}"
    elif aggregate == "min":
        if domains or boundaries:
            ftype = f"Min{dim_suffix}"
        else:
            ftype = f"Min{dim_suffix}"
    elif aggregate == "avg":
        if domains or boundaries:
            ftype = f"Int{dim_suffix}"
        else:
            ftype = "EvalGlobal"
    elif aggregate == "integral":
        ftype = f"Int{dim_suffix}"
    else:
        raise ValueError(f'Unknown aggregate "{aggregate}". Use max, min, avg, integral, or none.')

    with _temporary_numerical_feature(model, ftype) as feature:
        if domains:
            feature.selection().geom("geom1", dim)
            feature.selection().set(domains)
        elif boundaries:
            feature.selection().geom("geom1", max(dim - 1, 1))
            feature.selection().set(boundaries)
        feature.set("expr", [expression, "1"] if aggregate == "avg" and (domains or boundaries) else [expression])
        feature.run()
        values = _select_inner(_feature_value(feature), time_point)
        if isinstance(values, dict):
            raise RuntimeError("Complex aggregation is not supported by this legacy metric path (W17 typed complex support pending).")
        if aggregate == "avg" and (domains or boundaries):
            denom = values[1][0]
            return float("nan") if denom == 0 else float(values[0][0] / denom)
        return values if time_point == "all" else float(values[0][0])


# ---------------------------------------------------------------------------
# Model tree
# ---------------------------------------------------------------------------
def _model_tree_data(model: Any) -> dict[str, Any]:
    from comsol_mcp._state import _safe_model_label, _safe_model_path
    java = model.java
    components = [str(tag) for tag in java.component().tags()]
    component_details = []
    for component in components:
        comp = java.component(component)
        component_details.append(
            {
                "tag": component,
                "geometries": [str(tag) for tag in comp.geom().tags()],
                "meshes": [str(tag) for tag in comp.mesh().tags()],
                "physics": [str(tag) for tag in comp.physics().tags()],
                "materials": [str(tag) for tag in comp.material().tags()],
            }
        )
    return {
        "label": _safe_model_label(model),
        "file_path": _safe_model_path(model),
        "components": components,
        "component_details": component_details,
        "parameters": [row["name"] for row in _parameter_rows(model)],
        "studies": [str(tag) for tag in java.study().tags()],
        "solutions": [str(tag) for tag in java.sol().tags()],
        "datasets": [str(tag) for tag in java.result().dataset().tags()],
        "results": [str(tag) for tag in java.result().tags()],
    }


# ---------------------------------------------------------------------------
# Geometry feature helpers
# ---------------------------------------------------------------------------
def _normalize_properties(properties_json: str) -> list[tuple[str, list[str]]]:
    raw = str(properties_json or "").strip()
    if not raw:
        return []
    parsed = json.loads(raw)
    items: list[dict[str, Any]]
    if isinstance(parsed, dict):
        items = []
        for name, value in parsed.items():
            if isinstance(value, list):
                items.append({"name": name, "values": [str(item) for item in value]})
            else:
                items.append({"name": name, "value": str(value)})
    elif isinstance(parsed, list):
        items = parsed
    else:
        raise ValueError("properties_json must be a JSON object or array.")

    normalized: list[tuple[str, list[str]]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each properties_json entry must be an object.")
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        if "values" in item:
            values = item.get("values")
            if not isinstance(values, list):
                raise ValueError(f'Property "{name}" values must be an array.')
            normalized.append((name, [str(value) for value in values]))
        elif "value" in item:
            normalized.append((name, [str(item.get("value", ""))]))
        else:
            raise ValueError(f'Property "{name}" must include value or values.')
    return normalized


def _ensure_component_java(model: Any, component: str, dimension: int) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    return {"component": component, "dimension": dimension}


def _ensure_geometry_java(model: Any, component: str, geometry: str, dimension: int) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    if dimension <= 0:
        dimension = 2
    if geometry not in list(java.component(component).geom().tags()):
        java.component(component).geom().create(geometry, dimension)
    return {"component": component, "geometry": geometry, "dimension": dimension}


def _ensure_mesh_java(model: Any, component: str, mesh: str) -> dict[str, Any]:
    java = model.java
    if component not in list(java.component().tags()):
        java.component().create(component, True)
    if mesh not in list(java.component(component).mesh().tags()):
        java.component(component).mesh().create(mesh)
    return {"component": component, "mesh": mesh}


def _apply_feature_properties(feature: Any, properties: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    applied = []
    for name, values in properties:
        if len(values) <= 1:
            feature.set(name, values[0] if values else "")
            applied.append({"name": name, "value": values[0] if values else ""})
        else:
            feature.set(name, values)
            applied.append({"name": name, "values": values})
    return applied
