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
CONTROL_COORDINATE_M = ((50e-6, 50e-6, 50e-6),)
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
    "history_capture_v2": ("W24CureScienceFixture#run", None),
}
ALLOWED_PUBLIC_ACTIONS = {
    **ALLOWED_CAPTURE_ACTIONS,
    "study_run": ("W24CureScienceFixture#run", None),
    "readback": ("W24CureScienceFixture#run", None),
    "solution_snapshot": ("W24CureScienceFixture#run", None),
    "cure_metrics_capture": ("W24CureScienceFixture#run", None),
    "cure_v2_contract_readback": ("W24CureCouponFixture#run", None),
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
        schema, case_id = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1", "maxwell_ramp_hold_control"
        expressions, units, expected_times = list(MAXWELL_EXPRESSIONS), list(MAXWELL_UNITS), MAXWELL_TIMES_S
        expected_tlist = "range(0[s],1[s],901[s])"
    elif expected_action == "capture_gel_control":
        schema, case_id = "W24_CURE_V2_NATIVE_CONTROL_CAPTURE_V1", "gel_stress_free_control"
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
    if row.get("schema") != schema:
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
    expected_coords = [list(point) for point in (CONTROL_COORDINATE_M if expected_action != "history_capture_v2" else (
        (25e-6, 520e-6), (50e-6, 530e-6), (75e-6, 540e-6)))]
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
        "shape": expected_shape,
        "quasistatic_readback": "Quasistatic",
        "maxwell_branch_reference_state": "UNVERIFIED",
        "native_execution_performed_by_validator": False,
    }


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
    expected_solver_atols = {
        "comp1_T": 1e-4,
        "comp1_alpha": 1e-8,
        "comp1_alpha_iso": 1e-8,
        "comp1_qpost": 1e-8,
        "comp1_u": 1e-12,
        "comp1_w": 1e-12,
    }
    if not isinstance(solver_rows, Mapping) or set(solver_rows) != expected_stages:
        raise CaptureError("loaded model v2 solver readback omitted a staged SolverSequence")
    for stage, raw_solver in solver_rows.items():
        solver = _require_mapping(raw_solver, f"solver_readbacks.{stage}")
        fields = _require_mapping(solver.get("field_tolerances"), f"solver_readbacks.{stage}.field_tolerances")
        if (set(fields) != set(expected_solver_atols) or
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
        for field, expected_atol in expected_solver_atols.items():
            values = _require_mapping(fields.get(field), f"solver_readbacks.{stage}.{field}")
            try:
                observed_atol = float(values.get("atol"))
            except (TypeError, ValueError) as exc:
                raise CaptureError(f"loaded model v2 solver atol is missing for {stage}/{field}") from exc
            if (values.get("atolmethod") != "unscaled" or
                    values.get("atolvaluemethod") != "manual" or
                    not math.isfinite(observed_atol) or abs(observed_atol - expected_atol) > 1e-20):
                raise CaptureError(f"loaded model v2 solver field tolerance differs for {stage}/{field}")
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
            **expected_solver_atols,
            "comp1_Duv_rel": 1e-8,
        },
        "maxwell_branch_reference_state": "UNVERIFIED",
        "activation_history_semantics": "UNVERIFIED",
        "native_study_run_calls": 0,
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
    if arguments.get("action") != expected_action:
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
    if expected_action == "solution_snapshot_v2":
        if (arguments.get("study_tag") != artifact_receipt.get("study_tag") or
                artifact_receipt.get("quasistatic_readback") != "Quasistatic" or
                not isinstance(artifact_receipt.get("study_tlist_readback"), str) or
                not artifact_receipt.get("study_tlist_readback")):
            raise CaptureError("V2 Xmesh snapshot omitted its exact study or actual Quasistatic readback")
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
    else:
        artifact, evidence = _read_artifact(artifact_receipt, project_root, "capture artifact")
        validated = validate_capture_artifact(artifact, expected_action=expected_action)
        if (artifact_receipt.get("solver_tag") != artifact.get("solver_tag") or
                artifact_receipt.get("study_tag") != artifact.get("study_tag")):
            raise CaptureError("Java public response solver/study receipt differs from the raw artifact")
        if arguments.get("study_tag") is not None and arguments.get("study_tag") != artifact.get("study_tag"):
            raise CaptureError("Java result study tag differs from the exact public request")
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
    if action in {"history_capture_v2", "solution_snapshot_v2"} and (
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
