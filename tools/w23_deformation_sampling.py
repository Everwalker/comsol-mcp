"""Managed native sampling entry for independent W21-to-W23 map checks.

Held-out query coordinates come only from the immutable W21 checkpoint's
registered sample request. This module sends coordinates and expressions to
one trusted Java call; it never accepts field-value arrays. The Java adapter
evaluates direct source expressions and General-Extrusion expressions in one
request, then removes both temporary Interp nodes. Native execution remains
NOT_RUN until that managed operation is actually dispatched and reconciled.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

from tools.w23_deformation_mapping import (
    DeformationMappingError,
    _digest,
    compare_native_mapping_samples,
)


class DeformationSamplingError(ValueError):
    """The registered mapping-sample contract is incomplete or inconsistent."""


_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_SOURCE_ARTIFACT_PREFIX = "NativeW23DeformationMappingSampler#run"


def _fail(message: str) -> None:
    raise DeformationSamplingError(message)


def _sha256(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _points3(value: Any, label: str) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        _fail(f"{label} must be a nonempty registered list of xyz metre coordinates")
    points: list[list[float]] = []
    for index, point in enumerate(value):
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            _fail(f"{label}[{index}] must have exactly x, y, z")
        row = []
        for coordinate in point:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                _fail(f"{label}[{index}] must contain finite numeric coordinates")
            number = float(coordinate)
            if not math.isfinite(number):
                _fail(f"{label}[{index}] must contain finite numeric coordinates")
            row.append(number)
        points.append(row)
    if len({tuple(point) for point in points}) != len(points):
        _fail(f"{label} must not contain duplicate query coordinates")
    return points


def _volume_rank3(points: list[list[float]]) -> bool:
    """Require a non-coplanar 3-D query set with a scale-relative determinant."""
    if len(points) < 4:
        return False
    spans = [max(row[axis] for row in points) - min(row[axis] for row in points)
             for axis in range(3)]
    scale = max(spans)
    if scale <= 0:
        return False
    origin = points[0]
    vectors = [[point[axis] - origin[axis] for axis in range(3)] for point in points[1:]]
    tol = scale ** 3 * 1e-12
    for i in range(len(vectors)):
        a = vectors[i]
        for j in range(i + 1, len(vectors)):
            b = vectors[j]
            cross = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
            for k in range(j + 1, len(vectors)):
                c = vectors[k]
                det = sum(cross[axis] * c[axis] for axis in range(3))
                if abs(det) > tol:
                    return True
    return False


def build_registered_mapping_sample_contract(
    plan: Mapping[str, Any], checkpoint: Mapping[str, Any], *,
    source_native: Mapping[str, Any], target_native: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive deterministic 3-D query points from the immutable W21 contract.

    The point list is an evaluation request, not field data. Source values are
    freshly evaluated from the bound native W21 solution in COMSOL; no values
    from W21's stored field array are used as the comparison oracle.
    """
    if not isinstance(plan, Mapping) or plan.get("native_result") != "NOT_RUN":
        _fail("a software-only General Extrusion plan is required")
    if not isinstance(checkpoint, Mapping) or checkpoint.get("kind") != "w21stage":
        _fail("the registered W21 stage checkpoint is required")
    if checkpoint.get("sha256") != _digest(checkpoint):
        _fail("W21 checkpoint hash is invalid")
    source = plan.get("source")
    target = plan.get("target")
    adapter = plan.get("native_adapter")
    if not all(isinstance(item, Mapping) for item in (source, target, adapter)):
        _fail("mapping plan must bind source, destination, and General Extrusion identities")
    if checkpoint.get("checkpoint_id") != source.get("checkpoint_id"):
        _fail("W21 checkpoint does not match the mapping plan")
    if checkpoint.get("model_ref") != source.get("model_ref"):
        _fail("W21 checkpoint ModelRef differs from the managed mapping plan")
    if source_native.get("model_tag") != source.get("model_tag") or target_native.get("model_tag") != source.get("model_tag"):
        _fail("native sample endpoints are not in the bound COMSOL Model")
    if source_native.get("component_tag") != source.get("source_component"):
        _fail("native source component differs from W21 mapping provenance")
    if target_native.get("component_tag") != target.get("component"):
        _fail("native target component differs from W23 mapping provenance")
    if source_native.get("source_solution_tag") != source.get("source_solution"):
        _fail("native source solution differs from the authenticated W21 source")
    if source_native.get("source_dataset_tag") != source.get("source_dataset"):
        _fail("native source dataset differs from the authenticated W21 source")
    if source_native.get("source_study_tag") != source.get("source_study"):
        _fail("native source study differs from the authenticated W21 source")
    if source_native.get("source_solution_fingerprint") != source.get("source_solution_fingerprint"):
        _fail("native source solution fingerprint differs from registered W17 identity")
    if source_native.get("source_geometry_fingerprint") != source.get("source_geometry_fingerprint"):
        _fail("native source geometry changed after the mapping plan was built")
    if target_native.get("target_geometry_fingerprint") != target.get("geometry_fingerprint"):
        _fail("native optical geometry changed after the mapping plan was built")
    if source_native.get("selection_tag") != source.get("source_selection"):
        _fail("native source selection differs from the mapping plan")
    if target_native.get("selection_tag") != target.get("selection"):
        _fail("native destination selection differs from the mapping plan")
    if source_native.get("coordinate_unit") != "m" or target_native.get("coordinate_unit") != "m":
        _fail("native sample coordinates must be in SI metres")

    selection = checkpoint.get("selection")
    request = checkpoint.get("sample_request")
    points_raw = selection.get("points") if isinstance(selection, Mapping) else None
    request_points = request.get("points") if isinstance(request, Mapping) else None
    if points_raw is None or request_points is None:
        _fail("W21 checkpoint must bind its stored selection points to the original sample request")
    points = _points3(points_raw, "W21 selection.points")
    if _points3(request_points, "W21 sample_request.points") != points:
        _fail("W21 selection coordinates differ from the registered sample request")
    if selection.get("coordinate_unit") != "m" or checkpoint.get("coordinate_unit", "m") != "m":
        _fail("registered W21 sample query coordinates must be metres")
    if not _volume_rank3(points):
        _fail("held-out W21 sample coordinates must span a non-coplanar 3-D volume")

    plan_source_binding = {
        key: copy.deepcopy(source[key]) for key in (
            "project_id", "model_ref", "model_tag", "checkpoint_id", "observation_ref",
            "revision", "source_solution", "source_dataset", "source_study",
            "source_solution_fingerprint", "source_geometry_fingerprint", "source_component",
            "source_geometry", "source_selection", "source_selection_entity_ids",
            "outer", "inner", "solnum", "time_s",
        )
    }
    plan_source_binding["target_component"] = target["component"]
    plan_source_binding["target_geometry"] = target["geometry"]
    plan_source_binding["target_selection"] = target["selection"]
    plan_source_binding["target_selection_entity_ids"] = copy.deepcopy(target["selection_entity_ids"])
    source_expressions = copy.deepcopy(source.get("sampled_expressions"))
    mapped_expressions = copy.deepcopy(adapter.get("deformation_expressions"))
    if not isinstance(source_expressions, Mapping) or set(source_expressions) != {"x", "y", "z"}:
        _fail("source displacement expression map is incomplete")
    if not isinstance(mapped_expressions, Mapping) or set(mapped_expressions) != {"x", "y", "z"}:
        _fail("mapped displacement expression map is incomplete")
    selector_parts = [f"setind(t,{source['inner']})"]
    for item in source.get("outer_parameters", []):
        if (not isinstance(item, Mapping) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(item.get("name")))
                or not isinstance(item.get("unit"), str) or not item["unit"]
                or isinstance(item.get("value"), bool) or not isinstance(item.get("value"), (int, float))
                or not math.isfinite(float(item["value"]))):
            _fail("outer native parameter selectors must have exact name/value/unit bindings")
        selector_parts.append(f"setval({item['name']},{float(item['value']):.17g}[{item['unit']}])")
    source_component = source["source_component"]
    solution = source["source_solution"]
    direct_expressions = {
        axis: f"withsol('{solution}',{source_component}.{source_expressions[axis]},{','.join(selector_parts)})"
        for axis in ("x", "y", "z")
    }
    sample_identity = {
        "profile": "w23.w21_general_extrusion_heldout_v1",
        "plan_source": plan_source_binding,
        "source_geometry_fingerprint": source["source_geometry_fingerprint"],
        "target_geometry_fingerprint": target["geometry_fingerprint"],
        "coordinates_m": points,
        "direct_expressions": direct_expressions,
        "mapped_expressions": mapped_expressions,
        "native_selections": {
            "source": {
                "component": source["source_component"],
                "geometry": source["source_geometry"],
                "tag": source["source_selection"],
                "entity_dimension": 3,
                "entity_ids": copy.deepcopy(source["source_selection_entity_ids"]),
                "frame": source["source_frame"],
                "coordinate_unit": source["coordinate_unit"],
            },
            "target": {
                "component": target["component"],
                "geometry": target["geometry"],
                "tag": target["selection"],
                "entity_dimension": 3,
                "entity_ids": copy.deepcopy(target["selection_entity_ids"]),
                "frame": target["coordinate_frame"],
                "coordinate_unit": target["coordinate_unit"],
            },
        },
        "operator_tag": adapter["operator_tag"],
        "query_provenance": "W21 registered selection.points; fresh COMSOL evaluation; values are not read from the saved W21 field array",
    }
    contract_id = _sha256(sample_identity)
    return {
        "schema_version": 1,
        "sample_contract_id": contract_id,
        **sample_identity,
        "status": "READY_FOR_ONE_MANAGED_READBACK_CALL",
        "native_result": "NOT_RUN",
        "study_or_solver_invoked": False,
        "value_unit": "m",
        "coordinate_unit": "m",
        "sample_count": len(points),
        "coordinate_sha256": _sha256(points),
        "temporary_interpolation_tags": ["w23direct" + contract_id[:10], "w23mapped" + contract_id[:10]],
        "required_cleanup": "remove both request-owned Interp nodes in finally; fail if either remains",
        "outside_source": "NaN/refusal; never clip, extrapolate, or substitute nearest point",
            "comparison": "direct source component withsol vs destination GeneralExtrusion(withsol) at identical coordinates",
        "expected_binding": plan_source_binding,
    }


def build_mapping_sampling_dispatch(
    contract: Mapping[str, Any], *, source_artifact: str, request_id: str,
    idempotency_key: str, expected_revision: int,
) -> dict[str, Any]:
    """Build one managed Java execution request for both independent routes."""
    if not isinstance(contract, Mapping) or contract.get("status") != "READY_FOR_ONE_MANAGED_READBACK_CALL":
        _fail("a validated mapping sampling contract is required")
    if not isinstance(source_artifact, str) or not source_artifact:
        _fail("registered managed Java source artifact id is required")
    if not isinstance(request_id, str) or not request_id or not isinstance(idempotency_key, str) or not idempotency_key:
        _fail("request and idempotency identities are required")
    binding = contract.get("expected_binding")
    if not isinstance(binding, Mapping):
        _fail("the immutable W21 source binding is missing")
    if type(expected_revision) is not int or expected_revision != binding.get("revision", expected_revision):
        _fail("managed model revision differs from the source plan")
    return {
        "operation": "operation_call",
        "arguments": {"operation_id": "code.execute_java", "arguments": {
            "source_artifact": source_artifact,
            "entrypoint": _SOURCE_ARTIFACT_PREFIX,
            "mode": "trusted",
            "arguments": {"phase": "sample_mapping", "contract": dict(contract)},
        }},
        "execution": {
            "project_id": binding["project_id"],
            "model_ref": copy.deepcopy(binding["model_ref"]),
            "expected_revision": expected_revision,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        },
        "dispatch_scope": "single managed Java call; no Study.run or solver call",
    }


def reconcile_mapping_sample_response(
    plan: Mapping[str, Any], contract: Mapping[str, Any], java_readback: Mapping[str, Any], *,
    absolute_tolerance_m: float, relative_tolerance: float,
) -> dict[str, Any]:
    """Validate actual raw native samples, then independently recompute residuals."""
    if not isinstance(contract, Mapping) or not isinstance(java_readback, Mapping):
        _fail("native Java sample response and exact sampling contract are required")
    if java_readback.get("sample_contract_id") != contract.get("sample_contract_id"):
        _fail("native sample response differs from the submitted sampling contract")
    if java_readback.get("coordinate_sha256") != contract.get("coordinate_sha256"):
        _fail("native query coordinate receipt differs from the registered W21 request")
    if java_readback.get("native_result") != "COMSOL_NATIVE_RAW":
        _fail("software output cannot be promoted to native W21 mapping evidence")
    if java_readback.get("study_or_solver_invoked") is not False:
        _fail("mapping sampling entrypoint must not run a study or solver")
    cleanup = java_readback.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is not False:
        _fail("both temporary native Interp nodes must be confirmed removed")
    source_raw, mapped_raw = java_readback.get("source"), java_readback.get("mapped")
    if not isinstance(source_raw, Mapping) or not isinstance(mapped_raw, Mapping):
        _fail("native response must contain both independent sample routes")
    selections = contract.get("native_selections")
    if not isinstance(selections, Mapping):
        _fail("native sample contract lacks exact source/target selection identities")
    expected_routes = {
        "source": (source_raw, selections.get("source"), "direct_dataset_point_evaluation",
                   "native_source_component_expression", contract.get("direct_expressions")),
        "target": (mapped_raw, selections.get("target"), "general_extrusion_destination_evaluation",
                   "destination_component_general_extrusion", contract.get("mapped_expressions")),
    }
    for role, (raw, selection, route, expression_route, expressions) in expected_routes.items():
        if not isinstance(selection, Mapping) or not isinstance(expressions, Mapping):
            _fail(f"{role} selection or expression binding is incomplete")
        if raw.get("sample_contract_id") != contract.get("sample_contract_id"):
            _fail("one native sample route used another point/provenance contract")
        if raw.get("coordinates_m") != contract.get("coordinates_m"):
            _fail("native Interp coordinates differ from registered W21 sample points")
        if raw.get("sample_count") != contract.get("sample_count"):
            _fail("native Interp coordinate count differs from the sample contract")
        if raw.get("coordinate_sha256") != contract.get("coordinate_sha256"):
            _fail("native route coordinate hash differs from the W21 sample contract")
        if raw.get("value_unit") != "m" or raw.get("coordinate_unit") != "m":
            _fail("native displacement and coordinate units must read back as metres")
        if (raw.get("route") != route or raw.get("source_expression_route") != expression_route
                or raw.get("selection") != selection.get("tag")
                or raw.get("selection_geometry") != selection.get("geometry")
                or raw.get("selection_entity_dimension") != 3
                or raw.get("selection_entity_ids") != selection.get("entity_ids")
                or raw.get("geometry_frame") != selection.get("frame")):
            _fail(f"native {role} route/selection/frame differs from the frozen contract")
        if raw.get("expressions") != [expressions[axis] for axis in ("x", "y", "z")]:
            _fail(f"native {role} expressions differ from the frozen xyz expression order")
        if (raw.get("dataset") != contract["expected_binding"].get("source_dataset")
                or raw.get("real_solution_selection") != str(contract["expected_binding"].get("solnum"))
                or raw.get("outer_solution_selection") != str(contract["expected_binding"].get("outer"))):
            _fail(f"native {role} dataset or selected solution indices differ from W21")
        if ("displacement_real_m" not in raw or "displacement_imag_m" not in raw
                or raw.get("complex_readback") is not True):
            _fail(f"native {role} response must preserve both real and imaginary displacement samples")
    try:
        result = compare_native_mapping_samples(
            plan, source_raw, mapped_raw,
            absolute_tolerance_m=absolute_tolerance_m,
            relative_tolerance=relative_tolerance,
        )
    except DeformationMappingError as error:
        raise DeformationSamplingError(str(error)) from error
    return {**result,
            "sample_contract_id": contract["sample_contract_id"],
            "coordinate_sha256": contract["coordinate_sha256"],
            "native_result": "COMSOL_NATIVE_RAW",
            "software_result": result["status"],
            "mapping_complete": False,
            "full_w23_acceptance": "NOT_RUN"}
