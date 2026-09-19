#!/usr/bin/env python3
"""Pure helpers for physics interface and variable operations (no global state)."""

from __future__ import annotations

from typing import Any

from comsol_mcp._model_ops import _normalize_properties, _apply_feature_properties, _assert_existing_feature_type


# ---------------------------------------------------------------------------
# Physics interface helpers
# ---------------------------------------------------------------------------
def _list_physics(model: Any, component: str) -> list[dict[str, Any]]:
    comp = model.java.component(component)
    tags = list(comp.physics().tags())
    result = []
    for tag in tags:
        phys = comp.physics(tag)
        info: dict[str, Any] = {"tag": tag}
        try:
            info["label"] = str(phys.label())
        except Exception:
            pass
        try:
            features = list(phys.feature().tags())
            info["features"] = features
            info["feature_count"] = len(features)
        except Exception:
            info["features"] = []
            info["feature_count"] = 0
        result.append(info)
    return result


def _list_physics_features(model: Any, component: str, physics_tag: str) -> list[dict[str, Any]]:
    phys = model.java.component(component).physics(physics_tag)
    tags = list(phys.feature().tags())
    result = []
    for tag in tags:
        feat = phys.feature(tag)
        info: dict[str, Any] = {"tag": tag}
        try:
            info["label"] = str(feat.label())
        except Exception:
            pass
        try:
            info["feature_type"] = str(feat.identifier())
        except Exception:
            # identifier() may not exist in all COMSOL versions; try alternatives
            try:
                info["feature_type"] = str(feat.getType())
            except Exception:
                try:
                    info["feature_type"] = str(feat.getString("type"))
                except Exception:
                    pass
        try:
            props = list(feat.properties())
            info["properties"] = props
        except Exception:
            info["properties"] = []
        # COMSOL exposes the inherited-state predicate, not an authoritative
        # ``isEditable`` API.  Keep the two facts separate: callers can use
        # ``selection_inheriting`` as an observation, while
        # ``selection_editable`` remains tri-state because discovery does not
        # establish whether a selection edit is accepted.
        info["selection_inheriting"] = None
        info["selection_editable"] = None
        try:
            sel = feat.selection()
            info["selection_inheriting"] = bool(sel.isInheriting())
        except Exception:
            pass
        result.append(info)
    return result


def _auto_detect_dimension(model: Any, component: str) -> int:
    """Detect spatial dimension. Priority: workflow config > existing physics > 0."""
    # 1. Check workflow state
    try:
        from comsol_mcp._state import _read_workflow_state
        state = _read_workflow_state()
        dim = int(state.get("model_dimension", 0))
        if dim in (1, 2, 3):
            return dim
    except Exception:
        pass

    # 2. Try to detect from existing physics interfaces
    try:
        comp = model.java.component(component)
        phys_tags = list(comp.physics().tags())
        if phys_tags:
            phys = comp.physics(phys_tags[0])
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
    except Exception:
        pass

    # 3. Try geometry API methods
    try:
        comp = model.java.component(component)
        geom_tags = list(comp.geom().tags())
        if geom_tags:
            geom = comp.geom(geom_tags[0])
            for method_name in ("dimension", "getDimension", "dim", "spatialDim"):
                try:
                    val = getattr(geom, method_name)
                    if callable(val):
                        return int(val())
                    return int(val)
                except Exception:
                    continue
    except Exception:
        pass

    return 0


def _java_int(value: int):
    """Send an integer through the Worker protocol without starting a JVM."""
    return int(value)


def _create_physics(model: Any, component: str, tag: str, physics_type: str, dimension: int = 0, dependent_variables: str = "u") -> dict[str, Any]:
    comp = model.java.component(component)
    existing = list(comp.physics().tags())
    if tag in existing:
        _assert_existing_feature_type(comp.physics(tag), physics_type, kind="physics interface")
        return {"tag": tag, "physics_type": physics_type, "created": False, "message": "Physics already exists."}
    dim = int(dimension)
    if dim <= 0:
        dim = _auto_detect_dimension(model, component)

    if dim > 0:
        # mph Java bridge has no (String, String, int) overload.
        # Use (String, String, String[]) overload with dependent variable names.
        # COMSOL automatically matches spatial dimension from the component's geometry.
        dep_vars = [v.strip() for v in dependent_variables.split(",") if v.strip()]
        if not dep_vars:
            dep_vars = ["u"]
        comp.physics().create(tag, physics_type, dep_vars)
    else:
        comp.physics().create(tag, physics_type)
    return {"tag": tag, "physics_type": physics_type, "dimension": dim, "created": True}


def _remove_physics(model: Any, component: str, tag: str) -> dict[str, Any]:
    comp = model.java.component(component)
    existing = list(comp.physics().tags())
    if tag not in existing:
        raise LookupError(f'Physics "{tag}" does not exist in {component}.')
    comp.physics().remove(tag)
    return {"tag": tag, "removed": True}


def _create_physics_feature(
    model: Any, component: str, physics_tag: str,
    feature_tag: str, feature_type: str,
) -> dict[str, Any]:
    phys = model.java.component(component).physics(physics_tag)
    existing = list(phys.feature().tags())
    if feature_tag in existing:
        _assert_existing_feature_type(phys.feature(feature_tag), feature_type, kind="physics feature")
        return {"tag": feature_tag, "feature_type": feature_type, "created": False, "message": "Feature already exists."}
    phys.feature().create(feature_tag, feature_type)
    return {"tag": feature_tag, "feature_type": feature_type, "created": True}


def _remove_physics_feature(model: Any, component: str, physics_tag: str, feature_tag: str) -> dict[str, Any]:
    phys = model.java.component(component).physics(physics_tag)
    existing = list(phys.feature().tags())
    if feature_tag not in existing:
        raise LookupError(f'Feature "{feature_tag}" does not exist under physics "{physics_tag}".')
    phys.feature().remove(feature_tag)
    return {"tag": feature_tag, "removed": True}


def _apply_physics_properties(
    model: Any, component: str, physics_tag: str, feature_tag: str,
    properties_json: str,
) -> list[dict[str, Any]]:
    feat = model.java.component(component).physics(physics_tag).feature(feature_tag)
    props = _normalize_properties(properties_json)
    return _apply_feature_properties(feat, props)


def _set_physics_selection(
    model: Any, component: str, physics_tag: str, feature_tag: str,
    entities: list[int] | None = None,
    selection_kind: str = "explicit",
    named_selection: str = "",
) -> dict[str, Any]:
    phys = model.java.component(component).physics(physics_tag)
    is_parent = not feature_tag or feature_tag == physics_tag
    target = phys if is_parent else phys.feature(feature_tag)
    try:
        sel = target.selection()
        if not is_parent and bool(sel.isInheriting()):
            raise ValueError(
                f'Feature "{feature_tag}" has an inherited selection and cannot be modified directly. '
                f'Set the parent physics interface "{physics_tag}" instead.'
            )
        kind = str(selection_kind or "explicit").strip().lower()
        if kind == "explicit":
            sel.set(list(entities or []))
        elif kind == "all":
            sel.all()
        elif kind == "named":
            name = str(named_selection or "").strip()
            if not name:
                raise ValueError("named selection requires named_selection.")
            sel.named(name)
        elif kind == "inherited":
            if is_parent:
                raise ValueError("A physics interface has no parent selection to inherit.")
            sel.inherit(True)
        else:
            raise ValueError('selection kind must be explicit, all, named, or inherited.')
    except (ValueError, LookupError):
        raise
    except Exception as exc:
        err_str = str(exc).lower()
        if "selection" in err_str or "edit" in err_str or "inherited" in err_str:
            raise ValueError(
                f'Cannot set selection on {"physics interface" if is_parent else "feature"} "{feature_tag or physics_tag}": {exc}'
            ) from exc
        raise
    readback: dict[str, Any] = {"kind": kind}
    try:
        readback["inheriting"] = bool(sel.isInheriting())
    except Exception:
        pass
    if kind == "explicit":
        try:
            readback["entities"] = [int(v) for v in list(sel.entities())]
        except Exception as exc:
            raise RuntimeError(f"Selection was set but actual entity readback failed: {exc}") from exc
    elif kind == "named":
        try:
            readback["named"] = str(sel.named())
        except Exception as exc:
            raise RuntimeError(f"Selection was set but actual named-selection readback failed: {exc}") from exc
    elif kind == "all":
        try:
            readback["entities"] = [int(v) for v in list(sel.entities())]
        except Exception as exc:
            raise RuntimeError(f"Selection was set to all but actual readback failed: {exc}") from exc
    return {"tag": physics_tag if is_parent else feature_tag, "target": "physics" if is_parent else "feature", "selection": readback, "selection_set": True}


# ---------------------------------------------------------------------------
# Variable helpers
# ---------------------------------------------------------------------------
def _list_variables(model: Any, component: str = "") -> list[dict[str, Any]]:
    if component:
        var_container = model.java.component(component).variable()
    else:
        var_container = model.java.variable()
    tags = list(var_container.tags())
    result = []
    for tag in tags:
        var_obj = model.java.component(component).variable(tag) if component else model.java.variable(tag)
        info: dict[str, Any] = {"tag": str(tag), "variables": []}
        for name in list(var_obj.varnames()):
            key = str(name)
            info["variables"].append({"name": key, "expression": str(var_obj.get(key))})
        result.append(info)
    return result


def _create_variable(
    model: Any, component: str, tag: str, name: str, expression: str,
) -> dict[str, Any]:
    if component:
        var_container = model.java.component(component).variable()
    else:
        var_container = model.java.variable()
    existing = list(var_container.tags())
    created = tag not in existing
    if created:
        var_container.create(tag)
    var_node = model.java.component(component).variable(tag) if component else model.java.variable(tag)
    var_node.set(name, expression)
    values = [{"name": str(item), "expression": str(var_node.get(str(item)))} for item in list(var_node.varnames())]
    return {"tag": tag, "variables": values, "created": created, "updated": not created}


def _remove_variable(model: Any, component: str, tag: str) -> dict[str, Any]:
    if component:
        var_container = model.java.component(component).variable()
    else:
        var_container = model.java.variable()
    existing = list(var_container.tags())
    if tag not in existing:
        raise LookupError(f'Variable "{tag}" does not exist.')
    var_container.remove(tag)
    return {"tag": tag, "removed": True}
