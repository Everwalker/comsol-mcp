"""Strict COMSOL definition validation and semantic selection rebinding.

This module is intentionally independent of the production dispatcher while
the routes are being reviewed.  It implements the documented, bounded
COMSOL 6.4 subset below; callers must not treat incomplete observations as a
scientific or geometric acceptance result.

Verified COMSOL 6.4 sources (Build 293):

* Programming Reference, ``model.func()`` / Interpolation properties,
  ``doc/help/wtpwebapps/ROOT/doc/com.comsol.help.comsol/comsol_api_general.47.34.html``,
  chunk 16754, SHA-256
  ``c0b0ddce365ed8da5a8d8e45818ab0b056e552aaa01c99ca22e1f8be7c1e0edf``:
  ``argrange`` is one two-expression interval per function argument;
  ``extrap`` values are ``none|const|interior|linear|value``; ``interp``
  values include ``neighbor|linear|piecewisecubic|cubicspline``.
* Reference Manual, ``USER-DEFINED FUNCTIONS``, PDF page 403, chunk 12488,
  SHA-256 ``3dacc33243911a98cbcac19c6b83c9b662dd0b7c56b880b3a9e6a2ab602e7106``:
  nearest-neighbor selects a nearest defined value, piecewise-cubic has
  continuous first derivatives, and cubic spline has continuous second
  derivatives.  Those are interpolation-method guarantees, not proof of
  source-data quality.
* API ``FunctionFeature``, chunk 22848, SHA-256
  ``c99bb2a0c8134c5d2845ed55e76f894875de1f830d3484719c4d6822d6c9ae5d``:
  ``runTest()`` performs a computation to assess prior construction, so this
  module deliberately does not invoke it during validation.
* Programming Reference, ``model.cpl()`` / General Extrusion properties,
  chunk 16740, SHA-256
  ``29f57502ca11c069bb5713dcbf83da23316e6af13759a00f4f07541a69a16a29``:
  ``dstmap`` maps destination coordinates; ``srcframe`` identifies the source
  mesh frame; with ``usesrcmap=off``, ``dstmap`` maps destination to source
  coordinates; mesh-search success depends on the source selection and mesh.
  Therefore numeric map agreement at samples never proves source-mesh coverage.
* API ``GeomObjectSelection``, chunk 22884, SHA-256
  ``9eae62b35183321a2ce50763b5ad71ed27fdda2530ec24f50280927a7301d3a0``:
  documents geometry-object selection accessors and mutation methods.  The
  rebind operation below is narrower: an existing component ``Box``/``Ball``
  feature whose geometry identity and length unit can be read back.

Unsupported function kinds, nonliteral argument intervals, expression-based
coordinate maps outside the arithmetic subset, source-map inversion, missing
geometry identity, coordinate-unit conversion, and unobserved destination
frame/source-mesh coverage fail closed or remain explicitly UNVERIFIED.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ._execution_contract import PreWriteRefusal
from ._g2_contract import ExecutionContractError
from ._g3_common import (
    bound_model,
    call_probe,
    component_node,
    geometry_length_unit,
    node_type,
    operation_arguments,
    probe_geometry_tag,
    reject_unknown_keys,
    require_bool,
    require_mapping,
    require_number,
    require_string,
    require_string_array,
    resolve_path,
    validate_selection_spec,
)
from . import _g3_w13 as _w13


_INTERPOLATION_METHOD_GRADE = {
    "neighbor": -1,
    "linear": 0,
    "piecewisecubic": 1,
    "cubicspline": 2,
}
_CONTINUITY_GRADE = {"C0": 0, "C1": 1, "C2": 2}
_EXTRAPOLATION_METHODS = frozenset({"none", "const", "interior", "linear", "value"})
_DOMAIN_LITERAL = re.compile(r"^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$")


def _strict_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a finite number")
    return number


def _read_property(node: Any, name: str) -> dict[str, Any]:
    """Use the node's advertised property type and matching getter."""
    row = _w13._function_evaluate_read_property(node, name)
    if not row.get("ok"):
        error = row.get("error") or {"code": "PROPERTY_UNAVAILABLE"}
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"COMSOL property {name!r} is not completely readable: {error.get('code')}",
            details={"property": name, "error": error},
        )
    return row


def _literal_range(raw: Any, *, arity: int) -> list[tuple[float, float]]:
    """Accept only the documented StringMatrix shape with literal bounds.

    COMSOL permits expressions in ``argrange``.  Evaluating arbitrary model
    expressions here could introduce undeclared dependencies, so this version
    supports finite numeric literals only and refuses the rest.
    """
    if not isinstance(raw, (list, tuple)) or len(raw) != arity:
        raise ExecutionContractError(
            "API_UNSUPPORTED", "Interpolation.argrange must read as one two-string row per argument"
        )
    result: list[tuple[float, float]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ExecutionContractError(
                "API_UNSUPPORTED", f"Interpolation.argrange[{index}] is not a pair of expressions"
            )
        pair: list[float] = []
        for side, expression in zip(("lower", "upper"), row):
            if not isinstance(expression, str) or _DOMAIN_LITERAL.fullmatch(expression.strip()) is None:
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"Interpolation.argrange[{index}].{side} is not a finite numeric literal; expression evaluation is not inferred",
                )
            value = float(expression)
            if not math.isfinite(value):
                raise ExecutionContractError("API_UNSUPPORTED", "Interpolation.argrange contains a non-finite literal")
            pair.append(value)
        if pair[0] >= pair[1]:
            raise ExecutionContractError("INVALID_DEFINITION", f"Interpolation.argrange[{index}] is not increasing")
        result.append((pair[0], pair[1]))
    return result


def _continuity(method: str) -> dict[str, Any]:
    grade = _INTERPOLATION_METHOD_GRADE.get(method)
    if grade is None:
        raise ExecutionContractError("API_UNSUPPORTED", f"Interpolation.interp={method!r} is outside the verified method table")
    return {
        "method": method,
        "guaranteed_minimum": None if grade < 0 else f"C{grade}",
        "guarantee_status": "NO_CONTINUITY_GUARANTEE" if grade < 0 else "METHOD_GUARANTEE",
        "data_continuity_independently_checked": False,
    }


def function_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate unary Interpolation domain, units, interpolation and extrapolation.

    ``checks`` is finite and optional:

    * ``domain``: ``{lower, upper, unit}`` expected bounds for the one argument.
    * ``continuity``: minimum required ``C0``, ``C1`` or ``C2`` method guarantee.
    * ``extrapolation``: nonempty list of permitted COMSOL extrapolation modes.

    The function is read-only. It samples the two configured endpoints and
    midpoint with the existing model expression evaluator.  Passing these
    checks does not certify arbitrary expressions, data provenance or global
    smoothness.
    """
    args = operation_arguments(arguments, ("path", "checks"), ("path",))
    checks = require_mapping(args.get("checks", {}), "checks")
    reject_unknown_keys(checks, ("domain", "continuity", "extrapolation", "continuity_probes"), "checks")
    expected_domain = checks.get("domain")
    if expected_domain is not None:
        expected_domain = require_mapping(expected_domain, "checks.domain")
        reject_unknown_keys(expected_domain, ("lower", "upper", "unit"), "checks.domain")
        if set(expected_domain) != {"lower", "upper", "unit"}:
            raise ExecutionContractError("INVALID_REQUEST", "checks.domain requires lower, upper and unit")
        expected_domain = {
            "lower": _strict_float(expected_domain["lower"], "checks.domain.lower"),
            "upper": _strict_float(expected_domain["upper"], "checks.domain.upper"),
            "unit": require_string(expected_domain["unit"], "checks.domain.unit", max_length=64),
        }
        if expected_domain["lower"] >= expected_domain["upper"]:
            raise ExecutionContractError("INVALID_REQUEST", "checks.domain.lower must be below upper")
    requested_continuity = checks.get("continuity")
    if requested_continuity is not None and (
        not isinstance(requested_continuity, str) or requested_continuity not in _CONTINUITY_GRADE
    ):
        raise ExecutionContractError("INVALID_REQUEST", "checks.continuity must be C0, C1 or C2")
    allowed_extrap = checks.get("extrapolation")
    if allowed_extrap is not None:
        allowed_extrap = require_string_array(allowed_extrap, "checks.extrapolation")
        if not allowed_extrap or set(allowed_extrap) - _EXTRAPOLATION_METHODS:
            raise ExecutionContractError("INVALID_REQUEST", "checks.extrapolation must be a nonempty list of documented modes")
    continuity_probes_raw = checks.get("continuity_probes", [])
    if not isinstance(continuity_probes_raw, Sequence) or isinstance(continuity_probes_raw, (str, bytes, Mapping)):
        raise ExecutionContractError("INVALID_REQUEST", "checks.continuity_probes must be an array")
    if len(continuity_probes_raw) > 32:
        raise ExecutionContractError("INVALID_REQUEST", "checks.continuity_probes may contain at most 32 entries")
    continuity_probes: list[dict[str, Any]] = []
    for index, row in enumerate(continuity_probes_raw):
        probe = require_mapping(row, f"checks.continuity_probes[{index}]")
        reject_unknown_keys(probe, ("at", "delta", "max_jump", "unit"), f"checks.continuity_probes[{index}]")
        if set(probe) != {"at", "delta", "max_jump", "unit"}:
            raise ExecutionContractError("INVALID_REQUEST", f"checks.continuity_probes[{index}] requires at, delta, max_jump and unit")
        item = {key: _strict_float(probe[key], f"checks.continuity_probes[{index}].{key}")
                for key in ("at", "delta", "max_jump")}
        item["unit"] = require_string(probe["unit"], f"checks.continuity_probes[{index}].unit", max_length=64)
        if item["delta"] <= 0 or item["max_jump"] < 0:
            raise ExecutionContractError("INVALID_REQUEST", f"checks.continuity_probes[{index}] delta must be positive and max_jump nonnegative")
        continuity_probes.append(item)

    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    if type_id != "Interpolation":
        raise PreWriteRefusal(
            "API_UNSUPPORTED",
            f"function.validate supports only COMSOL Interpolation nodes with a finite declared argument range; observed {type_id!r}",
        )
    arity = _w13._function_evaluate_arity(node, type_id)
    if arity != 1:
        raise PreWriteRefusal("API_UNSUPPORTED", "function.validate currently verifies one-dimensional Interpolation functions only")

    range_row = _read_property(node, "argrange")
    ranges = _literal_range(range_row["value"], arity=arity)
    argunit_row = _read_property(node, "argunit")
    raw_units = argunit_row["value"]
    if isinstance(raw_units, str):
        units = [raw_units]
    elif isinstance(raw_units, (list, tuple)) and len(raw_units) == arity and all(isinstance(item, str) for item in raw_units):
        units = list(raw_units)
    else:
        raise ExecutionContractError("API_UNSUPPORTED", "Interpolation.argunit does not provide one unit string per argument")
    # Reuse the evaluator's unit grammar/readback before any COMSOL expression call.
    units = _w13._function_evaluate_arg_units(node, arity)
    extrapolation = _read_property(node, "extrap")["value"]
    method = _read_property(node, "interp")["value"]
    if not isinstance(extrapolation, str) or extrapolation not in _EXTRAPOLATION_METHODS:
        raise ExecutionContractError("API_UNSUPPORTED", "Interpolation.extrap readback is outside the documented vocabulary")
    continuity = _continuity(method) if isinstance(method, str) else None
    if continuity is None:
        raise ExecutionContractError("API_UNSUPPORTED", "Interpolation.interp did not read back as a string")

    lower, upper = ranges[0]
    for index, probe in enumerate(continuity_probes):
        if not lower < probe["at"] < upper or probe["delta"] >= min(probe["at"] - lower, upper - probe["at"]):
            raise ExecutionContractError("INVALID_REQUEST", f"checks.continuity_probes[{index}] must straddle two points inside the declared range")
    checks_out: list[dict[str, Any]] = []
    if expected_domain is not None:
        matches = (
            expected_domain["lower"] == lower
            and expected_domain["upper"] == upper
            and expected_domain["unit"] == units[0]
        )
        checks_out.append({"check": "domain", "status": "PASS" if matches else "FAIL",
                           "expected": expected_domain, "actual": {"lower": lower, "upper": upper, "unit": units[0]}})
    if requested_continuity is not None:
        grade = _INTERPOLATION_METHOD_GRADE[method]
        matches = grade >= _CONTINUITY_GRADE[requested_continuity]
        checks_out.append({"check": "interpolation_method_continuity_order", "status": "PASS" if matches else "FAIL",
                           "requested_minimum": requested_continuity,
                           "method_guarantee": continuity["guaranteed_minimum"],
                           "scope": "documented interpolation kernel only"})
    if allowed_extrap is not None:
        matches = extrapolation in allowed_extrap
        checks_out.append({"check": "extrapolation", "status": "PASS" if matches else "FAIL",
                           "expected_any_of": allowed_extrap, "actual": extrapolation})

    # Three interior/configured samples establish that the real expression is
    # evaluable at these points.  They do not establish continuity or data
    # quality across the interval.
    sample_arguments = [
        {"value": point, "unit": units[0]} if units[0] else {"value": point}
        for point in (lower, (lower + upper) / 2.0, upper)
    ]
    sample_labels = ["lower", "midpoint", "upper"]
    # Probe the closest representable points on each side of both boundaries.
    # This records actual configured extrapolation behavior independently of
    # the interpolation method's continuity order.
    near_lower = math.nextafter(lower, upper)
    outside_lower = math.nextafter(lower, -math.inf)
    near_upper = math.nextafter(upper, lower)
    outside_upper = math.nextafter(upper, math.inf)
    boundary_points = [near_lower, outside_lower, near_upper, outside_upper]
    if all(math.isfinite(point) for point in boundary_points) and len(set(boundary_points)) == 4:
        sample_arguments.extend(
            ({"value": point, "unit": units[0]} if units[0] else {"value": point})
            for point in boundary_points
        )
        sample_labels.extend(["lower_inside_neighbor", "lower_outside_neighbor",
                              "upper_inside_neighbor", "upper_outside_neighbor"])
    else:
        boundary_points = []
    for probe in continuity_probes:
        label_index = len(sample_labels)
        sample_arguments.extend(
            ({"value": point, "unit": units[0]} if units[0] else {"value": point})
            for point in (probe["at"] - probe["delta"], probe["at"] + probe["delta"])
        )
        sample_labels.extend([f"continuity_probe_{label_index}_left",
                              f"continuity_probe_{label_index + 1}_right"])
    function_unit = _read_property(node, "fununit")["value"]
    if not isinstance(function_unit, str):
        raise ExecutionContractError("API_UNSUPPORTED", "Interpolation.fununit did not read back as a string")
    effective_function_unit = function_unit.strip() or "1"
    if any(probe["unit"] != effective_function_unit for probe in continuity_probes):
        raise ExecutionContractError("API_UNSUPPORTED", "continuity probe unit must exactly match COMSOL-read-back Interpolation.fununit")
    samples = _w13.function_evaluate(worker, model_tag, {
        "path": args["path"],
        "arguments": sample_arguments,
    })
    sample_rows = samples.get("results") if isinstance(samples, Mapping) else None
    primary_rows = sample_rows[:3] if isinstance(sample_rows, list) else None
    def sample_unit_matches(row: Any) -> bool:
        if not isinstance(row, Mapping) or row.get("unit_status") != "OK":
            return False
        actual_unit = row.get("unit")
        return actual_unit == function_unit if function_unit.strip() else actual_unit in (None, "", "1")

    samples_ok = (
        isinstance(primary_rows, list) and len(primary_rows) == 3
        and all(isinstance(row, Mapping) and row.get("value_status") == "OK" and sample_unit_matches(row)
                for row in primary_rows)
    )
    checks_out.append({"check": "configured_interval_sample_availability", "status": "PASS" if samples_ok else "FAIL",
                       "sample_count": len(primary_rows) if isinstance(primary_rows, list) else 0,
                       "sample_values": primary_rows})
    boundary_rows = sample_rows[3:7] if boundary_points and isinstance(sample_rows, list) else []
    boundary_complete = bool(boundary_points) and len(boundary_rows) == 4 and all(isinstance(row, Mapping) for row in boundary_rows)
    checks_out.append({"check": "actual_boundary_neighbor_behavior", "status": "OBSERVED" if boundary_complete else "UNVERIFIED",
                       "points": boundary_points, "labels": sample_labels[3:7] if boundary_points else [],
                       "samples": boundary_rows,
                       "expected_outside_mode": extrapolation,
                       "note": "Boundary probes do not establish behavior at every internal interpolation knot."})
    discontinuity_rows: list[dict[str, Any]] = []
    if isinstance(sample_rows, list) and len(sample_rows) == len(sample_arguments):
        offset = 7 if boundary_points else 3
        for index, probe in enumerate(continuity_probes):
            left, right = sample_rows[offset + index * 2:offset + index * 2 + 2]
            left_value = left.get("value") if isinstance(left, Mapping) else None
            right_value = right.get("value") if isinstance(right, Mapping) else None
            pair_ok = all(isinstance(row, Mapping) and row.get("value_status") == "OK" and sample_unit_matches(row)
                          for row in (left, right))
            numeric_pair = (
                pair_ok and isinstance(left_value, Mapping) and isinstance(right_value, Mapping)
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
                        for value in (left_value.get("real"), left_value.get("imag"),
                                      right_value.get("real"), right_value.get("imag")))
            )
            jump = math.hypot(float(right_value["real"]) - float(left_value["real"]),
                              float(right_value["imag"]) - float(left_value["imag"])) if numeric_pair else None
            discontinuity_rows.append({"at": probe["at"], "delta": probe["delta"],
                                       "max_jump": probe["max_jump"], "observed_jump": jump,
                                       "status": "PASS" if numeric_pair and jump <= probe["max_jump"] else
                                       ("FAIL" if numeric_pair else "UNVERIFIED"),
                                       "samples": [left, right]})
    elif continuity_probes:
        discontinuity_rows = [{"at": row["at"], "status": "UNVERIFIED", "observed_jump": None}
                              for row in continuity_probes]
    if continuity_probes:
        checks_out.append({"check": "caller_selected_local_continuity_probes",
                           "status": "PASS" if discontinuity_rows and all(row["status"] == "PASS" for row in discontinuity_rows)
                           else ("FAIL" if any(row["status"] == "FAIL" for row in discontinuity_rows) else "UNVERIFIED"),
                           "probes": discontinuity_rows,
                           "scope": "only caller-selected local pairs; no global continuity conclusion"})
    failed = any(row["status"] == "FAIL" for row in checks_out)
    global_continuity_verified = False
    bounded_checks_ok = not failed
    return {
        "schema_version": "comsol-mcp.function-validate/1.0.0",
        "path": path,
        "function_type": type_id,
        "arity": arity,
        "domain": {"lower": lower, "upper": upper, "unit": units[0], "source": "Interpolation.argrange + argunit"},
        "function_unit": function_unit,
        "continuity": {**continuity, "global_data_continuity": "UNVERIFIED",
                       "continuity_probe_scope": "caller-selected local samples only"},
        "extrapolation": {"method": extrapolation, "status": "OBSERVED_CONFIGURATION"},
        "checks": checks_out,
        "ok": bounded_checks_ok and global_continuity_verified,
        "bounded_checks_ok": bounded_checks_ok,
        "status": "VALIDATION_FAILED" if failed else "PARTIAL_UNVERIFIED",
        "partial_change": False,
        "execution_state_unknown": False,
        "limits": ["Only numeric-literal argrange bounds are evaluated.",
                   "Interpolation-method continuity order is not a proof of source-data or global function continuity.",
                   "Boundary-neighbor and caller-selected discontinuity probes are finite samples only.",
                   "FunctionFeature.runTest() was not invoked because it performs a computation."],
    }


def _parse_safe_map_expression(expression: str) -> ast.Expression:
    if not isinstance(expression, str) or not expression or len(expression) > 256:
        raise ExecutionContractError("API_UNSUPPORTED", "map expression is empty or longer than 256 characters")
    # COMSOL uses ^ for exponentiation.  The accepted arithmetic subset is
    # intentionally small and does not execute Python or COMSOL functions.
    normalized = expression.replace("^", "**")
    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise ExecutionContractError("API_UNSUPPORTED", "map expression is outside the verified arithmetic grammar") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 80:
        raise ExecutionContractError("API_UNSUPPORTED", "map expression exceeds the bounded arithmetic node count")
    for item in nodes:
        if isinstance(item, ast.Name):
            if item.id not in {"x", "y", "z"}:
                raise ExecutionContractError("API_UNSUPPORTED", f"map variable {item.id!r} is outside x/y/z")
        elif isinstance(item, ast.Constant):
            if isinstance(item.value, bool) or not isinstance(item.value, (int, float)) or not math.isfinite(float(item.value)):
                raise ExecutionContractError("API_UNSUPPORTED", "map expression constants must be finite numbers")
        elif isinstance(item, ast.BinOp):
            if not isinstance(item.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
                raise ExecutionContractError("API_UNSUPPORTED", "map expression operator is unsupported")
        elif isinstance(item, ast.UnaryOp):
            if not isinstance(item.op, (ast.UAdd, ast.USub)):
                raise ExecutionContractError("API_UNSUPPORTED", "map expression unary operator is unsupported")
        elif isinstance(item, (ast.Expression, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub)):
            continue
        else:
            raise ExecutionContractError("API_UNSUPPORTED", f"map expression syntax {type(item).__name__} is unsupported")
    return tree


def _eval_safe_map(tree: ast.Expression, coordinates: Sequence[float]) -> float:
    variables = {name: coordinates[index] for index, name in enumerate(("x", "y", "z")) if index < len(coordinates)}

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ExecutionContractError("API_UNSUPPORTED", f"test point has no coordinate for map variable {node.id!r}")
            return variables[node.id]
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            elif isinstance(node.op, ast.Div):
                if right == 0:
                    raise ExecutionContractError("INVALID_DEFINITION", "map expression divides by zero at a test point")
                value = left / right
            elif isinstance(node.op, ast.Pow):
                if abs(right) > 8 or (left == 0 and right < 0):
                    raise ExecutionContractError("API_UNSUPPORTED", "map exponent is outside the bounded safe range")
                value = left ** right
            else:  # pragma: no cover - parser already excludes this
                raise ExecutionContractError("API_UNSUPPORTED", "map operator is unsupported")
            if not math.isfinite(value):
                raise ExecutionContractError("INVALID_DEFINITION", "map expression produced a non-finite result")
            return value
        raise ExecutionContractError("API_UNSUPPORTED", "map syntax is outside the verified arithmetic grammar")

    return visit(tree)


def _validate_mapping_point(row: Any, index: int, map_count: int) -> dict[str, Any]:
    point = require_mapping(row, f"test_points[{index}]")
    reject_unknown_keys(point, ("destination", "destination_unit", "expected_source", "source_unit"), f"test_points[{index}]")
    if set(point) != {"destination", "destination_unit", "expected_source", "source_unit"}:
        raise ExecutionContractError("INVALID_REQUEST", f"test_points[{index}] requires destination, destination_unit, expected_source and source_unit")
    destination = point["destination"]
    expected = point["expected_source"]
    for name, value in (("destination", destination), ("expected_source", expected)):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, Mapping)):
            raise ExecutionContractError("INVALID_REQUEST", f"test_points[{index}].{name} must be a finite numeric array")
    dest = [_strict_float(value, f"test_points[{index}].destination[{axis}]") for axis, value in enumerate(destination)]
    src = [_strict_float(value, f"test_points[{index}].expected_source[{axis}]") for axis, value in enumerate(expected)]
    if not 1 <= len(dest) <= 3 or len(src) != map_count:
        raise ExecutionContractError("INVALID_REQUEST", f"test_points[{index}] coordinate lengths must be destination 1..3 and expected_source {map_count}")
    dest_unit = require_string(point["destination_unit"], f"test_points[{index}].destination_unit", max_length=64)
    source_unit = require_string(point["source_unit"], f"test_points[{index}].source_unit", max_length=64)
    if dest_unit != source_unit:
        raise ExecutionContractError("API_UNSUPPORTED", "coordinate-unit conversion is not implemented; destination_unit must equal source_unit")
    return {"destination": dest, "destination_unit": dest_unit, "expected_source": src, "source_unit": source_unit}


def _positive_axis_index(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a positive one-based integer")
    return value


def _mapping_evaluation_spec(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = require_mapping(value, "checks.evaluation")
    reject_unknown_keys(raw, ("solution", "coordinate_frame", "coordinate_unit", "source_unit"), "checks.evaluation")
    if set(raw) != {"solution", "coordinate_frame", "coordinate_unit", "source_unit"}:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "checks.evaluation requires solution, coordinate_frame, coordinate_unit and source_unit",
        )
    solution = require_mapping(raw["solution"], "checks.evaluation.solution")
    reject_unknown_keys(solution, ("dataset", "solution", "outer", "inner"), "checks.evaluation.solution")
    if set(solution) != {"dataset", "solution", "outer", "inner"}:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            "checks.evaluation.solution requires explicit dataset, solution, outer and inner bindings",
        )
    frame = require_string(raw["coordinate_frame"], "checks.evaluation.coordinate_frame", max_length=32)
    if frame != "spatial":
        raise ExecutionContractError("API_UNSUPPORTED", "result.at_points currently binds only coordinate_frame='spatial'")
    return {
        "solution": {
            "dataset": require_string(solution["dataset"], "checks.evaluation.solution.dataset", max_length=63),
            "solution": require_string(solution["solution"], "checks.evaluation.solution.solution", max_length=63),
            "outer": _positive_axis_index(solution["outer"], "checks.evaluation.solution.outer"),
            "inner": _positive_axis_index(solution["inner"], "checks.evaluation.solution.inner"),
        },
        "coordinate_frame": frame,
        "coordinate_unit": require_string(raw["coordinate_unit"], "checks.evaluation.coordinate_unit", max_length=64),
        "source_unit": require_string(raw["source_unit"], "checks.evaluation.source_unit", max_length=64),
    }


def _numeric_finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _evaluate_mapping_operator(
    worker: Any,
    model_tag: str,
    *,
    operator_tag: str,
    dstmap_count: int,
    points: Sequence[Mapping[str, Any]],
    evaluation: Mapping[str, Any],
    absolute_tolerance: float,
    relative_tolerance: float,
) -> dict[str, Any]:
    """Evaluate cpltag(x/y/z) through the existing ephemeral Interp adapter.

    A response is a sampled operator witness only.  It cannot establish that
    every destination point maps into the source mesh.
    """
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", operator_tag) is None:
        raise ExecutionContractError("API_UNSUPPORTED", "GeneralExtrusion tag is not a safe coupling-expression identifier")
    for index, point in enumerate(points):
        if point["destination_unit"] != evaluation["coordinate_unit"]:
            raise ExecutionContractError("INVALID_REQUEST", f"test_points[{index}].destination_unit must match checks.evaluation.coordinate_unit")
        if point["source_unit"] != evaluation["source_unit"]:
            raise ExecutionContractError("INVALID_REQUEST", f"test_points[{index}].source_unit must match checks.evaluation.source_unit")

    expressions = [f"{operator_tag}({axis})" for axis in ("x", "y", "z")[:dstmap_count]]
    from ._g3_results import result_at_points

    response = result_at_points(worker, model_tag, {
        "spec": {
            "expressions": expressions,
            "solution": dict(evaluation["solution"]),
            "complex_mode": "real",
        },
        "points": [list(point["destination"]) for point in points],
        "coordinate_unit": evaluation["coordinate_unit"],
        "frame": evaluation["coordinate_frame"],
    })
    cleanup = response.get("cleanup") if isinstance(response, Mapping) else None
    status = response.get("status") if isinstance(response, Mapping) else None
    if not isinstance(cleanup, Mapping) or cleanup.get("cleanup_failed") or cleanup.get("removed") is not True:
        return {
            "status": "EXECUTION_STATE_UNKNOWN",
            "expressions": expressions,
            "cleanup": cleanup,
            "engine_status": status,
            "reason": "ephemeral result.Interp cleanup was not confirmed",
            "partial_change": bool(isinstance(cleanup, Mapping) and cleanup.get("created")),
            "execution_state_unknown": True,
        }
    if not isinstance(status, Mapping) or status.get("ok") is not True:
        return {
            "status": "UNVERIFIED",
            "expressions": expressions,
            "cleanup": cleanup,
            "engine_status": status,
            "reason": (status.get("engine_error") if isinstance(status, Mapping) else None)
                      or "result.at_points did not report a complete successful evaluation",
            "partial_change": False,
            "execution_state_unknown": False,
        }

    if response.get("dataset") != evaluation["solution"]["dataset"] or response.get("solution") != evaluation["solution"]["solution"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "result.at_points did not bind the requested dataset/solution", "partial_change": False,
                "execution_state_unknown": False}
    if response.get("frame") != evaluation["coordinate_frame"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "result.at_points response frame differs from the explicit request", "partial_change": False,
                "execution_state_unknown": False}
    if response.get("coordinate_unit") != evaluation["coordinate_unit"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "result.at_points response coordinate unit differs from the explicit request", "partial_change": False,
                "execution_state_unknown": False}
    if not isinstance(response.get("feature_readback"), Mapping) or response["feature_readback"].get("data_status") != "VERIFIED" or response["feature_readback"].get("data") != evaluation["solution"]["dataset"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "ephemeral Interp dataset property was not read back exactly", "partial_change": False,
                "execution_state_unknown": False}
    if not isinstance(response.get("dataset_binding"), Mapping):
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "dataset/solution resolver did not publish a binding witness", "partial_change": False,
                "execution_state_unknown": False}
    dataset_binding = response["dataset_binding"]
    if dataset_binding.get("binding_complete") is not True or dataset_binding.get("solution") != evaluation["solution"]["solution"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "dataset_binding": dict(dataset_binding),
                "reason": "dataset binding is incomplete or points to a different solution", "partial_change": False,
                "execution_state_unknown": False}
    coordinate_readback = response.get("coordinate_readback")
    if not isinstance(coordinate_readback, Mapping) or coordinate_readback.get("status") != "VERIFIED" or not isinstance(coordinate_readback.get("unit"), str):
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "coordinate_readback": coordinate_readback,
                "reason": "spatial coordinates or geometry length unit were not read back", "partial_change": False,
                "execution_state_unknown": False}

    field = response.get("field_array")
    if not isinstance(field, Mapping) or field.get("axes") != ["expression", "outer", "inner", "point"]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "result.at_points did not return the canonical bound FieldArray", "partial_change": False,
                "execution_state_unknown": False}
    shape = field.get("shape")
    values = field.get("values")
    if shape != [dstmap_count, 1, 1, len(points)] or not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != dstmap_count:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "shape": shape, "reason": "FieldArray shape is not one selected solution pair per expression and test point",
                "partial_change": False, "execution_state_unknown": False}
    coords = field.get("coords")
    if not isinstance(coords, Mapping) or coords.get("outer") != [evaluation["solution"]["outer"]] or coords.get("inner") != [evaluation["solution"]["inner"]]:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "FieldArray selected outer/inner coordinates do not match the explicit SolutionSpec",
                "partial_change": False, "execution_state_unknown": False}
    metadata = field.get("metadata")
    if not isinstance(metadata, Mapping) or metadata.get("pair_mapping_complete") is not True:
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "SolutionInfo pair mapping is incomplete", "partial_change": False,
                "execution_state_unknown": False}
    pairs = metadata.get("solution_pairs")
    if not isinstance(pairs, Sequence) or isinstance(pairs, (str, bytes)) or not any(
        isinstance(pair, Mapping) and pair.get("outer") == evaluation["solution"]["outer"]
        and pair.get("inner") == evaluation["solution"]["inner"] for pair in pairs
    ):
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "reason": "requested outer/inner pair is absent from SolutionInfo readback", "partial_change": False,
                "execution_state_unknown": False}
    units = field.get("units")
    expression_units = units.get("expression") if isinstance(units, Mapping) else None
    if not isinstance(expression_units, Mapping) or any(expression_units.get(expr) != evaluation["source_unit"] for expr in expressions):
        return {"status": "UNVERIFIED", "expressions": expressions, "cleanup": cleanup,
                "units": expression_units, "reason": "actual coupling-expression units are unavailable or differ from the requested source unit",
                "partial_change": False, "execution_state_unknown": False}

    sampled: list[dict[str, Any]] = []
    for point_index, point in enumerate(points):
        actual_values: list[float] = []
        shape_ok = True
        for expr_index in range(dstmap_count):
            try:
                raw = values[expr_index][0][0][point_index]
            except (IndexError, KeyError, TypeError):
                shape_ok = False
                break
            number = _numeric_finite(raw)
            if number is None:
                shape_ok = False
                break
            actual_values.append(number)
        if not shape_ok:
            sampled.append({"index": point_index, "status": "DOMAIN_ERROR_OR_INVALID_VALUE",
                            "reason": "operator evaluation did not return finite real source coordinates"})
            continue
        residuals = [actual - expected for actual, expected in zip(actual_values, point["expected_source"])]
        limits = [absolute_tolerance + relative_tolerance * abs(expected) for expected in point["expected_source"]]
        within = all(abs(residual) <= limit for residual, limit in zip(residuals, limits))
        sampled.append({"index": point_index, "destination": list(point["destination"]),
                        "destination_unit": point["destination_unit"],
                        "mapped_source": actual_values, "source_unit": evaluation["source_unit"],
                        "expected_source": list(point["expected_source"]), "residual": residuals,
                        "tolerance": limits, "expressions": expressions,
                        "status": "PASS" if within else "FAIL"})
    sample_status = "PASS" if sampled and all(row.get("status") == "PASS" for row in sampled) else (
        "FAIL" if any(row.get("status") in {"FAIL", "DOMAIN_ERROR_OR_INVALID_VALUE"} for row in sampled) else "UNVERIFIED"
    )
    return {
        "status": sample_status,
        "expressions": expressions,
        "dataset_binding": dict(dataset_binding),
        "solution_pair": {"outer": evaluation["solution"]["outer"], "inner": evaluation["solution"]["inner"]},
        "coordinate_frame": response.get("frame"),
        "coordinate_unit_requested": response.get("coordinate_unit"),
        "coordinate_unit_readback": coordinate_readback.get("unit"),
        "coordinate_readback": dict(coordinate_readback),
        "expression_units": dict(expression_units),
        "test_points": sampled,
        "cleanup": dict(cleanup),
        "partial_change": False,
        "execution_state_unknown": False,
        "source_mesh_coverage": "UNVERIFIED",
        "destination_frame_evidence": "result.at_points accepted only spatial and verified its coordinate readback",
    }


def definition_mapping_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Check sampled GeneralExtrusion coordinates, preferably through COMSOL.

    The optional arithmetic preview is a separate partial subcheck.  A COMSOL
    operator sample requires an explicit dataset, solution pair, spatial
    coordinate frame and units, then uses result.at_points' ephemeral Interp
    cleanup/readback path to evaluate ``cpltag(x/y/z)``.  Even a successful
    sample set cannot establish global source-mesh coverage or a documented
    destination-frame property.
    """
    args = operation_arguments(arguments, ("path", "test_points", "checks"), ("path", "test_points"))
    points_raw = args["test_points"]
    if not isinstance(points_raw, Sequence) or isinstance(points_raw, (str, bytes, Mapping)) or not 1 <= len(points_raw) <= 32:
        raise ExecutionContractError("INVALID_REQUEST", "test_points must contain 1..32 explicit coordinate comparisons")
    checks = require_mapping(args.get("checks", {}), "checks")
    reject_unknown_keys(checks, ("absolute_tolerance", "relative_tolerance", "evaluation"), "checks")
    abs_tol = _strict_float(checks.get("absolute_tolerance", 0.0), "checks.absolute_tolerance")
    rel_tol = _strict_float(checks.get("relative_tolerance", 0.0), "checks.relative_tolerance")
    if abs_tol < 0 or rel_tol < 0:
        raise ExecutionContractError("INVALID_REQUEST", "mapping tolerances must be nonnegative")
    evaluation = _mapping_evaluation_spec(checks.get("evaluation"))

    path, node = resolve_path(worker, model_tag, args["path"], label="path")
    if not path.get("segments") or path["segments"][-1].get("collection") != "cpl":
        raise ExecutionContractError("INVALID_NODE_PATH", "definition.mapping_validate path must end at a cpl node")
    type_id = node_type(node)
    if type_id != "GeneralExtrusion":
        raise PreWriteRefusal("API_UNSUPPORTED", f"mapping_validate supports GeneralExtrusion only; observed {type_id!r}")
    usesrcmap = _read_property(node, "usesrcmap")["value"]
    if usesrcmap not in ("off", False):
        raise PreWriteRefusal("API_UNSUPPORTED", "GeneralExtrusion source-map inversion is not implemented; usesrcmap must read back off")
    srcframe = _read_property(node, "srcframe")["value"]
    if srcframe not in {"mesh", "material", "spatial"}:
        raise ExecutionContractError("API_UNSUPPORTED", "GeneralExtrusion.srcframe is outside the documented frame vocabulary")
    dstmap_raw = _read_property(node, "dstmap")["value"]
    if not isinstance(dstmap_raw, (list, tuple)) or not dstmap_raw or not all(isinstance(item, str) for item in dstmap_raw):
        raise ExecutionContractError("API_UNSUPPORTED", "GeneralExtrusion.dstmap must read as a nonempty StringArray")
    # COMSOL ignores trailing empty expressions; retain only the meaningful prefix.
    dstmap = list(dstmap_raw)
    while dstmap and not dstmap[-1].strip():
        dstmap.pop()
    if not 1 <= len(dstmap) <= 3 or any(not expr.strip() for expr in dstmap):
        raise ExecutionContractError("API_UNSUPPORTED", "GeneralExtrusion.dstmap must contain 1..3 nonempty map expressions")
    trees: list[ast.Expression] | None = None
    map_parse_error: str | None = None
    try:
        trees = [_parse_safe_map_expression(expr) for expr in dstmap]
    except ExecutionContractError as exc:
        # The public COMSOL coupling expression is the authoritative path.  A
        # proprietary expression such as a registered variable/function must
        # not be rejected merely because it is outside our arithmetic preview.
        map_parse_error = f"{exc.code}: {exc}"

    normalized = [_validate_mapping_point(point, index, len(dstmap)) for index, point in enumerate(points_raw)]
    dimensions = {len(point["destination"]) for point in normalized}
    if len(dimensions) != 1:
        raise ExecutionContractError("INVALID_REQUEST", "all test_points must have the same destination coordinate dimension")
    if evaluation is not None and any(
        point["destination_unit"] != evaluation["coordinate_unit"] or point["source_unit"] != evaluation["source_unit"]
        for point in normalized
    ):
        raise ExecutionContractError("INVALID_REQUEST", "per-point units must match checks.evaluation coordinate_unit/source_unit")
    # The COMSOL 6.4 model.cpl() property table has srcframe but no dstframe for
    # GeneralExtrusion; do not synthesize one from a component or caller.
    source_selection = call_probe(node, "selection")
    if not source_selection["ok"]:
        raise ExecutionContractError("API_UNSUPPORTED", "GeneralExtrusion.selection() source binding is not readable")
    named_probe = call_probe(source_selection["value"], "named")
    source_binding = {"kind": "named" if named_probe["ok"] and isinstance(named_probe["value"], str) and named_probe["value"] else "UNOBSERVED",
                      "tag": named_probe["value"] if named_probe["ok"] else None}

    arithmetic_preview: list[dict[str, Any]] = []
    if trees is not None:
        for index, point in enumerate(normalized):
            try:
                actual = [_eval_safe_map(tree, point["destination"]) for tree in trees]
                residuals = [actual_axis - expected_axis for actual_axis, expected_axis in zip(actual, point["expected_source"])]
                limits = [abs_tol + rel_tol * abs(expected_axis) for expected_axis in point["expected_source"]]
                within = all(abs(residual) <= limit for residual, limit in zip(residuals, limits))
                arithmetic_preview.append({"index": index, "mapped_source": actual,
                                           "expected_source": point["expected_source"], "residual": residuals,
                                           "tolerance": limits, "status": "PASS" if within else "FAIL"})
            except ExecutionContractError as exc:
                arithmetic_preview.append({"index": index, "mapped_source": None,
                                           "expected_source": point["expected_source"], "status": "UNDEFINED_AT_SAMPLE",
                                           "error": {"code": exc.code, "message": str(exc)}})
    operator_result = None
    if evaluation is not None:
        operator_result = _evaluate_mapping_operator(
            worker, model_tag, operator_tag=path["segments"][-1]["tag"], dstmap_count=len(dstmap),
            points=normalized, evaluation=evaluation, absolute_tolerance=abs_tol, relative_tolerance=rel_tol,
        )
    operator_ok = isinstance(operator_result, Mapping) and operator_result.get("status") == "PASS"
    operator_failed = isinstance(operator_result, Mapping) and operator_result.get("status") == "FAIL"
    unknown_execution = isinstance(operator_result, Mapping) and operator_result.get("execution_state_unknown") is True
    status = "EXECUTION_STATE_UNKNOWN" if unknown_execution else (
        "VALIDATION_FAILED" if operator_failed else "PARTIAL_UNVERIFIED"
    )
    return {
        "schema_version": "comsol-mcp.definition-mapping-validate/1.0.0",
        "path": path,
        "coupling_type": type_id,
        "source_frame": {"value": srcframe, "status": "OBSERVED"},
        "destination_frame": {"value": None, "status": "NO_PROPERTY; EVALUATION_FRAME_BOUND" if evaluation is not None else "UNOBSERVED",
                               "evaluation_frame": evaluation["coordinate_frame"] if evaluation is not None else None,
                               "reason": "COMSOL 6.4 GeneralExtrusion exposes srcframe but no documented dstframe property"},
        "source_selection": source_binding,
        "map": {"usesrcmap": usesrcmap, "dstmap": dstmap,
                "arithmetic_preview": {"status": "PARTIAL" if trees is not None else "UNAVAILABLE",
                                       "error": map_parse_error,
                                       "description": "Python subset parses read-back map text only; never authoritative COMSOL evaluation"}},
        "arithmetic_preview_points": arithmetic_preview,
        "operator_evaluation": operator_result or {"status": "NOT_RUN", "reason": "explicit checks.evaluation SolutionSpec/frame/units were not supplied"},
        "checks": {"absolute_tolerance": abs_tol, "relative_tolerance": rel_tol,
                   "arithmetic_preview_match": ("PASS" if arithmetic_preview and all(row["status"] == "PASS" for row in arithmetic_preview)
                                                 else ("FAIL" if any(row["status"] == "FAIL" for row in arithmetic_preview)
                                                       else "UNVERIFIED")),
                   "sampled_operator_values_match": "PASS" if operator_ok else ("FAIL" if operator_failed else "UNVERIFIED"),
                   "source_mesh_coverage": "UNVERIFIED",
                   "overall_acceptance": "PARTIAL_UNVERIFIED"},
        "ok": False,
        "sampled_operator_values_ok": operator_ok,
        "status": status,
        "partial_change": False,
        "execution_state_unknown": False,
        "limits": ["A finite coordinate map comparison does not prove source-mesh mapping coverage.",
                   "Python arithmetic preview is not COMSOL operator evaluation and cannot establish the mapping contract.",
                   "GeneralExtrusion usesrcmap=on inversion is unsupported.",
                   "result.at_points currently supports a strict explicit dataset/solution/outer/inner pair, spatial coordinates and supported length units only.",
                   "Destination frame is not exposed by the verified GeneralExtrusion property surface; only the evaluation adapter's spatial frame is bound."],
    }


def _selection_rule(arguments: Any) -> tuple[str, int, str, str, dict[str, Any]]:
    rule = require_mapping(arguments, "rule")
    reject_unknown_keys(rule, ("kind", "entity_dimension", "geometry", "unit", "bounds", "center", "radius", "condition"), "rule")
    kind = rule.get("kind")
    if not isinstance(kind, str) or kind not in {"box", "ball"}:
        raise ExecutionContractError("API_UNSUPPORTED", "selection.rebind rule.kind must be box or ball")
    dimension = rule.get("entity_dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension not in (0, 1, 2, 3):
        raise ExecutionContractError("INVALID_REQUEST", "rule.entity_dimension must be an integer from 0 through 3")
    geometry = require_string(rule.get("geometry"), "rule.geometry", max_length=63)
    unit = require_string(rule.get("unit"), "rule.unit", max_length=64)
    values: dict[str, Any] = {}
    if kind == "box":
        if "center" in rule or "radius" in rule or "bounds" not in rule:
            raise ExecutionContractError("INVALID_REQUEST", "box requires bounds and forbids center/radius")
        bounds = require_mapping(rule["bounds"], "rule.bounds")
        keys = {"xmin", "xmax", "ymin", "ymax", "zmin", "zmax"}
        reject_unknown_keys(bounds, keys, "rule.bounds")
        if set(bounds) != keys:
            raise ExecutionContractError("INVALID_REQUEST", "box bounds requires xmin/xmax/ymin/ymax/zmin/zmax")
        values.update({key: _strict_float(value, f"rule.bounds.{key}") for key, value in bounds.items()})
        for axis in ("x", "y", "z"):
            if values[f"{axis}min"] >= values[f"{axis}max"]:
                raise ExecutionContractError("INVALID_REQUEST", f"rule.bounds.{axis}min must be below {axis}max")
    else:
        if "bounds" in rule or "center" not in rule or "radius" not in rule:
            raise ExecutionContractError("INVALID_REQUEST", "ball requires center and radius and forbids bounds")
        center = rule["center"]
        if not isinstance(center, Sequence) or isinstance(center, (str, bytes, Mapping)) or len(center) != 3:
            raise ExecutionContractError("INVALID_REQUEST", "ball center must be [x, y, z]")
        values.update({f"pos{axis}": _strict_float(value, f"rule.center[{index}]")
                       for index, (axis, value) in enumerate(zip("xyz", center))})
        radius = _strict_float(rule["radius"], "rule.radius")
        if radius <= 0:
            raise ExecutionContractError("INVALID_REQUEST", "rule.radius must be positive")
        values["r"] = radius
    condition = rule.get("condition", "intersects")
    if not isinstance(condition, str) or condition not in {"intersects", "inside", "somevertex", "allvertices"}:
        raise ExecutionContractError("INVALID_REQUEST", "rule.condition is outside the documented spatial selection vocabulary")
    values["condition"] = condition  # type: ignore[assignment]
    return kind, dimension, geometry, unit, values


def selection_rebind(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Rebind an existing named Box/Ball selection by its read-back semantics.

    Preview validates target identity, geometry length unit and rule values but
    performs no setters.  A write targets only the existing node; it never
    interprets old entity IDs as stable across geometry revisions.
    """
    args = operation_arguments(arguments, ("selection", "rule", "preview"), ("selection", "rule"))
    preview = require_bool(args.get("preview", False), "preview")
    spec = validate_selection_spec(args["selection"], label="selection", allowed_kinds=("named",))
    kind, dimension, geometry_tag, requested_unit, values = _selection_rule(args["rule"])
    expected_type = "Box" if kind == "box" else "Ball"
    if spec.get("geometry") is not None and spec["geometry"] != geometry_tag:
        raise ExecutionContractError("INVALID_REQUEST", "selection.geometry and rule.geometry must match")
    comp = component_node(worker, model_tag, spec["component"])
    container = call_probe(comp, "selection")
    if not container["ok"]:
        raise ExecutionContractError("API_UNSUPPORTED", "component selection list is not readable")
    tags = call_probe(container["value"], "tags")
    if not tags["ok"] or not isinstance(tags["value"], (list, tuple)):
        raise ExecutionContractError("API_UNSUPPORTED", "component selection tags() is not readable")
    if spec["tag"] not in tags["value"]:
        raise ExecutionContractError("NODE_NOT_FOUND", f"selection {spec['tag']!r} does not exist")
    node = call_probe(container["value"], "get", spec["tag"])
    if not node["ok"] or node["value"] is None:
        raise ExecutionContractError("NODE_NOT_FOUND", f"selection {spec['tag']!r} could not be resolved")
    selection_node = node["value"]
    current_type = node_type(selection_node)
    if current_type != expected_type:
        raise PreWriteRefusal("API_UNSUPPORTED", f"selection.rebind will not change selection type {current_type!r} to {expected_type!r}")
    current_geometry = probe_geometry_tag(selection_node)
    if current_geometry != geometry_tag:
        raise PreWriteRefusal("API_UNSUPPORTED", "the selection's geometry identity is absent or differs from rule.geometry")
    geometry = call_probe(comp, "geom", geometry_tag)
    if not geometry["ok"] or geometry["value"] is None:
        raise ExecutionContractError("NODE_NOT_FOUND", f"geometry {geometry_tag!r} could not be resolved")
    actual_unit = geometry_length_unit(geometry["value"])
    if not actual_unit:
        raise ExecutionContractError("API_UNSUPPORTED", "geometry lengthUnit() is not readable")
    if requested_unit != actual_unit:
        raise ExecutionContractError("API_UNSUPPORTED", "rule.unit must equal the read-back geometry lengthUnit(); no conversion is applied")
    if dimension > 0:
        sdim = call_probe(geometry["value"], "getSDim")
        if not sdim["ok"] or isinstance(sdim["value"], bool) or not isinstance(sdim["value"], int):
            raise ExecutionContractError("API_UNSUPPORTED", "geometry getSDim() is required to validate entity_dimension")
        if dimension > sdim["value"]:
            raise ExecutionContractError("INVALID_REQUEST", "rule.entity_dimension exceeds the geometry space dimension")

    current_entities = call_probe(selection_node, "entities")
    if not current_entities["ok"] or not isinstance(current_entities["value"], (list, tuple)):
        raise ExecutionContractError("API_UNSUPPORTED", "selection entities() readback is unavailable")
    current_dim = call_probe(selection_node, "dim")
    if not current_dim["ok"] or isinstance(current_dim["value"], bool) or not isinstance(current_dim["value"], int):
        raise ExecutionContractError("API_UNSUPPORTED", "selection dim() readback is unavailable")
    props = dict(values)
    desired_definition = {"entity_dimension": dimension, "properties": props}
    _w13._split_selection_definition(desired_definition, expected_type)
    if preview:
        return {
            "schema_version": "comsol-mcp.selection-rebind/1.0.0",
            "component": spec["component"], "geometry": geometry_tag, "selection_tag": spec["tag"],
            "selection_type": current_type, "preview": True, "write_performed": False,
            "unit": actual_unit, "requested_semantics": {"kind": kind, "entity_dimension": dimension, "properties": props},
            "current_entity_dimension": current_dim["value"], "current_entities": list(current_entities["value"]),
            "future_entities": None,
            "semantic_identity": "RULE_DEFINED; numeric entity IDs are geometry-revision dependent",
            "status": "PREVIEWED", "partial_change": False, "execution_state_unknown": False,
        }

    path, selection_obj = resolve_path(worker, model_tag,
                                       {"segments": [{"collection": "component", "tag": spec["component"]},
                                                     {"collection": "selection", "tag": spec["tag"]}]},
                                       label="selection")
    applied = _w13._apply_selection_definition(path, selection_obj, worker, model_tag, current_type, desired_definition)
    if applied.get("failed") or applied.get("execution_state_unknown"):
        property_result = applied.get("properties")
        property_writes = property_result.get("applied", []) if isinstance(property_result, Mapping) else []
        wrote_anything = bool(applied.get("applied") or property_writes)
        return {
            "schema_version": "comsol-mcp.selection-rebind/1.0.0",
            "component": spec["component"], "geometry": geometry_tag, "selection_tag": spec["tag"],
            "selection_type": current_type, "preview": False, "write_performed": wrote_anything,
            "applied": applied.get("applied", []), "failed": applied.get("failed", []),
            "not_executed": applied.get("not_executed", []), "status": "EXECUTION_STATE_UNKNOWN" if applied.get("execution_state_unknown") else "FAILED",
            "partial_change": wrote_anything,
            "execution_state_unknown": bool(applied.get("execution_state_unknown")),
        }
    after_entities = call_probe(selection_obj, "entities")
    after_dim = call_probe(selection_obj, "dim")
    if not after_entities["ok"] or not isinstance(after_entities["value"], (list, tuple)) or not after_dim["ok"] or after_dim["value"] != dimension:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", "selection rule was written but its final entities/dimension could not be verified",
            details={"partial_change": True, "entity_readback": after_entities.get("error"), "dimension_readback": after_dim.get("error")},
        )
    return {
        "schema_version": "comsol-mcp.selection-rebind/1.0.0",
        "component": spec["component"], "geometry": geometry_tag, "selection_tag": spec["tag"],
        "selection_type": current_type, "preview": False, "write_performed": True,
        "unit": actual_unit, "requested_semantics": {"kind": kind, "entity_dimension": dimension, "properties": props},
        "entities": list(after_entities["value"]), "entity_count": len(after_entities["value"]),
        "current_entity_dimension": int(after_dim["value"]),
        "numeric_id_stability": "NOT_CLAIMED_ACROSS_GEOMETRY_REVISIONS",
        "semantic_identity": "RULE_DEFINED_AND_READ_BACK",
        "applied": applied.get("applied", []), "status": "REBOUND", "partial_change": False,
        "execution_state_unknown": False,
    }


OPERATIONS = {
    "function.validate": function_validate,
    "selection.rebind": selection_rebind,
    "definition.mapping_validate": definition_mapping_validate,
}
