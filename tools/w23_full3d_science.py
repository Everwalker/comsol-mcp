"""Independent 3-D quadrature checks for managed W23 native field readbacks.

This module accepts only fresh COMSOL Interp readbacks, their exact native
solution identities, and a frozen circular-port sampling contract.  It never
accepts caller-supplied field arrays as native data and never changes native
status from NOT_RUN to PASS.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4


class Full3DScienceError(ValueError):
    """A native field readback or independent comparison is inadmissible."""


class ManagedRouteOutcomeError(RuntimeError):
    """One public managed dispatch failed or became unobservable; never replay it."""

    def __init__(self, label: str, outcome: str, message: str, *, response: Any = None,
                 job_id: str | None = None):
        super().__init__(f"{label}: {message}")
        self.label = label
        self.outcome = outcome
        self.response = response
        self.job_id = job_id
        self.retry_forbidden = True


_AXES = ("x", "y", "z")
_VECTOR_FIELDS = {
    "signal": tuple(f"ewfd.{prefix}{axis}" for prefix in ("E", "H") for axis in _AXES),
    "reference_mode": tuple(f"ewfd.{prefix}{axis}_2" for prefix in ("Emode", "Hmode") for axis in _AXES),
    "incident_reference": tuple(f"ewfd.{prefix}{axis}_1" for prefix in ("Emode", "Hmode") for axis in _AXES),
    "capture_signal": tuple(f"ewfd.{prefix}{axis}" for prefix in ("E", "H") for axis in _AXES),
}
_NORMAL_FIELDS = ("nx", "ny", "nz")
_COMPONENT = "comp3d"
_GEOMETRY = "geom3d"
_PLANE_IDS = {"input": "input_port", "output": "receiver_port"}
_POWER_UNIT = "W"
_MODEL_REF_KEYS = {"schema_version", "session_id", "server_instance_id", "model_tag", "generation"}
_TAG = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
# Frozen software comparison policy.  Values were selected before any W23
# native run; they are not inferred or widened from observed native results.
# The absolute cross tolerance is dimensionless after normalization by the
# positive reference-power scale 4*sqrt(P_signal*P_mode).  Only expected
# values inside that explicit near-zero band use the absolute floor.  Every
# larger nonzero comparison retains the prior strict relative limit.
FULL3D_COMPARISON_POLICY = {
    "policy_id": "w23.full3d.native_vs_quadrature.atol_rtol.v1",
    "relative_tolerance": 1e-3,
    "cross_absolute_normalized_tolerance": 1e-5,
    "eta_absolute_tolerance": 1e-10,
    "capture_flux_absolute_normalized_tolerance": 1e-5,
}


def _fail(message: str) -> None:
    raise Full3DScienceError(message)


def _sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail(f"{label} must be finite numeric data")
    return float(value)


def _vector3(value: Any, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _fail(f"{label} must be a three-vector")
    return tuple(_finite_number(item, label) for item in value)  # type: ignore[return-value]


def _normalize(value: Sequence[float], label: str) -> tuple[float, float, float]:
    norm = math.sqrt(sum(float(item) * float(item) for item in value))
    if not math.isfinite(norm) or norm <= 0.0:
        _fail(f"{label} must have positive finite length")
    return tuple(float(item) / norm for item in value)  # type: ignore[return-value]


def _cross(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float]:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def circular_port_quadrature(
    center_m: Sequence[float], axis_xyz: Sequence[float], radius_m: float, *,
    radial_intervals: int, angular_points: int,
) -> dict[str, Any]:
    """Construct deterministic polar Simpson/trapezoid points and area weights.

    The radial Simpson rule integrates ``r dr`` and the periodic trapezoid rule
    integrates angle. The center is represented once with zero area weight.
    The same routine independently reconstructs expected native Interp points.
    """
    center = _vector3(center_m, "port center")
    axis = _normalize(_vector3(axis_xyz, "port axis"), "port axis")
    radius = _finite_number(radius_m, "port radius")
    if radius <= 0.0:
        _fail("port radius must be positive")
    if type(radial_intervals) is not int or radial_intervals < 2 or radial_intervals % 2:
        _fail("radial_intervals must be a positive even integer")
    if type(angular_points) is not int or angular_points < 8 or angular_points % 4:
        _fail("angular_points must be an integer multiple of four and at least eight")

    reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (0.0, 1.0, 0.0)
    tangent_1 = _normalize(_cross(reference, axis), "port tangent basis")
    tangent_2 = _normalize(_cross(axis, tangent_1), "port tangent basis")
    dr = radius / radial_intervals
    dphi = 2.0 * math.pi / angular_points
    points: list[tuple[float, float, float]] = [center]
    weights: list[float] = [0.0]
    for radial_index in range(1, radial_intervals + 1):
        radial = dr * radial_index
        simpson_coefficient = (1 if radial_index == radial_intervals else
                               4 if radial_index % 2 else 2)
        ring_weight = simpson_coefficient * dr * radial / 3.0 * dphi
        for angular_index in range(angular_points):
            phi = dphi * angular_index
            c, s = math.cos(phi), math.sin(phi)
            points.append(tuple(center[k] + radial * (c * tangent_1[k] + s * tangent_2[k])
                                for k in range(3)))
            weights.append(ring_weight)
    identity = {
        "profile": "w23.full3d.circular_port.radial_simpson_periodic_trapezoid.v1",
        "center_m": list(center), "axis_xyz": list(axis), "tangent_1_xyz": list(tangent_1),
        "tangent_2_xyz": list(tangent_2), "radius_m": radius,
        "radial_intervals": radial_intervals, "angular_points": angular_points,
        "sample_count": len(points), "coordinate_unit": "m", "measure_unit": "m^2",
    }
    return {**identity, "quadrature_sha256": _sha256(identity),
            "coordinates_m": [list(point) for point in points], "weights_m2": weights,
            "weight_sum_m2": math.fsum(weights)}


def rectangular_port_quadrature(
    center_m: Sequence[float], axis_xyz: Sequence[float], half_widths_uv_m: Sequence[float], *,
    u_intervals: int, v_intervals: int,
) -> dict[str, Any]:
    """Tensor-product composite Simpson quadrature for a rectangular plane."""
    center = _vector3(center_m, "port center")
    axis = _normalize(_vector3(axis_xyz, "port axis"), "port axis")
    if not isinstance(half_widths_uv_m, (list, tuple)) or len(half_widths_uv_m) != 2:
        _fail("rectangular half widths must contain two local coordinates")
    half_u = _finite_number(half_widths_uv_m[0], "port half width u")
    half_v = _finite_number(half_widths_uv_m[1], "port half width v")
    if half_u <= 0.0 or half_v <= 0.0:
        _fail("rectangular half widths must be positive")
    if (type(u_intervals) is not int or u_intervals < 2 or u_intervals % 2
            or type(v_intervals) is not int or v_intervals < 2 or v_intervals % 2):
        _fail("rectangular Simpson interval counts must be positive even integers")
    reference = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (0.0, 1.0, 0.0)
    tangent_u = _normalize(_cross(reference, axis), "port tangent basis")
    tangent_v = _normalize(_cross(axis, tangent_u), "port tangent basis")
    du, dv = 2.0 * half_u / u_intervals, 2.0 * half_v / v_intervals
    points: list[tuple[float, float, float]] = []
    weights: list[float] = []
    for iu in range(u_intervals + 1):
        u = -half_u + iu * du
        wu = 1 if iu in (0, u_intervals) else (4 if iu % 2 else 2)
        for iv in range(v_intervals + 1):
            v = -half_v + iv * dv
            wv = 1 if iv in (0, v_intervals) else (4 if iv % 2 else 2)
            points.append(tuple(center[k] + u * tangent_u[k] + v * tangent_v[k]
                                for k in range(3)))
            weights.append(wu * wv * du * dv / 9.0)
    identity = {
        "profile": "w23.full3d.rectangular.tensor_simpson.v1",
        "center_m": list(center), "axis_xyz": list(axis),
        "tangent_u_xyz": list(tangent_u), "tangent_v_xyz": list(tangent_v),
        "half_widths_uv_m": [half_u, half_v],
        "radial_intervals": u_intervals, "angular_points": v_intervals,
        "u_intervals": u_intervals, "v_intervals": v_intervals,
        "sample_count": len(points), "coordinate_unit": "m", "measure_unit": "m^2",
    }
    return {**identity, "quadrature_sha256": _sha256(identity),
            "coordinates_m": [list(point) for point in points], "weights_m2": weights,
            "weight_sum_m2": math.fsum(weights)}


def build_raw_field_contract(
    *, case: Mapping[str, Any], role: str, source: Mapping[str, Any],
    plane: Mapping[str, Any], radial_intervals: int, angular_points: int,
) -> dict[str, Any]:
    """Bind one fresh vector-field extraction to a native source and local plane."""
    if role not in _VECTOR_FIELDS:
        _fail("raw-field role must name one registered signal/mode/capture source")
    if not isinstance(case, Mapping) or not isinstance(case.get("case_id"), str):
        _fail("a registered immutable full-3D case identity is required")
    required_source = ("dataset_id", "solution_id", "outer_index", "inner_index", "solnum")
    if not isinstance(source, Mapping) or any(key not in source for key in required_source):
        _fail("raw-field extraction must bind a complete native dataset/solution/index tuple")
    if (not isinstance(source["dataset_id"], str) or not source["dataset_id"]
            or not isinstance(source["solution_id"], str) or not source["solution_id"]
            or any(type(source[key]) is not int or source[key] < 1
                   for key in ("outer_index", "inner_index", "solnum"))):
        _fail("native source identity contains an invalid tag or solution index")
    if not isinstance(plane, Mapping):
        _fail("native local port frame is required")
    plane_id = plane.get("plane_id")
    expected_plane = "input_port" if role == "incident_reference" else "receiver_port"
    if plane_id != expected_plane:
        _fail("field role is bound to the wrong local native port plane")
    if (plane.get("component") != _COMPONENT or plane.get("geometry") != _GEOMETRY
            or plane.get("entity_dimension") != 2 or plane.get("coordinate_unit") != "um"):
        _fail("field plane must be a native 2-D boundary selection in comp3d/geom3d micrometre geometry")
    center_um = _vector3(plane.get("center_xyz_um"), "plane center")
    axis = _normalize(_vector3(plane.get("axis_xyz"), "plane axis"), "plane axis")
    native_sign = plane.get("native_normal_sign")
    if type(native_sign) is not int or native_sign not in {-1, 1}:
        _fail("native surface normal orientation must be read back as exactly +1 or -1")
    shape = plane.get("aperture_shape")
    frame = {"plane_id": plane_id, "component": _COMPONENT, "geometry": _GEOMETRY,
             "selection_tag": plane.get("selection_tag"), "entity_dimension": 2,
             "center_xyz_um": list(center_um), "center_xyz_m": [item * 1e-6 for item in center_um],
             "axis_xyz": list(axis), "native_normal_sign": native_sign,
             # native_normal_sign converts COMSOL's outward normal to the
             # physical +propagation axis; the quadrature formula uses that
             # forward axis directly.
             "physical_forward_normal_xyz": list(axis), "aperture_shape": shape,
             "coordinate_unit": "m", "normal_basis": "global_xyz"}
    if not isinstance(frame["selection_tag"], str) or not frame["selection_tag"]:
        _fail("local native selection tag is required")
    if shape == "circular":
        radius_um = _finite_number(plane.get("sample_radius_um"), "plane sample radius")
        if radius_um <= 0.0:
            _fail("port sample radius must be positive")
        frame.update({"sample_radius_um": radius_um, "sample_radius_m": radius_um * 1e-6})
        quadrature = circular_port_quadrature(
            frame["center_xyz_m"], axis, frame["sample_radius_m"],
            radial_intervals=radial_intervals, angular_points=angular_points)
    elif shape == "rectangle":
        raw_half_widths = plane.get("half_widths_uv_um")
        if not isinstance(raw_half_widths, (list, tuple)) or len(raw_half_widths) != 2:
            _fail("rectangular port needs two native-read-back local half widths")
        half_widths_um = [_finite_number(value, "port half width") for value in raw_half_widths]
        if min(half_widths_um) <= 0.0:
            _fail("rectangular port half widths must be positive")
        frame["half_widths_uv_um"] = half_widths_um
        frame["half_widths_uv_m"] = [value * 1e-6 for value in half_widths_um]
        quadrature = rectangular_port_quadrature(
            frame["center_xyz_m"], axis, frame["half_widths_uv_m"],
            u_intervals=radial_intervals, v_intervals=angular_points)
    else:
        _fail("native plane must bind a registered circular or rectangular aperture")
    identity = {"case_id": case["case_id"], "case_identity_sha256": case.get("case_identity_sha256"),
                "role": role, "source": {key: source[key] for key in required_source},
                "plane": frame, "quadrature_sha256": quadrature["quadrature_sha256"],
                "expressions": list(_VECTOR_FIELDS[role] + _NORMAL_FIELDS)}
    return {"schema_version": 1, "contract_id": _sha256(identity), **identity,
            "quadrature": {key: quadrature[key] for key in (
                "profile", "radial_intervals", "angular_points", "sample_count",
                "quadrature_sha256", "coordinate_unit", "measure_unit") if key in quadrature},
            "field_names": list(_VECTOR_FIELDS[role] + _NORMAL_FIELDS),
            "native_result": "NOT_RUN", "study_or_solver_invoked": False}


def build_raw_field_dispatch(
    contract: Mapping[str, Any], *, source_artifact: str, quadrature: Mapping[str, Any],
    project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(contract, Mapping) or not isinstance(contract.get("contract_id"), str):
        _fail("a complete frozen raw-field contract is required")
    if (not isinstance(quadrature, Mapping)
            or quadrature.get("quadrature_sha256") != contract.get("quadrature_sha256")
            or not isinstance(quadrature.get("coordinates_m"), list)):
        _fail("native sampling coordinates must be reconstructed from the exact bound quadrature")
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        _fail("project-registered full-3D fixture artifact is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return {"operation": "operation_call",
            "arguments": {"operation_id": "code.execute_java", "arguments": {
                "source_artifact": source_artifact,
                "entrypoint": "NativeW23Full3DFixture#run", "mode": "trusted",
                "arguments": {"phase": "raw_fields", "contract": dict(contract),
                              "coordinates_m": quadrature["coordinates_m"],
                              "native_result": "NOT_RUN", "study_or_solver_invoked": False}}},
            "execution": {key: binding[key] for key in
                          ("project_id", "session_id", "model_ref", "expected_revision",
                           "request_id", "idempotency_key")},
            "dispatch_scope": "one managed native Interp readback; no Study.run or solver call"}


def _managed_route_binding(*, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
                           revision: int, request_id: str,
                           idempotency_key: str) -> dict[str, Any]:
    if not isinstance(project_id, str) or not project_id.strip():
        _fail("authoritative registered project id is required")
    if not isinstance(model_ref, Mapping) or set(model_ref) != _MODEL_REF_KEYS:
        _fail("persisted project-bound ModelRef is required")
    if (type(model_ref.get("schema_version")) is not int or model_ref.get("schema_version") != 1
            or any(not isinstance(model_ref.get(key), str) or not model_ref[key].strip()
                   for key in ("session_id", "server_instance_id", "model_tag"))
            or type(model_ref.get("generation")) is not int or model_ref["generation"] < 1):
        _fail("persisted ModelRef is incomplete or malformed")
    if not isinstance(model_tag, str) or not model_tag.strip():
        _fail("native model tag is required")
    if type(revision) is not int or revision < 0:
        _fail("managed expected revision must be a nonnegative integer")
    if any(not isinstance(value, str) or not value.strip()
           for value in (request_id, idempotency_key)):
        _fail("managed request and idempotency identifiers are required")
    if model_ref.get("model_tag") != model_tag:
        _fail("persisted ModelRef tag differs from the native model tag")
    session_id = model_ref.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        _fail("managed operations require the exact session_id from the persisted ModelRef")
    return {"project_id": project_id, "model_ref": dict(model_ref), "model_tag": model_tag,
            "session_id": session_id, "expected_revision": revision, "request_id": request_id,
            "idempotency_key": idempotency_key}


def _operation_call(operation_id: str, arguments: Mapping[str, Any], *,
                    binding: Mapping[str, Any], dispatch_scope: str) -> dict[str, Any]:
    return {"operation": "operation_call",
            "arguments": {"operation_id": operation_id, "arguments": dict(arguments)},
            "execution": {key: binding[key] for key in
                          ("project_id", "session_id", "model_ref", "expected_revision", "request_id", "idempotency_key")},
            "dispatch_scope": dispatch_scope}


def build_full3d_study_run_dispatch(
    *, project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str, timeout_s: float,
) -> dict[str, Any]:
    """Build exactly one managed Study.run for the ordered 3-step std3d study."""
    timeout = _finite_number(timeout_s, "managed Study.run timeout")
    if timeout <= 0:
        _fail("managed Study.run timeout must be positive")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("study.run", {
        "study": {"segments": [{"collection": "study", "tag": "std3d"}]},
        "timeout_s": timeout,
    }, binding=binding, dispatch_scope=(
        "one actual managed Study.run submission for std3d; configured step order is "
        "bmaInput3d, bmaOutput3d, freq3d; underlying solver-call count remains runtime-evidence-bound"))


def build_full3d_solution_inventory_dispatch(
    *, source_artifact: str, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Read the generated solver-tree StudyStep bindings after a managed solve."""
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": {"phase": "solution_inventory"},
    }, binding=binding, dispatch_scope=(
        "managed native readback of std3d step bindings, generated solver tree and datasets; no solve"))


def build_full3d_dataset_list_dispatch(
    *, project_id: str, model_ref: Mapping[str, Any], model_tag: str, revision: int,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("dataset.list", {}, binding=binding,
                           dispatch_scope="managed native result-dataset inventory readback")


def build_full3d_dataset_indices_dispatch(
    dataset_tag: str, *, project_id: str, model_ref: Mapping[str, Any], model_tag: str,
    revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(dataset_tag, str) or not _TAG.fullmatch(dataset_tag):
        _fail("solution dataset tag must be an exact COMSOL tag")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("dataset.solution_indices", {"path": dataset_tag}, binding=binding,
                           dispatch_scope="managed native dataset-to-solution/index metadata readback")


def build_full3d_save_dispatch(
    *, source_artifact: str, path: str, project_id: str, model_ref: Mapping[str, Any],
    model_tag: str, revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Save one case model below its authoritative registered project workspace."""
    if not isinstance(path, str) or not path.strip():
        _fail("managed MPH destination path is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("code.execute_java", {
        "source_artifact": source_artifact,
        "entrypoint": "NativeW23Full3DFixture#run",
        "mode": "trusted", "arguments": {"phase": "save", "path": path},
    }, binding=binding, dispatch_scope=(
        "save one native case into the persisted project's child workspace; no solver call"))


def build_full3d_model_load_request(
    *, path: str, project_id: str, session_id: str, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Reopen one existing MPH using the production project-scoped model_load route."""
    if not isinstance(path, str) or not path.strip():
        _fail("managed saved-model path is required")
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, session_id, request_id, idempotency_key)):
        _fail("authoritative project/session and managed load identifiers are required")
    return {"operation": "model_load", "arguments": {"path": path},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_id,
                          "idempotency_key": idempotency_key},
            "dispatch_scope": "one production project-scoped managed model_load; no caller root override"}


def build_full3d_project_create_request(
    *, label: str, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    """Create the registered child workspace before any engine birth."""
    if not isinstance(label, str) or not label.strip():
        _fail("registered science project label is required")
    if any(not isinstance(value, str) or not value.strip()
           for value in (request_id, idempotency_key)):
        _fail("project.create requires distinct request and idempotency identifiers")
    return {"operation": "project.create",
            "arguments": {"label": label.strip(), "workspace": "science",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}},
            "execution": {"request_id": request_id, "idempotency_key": idempotency_key},
            "dispatch_scope": "offline authoritative project.create; no Worker or COMSOL engine call"}


def bind_full3d_project_create_response(
    response: Mapping[str, Any], *, authorized_container: str,
) -> dict[str, Any]:
    """Bind only the persisted project.create record and its exact child path."""
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("production project.create did not succeed")
    data = response.get("data")
    project = data.get("project") if isinstance(data, Mapping) else None
    if not isinstance(project, Mapping):
        _fail("project.create response lacks the persisted project record")
    project_id, workspace = project.get("project_id"), project.get("workspace")
    if (not isinstance(project_id, str) or not project_id.strip()
            or not isinstance(workspace, str) or not workspace.strip()):
        _fail("project.create response lacks authoritative project_id/workspace")
    from pathlib import Path
    try:
        root = Path(authorized_container).resolve(strict=True)
        child = Path(workspace).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(f"registered science workspace cannot be resolved: {type(exc).__name__}")
    if not root.is_dir() or not child.is_dir() or child.parent != root or child.name != "science":
        _fail("registered science workspace is not the exact authorized child")
    policy = project.get("policy")
    permissions = policy.get("permissions") if isinstance(policy, Mapping) else None
    if not isinstance(permissions, list) or not {
            "inspect", "project_write", "compute", "trusted_code"}.issubset(set(permissions)):
        _fail("persisted science project policy lacks the required inspect/write/compute/trusted-code grants")
    return {"status": "PROJECT_CREATE_BOUND", "project_id": project_id,
            "workspace": str(child), "schema_version": project.get("schema_version"),
            "revision": project.get("revision"), "label": project.get("label"),
            "contract": project.get("contract"), "policy": project.get("policy"),
            "native_result": "NOT_RUN"}


def build_full3d_server_connect_request(
    *, project_id: str, host: str, port: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, host, request_id, idempotency_key)):
        _fail("server_connect requires project, endpoint, request and idempotency identities")
    if type(port) is not int or not 1 <= port <= 65535:
        _fail("server_connect port must be a valid TCP port")
    return {"operation": "server_connect", "arguments": {"host": host, "port": port},
            "execution": {"project_id": project_id, "request_id": request_id,
                          "idempotency_key": idempotency_key},
            "dispatch_scope": "connect this managed session to the already-owned server endpoint"}


def bind_full3d_connection_response(
    response: Mapping[str, Any], *, expected_endpoint: str,
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("managed server_connect did not succeed")
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(worker, Mapping):
        _fail("server_connect lacks its runtime connection readback")
    session_id, server_id = data.get("session_id"), data.get("server_instance_id")
    endpoint = data.get("endpoint")
    epoch = worker.get("generation", worker.get("connection_epoch"))
    if (not isinstance(session_id, str) or not session_id.strip()
            or not isinstance(server_id, str) or not server_id.strip()
            or endpoint != expected_endpoint or type(epoch) is not int or epoch < 1):
        _fail("server_connect session/epoch/endpoint differs from the owned runtime")
    return {"status": "MANAGED_SESSION_BOUND", "session_id": session_id,
            "server_instance_id": server_id, "worker_epoch": epoch,
            "endpoint": endpoint, "native_result": "NOT_RUN"}


def build_full3d_model_create_request(
    *, project_id: str, session_id: str, name: str,
    request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if any(not isinstance(value, str) or not value.strip()
           for value in (project_id, session_id, name, request_id, idempotency_key)):
        _fail("model_create requires authoritative project/session and request identities")
    return {"operation": "model_create", "arguments": {"name": name},
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_id, "idempotency_key": idempotency_key},
            "dispatch_scope": "one production project-scoped model_create"}


def bind_full3d_model_create_response(
    response: Mapping[str, Any], *, project_id: str, session: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(response, Mapping) or response.get("success") is not True:
        _fail("managed project-scoped model_create did not succeed")
    execution = response.get("execution")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS:
        _fail("model_create did not return an exact ModelRef")
    if (ref.get("schema_version") != 1 or ref.get("session_id") != session.get("session_id")
            or ref.get("server_instance_id") != session.get("server_instance_id")
            or not isinstance(ref.get("model_tag"), str) or type(ref.get("generation")) is not int
            or ref["generation"] < 1 or type(revision) is not int or revision < 0):
        _fail("model_create ModelRef/revision is not bound to the observed session epoch")
    return {"status": "MODEL_CREATED_PROJECT_SCOPED", "project_id": project_id,
            "session_id": session["session_id"], "worker_epoch": session["worker_epoch"],
            "model_ref": dict(ref), "model_tag": ref["model_tag"],
            "revision": revision, "native_result": "NOT_RUN"}


def stage_full3d_fixture_source(
    source_path: str | Path, *, project_workspace: str | Path,
    expected_sha256: str,
) -> dict[str, Any]:
    """Copy the exact Java source into the registered science workspace once."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        _fail("frozen full-3D Java source SHA-256 is required")
    try:
        source = Path(source_path)
        if source.is_symlink():
            _fail("fixture source must not be a symlink")
        source = source.resolve(strict=True)
        workspace = Path(project_workspace).resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(f"fixture source/workspace cannot be resolved: {type(exc).__name__}")
    if not source.is_file() or source.suffix.lower() != ".java" or not workspace.is_dir():
        _fail("fixture source must be a Java file and the registered workspace an existing directory")
    payload = source.read_bytes()
    source_hash = hashlib.sha256(payload).hexdigest()
    if source_hash != expected_sha256:
        _fail("full-3D Java source differs from the frozen source hash")
    destination = workspace / "NativeW23Full3DFixture.java"
    if destination.exists() or destination.is_symlink():
        _fail("refusing to overwrite a preexisting project-local Java source")
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
    except OSError as exc:
        _fail(f"project-local Java source staging failed: {type(exc).__name__}")
    if destination.is_symlink() or destination.resolve(strict=True).parent != workspace:
        _fail("staged Java source escaped the registered project workspace")
    staged_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
    if staged_hash != expected_sha256:
        _fail("staged Java source SHA-256 differs from its frozen source")
    return {"status": "PROJECT_LOCAL_SOURCE_STAGED", "source_artifact": destination.name,
            "workspace": str(workspace), "path": str(destination), "bytes": len(payload),
            "sha256": staged_hash, "native_result": "NOT_RUN"}


def _managed_job_id(response: Mapping[str, Any]) -> str | None:
    execution, data = response.get("execution"), response.get("data")
    candidates = [execution.get("job_id") if isinstance(execution, Mapping) else None,
                  data.get("job_id") if isinstance(data, Mapping) else None]
    return next((value for value in candidates if isinstance(value, str) and value), None)


def dispatch_public_managed_route(
    daemon: Any, request: Mapping[str, Any], *, label: str,
    timeout_s: float = 300.0, poll_interval_s: float = 0.1,
) -> dict[str, Any]:
    """Use only ControlDaemon.dispatch, including public job.wait for queued work.

    The operation request is sent once. Repeated job.wait calls observe only the
    same durable job; any dispatch/wait ambiguity is returned as UNKNOWN and is
    never turned into a second operation submission.
    """
    if not callable(getattr(daemon, "dispatch", None)):
        _fail("managed W23 route requires the public ControlDaemon.dispatch API")
    timeout = _finite_number(timeout_s, "public managed-route wait timeout")
    interval = _finite_number(poll_interval_s, "public managed-route poll interval")
    if timeout <= 0.0 or interval <= 0.0:
        _fail("managed-route timeout and poll interval must be positive")
    raw_request = dict(request)
    try:
        initial = daemon.dispatch(raw_request)
    except BaseException as exc:
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            f"public dispatch raised {type(exc).__name__}; request is not replayed") from exc
    if not isinstance(initial, Mapping):
        raise ManagedRouteOutcomeError(label, "UNKNOWN", "dispatch returned a non-object; request is not replayed",
                                       response=initial)
    job_id = _managed_job_id(initial)
    if job_id is None:
        if initial.get("success") is True:
            return {"response": dict(initial), "dispatch_response": dict(initial),
                    "job_wait_responses": [], "job_id": None,
                    "outcome": "SUCCEEDED", "retry_forbidden": True}
        error = initial.get("error")
        code = error.get("code") if isinstance(error, Mapping) else None
        outcome = "UNKNOWN" if code in {"EXECUTION_STATE_UNKNOWN", "WORKER_STATE_UNKNOWN"} else "FAILED"
        raise ManagedRouteOutcomeError(label, outcome, "public dispatch did not succeed",
                                       response=dict(initial))

    deadline = time.monotonic() + timeout
    waits: list[dict[str, Any]] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                "public job.wait deadline expired while observing the same job; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        wait_request = {"operation": "job.wait",
                        "arguments": {"job_id": job_id,
                                      "timeout_s": min(remaining, max(interval, 1.0)),
                                      "poll_interval_s": min(interval, 1.0)},
                        "execution": {}}
        try:
            observed = daemon.dispatch(wait_request)
        except BaseException as exc:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait raised {type(exc).__name__}; job {job_id} remains unreplayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id) from exc
        if not isinstance(observed, Mapping) or observed.get("success") is not True:
            waits.append(dict(observed) if isinstance(observed, Mapping) else {"non_object": repr(observed)})
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait could not establish job {job_id} state; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        waits.append(dict(observed))
        job = observed.get("data")
        if not isinstance(job, Mapping) or job.get("job_id") != job_id:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"public job.wait response is not bound to exact job {job_id}",
                response={"dispatch": dict(initial), "job_wait_responses": waits}, job_id=job_id)
        status = job.get("status")
        if job.get("wait_expired") is True or status not in {"SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED", "UNKNOWN", "LOST"}:
            continue
        if status != "SUCCEEDED":
            raise ManagedRouteOutcomeError(label, "UNKNOWN" if status in {"UNKNOWN", "LOST"} else "FAILED",
                f"job {job_id} reached terminal status {status}; operation is not replayed",
                response={"dispatch": dict(initial), "job_wait_responses": waits, "job": dict(job)},
                job_id=job_id)
        terminal = job.get("result")
        if not isinstance(terminal, Mapping) or terminal.get("success") is not True:
            raise ManagedRouteOutcomeError(label, "UNKNOWN",
                f"job {job_id} is marked SUCCEEDED but its terminal result is absent or inconsistent",
                response={"dispatch": dict(initial), "job_wait_responses": waits, "job": dict(job)},
                job_id=job_id)
        return {"response": dict(terminal), "dispatch_response": dict(initial),
                "job_wait_responses": waits, "job_id": job_id,
                "outcome": "SUCCEEDED", "retry_forbidden": True}


def _java_action_readback(response: Mapping[str, Any], label: str) -> dict[str, Any]:
    if response.get("success") is not True:
        raise ManagedRouteOutcomeError(label, "FAILED", "managed Java operation failed",
                                       response=dict(response))
    data = response.get("data")
    worker = data.get("worker") if isinstance(data, Mapping) else None
    wrapper = data.get("readback") if isinstance(data, Mapping) else None
    if (not isinstance(worker, Mapping) or worker.get("ok") is not True
            or worker.get("status") != "SUCCEEDED" or not isinstance(wrapper, Mapping)
            or wrapper.get("executed") is not True or not isinstance(wrapper.get("readback"), Mapping)):
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            "terminal managed Java result lacks matching Worker/readback evidence",
            response=dict(response))
    if not isinstance(worker.get("result"), Mapping) or worker["result"].get("readback") != wrapper["readback"]:
        raise ManagedRouteOutcomeError(label, "UNKNOWN",
            "Worker and managed Java readbacks disagree", response=dict(response))
    return dict(wrapper["readback"])


def _updated_full3d_model_state(
    response: Mapping[str, Any], *, prior_model: Mapping[str, Any],
) -> dict[str, Any]:
    execution = response.get("execution")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    revision = execution.get("revision") if isinstance(execution, Mapping) else None
    if not isinstance(ref, Mapping) or set(ref) != _MODEL_REF_KEYS or type(revision) is not int or revision < 0:
        raise ManagedRouteOutcomeError("model state readback", "UNKNOWN",
            "managed mutation omitted the authoritative returned ModelRef/revision; no next mutation is issued",
            response=dict(response))
    for key in _MODEL_REF_KEYS:
        if ref.get(key) != prior_model.get("model_ref", {}).get(key):
            raise ManagedRouteOutcomeError("model state readback", "UNKNOWN",
                f"managed mutation changed ModelRef identity field {key}; no next mutation is issued",
                response=dict(response))
    return {**dict(prior_model), "model_ref": dict(ref), "revision": revision}


def _check_full3d_fixture_readback(
    readback: Mapping[str, Any], *, recipe_sha256: str, project_id: str,
    model: Mapping[str, Any], case: Mapping[str, Any] | None = None,
) -> None:
    status = "GEOMETRY_CASE_CONFIGURED_NOT_SOLVED" if case is not None else "BUILT_CONFIGURED_NOT_SOLVED"
    if (readback.get("fixture_id") != "w23_full3d_fiber_ball_lens_vector_pml_v1"
            or readback.get("recipe_sha256") != recipe_sha256
            or readback.get("status") != status or readback.get("native_result") != "NOT_RUN"
            or readback.get("study_or_solver_invoked") is not False):
        _fail("native full-3D fixture readback is not the exact configured/no-solve result")
    identity = readback.get("managed_identity")
    if (not isinstance(identity, Mapping) or identity.get("project_id") != project_id
            or identity.get("model_tag") != model.get("model_tag")
            or identity.get("model_ref") != model.get("model_ref")):
        _fail("native fixture readback is not bound to the current authoritative project/ModelRef")
    if case is not None:
        expected_pairs = (("case_id", "case_id"), ("case_identity_sha256", "case_identity_sha256"),
                          ("experiment_id", "experiment_id"), ("factor", "factor"),
                          ("factor_value", "value"))
        if any(readback.get(output_key) != case.get(case_key)
               for output_key, case_key in expected_pairs):
            _fail("native geometry readback differs from the exact registered case identity")


def execute_full3d_managed_configuration_chain(
    daemon: Any, *, project_create_response: Mapping[str, Any], authorized_container: str | Path,
    connection_response: Mapping[str, Any], expected_endpoint: str,
    fixture_source_path: str | Path, fixture_source_sha256: str,
    recipe: Mapping[str, Any], experiment_id: str, model_name: str,
    wait_timeout_s: float = 300.0,
) -> dict[str, Any]:
    """Stage source, create/bind one model, and run the no-solve 3-D build/baseline chain.

    This only reaches a configured fixture. It returns the exact single
    ``study.run`` request for a later separately reviewed native campaign and
    never submits it here.
    """
    from tools.w23_full3d import (
        bind_case_matrix, build_full3d_case_dispatch, build_full3d_fixture_dispatch,
        canonical_full3d_recipe, verify_recipe,
    )

    if not isinstance(recipe, Mapping):
        recipe = canonical_full3d_recipe()
    recipe_check = verify_recipe(recipe)
    project = bind_full3d_project_create_response(
        project_create_response, authorized_container=str(authorized_container))
    session = bind_full3d_connection_response(
        connection_response, expected_endpoint=expected_endpoint)
    staged = stage_full3d_fixture_source(fixture_source_path,
        project_workspace=project["workspace"], expected_sha256=fixture_source_sha256)

    def call_id(label: str) -> tuple[str, str]:
        return f"w23f3d-{label}-{uuid4()}", f"w23f3d-idem-{label}-{uuid4()}"

    request_id, key = call_id("model-create")
    create_request = build_full3d_model_create_request(
        project_id=project["project_id"], session_id=session["session_id"], name=model_name,
        request_id=request_id, idempotency_key=key)
    created = dispatch_public_managed_route(daemon, create_request, label="model_create",
                                             timeout_s=wait_timeout_s)
    model = bind_full3d_model_create_response(
        created["response"], project_id=project["project_id"], session=session)

    request_id, key = call_id("fixture-build")
    build_request = build_full3d_fixture_dispatch(
        recipe, source_artifact=staged["source_artifact"], project_id=project["project_id"],
        model_ref=model["model_ref"], model_tag=model["model_tag"], revision=model["revision"],
        request_id=request_id, idempotency_key=key)
    built = dispatch_public_managed_route(daemon, build_request, label="full3d_fixture_build",
                                          timeout_s=wait_timeout_s)
    model = _updated_full3d_model_state(built["response"], prior_model=model)
    build_readback = _java_action_readback(built["response"], "full3d_fixture_build")
    _check_full3d_fixture_readback(
        build_readback, recipe_sha256=recipe_check["recipe_sha256"],
        project_id=project["project_id"], model=model)

    plan = bind_case_matrix(recipe, project_id=project["project_id"],
        model_ref=model["model_ref"], model_tag=model["model_tag"], revision=model["revision"],
        experiment_id=experiment_id)
    baseline = next((row for row in plan["cases"] if row.get("factor") == "baseline"), None)
    if not isinstance(baseline, Mapping):
        _fail("registered full-3D case plan lacks its unique baseline row")
    if sum(1 for row in plan["cases"] if row.get("factor") == "baseline") != 1:
        _fail("registered full-3D case plan has duplicate baseline rows")
    request_id, key = call_id("baseline-apply")
    apply_request = build_full3d_case_dispatch(
        plan, baseline, source_artifact=staged["source_artifact"],
        request_id=request_id, idempotency_key=key)
    applied = dispatch_public_managed_route(daemon, apply_request, label="full3d_baseline_apply",
                                            timeout_s=wait_timeout_s)
    model = _updated_full3d_model_state(applied["response"], prior_model=model)
    apply_readback = _java_action_readback(applied["response"], "full3d_baseline_apply")
    _check_full3d_fixture_readback(
        apply_readback, recipe_sha256=recipe_check["recipe_sha256"],
        project_id=project["project_id"], model=model, case=baseline)

    request_id, key = call_id("study-run")
    study_request = build_full3d_study_run_dispatch(
        project_id=project["project_id"], model_ref=model["model_ref"],
        model_tag=model["model_tag"], revision=model["revision"], request_id=request_id,
        idempotency_key=key, timeout_s=wait_timeout_s)
    return {"status": "FULL3D_CONFIGURED_STUDY_READY_NOT_RUN",
            "project": project, "session": session, "staged_fixture": staged,
            "model": model, "recipe": {"fixture_id": recipe.get("fixture_id"),
                                        "recipe_sha256": recipe_check["recipe_sha256"]},
            "case_plan": plan, "selected_case": dict(baseline),
            "build_readback": build_readback, "apply_readback": apply_readback,
            "study_run_request": study_request,
            "study_or_solver_invoked": False, "native_result": "NOT_RUN",
            "native_solve_authorized_or_submitted": False,
            "calls": {"model_create": created, "fixture_build": built,
                      "baseline_apply": applied}}


def build_full3d_mode_overlap_definition(
    sources: Mapping[str, Mapping[str, Any]], *, output_normal_sign: int,
    input_normal_sign: int, capture_normal_sign: int, power_floor_w: float = 1e-12,
) -> dict[str, Any]:
    """Bind native 3-D source identities and named boundaries to result.mode_overlap."""
    expected_roles = {"signal", "reference_mode", "incident_reference"}
    if not isinstance(sources, Mapping) or set(sources) != expected_roles:
        _fail("native source inventory must uniquely bind signal, output mode and input mode")
    for value, label in ((output_normal_sign, "output"), (input_normal_sign, "input"),
                         (capture_normal_sign, "capture")):
        if type(value) is not int or value not in {-1, 1}:
            _fail(f"native {label} surface normal sign must be measured as exactly +1 or -1")
    if capture_normal_sign != output_normal_sign:
        _fail("capture and output surfaces must share the same physical forward-normal convention")
    floor = _finite_number(power_floor_w, "native power floor")
    if floor <= 0:
        _fail("native 3-D power floor must be positive")

    def source(role: str) -> dict[str, Any]:
        row = sources[role]
        required = ("dataset_id", "solution_id", "outer_index", "inner_index")
        if not isinstance(row, Mapping) or any(key not in row for key in required):
            _fail(f"native {role} source is missing exact dataset/solution/inner/outer identity")
        if (not isinstance(row["dataset_id"], str) or not row["dataset_id"].strip()
                or not isinstance(row["solution_id"], str) or not row["solution_id"].strip()
                or any(type(row[key]) is not int or row[key] < 1
                       for key in ("outer_index", "inner_index"))):
            _fail(f"native {role} source identity is malformed")
        return {key: row[key] for key in required}

    def fields(electric_prefix: str, magnetic_prefix: str, suffix: str = "") -> dict[str, Any]:
        return {"electric": {axis: f"ewfd.{electric_prefix}{axis}{suffix}" for axis in _AXES},
                "magnetic": {axis: f"ewfd.{magnetic_prefix}{axis}{suffix}" for axis in _AXES}}

    definition = {
        "schema_version": "1.0.0",
        "phasor_convention": {
            "time_dependence": "exp(-i omega t)",
            "complex_field_representation": "full_physical_complex_phasor_including_reconstructed_envelope_phase",
        },
        "output_surface": {"plane_id": "receiver_port",
                           "selection": {"component": "comp3d", "geometry": "geom3d",
                                         "tag": "sel3dOutputPort"},
                           "normal_sign": output_normal_sign},
        "signal": {"field_id": "w23_full3d_frequency_signal", "source": source("signal"),
                   "fields": fields("E", "H")},
        "reference_mode": {"mode_id": "w23_full3d_output_mode_1",
                           "mode_axis_parameter": "modeIndex",
                           "source": source("reference_mode"),
                           "fields": fields("Emode", "Hmode", "_2")},
        "incident_reference": {
            "reference_id": "w23_full3d_input_mode_1", "input_plane_id": "input_port",
            "selection": {"component": "comp3d", "geometry": "geom3d", "tag": "sel3dInputPort"},
            "normal_sign": input_normal_sign,
            "source": source("incident_reference"),
            "fields": fields("Emode", "Hmode", "_1"),
        },
        "power_floor": {"value": floor, "unit": "W"},
        "capture": {"aperture_id": "w23_full3d_output_core_capture",
                    "plane_id": "receiver_port",
                    "selection": {"component": "comp3d", "geometry": "geom3d",
                                  "tag": "sel3dOutputCoreCapture"},
                    "normal_sign": capture_normal_sign,
                    "incident_reference_id": "w23_full3d_input_mode_1"},
    }
    return definition


def resolve_full3d_native_sources(
    dataset_rows: Sequence[Mapping[str, Any]],
    index_by_tag: Mapping[str, Mapping[str, Any]],
    solver_tags_by_step: Mapping[str, Sequence[str]], *,
    frequency_hz: float,
) -> dict[str, Any]:
    """Resolve unique native frequency/BMA sources from live managed inventories.

    No tag naming convention or dataset order is used as a proxy for solver
    provenance. Every COMSOL Solution dataset must have complete native axes.
    """
    expected_frequency = _finite_number(frequency_hz, "registered optical frequency")
    if expected_frequency <= 0:
        _fail("registered optical frequency must be positive")
    solution_rows = [row for row in dataset_rows
                     if isinstance(row, Mapping)
                     and str(row.get("type_id", "")).lower() == "solution"]
    unresolved = [row.get("tag") for row in solution_rows
                  if not isinstance(row.get("tag"), str)
                  or row.get("tag") not in index_by_tag
                  or index_by_tag[row["tag"]].get("binding_complete") is not True]
    if unresolved:
        _fail("native source uniqueness cannot be proven while Solution dataset axes are incomplete: "
              + ", ".join(str(tag) for tag in unresolved))
    required_steps = {"signal": "freq3d", "reference_mode": "bmaOutput3d",
                      "incident_reference": "bmaInput3d"}
    role_solutions: dict[str, set[str]] = {}
    for role, step in required_steps.items():
        values = solver_tags_by_step.get(step)
        if not isinstance(values, (list, tuple)) or not values:
            _fail(f"generated native solver tree does not bind configured step {step}")
        if any(not isinstance(value, str) or not value.strip() for value in values):
            _fail(f"solver tree contains a malformed sequence tag for {step}")
        role_solutions[role] = set(values)

    chosen: dict[str, dict[str, Any]] = {}
    aliases = {"f", "freq", "frequency"}
    unit_scale = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "THz": 1e12}
    for role, allowed_solutions in role_solutions.items():
        candidates: list[dict[str, Any]] = []
        for row in solution_rows:
            tag = row.get("tag")
            if (not isinstance(tag, str) or row.get("component") != "comp3d"
                    or row.get("geometry") != "geom3d"):
                continue
            axes = index_by_tag.get(tag)
            if not isinstance(axes, Mapping) or axes.get("binding_complete") is not True:
                continue
            solution_id = axes.get("solution")
            if solution_id not in allowed_solutions or row.get("solution") != solution_id:
                continue
            params = axes.get("parameters")
            pairs = params.get("by_pair") if isinstance(params, Mapping) else None
            if not isinstance(pairs, Mapping):
                continue
            for pair_key, pair in pairs.items():
                if not isinstance(pair, Mapping):
                    continue
                names, values, units = (list(pair.get(key) or []) for key in ("names", "values", "units"))
                if len(names) != len(values) or len(names) != len(units):
                    continue
                frequency_slots = [index for index, name in enumerate(names)
                                   if str(name).strip().lower() in aliases]
                if len(frequency_slots) != 1:
                    continue
                fi = frequency_slots[0]
                unit = str(units[fi] or "").strip()
                value = values[fi]
                if (unit not in unit_scale or isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(float(value))
                        or not math.isclose(float(value) * unit_scale[unit], expected_frequency,
                                            rel_tol=1e-12, abs_tol=0.0)):
                    continue
                mode_slots = [index for index, name in enumerate(names)
                              if str(name).strip().lower() == "modeindex"]
                if role == "signal":
                    if mode_slots and (len(mode_slots) != 1 or values[mode_slots[0]] != 1):
                        continue
                elif (len(mode_slots) != 1 or isinstance(values[mode_slots[0]], bool)
                      or values[mode_slots[0]] != 1):
                    continue
                try:
                    outer, inner = (int(value) for value in str(pair_key).split(":"))
                except (TypeError, ValueError):
                    continue
                solnum = pair.get("solnum")
                if (outer < 1 or inner < 1 or type(solnum) is not int or solnum < 1):
                    continue
                candidates.append({"dataset_id": tag, "solution_id": solution_id,
                    "outer_index": outer, "inner_index": inner, "solnum": solnum,
                    "frequency_hz": expected_frequency,
                    "native_parameter_names": names, "native_parameter_values": values,
                    "native_parameter_units": units})
        if len(candidates) != 1:
            _fail(f"{role} requires exactly one native dataset/index tuple at the frozen frequency; found {len(candidates)}")
        chosen[role] = candidates[0]
    signal, mode, incident = (chosen[key] for key in
                              ("signal", "reference_mode", "incident_reference"))
    if (signal["outer_index"] != mode["outer_index"]
            or signal["outer_index"] != incident["outer_index"]
            or signal["inner_index"] != incident["inner_index"]):
        _fail("native full-3D source indices do not bind one shared outer and signal/input inner index")
    return {"status": "SOFTWARE_UNIQUE_FULL3D_NATIVE_SOURCE_BINDINGS",
            "native_result": "NOT_RUN", "sources": chosen,
            "solver_step_sequence_tags": {key: list(value) for key, value in solver_tags_by_step.items()},
            "caller_arrays_used": False}


def build_full3d_mode_overlap_dispatch(
    definition: Mapping[str, Any], *, project_id: str, model_ref: Mapping[str, Any],
    model_tag: str, revision: int, request_id: str, idempotency_key: str,
) -> dict[str, Any]:
    if not isinstance(definition, Mapping):
        _fail("native result.mode_overlap definition is required")
    binding = _managed_route_binding(
        project_id=project_id, model_ref=model_ref, model_tag=model_tag, revision=revision,
        request_id=request_id, idempotency_key=idempotency_key)
    return _operation_call("result.mode_overlap", {"definition": dict(definition)},
                           binding=binding,
                           dispatch_scope=("one managed native result.mode_overlap call; "
                                           "no caller field arrays; native result remains separately audited"))


def _expression_map(raw: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, list[complex]]:
    if (raw.get("native_result") != "COMSOL_NATIVE_RAW"
            or raw.get("study_or_solver_invoked") is not False
            or raw.get("complex_readback") is not True):
        _fail("field source must be a fresh COMSOL native complex readback without hidden solve")
    rows = raw.get("expressions")
    if not isinstance(rows, list):
        _fail("native Interp response must include expression rows")
    result: dict[str, list[complex]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("expression"), str):
            _fail("native expression row is malformed")
        name = row["expression"]
        if name in result:
            _fail(f"native expression {name!r} is repeated")
        real, imag = row.get("real"), row.get("imag")
        count = contract["quadrature"]["sample_count"]
        if not isinstance(real, list) or not isinstance(imag, list) or len(real) != count or len(imag) != count:
            _fail(f"native field {name!r} sample axes differ from the exact quadrature contract")
        values = []
        for r, i in zip(real, imag):
            a, b = _finite_number(r, name + ".real"), _finite_number(i, name + ".imag")
            values.append(complex(a, b))
        result[name] = values
    if set(result) != set(contract["field_names"]):
        _fail("native Interp expression inventory differs from the frozen six-vector-plus-normal contract")
    return result


def _readback_coordinates(raw: Mapping[str, Any], expected: Sequence[Sequence[float]]) -> None:
    coordinates = raw.get("coordinates_m")
    if not isinstance(coordinates, list) or len(coordinates) != 3:
        _fail("native Interp must read back three global coordinate arrays in SI metres")
    count = len(expected)
    if any(not isinstance(axis, list) or len(axis) != count for axis in coordinates):
        _fail("native Interp coordinate axes do not match the frozen point count")
    for index, point in enumerate(expected):
        for axis in range(3):
            observed = _finite_number(coordinates[axis][index], "native coordinate")
            if not math.isclose(observed, float(point[axis]), rel_tol=0.0, abs_tol=2e-12):
                _fail("native Interp coordinate readback differs from the independently reconstructed local plane grid")


def validate_native_field_readback(
    contract: Mapping[str, Any], raw: Mapping[str, Any], *,
    expected_quadrature: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(contract, Mapping) or not isinstance(raw, Mapping):
        _fail("native field response and frozen contract are required")
    expected_id = contract.get("contract_id")
    if raw.get("contract_id") != expected_id or raw.get("quadrature_sha256") != contract.get("quadrature_sha256"):
        _fail("native field response differs from the exact field/quadrature contract")
    if raw.get("source") != contract.get("source") or raw.get("plane") != contract.get("plane"):
        _fail("native field response has a foreign dataset/index or port plane")
    if raw.get("native_result") != "COMSOL_NATIVE_RAW":
        _fail("software or mock field values cannot be promoted to native readback")
    cleanup = raw.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("created") is not True \
            or cleanup.get("removed") is not True or cleanup.get("cleanup_failed") is not False:
        _fail("temporary Interp node cleanup must be fully observed")
    if raw.get("sample_count") != contract["quadrature"]["sample_count"]:
        _fail("native field readback sample count differs from contract")
    _readback_coordinates(raw, expected_quadrature["coordinates_m"])
    fields = _expression_map(raw, contract)
    axis = _vector3(contract["plane"]["axis_xyz"], "expected plane axis")
    sign = contract["plane"]["native_normal_sign"]
    normal_values = [fields[name] for name in _NORMAL_FIELDS]
    expected_normal = tuple(sign * item for item in axis)
    for sample in range(contract["quadrature"]["sample_count"]):
        observed = tuple(normal_values[index][sample] for index in range(3))
        if max(abs(item.imag) for item in observed) > 1e-10:
            _fail("native surface normal readback must be real")
        real_normal = tuple(item.real for item in observed)
        norm = math.sqrt(sum(item * item for item in real_normal))
        dot = sum(real_normal[index] * expected_normal[index] for index in range(3))
        if abs(norm - 1.0) > 1e-8 or abs(dot - 1.0) > 1e-6:
            _fail("native sampled boundary normals are not consistently outward along the registered port axis")
    return {"status": "NATIVE_RAW_VECTOR_READBACK_VALIDATED", "native_result": "COMSOL_NATIVE_RAW",
            "contract_id": expected_id, "source": dict(contract["source"]),
            "plane": dict(contract["plane"]), "sample_count": contract["quadrature"]["sample_count"],
            "field_names": list(contract["field_names"]), "cleanup": dict(cleanup),
            "coordinate_sha256": _sha256(expected_quadrature["coordinates_m"]),
            "normal_orientation": "native normals match the measured outward sign; declared sign maps to physical propagation direction"}


def _cross_dot(e: Sequence[complex], h: Sequence[complex], normal: Sequence[float], *,
               conjugate_e: bool = False, conjugate_h: bool = False) -> complex:
    values_e = [value.conjugate() if conjugate_e else value for value in e]
    values_h = [value.conjugate() if conjugate_h else value for value in h]
    cross = (values_e[1] * values_h[2] - values_e[2] * values_h[1],
             values_e[2] * values_h[0] - values_e[0] * values_h[2],
             values_e[0] * values_h[1] - values_e[1] * values_h[0])
    return sum(cross[index] * normal[index] for index in range(3))


def _field_vector(fields: Mapping[str, list[complex]], role: str, prefix: str,
                  index: int) -> tuple[complex, complex, complex]:
    if role in {"signal", "capture_signal"}:
        expression = lambda axis: f"ewfd.{prefix}{axis}"
    elif role == "reference_mode":
        expression = lambda axis: f"ewfd.{prefix}mode{axis}_2"
    elif role == "incident_reference":
        expression = lambda axis: f"ewfd.{prefix}mode{axis}_1"
    else:
        _fail("field vector role is not registered")
    return tuple(fields[expression(axis)][index] for axis in _AXES)  # type: ignore[return-value]


def _integrate(values: Sequence[complex], weights: Sequence[float]) -> complex:
    if len(values) != len(weights):
        _fail("quadrature field and area-weight axes differ")
    return sum((value * weight for value, weight in zip(values, weights)), 0j)


def independent_mode_overlap_integrals(
    signal_raw: Mapping[str, Any], mode_raw: Mapping[str, Any], incident_raw: Mapping[str, Any], *,
    signal_contract: Mapping[str, Any], mode_contract: Mapping[str, Any], incident_contract: Mapping[str, Any],
    signal_quadrature: Mapping[str, Any], mode_quadrature: Mapping[str, Any],
    incident_quadrature: Mapping[str, Any], power_floor_w: float = 1e-12,
    capture_raw: Mapping[str, Any] | None = None,
    capture_contract: Mapping[str, Any] | None = None,
    capture_quadrature: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Independently recompute full-vector powers and reciprocal numerator in W."""
    for label, contract in (("signal", signal_contract), ("mode", mode_contract), ("incident", incident_contract)):
        if contract.get("role") != {"signal": "signal", "mode": "reference_mode", "incident": "incident_reference"}[label]:
            _fail(f"{label} raw-field contract has the wrong source role")
    validations = [
        validate_native_field_readback(signal_contract, signal_raw, expected_quadrature=signal_quadrature),
        validate_native_field_readback(mode_contract, mode_raw, expected_quadrature=mode_quadrature),
        validate_native_field_readback(incident_contract, incident_raw, expected_quadrature=incident_quadrature),
    ]
    capture_args = (capture_raw, capture_contract, capture_quadrature)
    if any(item is not None for item in capture_args):
        if not all(item is not None for item in capture_args):
            _fail("capture raw field, contract, and quadrature must be supplied together")
        assert capture_raw is not None and capture_contract is not None and capture_quadrature is not None
        if capture_contract.get("role") != "capture_signal":
            _fail("capture aperture raw fields must use the capture_signal role")
        if capture_contract.get("source") != signal_contract.get("source"):
            _fail("capture aperture must bind the same native frequency-signal solution as the output plane")
        validations.append(validate_native_field_readback(
            capture_contract, capture_raw, expected_quadrature=capture_quadrature))
    if (signal_contract["plane"] != mode_contract["plane"]
            or signal_contract["quadrature_sha256"] != mode_contract["quadrature_sha256"]):
        _fail("signal and output mode must be sampled on the identical local port plane and quadrature grid")
    if (incident_contract["plane"]["plane_id"] != "input_port"
            or signal_contract["plane"]["plane_id"] != "receiver_port"):
        _fail("incident and receiver sources are bound to the wrong native port planes")
    signal_fields = _expression_map(signal_raw, signal_contract)
    mode_fields = _expression_map(mode_raw, mode_contract)
    incident_fields = _expression_map(incident_raw, incident_contract)
    signal_weights = signal_quadrature["weights_m2"]
    mode_weights = mode_quadrature["weights_m2"]
    incident_weights = incident_quadrature["weights_m2"]
    if not (len(signal_weights) == len(mode_weights) == signal_contract["quadrature"]["sample_count"]):
        _fail("output field and mode quadrature weights do not match")
    output_normal = signal_contract["plane"]["physical_forward_normal_xyz"]
    input_normal = incident_contract["plane"]["physical_forward_normal_xyz"]
    signal_flux, mode_flux, cross_terms = [], [], []
    for index in range(len(signal_weights)):
        signal_e = _field_vector(signal_fields, "signal", "E", index)
        signal_h = _field_vector(signal_fields, "signal", "H", index)
        mode_e = _field_vector(mode_fields, "reference_mode", "E", index)
        mode_h = _field_vector(mode_fields, "reference_mode", "H", index)
        signal_flux.append(0.5 * _cross_dot(signal_e, signal_h, output_normal, conjugate_h=True).real)
        mode_flux.append(0.5 * _cross_dot(mode_e, mode_h, output_normal, conjugate_h=True).real)
        first = _cross_dot(signal_e, mode_h, output_normal, conjugate_h=True)
        second = _cross_dot(mode_e, signal_h, output_normal, conjugate_e=True)
        cross_terms.append(first + second)
    input_flux = []
    for index in range(len(incident_weights)):
        incident_e = _field_vector(incident_fields, "incident_reference", "E", index)
        incident_h = _field_vector(incident_fields, "incident_reference", "H", index)
        input_flux.append(0.5 * _cross_dot(incident_e, incident_h, input_normal, conjugate_h=True).real)
    signal_power = _integrate(signal_flux, signal_weights).real
    mode_power = _integrate(mode_flux, mode_weights).real
    incident_power = _integrate(input_flux, incident_weights).real
    cross = _integrate(cross_terms, signal_weights)
    floor = _finite_number(power_floor_w, "power floor")
    if floor <= 0.0 or min(signal_power, mode_power, incident_power) <= floor:
        _fail("native port power must be positive and exceed the frozen W normalization floor")
    amplitude_denominator = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    normalized_overlap = (abs(cross) / amplitude_denominator) ** 2
    eta_mode = normalized_overlap * signal_power / incident_power
    result = {"status": "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS",
            "evidence_scope": "independent full-vector quadrature of fresh native COMSOL Interp data",
            "integration": signal_contract["quadrature"]["profile"], "unit": _POWER_UNIT,
            "geometry_dimension": 3,
            "signal_power": signal_power, "reference_mode_power": mode_power,
            "incident_reference_power": incident_power,
            "reciprocal_overlap_numerator": {"real": cross.real, "imag": cross.imag},
            "normalized_overlap": normalized_overlap, "eta_mode": eta_mode,
            "power_floor_w": floor,
            "sample_counts": {"signal": signal_contract["quadrature"]["sample_count"],
                              "reference_mode": mode_contract["quadrature"]["sample_count"],
                              "incident_reference": incident_contract["quadrature"]["sample_count"]},
            "native_raw_validations": validations, "caller_field_arrays_accepted": False,
            "native_result": "NOT_RUN"}
    if capture_raw is not None:
        assert capture_contract is not None and capture_quadrature is not None
        capture_fields = _expression_map(capture_raw, capture_contract)
        capture_weights = capture_quadrature["weights_m2"]
        capture_normal = capture_contract["plane"]["physical_forward_normal_xyz"]
        capture_flux = []
        for index in range(len(capture_weights)):
            electric = _field_vector(capture_fields, "capture_signal", "E", index)
            magnetic = _field_vector(capture_fields, "capture_signal", "H", index)
            capture_flux.append(0.5 * _cross_dot(
                electric, magnetic, capture_normal, conjugate_h=True).real)
        capture_power = _integrate(capture_flux, capture_weights).real
        if not math.isfinite(capture_power):
            _fail("native aperture flux is not finite")
        result.update({"capture_aperture_signal_flux": capture_power,
                       "eta_capture": capture_power / incident_power,
                       "capture_aperture_id": capture_contract["plane"]["selection_tag"],
                       "capture_sample_count": capture_contract["quadrature"]["sample_count"]})
    return result


def compare_native_mode_overlap(
    native: Mapping[str, Any], independent: Mapping[str, Any],
) -> dict[str, Any]:
    if native.get("status") != "SUCCEEDED" or native.get("result_status") != "COMPUTED_NATIVE_INTEGRALS":
        _fail("managed result.mode_overlap did not return native COMSOL numerical integrals")
    if independent.get("status") != "SOFTWARE_RECOMPUTED_FROM_NATIVE_RAW_FIELDS":
        _fail("independent vector-field quadrature provenance is missing")
    surface = native.get("surface")
    if (not isinstance(surface, Mapping) or surface.get("geometry_dimension") != 3
            or surface.get("power_unit") != "W"):
        _fail("3-D native overlap surface must report full vector geometry and W integrals")
    tolerance = FULL3D_COMPARISON_POLICY["relative_tolerance"]
    integrals = native.get("integrals")
    if not isinstance(integrals, Mapping):
        _fail("native overlap result omitted integral records")
    relative_errors: dict[str, float] = {}
    absolute_normalized_errors: dict[str, float] = {}
    absolute_efficiency_errors: dict[str, float] = {}
    failures: list[str] = []
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        row = integrals.get(key)
        expected = _finite_number(independent.get(key), f"independent {key}")
        if not isinstance(row, Mapping) or row.get("unit") != _POWER_UNIT:
            _fail(f"native {key} unit is not W")
        observed = complex(_finite_number(row.get("real"), f"native {key}.real"),
                           _finite_number(row.get("imag", 0), f"native {key}.imag"))
        if abs(observed.imag) > 1e-10 * max(abs(observed.real), 1e-30):
            _fail(f"native {key} should be a real time-averaged power")
        relative_errors[key] = abs(observed.real - expected) / max(abs(expected), 1e-30)
        if relative_errors[key] > tolerance:
            failures.append(f"{key} relative error exceeds {tolerance:g}")
    row = integrals.get("reciprocal_overlap_numerator")
    expected_row = independent.get("reciprocal_overlap_numerator")
    if not isinstance(row, Mapping) or not isinstance(expected_row, Mapping) or row.get("unit") != _POWER_UNIT:
        _fail("native and independent complex reciprocal numerators are required in W")
    observed_cross = complex(_finite_number(row.get("real"), "native cross real"),
                             _finite_number(row.get("imag"), "native cross imaginary"))
    expected_cross = complex(_finite_number(expected_row.get("real"), "independent cross real"),
                             _finite_number(expected_row.get("imag"), "independent cross imaginary"))
    signal_power = _finite_number(independent.get("signal_power"), "independent signal power")
    mode_power = _finite_number(independent.get("reference_mode_power"), "independent mode power")
    if signal_power <= 0.0 or mode_power <= 0.0:
        _fail("independent cross normalization requires positive signal and mode powers")
    cross_power_scale = 4.0 * math.sqrt(signal_power) * math.sqrt(mode_power)
    cross_absolute_error = abs(observed_cross - expected_cross) / cross_power_scale
    expected_cross_normalized = abs(expected_cross) / cross_power_scale
    cross_atol = FULL3D_COMPARISON_POLICY["cross_absolute_normalized_tolerance"]
    if expected_cross_normalized <= cross_atol:
        absolute_normalized_errors["reciprocal_overlap_numerator"] = cross_absolute_error
        if cross_absolute_error > cross_atol:
            failures.append("reciprocal overlap normalized absolute error exceeds the frozen zero-channel limit")
    else:
        relative_errors["reciprocal_overlap_numerator"] = (
            abs(observed_cross - expected_cross) / abs(expected_cross))
        if relative_errors["reciprocal_overlap_numerator"] > tolerance:
            failures.append(f"reciprocal overlap relative error exceeds {tolerance:g}")
    native_eta = _finite_number(native.get("eta_mode"), "native eta_mode")
    expected_eta = _finite_number(independent.get("eta_mode"), "independent eta_mode")
    eta_absolute_error = abs(native_eta - expected_eta)
    eta_atol = FULL3D_COMPARISON_POLICY["eta_absolute_tolerance"]
    if abs(expected_eta) <= eta_atol:
        absolute_efficiency_errors["eta_mode"] = eta_absolute_error
        if eta_absolute_error > eta_atol:
            failures.append("eta_mode absolute error exceeds the frozen zero-channel limit")
    else:
        relative_errors["eta_mode"] = eta_absolute_error / abs(expected_eta)
        if relative_errors["eta_mode"] > tolerance:
            failures.append(f"eta_mode relative error exceeds {tolerance:g}")
    independent_capture = "eta_capture" in independent
    capture = native.get("eta_capture")
    if independent_capture:
        if not isinstance(capture, Mapping) or capture.get("status") != "COMPUTED_NATIVE_APERTURE_FLUX":
            _fail("independent core-aperture flux requires an actual native capture integral")
        denominator = capture.get("denominator")
        numerator = integrals.get("capture_aperture_signal_flux")
        if (not isinstance(denominator, Mapping) or denominator.get("unit") != _POWER_UNIT
                or not isinstance(numerator, Mapping) or numerator.get("unit") != _POWER_UNIT):
            _fail("native capture numerator and input-power denominator must both be reported in W")
        denominator_value = _finite_number(denominator.get("value"), "native capture denominator")
        native_incident_row = integrals.get("incident_reference_power")
        if not isinstance(native_incident_row, Mapping):
            _fail("capture normalization must bind the native incident-power integral")
        incident_power = _finite_number(native_incident_row.get("real"), "native incident power")
        if abs(denominator_value - incident_power) > 1e-12 * max(abs(incident_power), 1e-30):
            _fail("native capture denominator value differs from the native incident integral")
        numerator_value = _finite_number(numerator.get("real"), "native capture flux")
        if abs(_finite_number(numerator.get("imag", 0), "native capture flux imaginary")) > 1e-10 * max(abs(numerator_value), 1e-30):
            _fail("native signed time-average capture flux has a non-real residual")
        eta_from_integrals = numerator_value / denominator_value
        native_capture_eta = _finite_number(capture.get("value"), "native eta_capture")
        internal_eta_error = abs(native_capture_eta - eta_from_integrals)
        if abs(eta_from_integrals) <= eta_atol:
            internal_eta_consistent = internal_eta_error <= eta_atol
        else:
            internal_eta_consistent = internal_eta_error / abs(eta_from_integrals) <= 1e-12
        if not internal_eta_consistent:
            _fail("native eta_capture does not equal its reported signed numerator divided by denominator")
        expected_capture_flux = _finite_number(independent.get("capture_aperture_signal_flux"),
                                                "independent capture flux")
        expected_capture_eta = _finite_number(independent.get("eta_capture"), "independent eta_capture")
        incident_reference_power = _finite_number(
            independent.get("incident_reference_power"), "independent incident-reference power")
        if incident_reference_power <= 0.0:
            _fail("capture-flux normalization requires positive independent incident power")
        capture_flux_error = abs(numerator_value - expected_capture_flux) / incident_reference_power
        expected_capture_flux_normalized = abs(expected_capture_flux) / incident_reference_power
        capture_flux_atol = FULL3D_COMPARISON_POLICY["capture_flux_absolute_normalized_tolerance"]
        if expected_capture_flux_normalized <= capture_flux_atol:
            absolute_normalized_errors["capture_aperture_signal_flux"] = capture_flux_error
            if capture_flux_error > capture_flux_atol:
                failures.append("capture flux normalized absolute error exceeds the frozen zero-channel limit")
        else:
            relative_errors["capture_aperture_signal_flux"] = (
                abs(numerator_value - expected_capture_flux) / abs(expected_capture_flux))
            if relative_errors["capture_aperture_signal_flux"] > tolerance:
                failures.append(f"capture flux relative error exceeds {tolerance:g}")
        capture_eta_error = abs(native_capture_eta - expected_capture_eta)
        if abs(expected_capture_eta) <= eta_atol:
            absolute_efficiency_errors["eta_capture"] = capture_eta_error
            if capture_eta_error > eta_atol:
                failures.append("eta_capture absolute error exceeds the frozen zero-channel limit")
        else:
            relative_errors["eta_capture"] = capture_eta_error / abs(expected_capture_eta)
            if relative_errors["eta_capture"] > tolerance:
                failures.append(f"eta_capture relative error exceeds {tolerance:g}")
    elif isinstance(capture, Mapping) and capture.get("status") == "COMPUTED_NATIVE_APERTURE_FLUX":
        _fail("native result reports capture efficiency without an independent core-aperture recomputation")
    if (any(not math.isfinite(value) for value in
            (*relative_errors.values(), *absolute_normalized_errors.values(),
             *absolute_efficiency_errors.values())) or failures):
        _fail("native COMSOL integrals differ from independently sampled vector quadrature: "
              f"failures={failures}; relative={relative_errors}; "
              f"normalized_absolute={absolute_normalized_errors}; "
              f"efficiency_absolute={absolute_efficiency_errors}")
    return {"status": "SOFTWARE_NATIVE_VS_INDEPENDENT_FULL3D_COMPARISON_VALID",
            "native_result": "NOT_RUN", "comparison_policy": dict(FULL3D_COMPARISON_POLICY),
            "relative_errors": relative_errors,
            "normalized_absolute_errors": absolute_normalized_errors,
            "efficiency_absolute_errors": absolute_efficiency_errors,
            "unit": _POWER_UNIT,
            "native_result_status": native.get("result_status"),
            "independent_evidence_scope": independent.get("evidence_scope")}


__all__ = [
    "Full3DScienceError", "ManagedRouteOutcomeError", "FULL3D_COMPARISON_POLICY",
    "build_raw_field_contract", "build_raw_field_dispatch",
    "circular_port_quadrature", "rectangular_port_quadrature", "compare_native_mode_overlap",
    "independent_mode_overlap_integrals", "validate_native_field_readback",
    "build_full3d_study_run_dispatch", "build_full3d_solution_inventory_dispatch",
    "build_full3d_dataset_list_dispatch", "build_full3d_dataset_indices_dispatch",
    "build_full3d_save_dispatch", "build_full3d_model_load_request",
    "build_full3d_mode_overlap_definition", "build_full3d_mode_overlap_dispatch",
    "resolve_full3d_native_sources", "stage_full3d_fixture_source",
    "dispatch_public_managed_route", "execute_full3d_managed_configuration_chain",
]
