"""Source- and OperationStore-bound W24 cure-law v2 capture plumbing.

This module only validates or dispatches read-only capture actions against an
already connected public ControlDaemon. It never starts a server, creates a
Worker, or calls Study.run. Its successful software receipts still require
later native review; Maxwell branch reference-state observability remains
explicitly UNVERIFIED until COMSOL exposes the actual state through a public
capture.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.w24_science_acceptance import AcceptanceError


SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAXWELL_TIMES_S = tuple(float(i) for i in range(902))
GEL_TIMES_S = tuple(i * 0.5 for i in range(7))
CONTROL_COORDINATE_M = ((50e-6, 50e-6, 50e-6),)  # V1 compatibility only.
CONTROL_COORDINATES_V2_M = tuple(
    (100e-6 * x, 100e-6 * y, 100e-6 * z)
    for x in (0.25, 0.5, 0.75)
    for y in (0.25, 0.5, 0.75)
    for z in (0.25, 0.5, 0.75)
)
CONTROL_CAPTURE_SCHEMA_V1 = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1"
CONTROL_CAPTURE_SCHEMA_V2 = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V2"
MAXWELL_EXPRESSIONS = ("solid.sx", "solid.sy")
MAXWELL_UNITS = ("Pa", "Pa")
GEL_EXPRESSIONS = (
    "solid.sx", "solid.sy", "solid.sz", "solid.sxy", "solid.sxz", "solid.syz",
    "solid.isactive", "solid.wasactive",
)
GEL_UNITS = ("Pa", "Pa", "Pa", "Pa", "Pa", "Pa", "1", "1")
HISTORY_EXPRESSIONS = (
    "T", "alpha", "Duv_rel", "qpost", "u", "w", "solid.isactive", "solid.wasactive",
)
HISTORY_UNITS = ("K", "1", "s", "1", "m", "m", "1", "1")
ALLOWED_CAPTURE_ACTIONS = {
    "capture_maxwell_control": ("W24CureLawV2ControlFixture#run", "maxwell_ramp_hold_control"),
    "capture_gel_control": ("W24CureLawV2ControlFixture#run", "gel_stress_free_control"),
    "solution_snapshot_v2": ("W24CureLawV2ControlFixture#run", None),
    "solution_snapshot_v3": ("W24CureLawV2ControlFixture#run", None),
    "history_capture_v2": ("W24CureScienceFixture#run", None),
}
ALLOWED_PUBLIC_ACTIONS = {
    **ALLOWED_CAPTURE_ACTIONS,
    "build_maxwell_ramp_hold": ("W24CureLawV2ControlFixture#run", None),
    "readback_maxwell_ramp_hold": ("W24CureLawV2ControlFixture#run", None),
    "build_gel_stress_free": ("W24CureLawV2ControlFixture#run", None),
    "readback_gel_stress_free": ("W24CureLawV2ControlFixture#run", None),
    "study_run_control": ("W24CureLawV2ControlFixture#run", None),
    "study_run": ("W24CureScienceFixture#run", None),
    "readback": ("W24CureScienceFixture#run", None),
    "solution_snapshot": ("W24CureScienceFixture#run", None),
    "cure_metrics_capture": ("W24CureScienceFixture#run", None),
    "cure_v2_contract_readback": ("W24CureCouponFixture#run", None),
    "equation_view_readback_v1": ("W24CureCouponFixture#run", None),
}


class CaptureError(AcceptanceError):
    """A public capture response or artifact failed its frozen identity contract."""


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaptureError(f"{label} must be a mapping")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resolve_project_file(root: Path, raw: Any, label: str, *, must_exist: bool) -> Path:
    if not isinstance(raw, str) or not raw:
        raise CaptureError(f"{label} path is required")
    root = root.resolve(strict=True)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as exc:
        raise CaptureError(f"{label} parent is unavailable") from exc
    if root != resolved_parent and root not in resolved_parent.parents:
        raise CaptureError(f"{label} path escapes the registered project")
    if candidate.is_symlink():
        raise CaptureError(f"{label} must not be a symlink")
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise CaptureError(f"{label} is unavailable") from exc
    if root != resolved and root not in resolved.parents:
        raise CaptureError(f"{label} resolves outside the registered project")
    if must_exist:
        try:
            metadata = resolved.lstat()
        except OSError as exc:
            raise CaptureError(f"{label} is unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise CaptureError(f"{label} must be a regular nonsymlink file")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_artifact(receipt: Mapping[str, Any], root: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve_project_file(root, receipt.get("path"), f"{label}.path", must_exist=True)
    size = receipt.get("size_bytes")
    expected_hash = receipt.get("sha256")
    if not _is_int(size) or size <= 0 or not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
        raise CaptureError(f"{label} lacks an exact size and lowercase SHA-256 receipt")
    observed_size = path.stat().st_size
    observed_hash = _sha256(path)
    if observed_size != size or observed_hash != expected_hash:
        raise CaptureError(f"{label} path, size, or SHA-256 does not match the public Java receipt")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"{label} is not a complete UTF-8 JSON artifact") from exc
    if not isinstance(artifact, dict):
        raise CaptureError(f"{label} JSON root must be an object")
    return artifact, {"path": str(path), "size_bytes": observed_size, "sha256": observed_hash}


def _exact_float_vector(raw: Any, expected: Sequence[float], label: str) -> list[float]:
    if not isinstance(raw, list) or len(raw) != len(expected):
        raise CaptureError(f"{label} must contain the full frozen stored-time vector")
    values: list[float] = []
    for index, item in enumerate(raw):
        if isinstance(item, bool):
            raise CaptureError(f"{label}[{index}] must be numeric")
        try:
            value = float(item)
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"{label}[{index}] must be numeric") from exc
        if not math.isfinite(value) or value != expected[index]:
            raise CaptureError(f"{label}[{index}] differs from the frozen native stored-time contract")
        values.append(value)
    return values


def validate_capture_artifact(artifact: Mapping[str, Any], *, expected_action: str) -> dict[str, Any]:
    """Validate complete source-produced result shape without claiming it is native."""
    row = _require_mapping(artifact, "capture artifact")
    if row.get("native_study_run_calls") != 0:
        raise CaptureError("capture action must not submit an additional Study.run")
    feature = _require_mapping(row.get("feature_readback"), "feature_readback")
    if row.get("quasistatic_readback") != "Quasistatic":
        raise CaptureError("capture omitted the actual Solid Mechanics Quasistatic property readback")
    if row.get("time_source") != "SolverSequence.getPVals":
        raise CaptureError("capture must identify the native SolverSequence.getPVals time source")
    if row.get("dataset_type_requested") != "Solution" or row.get("dataset_solution_readback") != row.get("solver_tag"):
        raise CaptureError("capture dataset does not bind to the exact solution SolverSequence")
    if not all(isinstance(row.get(name), str) and row.get(name) for name in ("study_tag", "solver_tag", "dataset_tag")):
        raise CaptureError("capture omitted its exact study, solver, or dataset tag")
    if feature.get("type") != "Interp" or feature.get("dataset") != row.get("dataset_tag"):
        raise CaptureError("native Interp feature is not bound to the recorded Solution dataset")
    if feature.get("solnum") != "all" or feature.get("coorderr") != "on" or feature.get("matherr") != "on":
        raise CaptureError("native Interp feature omitted all-stored-solution or fail-on-error settings")
    if feature.get("expressions") != row.get("expressions") or feature.get("units") != row.get("units"):
        raise CaptureError("native Interp expression or unit readback differs from the capture")
    if feature.get("shape") != row.get("shape"):
        raise CaptureError("native Interp shape readback differs from the complete capture shape")
    if feature.get("coordinates_m") != row.get("coordinates_m"):
        raise CaptureError("native Interp coordinate receipt differs from the capture coordinates")

    if expected_action == "capture_maxwell_control":
        schema, case_id = {CONTROL_CAPTURE_SCHEMA_V1, CONTROL_CAPTURE_SCHEMA_V2}, "maxwell_ramp_hold_control"
        expressions, units, expected_times = list(MAXWELL_EXPRESSIONS), list(MAXWELL_UNITS), MAXWELL_TIMES_S
        expected_tlist = "range(0[s],1[s],901[s])"
    elif expected_action == "capture_gel_control":
        schema, case_id = {CONTROL_CAPTURE_SCHEMA_V1, CONTROL_CAPTURE_SCHEMA_V2}, "gel_stress_free_control"
        expressions, units, expected_times = list(GEL_EXPRESSIONS), list(GEL_UNITS), GEL_TIMES_S
        expected_tlist = "range(0[s],0.5[s],3[s])"
    elif expected_action == "history_capture_v2":
        schema, case_id = "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1", None
        expressions, units, expected_times = list(HISTORY_EXPRESSIONS), list(HISTORY_UNITS), None
        expected_tlist = None
    else:
        raise CaptureError("unsupported capture action")
    if expected_action != "history_capture_v2" and row.get("case_id") != case_id:
        raise CaptureError("capture case identity differs from the requested control action")
    observed_schema = row.get("schema")
    schema_matches = observed_schema in schema if isinstance(schema, set) else observed_schema == schema
    if not schema_matches:
        raise CaptureError("capture schema differs from the action's frozen result contract")
    if expected_tlist is not None and row.get("study_tlist_readback") != expected_tlist:
        raise CaptureError("capture study time-list readback differs from the frozen control schedule")
    if expected_tlist is None and (not isinstance(row.get("study_tlist_readback"), str) or
                                   not row.get("study_tlist_readback")):
        raise CaptureError("history capture omitted the native Study time-list readback")
    if row.get("status") not in {"NATIVE_CONTROL_CAPTURED_NO_SOLVE_SUBMITTED", "NATIVE_HISTORY_CAPTURED_NO_SOLVE_SUBMITTED"}:
        raise CaptureError("capture artifact lacks its exact read-only capture status")
    observed_expressions = row.get("expressions")
    observed_units = row.get("units")
    if observed_expressions != expressions or observed_units != units:
        raise CaptureError("capture omitted, reordered, or changed an exact expression/unit")
    if expected_action == "history_capture_v2":
        expected_coords = [[25e-6, 520e-6], [50e-6, 530e-6], [75e-6, 540e-6]]
    elif observed_schema == CONTROL_CAPTURE_SCHEMA_V2:
        expected_coords = [list(point) for point in CONTROL_COORDINATES_V2_M]
        plan_sha = row.get("control_plan_sha256")
        if (not isinstance(plan_sha, str) or not SHA256_RE.fullmatch(plan_sha) or
                not isinstance(row.get("campaign_id"), str) or not row.get("campaign_id") or
                not isinstance(row.get("approval_sha256"), str) or
                not SHA256_RE.fullmatch(row["approval_sha256"]) or
                not isinstance(row.get("slot_idempotency_key"), str) or
                not SHA256_RE.fullmatch(row["slot_idempotency_key"]) or
                row.get("coordinate_grid") !=
                "uniform 3x3x3 control cube at 25/50/75 percent fractions on every axis"):
            raise CaptureError("V2 control capture lacks the exact approved plan/slot and spatial-grid identity")
    else:
        expected_coords = [list(point) for point in CONTROL_COORDINATE_M]
    if row.get("coordinates_m") != expected_coords or feature.get("coordinate_source", "").find("setInterpolationCoordinates") < 0:
        raise CaptureError("capture coordinates differ from the exact Java interpolation points")
    times_raw = row.get("stored_times_s")
    if expected_times is None:
        if not isinstance(times_raw, list) or not times_raw:
            raise CaptureError("history capture omitted the complete stored-time vector")
        times: list[float] = []
        for i, value in enumerate(times_raw):
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise CaptureError(f"stored_times_s[{i}] must be numeric") from exc
            if not math.isfinite(number) or (times and number <= times[-1]):
                raise CaptureError("history capture stored times must be finite and strictly increasing")
            times.append(number)
    else:
        times = _exact_float_vector(times_raw, expected_times, "stored_times_s")
    point_count = len(expected_coords)
    expected_shape = [len(expressions), len(times), point_count]
    if row.get("shape") != expected_shape or feature.get("shape") != expected_shape:
        raise CaptureError("capture is missing an expression, stored-time, or coordinate axis")
    data = row.get("data")
    if not isinstance(data, list) or len(data) != len(expressions):
        raise CaptureError("capture does not contain every native expression series")
    for exp_index, expression_rows in enumerate(data):
        if not isinstance(expression_rows, list) or len(expression_rows) != len(times):
            raise CaptureError(f"capture time axis for {expressions[exp_index]} is incomplete")
        for time_index, point_rows in enumerate(expression_rows):
            if not isinstance(point_rows, list) or len(point_rows) != point_count:
                raise CaptureError(f"capture coordinate axis for {expressions[exp_index]} is incomplete")
            for point_index, raw_value in enumerate(point_rows):
                if isinstance(raw_value, bool):
                    raise CaptureError("capture values must be numeric, not boolean")
                try:
                    value = float(raw_value)
                except (TypeError, ValueError) as exc:
                    raise CaptureError("capture contains a nonnumeric native value") from exc
                if not math.isfinite(value):
                    raise CaptureError("capture contains a nonfinite native value")
                if expressions[exp_index] in {"solid.isactive", "solid.wasactive"} and value not in (0.0, 1.0):
                    raise CaptureError("native Activation expressions must be exactly binary")
    if expected_action == "capture_gel_control":
        for expression, checkpoints in (("solid.isactive", (2.0, 2.5, 3.0)),
                                        ("solid.wasactive", (2.5, 3.0))):
            index = expressions.index(expression)
            for target_time in checkpoints:
                time_index = times.index(target_time)
                if any(float(value) != 1.0 for value in data[index][time_index]):
                    raise CaptureError(f"{expression} is not active throughout the exact post-gel checkpoint {target_time:g} s")
    branch = row.get("maxwell_branch_reference_state", row.get("maxwell_branch_state"))
    if branch != "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE":
        raise CaptureError("Maxwell branch reference-state semantics must remain explicitly UNVERIFIED")
    return {
        "status": "CAPTURE_SCHEMA_VALIDATED_NATIVE_NOT_RUN",
        "action": expected_action,
        "case_id": row.get("case_id"),
        "stored_time_count": len(times),
        "expression_count": len(expressions),
        "coordinate_count": point_count,
        "control_capture_schema": observed_schema,
        "control_plan_sha256": row.get("control_plan_sha256"),
        "shape": expected_shape,
        "quasistatic_readback": "Quasistatic",
        "maxwell_branch_reference_state": "UNVERIFIED",
        "native_execution_performed_by_validator": False,
    }


def validate_control_configuration_readback(readback: Mapping[str, Any], *,
                                            case_id: str) -> dict[str, Any]:
    """Validate the exact unsolved 3D control-model configuration readback."""
    row = _require_mapping(readback, "control configuration readback")
    if (case_id not in {"maxwell_ramp_hold_control", "gel_stress_free_control"} or
            row.get("schema") != "W24_CURE_LAW_V2_CONTROL_READBACK_V1" or
            row.get("case_id") != case_id or
            row.get("geometry_dimension") != 3 or row.get("geometry_domain_count") != 1 or
            row.get("all_six_exterior_faces_selected") is not True or
            row.get("quasistatic_readback") != "Quasistatic" or
            row.get("solver_submissions") != 0 or
            row.get("native_study_run_calls") != 0):
        raise CaptureError("control configuration readback is not the exact unsolved 3D quasistatic fixture")
    bounds = row.get("bounding_box_m")
    if (not isinstance(bounds, list) or len(bounds) != 6 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v))
                for v in bounds) or
            any(abs(float(actual) - expected) > 1e-12
                for actual, expected in zip(bounds, (0.0, 100e-6, 0.0, 100e-6, 0.0, 100e-6)))):
        raise CaptureError("control geometry bounds differ from the frozen 100 um cube")
    if (row.get("thermal_physics") != "absent" or
            row.get("chemical_physics") != "absent" or
            row.get("absolute_irradiance_or_thermal_load") != "absent"):
        raise CaptureError("uniform mechanics control contains a forbidden thermal/chemical/load term")
    if case_id == "maxwell_ramp_hold_control":
        if (row.get("study_tag") != "stdMaxwell" or
                not isinstance(row.get("solver_sequence"), str) or not row["solver_sequence"] or
                row.get("study_output_times") != "range(0[s],1[s],901[s])" or
                row.get("material_model") != "GeneralizedMaxwell" or
                row.get("deformation_model") != "full" or
                row.get("E_long_term") != "Einf=0.5[GPa]" or
                row.get("E_branch_0") != "Ebranch=1.5[GPa]" or
                row.get("nu") != "nuVisco=0.35" or
                row.get("Kvm_v") != ["Kbranch"] or row.get("Gvm") != ["Gbranch"] or
                row.get("tauvm") != ["tauMaxwell"] or
                row.get("thermal_or_chemical_strain_features") != 0 or
                row.get("activation_feature") != "none; solid material is active from initial time" or
                row.get("native_branch_initial_reference_state") != "UNVERIFIED_FAIL_CLOSED"):
            raise CaptureError("Maxwell control configuration differs from its complete ordered branch/readback contract")
        return {"status": "MAXWELL_CONTROL_CONFIGURATION_MATCHED_NOT_SOLVED",
                "branch_reference_state": "UNVERIFIED", "native_acceptance": "NOT_RUN"}
    raw_actfac = row.get("actfac")
    if isinstance(raw_actfac, bool) or not isinstance(raw_actfac, (int, float, str)):
        raise CaptureError("gel control actfac readback must be a finite numeric value")
    try:
        actfac = float(raw_actfac)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CaptureError("gel control actfac readback must be a finite numeric value") from exc
    if not math.isfinite(actfac):
        raise CaptureError("gel control actfac readback must be a finite numeric value")
    if (row.get("study_tag") != "stdGel" or
            not isinstance(row.get("solver_sequence"), str) or not row["solver_sequence"] or
            row.get("study_output_times") != "range(0[s],0.5[s],3[s])" or
            row.get("activation_expression") != "t>=tGel || solid.wasactive" or
            row.get("actfac_was_set") is not False or
            abs(actfac - 1e-5) > 1e-15 or
            row.get("native_activation_reference_state") != "UNVERIFIED_FAIL_CLOSED"):
        raise CaptureError("gel control configuration differs from the exact activation/reference readback")
    return {"status": "GEL_CONTROL_CONFIGURATION_MATCHED_NOT_SOLVED",
            "activation_reference_state": "UNVERIFIED", "native_acceptance": "NOT_RUN"}


def validate_cure_law_v2_contract_readback(readback: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on an actual loaded-model W24 cure-law v2 readback."""
    row = _require_mapping(readback, "cure-law v2 readback")
    dose = _require_mapping(row.get("relative_exposure_dose"), "relative_exposure_dose")
    spatial = _require_mapping(row.get("spatial_uv_readback"), "spatial_uv_readback")
    activation = _require_mapping(row.get("activation_readback"), "activation_readback")
    visco = _require_mapping(row.get("viscoelastic_readback"), "viscoelastic_readback")
    if row.get("cure_law_version") != "W24_CURE_LAW_V2":
        raise CaptureError("loaded model did not publicly read back the explicit cure-law v2 version")
    if (dose.get("field") != "Duv_rel" or dose.get("unit") != "s" or
            dose.get("dependent_variable_quantity") != "time" or
            dose.get("source") != "Irel" or dose.get("source_term_quantity") != "dimensionless" or
            dose.get("initial") != "0[s]" or dose.get("source_scope") != "adhesive_only"):
        raise CaptureError("loaded model relative-dose field/units/initial/selection readback is incomplete")
    if (spatial.get("synthetic_estimated") is not True or
            spatial.get("absolute_irradiance") is not False or
            isinstance(spatial.get("z_surface_m"), bool) or
            not isinstance(spatial.get("z_surface_m"), (int, float)) or
            abs(float(spatial["z_surface_m"]) - 550e-6) > 1e-12 or
            spatial.get("intensity_expression") != "S_uv*exp(-muUV*(zUVSurface-z))"):
        raise CaptureError("loaded model spatial UV readback is not the frozen relative Beer-Lambert contract")
    try:
        alpha_gel = float(activation.get("alpha_gel"))
        actfac = float(activation.get("actfac"))
    except (TypeError, ValueError) as exc:
        raise CaptureError("loaded model Activation readback omitted numeric alpha_gel/actfac") from exc
    selection = activation.get("selection")
    if (activation.get("feature_type") != "Activation" or abs(alpha_gel - 0.5) > 1e-12 or
            abs(actfac - 1e-5) > 1e-15 or activation.get("actfac_was_set") is not False or
            not isinstance(selection, list) or not selection):
        raise CaptureError("loaded model Activation type/selection/gel/default-factor readback differs")
    branch_k = visco.get("Kvm_v")
    branch_g = visco.get("Gvm")
    branch_tau = visco.get("tauvm")
    if (visco.get("feature_type") != "Viscoelasticity" or
            visco.get("material_model") != "GeneralizedMaxwell" or
            visco.get("deformation_model") != "full" or
            not all(isinstance(values, list) and len(values) == 1 and
                    isinstance(values[0], str) and values[0]
                    for values in (branch_k, branch_g, branch_tau)) or
            visco.get("branch_order_binding") != "same ordered index across Kvm_v/Gvm/tauvm"):
        raise CaptureError("loaded model full Generalized Maxwell ordered branch readback is incomplete")
    tolerance_rows = row.get("dose_solver_tolerance_readbacks")
    if not isinstance(tolerance_rows, Mapping) or set(tolerance_rows) != {"stdUV", "stdBake", "stdCool"}:
        raise CaptureError("loaded model v2 dose solver tolerances are missing from one or more stages")
    for tag, raw in tolerance_rows.items():
        tolerance = _require_mapping(raw, f"dose_solver_tolerance_readbacks.{tag}")
        if (tolerance.get("field") != "comp1_Duv_rel" or
                tolerance.get("scale") != "unscaled" or tolerance.get("method") != "manual"):
            raise CaptureError(f"loaded model Duv_rel solver tolerance readback differs for {tag}")
        try:
            atol = float(tolerance.get("absolute_tolerance"))
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"loaded model Duv_rel solver atol is missing for {tag}") from exc
        if not math.isfinite(atol) or abs(atol - 1e-8) > 1e-20:
            raise CaptureError(f"loaded model Duv_rel solver atol differs for {tag}")
    solver_rows = row.get("solver_readbacks")
    expected_stages = {"stdUV", "stdBake", "stdCool"}
    expected_component_atols = {
        "comp1_T": 1e-4,
        "comp1_alpha": 1e-8,
        "comp1_alpha_iso": 1e-8,
        "comp1_qpost": 1e-8,
        "comp1_u": 1e-12,
        "comp1_w": 1e-12,
    }
    expected_native_atols = {
        "comp1_T": 1e-4,
        "comp1_alpha": 1e-8,
        "comp1_alpha_iso": 1e-8,
        "comp1_qpost": 1e-8,
        "comp1_u": 1e-12,
        "comp1_Duv_rel": 1e-8,
    }
    if not isinstance(solver_rows, Mapping) or set(solver_rows) != expected_stages:
        raise CaptureError("loaded model v2 solver readback omitted a staged SolverSequence")
    field_contract_signature: tuple[Any, ...] | None = None
    for stage, raw_solver in solver_rows.items():
        solver = _require_mapping(raw_solver, f"solver_readbacks.{stage}")
        field_contract = _require_mapping(solver.get("solver_field_contract"),
                                          f"solver_readbacks.{stage}.solver_field_contract")
        descriptor = _require_mapping(field_contract.get("physics_field_descriptor"),
                                      f"solver_readbacks.{stage}.solver_field_contract.physics_field_descriptor")
        components = descriptor.get("components")
        component_bindings = field_contract.get("component_solver_entry_bindings")
        inventory = field_contract.get("physics_field_descriptors")
        count = field_contract.get("physics_field_count")
        if (not isinstance(inventory, list) or not inventory or isinstance(count, bool) or
                not isinstance(count, int) or count != len(inventory)):
            raise CaptureError(f"loaded model full PhysicsField inventory/count is incomplete for {stage}")
        tags = set()
        displacement = []
        inventory_signature = []
        for raw_descriptor in inventory:
            observed = _require_mapping(raw_descriptor, f"solver_readbacks.{stage}.physics_field_descriptors")
            tag, name, observed_components = observed.get("field_tag"), observed.get("field"), observed.get("components")
            if (set(observed) != {"physics_tag", "physics_field_count", "field_tag", "field", "components"} or
                    observed.get("physics_tag") != "solid" or isinstance(observed.get("physics_field_count"), bool) or
                    not isinstance(observed.get("physics_field_count"), int) or observed.get("physics_field_count") != count or
                    not isinstance(tag, str) or not tag.strip() or tag in tags or
                    not isinstance(name, str) or not name.strip() or
                    not isinstance(observed_components, list) or not observed_components or
                    any(not isinstance(component, str) or not component.strip() for component in observed_components) or
                    len(set(observed_components)) != len(observed_components)):
                raise CaptureError(f"loaded model full PhysicsField descriptor inventory is malformed for {stage}")
            tags.add(tag)
            inventory_signature.append((tag, name, tuple(observed_components)))
            if name == "u":
                displacement.append(observed)
        if (isinstance(field_contract.get("geometry_dimension"), bool) or field_contract.get("geometry_dimension") != 2 or
                field_contract.get("geometry_axisymmetric") is not True or len(displacement) != 1 or
                isinstance(field_contract.get("selected_displacement_field_count"), bool) or
                not isinstance(field_contract.get("selected_displacement_field_count"), int) or
                field_contract.get("selected_displacement_field_count") != 1 or
                isinstance(descriptor.get("physics_field_count"), bool) or
                not isinstance(descriptor.get("physics_field_count"), int) or descriptor != displacement[0] or
                not isinstance(components, list) or len(components) != 2 or set(components) != {"u", "w"} or
                component_bindings != {"u": "comp1_u", "w": "comp1_u"}):
            raise CaptureError(f"loaded model solver field descriptor or observed u/w binding is incomplete for {stage}")
        if any(set(observed["components"]) & {"u", "w"}
               for observed in inventory if observed["field"] != "u"):
            raise CaptureError(f"loaded model nonselected descriptor aliases selected u/w component ownership for {stage}")
        signature = (field_contract.get("geometry_dimension"), field_contract.get("geometry_axisymmetric"),
                     count, tuple(inventory_signature), descriptor.get("field_tag"), descriptor.get("field"),
                     tuple(components), tuple(sorted(component_bindings.items())))
        if field_contract_signature is None:
            field_contract_signature = signature
        elif signature != field_contract_signature:
            raise CaptureError("loaded model SolverSequence stages have inconsistent field descriptors or bindings")
        fields = _require_mapping(solver.get("field_tolerances"), f"solver_readbacks.{stage}.field_tolerances")
        entry_keys = field_contract.get("solver_entry_keys")
        logical_fields = _require_mapping(solver.get("logical_component_tolerances"),
                                          f"solver_readbacks.{stage}.logical_component_tolerances")
        if (not isinstance(entry_keys, list) or any(not isinstance(key, str) or not key for key in entry_keys) or
                len(entry_keys) != len(set(entry_keys)) or set(entry_keys) != set(expected_native_atols) or
                set(fields) != set(expected_native_atols) or set(logical_fields) != set(expected_component_atols) or
                isinstance(solver.get("rtol"), bool) or
                not isinstance(solver.get("rtol"), (int, float)) or
                not math.isfinite(float(solver["rtol"])) or
                abs(float(solver["rtol"]) - 1e-5) > 1e-15 or
                solver.get("atolglobalmethod") != "unscaled" or
                isinstance(solver.get("atolglobal"), bool) or
                not isinstance(solver.get("atolglobal"), (int, float)) or
                not math.isfinite(float(solver["atolglobal"])) or
                abs(float(solver["atolglobal"]) - 1e-8) > 1e-18):
            raise CaptureError(f"loaded model v2 solver global or dependent-field tolerance readback is incomplete for {stage}")
        for field, expected_atol in expected_native_atols.items():
            values = _require_mapping(fields.get(field), f"solver_readbacks.{stage}.{field}")
            try:
                observed_atol = float(values.get("atol"))
            except (TypeError, ValueError) as exc:
                raise CaptureError(f"loaded model v2 solver atol is missing for {stage}/{field}") from exc
            if (values.get("atolmethod") != "unscaled" or
                    values.get("atolvaluemethod") != "manual" or
                    not math.isfinite(observed_atol) or abs(observed_atol - expected_atol) > 1e-20):
                raise CaptureError(f"loaded model v2 solver field tolerance differs for {stage}/{field}")
        for logical_field, expected_atol in expected_component_atols.items():
            entry_key = "comp1_u" if logical_field == "comp1_w" else logical_field
            logical = _require_mapping(logical_fields.get(logical_field),
                                       f"solver_readbacks.{stage}.logical_component_tolerances.{logical_field}")
            observed = _require_mapping(fields.get(entry_key),
                                        f"solver_readbacks.{stage}.field_tolerances.{entry_key}")
            expected_component = logical_field.removeprefix("comp1_")
            try:
                logical_atol = float(logical.get("atol"))
                observed_atol = float(observed.get("atol"))
            except (TypeError, ValueError) as exc:
                raise CaptureError(f"loaded model v2 logical solver atol is missing for {stage}/{logical_field}") from exc
            if (logical.get("component") != expected_component or
                    logical.get("solver_entry_key") != entry_key or
                    logical.get("source") != "DERIVED_FROM_OBSERVED_PHYSICS_FIELD_BINDING" or
                    logical.get("atolmethod") != observed.get("atolmethod") or
                    logical.get("atolvaluemethod") != observed.get("atolvaluemethod") or
                    not math.isfinite(logical_atol) or not math.isfinite(observed_atol) or
                    abs(logical_atol - expected_atol) > 1e-20 or
                    abs(observed_atol - expected_atol) > 1e-20):
                raise CaptureError(f"loaded model v2 logical component tolerance differs for {stage}/{logical_field}")
        dose_actual = _require_mapping(fields.get("comp1_Duv_rel"),
                                       f"solver_readbacks.{stage}.field_tolerances.comp1_Duv_rel")
        try:
            dose_actual_atol = float(dose_actual.get("atol"))
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"loaded model v2 actual dose solver atol is missing for {stage}") from exc
        dose_readback = _require_mapping(tolerance_rows.get(stage), f"dose_solver_tolerance_readbacks.{stage}")
        try:
            dose_separate_atol = float(dose_readback.get("absolute_tolerance"))
        except (TypeError, ValueError) as exc:
            raise CaptureError(f"loaded model v2 independent dose solver atol is missing for {stage}") from exc
        if (dose_actual.get("atolmethod") != "unscaled" or
                dose_actual.get("atolvaluemethod") != "manual" or
                not math.isfinite(dose_actual_atol) or abs(dose_actual_atol - 1e-8) > 1e-20 or
                not math.isfinite(dose_separate_atol) or abs(dose_separate_atol - dose_actual_atol) > 1e-20):
            raise CaptureError(f"loaded model v2 independent dose solver entry differs for {stage}")
    if row.get("native_study_run_calls") != 0:
        raise CaptureError("cure-law v2 contract readback must not submit a solver run")
    return {
        "status": "V2_MODEL_CONTRACT_READBACK_VALIDATED_NATIVE_NOT_RUN",
        "cure_law_version": "W24_CURE_LAW_V2",
        "dose_field": "Duv_rel",
        "activation_feature": "Activation",
        "maxwell_model": "GeneralizedMaxwell",
        "maxwell_branch_count": 1,
        "full_dof_absolute_tolerances": {
            **{name: float(expected_component_atols[name]) for name in expected_component_atols},
            "comp1_Duv_rel": 1e-8,
        },
        "full_dof_absolute_tolerance_scope": "FROZEN_COMPONENT_COMPARISON_INPUTS_DERIVED_FROM_OBSERVED_FIELD_BINDING",
        "effective_serendipity_dof_tolerance_conversion": "UNVERIFIED",
        "maxwell_branch_reference_state": "UNVERIFIED",
        "activation_history_semantics": "UNVERIFIED",
        "native_study_run_calls": 0,
    }


def validate_equation_view_readback_v1(value: Any, *, expected_study: str,
                                       expected_solver: str) -> dict[str, Any]:
    """Validate raw COMSOL Equation View tables without assigning semantics.

    FeatureInfo.getInfoTable returns raw row arrays. The documented 6.4 API has
    no separate header getter, so all cells and widths are retained and column
    labels are explicitly marked unavailable rather than invented.
    """
    artifact = _require_mapping(value, "Equation View raw artifact")
    table_types = ["Expression", "Shape", "Weak", "Constraint"]
    options = ["recursive", "all"]
    if (artifact.get("schema") != "W24_COMSOL_EQUATION_VIEW_READBACK_V1" or
            artifact.get("status") != "COMPLETE_RAW_TABLES_NOT_EVALUATED" or
            artifact.get("complete") is not True or artifact.get("read_only") is not True or
            artifact.get("model_mutations") != 0 or artifact.get("native_study_run_calls") != 0 or
            artifact.get("component_tag") != "comp1" or
            artifact.get("study_tag") != expected_study or artifact.get("solver_tag") != expected_solver or
            artifact.get("attached_solver_sequences") != [expected_solver] or
            artifact.get("quasistatic_readback") != "Quasistatic" or
            artifact.get("equation_view_table_types") != table_types or
            artifact.get("table_options") != options or
            artifact.get("table_request_source") != "COMSOL 6.4 FeatureInfo.getInfoTable(String,String...) API documentation" or
            artifact.get("feature_info_tag_source") != "COMSOL 6.4 EquationViewParent.featureInfo() and FeatureInfoList.tags() API" or
            artifact.get("feature_info_api_sha256") != "4235da3e67011348aeed61e3ca50fcdec888068f2152ac5d659b0280c68d03a0" or
            artifact.get("equation_view_parent_api_sha256") != "98f7b2c54e2a31dc52f9c0fdfae5073d343b035cb0d295130011f4b4ca0e732b" or
            artifact.get("column_labels") != "API_NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE" or
            artifact.get("raw_cells_preserved") is not True or artifact.get("errors") != []):
        raise CaptureError("Equation View artifact is incomplete or differs from its documented read-only schema")
    if not isinstance(artifact.get("study_tlist_readback"), str) or not artifact["study_tlist_readback"]:
        raise CaptureError("Equation View artifact omitted the actual study time-list readback")
    times = artifact.get("stored_times_s")
    if (not isinstance(times, list) or not times or
            any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(float(t)) for t in times) or
            any(float(right) <= float(left) for left, right in zip(times, times[1:]))):
        raise CaptureError("Equation View artifact omitted finite increasing stored solution times")
    physics_tags = artifact.get("component_physics_tags")
    physics_rows = artifact.get("physics")
    if (not isinstance(physics_tags, list) or not physics_tags or
            any(not isinstance(tag, str) or not tag for tag in physics_tags) or
            len(set(physics_tags)) != len(physics_tags) or
            not isinstance(physics_rows, list) or len(physics_rows) != len(physics_tags) or
            artifact.get("physics_count") != len(physics_tags)):
        raise CaptureError("Equation View artifact does not preserve actual component physics tags")

    observed_physics: list[str] = []
    feature_count = info_owner_count = table_count = expression_rows = 0

    def validate_info_owner(raw_info: Any, declared_tags: Any, *, owner_kind: str,
                            owner_tag: str, owner_path: str) -> None:
        nonlocal info_owner_count, table_count, expression_rows
        if not isinstance(raw_info, list) or not isinstance(declared_tags, list):
            raise CaptureError("Equation View owner omitted its native featureInfo tag list")
        info_owner_count += 1
        observed_tags: list[str] = []
        for info in raw_info:
            if not isinstance(info, Mapping):
                raise CaptureError("Equation View featureInfo entry is malformed")
            tag = info.get("feature_info_tag")
            if (info.get("owner_kind") != owner_kind or info.get("owner_tag") != owner_tag or
                    info.get("owner_path") != owner_path or not isinstance(tag, str) or not tag or
                    info.get("feature_info_native_tag") != tag or
                    not isinstance(info.get("feature_info_name"), (str, type(None)))):
                raise CaptureError("Equation View featureInfo identity differs from its exact owner path/tag")
            observed_tags.append(tag)
            tables = info.get("tables")
            if not isinstance(tables, list) or len(tables) != len(table_types):
                raise CaptureError("Equation View featureInfo entry omitted one or more table types")
            for table_type, table in zip(table_types, tables):
                if (not isinstance(table, Mapping) or table.get("status") != "READ" or
                        table.get("table_type") != table_type or table.get("options") != options or
                        table.get("column_labels") != "API_NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"):
                    raise CaptureError("Equation View raw table type/options/status is incomplete")
                rows, widths = table.get("raw_rows"), table.get("row_widths")
                max_columns, indices = table.get("max_column_count"), table.get("column_indices_zero_based")
                if (not isinstance(rows, list) or table.get("row_count") != len(rows) or
                        not isinstance(widths, list) or len(widths) != len(rows) or
                        isinstance(max_columns, bool) or not isinstance(max_columns, int) or max_columns < 0 or
                        not isinstance(indices, list) or indices != list(range(max_columns))):
                    raise CaptureError("Equation View raw table row/column dimensions are inconsistent")
                observed_widths: list[int] = []
                for row in rows:
                    if row is None:
                        observed_widths.append(-1)
                    elif isinstance(row, list) and all(cell is None or isinstance(cell, str) for cell in row):
                        observed_widths.append(len(row))
                    else:
                        raise CaptureError("Equation View raw table contains malformed rows or non-string cells")
                if widths != observed_widths or max(observed_widths + [0]) != max_columns:
                    raise CaptureError("Equation View raw row widths differ from preserved native cells")
                if table_type == "Expression":
                    expression_rows += len(rows)
                table_count += 1
        if observed_tags != declared_tags or len(set(observed_tags)) != len(observed_tags):
            raise CaptureError("Equation View featureInfo table set differs from the exact native tag enumeration")

    def validate_feature(feature: Any, *, parent_path: str) -> None:
        nonlocal feature_count
        if not isinstance(feature, Mapping):
            raise CaptureError("Equation View physics feature tree contains a malformed node")
        tag, path, feature_type = (feature.get("feature_tag"), feature.get("feature_path"),
                                   feature.get("feature_type"))
        if (not isinstance(tag, str) or not tag or path != parent_path + "/" + tag or
                not isinstance(feature_type, str) or not feature_type):
            raise CaptureError("Equation View feature path/tag/type is not its exact native identity")
        feature_count += 1
        validate_info_owner(feature.get("equation_view"), feature.get("feature_info_tags"),
                            owner_kind="physics_feature", owner_tag=tag, owner_path=path)
        children = feature.get("children")
        if not isinstance(children, list):
            raise CaptureError("Equation View feature omitted its recursively enumerated children")
        child_tags: set[str] = set()
        for child in children:
            if not isinstance(child, Mapping) or not isinstance(child.get("feature_tag"), str) or child["feature_tag"] in child_tags:
                raise CaptureError("Equation View child feature tag list is duplicated or malformed")
            child_tags.add(child["feature_tag"])
            validate_feature(child, parent_path=path)

    for row, expected_tag in zip(physics_rows, physics_tags):
        if not isinstance(row, Mapping):
            raise CaptureError("Equation View physics row is malformed")
        tag, path = row.get("physics_tag"), f"comp1/{expected_tag}"
        if (tag != expected_tag or row.get("physics_path") != path or
                not isinstance(row.get("physics_type"), str) or not row.get("physics_type")):
            raise CaptureError("Equation View physics path/type differs from actual component enumeration")
        observed_physics.append(tag)
        validate_info_owner(row.get("equation_view"), row.get("feature_info_tags"),
                            owner_kind="physics", owner_tag=tag, owner_path=path)
        features = row.get("features")
        if not isinstance(features, list):
            raise CaptureError("Equation View physics omitted its native feature-tag enumeration")
        feature_tags: set[str] = set()
        for feature in features:
            if (not isinstance(feature, Mapping) or not isinstance(feature.get("feature_tag"), str) or
                    feature["feature_tag"] in feature_tags):
                raise CaptureError("Equation View physics feature-tag list is duplicated or malformed")
            feature_tags.add(feature["feature_tag"])
            validate_feature(feature, parent_path=path)
    if (observed_physics != physics_tags or artifact.get("physics_feature_count") != feature_count or
            artifact.get("feature_info_owner_count") != info_owner_count or
            artifact.get("table_count") != table_count or artifact.get("expression_row_count") != expression_rows):
        raise CaptureError("Equation View artifact counters differ from its recursively preserved raw tables")
    return {
        "status": "RAW_EQUATION_VIEW_TABLES_AUTHENTICATED_NATIVE_SEMANTICS_UNVERIFIED",
        "study_tag": expected_study, "solver_tag": expected_solver,
        "stored_times_s": [float(time) for time in times],
        "component_physics_tags": list(physics_tags),
        "physics_feature_count": feature_count,
        "feature_info_owner_count": info_owner_count,
        "table_count": table_count, "expression_row_count": expression_rows,
        "column_labels": "API_NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE",
        "native_equation_semantics": "UNVERIFIED",
        "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
        "maxwell_branch_reference_state": "UNVERIFIED", "native_acceptance": "NOT_RUN",
    }


def associate_equation_view_with_xmesh_v1(equation_view: Any, xmesh: Any) -> dict[str, Any]:
    """Record exact raw-cell candidates without guessing columns or semantics."""
    artifact = _require_mapping(equation_view, "Equation View association source")
    metadata = _require_mapping(xmesh, "V3 Xmesh association source")
    if (artifact.get("schema") != "W24_COMSOL_EQUATION_VIEW_READBACK_V1" or
            artifact.get("complete") is not True or
            metadata.get("snapshot_schema") != "W24-DOF-SNAPSHOT-3" or
            metadata.get("complete_xmesh_internal_dof_capture") is not True):
        raise CaptureError("Equation View association requires complete raw tables and complete V3 Xmesh layout")
    field_names, field_counts = metadata.get("fieldNames"), metadata.get("fieldNDofs")
    dof_names = metadata.get("dofNames")
    if (not isinstance(field_names, list) or not field_names or
            not isinstance(field_counts, list) or len(field_counts) != len(field_names) or
            not isinstance(dof_names, list) or not dof_names or
            any(not isinstance(name, str) or not name for name in field_names + dof_names) or
            any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in field_counts) or
            not isinstance(metadata.get("layout_sha256"), str) or
            not SHA256_RE.fullmatch(metadata["layout_sha256"])):
        raise CaptureError("V3 Xmesh field/layout names are missing or malformed")
    cells: list[dict[str, Any]] = []

    def add_owner(owner: Mapping[str, Any], physics_tag: str, physics_type: str) -> None:
        for info in owner.get("equation_view", []):
            for table in info.get("tables", []):
                for row_index, row in enumerate(table.get("raw_rows", [])):
                    if row is None:
                        continue
                    for column_index, cell in enumerate(row):
                        if cell is None:
                            continue
                        cells.append({
                            "physics_tag": physics_tag, "physics_type": physics_type,
                            "feature_path": owner.get("feature_path", owner.get("physics_path")),
                            "feature_type": owner.get("feature_type", owner.get("physics_type")),
                            "feature_info_tag": info.get("feature_info_tag"),
                            "table_type": table.get("table_type"), "row_index": row_index,
                            "column_index": column_index, "cell_value": cell,
                        })

    def visit(feature: Any, physics_tag: str, physics_type: str) -> None:
        if not isinstance(feature, Mapping):
            raise CaptureError("Equation View association encountered malformed feature data")
        add_owner(feature, physics_tag, physics_type)
        for child in feature.get("children", []):
            visit(child, physics_tag, physics_type)

    for physics in artifact.get("physics", []):
        if not isinstance(physics, Mapping):
            raise CaptureError("Equation View association encountered malformed physics data")
        ptag, ptype = physics.get("physics_tag"), physics.get("physics_type")
        add_owner(physics, ptag, ptype)
        for feature in physics.get("features", []):
            visit(feature, ptag, ptype)

    def matches(name: str, kind: str, index: int, ndofs: int | None) -> dict[str, Any]:
        exact = [dict(cell) for cell in cells if cell["cell_value"] == name]
        state = ("EXACT_CELL_CANDIDATE_UNIQUE" if len(exact) == 1 else
                 "EXACT_CELL_CANDIDATE_AMBIGUOUS" if len(exact) > 1 else "NO_EXACT_CELL_CANDIDATE")
        return {"source_kind": kind, "name_index": index, "name": name,
                "field_ndofs": ndofs, "exact_cell_match_count": len(exact),
                "exact_cell_matches": exact, "status": state,
                "branch_identity": "UNVERIFIED_NOT_INFERRED"}

    field_rows = [matches(name, "XmeshInfo.fieldNames", index, field_counts[index])
                  for index, name in enumerate(field_names)]
    dof_rows = [matches(name, "XmeshInfoDofs.dofNames", index, None)
                for index, name in enumerate(dof_names)]
    all_unique = all(row["status"] == "EXACT_CELL_CANDIDATE_UNIQUE" for row in field_rows + dof_rows)
    return {
        "schema": "W24_EQUATION_VIEW_XMESH_EXACT_CELL_ASSOCIATION_V1",
        "status": ("EXACT_CELL_CANDIDATES_RECORDED_NATIVE_SEMANTICS_UNVERIFIED" if all_unique
                   else "CANDIDATE_ASSOCIATION_HAS_UNMATCHED_OR_AMBIGUOUS_FIELDS"),
        "exact_equality_only": True, "normalization": "NONE", "substring_matching": False,
        "column_label_interpretation": "NONE_API_DOES_NOT_EXPOSE_HEADERS",
        "equation_view_schema": artifact.get("schema"),
        "snapshot_schema": metadata.get("snapshot_schema"),
        "layout_sha256": metadata.get("layout_sha256"),
        "field_associations": field_rows, "dof_name_associations": dof_rows,
        "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
        "maxwell_branch_reference_state": "UNVERIFIED", "native_acceptance": "NOT_RUN",
    }


def _verify_public_capture_record(response: Any, operation_record: Any, *, project_root: Path,
                                  source_artifact_path: Path, expected_source_sha256: str,
                                  expected_action: str, expected_project_id: str,
                                  expected_session_id: str, expected_model_ref: Mapping[str, Any],
                                  expected_revision: int) -> dict[str, Any]:
    """Check a row already obtained from the trusted daemon's private store."""
    reply = _require_mapping(response, "ControlDaemon response")
    record = _require_mapping(operation_record, "OperationStore record")
    if (reply.get("success") is not True or record.get("status") != "SUCCEEDED" or
            record.get("job_status") != "SUCCEEDED"):
        raise CaptureError("capture requires one terminal successful public operation")
    execution = _require_mapping(reply.get("execution"), "response.execution")
    record_metadata = _require_mapping(record.get("metadata"), "OperationStore.metadata")
    request = _require_mapping(record_metadata.get("arguments"), "OperationStore.arguments")
    binding = _require_mapping(record_metadata.get("execution"), "OperationStore.execution")
    if request.get("operation_id") != "code.execute_java":
        raise CaptureError("capture did not use the exact public code.execute_java route")
    nested = _require_mapping(request.get("arguments"), "operation_call.arguments")
    arguments = _require_mapping(nested.get("arguments"), "code.execute_java.arguments")
    if nested.get("mode") != "trusted":
        raise CaptureError("capture Java route did not use the approved trusted execution mode")
    entrypoint, _expected_case = ALLOWED_PUBLIC_ACTIONS.get(expected_action, (None, None))
    if entrypoint is None or nested.get("entrypoint") != entrypoint:
        raise CaptureError("capture Java entrypoint does not match the frozen action route")
    route_action = (arguments.get("phase") if expected_action == "equation_view_readback_v1"
                    else arguments.get("action"))
    if route_action != expected_action:
        raise CaptureError("OperationStore record contains a different capture action")

    source_path = _resolve_project_file(project_root, str(source_artifact_path), "Java source", must_exist=True)
    if not isinstance(expected_source_sha256, str) or not SHA256_RE.fullmatch(expected_source_sha256):
        raise CaptureError("expected frozen source SHA-256 is missing")
    observed_source_sha = _sha256(source_path)
    if observed_source_sha != expected_source_sha256:
        raise CaptureError("Java source artifact no longer matches the frozen source SHA-256")
    referenced_source = nested.get("source_artifact")
    if not isinstance(referenced_source, str) or Path(referenced_source).name != source_path.name:
        raise CaptureError("public Java route source_artifact differs from the pinned fixture file")
    try:
        registered_source = _resolve_project_file(project_root, referenced_source, "operation source artifact", must_exist=True)
    except CaptureError:
        # Project runtimes commonly keep sources under tools/java while operation_call
        # accepts the repository-relative path. Resolve that exact joined path.
        registered_source = _resolve_project_file(project_root, str(source_path.relative_to(Path(project_root).resolve())),
                                                  "operation source artifact", must_exist=True)
    if registered_source != source_path:
        raise CaptureError("public Java source path resolves to a different file than the pinned fixture")

    for key in ("request_id", "operation_id", "idempotency_key", "request_hash", "job_id"):
        if not isinstance(record.get(key), str) or execution.get(key) != record.get(key):
            raise CaptureError(f"ControlDaemon response does not match its durable OperationStore {key}")
    if record.get("operation") != "operation_call":
        raise CaptureError("durable operation row is not the public operation_call wrapper")
    # The public ControlDaemon execution envelope for G2 model calls carries
    # session/ModelRef/revision but currently omits project_id.  The immutable
    # OperationStore execution metadata is the authoritative project binding;
    # if the response does include a project id, it must agree with that row.
    if (binding.get("project_id") != expected_project_id or
            execution.get("project_id") not in (None, expected_project_id)):
        raise CaptureError("capture response or durable request changed its exact registered project")
    if binding.get("session_id") != expected_session_id or execution.get("session_id") != expected_session_id:
        raise CaptureError("capture response or durable request changed its exact Worker session")
    if binding.get("model_ref") != dict(expected_model_ref) or execution.get("model_ref") != dict(expected_model_ref):
        raise CaptureError("capture response or durable request changed its exact ModelRef")
    if binding.get("expected_revision") != expected_revision:
        raise CaptureError("durable public request was not bound to the frozen pre-capture model revision")
    timeouts = _require_mapping(record.get("effective_timeouts"), "OperationStore.effective_timeouts")
    from comsol_mcp._execution_contract import canonical_request_hash

    recomputed_hash = canonical_request_hash(
        "code.execute_java", nested, expected_model_ref, expected_revision,
        project_id=expected_project_id, session_id=expected_session_id,
        queue_timeout_s=timeouts.get("queue_timeout_s"),
        execution_timeout_s=timeouts.get("execution_timeout_s"),
        no_progress_warning_s=timeouts.get("no_progress_warning_s"),
    )
    if record.get("request_hash") != recomputed_hash:
        raise CaptureError("OperationStore request_hash does not authenticate the exact Java source/action/ModelRef body")
    returned_revision = execution.get("revision")
    if not _is_int(returned_revision) or returned_revision < expected_revision or returned_revision > expected_revision + 1:
        raise CaptureError("capture response revision is outside the allowed same-or-single-transition readback")

    data = _require_mapping(reply.get("data"), "response.data")
    worker = _require_mapping(data.get("worker"), "response.data.worker")
    wrapped = _require_mapping(data.get("readback"), "response.data.readback")
    java = _require_mapping(wrapped.get("readback"), "response.data.readback.readback")
    worker_result = _require_mapping(worker.get("result"), "response.data.worker.result")
    if (worker.get("ok") is not True or worker.get("status") != "SUCCEEDED" or
            data.get("source_sha256") != observed_source_sha or data.get("entrypoint") != entrypoint or
            worker_result.get("readback") != java):
        raise CaptureError("capture lacks an observed terminal successful Worker Java result")
    if java.get("source_sha256") != observed_source_sha or java.get("entrypoint") != entrypoint:
        raise CaptureError("Worker Java result does not identify the exact pinned source and entrypoint")
    if java.get("model_tag") != expected_model_ref.get("model_tag"):
        raise CaptureError("Worker Java result identifies a foreign native model tag")

    stored_result = record.get("result")
    if stored_result != dict(reply):
        raise CaptureError("RPC response differs from the immutable result retained in OperationStore")
    if record.get("job_result") != dict(reply):
        raise CaptureError("RPC response differs from the matching durable Job result")
    if record.get("job_id") is None or execution.get("job_id") != record.get("job_id"):
        raise CaptureError("capture response job id differs from the matching durable Job row")
    if expected_action == "cure_v2_contract_readback":
        readback = _require_mapping(java.get("readback"), "Java cure-law v2 model readback")
        validated = validate_cure_law_v2_contract_readback(readback)
        return {
            "status": "PUBLIC_OPERATION_AND_V2_MODEL_READBACK_MATCHED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": None, "artifact_receipt": None, "artifact_data": None,
            "readback_data": dict(readback), "capture_validation": validated,
            "native_acceptance": "NOT_RUN",
        }
    if expected_action in {"build_maxwell_ramp_hold", "readback_maxwell_ramp_hold",
                           "build_gel_stress_free", "readback_gel_stress_free"}:
        readback = _require_mapping(java.get("readback"), "Java control configuration readback")
        is_maxwell = "maxwell" in expected_action
        case_id = "maxwell_ramp_hold_control" if is_maxwell else "gel_stress_free_control"
        expected_status = ("BUILT_NOT_SOLVED" if expected_action.startswith("build_")
                           else "CONTROL_CONFIGURATION_READBACK_NOT_SOLVED")
        if (readback.get("case_id") != case_id or
                readback.get("status") != expected_status or
                readback.get("native_study_run_calls") != 0 or
                readback.get("solver_submissions") != 0):
            raise CaptureError("control setup/readback route changed its exact case or submitted Study.run")
        validated = validate_control_configuration_readback(readback, case_id=case_id)
        return {
            "status": "PUBLIC_CONTROL_CONFIGURATION_OPERATION_AUTHENTICATED_NATIVE_NOT_RUN",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": None, "artifact_receipt": None, "artifact_data": None,
            "readback_data": dict(readback), "capture_validation": validated,
            "native_acceptance": "NOT_RUN",
        }
    if expected_action == "study_run_control":
        request_arguments = _require_mapping(arguments, "control Study.run request arguments")
        readback = _require_mapping(java.get("readback"), "Java control Study.run readback")
        case_id = request_arguments.get("case_id")
        study_tag = request_arguments.get("study_tag")
        expected_pair = {
            "maxwell_ramp_hold_control": "stdMaxwell",
            "gel_stress_free_control": "stdGel",
        }
        if (case_id not in expected_pair or study_tag != expected_pair[case_id] or
                readback.get("status") != "NATIVE_STUDY_RUN_RETURNED" or
                readback.get("case_id") != case_id or readback.get("study_tag") != study_tag or
                readback.get("study_run_calls_from_this_action") != 1 or
                readback.get("native_study_run_calls") != 1 or
                readback.get("quasistatic_readback") != "Quasistatic" or
                readback.get("solver_sequence") != request_arguments.get("solver_tag") or
                readback.get("campaign_id") != request_arguments.get("campaign_id") or
                readback.get("approval_sha256") != request_arguments.get("approval_sha256") or
                readback.get("control_plan_sha256") != request_arguments.get("control_plan_sha256") or
                readback.get("slot_idempotency_key") != request_arguments.get("slot_idempotency_key")):
            raise CaptureError("control Study.run operation differs from its exact case/solver/approval slot")
        if returned_revision != expected_revision + 1:
            raise CaptureError("control Study.run did not produce exactly one public model revision transition")
        requested_save_path = request_arguments.get("save_after_success_path")
        if not isinstance(requested_save_path, str) or not requested_save_path:
            raise CaptureError("control Study.run requires the exact immediate saved-MPH target")
        save_path = _resolve_project_file(
            project_root, requested_save_path, "control Study.run saved MPH", must_exist=True)
        save_receipt = _require_mapping(readback.get("immediate_save_receipt"),
                                        "control Study.run immediate-save receipt")
        size = save_path.stat().st_size
        digest = _sha256(save_path)
        if (readback.get("immediate_save_path") != requested_save_path or
                save_receipt.get("status") != "STUDY_RUN_MPH_SAVED_AND_HASHED" or
                save_receipt.get("path") != requested_save_path or
                not _is_int(save_receipt.get("size_bytes")) or save_receipt.get("size_bytes") != size or
                save_receipt.get("sha256") != digest or size <= 0):
            raise CaptureError("control Study.run save receipt differs from the exact request and saved bytes")
        return {
            "status": "PUBLIC_CONTROL_STUDY_RUN_AND_SAVE_AUTHENTICATED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"], "job_id": record["job_id"],
            "artifact": {"path": str(save_path), "size_bytes": size, "sha256": digest},
            "artifact_receipt": dict(save_receipt), "artifact_data": None,
            "readback_data": dict(readback),
            "capture_validation": {
                "status": "PUBLIC_CONTROL_STUDY_RUN_RECEIPT_SCHEMA_VALIDATED_NATIVE_NOT_RUN",
                "case_id": case_id, "study_tag": study_tag,
                "solver_tag": readback.get("solver_sequence"),
                "study_run_calls_from_this_action": 1,
                "approval_sha256": request_arguments.get("approval_sha256"),
                "control_plan_sha256": request_arguments.get("control_plan_sha256"),
                "slot_idempotency_key": request_arguments.get("slot_idempotency_key"),
                "immediate_save": {"path": str(save_path), "size_bytes": size, "sha256": digest},
            },
            "native_acceptance": "NOT_RUN",
        }
    if expected_action == "study_run":
        arguments = _require_mapping(arguments, "Study.run public arguments")
        readback = _require_mapping(java.get("readback"), "Java Study.run readback")
        if (readback.get("status") != "NATIVE_STUDY_RUN_RETURNED" or
                readback.get("case_id") != arguments.get("case_id") or
                readback.get("study_tag") != arguments.get("study_tag") or
                readback.get("study_run_calls_from_this_action") != 1 or
                not isinstance(readback.get("solver_sequence"), str) or not readback.get("solver_sequence")):
            raise CaptureError("Study.run public Java response is not the exact one-call requested solve receipt")
        requested_save_path = arguments.get("save_after_success_path")
        artifact = None
        artifact_receipt = readback.get("immediate_save_receipt")
        save_validation: dict[str, Any]
        if requested_save_path is not None:
            if not isinstance(requested_save_path, str) or not requested_save_path:
                raise CaptureError("Study.run save request path must be a nonempty exact project path")
            save_path = _resolve_project_file(
                project_root, requested_save_path, "Study.run immediate-save artifact", must_exist=True)
            if str(save_path) != requested_save_path:
                raise CaptureError("Study.run immediate-save request path is not its canonical registered-project path")
            receipt = _require_mapping(artifact_receipt, "Study.run immediate-save receipt")
            size = save_path.stat().st_size
            digest = _sha256(save_path)
            if (readback.get("immediate_save_path") != requested_save_path or
                    receipt.get("status") != "STUDY_RUN_MPH_SAVED_AND_HASHED" or
                    receipt.get("path") != requested_save_path or
                    not _is_int(receipt.get("size_bytes")) or receipt["size_bytes"] <= 0 or
                    not isinstance(receipt.get("sha256"), str) or
                    not SHA256_RE.fullmatch(receipt["sha256"]) or
                    size != receipt.get("size_bytes") or digest != receipt.get("sha256")):
                raise CaptureError("Study.run save path/size/SHA-256 receipt does not match its request and saved bytes")
            artifact = {"path": str(save_path), "size_bytes": size, "sha256": digest}
            save_validation = {
                "status": "PUBLIC_STUDY_RUN_SAVE_RECEIPT_MATCHED_REQUEST_AND_BYTES",
                "save_request_path": requested_save_path,
                "size_bytes": size,
                "sha256": digest,
            }
        else:
            if (readback.get("immediate_save_path") not in (None, "") or
                    artifact_receipt is not None):
                raise CaptureError("Study.run returned an immediate-save receipt without a matching save request")
            artifact_receipt = None
            save_validation = {
                "status": "PUBLIC_STUDY_RUN_NO_SAVE_REQUEST",
                "save_request_path": None,
            }
        return {
            "status": "PUBLIC_OPERATION_AND_STUDY_RUN_MATCHED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": artifact,
            "artifact_receipt": dict(artifact_receipt) if isinstance(artifact_receipt, Mapping) else None,
            "artifact_data": None,
            "readback_data": dict(readback), "capture_validation": {
                "status": "PUBLIC_STUDY_RUN_RECEIPT_SCHEMA_VALIDATED_NATIVE_NOT_RUN",
                "case_id": arguments.get("case_id"), "study_tag": arguments.get("study_tag"),
                "solver_tag": readback.get("solver_sequence"),
                "study_run_calls_from_this_action": 1,
                **save_validation,
            }, "native_acceptance": "NOT_RUN",
        }
    if expected_action == "readback":
        readback = _require_mapping(java.get("readback"), "Java native setup readback")
        if (readback.get("status") != "SCIENCE_ACTIONS_READY_NOT_SOLVED" or
                readback.get("study_run_calls") != 0 or
                not isinstance(readback.get("studies"), list) or
                not isinstance(readback.get("solver_readbacks"), Mapping) or
                not isinstance(readback.get("study_time_readbacks"), Mapping) or
                readback.get("quasistatic_readback") != "Quasistatic"):
            raise CaptureError("public native setup readback is incomplete or reports a solver run")
        return {
            "status": "PUBLIC_NATIVE_SETUP_READBACK_MATCHED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": None, "artifact_receipt": None, "artifact_data": None,
            "readback_data": dict(readback), "capture_validation": {
                "status": "PUBLIC_SETUP_READBACK_SCHEMA_VALIDATED_NATIVE_NOT_RUN",
                "native_study_run_calls": 0,
                "study_count": len(readback["studies"]),
                "solver_count": len(readback["solver_readbacks"]),
                "study_time_readback_count": len(readback["study_time_readbacks"]),
                "quasistatic_readback": "Quasistatic",
            }, "native_acceptance": "NOT_RUN",
        }
    if expected_action in {"solution_snapshot", "cure_metrics_capture"}:
        readback = _require_mapping(java.get("readback"), f"Java {expected_action} readback")
        if (arguments.get("solver_tag") != readback.get("solver_tag") or
                arguments.get("path") != readback.get("path")):
            raise CaptureError(f"{expected_action} response differs from its exact public solver/path request")
        artifact_path = _resolve_project_file(project_root, readback.get("path"),
                                              f"{expected_action} artifact", must_exist=True)
        size = artifact_path.stat().st_size
        digest = _sha256(artifact_path)
        if expected_action == "solution_snapshot":
            if (readback.get("status") != "SOLUTION_SNAPSHOT_WRITTEN" or
                    readback.get("real_solution") is not True or
                    not _is_int(readback.get("stored_time_count")) or readback["stored_time_count"] <= 0 or
                    not _is_int(readback.get("dof_count")) or readback["dof_count"] <= 0 or
                    not isinstance(readback.get("dof_names"), list)):
                raise CaptureError("solution_snapshot action lacks its exact completed real-solution readback")
            if readback.get("size_bytes") is not None and readback.get("size_bytes") != size:
                raise CaptureError("solution_snapshot artifact size differs from its public readback")
            from tools.run_native_w24_cure_science import iter_solution_snapshot

            frames = list(iter_solution_snapshot(artifact_path))
            if (len(frames) != readback["stored_time_count"] or not frames or
                    frames[0]["dofs"].get("dofNames") != readback.get("dof_names") or
                    len(frames[0]["dofs"].get("geomNums", [])) != readback["dof_count"]):
                raise CaptureError("solution_snapshot artifact differs from its exact public DOF/time readback")
            validation = {"status": "PUBLIC_SOLUTION_SNAPSHOT_READBACK_MATCHED_NATIVE_NOT_RUN",
                          "stored_time_count": len(frames), "dof_count": readback["dof_count"],
                          "dof_names": list(readback["dof_names"]), "sha256": digest}
        else:
            if (readback.get("status") != "NATIVE_CURE_METRICS_CAPTURED" or
                    not isinstance(readback.get("sha256"), str) or
                    not SHA256_RE.fullmatch(readback["sha256"]) or
                    readback.get("size_bytes") != size or digest != readback.get("sha256")):
                raise CaptureError("cure_metrics_capture artifact size or SHA-256 differs from its public readback")
            try:
                artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CaptureError("cure_metrics_capture artifact is not valid JSON") from exc
            if (not isinstance(artifact_data, Mapping) or
                    artifact_data.get("status") != "NATIVE_CURE_METRICS_CAPTURED" or
                    artifact_data.get("solver_tag") != readback.get("solver_tag")):
                raise CaptureError("cure_metrics_capture artifact differs from its public solver/status readback")
            validation = {"status": "PUBLIC_CURE_METRICS_ARTIFACT_HASH_MATCHED_NATIVE_NOT_RUN",
                          "sha256": digest, "size_bytes": size}
        return {
            "status": "PUBLIC_POST_CAPTURE_OPERATION_MATCHED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": {"path": str(artifact_path), "size_bytes": size, "sha256": digest},
            "artifact_receipt": dict(readback), "artifact_data": None,
            "readback_data": dict(readback), "capture_validation": validation,
            "native_acceptance": "NOT_RUN",
        }
    artifact_receipt = _require_mapping(java.get("readback"), "Java capture artifact receipt")
    if arguments.get("solver_tag") != artifact_receipt.get("solver_tag"):
        raise CaptureError("Java result solver tag differs from the exact public request")
    if arguments.get("path") != artifact_receipt.get("path"):
        raise CaptureError("Java result artifact path differs from the exact public request")
    if expected_action == "equation_view_readback_v1":
        if (arguments.get("study_tag") != artifact_receipt.get("study_tag") or
                artifact_receipt.get("status") != "EQUATION_VIEW_RAW_TABLES_CAPTURED" or
                artifact_receipt.get("schema") != "W24_COMSOL_EQUATION_VIEW_READBACK_V1" or
                artifact_receipt.get("complete") is not True or
                artifact_receipt.get("native_acceptance") != "NOT_RUN"):
            raise CaptureError("Equation View Worker receipt differs from the exact read-only request")
        artifact_path = _resolve_project_file(
            project_root, artifact_receipt.get("path"), "Equation View raw artifact", must_exist=True)
        size = artifact_receipt.get("size_bytes")
        expected_digest = artifact_receipt.get("sha256")
        if (not _is_int(size) or size <= 0 or artifact_path.stat().st_size != size or
                not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest) or
                _sha256(artifact_path) != expected_digest):
            raise CaptureError("Equation View artifact bytes differ from the exact Worker size/SHA-256 receipt")
        try:
            artifact_data = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaptureError("Equation View raw artifact is not valid JSON") from exc
        validated = validate_equation_view_readback_v1(
            artifact_data, expected_study=str(arguments.get("study_tag")),
            expected_solver=str(arguments.get("solver_tag")))
        for key in ("study_tlist_readback", "quasistatic_readback", "stored_times_s"):
            if artifact_receipt.get(key) != artifact_data.get(key):
                raise CaptureError(f"Equation View receipt {key} differs from its raw source artifact")
        return {
            "status": "PUBLIC_EQUATION_VIEW_OPERATION_AND_RAW_TABLES_AUTHENTICATED_NATIVE_REVIEW_REQUIRED",
            "source_path": str(source_path), "source_sha256": observed_source_sha,
            "project_id": expected_project_id, "session_id": expected_session_id,
            "model_ref": dict(expected_model_ref), "revision_before": expected_revision,
            "revision_after": returned_revision, "request_id": record["request_id"],
            "operation_id": record["operation_id"], "idempotency_key": record["idempotency_key"],
            "request_hash": record["request_hash"],
            "job_id": record["result"].get("execution", {}).get("job_id"),
            "artifact": {"path": str(artifact_path), "size_bytes": size, "sha256": expected_digest},
            "artifact_receipt": dict(artifact_receipt), "artifact_data": artifact_data,
            "readback_data": dict(artifact_receipt), "capture_validation": validated,
            "native_acceptance": "NOT_RUN",
        }
    if expected_action in {"solution_snapshot_v2", "solution_snapshot_v3"}:
        if (arguments.get("study_tag") != artifact_receipt.get("study_tag") or
                artifact_receipt.get("quasistatic_readback") != "Quasistatic" or
                not isinstance(artifact_receipt.get("study_tlist_readback"), str) or
                not artifact_receipt.get("study_tlist_readback")):
            raise CaptureError("Xmesh snapshot omitted its exact study or actual Quasistatic readback")
    if expected_action == "solution_snapshot_v2":
        snapshot_path = _resolve_project_file(project_root, artifact_receipt.get("path"),
                                              "V2 Xmesh snapshot", must_exist=True)
        size = artifact_receipt.get("size_bytes")
        expected_snapshot_hash = artifact_receipt.get("sha256")
        if (not _is_int(size) or size <= 0 or snapshot_path.stat().st_size != size or
                not isinstance(expected_snapshot_hash, str) or
                not SHA256_RE.fullmatch(expected_snapshot_hash) or
                _sha256(snapshot_path) != expected_snapshot_hash):
            raise CaptureError("V2 Xmesh snapshot size or SHA-256 differs from the Java receipt")
        from tools.run_native_w24_cure_science import iter_solution_snapshot

        frame_count = 0
        first_frame: Mapping[str, Any] | None = None
        dof_names: list[str] = []
        stored_times: list[float] = []
        expected_dofs = artifact_receipt.get("dof_count")
        expected_dof_names = artifact_receipt.get("dof_names")
        expected_max_vector = artifact_receipt.get("max_solution_vector_index")
        for frame in iter_solution_snapshot(snapshot_path):
            if first_frame is None:
                first_frame = frame
                dof_names = list(frame["dofs"]["dofNames"])
            elif frame["dofs"] != first_frame["dofs"]:
                raise CaptureError("V2 Xmesh snapshot changed its exact DOF mapping between stored times")
            stored_times.append(float(frame["time_s"]))
            frame_count += 1
        if (first_frame is None or first_frame["dofs"].get("snapshot_schema") != "W24-DOF-SNAPSHOT-2" or
                first_frame["dofs"].get("coordinate_axes") not in (2, 3) or
                artifact_receipt.get("coordinate_axes") != first_frame["dofs"].get("coordinate_axes") or
                not _is_int(expected_dofs) or len(first_frame["dofs"]["geomNums"]) != expected_dofs or
                not isinstance(expected_dof_names, list) or expected_dof_names != first_frame["dofs"]["dofNames"] or
                not _is_int(expected_max_vector) or
                expected_max_vector != max(first_frame["dofs"]["solVectorInds"]) or
                artifact_receipt.get("real_solution") is not True or
                artifact_receipt.get("complete_xmesh_dofs") is not True or
                artifact_receipt.get("stored_time_count") != frame_count):
            raise CaptureError("V2 Xmesh snapshot does not contain the exact complete native mapping/time receipt")
        evidence = {"path": str(snapshot_path), "size_bytes": size, "sha256": _sha256(snapshot_path)}
        validated = {"status": "V2_FULL_XMESH_SNAPSHOT_FORMAT_VALIDATED_NATIVE_NOT_RUN",
                     "snapshot_schema": "W24-DOF-SNAPSHOT-2",
                     "coordinate_axes": first_frame["dofs"]["coordinate_axes"],
                     "complete_dof_count": expected_dofs, "stored_time_count": frame_count,
                     "dof_names": dof_names, "stored_times_s": stored_times}
        artifact = None
    elif expected_action == "solution_snapshot_v3":
        snapshot_path = _resolve_project_file(project_root, artifact_receipt.get("path"),
                                              "V3 full-Xmesh snapshot", must_exist=True)
        size = artifact_receipt.get("size_bytes")
        expected_snapshot_hash = artifact_receipt.get("sha256")
        if (not _is_int(size) or size <= 0 or snapshot_path.stat().st_size != size or
                not isinstance(expected_snapshot_hash, str) or
                not SHA256_RE.fullmatch(expected_snapshot_hash) or
                _sha256(snapshot_path) != expected_snapshot_hash):
            raise CaptureError("V3 full-Xmesh snapshot size or SHA-256 differs from the Java receipt")
        from tools.run_native_w24_cure_science import iter_solution_snapshot

        frame_count = 0
        first_frame: Mapping[str, Any] | None = None
        stored_times: list[float] = []
        for frame in iter_solution_snapshot(snapshot_path):
            if first_frame is None:
                first_frame = frame
            elif frame["dofs"] != first_frame["dofs"]:
                raise CaptureError("V3 full-Xmesh layout or solution-index mapping changed between stored times")
            stored_times.append(float(frame["time_s"]))
            frame_count += 1
        if first_frame is None:
            raise CaptureError("V3 full-Xmesh snapshot contains no stored solution frame")
        metadata = first_frame["dofs"]
        summary = metadata.get("mapping_summary")
        if not isinstance(summary, Mapping):
            raise CaptureError("V3 full-Xmesh parser omitted its explicit solution-index coverage summary")
        expected_complete = summary.get("full_vector_and_internal_dof_map_covered") is True
        expected_fields = artifact_receipt.get("field_names")
        expected_field_counts = artifact_receipt.get("field_ndofs")
        if (metadata.get("snapshot_schema") != "W24-DOF-SNAPSHOT-3" or
                metadata.get("coordinate_axes") not in (2, 3) or
                metadata.get("xmesh_n_dofs") != artifact_receipt.get("xmesh_n_dofs") or
                metadata.get("fieldNames") != expected_fields or
                metadata.get("fieldNDofs") != expected_field_counts or
                len(metadata.get("geomNums", [])) != artifact_receipt.get("dof_count") or
                metadata.get("element_local_map_group_count") != artifact_receipt.get("element_local_map_group_count") or
                metadata.get("element_local_map_entries") != artifact_receipt.get("element_local_map_entries") or
                metadata.get("invalid_element_dof_references") != artifact_receipt.get("invalid_element_dof_references") or
                summary.get("unmapped_dof_rows") != artifact_receipt.get("unmapped_xmesh_dof_rows") or
                summary.get("invalid_solution_indices") != artifact_receipt.get("invalid_xmesh_solution_indices") or
                summary.get("out_of_range_solution_indices") != artifact_receipt.get("out_of_range_xmesh_solution_indices") or
                summary.get("duplicate_solution_vector_index_rows") != artifact_receipt.get("duplicate_solution_vector_index_rows") or
                summary.get("unrepresented_solution_vector_indices") != artifact_receipt.get("unrepresented_solution_vector_indices") or
                expected_complete is not artifact_receipt.get("complete_internal_dof_capture") or
                artifact_receipt.get("maxwell_branch_field_identity") != "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME" or
                artifact_receipt.get("stored_time_count") != frame_count or
                artifact_receipt.get("schema") != "W24-DOF-SNAPSHOT-3" or
                artifact_receipt.get("real_solution") is not True):
            raise CaptureError("V3 full-Xmesh snapshot disagrees with its explicit layout/mapping receipt")
        if not expected_complete:
            raise CaptureError("V3 full-Xmesh snapshot has unmapped, invalid, or uncovered solution-vector entries")
        validated = {
            "status": "V3_FULL_XMESH_INTERNAL_DOF_CAPTURE_FORMAT_VALIDATED_NATIVE_NOT_RUN",
            "snapshot_schema": "W24-DOF-SNAPSHOT-3",
            "coordinate_axes": metadata["coordinate_axes"],
            "xmesh_n_dofs": metadata["xmesh_n_dofs"],
            "field_names": list(metadata["fieldNames"]),
            "field_ndofs": list(metadata["fieldNDofs"]),
            "complete_internal_dof_capture": True,
            "mapping_summary": dict(summary),
            "element_local_map_group_count": metadata["element_local_map_group_count"],
            "element_local_map_entries": metadata["element_local_map_entries"],
            "layout_sha256": metadata["layout_sha256"],
            "stored_times_s": stored_times,
            "maxwell_branch_field_identity": "UNVERIFIED_NOT_INFERRED_FROM_FIELD_NAME",
            "maxwell_branch_reference_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
            "native_acceptance": "NOT_RUN",
        }
        evidence = {"path": str(snapshot_path), "size_bytes": size, "sha256": _sha256(snapshot_path)}
        artifact = None
    else:
        artifact, evidence = _read_artifact(artifact_receipt, project_root, "capture artifact")
        validated = validate_capture_artifact(artifact, expected_action=expected_action)
        if (artifact_receipt.get("solver_tag") != artifact.get("solver_tag") or
                artifact_receipt.get("study_tag") != artifact.get("study_tag")):
            raise CaptureError("Java public response solver/study receipt differs from the raw artifact")
        if arguments.get("study_tag") is not None and arguments.get("study_tag") != artifact.get("study_tag"):
            raise CaptureError("Java result study tag differs from the exact public request")
        if expected_action in {"capture_maxwell_control", "capture_gel_control"} and \
                arguments.get("control_plan_sha256") is not None:
            expected_identity = {
                "campaign_id": arguments.get("campaign_id"),
                "approval_sha256": arguments.get("approval_sha256"),
                "control_plan_sha256": arguments.get("control_plan_sha256"),
                "slot_idempotency_key": arguments.get("slot_idempotency_key"),
            }
            if (artifact.get("schema") != CONTROL_CAPTURE_SCHEMA_V2 or
                    any(not isinstance(value, str) or not value or artifact.get(key) != value
                        for key, value in expected_identity.items())):
                raise CaptureError("V2 control capture differs from its authenticated approval/plan/slot request")
    return {
        "status": "PUBLIC_CAPTURE_ENVELOPE_AND_ARTIFACT_MATCHED_NATIVE_REVIEW_REQUIRED",
        "source_path": str(source_path),
        "source_sha256": observed_source_sha,
        "project_id": expected_project_id,
        "session_id": expected_session_id,
        "model_ref": dict(expected_model_ref),
        "revision_before": expected_revision,
        "revision_after": returned_revision,
        "request_id": record["request_id"],
        "operation_id": record["operation_id"],
        "idempotency_key": record["idempotency_key"],
        "request_hash": record["request_hash"],
        "job_id": record["result"].get("execution", {}).get("job_id"),
        "artifact_receipt": dict(artifact_receipt),
        "artifact": evidence,
        "artifact_data": artifact,
        "capture_validation": validated,
        "native_acceptance": "NOT_RUN",
    }


def verify_public_capture(daemon: Any, response: Any, *, operation_id: str,
                          project_root: Path, source_artifact_path: Path,
                          expected_source_sha256: str, expected_action: str,
                          expected_project_id: str, expected_session_id: str,
                          expected_model_ref: Mapping[str, Any],
                          expected_revision: int) -> dict[str, Any]:
    """Read authoritative operation/job/project facts from the live daemon.

    Callers cannot authenticate a capture by presenting their own copy of
    OperationStore metadata. The project grant and matching original rows are
    reread through the injected current ControlDaemon and its private SQLite
    store immediately before the capture receipt is accepted.
    """
    from comsol_mcp._operation_store import OperationStore

    store = getattr(daemon, "store", None)
    authority = getattr(daemon, "project_authority", None)
    if not isinstance(store, OperationStore) or not callable(getattr(authority, "authorize_operation", None)):
        raise CaptureError("capture verification requires the current ControlDaemon private OperationStore and project authority")
    if not isinstance(operation_id, str) or not operation_id:
        raise CaptureError("capture verification requires its durable public operation_id")
    try:
        authority.authorize_operation(expected_project_id, "trusted_code")
    except Exception as exc:
        raise CaptureError("capture project is no longer authorized for trusted Java execution") from exc
    record = store.get_operation(operation_id)
    job = store.operation_job(operation_id)
    if not isinstance(record, Mapping) or record.get("operation_id") != operation_id:
        raise CaptureError("original public capture operation row is unavailable from the current private store")
    if not isinstance(job, Mapping) or job.get("operation_id") != operation_id:
        raise CaptureError("matching public capture Job row is unavailable from the current private store")
    joined = dict(record)
    joined["job_id"] = job.get("job_id")
    joined["job_status"] = job.get("status")
    joined["job_result"] = job.get("result")
    return _verify_public_capture_record(
        response, joined, project_root=project_root,
        source_artifact_path=source_artifact_path,
        expected_source_sha256=expected_source_sha256,
        expected_action=expected_action,
        expected_project_id=expected_project_id,
        expected_session_id=expected_session_id,
        expected_model_ref=expected_model_ref,
        expected_revision=expected_revision,
    )


def verify_public_model_load(daemon: Any, response: Any, *, project_root: Path,
                             requested_path: Path, expected_file_sha256: str,
                             expected_project_id: str, expected_session_id: str,
                             expected_model_ref: Mapping[str, Any],
                             expected_revision: int) -> dict[str, Any]:
    """Authenticate one exact model_load and its saved MPH against SQLite.

    The project id and operation/job metadata come from the current daemon's
    private store. This helper does not accept caller-supplied metadata copies.
    """
    from comsol_mcp._operation_store import OperationStore
    from comsol_mcp._execution_contract import canonical_request_hash

    reply = _require_mapping(response, "model_load response")
    store = getattr(daemon, "store", None)
    authority = getattr(daemon, "project_authority", None)
    backend = getattr(daemon, "backend", None)
    if (not isinstance(store, OperationStore) or
            not callable(getattr(authority, "authorize_operation", None)) or
            not callable(getattr(backend, "model_project_binding", None))):
        raise CaptureError("model_load verification requires the current private OperationStore, project authority, and managed backend")
    try:
        authority.authorize_operation(expected_project_id, "project_write")
    except Exception as exc:
        raise CaptureError("model_load project is no longer authorized") from exc

    execution = _require_mapping(reply.get("execution"), "model_load response.execution")
    response_data = reply.get("data")
    declared_project_fields = [("response", reply)]
    if isinstance(response_data, Mapping):
        declared_project_fields.append(("response.data", response_data))
    declared_project_fields.append(("response.execution", execution))
    for label, source in declared_project_fields:
        echoed_project_id = source.get("project_id")
        if echoed_project_id is not None and echoed_project_id != expected_project_id:
            raise CaptureError(f"model_load {label} echoed a different project id")
    operation_id = execution.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise CaptureError("model_load response omitted its durable operation_id")
    record = store.get_operation(operation_id)
    job = store.operation_job(operation_id)
    if (not isinstance(record, Mapping) or record.get("operation_id") != operation_id or
            not isinstance(job, Mapping) or job.get("operation_id") != operation_id):
        raise CaptureError("original model_load operation or its matching Job row is unavailable")
    if (record.get("operation") != "model_load" or record.get("status") != "SUCCEEDED" or
            job.get("status") != "SUCCEEDED" or
            job.get("operation_id") != operation_id or
            not isinstance(job.get("job_id"), str) or not job.get("job_id")):
        raise CaptureError("model_load requires its exact successful terminal OperationStore and Job rows")

    metadata = _require_mapping(record.get("metadata"), "model_load OperationStore.metadata")
    arguments = _require_mapping(metadata.get("arguments"), "model_load OperationStore.arguments")
    stored_execution = _require_mapping(metadata.get("execution"), "model_load OperationStore.execution")
    path = _resolve_project_file(project_root, str(requested_path), "saved MPH", must_exist=True)
    if (arguments.get("path") != str(path) or
            stored_execution.get("project_id") != expected_project_id or
            stored_execution.get("session_id") != expected_session_id):
        raise CaptureError("model_load operation path or registered project identity differs from the requested source")
    if (stored_execution.get("model_ref") is not None or
            stored_execution.get("expected_revision") is not None):
        raise CaptureError("public model_load request must be unbound; its returned ModelRef is verified from the immutable result")
    if (not isinstance(expected_file_sha256, str) or not SHA256_RE.fullmatch(expected_file_sha256) or
            _sha256(path) != expected_file_sha256):
        raise CaptureError("saved MPH bytes differ from the frozen source artifact hash")

    for key in ("request_id", "operation_id", "idempotency_key", "request_hash"):
        if not isinstance(record.get(key), str) or execution.get(key) != record.get(key):
            raise CaptureError(f"model_load response does not match the durable OperationStore {key}")
    if execution.get("job_id") != job.get("job_id"):
        raise CaptureError("model_load response job_id differs from its exact durable Job row")
    timeouts = _require_mapping(record.get("effective_timeouts"), "model_load effective timeouts")
    recomputed = canonical_request_hash(
        "model_load", dict(arguments), stored_execution.get("model_ref"),
        stored_execution.get("expected_revision"),
        project_id=expected_project_id,
        session_id=stored_execution.get("session_id"),
        queue_timeout_s=timeouts.get("queue_timeout_s"),
        execution_timeout_s=timeouts.get("execution_timeout_s"),
        no_progress_warning_s=timeouts.get("no_progress_warning_s"),
    )
    if record.get("request_hash") != recomputed:
        raise CaptureError("model_load request_hash does not authenticate its path and project-bound request")

    response_result = _require_mapping(record.get("result"), "model_load stored result")
    if response_result != dict(reply) or job.get("result") != dict(reply):
        raise CaptureError("observed model_load response differs from its immutable OperationStore or Job result")
    if (execution.get("session_id") != expected_session_id or
            execution.get("model_ref") != dict(expected_model_ref) or
            execution.get("revision") != expected_revision):
        raise CaptureError("model_load response changed the exact session, ModelRef, or revision")
    persisted = backend.model_project_binding(dict(expected_model_ref))
    if (not isinstance(persisted, Mapping) or persisted.get("attribution") != "PROJECT_BOUND" or
            persisted.get("project_id") != expected_project_id):
        raise CaptureError("loaded ModelRef lacks its authoritative persisted project binding")
    return {
        "status": "PUBLIC_MODEL_LOAD_AND_SAVED_MPH_AUTHENTICATED",
        "project_id": expected_project_id,
        "session_id": expected_session_id,
        "model_ref": dict(expected_model_ref),
        "revision": expected_revision,
        "request_id": record["request_id"],
        "operation_id": operation_id,
        "idempotency_key": record["idempotency_key"],
        "request_hash": record["request_hash"],
        "job_id": job["job_id"],
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": expected_file_sha256,
        "persisted_project_binding": dict(persisted),
        "native_acceptance": "NOT_RUN",
    }


def dispatch_capture(daemon: Any, *, project_root: Path, source_artifact: str,
                    expected_source_sha256: str, action: str, arguments: Mapping[str, Any],
                    project_id: str, session_id: str, model_ref: Mapping[str, Any],
                    revision: int, timeout_s: float = 120.0) -> dict[str, Any]:
    """Dispatch one read-only capture on an injected, already-live ControlDaemon.

    The caller owns server/Worker lifecycle and the campaign birth budget. This
    function returns immediately on pending/unknown operations and never retries.
    """
    if action not in ALLOWED_CAPTURE_ACTIONS:
        raise CaptureError("unsupported capture action")
    source_path = _resolve_project_file(project_root, source_artifact, "Java source", must_exist=True)
    if _sha256(source_path) != expected_source_sha256:
        raise CaptureError("capture Java source differs from its externally frozen hash")
    if not isinstance(timeout_s, (float, int)) or isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise CaptureError("capture RPC timeout must be positive and finite")
    call_arguments = dict(arguments)
    call_arguments["action"] = action
    for required in ("solver_tag", "path"):
        if not isinstance(call_arguments.get(required), str) or not call_arguments[required]:
            raise CaptureError(f"capture action requires an exact {required}")
    if action in {"history_capture_v2", "solution_snapshot_v2", "solution_snapshot_v3"} and (
            not isinstance(call_arguments.get("study_tag"), str) or not call_arguments["study_tag"]):
        raise CaptureError("capture action requires its exact attached study_tag")
    entrypoint, _ = ALLOWED_CAPTURE_ACTIONS[action]
    key_payload = {"action": action, "arguments": call_arguments, "project_id": project_id,
                   "session_id": session_id, "model_ref": dict(model_ref), "revision": revision,
                   "source_sha256": expected_source_sha256}
    key_hash = hashlib.sha256(json.dumps(key_payload, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode("utf-8")).hexdigest()
    idempotency_key = f"w24-cure-v2-capture-{key_hash}"
    request_id = f"w24-cure-v2-capture-{key_hash}"
    nested = {"operation_id": "code.execute_java", "arguments": {
        "source_artifact": str(Path(source_artifact)), "entrypoint": entrypoint,
        "arguments": call_arguments, "mode": "trusted"}}
    from tools.run_native_resume_smoke import _dispatch

    response = _dispatch(
        daemon, "operation_call", nested, project_id=project_id,
        ref=dict(model_ref), revision=revision, idempotency_key=idempotency_key,
        request_id=request_id, rpc_timeout_s=float(timeout_s),
    )
    response_execution = response.get("execution") if isinstance(response, Mapping) else None
    operation_id = response_execution.get("operation_id") if isinstance(response_execution, Mapping) else None
    if not isinstance(operation_id, str):
        raise CaptureError("public capture response lacks durable OperationStore identity; no retry attempted")
    return verify_public_capture(
        daemon, response, operation_id=operation_id,
        project_root=project_root, source_artifact_path=source_path,
        expected_source_sha256=expected_source_sha256, expected_action=action,
        expected_project_id=project_id, expected_session_id=session_id,
        expected_model_ref=model_ref, expected_revision=revision,
    )
