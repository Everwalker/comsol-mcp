"""W13 domain operations: parameter, variable, function and selection.

Frozen publishing contract (the control plane dispatches through this):

    OPERATIONS: dict[str, Callable[[Any, str, dict], dict]]

Each entry is called as ``fn(worker, model_tag, arguments)``.  It returns the
operation's ``data`` dictionary on success and raises
``comsol_mcp._g2_contract.ExecutionContractError`` for a refusal or a
pre-write validation failure.  It never wraps its own result in a
``{"success": ...}`` envelope: the control plane owns that.

Post-write verification failures (a setter ran but the readback did not match)
are *data*, mirroring the G2 property-set discipline: the result carries
``ok``, ``status``, ``applied``/``failed``/``not_executed`` and
``partial_change``/``execution_state_unknown`` so the caller sees exactly which
assignment happened and which never ran.  Nothing is retried silently.

op -> COMSOL API -> verification source
---------------------------------------
parameter.list        ``model.param().group().tags()`` + ``group(tag).varnames()``
                      -> Programming Reference ``model.param()``; javap
                      ``ModelParam.group()/ModelParamGroupList``.
parameter.get         ``varnames()/get()/descr()/evaluate()/evaluateComplex()/
                      evaluateUnit()`` -> Programming Reference ``model.param()``.
parameter.set         ``set(name, expr[, descr])`` -> same page; readback via
                      ``get()/descr()/evaluateUnit()``.
parameter.remove      ``varnames()/remove(name)`` -> same page.
parameter.group_manage ``param().create(tag)`` / ``group().create(tag)`` /
                      ``group().remove(tag)`` (javap ``GenericModelEntityList``) /
                      ``param().rename(old,new)`` / ``param().move(names,tag)``
                      -> same page.  Group *rename* is refused: no offline source
                      documents that a group tag can be changed.
variable.list/get/set/remove ``model.variable(<tag>).varnames()/get()/set()/
                      descr()/remove()/model()/scope()`` -> Programming
                      Reference ``model.variable()``; javap ``ExprList``/
                      ``ComponentExprList``/``ModelNode.variable()``.
variable.group_create ``variable().create(tag)`` -> same page.
variable.selection_set ``selection().named(tag)`` / ``selection().geom(gtag,dim)``
                      + ``selection().set(entities)`` / ``selection().all()`` /
                      ``selection().inherit(true)`` -> same page (the component
                      node must be set first for a global variable group).
function.list/remove  ``func().tags()`` / ``func(tag).getType()`` /
                      ``remove(tag)`` -> Programming Reference ``model.func()``.
function.create       ``func().create(tag, type)`` + ``set(property,value)`` ->
                      same page; the type vocabulary and the per-type property
                      tables are quoted in ``_g3_common.FUNCTION_TYPE_IDS`` /
                      ``FUNCTION_TYPE_PROPERTIES``.
function.inspect      ``getType()``/``functionNames()``/``getValueType``+
                      ``getString``-family readback -> same page + javap
                      ``PropFeature``/``FunctionFeature``.
function.update       ``set(property,value)`` for the node's own documented
                      property table.
function.data_import  ``set('filename'/'struct'/'nargs'/'dseparator'/'funcs'/
                      'argunit'/'fununit'...)`` + ``importData()`` -> same page
                      (``importData`` javap-verified).
function.data_reload  ``filename`` readback + ``refresh()`` -> same page
                      (``refresh`` javap-verified).
function.evaluate     refused before any engine call: no offline source
                      documents a COMSOL API that samples a function object at
                      arbitrary coordinates (see ``function_evaluate``).
selection.list        ``component.selection().tags()`` / ``getType()`` /
                      ``dim()`` / ``dimension()`` / ``entities()`` -> javap
                      ``SelectionList``/``SelectionFeature``/``Selection``/
                      ``AbstractSelection``.
selection.create/update ``selection().create(tag,type)`` + ``geom(dim)`` +
                      ``set(entities)``/``all()`` + ``set(property,value)`` ->
                      Programming Reference ``model.selection()`` and
                      ``Coordinate-Based Selections`` (Ball/Box property names).
selection.inspect     the same read accessors as ``selection.list``.
selection.remove      ``selection().remove(tag)`` -> javap ``SelectionList``.
selection.entities    ``Selection.entities()`` / ``entities(object)`` /
                      measurement-tool ``all()`` -> javap + Programming
                      Reference ``model.selection()``.
selection.measure     ``component.measure()`` -> ``GeomMeasureFinal`` ->
                      ``selection().geom(dim).set(entities)`` -> metric getters
                      -> javap ``GeomMeasureBase`` (no centroid getter exists).
selection.query_spatial ``selection().create(tag,'Ball'|'Box'|'Cylinder'|'Disk')``
                      + ``set('entitydim'/'posx'/'r'/'xmin'/...)`` + ``entities()``
                      + ``remove(tag)`` -> Programming Reference
                      ``Coordinate-Based Selections`` and the
                      Ball/Box/Cylinder/Disk property tables.
selection.adjacency   ``geom.getAdj(fromDim,toDim)`` -> Programming Reference
                      "Adjacency": ``a[fromIdx]`` = entities of ``toDim``
                      adjacent to entity ``fromIdx``.
selection.validate    composition of the verified reads/measurements above;
                      a bounding-box expectation is refused because no offline
                      source documents the entry ordering of
                      ``getBoundingBox()``.

Known, reported gaps (refused before the first write, never guessed):
``function.evaluate`` sampling, ``centroid`` measurement, selection kinds
``spatial``/``objects`` resolution for read-only ops, ``objects`` binding,
geometry-scoped selection *creation*, function types without a retrieved
property table, and the ``data_import`` artifact-path resolution (the engine
path must be supplied in ``layout``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Callable, Mapping, Sequence

from ._g2_contract import ExecutionContractError, typed_value_from_engine
from ._g2_engine import (
    _call,
    _cursor_context,
    _cursor_decode,
    _cursor_encode,
    _cursor_verify,
    _sha256_json,
    property_set,
)
from ._g3_common import (
    FUNCTION_TYPE_IDS,
    FUNCTION_TYPE_PROPERTIES,
    MEASURE_METRICS,
    SELECTION_COMMON_PROPERTIES,
    SELECTION_KINDS,
    SELECTION_REGION_PROPERTIES,
    SPATIAL_CONDITIONS,
    SPATIAL_SELECTION_TYPES,
    accessor_container,
    all_entities_of_dimension,
    apply_local_selection,
    bound_model,
    call_probe,
    child_node,
    collection_spec,
    component_node,
    definition_properties,
    describe_engine_failure,
    entity_list_hash,
    expression_names,
    expression_read,
    expression_remove,
    expression_write,
    geometry_length_unit,
    geometry_node,
    geometry_sdim,
    measure_selection,
    node_create,
    node_not_found,
    node_remove,
    node_type,
    operation_arguments,
    path_with_segment,
    probe_geometry_tag,
    property_definition,
    property_read_rows,
    quantity,
    require_bool,
    require_entity_id_array,
    require_int,
    require_mapping,
    require_string,
    require_string_array,
    resolve_path,
    resolve_selection_entities,
    selection_state,
    split_parent_path,
    tag_conflict,
    tag_list,
    validate_selection_spec,
    validate_tag,
)

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

_PARAMETER_FORBIDDEN_CHARACTERS = frozenset(" =\t\n\r\"'()[]{};,|\\")


def _validate_name(value: Any, label: str) -> str:
    """Conservative pre-check for a parameter/variable name.

    The corpus does not publish a formal grammar for parameter names; this
    check can only narrow (never widen) what COMSOL accepts, and it rejects
    the characters that would make the name ambiguous in an expression.
    """
    name = require_string(value, label, max_length=64)
    bad = sorted(set(name) & _PARAMETER_FORBIDDEN_CHARACTERS)
    if bad:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} contains unsupported characters: {bad}")
    return name


def _status(applied: Sequence[Any], failed: Sequence[Any], not_executed: Sequence[Any],
            execution_state_unknown: bool) -> dict[str, Any]:
    if execution_state_unknown:
        status = "EXECUTION_STATE_UNKNOWN"
    elif failed:
        status = "PARTIAL_FAILURE" if applied else "FAILED"
    else:
        status = "APPLIED"
    return {
        "ok": not failed,
        "status": status,
        "partial_change": bool(applied) or bool(execution_state_unknown),
        "execution_state_unknown": bool(execution_state_unknown),
        "applied_count": len(applied),
        "failed_count": len(failed),
        "not_executed_count": len(not_executed),
    }


def _property_write(node_path: Mapping[str, Any], worker: Any, model_tag: str,
                    payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the G2 property-set discipline and normalise its envelope."""
    if not payload:
        return {"applied": [], "failed": [], "not_executed": [], "execution_state_unknown": False,
                "readback_values": {}}
    result = property_set(worker, model_tag, node_path, payload)
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied = list(data.get("applied") or [])
    return {
        "applied": applied,
        "failed": list(data.get("failed") or []),
        "not_executed": list(data.get("not_executed") or []),
        "execution_state_unknown": bool(result.get("execution_state_unknown")),
        "readback_values": {row.get("name"): row.get("readback") for row in applied if isinstance(row, Mapping) and row.get("name") is not None},
        "engine_error": result.get("error"),
    }


def _component_path(component: str) -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component}]}


def _require_component(worker: Any, model_tag: str, component: str) -> Any:
    component = validate_tag(component, "component")
    model = bound_model(worker, model_tag)
    probe = call_probe(model, "component")
    if probe["ok"]:
        tags = [str(item) for item in (tag_list(probe["value"]) if probe["value"] is not None else [])]
        if component not in tags:
            raise node_not_found(f"component {component!r} does not exist")
    return _call(model, "component", component)


def _require_geometry(worker: Any, model_tag: str, component: str, geometry: str) -> Any:
    geometry = validate_tag(geometry, "geometry")
    comp = _require_component(worker, model_tag, component)
    probe = call_probe(comp, "geom")
    if probe["ok"] and probe["value"] is not None:
        tags = [str(item) for item in tag_list(probe["value"])]
        if geometry not in tags:
            raise node_not_found(
                f"geometry {geometry!r} does not exist in component {component!r}; the engine reports {tags}"
            )
    return _call(comp, "geom", geometry)


def _scope_container(worker: Any, model_tag: str, arguments: Mapping[str, Any], collection: str
                     ) -> tuple[dict[str, Any], Any, str]:
    """Return (parent path, list node, scope label) for a component-scoped or global collection."""
    component = arguments.get("component")
    if component is None:
        parent = {"segments": []}
        node = _call(bound_model(worker, model_tag), collection)
        return parent, node, "global"
    component = validate_tag(component, "component")
    comp = _require_component(worker, model_tag, component)
    return _component_path(component), _call(comp, collection), component


# ---------------------------------------------------------------------------
# parameter domain
# ---------------------------------------------------------------------------


def parameter_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("group",))
    spec = collection_spec("param")
    container = accessor_container(worker, model_tag, {"segments": []}, "param")
    group_probe = call_probe(container, "group")
    requested = args.get("group")
    if requested is not None:
        requested = validate_tag(requested, "group")
    if group_probe["ok"] and group_probe["value"] is not None:
        groups: list[Any] = list(tag_list(container, spec))
        attribution = "model.param().group().tags()"
        group_probe_error = None
    else:
        groups = [None]
        attribution = "top_level_collection_fallback"
        group_probe_error = group_probe["error"]
    if requested is not None:
        if requested not in [item for item in groups if item is not None]:
            raise node_not_found(f"parameter group {requested!r} does not exist")
        groups = [requested]
    rows = []
    total = 0
    for group_tag in groups:
        node = container if group_tag is None else _call(container, "group", group_tag)
        names = expression_names(node)
        entries = []
        errors = []
        for name in names:
            try:
                entries.append(expression_read(node, name))
            except ExecutionContractError as exc:
                errors.append({**describe_engine_failure(exc, "get/descr/evaluateUnit"), "name": name})
        total += len(entries)
        rows.append({
            "group": group_tag,
            "readback_scope": "model.param()" if group_tag is None else f"model.param({group_tag!r})",
            "parameters": entries,
            "count": len(entries),
            "errors": errors,
        })
    return {
        "scope": "model",
        "group_attribution": attribution,
        "group_probe_error": group_probe_error,
        "groups": rows,
        "group_count": len(rows),
        "parameter_count": total,
        "unit_policy": "units are reported verbatim from evaluateUnit(); this layer never converts",
        "notes": [
            "the default parameter group has the fixed tag 'default'",
        ],
    }


def _find_parameter_group(container: Any, spec: Mapping[str, Any], name: str) -> tuple[Any, str | None]:
    """Locate a parameter by name in the top-level collection, then per group."""
    probe = call_probe(container, "get", name)
    if probe["ok"]:
        return container, None
    group_probe = call_probe(container, "group")
    if group_probe["ok"] and group_probe["value"] is not None:
        for group_tag in tag_list(container, spec):
            node = _call(container, "group", group_tag)
            inner = call_probe(node, "get", name)
            if inner["ok"]:
                return node, str(group_tag)
    raise ExecutionContractError("NAME_NOT_FOUND", f"parameter {name!r} does not exist in this model")


def _evaluate_parameter(node: Any, name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    probe = call_probe(node, "evaluate", name)
    if probe["ok"] and isinstance(probe["value"], (int, float)) and not isinstance(probe["value"], bool):
        return typed_value_from_engine(float(probe["value"]), kind="float64"), None
    complex_probe = call_probe(node, "evaluateComplex", name)
    if complex_probe["ok"] and isinstance(complex_probe["value"], (list, tuple)) and len(complex_probe["value"]) == 2:
        real, imag = complex_probe["value"]
        return typed_value_from_engine({"real": float(real), "imag": float(imag)}, kind="complex128"), None
    return None, {"evaluate": probe["error"], "evaluateComplex": complex_probe["error"]}


def parameter_get(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("names", "evaluate"), ("names",))
    names = require_string_array(args["names"], "names")
    evaluate = args.get("evaluate")
    evaluate = False if evaluate is None else require_bool(evaluate, "evaluate")
    spec = collection_spec("param")
    container = accessor_container(worker, model_tag, {"segments": []}, "param")
    rows = []
    for name in names:
        node, group_tag = _find_parameter_group(container, spec, _validate_name(name, "names[]"))
        row: dict[str, Any] = {"name": name, "group": group_tag, **expression_read(node, name)}
        if evaluate:
            value, errors = _evaluate_parameter(node, name)
            row["evaluated"] = value
            row["evaluate_errors"] = errors
        rows.append(row)
    return {
        "scope": "model",
        "parameters": rows,
        "count": len(rows),
        "unit_policy": "units are reported verbatim from evaluateUnit(); this layer never converts",
    }


def _declared_unit_matches(declared: str, readback: str | None) -> bool:
    """Exact string equality, with dimensionless '-'/'1' treated as "no unit"."""
    if readback is None:
        return declared in {"", "1", "dimensionless"}
    return declared == readback


def parameter_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("parameters", "group"), ("parameters",))
    raw_items = args["parameters"]
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, Mapping)) or not raw_items:
        raise ExecutionContractError("INVALID_REQUEST", "parameters must be a non-empty array")
    requested_group = args.get("group")
    spec = collection_spec("param")
    container = accessor_container(worker, model_tag, {"segments": []}, "param")
    group_probe = call_probe(container, "group")
    if requested_group is not None:
        requested_group = validate_tag(requested_group, "group")
        if not (group_probe["ok"] and group_probe["value"] is not None):
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                "a parameter group target requires model.param().group().tags() to prove the group exists; "
                "the connected worker refused 'group' (allow-list entry required), so the write is refused "
                "before it could create a group by accident",
            )
        if requested_group not in tag_list(container, spec):
            raise node_not_found(f"parameter group {requested_group!r} does not exist")
        target = _call(container, "group", requested_group)
    else:
        target = container

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        row = require_mapping(item, f"parameters[{index}]")
        unknown = sorted(set(row) - {"name", "expression", "description", "unit"})
        if unknown:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"parameters[{index}] has unsupported fields: {unknown}"
            )
        for field in ("name", "expression"):
            if row.get(field) is None:
                raise ExecutionContractError("INVALID_REQUEST", f"parameters[{index}].{field} is required")
        name = _validate_name(row["name"], f"parameters[{index}].name")
        if name in seen:
            raise ExecutionContractError("INVALID_REQUEST", f"parameters contains duplicate name {name!r}")
        seen.add(name)
        expression = require_string(row["expression"], f"parameters[{index}].expression")
        description = row.get("description")
        if description is not None:
            description = require_string(description, f"parameters[{index}].description")
        unit = row.get("unit")
        if unit is not None:
            unit = require_string(unit, f"parameters[{index}].unit", max_length=64)
        items.append({"name": name, "expression": expression, "description": description, "unit": unit})

    for index, item in enumerate(items):
        name = item["name"]
        try:
            pre = call_probe(target, "evaluateUnit", name)
            if item["unit"] is not None and pre["ok"] and not _declared_unit_matches(item["unit"], pre["value"]):
                raise ExecutionContractError(
                    "PROPERTY_TYPE_MISMATCH",
                    f"parameter {name!r} already carries unit {pre['value']!r} which does not match the "
                    f"declared unit {item['unit']!r}; this layer never converts units, so the write is refused",
                )
            recorded = expression_write(target, name, item["expression"], item["description"],
                                        label="parameter expression")
            if item["unit"] is not None and not _declared_unit_matches(item["unit"], recorded.get("unit")):
                applied.append({**recorded, "unit_requested": item["unit"], "unit_verified": False})
                failed.append({
                    "name": name,
                    "error": f"post-write unit readback {recorded.get('unit')!r} does not match the declared "
                             f"unit {item['unit']!r}; no conversion is performed",
                    "partial_change": True,
                    "execution_state_unknown": False,
                })
                not_executed.extend(items[index + 1:])
                break
            applied.append({**recorded, "unit_requested": item["unit"], "unit_verified": item["unit"] is not None})
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({
                "name": name,
                "error": {"code": exc.code, "message": str(exc)},
                "partial_change": bool(applied),
                "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN",
            })
            not_executed.extend(items[index + 1:])
            break
    result: dict[str, Any] = {
        "scope": "model",
        "group": requested_group,
        "readback_scope": "model.param()" if requested_group is None else f"model.param({requested_group!r})",
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "unit_policy": "declared units are compared against evaluateUnit() as exact text; no conversion",
        **_status(applied, failed, not_executed, execution_state_unknown),
    }
    return result


def parameter_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("names", "group"), ("names",))
    names = require_string_array(args["names"], "names")
    requested_group = args.get("group")
    spec = collection_spec("param")
    container = accessor_container(worker, model_tag, {"segments": []}, "param")
    if requested_group is not None:
        requested_group = validate_tag(requested_group, "group")
        group_probe = call_probe(container, "group")
        if not (group_probe["ok"] and group_probe["value"] is not None):
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                "a parameter group target requires model.param().group().tags() to prove the group exists; "
                "the connected worker refused 'group' (allow-list entry required)",
            )
        if requested_group not in tag_list(container, spec):
            raise node_not_found(f"parameter group {requested_group!r} does not exist")
        target = _call(container, "group", requested_group)
    else:
        target = container

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[Any] = []
    execution_state_unknown = False
    for index, raw_name in enumerate(names):
        name = _validate_name(raw_name, "names[]")
        try:
            applied.append(expression_remove(target, name))
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"name": name, "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": bool(applied),
                           "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN"})
            not_executed.extend(names[index + 1:])
            break
    return {
        "scope": "model",
        "group": requested_group,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "reference_risk": {
            "level": "UNSCANNED",
            "note": "removing a parameter does not scan for expressions that referenced it; a model that "
                    "still references the name will fail its next evaluation",
        },
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def parameter_group_manage(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("action", "tag", "arguments"), ("action", "tag"))
    action = require_string(args["action"], "action", max_length=32)
    tag = validate_tag(args["tag"])
    extra = require_mapping(args.get("arguments") or {}, "arguments")
    spec = collection_spec("param")
    container = accessor_container(worker, model_tag, {"segments": []}, "param")

    if action == "create":
        unknown = sorted(set(extra) - {"label"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"arguments has unsupported fields: {unknown}")
        created = node_create(worker, model_tag, {"segments": []}, "param", tag, None)
        if "label" in extra:
            label = require_string(extra["label"], "arguments.label", max_length=200)
            node = _call(container, "group", tag)
            _call(node, "label", label)
        return {"action": action, **created,
                "group_count": len(tag_list(container, spec)),
                "notes": ["model.param().create(<tag>) and model.param().group().create(<tag>) are equivalent"]}

    if action == "delete":
        unknown = sorted(set(extra))
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"arguments has unsupported fields: {unknown}")
        removed = node_remove(worker, model_tag, {"segments": []}, "param", tag)
        return {"action": action, **removed, "group_count": len(tag_list(container, spec)),
                "reference_risk": {"level": "UNSCANNED",
                                   "note": "parameters removed with their group are not scanned for references"}}

    if action == "rename":
        unknown = sorted(set(extra) - {"new_name"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"arguments has unsupported fields: {unknown}")
        new_name = extra.get("new_name")
        if new_name is None:
            raise ExecutionContractError("INVALID_REQUEST", "arguments.new_name is required for action 'rename'")
        old = _validate_name(tag, "tag")
        new = _validate_name(new_name, "arguments.new_name")
        names = expression_names(container)
        if old not in names:
            raise ExecutionContractError("NAME_NOT_FOUND", f"parameter {old!r} does not exist")
        if new in names:
            raise tag_conflict(f"parameter {new!r} already exists")
        _call(container, "rename", old, new)
        after = expression_names(container)
        if new not in after or old in after:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"model.param().rename({old!r}, {new!r}) readback did not confirm the rename",
            )
        return {"action": action, "tag": old, "new_tag": new, "renamed": True,
                "readback": {"names": after},
                "notes": ["model.param().rename(<old>,<new>) renames a parameter, not a parameter group; "
                          "a parameter-group rename has no offline-verified API and is refused"]}

    if action == "move":
        unknown = sorted(set(extra) - {"names"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"arguments has unsupported fields: {unknown}")
        names = require_string_array(extra.get("names"), "arguments.names")
        group_probe = call_probe(container, "group")
        if not (group_probe["ok"] and group_probe["value"] is not None):
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                "moving parameters requires model.param().group().tags() to prove the target group exists; "
                "the connected worker refused 'group' (allow-list entry required)",
            )
        group_tags = tag_list(container, spec)
        if tag not in group_tags:
            raise node_not_found(f"parameter group {tag!r} does not exist")
        before = {group: set(expression_names(_call(container, "group", group))) for group in group_tags}
        missing = [name for name in names if not any(name in group for group in before.values())]
        if missing:
            raise ExecutionContractError("NAME_NOT_FOUND", f"parameters do not exist: {missing}")
        _call(container, "move", list(names), tag)
        after = {group: set(expression_names(_call(container, "group", group))) for group in group_tags}
        moved = [name for name in names if name in after.get(tag, set())]
        if len(moved) != len(names):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"model.param().move(...) readback shows {moved} in group {tag!r}, expected {names}",
            )
        return {"action": action, "target_group": tag, "names": names, "moved": moved,
                "readback": {group: sorted(names) for group, names in after.items()},
                "notes": ["model.param().move(<param>,<ptag>) is documented on the top-level "
                          "model.param() collection"]}

    raise ExecutionContractError(
        "API_UNSUPPORTED",
        f"parameter.group_manage action {action!r} is not implemented; supported actions are "
        "create, delete, rename (parameter), move",
    )


# ---------------------------------------------------------------------------
# variable domain
# ---------------------------------------------------------------------------


def _variable_group_context(worker: Any, model_tag: str, arguments: Mapping[str, Any]
                            ) -> tuple[dict[str, Any], Any, str | None, str]:
    """Resolve a variable-group NodePath into (path, node, component, scope)."""
    path, node = resolve_path(worker, model_tag, arguments.get("group"), label="group")
    component: str | None = None
    for segment in path["segments"][:-1]:
        if segment.get("collection") == "component":
            component = str(segment.get("tag"))
    return path, node, component, "component" if component else "global"


def variable_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    parent, container, scope = _scope_container(worker, model_tag, args, "variable")
    groups = tag_list(container, collection_spec("variable"))
    rows = []
    for group_tag in groups:
        node = child_node(worker, model_tag, parent, "variable", group_tag)
        entries = []
        errors = []
        for name in expression_names(node):
            try:
                entries.append(expression_read(node, name))
            except ExecutionContractError as exc:
                errors.append({**describe_engine_failure(exc, "get/descr"), "name": name})
        rows.append({
            "group": group_tag,
            "path": path_with_segment(parent, "variable", group_tag),
            "variables": entries,
            "count": len(entries),
            "errors": errors,
            "selection": selection_state(_call(node, "selection")) if _has_accessor(node, "selection") else None,
            "model": call_probe(node, "model")["value"] if call_probe(node, "model")["ok"] else None,
            "scope": call_probe(node, "scope")["value"] if call_probe(node, "scope")["ok"] else None,
        })
    return {"scope": scope, "groups": rows, "group_count": len(rows)}


def _has_accessor(node: Any, method: str) -> bool:
    return getattr(node, method, None) is not None


def variable_get(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("group", "names"), ("group",))
    path, node, component, scope = _variable_group_context(worker, model_tag, args)
    names_arg = args.get("names")
    available = expression_names(node)
    if names_arg is None:
        selected = available
    else:
        selected = require_string_array(names_arg, "names")
        missing = [name for name in selected if name not in available]
        if missing:
            raise ExecutionContractError("NAME_NOT_FOUND", f"variables do not exist in this group: {missing}")
    rows = [expression_read(node, name) for name in selected]
    return {
        "path": path,
        "scope": scope,
        "component": component,
        "variables": rows,
        "count": len(rows),
        "varnames": available,
        "model": call_probe(node, "model")["value"] if call_probe(node, "model")["ok"] else None,
        "scope_name": call_probe(node, "scope")["value"] if call_probe(node, "scope")["ok"] else None,
        "selection": selection_state(_call(node, "selection")) if _has_accessor(node, "selection") else None,
    }


def variable_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("group", "variables"), ("group", "variables"))
    path, node, component, scope = _variable_group_context(worker, model_tag, args)
    raw_items = args["variables"]
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, Mapping)) or not raw_items:
        raise ExecutionContractError("INVALID_REQUEST", "variables must be a non-empty array")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        row = require_mapping(item, f"variables[{index}]")
        unknown = sorted(set(row) - {"name", "expression", "description"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"variables[{index}] has unsupported fields: {unknown}")
        for field in ("name", "expression"):
            if row.get(field) is None:
                raise ExecutionContractError("INVALID_REQUEST", f"variables[{index}].{field} is required")
        name = _validate_name(row["name"], f"variables[{index}].name")
        if name in seen:
            raise ExecutionContractError("INVALID_REQUEST", f"variables contains duplicate name {name!r}")
        seen.add(name)
        description = row.get("description")
        if description is not None:
            description = require_string(description, f"variables[{index}].description")
        items.append({"name": name,
                      "expression": require_string(row["expression"], f"variables[{index}].expression"),
                      "description": description})
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    for index, item in enumerate(items):
        try:
            applied.append(expression_write(node, item["name"], item["expression"], item["description"],
                                            label="variable expression"))
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"name": item["name"], "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": bool(applied),
                           "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN"})
            not_executed.extend(items[index + 1:])
            break
    return {
        "path": path,
        "scope": scope,
        "component": component,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "varnames": expression_names(node),
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def variable_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("group", "names", "whole_group"), ("group",))
    path, node, component, scope = _variable_group_context(worker, model_tag, args)
    whole_group = args.get("whole_group")
    whole_group = False if whole_group is None else require_bool(whole_group, "whole_group")
    names_arg = args.get("names")
    if whole_group:
        if names_arg is not None:
            raise ExecutionContractError("INVALID_REQUEST", "names must be omitted when whole_group is true")
        parent, collection, tag = split_parent_path(path, label="group")
        removed = node_remove(worker, model_tag, parent, collection, tag)
        return {"path": path, "scope": scope, "whole_group": True, **removed,
                "reference_risk": {"level": "UNSCANNED",
                                   "note": "removing a variable group does not scan for expressions that "
                                           "referenced its variables"}}
    if names_arg is None:
        raise ExecutionContractError("INVALID_REQUEST", "names is required unless whole_group is true")
    names = require_string_array(names_arg, "names")
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[Any] = []
    execution_state_unknown = False
    for index, raw_name in enumerate(names):
        try:
            applied.append(expression_remove(node, _validate_name(raw_name, "names[]")))
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"name": raw_name, "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": bool(applied),
                           "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN"})
            not_executed.extend(names[index + 1:])
            break
    return {
        "path": path,
        "scope": scope,
        "component": component,
        "whole_group": False,
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "varnames": expression_names(node),
        "reference_risk": {"level": "UNSCANNED",
                           "note": "removing variables does not scan for the expressions that referenced them"},
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def variable_group_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("tag", "component", "selection"), ("tag",))
    tag = validate_tag(args["tag"])
    component = args.get("component")
    scope_spec = {} if component is None else {"component": component}
    parent, container, scope = _scope_container(worker, model_tag, scope_spec, "variable")
    created = node_create(worker, model_tag, parent, "variable", tag, None)
    node = child_node(worker, model_tag, parent, "variable", tag)
    selection = args.get("selection")
    selection_result = None
    if selection is not None:
        resolved_spec = validate_selection_spec(selection, label="selection")
        effective_component = component or resolved_spec.get("component")
        if scope == "global" and resolved_spec["kind"] in {"explicit", "all"} and not effective_component:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                "a local selection on a global variable group requires the model component node to be set "
                "first (model.variable(<tag>).model(<mtag>)); pass component or selection.component",
            )
        if not _has_accessor(node, "selection"):
            raise ExecutionContractError("API_UNSUPPORTED", "this variable group does not expose selection()")
        local = _call(node, "selection")
        selection_result = apply_local_selection(
            local, worker, model_tag,
            effective_component if isinstance(effective_component, str) else None,
            resolved_spec, owner=node,
        )
    return {
        **created,
        "scope": scope,
        "component": component,
        "group": tag,
        "selection": selection_result,
        "readback": {**created.get("readback", {}), "model": call_probe(node, "model")["value"],
                     "scope_name": call_probe(node, "scope")["value"]},
    }


def variable_selection_set(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("group", "selection"), ("group", "selection"))
    path, node, component, scope = _variable_group_context(worker, model_tag, args)
    resolved_spec = validate_selection_spec(args["selection"], label="selection")
    effective_component = component or resolved_spec.get("component")
    if scope == "global" and resolved_spec["kind"] in {"explicit", "all"} and not effective_component:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "a local selection on a global variable group requires the model component node to be set first "
            "(model.variable(<tag>).model(<mtag>)); pass selection.component",
        )
    if not _has_accessor(node, "selection"):
        raise ExecutionContractError("API_UNSUPPORTED", "this variable group does not expose selection()")
    selection_node = _call(node, "selection")
    applied = apply_local_selection(selection_node, worker, model_tag,
                                    effective_component if isinstance(effective_component, str) else None,
                                    resolved_spec, owner=node)
    return {
        "path": path,
        "scope": scope,
        "component": component,
        "selection": applied,
        "entity_state": selection_state(selection_node),
        "ok": True,
        "status": "APPLIED",
    }


# ---------------------------------------------------------------------------
# function domain
# ---------------------------------------------------------------------------


_FUNCTION_LAYOUT_PROPERTIES: dict[str, tuple[str, frozenset[str] | None]] = {
    # Recording the documented vocabulary of each interpolation layout property
    # (Programming Reference "interpolation properties" table).
    "struct": ("struct", frozenset({"grid", "sectionwise", "spreadsheet"})),
    "nargs": ("nargs", None),
    "dseparator": ("dseparator", frozenset({"point", "comma"})),
    "funcs": ("funcs", None),
    "funcnametable": ("funcnametable", None),
    "argrange": ("argrange", None),
    "interp": ("interp", frozenset({"neighbor", "linear", "piecewisecubic", "cubicspline"})),
    "extrap": ("extrap", frozenset({"none", "const", "interior", "linear", "value"})),
    "extrapvalue": ("extrapvalue", None),
    "sampling": ("sampling", frozenset({"automatic", "uniform", "adaptive"})),
    "points": ("points", None),
    "adaptol": ("adaptol", None),
    "scaledata": ("scaledata", frozenset({"auto", "on", "off"})),
    "defvars": ("defvars", None),
    "frame": ("frame", None),
    "source": ("source", frozenset({"file", "table", "resultTable", "function"})),
    "sourcetype": ("sourcetype", frozenset({"model", "user"})),
    "table": ("table", None),
    "resultTable": ("resultTable", None),
    "argtrans": ("argtrans", frozenset({"none", "logarithmic"})),
    "valtrans": ("valtrans", frozenset({"none", "logarithmic"})),
}
_LAYOUT_FILE_KEYS = ("file_path", "resolved_path", "local_path")
_FUNCTION_DATA_IMPORT_TYPES = frozenset({"Interpolation"})


def _function_scope(worker: Any, model_tag: str, arguments: Mapping[str, Any]
                    ) -> tuple[dict[str, Any], Any, str]:
    scope = arguments.get("scope")
    if scope is None:
        return {"segments": []}, _call(bound_model(worker, model_tag), "func"), "global"
    path, node = resolve_path(worker, model_tag, scope, label="scope")
    probe = call_probe(node, "func")
    if not probe["ok"]:
        raise node_not_found(
            f"scope {scope!r} does not expose func(): {probe['error']}"
        )
    return path, probe["value"], path["segments"][0].get("tag") if path["segments"] else "global"


def function_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    parent, container, scope = _scope_container(worker, model_tag, args, "func")
    rows = []
    for tag in tag_list(container, collection_spec("func")):
        node = child_node(worker, model_tag, parent, "func", tag)
        names = call_probe(node, "functionNames")
        rows.append({
            "tag": tag,
            "path": path_with_segment(parent, "func", tag),
            "type_id": node_type(node),
            "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
            "function_names_error": None if names["ok"] else names["error"],
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
        })
    return {"scope": scope, "functions": rows, "count": len(rows)}


#: GUI-label -> documented API property name for a function *definition* object.
#:
#: The COMSOL GUI shows these settings under the labels used on the left; the
#: API property names are the ones the local 6.4 corpus documents, so a caller
#: spelling a documented GUI label is translated to the documented property
#: instead of being refused as an unknown key.  Only labels with a citation are
#: listed; a name that is neither a documented property nor a cited label is
#: still refused before the write.
#:
#: Sources (Programming Reference 6.4, "interpolation properties" table, p.113;
#: the same names are used by the XML file format's ``<interp>``/``<extrap>``/
#: ``<fununit>``/``<argunit>`` elements and by the Application Programming Guide
#: ``interp``/``extrap`` examples):
#:
#: * ``interpolation`` -> ``interp`` (the interpolation method row)
#: * ``extrapolation`` -> ``extrap`` (the extrapolation method row)
#: * ``data_unit`` / ``function_unit`` -> ``fununit`` (the function value unit)
#: * ``argument_unit`` -> ``argunit``
#: * ``number_of_arguments`` / ``num_args`` -> ``nargs``
FUNCTION_DEFINITION_ALIASES: dict[str, dict[str, str]] = {
    "Interpolation": {
        "interpolation": "interp",
        "interpolation_method": "interp",
        "extrapolation": "extrap",
        "extrapolation_method": "extrap",
        "data_unit": "fununit",
        "function_unit": "fununit",
        "argument_unit": "argunit",
        "number_of_arguments": "nargs",
        "num_args": "nargs",
    },
    "Analytic": {
        "extrapolation": "periodic",
        "data_unit": "fununit",
        "function_unit": "fununit",
        "argument_unit": "argunit",
        "expression": "expr",
        "number_of_arguments": "nargs",
        "num_args": "nargs",
    },
}

#: Which documented-label view ``function.inspect`` reports next to the raw
#: property names (label -> documented property).
FUNCTION_SETTING_VIEWS: dict[str, str] = {
    "interpolation": "interp",
    "extrapolation": "extrap",
    "data_unit": "fununit",
}


def _translate_function_definition_keys(definition: Mapping[str, Any], type_id: str,
                                        allowed: Iterable[str]) -> dict[str, Any]:
    """Rename cited GUI labels to their documented API property names.

    A translation is only performed when the target is in the documented table
    for this function type; when both spellings are present the request is
    refused (a silent overwrite could change which value is written).  The
    caller-side record of what was translated is reported by the operations
    through ``_function_definition_translations``.
    """
    aliases = FUNCTION_DEFINITION_ALIASES.get(type_id, {})
    translated: dict[str, Any] = {}
    for name, value in definition.items():
        target = aliases.get(name)
        if target is None or name in allowed:
            translated[name] = value
            continue
        if target not in allowed:
            # The alias table has no verified target for this type: keep the
            # original name so the documented-table refusal still fires instead
            # of writing an unverified property.
            translated[name] = value
            continue
        if target in definition or target in translated:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"definition gives {target!r} twice: the documented property name and its GUI label {name!r} "
                f"were both supplied",
            )
        translated[target] = value
    return translated


def _function_definition_translations(definition: Mapping[str, Any], type_id: str) -> list[dict[str, str]]:
    """The translations ``_translate_function_definition_keys`` would apply."""
    aliases = FUNCTION_DEFINITION_ALIASES.get(type_id, {})
    allowed = FUNCTION_TYPE_PROPERTIES.get(type_id, None)
    if allowed is None:
        return []
    records: list[dict[str, str]] = []
    for name in definition:
        target = aliases.get(name)
        if target is not None and name not in allowed and target in allowed:
            records.append({
                "given": name,
                "property": target,
                "basis": "Programming Reference 6.4 interpolation/analytic properties table (the GUI label "
                         "and the API property name refer to the same setting)",
            })
    return records


def _function_definition_payload(definition: Mapping[str, Any], type_id: str, node: Any) -> list[dict[str, Any]]:
    """Split a definition object into flat properties plus an optional wrapper."""
    allowed = FUNCTION_TYPE_PROPERTIES.get(type_id, None)
    wrapper = definition.get("properties")
    flat = {name: value for name, value in definition.items() if name != "properties"}
    if wrapper is not None:
        nested = property_definition(wrapper, "definition.properties")
        duplicates = sorted(set(nested) & set(flat))
        if duplicates:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"definition properties are given twice: {duplicates}"
            )
        flat.update(nested)
    if allowed is None and flat:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"the documented property table for function type {type_id!r} was not available offline; "
            f"{sorted(flat)} is refused before the write instead of being guessed",
        )
    if allowed is not None:
        flat = _translate_function_definition_keys(flat, type_id, allowed)
    return definition_properties(node, flat, allowed, label="definition")


def function_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("scope", "tag", "type_id", "definition"),
                               ("tag", "type_id", "definition"))
    tag = validate_tag(args["tag"])
    type_id = require_string(args["type_id"], "type_id", max_length=64)
    if type_id not in FUNCTION_TYPE_IDS:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{type_id!r} is not in the verified COMSOL 6.4 function type vocabulary: "
            f"{sorted(FUNCTION_TYPE_IDS)}",
        )
    definition = require_mapping(args["definition"], "definition")
    parent, container, scope = _function_scope(worker, model_tag, args)
    created = node_create(worker, model_tag, parent, "func", tag, type_id)
    node = child_node(worker, model_tag, parent, "func", tag)
    payload = _function_definition_payload(definition, type_id, node)
    write = _property_write(created["path"], worker, model_tag, payload)
    names = call_probe(node, "functionNames")
    result: dict[str, Any] = {
        "scope": scope,
        "path": created["path"],
        "tag": tag,
        "type_id": type_id,
        "type_readback": created["type_readback"],
        "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
        "applied": write["applied"],
        "failed": write["failed"],
        "not_executed": write["not_executed"],
        "properties": write["readback_values"],
        "definition_keys": sorted(definition),
        "definition_key_translations": _function_definition_translations(definition, type_id),
        "notes": [
            "function property values are typed from the engine's own getValueType metadata; "
            "units are set as text and never converted",
        ],
    }
    result.update(_status(write["applied"], write["failed"], write["not_executed"],
                          write["execution_state_unknown"]))
    return result


def _function_inspect_payload(node: Any, type_id: str | None) -> dict[str, Any]:
    allowed = FUNCTION_TYPE_PROPERTIES.get(type_id or "", None)
    if allowed is None:
        return {
            "properties": {},
            "property_metadata": {},
            "settings": {label: None for label in FUNCTION_SETTING_VIEWS},
            "type_properties_verified": False,
            "note": None if type_id else "the node type could not be read",
        }
    values = property_read_rows(node, sorted(allowed))
    return {
        "properties": values,
        # The documented-label view of the same settings, so a caller can assert
        # "interpolation / extrapolation / data unit" without knowing that the
        # API property names are ``interp`` / ``extrap`` / ``fununit``.
        "settings": {
            label: {
                "property": property_name,
                "value": (values.get(property_name) or {}).get("value"),
                "error": (values.get(property_name) or {}).get("error"),
                "basis": "Programming Reference 6.4 interpolation properties table",
            }
            for label, property_name in FUNCTION_SETTING_VIEWS.items()
        },
        "property_metadata": {
            name: {"kind": row.get("kind"), "shape_rank": row.get("shape_rank"),
                   "value_type": row.get("value_type"), "allowed_values": row.get("allowed_values")}
            for name, row in _metadata_rows(node, sorted(allowed)).items()
        },
        "type_properties_verified": True,
    }


def _metadata_rows(node: Any, names: Sequence[str]) -> dict[str, dict[str, Any]]:
    from ._g2_contract import property_schema_from_engine

    return {name: property_schema_from_engine(node, name) for name in names}


def function_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    names = call_probe(node, "functionNames")
    payload = _function_inspect_payload(node, type_id)
    settings = payload.get("settings") if isinstance(payload.get("settings"), Mapping) else {}

    def setting_value(label: str) -> Any:
        row = settings.get(label) if isinstance(settings, Mapping) else None
        return row.get("value") if isinstance(row, Mapping) else None

    return {
        "path": path,
        "type_id": type_id,
        "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
        "function_names_error": None if names["ok"] else names["error"],
        "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
        # Documented-label view of the settings, at the top level so a caller can
        # assert the interpolation/extrapolation/data-unit settings directly; the
        # API property names (interp/extrap/fununit) stay in ``properties``.
        "interpolation": setting_value("interpolation"),
        "extrapolation": setting_value("extrapolation"),
        "data_unit": setting_value("data_unit"),
        "settings_view_note": "the documented API property names are interp/extrap/fununit; these top-level "
                              "keys are the same values under their documented GUI labels",
        **payload,
    }


def function_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "definition"), ("path", "definition"))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    definition = require_mapping(args["definition"], "definition")
    if type_id is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the node type could not be read (getType is unavailable or failed), so the documented property "
            "table cannot be applied; refusing the write",
        )
    payload = _function_definition_payload(definition, type_id, node)
    write = _property_write(path, worker, model_tag, payload)
    names = call_probe(node, "functionNames")
    result: dict[str, Any] = {
        "path": path,
        "type_id": type_id,
        "applied": write["applied"],
        "failed": write["failed"],
        "not_executed": write["not_executed"],
        "properties": write["readback_values"],
        "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
        "definition_key_translations": _function_definition_translations(definition, type_id),
    }
    result.update(_status(write["applied"], write["failed"], write["not_executed"],
                          write["execution_state_unknown"]))
    return result


def function_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    parent, collection, tag = split_parent_path(args["path"], label="path")
    if collection != "func":
        raise ExecutionContractError("INVALID_REQUEST", "path must end in a func:<tag> segment")
    removed = node_remove(worker, model_tag, parent, collection, tag)
    return {
        **removed,
        "reference_risk": {"level": "UNSCANNED",
                           "note": "removing a function does not scan for expressions that referenced it"},
    }


def function_data_import(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "artifact_id", "layout", "units"),
                               ("path", "artifact_id", "layout", "units"))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    if type_id not in _FUNCTION_DATA_IMPORT_TYPES:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"function.data_import is implemented for {sorted(_FUNCTION_DATA_IMPORT_TYPES)} "
            f"(the offline-verified data-import properties); node type is {type_id!r}",
        )
    artifact_id = require_string(args["artifact_id"], "artifact_id")
    layout = require_mapping(args["layout"], "layout")
    units = require_mapping(args["units"], "units")

    engine_path = None
    for key in _LAYOUT_FILE_KEYS:
        value = layout.get(key)
        if isinstance(value, str) and value:
            engine_path = value
            break
    unknown_layout = sorted(set(layout) - set(_FUNCTION_LAYOUT_PROPERTIES) - set(_LAYOUT_FILE_KEYS))
    if unknown_layout:
        raise ExecutionContractError("INVALID_REQUEST", f"layout has unsupported fields: {unknown_layout}")
    if engine_path is None:
        raise ExecutionContractError(
            "ARTIFACT_MISSING",
            "layout must carry the engine-visible data file path (layout.file_path / layout.resolved_path); "
            "an artifact_id alone cannot be resolved to a filesystem path in this layer, so the import is "
            "refused instead of guessing a location",
        )
    unknown_units = sorted(set(units) - {"argunit", "fununit"})
    if unknown_units:
        raise ExecutionContractError("INVALID_REQUEST", f"units has unsupported fields: {unknown_units}")
    for key, value in units.items():
        if value is not None and not isinstance(value, str):
            raise ExecutionContractError("INVALID_REQUEST", f"units.{key} must be a string")

    raw_properties: dict[str, Any] = {"filename": engine_path}
    raw_properties.update({key: value for key, value in layout.items() if key in _FUNCTION_LAYOUT_PROPERTIES})
    for key, value in units.items():
        if value is not None:
            raw_properties[key] = value
    raw_properties.setdefault("source", "file")
    for key, (_, vocabulary) in _FUNCTION_LAYOUT_PROPERTIES.items():
        if key in raw_properties and vocabulary is not None and isinstance(raw_properties[key], str):
            if raw_properties[key] not in vocabulary:
                raise ExecutionContractError(
                    "INVALID_REQUEST",
                    f"layout.{key}={raw_properties[key]!r} is outside the documented vocabulary "
                    f"{sorted(vocabulary)}",
                )
    if "nargs" in raw_properties:
        nargs = raw_properties["nargs"]
        if isinstance(nargs, bool) or not isinstance(nargs, int) or not 1 <= nargs <= 3:
            raise ExecutionContractError("INVALID_REQUEST", "layout.nargs must be an integer in 1..3")

    payload = definition_properties(node, raw_properties, FUNCTION_TYPE_PROPERTIES.get("Interpolation"),
                                    label="layout/units")
    write = _property_write(path, worker, model_tag, payload)

    import_result: dict[str, Any]
    if write["failed"] or write["execution_state_unknown"]:
        import_result = {
            "ok": False,
            "skipped": True,
            "reason": "importData() was not called because a layout/units property failed its readback",
        }
    else:
        try:
            _call(node, "importData")
            import_result = {"ok": True}
        except ExecutionContractError as exc:
            import_result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}

    readback = property_read_rows(node, ["filename", "struct", "nargs", "dseparator", "source", "argunit",
                                        "fununit", "funcs", "filecolumns", "fileheaders"])
    names = call_probe(node, "functionNames")
    result: dict[str, Any] = {
        "path": path,
        "type_id": type_id,
        "artifact": {
            "artifact_id": artifact_id,
            "engine_path": engine_path,
            "sha256": _local_sha256(engine_path),
            "verify_status": "properties_readback_only",
        },
        "applied": write["applied"],
        "failed": write["failed"],
        "not_executed": write["not_executed"],
        "import_data": import_result,
        "readback": readback,
        "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
        "notes": [
            "the data file is read by the COMSOL engine host; the local sha256 is reported only when the "
            "engine path is readable from this process (remote/container engines report unavailable)",
        ],
        **_status(write["applied"], write["failed"], write["not_executed"], write["execution_state_unknown"]),
    }
    if not import_result.get("ok"):
        # The layout/unit properties were written but the engine never loaded the
        # data, so the function is left half-configured: report it as a partial
        # failure instead of a success envelope.
        result["ok"] = False
        result["status"] = "PARTIAL_FAILURE" if write["applied"] else "FAILED"
        result["partial_change"] = bool(write["applied"])
    return result


def function_data_reload(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    if type_id not in _FUNCTION_DATA_IMPORT_TYPES:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"function.data_reload is implemented for {sorted(_FUNCTION_DATA_IMPORT_TYPES)}; node type is {type_id!r}",
        )
    before = property_read_rows(node, ["filename", "struct", "nargs", "dseparator", "source"])
    filename = before.get("filename", {}).get("value")
    if not isinstance(filename, str) or not filename:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "the function has no filename property value, so there is nothing to reload; use function.data_import",
        )
    try:
        _call(node, "refresh")
        refresh_result = {"ok": True}
    except ExecutionContractError as exc:
        refresh_result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    after = property_read_rows(node, ["filename", "struct", "nargs", "dseparator", "source", "filecolumns", "fileheaders"])
    names = call_probe(node, "functionNames")
    return {
        "path": path,
        "type_id": type_id,
        "filename": filename,
        "sha256": _local_sha256(filename),
        "refresh": refresh_result,
        "readback_before": before,
        "readback_after": after,
        "function_names": list(names["value"]) if names["ok"] and isinstance(names["value"], (list, tuple)) else None,
        "ok": bool(refresh_result.get("ok")),
        "status": "APPLIED" if refresh_result.get("ok") else "EXECUTION_STATE_UNKNOWN",
        "execution_state_unknown": not bool(refresh_result.get("ok")),
        "partial_change": False,
    }


def function_evaluate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "arguments", "derivative"), ("path", "arguments"))
    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    samples = args["arguments"]
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes, Mapping)) or not samples:
        raise ExecutionContractError("INVALID_REQUEST", "arguments must be a non-empty array of sample objects")
    for index, sample in enumerate(samples):
        row = require_mapping(sample, f"arguments[{index}]")
        unknown = sorted(set(row) - {"coordinate", "value", "unit"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"arguments[{index}] has unsupported fields: {unknown}")
        if "coordinate" in row:
            coordinates = row["coordinate"]
            if not isinstance(coordinates, Sequence) or isinstance(coordinates, (str, bytes, Mapping)):
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"arguments[{index}].coordinate must be an array of numbers"
                )
            for position, value in enumerate(coordinates):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ExecutionContractError(
                        "INVALID_REQUEST", f"arguments[{index}].coordinate[{position}] must be a number"
                    )
        elif "value" in row:
            value = row["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExecutionContractError("INVALID_REQUEST", f"arguments[{index}].value must be a number")
        else:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"arguments[{index}] requires coordinate (array) or value (number)"
            )
        if row.get("unit") is not None:
            require_string(row["unit"], f"arguments[{index}].unit", max_length=64)
    if args.get("derivative") is not None:
        derivative = require_mapping(args["derivative"], "derivative")
        unknown = sorted(set(derivative) - {"order", "argument_index"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"derivative has unsupported fields: {unknown}")
        if derivative.get("order") is not None:
            require_int(derivative["order"], "derivative.order", minimum=1, maximum=2)
        if derivative.get("argument_index") is not None:
            require_int(derivative["argument_index"], "derivative.argument_index", minimum=0, maximum=2)
    names = call_probe(node, "functionNames")
    raise ExecutionContractError(
        "API_UNSUPPORTED",
        "function.evaluate: no offline-verified COMSOL 6.4 API samples a function object at arbitrary "
        "coordinates.  The function node exposes functionNames() only (verified), and the documented data "
        "paths (an Evaluation or Grid dataset feeding an evaluation node) belong to the results domain, "
        "which is outside W13; sampling was therefore refused before any engine call rather than guessed. "
        f"Requested {len(samples)} sample point(s) for a function with "
        f"functionNames()={names['value'] if names['ok'] else 'unavailable'}.",
    )


def _local_sha256(path: str) -> dict[str, Any]:
    import hashlib
    from pathlib import Path

    try:
        candidate = Path(path)
        if not candidate.is_file():
            return {"status": "unavailable", "reason": "path is not a readable file from this process"}
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {"status": "verified", "value": digest.hexdigest(), "size": candidate.stat().st_size}
    except Exception as exc:  # noqa: BLE001 - a missing local file must not fail the operation
        return {"status": "unavailable", "reason": f"{type(exc).__name__}"}


# ---------------------------------------------------------------------------
# selection domain
# ---------------------------------------------------------------------------


_SELECTION_DEFINITION_KEYS = frozenset(
    {"entities", "entity_dimension", "geometry", "named", "inherit", "properties", "object_tags", "label"}
)


def _selection_property_allowance(type_id: str) -> frozenset[str]:
    if type_id in SELECTION_REGION_PROPERTIES:
        return frozenset(SELECTION_COMMON_PROPERTIES | SELECTION_REGION_PROPERTIES[type_id])
    return frozenset(SELECTION_COMMON_PROPERTIES)


def _region_group_properties(definition: Mapping[str, Any], type_id: str) -> dict[str, Any]:
    """Translate a documented spatial-region group into selection properties.

    selection.query.spatial documents a region as {kind: box, xmin: ...}; the same
    group is accepted here nested under its kind so a caller can describe a Box
    selection without spelling the limit names.  Only the group that belongs to
    this selection type and only its documented property names are accepted:
    anything else stays a refusal.
    """
    out: dict[str, Any] = {}
    for kind, spec in _SPATIAL_QUERY_SPECS.items():
        group = definition.get(kind)
        if group is None:
            continue
        selection_type = spec["selection_type"]
        if selection_type != type_id:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"definition.{kind} describes a {selection_type} selection, not {type_id}",
            )
        if not isinstance(group, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", f"definition.{kind} must be an object")
        allowed = set(SELECTION_REGION_PROPERTIES.get(selection_type, frozenset()))
        unknown = sorted(set(group) - allowed)
        if unknown:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"definition.{kind} has fields that are not documented {selection_type} properties: {unknown}",
            )
        if not group:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"definition.{kind} must name at least one {selection_type} property",
            )
        for name, value in group.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ExecutionContractError("INVALID_REQUEST", f"definition.{kind}.{name} must be a number")
            out[name] = value
    return out


def _split_selection_definition(definition: Mapping[str, Any], type_id: str) -> dict[str, Any]:
    """Validate a selection definition against the type's documented vocabulary."""
    unknown = sorted(set(definition) - _SELECTION_DEFINITION_KEYS - _selection_property_allowance(type_id)
                     - set(_SPATIAL_QUERY_SPECS))
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"definition has fields that are neither assignment fields nor documented {type_id} properties: "
            f"{unknown}",
        )
    region_properties = _region_group_properties(definition, type_id)
    out: dict[str, Any] = {}
    if definition.get("label") is not None:
        out["label"] = require_string(definition["label"], "definition.label", max_length=200)
    if definition.get("entities") is not None:
        if type_id != "Explicit":
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"only the Explicit selection type supports entity assignment; {type_id} accepts properties "
                "only (Programming Reference model.selection())",
            )
        out["entities"] = require_entity_id_array(definition["entities"], "definition.entities")
    if definition.get("entity_dimension") is not None:
        dimension = require_int(definition["entity_dimension"], "definition.entity_dimension", minimum=0, maximum=3)
        if type_id == "Explicit":
            out["entity_dimension"] = dimension
        else:
            out.setdefault("properties", {})["entitydim"] = dimension
    if definition.get("geometry") is not None:
        out["geometry"] = validate_tag(definition["geometry"], "definition.geometry")
    if definition.get("named") is not None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "a component-level named selection has no verified 'named' assignment (SelectionFeature does not "
            "expose Selection.named); the documented named reference is a *local* selection assignment, which "
            "this layer exposes through variable.selection_set",
        )
    if definition.get("inherit") is not None:
        out["inherit"] = require_bool(definition["inherit"], "definition.inherit")
    if definition.get("object_tags") is not None:
        out["object_tags"] = require_string_array(definition["object_tags"], "definition.object_tags")
        if type_id != "Explicit":
            raise ExecutionContractError(
                "INVALID_REQUEST", "object-scoped entity assignment is only supported by Explicit selections"
            )
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "object-scoped entity assignment (Selection.set(<object>, <entities>)) needs the object's entity "
            "ids; the verified GeomObjectSelection setters are object-tag based and this layer has no verified "
            "read-only object->entity mapping for a component selection",
        )
    properties = dict(out.get("properties") or {})
    wrapper = definition.get("properties")
    if wrapper is not None:
        nested = property_definition(wrapper, "definition.properties")
        duplicates = sorted(set(nested) & set(properties))
        if duplicates:
            raise ExecutionContractError("INVALID_REQUEST", f"selection properties are given twice: {duplicates}")
        properties.update(nested)
    flat = {name: value for name, value in definition.items()
            if name in _selection_property_allowance(type_id) and name not in _SELECTION_DEFINITION_KEYS}
    duplicates = sorted(set(flat) & set(properties))
    if duplicates:
        raise ExecutionContractError("INVALID_REQUEST", f"selection properties are given twice: {duplicates}")
    properties.update(flat)
    duplicates = sorted(set(region_properties) & set(properties))
    if duplicates:
        raise ExecutionContractError("INVALID_REQUEST", f"selection properties are given twice: {duplicates}")
    properties.update(region_properties)
    if properties:
        out["properties"] = properties
    if "condition" in properties and properties["condition"] not in SPATIAL_CONDITIONS:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"definition.condition must be one of {sorted(SPATIAL_CONDITIONS)}",
        )
    return out


def _apply_selection_definition(node_path: Mapping[str, Any], node: Any, worker: Any, model_tag: str,
                                type_id: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    parsed = _split_selection_definition(definition, type_id)
    applied: list[dict[str, Any]] = []
    if "label" in parsed:
        _call(node, "label", parsed["label"])
        applied.append({"assignment": "label", "readback": call_probe(node, "label")["value"]})
    if "inherit" in parsed:
        _call(node, "inherit", parsed["inherit"])
        applied.append({"assignment": "inherit", "requested": parsed["inherit"],
                        "readback": call_probe(node, "isInheriting")["value"]})
    entity_result: dict[str, Any] | None = None
    properties_result: dict[str, Any] | None = None
    if "properties" in parsed:
        payload = definition_properties(node, parsed["properties"], _selection_property_allowance(type_id),
                                       label="definition.properties")
        write = _property_write(node_path, worker, model_tag, payload)
        properties_result = write
        if write["failed"] or write["execution_state_unknown"]:
            return {"type_id": type_id, "entity_assignment": None, "properties": write, "applied": applied,
                    "failed": write["failed"], "not_executed": write["not_executed"],
                    "execution_state_unknown": write["execution_state_unknown"]}
    if "entities" in parsed:
        dimension = parsed.get("entity_dimension")
        if dimension is None:
            raise ExecutionContractError(
                "INVALID_REQUEST", "definition.entities requires definition.entity_dimension"
            )
        geometry = parsed.get("geometry")
        if geometry:
            _call(node, "geom", geometry, dimension)
        else:
            _call(node, "geom", dimension)
        entities = parsed["entities"]
        _call(node, "set", list(entities))
        readback = require_entity_id_array(_call(node, "entities"), "entities()", allow_empty=True)
        if sorted(readback) != sorted(entities):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"selection entity readback {readback} does not match the requested entities {entities}",
            )
        entity_result = {"entities": entities, "readback": readback, "entity_dimension": dimension,
                         "geometry": geometry}
    return {
        "type_id": type_id,
        "applied": applied,
        "entity_assignment": entity_result,
        "properties": properties_result,
        "failed": properties_result["failed"] if properties_result else [],
        "not_executed": properties_result["not_executed"] if properties_result else [],
        "execution_state_unknown": bool(properties_result["execution_state_unknown"]) if properties_result else False,
    }


def selection_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    parent, container, scope = _scope_container(worker, model_tag, args, "selection")
    rows = []
    for tag in tag_list(container, collection_spec("selection")):
        node = child_node(worker, model_tag, parent, "selection", tag)
        entities_probe = call_probe(node, "entities")
        entities = list(entities_probe["value"]) if entities_probe["ok"] and isinstance(entities_probe["value"], (list, tuple)) else None
        rows.append({
            "tag": tag,
            "type_id": node_type(node),
            "entity_dimension": call_probe(node, "dim")["value"],
            "dimension": call_probe(node, "dimension")["value"],
            "entity_count": len(entities) if entities is not None else None,
            "entities_sha256": entity_list_hash(entities) if entities is not None else None,
            "entities_error": None if entities_probe["ok"] else entities_probe["error"],
            "is_inheriting": call_probe(node, "isInheriting")["value"],
            "geometry": probe_geometry_tag(node),
            "named": call_probe(node, "named")["value"],
        })
    return {"scope": scope, "selections": rows, "count": len(rows),
            "notes": ["entity ids are geometry-version dependent; no cross-revision stability is promised"]}


def selection_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "type_id", "definition"),
                               ("component", "tag", "type_id", "definition"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    type_id = require_string(args["type_id"], "type_id", max_length=64)
    definition = require_mapping(args["definition"], "definition")
    _require_component(worker, model_tag, component)
    parent = _component_path(component)
    container = _call(component_node(worker, model_tag, component), "selection")
    # Validate the definition before the node exists so a bad request cannot
    # leave a half-configured selection behind.
    _split_selection_definition(definition, type_id)
    created = node_create(worker, model_tag, parent, "selection", tag, type_id)
    node = child_node(worker, model_tag, parent, "selection", tag)
    applied = _apply_selection_definition(created["path"], node, worker, model_tag, type_id, definition)
    entities_probe = call_probe(node, "entities")
    result: dict[str, Any] = {
        "component": component,
        "path": created["path"],
        "tag": tag,
        "type_id": type_id,
        "type_readback": created["type_readback"],
        "definition": applied,
        "entities": list(entities_probe["value"]) if entities_probe["ok"] and isinstance(entities_probe["value"], (list, tuple)) else None,
        "entity_dimension": call_probe(node, "dim")["value"],
        "properties": (applied.get("properties") or {}).get("readback_values", {}),
        "applied": applied.get("applied", []),
        "failed": applied.get("failed", []),
        "not_executed": applied.get("not_executed", []),
    }
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          bool(applied.get("execution_state_unknown"))))
    return result


def selection_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag"), ("component", "tag"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    comp = _require_component(worker, model_tag, component)
    container = _call(comp, "selection")
    probe = call_probe(container, "tags")
    if probe["ok"] and tag not in [str(item) for item in probe["value"]]:
        raise node_not_found(f"selection {tag!r} does not exist in {component!r}")
    node = child_node(worker, model_tag, _component_path(component), "selection", tag)
    type_id = node_type(node)
    entities_probe = call_probe(node, "entities")
    entities = list(entities_probe["value"]) if entities_probe["ok"] and isinstance(entities_probe["value"], (list, tuple)) else None
    properties: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    if type_id in SELECTION_REGION_PROPERTIES or type_id == "Explicit":
        names = sorted(_selection_property_allowance(type_id))
        properties = property_read_rows(node, names)
        metadata = {
            name: {"kind": row.get("kind"), "shape_rank": row.get("shape_rank"),
                   "value_type": row.get("value_type"), "allowed_values": row.get("allowed_values")}
            for name, row in _metadata_rows(node, names).items()
        }
    return {
        "component": component,
        "tag": tag,
        "path": path_with_segment(_component_path(component), "selection", tag),
        "type_id": type_id,
        "entity_dimension": call_probe(node, "dim")["value"],
        "dimension": call_probe(node, "dimension")["value"],
        "entities": entities,
        "entity_count": len(entities) if entities is not None else None,
        "entities_sha256": entity_list_hash(entities) if entities is not None else None,
        "entities_error": None if entities_probe["ok"] else entities_probe["error"],
        "state": selection_state(node),
        "properties": properties,
        "property_metadata": metadata,
        "notes": ["entity ids are geometry-version dependent; no cross-revision stability is promised"],
    }


def selection_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "definition"), ("component", "tag", "definition"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    definition = require_mapping(args["definition"], "definition")
    comp = _require_component(worker, model_tag, component)
    container = _call(comp, "selection")
    tag_probe = call_probe(container, "tags")
    if tag_probe["ok"] and tag not in [str(item) for item in tag_probe["value"]]:
        raise node_not_found(f"selection {tag!r} does not exist in {component!r}")
    node = child_node(worker, model_tag, _component_path(component), "selection", tag)
    type_id = node_type(node)
    if type_id is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the selection type could not be read (getType failed), so the documented property vocabulary "
            "cannot be applied; refusing the write",
        )
    before_probe = call_probe(node, "entities")
    before = sorted(before_probe["value"]) if before_probe["ok"] and isinstance(before_probe["value"], (list, tuple)) else None
    _split_selection_definition(definition, type_id)
    applied = _apply_selection_definition(path_with_segment(_component_path(component), "selection", tag),
                                          node, worker, model_tag, type_id, definition)
    after_probe = call_probe(node, "entities")
    after = sorted(after_probe["value"]) if after_probe["ok"] and isinstance(after_probe["value"], (list, tuple)) else None
    added = None if before is None or after is None else sorted(set(after) - set(before))
    removed = None if before is None or after is None else sorted(set(before) - set(after))
    result: dict[str, Any] = {
        "component": component,
        "tag": tag,
        "path": path_with_segment(_component_path(component), "selection", tag),
        "type_id": type_id,
        "definition": applied,
        "entity_changes": {"before": before, "after": after, "added": added, "removed": removed,
                           "unchanged": bool(before is not None and before == after)},
        "properties": (applied.get("properties") or {}).get("readback_values", {}),
        "applied": applied.get("applied", []),
        "failed": applied.get("failed", []),
        "not_executed": applied.get("not_executed", []),
    }
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          bool(applied.get("execution_state_unknown"))))
    return result


def selection_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag"), ("component", "tag"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    _require_component(worker, model_tag, component)
    removed = node_remove(worker, model_tag, _component_path(component), "selection", tag)
    return {
        **removed,
        "component": component,
        "reference_risk": {"level": "UNSCANNED",
                           "note": "removing a named selection does not scan for the features that referenced it"},
    }


def selection_entities(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("selection", "cursor", "limit"), ("selection",))
    limit = args.get("limit")
    limit = 100 if limit is None else require_int(limit, "limit", minimum=1, maximum=500)
    resolved = resolve_selection_entities(worker, model_tag, args["selection"])
    entities = list(resolved.get("entities") or [])
    scope_hash = _sha256_json({"selection": resolved["kind"], "component": resolved.get("component"),
                               "geometry": resolved.get("geometry"),
                               "entity_dimension": resolved.get("entity_dimension"),
                               "selection_tag": resolved.get("selection_tag"),
                               "entities_sha256": entity_list_hash(entities)})
    context = _cursor_context(worker, model_tag, arguments.get("model_revision"), "selection.entities", scope_hash)
    offset = 0
    cursor = args.get("cursor")
    if cursor is not None:
        payload = _cursor_decode(cursor)
        _cursor_verify(payload, context)
        offset = int(payload.get("offset", 0) or 0)
        if offset < 0 or offset > len(entities):
            raise ExecutionContractError("INVALID_CURSOR", "cursor offset is outside the entity list")
    page = entities[offset:offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < len(entities)
    return {
        "selection": resolved,
        "component": resolved.get("component"),
        "geometry": resolved.get("geometry"),
        "entity_dimension": resolved.get("entity_dimension"),
        "entities_sha256": entity_list_hash(entities),
        "total_count": len(entities),
        "entities": page,
        "returned": len(page),
        "limit": limit,
        "has_more": has_more,
        "next_cursor": _cursor_encode({**context, "offset": next_offset}) if has_more else None,
        "model_revision": context.get("model_revision"),
        "notes": resolved.get("notes", []) + [
            "entity ids are bound to the geometry revision that produced them (entities_sha256); a resume "
            "with a stale cursor is refused instead of restarted",
        ],
    }


def selection_measure(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("selection", "metrics"), ("selection", "metrics"))
    metrics = require_string_array(args["metrics"], "metrics")
    unsupported = [name for name in metrics if name not in MEASURE_METRICS]
    if unsupported:
        hint = ""
        if "centroid" in unsupported:
            hint = ("; 'centroid' has no verified getter - GeomMeasureBase exposes getVtxCoord (average of "
                    "selected *vertices*) only, so it is refused instead of approximated")
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"unsupported measure metrics {unsupported}{hint}; verified metrics: {sorted(MEASURE_METRICS)}",
        )
    resolved = resolve_selection_entities(worker, model_tag, args["selection"])
    component = resolved.get("component")
    if not component:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "selection.measure needs the component: pass selection.component (the measurement tool is reached "
            "as component.measure())",
        )
    entities = list(resolved.get("entities") or [])
    dimension = resolved.get("entity_dimension")
    if dimension is None:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "selection.measure needs the entity dimension: pass selection.entity_dimension",
        )
    comp = component_node(worker, model_tag, component)
    measure, _selection = measure_selection(comp, int(dimension), entities)
    geometry_tag = resolved.get("geometry") or probe_geometry_tag(_selection)
    length_unit = None
    if geometry_tag:
        try:
            length_unit = geometry_length_unit(_geometry_for(worker, model_tag, component, geometry_tag))
        except ExecutionContractError:
            length_unit = None
    results: dict[str, Any] = {}
    for name in metrics:
        getter = MEASURE_METRICS[name]
        probe = call_probe(measure, getter)
        if not probe["ok"]:
            results[name] = {"error": probe["error"]}
            continue
        typed = typed_value_from_engine(probe["value"])
        results[name] = {
            "value": typed,
            "length_unit": length_unit,
            "length_unit_power": _metric_length_power(name),
            "unit_policy": "values are reported in the geometry's own length unit; no conversion",
        }
    return {
        "selection": resolved,
        "component": component,
        "geometry": geometry_tag,
        "entity_dimension": dimension,
        "entity_count": len(entities),
        "metrics": results,
        "measurement_method": "model.component(<ctag>).measure() -> GeomMeasureFinal (finalized geometry)",
        "isolation_required": True,
        "notes": [
            "the measurement tool's selection state is transient model state, so the control plane must run "
            "this operation isolated (selection.measure is a catalogue EVALUATE effect)",
            "getBoundingBox() is returned verbatim: no offline source documents the entry ordering, so this "
            "layer does not interpret it",
        ],
    }


def _metric_length_power(name: str) -> int | None:
    return {"area": 2, "boundary_area": 2, "length": 1, "perimeter": 1, "volume": 3,
            "boundary_volume": 3}.get(name)


def _geometry_for(worker: Any, model_tag: str, component: str, geometry: str) -> Any:
    return _require_geometry(worker, model_tag, component, geometry)


_SPATIAL_QUERY_SPECS: dict[str, dict[str, Any]] = {
    "box": {"selection_type": "Box", "required": ("xmin", "xmax")},
    "ball": {"selection_type": "Ball", "required": ("r",)},
    "cylinder": {"selection_type": "Cylinder", "required": ("r",)},
    "disk": {"selection_type": "Disk", "required": ("r", "posx", "posy")},
}


def selection_query_spatial(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "geometry", "dimension", "query", "tolerance"),
                               ("component", "geometry", "dimension", "query", "tolerance"))
    component = validate_tag(args["component"], "component")
    geometry = validate_tag(args["geometry"], "geometry")
    dimension = require_int(args["dimension"], "dimension", minimum=0, maximum=3)
    query = require_mapping(args["query"], "query")
    tolerance = quantity(args["tolerance"], "tolerance")
    geom = _require_geometry(worker, model_tag, component, geometry)
    comp = component_node(worker, model_tag, component)

    kind = query.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ExecutionContractError("INVALID_REQUEST", "query.kind is required")
    condition = query.get("condition", "intersects")
    if condition not in SPATIAL_CONDITIONS:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"query.condition must be one of {sorted(SPATIAL_CONDITIONS)}"
        )
    if kind != "all":
        spec = _SPATIAL_QUERY_SPECS.get(kind)
        if spec is None:
            raise ExecutionContractError(
                "API_UNSUPPORTED",
                f"query.kind {kind!r} has no verified spatial-selection mapping; supported kinds: "
                f"{sorted(list(_SPATIAL_QUERY_SPECS) + ['all'])} (point/normal/measure queries are not "
                "documented as coordinate-based selection conditions)",
            )
        unknown = sorted(set(query) - {"kind", "condition"} -
                         {name for name in SELECTION_REGION_PROPERTIES[spec["selection_type"]]} -
                         {"center"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"query has unsupported fields for {kind!r}: {unknown}")
        for required in spec["required"]:
            if required not in query:
                raise ExecutionContractError("INVALID_REQUEST", f"query.{required} is required for kind {kind!r}")
        region = {name: float(query[name]) for name in query
                  if name in SELECTION_REGION_PROPERTIES[spec["selection_type"]]}
        for key, value in list(region.items()):
            if isinstance(query.get(key), bool) or not isinstance(query.get(key), (int, float)):
                raise ExecutionContractError("INVALID_REQUEST", f"query.{key} must be a number")
        if kind == "box":
            if "ymin" not in region or "ymax" not in region:
                raise ExecutionContractError("INVALID_REQUEST", "query requires ymin/ymax for a box")
            if region["xmin"] > region["xmax"] or region["ymin"] > region["ymax"]:
                raise ExecutionContractError("INVALID_REQUEST", "box minimums must not exceed maximums")
            if "zmin" in region or "zmax" in region:
                if region.get("zmin", 0.0) > region.get("zmax", 0.0):
                    raise ExecutionContractError("INVALID_REQUEST", "box zmin must not exceed zmax")
        if kind == "ball":
            center = query.get("center")
            if center is not None:
                if not isinstance(center, Sequence) or isinstance(center, (str, bytes, Mapping)):
                    raise ExecutionContractError("INVALID_REQUEST", "query.center must be an array of numbers")
                for axis, name in enumerate(("posx", "posy", "posz")):
                    if axis < len(center):
                        region[name] = float(center[axis])
        if region.get("r", 0.0) < 0:
            raise ExecutionContractError("INVALID_REQUEST", "query.r must be non-negative")
        selection_type = spec["selection_type"]
    else:
        unknown = sorted(set(query) - {"kind"})
        if unknown:
            raise ExecutionContractError("INVALID_REQUEST", f"query has unsupported fields for kind 'all': {unknown}")
        region = {}
        selection_type = "Explicit"

    sdim = geometry_sdim(geom)
    if sdim is not None and dimension > sdim:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"dimension {dimension} exceeds the geometry space dimension {sdim}"
        )
    length_unit = geometry_length_unit(geom)
    if length_unit is None:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the geometry length unit cannot be read (GeomSequence.lengthUnit() failed or is not allow-listed), "
            "so the tolerance unit cannot be verified without conversion; refusing the query",
        )
    if tolerance["unit"] != length_unit:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"tolerance unit {tolerance['unit']!r} does not match the geometry length unit {length_unit!r}; "
            "this layer never converts units, so the query is refused",
        )
    inflation = abs(float(tolerance["value"]))
    if kind == "box":
        region["xmin"] -= inflation
        region["xmax"] += inflation
        region["ymin"] -= inflation
        region["ymax"] += inflation
        if "zmin" in region:
            region["zmin"] -= inflation
        if "zmax" in region:
            region["zmax"] += inflation
    elif kind in {"ball", "disk", "cylinder"}:
        region["r"] = float(region.get("r", 0.0)) + inflation

    container = _call(comp, "selection")
    existing = [str(item) for item in tag_list(container, collection_spec("selection"))]
    temp_tag = None
    for suffix in range(0, 8):
        candidate = "mcpq" + _sha256_json({"component": component, "geometry": geometry, "kind": kind,
                                           "query": {k: region[k] for k in sorted(region)},
                                           "dimension": dimension})[:8]
        candidate = f"{candidate}_{suffix}" if suffix else candidate
        if candidate not in existing:
            temp_tag = candidate
            break
    if temp_tag is None:
        raise tag_conflict(
            "no unused temporary query-selection tag is available; refusing to reuse one"
        )
    node_path = path_with_segment(_component_path(component), "selection", temp_tag)
    _call(container, "create", temp_tag, selection_type)
    created_tags = [str(item) for item in tag_list(container, collection_spec("selection"))]
    if temp_tag not in created_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "the temporary query selection was created but the readback does not show it"
        )
    node = child_node(worker, model_tag, _component_path(component), "selection", temp_tag)
    write = {}
    try:
        properties: dict[str, Any] = {"entitydim": dimension}
        if kind != "all":
            properties["condition"] = condition
            properties.update(region)
        payload = definition_properties(node, properties, _selection_property_allowance(selection_type),
                                        label="query")
        write = _property_write(node_path, worker, model_tag, payload)
        if write["failed"] or write["execution_state_unknown"]:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN" if write["execution_state_unknown"] else "ENGINE_CALL_FAILED",
                "the temporary query selection could not be configured",
            )
        if selection_type == "Explicit":
            _call(node, "all")
        entities = require_entity_id_array(_call(node, "entities"), "entities()", allow_empty=True)
    finally:
        cleanup_error = None
        try:
            _call(container, "remove", temp_tag)
            remaining = [str(item) for item in tag_list(container, collection_spec("selection"))]
            if temp_tag in remaining:
                cleanup_error = "the temporary query selection still appears in the tag readback"
        except ExecutionContractError as exc:
            cleanup_error = f"{exc.code}: {exc}"
        if cleanup_error:
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"temporary query selection {temp_tag!r} could not be removed ({cleanup_error}); the model "
                "tree still holds it and it must be reconciled manually",
            )
    return {
        "component": component,
        "geometry": geometry,
        "dimension": dimension,
        "query": {"kind": kind, "condition": condition if kind != "all" else None},
        "region": region,
        "region_unit": length_unit,
        "tolerance": {"value": tolerance["value"], "unit": tolerance["unit"],
                      "applied_as": "region inflation, in the geometry length unit"},
        "entities": entities,
        "entity_count": len(entities),
        "empty": not entities,
        "temporary_node": {"tag": temp_tag, "type_id": selection_type, "created": True, "removed": True,
                           "properties": write.get("readback_values", {})},
        "method": "ephemeral component selection node (Programming Reference Coordinate-Based Selections), "
                  "read through entities() and removed with a tag readback",
        "isolation_required": True,
        "ok": True,
        "status": "APPLIED",
    }


def selection_adjacency(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("selection", "target_dimension"), ("selection", "target_dimension"))
    target_dimension = require_int(args["target_dimension"], "target_dimension", minimum=0, maximum=3)
    resolved = resolve_selection_entities(worker, model_tag, args["selection"])
    component = resolved.get("component")
    if not component:
        raise ExecutionContractError("INVALID_REQUEST", "selection.adjacency needs selection.component")
    dimension = resolved.get("entity_dimension")
    if dimension is None:
        raise ExecutionContractError(
            "INVALID_REQUEST", "selection.adjacency needs the entity dimension (selection.entity_dimension)"
        )
    entities = list(resolved.get("entities") or [])
    geometry = resolved.get("geometry")
    geometry_selection = "from_selection_spec"
    if not geometry:
        geom_list_probe = call_probe(component_node(worker, model_tag, component), "geom")
        tags = [str(item) for item in tag_list(geom_list_probe["value"])] if geom_list_probe["ok"] and geom_list_probe["value"] is not None else []
        if len(tags) == 1:
            geometry = tags[0]
            geometry_selection = "single_geometry_auto_selected"
        else:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"selection.geometry is required when the component has {len(tags)} geometries",
            )
    geom = _require_geometry(worker, model_tag, component, geometry)
    adjacency = _call(geom, "getAdj", dimension, target_dimension)
    if adjacency is None:
        raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "getAdj() returned no adjacency matrix")
    rows = list(adjacency)
    per_entity = []
    neighbors: set[int] = set()
    for entity in entities:
        if entity >= len(rows):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"the adjacency matrix has {len(rows)} rows and does not cover entity {entity}; the geometry "
                "is probably not finalized",
            )
        row = [int(item) for item in (rows[entity] or [])]
        neighbors.update(row)
        per_entity.append({"entity": entity, "adjacent": sorted(row)})
    truncated = len(per_entity) > 1000
    return {
        "selection": resolved,
        "component": component,
        "geometry": geometry,
        "geometry_selection": geometry_selection,
        "entity_dimension": dimension,
        "target_dimension": target_dimension,
        "source_entities": entities,
        "adjacent_entities": sorted(neighbors),
        "adjacent_count": len(neighbors),
        "per_entity": per_entity[:1000],
        "per_entity_truncated": truncated,
        "matrix_rows": len(rows),
        "method": "GeomInfo.getAdj(fromDim, toDim): a[fromIdx] = entities of toDim adjacent to entity fromIdx",
        "notes": [
            "orientation flags (getAdjOrient, 1/-1/2) are available in the API but are not read here to keep "
            "the call count bounded by the selection size",
        ],
    }


def selection_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("selection", "expectations"), ("selection", "expectations"))
    expectations = require_mapping(args["expectations"], "expectations")
    allowed_keys = {"non_empty", "entity_dimension", "count", "min_count", "max_count", "measures",
                    "entity_ids_in_range"}
    unknown = sorted(set(expectations) - allowed_keys)
    if unknown:
        hint = ""
        if "bounding_box" in unknown:
            hint = ("; a bounding-box expectation is refused because no offline source documents the entry "
                    "ordering of getBoundingBox() - use selection.measure to read it verbatim")
        raise ExecutionContractError(
            "API_UNSUPPORTED" if "bounding_box" in unknown else "INVALID_REQUEST",
            f"expectations has unsupported fields: {unknown}{hint}",
        )
    non_empty = expectations.get("non_empty")
    if non_empty is not None:
        require_bool(non_empty, "expectations.non_empty")
    expected_dimension = expectations.get("entity_dimension")
    if expected_dimension is not None:
        require_int(expected_dimension, "expectations.entity_dimension", minimum=0, maximum=3)
    for key in ("count", "min_count", "max_count"):
        if expectations.get(key) is not None:
            require_int(expectations[key], f"expectations.{key}", minimum=0)
    measures = expectations.get("measures")
    if measures is not None:
        measures = require_mapping(measures, "expectations.measures")
        for name, row in measures.items():
            if name not in MEASURE_METRICS:
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"expectations.measures.{name} is not a verified metric: {sorted(MEASURE_METRICS)}",
                )
            payload = require_mapping(row, f"expectations.measures.{name}")
            extra = sorted(set(payload) - {"value", "unit", "abs_tol", "rel_tol"})
            if extra:
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"expectations.measures.{name} has unsupported fields: {extra}"
                )
            if payload.get("value") is None:
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"expectations.measures.{name}.value is required"
                )
    if expectations.get("entity_ids_in_range") is not None:
        require_bool(expectations["entity_ids_in_range"], "expectations.entity_ids_in_range")

    resolved = resolve_selection_entities(worker, model_tag, args["selection"])
    entities = list(resolved.get("entities") or [])
    dimension = resolved.get("entity_dimension")
    checks: list[dict[str, Any]] = []
    if non_empty is not None:
        checks.append({"check": "non_empty", "expected": non_empty, "actual": bool(entities),
                       "status": "PASS" if bool(entities) == bool(non_empty) else "FAIL"})
    if expected_dimension is not None:
        checks.append({"check": "entity_dimension", "expected": expected_dimension, "actual": dimension,
                       "status": "PASS" if dimension == expected_dimension else "FAIL"})
    for key in ("count", "min_count", "max_count"):
        if expectations.get(key) is not None:
            expected = expectations[key]
            actual = len(entities)
            if key == "count":
                ok = actual == expected
            elif key == "min_count":
                ok = actual >= expected
            else:
                ok = actual <= expected
            checks.append({"check": key, "expected": expected, "actual": actual,
                           "status": "PASS" if ok else "FAIL"})

    component = resolved.get("component")
    geometry = resolved.get("geometry")
    measure_results: dict[str, Any] = {}
    if measures and component:
        comp = component_node(worker, model_tag, component)
        if dimension is None:
            checks.append({"check": "measures", "status": "ERROR",
                           "reason": "the entity dimension is unknown, so the measurement tool cannot be set"})
        else:
            measure, selection_node = measure_selection(comp, int(dimension), entities)
            if not geometry:
                geometry = probe_geometry_tag(selection_node)
            length_unit = None
            if geometry:
                try:
                    length_unit = geometry_length_unit(_geometry_for(worker, model_tag, component, geometry))
                except ExecutionContractError:
                    length_unit = None
            for name, payload in measures.items():
                probe = call_probe(measure, MEASURE_METRICS[name])
                if not probe["ok"]:
                    measure_results[name] = {"status": "ERROR", "error": probe["error"]}
                    continue
                actual = probe["value"]
                if isinstance(actual, (list, tuple)):
                    measure_results[name] = {"status": "NOT_COMPARABLE", "actual": list(actual),
                                             "reason": "the metric returns an array; compare it explicitly"}
                    continue
                requested = float(payload["value"])
                abs_tol = float(payload.get("abs_tol", 0.0) or 0.0)
                rel_tol = float(payload.get("rel_tol", 0.0) or 0.0)
                unit = payload.get("unit")
                unit_ok = unit is None or (length_unit is not None and unit == length_unit)
                delta = abs(float(actual) - requested)
                tolerance = max(abs_tol, rel_tol * abs(requested))
                matched = delta <= tolerance and unit_ok
                measure_results[name] = {
                    "status": "PASS" if matched else "FAIL",
                    "expected": requested, "actual": actual, "delta": delta, "tolerance": tolerance,
                    "unit_requested": unit, "length_unit": length_unit, "unit_policy": "no conversion",
                }
                checks.append({"check": f"measure.{name}", "expected": requested, "actual": actual,
                               "status": "PASS" if matched else "FAIL"})

    if expectations.get("entity_ids_in_range") is True:
        if not component or dimension is None:
            checks.append({"check": "entity_ids_in_range", "status": "ERROR",
                           "reason": "component or entity_dimension is unknown"})
        else:
            comp = component_node(worker, model_tag, component)
            available = set(all_entities_of_dimension(comp, int(dimension)))
            out_of_range = [entity for entity in entities if entity not in available]
            checks.append({"check": "entity_ids_in_range", "expected": True,
                           "actual": not out_of_range, "out_of_range": out_of_range,
                           "status": "PASS" if not out_of_range else "FAIL"})

    failed = [check for check in checks if check["status"] not in {"PASS"}]
    verdict = "FAIL" if failed else "PASS"
    return {
        "selection": resolved,
        "component": component,
        "geometry": geometry,
        "entity_dimension": dimension,
        "entity_count": len(entities),
        "checks": checks,
        "measures": measure_results,
        "verdict": verdict,
        "ok": verdict == "PASS",
        "status": "APPLIED" if verdict == "PASS" else "FAILED",
        "failure_count": len(failed),
        "partial_change": False,
        "execution_state_unknown": False,
        "isolation_required": True,
        "notes": [
            "a failing required check never reports PASS; the verdict is data, not an exception, so the caller "
            "sees every check result",
        ],
    }


# ---------------------------------------------------------------------------
# publishing table
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "parameter.list": parameter_list,
    "parameter.get": parameter_get,
    "parameter.set": parameter_set,
    "parameter.remove": parameter_remove,
    "parameter.group_manage": parameter_group_manage,
    "variable.list": variable_list,
    "variable.get": variable_get,
    "variable.set": variable_set,
    "variable.remove": variable_remove,
    "variable.group_create": variable_group_create,
    "variable.selection_set": variable_selection_set,
    "function.list": function_list,
    "function.create": function_create,
    "function.inspect": function_inspect,
    "function.update": function_update,
    "function.remove": function_remove,
    "function.data_import": function_data_import,
    "function.data_reload": function_data_reload,
    "function.evaluate": function_evaluate,
    "selection.list": selection_list,
    "selection.create": selection_create,
    "selection.inspect": selection_inspect,
    "selection.update": selection_update,
    "selection.remove": selection_remove,
    "selection.entities": selection_entities,
    "selection.measure": selection_measure,
    "selection.query_spatial": selection_query_spatial,
    "selection.adjacency": selection_adjacency,
    "selection.validate": selection_validate,
}

__all__ = ["OPERATIONS"]
