#!/usr/bin/env python3
"""Prepare and explicitly execute bounded W21 public-MCP runs.

The default metadata-only mode builds one task-owned HeatTransfer fixture and
captures field-identity metadata without solving. The separately frozen
solve-readback mode performs one public Study solve and captures actual
dataset/FieldArray output. Neither mode establishes native admission or
physical validation.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import textwrap
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4


SCHEMA = "W21_FIELD_IDENTITY_MCP_RUN_V2"
SOLVE_READBACK_SCHEMA = "W21_SOLVE_READBACK_MCP_RUN_V1"
INITIAL_STAGE_SCHEMA = "W21_INITIAL_STAGE_MCP_RUN_V1"
METADATA_MODE = "metadata-only"
SOLVE_READBACK_MODE = "solve-readback"
INITIAL_STAGE_MODE = "initial-stage"
SOLVE_READBACK_KIND = "W21_PUBLIC_SOLVE_READBACK_CAPTURE_ONLY"
INITIAL_STAGE_KIND = "W21_PUBLIC_INITIAL_STAGE_EXECUTION"
SINGLE_FIELD_PROFILE = "single-field"
AUTO_UNIT_CONTROLS_PROFILE = "auto-unit-controls"
INITIAL_STAGE_PROFILE = "thermal-initial-v2"
FIELD_READBACK_PROFILES = (SINGLE_FIELD_PROFILE, AUTO_UNIT_CONTROLS_PROFILE)
AUTO_UNIT_CONTROL_EXPRESSIONS = ("T", "T/1[K]", "1")
AUTO_UNIT_CONTROL_EXPECTED_UNITS = {"T": "K", "T/1[K]": "1", "1": "1"}
AUTO_UNIT_CONTROL_ABS_TOL = 1e-10
AUTO_UNIT_CONTROL_REL_TOL = 1e-12
RUN_BUDGET_S = 900
RPC_WAIT_S = 45
CLEANUP_RESERVE_S = 60
REPOSITORY = Path(__file__).resolve().parents[1]
FIXTURE = REPOSITORY / "tools/java/W21Fixture.java"
PROBE = REPOSITORY / "tools/java/W21FieldIdentityProbe.java"
PROCESS_HELPER = REPOSITORY / "tools/run_function_evaluate_probe.py"
REQUIRED_TOOLS = ("operation_call", "operation_describe", "model_create")
LOGICAL_OPERATIONS = (
    "project.create", "session.start", "session.connect", "model.inspect",
    "artifact.register", "code.execute_java", "session.disconnect", "session.stop",
    "job.list", "job.status", "job.wait",
)
SOLVE_READBACK_LOGICAL_OPERATIONS = (
    "dataset.solution_indices", "result.evaluate",
)
SOLVE_READBACK_TOOLS = (*REQUIRED_TOOLS, "run_study")
STAGE_READ_ACTION_COUNTERS = {
    "study.solve": ("study_dispatch", "solver_dispatch"),
    "solution_indices": ("solution_tuple_reads",),
    "result.evaluate": ("field_reads",),
}
INITIAL_STAGE_ACTION_COUNTERS = {
    "stage_define": ("stage_define_dispatch",),
    "stage_run": ("stage_run_dispatch", "underlying_solve_dispatch", "study_dispatch", "solver_dispatch"),
    "stage_run_wait": ("stage_run_wait_calls",),
}
W21_FIELD_READBACK_MAX_NUMERIC_SCALARS = 65_536
W21_FIELD_READBACK_MAX_JSON_BYTES = 8 * 1024 * 1024
W21_FIELD_IDENTITY_PROBE_MAX_JSON_UTF8_BYTES = 65_536
INITIAL_STAGE_OUTPUT_INTERNAL_READ_ACTIONS_MAX = 12
INITIAL_STAGE_ARTIFACT_ID_LENGTH = 36
WINDOWS_MAX_PATH_UNITS_WITH_NUL = 260
INITIAL_STAGE_ACCEPTANCE_SCOPE = "INITIAL_OUTPUT_AND_SOURCE_ARTIFACT_ONLY"
INITIAL_STAGE_ID = "thermal_initial"
INITIAL_STAGE_PLAN_ID = "w21_initial_stage"
INITIAL_STAGE_LOGICAL_OPERATIONS = (
    "experiment.stage_define", "experiment.stage_run",
)


class RunnerError(RuntimeError):
    """A frozen-input, public-route, identity, or bounded-run refusal."""


def _safe_runner_error_causes(exc: BaseException, *, limit: int = 3) -> list[dict[str, str]]:
    """Expose only known runner-owned leaf failures from wrapped task groups."""
    found: list[dict[str, str]] = []

    def visit(error: BaseException) -> None:
        if len(found) >= limit:
            return
        if isinstance(error, ModuleNotFoundError):
            module = error.name if isinstance(error.name, str) else ""
            if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module) is None:
                module = "unknown"
            found.append({"type": "ModuleNotFoundError", "module": module})
            return
        if isinstance(error, RunnerError):
            message = " ".join(str(error).split())[:500]
            message = re.sub(
                r"(?i)(?:[a-z]:[\\/]|/)(?:[^\\/\s\"']+[\\/])*[^\\/\s\"']*",
                "<path>", message,
            )
            found.append({"type": "RunnerError", "message": message})
            return
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                visit(nested)
                if len(found) >= limit:
                    return

    visit(exc)
    return found


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> str:
    """Write one durable receipt without replacing a pre-existing file."""
    if path.exists() or path.is_symlink():
        raise RunnerError(f"refusing to replace evidence: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False,
                     allow_nan=False).encode("utf-8") + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return hashlib.sha256(raw).hexdigest()


def _source_files(root: Path) -> list[Path]:
    package = root / "comsol_mcp"
    if not package.is_dir():
        raise RunnerError("source checkout does not contain comsol_mcp")
    files = set(package.rglob("*.py"))
    files.update((root / "comsol_mcp/data/g2").glob("*.json"))
    files.add(root / "comsol_mcp/worker_java/PersistentComsolWorker.java")
    files.update(root / relative for relative in (
        "tools/run_w21_stage_native.py", "tools/java/W21Fixture.java",
        "tools/java/W21FieldIdentityProbe.java", "tools/run_function_evaluate_probe.py",
        "docs/comsol_mcp_design_v1/02_ACTION_CATALOG.json",
    ))
    # exFAT may expose AppleDouble resource-fork sidecars next to source files.
    # They are filesystem metadata, never part of the frozen Python/catalog
    # closure; requested primary inputs above remain exact-path checked.
    files = {path for path in files if not path.name.startswith("._")}
    checked: list[Path] = []
    for path in sorted(files):
        if not path.is_file() or path.is_symlink():
            raise RunnerError(f"frozen source file is missing or aliased: {path.name}")
        try:
            path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except ValueError as exc:
            raise RunnerError("frozen source closure escaped the checkout") from exc
        checked.append(path)
    return checked


def source_manifest(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in _source_files(root)}


def _bind_candidate_source(root: Path) -> Path:
    """Put the explicit candidate first and reject already-loaded foreign code."""
    resolved = Path(root).resolve(strict=True)
    package_init = resolved / "comsol_mcp" / "__init__.py"
    if not package_init.is_file():
        raise RunnerError("selected source root has no regular comsol_mcp package")
    for name, module in tuple(sys.modules.items()):
        if name != "comsol_mcp" and not name.startswith("comsol_mcp."):
            continue
        origin = getattr(module, "__file__", None)
        if not isinstance(origin, str):
            raise RunnerError("a loaded comsol_mcp module has no verifiable source origin")
        try:
            Path(origin).resolve(strict=False).relative_to(resolved)
        except (OSError, ValueError) as exc:
            raise RunnerError("a loaded comsol_mcp module came from outside the selected source root") from exc
    root_text = str(resolved)
    if sys.path and sys.path[0] == root_text:
        return resolved
    sys.path.insert(0, root_text)
    return resolved


def _assert_frozen_source(plan: Mapping[str, Any], source_root: Path) -> Path:
    """Validate the frozen path and complete source closure before importing it."""
    try:
        declared = Path(plan.get("source_root", source_root)).resolve(strict=True)
        selected = Path(source_root).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise RunnerError("frozen source root is unavailable") from exc
    if declared != selected:
        raise RunnerError("source root differs from the frozen candidate")
    current = source_manifest(selected)
    if (current != plan.get("source_manifest")
            or sha256_value(current) != plan.get("source_manifest_sha256")):
        raise RunnerError("frozen source manifest drifted; no MCP call was made")
    return _bind_candidate_source(selected)


def _python_identity() -> dict[str, Any]:
    executable = Path(sys.executable).resolve(strict=True)
    inventory: list[tuple[str, str]] = []
    try:
        for distribution in importlib.metadata.distributions():
            name = distribution.metadata.get("Name")
            if isinstance(name, str) and name.strip():
                normalized = name.strip().casefold().replace("_", "-")
                inventory.append((normalized, distribution.version))
    except Exception as exc:
        raise RunnerError(f"Python dependency inventory is unreadable: {type(exc).__name__}") from None
    inventory.sort()
    mcp_spec = importlib.util.find_spec("mcp")
    if mcp_spec is None or not isinstance(mcp_spec.origin, str):
        raise RunnerError("selected Python environment cannot resolve the MCP client package")
    return {
        "executable_sha256": sha256_file(executable),
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "mcp_distribution_version": importlib.metadata.version("mcp"),
        "mcp_import_origin": str(Path(mcp_spec.origin).resolve(strict=True)),
        "distribution_count": len(inventory),
        "distribution_inventory_sha256": sha256_value(inventory),
    }


def _mode_schema(mode: str) -> str:
    if mode == METADATA_MODE:
        return SCHEMA
    if mode == SOLVE_READBACK_MODE:
        return SOLVE_READBACK_SCHEMA
    if mode == INITIAL_STAGE_MODE:
        return INITIAL_STAGE_SCHEMA
    raise RunnerError("mode must be exactly metadata-only, solve-readback, or initial-stage")


def _mode_kind(mode: str) -> str:
    return ("W21_FIELD_IDENTITY_METADATA_PROBE" if mode == METADATA_MODE
            else SOLVE_READBACK_KIND if mode == SOLVE_READBACK_MODE
            else INITIAL_STAGE_KIND if mode == INITIAL_STAGE_MODE
            else _mode_schema(mode))


def _mode_tools(mode: str) -> tuple[str, ...]:
    return SOLVE_READBACK_TOOLS if mode == SOLVE_READBACK_MODE else REQUIRED_TOOLS


def _project_policy_permissions(mode: str) -> list[str]:
    """Grant only the project capabilities used by the frozen runner mode."""
    permissions = ["inspect", "project_write", "trusted_code", "host_control"]
    if mode == METADATA_MODE:
        return permissions
    if mode == SOLVE_READBACK_MODE:
        return [*permissions, "compute"]
    if mode == INITIAL_STAGE_MODE:
        return [*permissions, "compute"]
    raise RunnerError("mode must be exactly metadata-only, solve-readback, or initial-stage")


def _mode_operations(mode: str) -> tuple[str, ...]:
    if mode == SOLVE_READBACK_MODE:
        return LOGICAL_OPERATIONS + SOLVE_READBACK_LOGICAL_OPERATIONS
    if mode == INITIAL_STAGE_MODE:
        return LOGICAL_OPERATIONS + INITIAL_STAGE_LOGICAL_OPERATIONS
    return LOGICAL_OPERATIONS


def _mode_budgets(mode: str) -> dict[str, Any]:
    common = {
        "server_births_max": 1, "worker_births_max": 1,
        "seconds_from_session_start_dispatch": RUN_BUDGET_S,
        "ordinary_rpc_wait_seconds": RPC_WAIT_S,
        "project_create_wait_calls_max": 1,
        "project_create_wait_timeout_seconds": 30,
        "geometry_run": 1, "mesh_run": 1,
    }
    if mode == METADATA_MODE:
        return {**common, "study_dispatch": 0, "solver_dispatch": 0}
    if mode == SOLVE_READBACK_MODE:
        return {
            **common, "study_dispatch": 1, "solver_dispatch": 1,
            "solution_tuple_reads": 1, "field_reads": 1,
            "queue_timeout_seconds": 30, "execution_timeout_seconds": 240,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        }
    if mode == INITIAL_STAGE_MODE:
        return {
            **common, "study_dispatch": 1, "solver_dispatch": 1,
            "stage_define_dispatch": 1, "stage_run_dispatch": 1,
            "stage_run_wait_calls_max": 1,
            "stage_run_queue_timeout_seconds": 30,
            "stage_run_execution_timeout_seconds": 240,
            "stage_run_rpc_wait_seconds": RPC_WAIT_S,
            "stage_run_result_wait_seconds": 240,
            "stage_output_internal_read_actions_max": INITIAL_STAGE_OUTPUT_INTERNAL_READ_ACTIONS_MAX,
            "cleanup_reserve_seconds": CLEANUP_RESERVE_S,
        }
    _mode_schema(mode)
    raise AssertionError("unreachable")


def _normalize_field_readback_profile(mode: str, profile: str | None) -> str | None:
    if mode == METADATA_MODE:
        if profile is not None:
            raise RunnerError("field-readback profiles are available only in solve-readback mode")
        return None
    if mode == INITIAL_STAGE_MODE:
        if profile is not None:
            raise RunnerError("field-readback profiles are available only in solve-readback mode")
        return None
    if mode != SOLVE_READBACK_MODE:
        _mode_schema(mode)
    selected = SINGLE_FIELD_PROFILE if profile is None else profile
    if selected not in FIELD_READBACK_PROFILES:
        raise RunnerError("field-readback profile is not a supported frozen solve-readback profile")
    return selected


def _frozen_field_readback_profile(plan: Mapping[str, Any]) -> str | None:
    mode = plan.get("mode", METADATA_MODE)
    profile = plan.get("field_readback_profile")
    if mode == SOLVE_READBACK_MODE and "field_readback_profile" not in plan:
        raise RunnerError("frozen solve-readback plan omits its field-readback profile")
    return _normalize_field_readback_profile(mode, profile)


def _field_readback_expressions(profile: str) -> tuple[str, ...]:
    if profile == AUTO_UNIT_CONTROLS_PROFILE:
        return AUTO_UNIT_CONTROL_EXPRESSIONS
    if profile == SINGLE_FIELD_PROFILE:
        return ("T",)
    raise RunnerError("field-readback profile is not supported")


def _initial_stage_definition() -> dict[str, Any]:
    """Return the one frozen v2 declaration used by initial-stage mode."""
    return {
        "version": 2,
        "plan_id": INITIAL_STAGE_PLAN_ID,
        "stages": [{
            "stage_id": INITIAL_STAGE_ID,
            "ordinal": 1,
            "depends_on": [],
            "study_target": {"segments": [{"collection": "study", "tag": "std1"}]},
            "source_selection": {"kind": "initial_state", "strategy": "declared_initial"},
            "target_selection": {
                "dataset": "dset1", "outer": "last", "inner": "last",
            },
            "mapping_profile": "same_name_same_mesh_initialization",
            "variable_mappings": [{
                "source_variable": "T", "target_variable": "T",
                "source_unit": "K", "target_unit": "K", "mapping_method": "identity",
            }],
            "reference_state": {"strategy": "initial_state"},
            "checks": [],
        }],
    }


def _initial_stage_operations() -> list[dict[str, Any]]:
    definition = _initial_stage_definition()
    rows = [
        ("experiment.stage_define", {"definition": definition}),
        ("experiment.stage_run", {"stage_id": INITIAL_STAGE_ID}),
    ]
    return [{
        "tool": "operation_call", "operation_id": operation,
        "arguments": arguments,
        "arguments_sha256": sha256_value({"operation_id": operation, "arguments": arguments}),
    } for operation, arguments in rows]


def _stage_output_windows_path_budget(project_workspace: Path) -> dict[str, Any]:
    """Freeze a conservative UTF-16 MAX_PATH check for output and AtomicSave temp names."""
    placeholder = "0" * INITIAL_STAGE_ARTIFACT_ID_LENGTH
    target = project_workspace / "stage_outputs" / f"{placeholder}.mph"
    temporary = target.parent / f".{target.name}.{'0' * 32}.tmp.mph"
    target_units = len(str(target).encode("utf-16-le")) // 2 + 1
    temporary_units = len(str(temporary).encode("utf-16-le")) // 2 + 1
    if max(target_units, temporary_units) > WINDOWS_MAX_PATH_UNITS_WITH_NUL:
        raise RunnerError("frozen stage artifact or AtomicSave temporary path exceeds Windows MAX_PATH")
    return {
        "relative_target_template": "stage_outputs/<36-character-attempt-id>.mph",
        "relative_atomic_save_temporary_template": "stage_outputs/.<36-character-attempt-id>.mph.<32-hex>.tmp.mph",
        "target_utf16_units_including_nul": target_units,
        "atomic_save_temporary_utf16_units_including_nul": temporary_units,
        "max_utf16_units_including_nul": WINDOWS_MAX_PATH_UNITS_WITH_NUL,
    }


def _validate_frozen_initial_stage(plan: Mapping[str, Any]) -> None:
    if plan.get("mode") != INITIAL_STAGE_MODE:
        return
    expected_definition = _initial_stage_definition()
    expected_operations = _initial_stage_operations()
    if (plan.get("initial_stage_profile") != INITIAL_STAGE_PROFILE
            or plan.get("initial_stage_definition") != expected_definition
            or plan.get("initial_stage_definition_sha256") != sha256_value(expected_definition)
            or plan.get("initial_stage_operations") != expected_operations
            or plan.get("acceptance_scope") != INITIAL_STAGE_ACCEPTANCE_SCOPE
            or plan.get("budgets", {}).get("study_dispatch") != 1
            or plan.get("budgets", {}).get("solver_dispatch") != 1
            or plan.get("budgets", {}).get("stage_define_dispatch") != 1
            or plan.get("budgets", {}).get("stage_run_dispatch") != 1
            or plan.get("budgets", {}).get("stage_run_wait_calls_max") != 1):
        raise RunnerError("frozen initial-stage plan differs from the exact one-stage contract")
    expected_path_budget = _stage_output_windows_path_budget(Path(plan["project_workspace"]))
    if plan.get("stage_output_windows_path_budget") != expected_path_budget:
        raise RunnerError("frozen initial-stage artifact/temporary path budget differs from current paths")


def _auto_unit_control_evidence(field_readbacks: Mapping[str, Any], *,
                                selected_tuple: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the three frozen unit/numeric controls from complete tuple arrays."""
    if set(field_readbacks) != set(AUTO_UNIT_CONTROL_EXPRESSIONS):
        raise RunnerError("auto-unit-controls receipt has missing or unexpected expression fields")
    if (set(selected_tuple) != {"outer", "inner", "solnum"}
            or any(type(selected_tuple.get(axis)) is not int or selected_tuple[axis] < 1
                   for axis in ("outer", "inner", "solnum"))):
        raise RunnerError("auto-unit-controls selected solution tuple is malformed")

    def real_imag(value: Any) -> tuple[float, float]:
        if isinstance(value, Mapping):
            if set(value) != {"real", "imag"}:
                raise RunnerError("auto-unit-controls scalar complex payload is malformed")
            real_value, imaginary_value = value["real"], value["imag"]
        else:
            real_value, imaginary_value = value, 0.0
        if (type(real_value) not in (int, float)
                or type(imaginary_value) not in (int, float)):
            raise RunnerError("auto-unit-controls receipt contains invalid numeric values")
        try:
            real_value = float(real_value)
            imaginary_value = float(imaginary_value)
        except (OverflowError, TypeError, ValueError):
            raise RunnerError("auto-unit-controls receipt contains invalid numeric values") from None
        if not math.isfinite(real_value) or not math.isfinite(imaginary_value):
            raise RunnerError("auto-unit-controls receipt contains non-finite numeric values")
        return real_value, imaginary_value

    def iter_scalars(value: Any):
        if isinstance(value, list):
            for item in value:
                yield from iter_scalars(item)
        else:
            yield value

    units: dict[str, Any] = {}
    vectors: dict[str, list[float]] = {}
    imaginary_vectors: dict[str, list[float]] = {}
    common_axes: tuple[list[Any], list[Any]] | None = None
    common_shape: list[int] | None = None
    for expression in AUTO_UNIT_CONTROL_EXPRESSIONS:
        row = field_readbacks.get(expression)
        if not isinstance(row, Mapping):
            raise RunnerError("auto-unit-controls receipt has a malformed expression readback")
        unit = row.get("unit")
        units[expression] = unit
        values = row.get("selected_tuple_values")
        if not isinstance(values, list) or not values:
            raise RunnerError("auto-unit-controls receipt lacks complete selected-tuple values")
        vector: list[float] = []
        imaginary_vector: list[float] = []
        for value in values:
            real_value, imaginary_value = real_imag(value)
            vector.append(real_value)
            imaginary_vector.append(imaginary_value)
        if row.get("selected_tuple_values_sha256") != sha256_value(values):
            raise RunnerError("auto-unit-controls selected-tuple values hash does not match")
        full_values = row.get("full_dataset_values")
        shape = row.get("shape")
        outer_indices = row.get("outer_indices")
        inner_indices = row.get("inner_indices")
        if (not isinstance(full_values, list) or not isinstance(shape, list)
                or not isinstance(outer_indices, list) or not isinstance(inner_indices, list)
                or tuple(shape) != _nested_shape(full_values)
                or len(shape) != 3 or any(type(size) is not int or size < 1 for size in shape)
                or len(outer_indices) != shape[0] or len(inner_indices) != shape[1]
                or any(type(index) is not int or index < 1 for index in outer_indices)
                or any(type(index) is not int or index < 1 for index in inner_indices)
                or len(outer_indices) != len(set(outer_indices))
                or len(inner_indices) != len(set(inner_indices))
                or type(selected_tuple.get("outer")) is not int
                or type(selected_tuple.get("inner")) is not int
                or selected_tuple["outer"] not in outer_indices
                or selected_tuple["inner"] not in inner_indices
                or row.get("tuple") != dict(selected_tuple)
                or row.get("full_dataset_values_sha256") != sha256_value(full_values)):
            raise RunnerError("auto-unit-controls receipt lacks complete shape/tuple-bound field values")
        _numeric_payload_count(full_values, finite=True)
        for scalar in iter_scalars(full_values):
            real_imag(scalar)
        selected_from_full = full_values[outer_indices.index(selected_tuple["outer"])][
            inner_indices.index(selected_tuple["inner"])
        ]
        if selected_from_full != values:
            raise RunnerError("auto-unit-controls selected values do not match the exact tuple in full data")
        axes = (outer_indices, inner_indices)
        if common_axes is None:
            common_axes, common_shape = axes, shape
        elif common_axes != axes or common_shape != shape:
            raise RunnerError("auto-unit-controls expressions do not share the same dataset axes/shape")
        vectors[expression] = vector
        imaginary_vectors[expression] = imaginary_vector

    unit_missing = False
    unit_mismatch = False
    for expression, expected in AUTO_UNIT_CONTROL_EXPECTED_UNITS.items():
        actual = units[expression]
        if not isinstance(actual, str) or not actual.strip():
            unit_missing = True
        elif actual != expected:
            unit_mismatch = True
    unit_status = "FAIL" if unit_mismatch else "UNVERIFIED" if unit_missing else "PASS"

    temperatures = vectors["T"]
    normalized = vectors["T/1[K]"]
    ones = vectors["1"]
    if len(temperatures) != len(normalized) or not ones:
        raise RunnerError("auto-unit-controls selected tuple arrays have inconsistent point counts")
    differences = [abs(left - right) for left, right in zip(temperatures, normalized)]
    equality_limits = [AUTO_UNIT_CONTROL_ABS_TOL + AUTO_UNIT_CONTROL_REL_TOL * max(abs(left), abs(right))
                       for left, right in zip(temperatures, normalized)]
    ones_errors = [abs(value - 1.0) for value in ones]
    ones_limits = [AUTO_UNIT_CONTROL_ABS_TOL + AUTO_UNIT_CONTROL_REL_TOL * max(abs(value), 1.0)
                   for value in ones]
    equality_passed = all(math.isfinite(error) and error <= limit
                          for error, limit in zip(differences, equality_limits))
    ones_passed = all(math.isfinite(error) and error <= limit
                      for error, limit in zip(ones_errors, ones_limits))
    numeric_status = "PASS" if equality_passed and ones_passed else "FAIL"
    imaginary_values = [value for expression_values in imaginary_vectors.values()
                        for value in expression_values]
    imaginary_passed = all(value == 0.0 for value in imaginary_values)
    if not imaginary_passed:
        numeric_status = "FAIL"
    if numeric_status == "FAIL" or unit_status == "FAIL":
        status = "FAIL"
    elif unit_status == "UNVERIFIED":
        status = "UNVERIFIED"
    else:
        status = "PASS"
    return {
        "profile": AUTO_UNIT_CONTROLS_PROFILE,
        "status": status,
        "selected_tuple": dict(selected_tuple),
        "expected_units": dict(AUTO_UNIT_CONTROL_EXPECTED_UNITS),
        "observed_units": units,
        "unit_status": unit_status,
        "numeric_status": numeric_status,
        "numeric_checks": {
            "T_over_1K_equals_T": {
                "status": "PASS" if equality_passed else "FAIL",
                "absolute_tolerance": AUTO_UNIT_CONTROL_ABS_TOL,
                "relative_tolerance": AUTO_UNIT_CONTROL_REL_TOL,
                "max_absolute_difference": (max(differences)
                                            if all(math.isfinite(value) for value in differences)
                                            else None),
                "max_allowed_difference": max(equality_limits),
                "point_count": len(differences),
            },
            "one_is_one": {
                "status": "PASS" if ones_passed else "FAIL",
                "absolute_tolerance": AUTO_UNIT_CONTROL_ABS_TOL,
                "relative_tolerance": AUTO_UNIT_CONTROL_REL_TOL,
                "max_absolute_difference": max(ones_errors),
                "max_allowed_difference": max(ones_limits),
                "point_count": len(ones_errors),
            },
            "all_imaginary_components_zero": {
                "status": "PASS" if imaginary_passed else "FAIL",
                "max_absolute_imaginary": max(abs(value) for value in imaginary_values),
                "point_count": len(imaginary_values),
            },
        },
    }


def _published_tool_schemas(root: Path, names: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Read the schemas from the actual registered FastMCP tool registry."""
    root_text = str(root.resolve(strict=True))
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    import comsol_mcp.mcp_server as server  # import registers tools; starts no service

    schemas = {}
    tools = server.mcp._tool_manager._tools
    for name in names or REQUIRED_TOOLS:
        item = tools.get(name)
        if item is None or not isinstance(item.parameters, dict):
            raise RunnerError(f"required public MCP tool is not registered: {name}")
        schemas[name] = item.parameters
    return schemas


def _logical_schemas(root: Path, operation_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
    root_text = str(root.resolve(strict=True))
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from comsol_mcp import _g2_registry

    result: dict[str, Any] = {}
    for operation in operation_ids or LOGICAL_OPERATIONS:
        entry = _g2_registry.BY_ID.get(operation)
        if entry is None or not isinstance(entry.input_schema, Mapping):
            raise RunnerError(f"logical operation schema is unavailable: {operation}")
        result[operation] = dict(entry.input_schema)
    return result


def _jdk_identity(home: Path) -> dict[str, Any]:
    home = home.resolve(strict=True)
    release = home / "release"
    java = home / "bin/java.exe"
    javac = home / "bin/javac.exe"
    if not all(path.is_file() for path in (release, java, javac)):
        raise RunnerError("selected Windows JDK must contain release, bin/java.exe, and bin/javac.exe")
    release_text = release.read_text(encoding="utf-8", errors="replace")
    version = next((line.split("=", 1)[1].strip('"') for line in release_text.splitlines()
                    if line.startswith("JAVA_VERSION=")), None)
    return {
        "home": str(home), "java_sha256": sha256_file(java),
        "javac_sha256": sha256_file(javac), "release_sha256": sha256_file(release),
        "java_version": version,
    }


def _comsol_identity(root: Path, version: str) -> dict[str, Any]:
    from comsol_mcp._runtime_installation import inspect_installation, runtime_id_for_root

    runtime_id = runtime_id_for_root(root)
    observed = inspect_installation(runtime_id, system="Windows")["installation"]
    version_value = observed.get("version", {}).get("value")
    build_value = observed.get("build", {}).get("value")
    if not isinstance(version_value, str) or not version_value.startswith(version):
        raise RunnerError(f"installed COMSOL version does not match selected {version}")
    executable = Path(root).resolve(strict=True) / "bin/win64/comsolmphserver.exe"
    if not executable.is_file():
        raise RunnerError("selected COMSOL installation lacks bin/win64/comsolmphserver.exe")
    return {
        "runtime_id": runtime_id, "root": str(Path(root).resolve(strict=True)),
        "version": version_value, "build": str(build_value) if build_value is not None else None,
        "server_executable_sha256": sha256_file(executable),
    }


def _comsol_numeric_version(value: Any, *, label: str, build: int | None = None) -> tuple[int, int, int]:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value) is None:
        raise RunnerError(f"{label} is not an unambiguous numeric COMSOL version")
    parts = [int(part) for part in value.split(".")]
    if len(parts) == 4:
        if build is None or parts[-1] != build:
            raise RunnerError(f"{label} four-part version does not bind its build")
        parts.pop()
    if any(part < 0 for part in parts) or parts[0] < 1 or parts[1] < 0:
        raise RunnerError(f"{label} contains an invalid version component")
    if len(parts) == 2:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _comsol_build_number(value: Any, *, label: str) -> int:
    if type(value) is int:
        number = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value) is not None:
        number = int(value)
    else:
        raise RunnerError(f"{label} is missing or malformed")
    if number <= 0:
        raise RunnerError(f"{label} is missing or malformed")
    return number


def _resolve_remote_comsol_identity(
        connected: Mapping[str, Any], selected: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly resolve remote version/build evidence against the frozen install."""
    selected_build = _comsol_build_number(selected.get("build"), label="frozen COMSOL build")
    selected_version = _comsol_numeric_version(
        selected.get("version"), label="frozen COMSOL version", build=selected_build,
    )

    raw_version = connected.get("remote_engine_version")
    raw_build = connected.get("remote_engine_build")
    raw_build_source = connected.get("remote_engine_build_source")
    inline = re.fullmatch(
        r"COMSOL Multiphysics (?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?) "
        r"\((?:Build: (?P<english_build>[0-9]+)|开发版本: (?P<localized_build>[0-9]+))\)",
        raw_version if isinstance(raw_version, str) else "",
    )
    if inline is not None:
        remote_version_text = inline.group("version")
        inline_build = _comsol_build_number(
            inline.group("english_build") or inline.group("localized_build"),
            label="remote version-text build",
        )
        remote_version = _comsol_numeric_version(remote_version_text, label="remote COMSOL version")
        build_source = "remote_version_text"
        if raw_build is not None:
            explicit_build = _comsol_build_number(raw_build, label="remote COMSOL build")
            if explicit_build != inline_build or raw_build_source == "NOT_REPORTED":
                raise RunnerError("remote version-text and explicit build evidence conflict")
    else:
        if not isinstance(raw_version, str) or re.fullmatch(
                r"[0-9]+(?:\.[0-9]+){1,3}", raw_version) is None:
            raise RunnerError("remote COMSOL version text is unrecognized or ambiguous")
        if raw_build is None or raw_build_source == "NOT_REPORTED":
            raise RunnerError("remote COMSOL build has no independent reported value")
        explicit_build = _comsol_build_number(raw_build, label="remote COMSOL build")
        remote_version = _comsol_numeric_version(
            raw_version, label="remote COMSOL version", build=explicit_build,
        )
        inline_build = explicit_build
        build_source = "remote_explicit_build"

    if remote_version != selected_version or inline_build != selected_build:
        raise RunnerError("remote COMSOL version/build differs from the frozen Server/runtime")
    if (raw_build_source is not None
            and (not isinstance(raw_build_source, str) or not raw_build_source.strip())):
        raise RunnerError("remote COMSOL build source is malformed")
    return {
        "normalized_version": ".".join(str(part) for part in remote_version),
        "normalized_build": str(inline_build),
        "version_source": "remote_version_text",
        "build_source": build_source,
        "remote_engine_version_raw": raw_version,
        "remote_engine_build_raw": raw_build,
        "remote_engine_build_source_raw": raw_build_source,
    }


def _check_64_receipt(path: Path, *, mode: str = METADATA_MODE,
                      field_readback_profile: str | None = None,
                      expected_source_manifest_sha256: str | None = None) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        label = ("solve-readback" if mode == SOLVE_READBACK_MODE else
                 "initial-stage" if mode == INITIAL_STAGE_MODE else "metadata-only probe")
        raise RunnerError(f"6.3 preparation requires a real 6.4 {label} receipt")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        label = ("solve-readback" if mode == SOLVE_READBACK_MODE else
                 "initial-stage" if mode == INITIAL_STAGE_MODE else "metadata-only probe")
        raise RunnerError(f"6.3 preparation requires a complete actual 6.4 {label} receipt")
    if mode == METADATA_MODE:
        selected_comsol = value.get("selected_comsol")
        cleanup = value.get("cleanup")
        if (value.get("schema") != SCHEMA
                or value.get("status") != "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION"
                or not isinstance(selected_comsol, Mapping)
                or not str(selected_comsol.get("version", "")).startswith("6.4")
                or not isinstance(cleanup, Mapping)
                or cleanup.get("status") != "CLEANUP_COMPLETE"):
            raise RunnerError("6.3 preparation requires a cleaned-up 6.4 metadata-only probe receipt")
        return {"path": str(path.resolve()), "sha256": sha256_file(path)}
    if mode == INITIAL_STAGE_MODE:
        selected_comsol = value.get("selected_comsol")
        acceptance = value.get("initial_stage_acceptance")
        cleanup = value.get("cleanup")
        if (value.get("schema") != INITIAL_STAGE_SCHEMA
                or value.get("kind") != INITIAL_STAGE_KIND
                or value.get("mode") != INITIAL_STAGE_MODE
                or value.get("status") != "INITIAL_STAGE_OUTPUT_ACCEPTED_WITHIN_SCOPE"
                or value.get("acceptance_scope") != INITIAL_STAGE_ACCEPTANCE_SCOPE
                or value.get("initial_stage_profile") != INITIAL_STAGE_PROFILE
                or not isinstance(selected_comsol, Mapping)
                or not str(selected_comsol.get("version", "")).startswith("6.4")
                or str(selected_comsol.get("build", selected_comsol.get("build_number", ""))) != "293"
                or value.get("source_manifest_sha256") != expected_source_manifest_sha256
                or not isinstance(acceptance, Mapping)
                or acceptance.get("status") != "PASS"
                or acceptance.get("scope") != INITIAL_STAGE_ACCEPTANCE_SCOPE
                or not isinstance(cleanup, Mapping)
                or cleanup.get("status") != "CLEANUP_COMPLETE"
                or cleanup.get("worker_retired") is not True
                or cleanup.get("owned_server_stopped") is not True):
            raise RunnerError("6.3 initial-stage requires a successful same-profile 6.4 source-bound receipt")
        receipt_hash = sha256_file(path)
        state_path = path.parent / "state.json"
        if state_path.is_symlink() or not state_path.is_file():
            raise RunnerError("6.4 initial-stage receipt lacks its durable matching state")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (not isinstance(state, Mapping)
                or state.get("schema") != INITIAL_STAGE_SCHEMA
                or state.get("status") != value.get("status")
                or state.get("receipt_path") != str(path.resolve())
                or state.get("receipt_sha256") != receipt_hash
                or state.get("source_manifest_sha256") != expected_source_manifest_sha256
                or state.get("initial_stage_acceptance_sha256") != sha256_value(acceptance)):
            raise RunnerError("6.4 initial-stage receipt does not match its durable completion state")
        revalidated_acceptance = _revalidate_initial_stage_receipt(
            path, value, state,
            expected_source_manifest_sha256=expected_source_manifest_sha256,
        )
        if revalidated_acceptance != acceptance:
            raise RunnerError("6.4 initial-stage acceptance changed during strict evidence revalidation")
        expected_counters = {
            "stage_define_dispatch": 1, "stage_define_dispatch_possible": 1,
            "stage_define_dispatch_status": "CONFIRMED",
            "stage_run_dispatch": 1, "stage_run_dispatch_possible": 1,
            "stage_run_dispatch_status": "CONFIRMED",
            "underlying_solve_dispatch": 1, "underlying_solve_dispatch_possible": 1,
            "underlying_solve_dispatch_status": "CONFIRMED",
            "study_dispatch": 1, "study_dispatch_possible": 1,
            "study_dispatch_status": "CONFIRMED",
            "solver_dispatch": 1, "solver_dispatch_possible": 1,
            "solver_dispatch_status": "CONFIRMED",
        }
        if any(state.get(key) != expected for key, expected in expected_counters.items()):
            raise RunnerError("6.4 initial-stage state lacks exact one-stage/one-solve dispatch accounting")
        if (value.get("study_dispatch") != 1 or value.get("solver_dispatch") != 1
                or value.get("stage_define_dispatch") != 1
                or value.get("stage_run_dispatch") != 1
                or value.get("underlying_solve_dispatch") != 1
                or value.get("native_admission") != "UNVERIFIED"
                or value.get("physical_validation") != "NOT_RUN"
                or value.get("state_transfer") != "NOT_RUN"
                or value.get("continuity") != "NOT_RUN"
                or value.get("conservation") != "NOT_RUN"
                or value.get("scientific_validation") != "NOT_RUN"):
            raise RunnerError("6.4 initial-stage receipt claims an unsupported scientific or transfer scope")
        isolation_path = path.parent / "owned_server_isolation.json"
        if isolation_path.is_symlink() or not isolation_path.is_file():
            raise RunnerError("6.4 initial-stage receipt lacks the owned-server cleanup record")
        isolation = json.loads(isolation_path.read_text(encoding="utf-8"))
        if not isinstance(isolation, Mapping) or isolation.get("status") != "STOPPED":
            raise RunnerError("6.4 initial-stage owned-server record is not safely stopped")
        return {"path": str(path.resolve()), "sha256": receipt_hash,
                "mode": INITIAL_STAGE_MODE, "kind": INITIAL_STAGE_KIND,
                "initial_stage_profile": INITIAL_STAGE_PROFILE,
                "source_manifest_sha256": expected_source_manifest_sha256,
                "freeze_sha256": value.get("freeze_sha256"),
                "acceptance_sha256": sha256_value(revalidated_acceptance),
                "saved_artifact_sha256": revalidated_acceptance["saved_artifact"]["sha256"],
                "cleanup_status": "CLEANUP_COMPLETE"}
    if mode != SOLVE_READBACK_MODE:
        raise RunnerError("6.3 receipt mode is unsupported")
    requested_profile = _normalize_field_readback_profile(mode, field_readback_profile)
    solve_readback = value.get("solve_readback")
    tuple_readback = value.get("solution_tuple_readback")
    cleanup = value.get("cleanup")
    selected_comsol = value.get("selected_comsol")
    receipt_profile = value.get("field_readback_profile")
    if requested_profile == AUTO_UNIT_CONTROLS_PROFILE:
        if receipt_profile != AUTO_UNIT_CONTROLS_PROFILE:
            raise RunnerError("6.3 auto-unit-controls requires a matching 6.4 profile receipt")
    elif receipt_profile not in (None, SINGLE_FIELD_PROFILE):
        raise RunnerError("6.3 single-field profile cannot use a different 6.4 field-readback profile")
    if (value.get("schema") != SOLVE_READBACK_SCHEMA
            or value.get("kind") != SOLVE_READBACK_KIND
            or value.get("mode") != SOLVE_READBACK_MODE
            or value.get("status") != "SOLVE_READBACK_CAPTURED_ONLY_NOT_ADMISSION"
            or not isinstance(selected_comsol, Mapping)
            or not str(selected_comsol.get("version", "")).startswith("6.4")
            or value.get("study_dispatch") != 1 or value.get("solver_dispatch") != 1
            or not isinstance(solve_readback, Mapping) or solve_readback.get("status") != "CAPTURED"
            or not isinstance(solve_readback.get("field_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", solve_readback["field_sha256"])
            or not isinstance(tuple_readback, Mapping) or tuple_readback.get("status") != "VERIFIED"
            or not isinstance(cleanup, Mapping) or cleanup.get("status") != "CLEANUP_COMPLETE"
            or cleanup.get("worker_retired") is not True
            or cleanup.get("owned_server_stopped") is not True):
        raise RunnerError("6.3 preparation requires a complete actual 6.4 solve-readback receipt")
    receipt_hash = sha256_file(path)
    state_path = path.parent / "state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise RunnerError("6.4 solve-readback receipt lacks its durable matching state")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, Mapping):
        raise RunnerError("6.4 solve-readback receipt does not match its durable completion state")
    if (state.get("schema") != SOLVE_READBACK_SCHEMA
            or state.get("status") != value.get("status")
            or state.get("receipt_path") != str(path.resolve())
            or state.get("receipt_sha256") != receipt_hash):
        raise RunnerError("6.4 solve-readback receipt does not match its durable completion state")
    if requested_profile == AUTO_UNIT_CONTROLS_PROFILE:
        controls = solve_readback.get("unit_controls")
        field_readbacks = solve_readback.get("field_readbacks")
        if (not isinstance(controls, Mapping)
                or not isinstance(field_readbacks, Mapping)
                or controls.get("profile") != AUTO_UNIT_CONTROLS_PROFILE
                or solve_readback.get("field_readback_profile") != AUTO_UNIT_CONTROLS_PROFILE
                or not _valid_sha256(solve_readback.get("tuple_binding_sha256"))
                or set(field_readbacks) != set(AUTO_UNIT_CONTROL_EXPRESSIONS)
                or any(not isinstance(row, Mapping)
                       or row.get("expression") != expression
                       or row.get("dataset") != solve_readback.get("dataset")
                       or row.get("solution") != solve_readback.get("solution")
                       or row.get("tuple_binding_sha256") != solve_readback.get("tuple_binding_sha256")
                       or row.get("full_dataset_field_sha256") != solve_readback.get("field_sha256")
                       or row.get("field_operation") != solve_readback.get("field_operation")
                       for expression, row in field_readbacks.items())):
            raise RunnerError("6.4 receipt lacks auto-unit-controls evidence")
        selected_tuple = solve_readback.get("tuple")
        if not isinstance(selected_tuple, Mapping):
            raise RunnerError("6.4 auto-unit-controls receipt lacks its exact selected tuple")
        recomputed_controls = _auto_unit_control_evidence(
            field_readbacks, selected_tuple=selected_tuple,
        )
        if controls != recomputed_controls or controls.get("status") != "PASS":
            raise RunnerError("6.4 auto-unit-controls evidence did not pass its frozen unit/numeric controls")
    expected_accounting = {
        "study_dispatch": 1, "study_dispatch_possible": 1,
        "study_dispatch_status": "CONFIRMED",
        "solver_dispatch": 1, "solver_dispatch_possible": 1,
        "solver_dispatch_status": "CONFIRMED",
        "solution_tuple_reads": 1, "solution_tuple_reads_possible": 1,
        "solution_tuple_reads_status": "CONFIRMED",
        "field_reads": 1, "field_reads_possible": 1,
        "field_reads_status": "CONFIRMED",
    }
    if any(state.get(key) != expected for key, expected in expected_accounting.items()):
        raise RunnerError("6.4 solve-readback durable state lacks complete confirmed dispatch accounting")
    tuple_progress = state.get("solution_tuple_readback_progress")
    field_progress = state.get("field_readback_progress")
    if (not isinstance(tuple_progress, Mapping) or tuple_progress.get("status") != "VERIFIED"
            or tuple_progress.get("tuple") != tuple_readback.get("target_tuple")
            or not isinstance(field_progress, Mapping) or field_progress.get("status") != "VERIFIED"
            or field_progress.get("field_sha256") != solve_readback.get("field_sha256")):
        raise RunnerError("6.4 solve-readback durable state lacks its exact tuple/field evidence")
    if requested_profile == AUTO_UNIT_CONTROLS_PROFILE:
        controls = solve_readback["unit_controls"]
        if (field_progress.get("field_readback_profile") != AUTO_UNIT_CONTROLS_PROFILE
                or field_progress.get("unit_controls_sha256") != sha256_value(controls)):
            raise RunnerError("6.4 durable state lacks matching auto-unit-controls evidence")
    isolation_path = path.parent / "owned_server_isolation.json"
    if isolation_path.is_symlink() or not isolation_path.is_file():
        raise RunnerError("6.4 solve-readback receipt lacks the owned-server cleanup record")
    isolation = json.loads(isolation_path.read_text(encoding="utf-8"))
    if not isinstance(isolation, Mapping) or isolation.get("status") != "STOPPED":
        raise RunnerError("6.4 solve-readback owned-server record is not safely stopped")
    return {"path": str(path.resolve()), "sha256": receipt_hash,
            "mode": SOLVE_READBACK_MODE, "kind": SOLVE_READBACK_KIND,
            "field_readback_profile": receipt_profile,
            "cleanup_status": "CLEANUP_COMPLETE"}


def _task_owned_server_home_root(task_root: Path, requested: Path | None, *,
                                 create: bool) -> Path:
    """Resolve a real, non-aliased runtime-home root inside this frozen task root."""
    root = Path(task_root)
    if root.is_symlink():
        raise RunnerError("task root cannot be a symlink")
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise RunnerError("task root is unavailable") from exc
    if not root.is_dir():
        raise RunnerError("task root must be a real directory")
    raw = Path(requested) if requested is not None else root / "h"
    candidate = raw if raw.is_absolute() else root / raw
    candidate = Path(os.path.abspath(str(candidate)))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RunnerError("server-home root must remain inside the authorized task root") from exc
    if not relative.parts:
        raise RunnerError("server-home root must be a dedicated task subdirectory")

    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        junction_probe = getattr(cursor, "is_junction", None)
        if (cursor.is_symlink()
                or (callable(junction_probe) and junction_probe())):
            raise RunnerError("server-home root cannot contain symlink or junction components")
        if cursor.exists() and not cursor.is_dir():
            raise RunnerError("server-home root components must be directories")
        if cursor.exists():
            try:
                resolved_component = cursor.resolve(strict=True)
                resolved_text = os.path.normcase(os.path.abspath(str(resolved_component)))
                lexical_text = os.path.normcase(os.path.abspath(str(cursor)))
                resolved_component.relative_to(root)
            except (OSError, ValueError) as exc:
                raise RunnerError("server-home root contains an alias outside the authorized task root") from exc
            if resolved_text != lexical_text:
                raise RunnerError("server-home root contains a non-canonical alias or junction")
    if create:
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            junction_probe = getattr(cursor, "is_junction", None)
            if (cursor.is_symlink()
                    or (callable(junction_probe) and junction_probe())
                    or not cursor.is_dir()):
                raise RunnerError("server-home root changed or is not a real directory")
            try:
                resolved_component = cursor.resolve(strict=True)
                resolved_component.relative_to(root)
            except (OSError, ValueError) as exc:
                raise RunnerError("server-home root escaped the authorized task root") from exc
            if os.path.normcase(os.path.abspath(str(resolved_component))) != os.path.normcase(os.path.abspath(str(cursor))):
                raise RunnerError("server-home root changed to a non-canonical alias or junction")
    return candidate


def _server_home_budget(server_home: Path) -> dict[str, Any]:
    try:
        from comsol_mcp._session_server import windows_owned_server_path_budget
        from comsol_mcp._session_context import runtime_state_root
    except ModuleNotFoundError as exc:
        module = exc.name if isinstance(exc.name, str) else "unknown"
        if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module) is None:
            module = "unknown"
        raise RunnerError(f"Windows Server path preflight could not import module: {module}") from None

    try:
        # The real project/session IDs are assigned after public project.create.
        # The shared session_state_directory helper makes their hash exactly 64
        # hex characters, so any fixed labels produce the exact path length.
        state_root = runtime_state_root(
            Path(server_home) / "control-private", platform_name="Windows",
        )
        return windows_owned_server_path_budget(
            state_root, "w21-preflight-project", "w21-preflight-session",
        )
    except Exception as exc:
        if isinstance(exc, RunnerError):
            raise
        message = str(exc)
        if "budget exceeded" in message:
            raise RunnerError(message) from None
        raise RunnerError("Windows Server path preflight could not be completed") from None


def _validate_frozen_server_home(plan: Mapping[str, Any], *, require_absent: bool = True) -> dict[str, Any]:
    try:
        task_root = Path(plan["task_root"])
        home_root = Path(plan["server_home_root"])
        server_home_id = plan["server_home_id"]
        server_home = Path(plan["server_home"])
    except (KeyError, TypeError) as exc:
        raise RunnerError("frozen server-home identity is incomplete") from exc
    if not isinstance(server_home_id, str) or re.fullmatch(r"[0-9a-f]{16}", server_home_id) is None:
        raise RunnerError("frozen server-home unique id is malformed")
    resolved_root = _task_owned_server_home_root(task_root, home_root, create=False)
    expected = resolved_root / server_home_id
    if os.path.normcase(os.path.abspath(str(server_home))) != os.path.normcase(os.path.abspath(str(expected))):
        raise RunnerError("frozen server-home path does not match its task-owned root and unique id")
    run_root_text = plan.get("run_root")
    if isinstance(run_root_text, str):
        run_root = Path(os.path.abspath(run_root_text))
        if (run_root == resolved_root or run_root in resolved_root.parents
                or resolved_root in run_root.parents):
            raise RunnerError("server-home root must be separate from the per-run evidence directory")
    junction_probe = getattr(server_home, "is_junction", None)
    if (server_home.is_symlink()
            or (callable(junction_probe) and junction_probe())):
        raise RunnerError("frozen server-home directory cannot be a symlink or junction")
    if require_absent and server_home.exists():
        raise RunnerError("frozen server-home unique directory is no longer fresh")
    if not require_absent:
        if not server_home.is_dir():
            raise RunnerError("created server-home is not a real directory")
        try:
            resolved_home = server_home.resolve(strict=True)
            resolved_home.relative_to(task_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise RunnerError("created server-home escaped the authorized task root") from exc
        if os.path.normcase(os.path.abspath(str(resolved_home))) != os.path.normcase(os.path.abspath(str(server_home))):
            raise RunnerError("created server-home resolves through an alias or junction")
    budget = _server_home_budget(server_home)
    if budget != plan.get("server_home_path_budget"):
        raise RunnerError("frozen Windows server-home path budget does not match current paths")
    return budget


def prepare(*, version: str, comsol_root: Path, jdk_home: Path,
            evidence_root: Path, source_root: Path = REPOSITORY,
            prerequisite_64_receipt: Path | None = None,
            server_home_root: Path | None = None,
            mode: str = METADATA_MODE, probe_discovery_only: bool = False,
            probe_feature_info_tag: str | None = None,
            probe_table_id: str | None = None,
            field_readback_profile: str | None = None) -> dict[str, Any]:
    if version not in {"6.4", "6.3"}:
        raise RunnerError("selected version must be exactly 6.4 or 6.3")
    schema = _mode_schema(mode)
    selected_field_profile = _normalize_field_readback_profile(mode, field_readback_profile)
    probe_request = _normalize_probe_request(
        discovery_only=probe_discovery_only,
        feature_info_tag=probe_feature_info_tag,
        table_id=probe_table_id,
    )
    if platform.system() != "Windows":
        raise RunnerError("prepare is metadata-only but must run on the target Windows host")
    source_root = source_root.resolve(strict=True)
    _bind_candidate_source(source_root)
    manifest = source_manifest(source_root)
    if os.environ.get("COMSOL_MCP_TOOL_PROFILE", "full").casefold() != "full":
        raise RunnerError("COMSOL_MCP_TOOL_PROFILE must be full to freeze the published W21 routes")
    if Path(evidence_root).is_symlink():
        raise RunnerError("evidence root cannot be a symlink")
    evidence_root = evidence_root.resolve(strict=True)
    if not evidence_root.is_dir():
        raise RunnerError("evidence root must be a real directory")
    if evidence_root == source_root or source_root in evidence_root.parents:
        raise RunnerError("evidence root must be outside the source checkout")

    prerequisite = None
    if version == "6.3":
        if prerequisite_64_receipt is None:
            raise RunnerError("6.3 cannot be prepared before a successful cleaned-up 6.4 probe")
        prerequisite = _check_64_receipt(
            prerequisite_64_receipt, mode=mode,
            field_readback_profile=selected_field_profile,
            expected_source_manifest_sha256=sha256_value(manifest),
        )
    elif prerequisite_64_receipt is not None:
        raise RunnerError("6.4 is the first-version step and accepts no earlier receipt")

    comsol = _comsol_identity(comsol_root, version)
    jdk = _jdk_identity(jdk_home)
    tool_schemas = _published_tool_schemas(source_root, _mode_tools(mode))
    operation_schemas = _logical_schemas(source_root, _mode_operations(mode))
    python_identity = _python_identity()
    if source_manifest(source_root) != manifest:
        raise RunnerError("source manifest changed while prepare was freezing runtime schemas")

    run_id = f"w21-{version.replace('.', '')}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    run_root = evidence_root / version / run_id
    if run_root.exists() or run_root.is_symlink():
        raise RunnerError("run evidence path already exists")
    resolved_server_home_root = _task_owned_server_home_root(
        evidence_root, server_home_root, create=False,
    )
    if (run_root == resolved_server_home_root
            or run_root in resolved_server_home_root.parents
            or resolved_server_home_root in run_root.parents):
        raise RunnerError("server-home root must be separate from the per-run evidence directory")
    server_home_id = uuid4().hex[:16]
    server_home = resolved_server_home_root / server_home_id
    if server_home.exists() or server_home.is_symlink():
        raise RunnerError("new server-home unique directory already exists")
    server_home_path_budget = _server_home_budget(server_home)

    run_root.mkdir(parents=True, mode=0o700)
    resolved_server_home_root = _task_owned_server_home_root(
        evidence_root, resolved_server_home_root, create=True,
    )
    server_home = resolved_server_home_root / server_home_id
    if server_home.exists() or server_home.is_symlink():
        raise RunnerError("new server-home unique directory was claimed before freeze")
    workspace_root = run_root / "workspaces"
    workspace_root.mkdir(mode=0o700)
    project_workspace = workspace_root / "field-identity-probe"
    initial_stage_definition = _initial_stage_definition() if mode == INITIAL_STAGE_MODE else None
    initial_stage_operations = _initial_stage_operations() if mode == INITIAL_STAGE_MODE else None
    stage_output_windows_path_budget = (
        _stage_output_windows_path_budget(project_workspace) if mode == INITIAL_STAGE_MODE else None
    )

    request_names = (
        "project_create", "project_create_wait", "session_start", "session_connect", "model_create",
        "model_inspect_before_fixture", "fixture_register", "fixture_execute",
        "model_inspect_after_fixture", "probe_register", "probe_execute",
        "session_disconnect", "session_stop", "unknown_query",
        "failure_session_disconnect", "failure_session_stop",
    )
    idempotency_names = (
        "project_create", "session_start", "session_connect", "fixture_register",
        "fixture_execute", "probe_register", "probe_execute", "session_disconnect", "session_stop",
        "failure_session_disconnect", "failure_session_stop",
    )
    if mode == SOLVE_READBACK_MODE:
        request_names += ("study_solve", "solution_indices", "result_evaluate")
        idempotency_names += ("study_solve", "solution_indices", "result_evaluate")
    elif mode == INITIAL_STAGE_MODE:
        request_names += ("stage_define", "stage_run", "stage_run_wait")
        idempotency_names += ("stage_define", "stage_run")
    request_ids = {name: str(uuid4()) for name in request_names}
    idempotency = {name: str(uuid4()) for name in idempotency_names}
    plan: dict[str, Any] = {
        "schema": schema, "mode": mode, "kind": _mode_kind(mode),
        "run_id": run_id, "run_root": str(run_root.resolve()),
        "task_root": str(evidence_root),
        "server_home_root": str(resolved_server_home_root),
        "server_home_id": server_home_id,
        "server_home_path_budget": server_home_path_budget,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "requested_version": version, "selected_comsol": comsol, "selected_jdk": jdk,
        "python": python_identity, "source_root": str(source_root),
        "source_manifest": manifest, "source_manifest_sha256": sha256_value(manifest),
        "fixture_sha256": manifest["tools/java/W21Fixture.java"],
        "probe_sha256": manifest["tools/java/W21FieldIdentityProbe.java"],
        "process_preflight_sha256": manifest["tools/run_function_evaluate_probe.py"],
        "published_tool_schemas": tool_schemas,
        "published_tool_schemas_sha256": sha256_value(tool_schemas),
        "logical_operation_schemas": operation_schemas,
        "logical_operation_schemas_sha256": sha256_value(operation_schemas),
        "project_workspace": str(project_workspace),
        "stdio_home": str(run_root / "stdio-home"),
        "server_home": str(server_home),
        "isolation_receipt": str(run_root / "owned_server_isolation.json"),
        "scratch": str(run_root / "scratch"),
        "prerequisite_64_receipt": prerequisite,
        "request_ids": request_ids, "idempotency_keys": idempotency,
        "probe_request": probe_request,
        "field_readback_profile": selected_field_profile,
        "budgets": _mode_budgets(mode),
        **({
            "initial_stage_profile": INITIAL_STAGE_PROFILE,
            "initial_stage_definition": initial_stage_definition,
            "initial_stage_definition_sha256": sha256_value(initial_stage_definition),
            "initial_stage_operations": initial_stage_operations,
            "stage_output_windows_path_budget": stage_output_windows_path_budget,
        } if mode == INITIAL_STAGE_MODE else {}),
        "route": "public stdio MCP; ControlDaemon; OwnedServerLauncher; one registered session",
        "prepare_side_effects": "filesystem receipts/directories only; no MCP call or COMSOL process",
        "acceptance_scope": ("field identity metadata capture only; native admission remains UNVERIFIED"
                             if mode == METADATA_MODE else
                             "one public Study solve and actual dataset/FieldArray capture; native admission and physical validation remain UNVERIFIED"
                             if mode == SOLVE_READBACK_MODE else INITIAL_STAGE_ACCEPTANCE_SCOPE),
    }
    plan["freeze_sha256"] = sha256_value(plan)
    write_json_atomic(run_root / "freeze.json", plan)
    state = {
        "schema": schema, "run_id": run_id, "freeze_sha256": plan["freeze_sha256"],
        "status": "PREPARED", "action_history": [], "server_births_possible": 0,
        "worker_births_possible": 0, "study_dispatch": 0, "solver_dispatch": 0,
        "study_dispatch_possible": 0, "solver_dispatch_possible": 0,
        "study_dispatch_status": "NOT_DISPATCHED", "solver_dispatch_status": "NOT_DISPATCHED",
        "solution_tuple_reads": 0, "solution_tuple_reads_possible": 0,
        "solution_tuple_reads_status": "NOT_DISPATCHED",
        "field_reads": 0, "field_reads_possible": 0, "field_reads_status": "NOT_DISPATCHED",
        "stage_define_dispatch": 0, "stage_define_dispatch_possible": 0,
        "stage_define_dispatch_status": "NOT_DISPATCHED",
        "stage_run_dispatch": 0, "stage_run_dispatch_possible": 0,
        "stage_run_dispatch_status": "NOT_DISPATCHED",
        "stage_run_wait_calls": 0, "stage_run_wait_calls_possible": 0,
        "underlying_solve_dispatch": 0, "underlying_solve_dispatch_possible": 0,
        "underlying_solve_dispatch_status": "NOT_DISPATCHED",
        "job_ids": [], "unknown_action": None,
    }
    write_json_atomic(run_root / "state.json", state)
    (run_root / "scratch").mkdir(mode=0o700)
    return {"run_root": str(run_root), "freeze_sha256": plan["freeze_sha256"],
            "status": "PREPARED_ONLY", "plan": plan}


def verify_plan(plan: Mapping[str, Any], *, expected_sha256: str,
                source_root: Path = REPOSITORY) -> None:
    mode = plan.get("mode", METADATA_MODE)
    _frozen_field_readback_profile(plan)
    _validate_frozen_initial_stage(plan)
    if plan.get("schema") != _mode_schema(mode) or plan.get("kind") not in (None, _mode_kind(mode)):
        raise RunnerError("frozen mode/schema/kind identity is inconsistent")
    if plan.get("budgets") != _mode_budgets(mode):
        raise RunnerError("frozen mode budgets differ from the bounded runner contract")
    _frozen_probe_request(plan)
    candidate = dict(plan)
    observed_hash = candidate.pop("freeze_sha256", None)
    if observed_hash != expected_sha256 or sha256_value(candidate) != expected_sha256:
        raise RunnerError("freeze hash mismatch; execute requires the exact prepared candidate")
    selected_source_root = _assert_frozen_source(plan, source_root)
    _validate_frozen_server_home(plan)
    if _python_identity() != plan.get("python"):
        raise RunnerError("Python/interpreter identity differs from the frozen candidate")
    if _published_tool_schemas(source_root, _mode_tools(mode)) != plan.get("published_tool_schemas"):
        raise RunnerError("registered MCP tool schemas differ from the frozen candidate")
    if _logical_schemas(source_root, _mode_operations(mode)) != plan.get("logical_operation_schemas"):
        raise RunnerError("logical operation schemas differ from the frozen candidate")
    observed = _comsol_identity(Path(plan["selected_comsol"]["root"]), plan["requested_version"])
    if observed != plan.get("selected_comsol"):
        raise RunnerError("COMSOL installation/build identity differs from the frozen candidate")
    if _jdk_identity(Path(plan["selected_jdk"]["home"])) != plan.get("selected_jdk"):
        raise RunnerError("JDK identity differs from the frozen candidate")
    if source_manifest(selected_source_root) != plan.get("source_manifest"):
        raise RunnerError("frozen source manifest changed during candidate validation")


def validate_model_binding(response: Mapping[str, Any], *, project_id: str,
                           session_id: str, server_instance_id: str,
                           worker_epoch: int) -> dict[str, Any]:
    execution = response.get("execution")
    data = response.get("data")
    ref = execution.get("model_ref") if isinstance(execution, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(execution, Mapping) or not isinstance(ref, Mapping):
        raise RunnerError("public model_create omitted execution ModelRef identity")
    model_tag = data.get("model_tag")
    if (response.get("success") is not True
            or execution.get("project_id") != project_id
            or execution.get("session_id") != session_id
            or not isinstance(model_tag, str) or not model_tag
            or ref.get("model_tag") != model_tag
            or ref.get("session_id") != session_id
            or ref.get("server_instance_id") != server_instance_id
            or type(ref.get("generation")) is not int
            or ref.get("generation") <= 0):
        raise RunnerError("public model_create returned a foreign or incomplete project/session/server/Worker binding")
    revision = execution.get("revision")
    if type(revision) is not int or revision < 0:
        raise RunnerError("public model_create omitted a non-negative model revision")
    return {"project_id": project_id, "session_id": session_id,
            "server_instance_id": server_instance_id,
            "model_tag": model_tag, "worker_epoch": worker_epoch,
            "model_ref": dict(ref), "revision": revision}


def _validate_model_inspect(response: Mapping[str, Any], binding: Mapping[str, Any]) -> int:
    execution = response.get("execution")
    if (response.get("success") is not True or not isinstance(execution, Mapping)
            or execution.get("project_id") != binding["project_id"]
            or execution.get("session_id") != binding["session_id"]
            or execution.get("model_ref") != binding["model_ref"]
            or type(execution.get("revision")) is not int
            or execution["revision"] != binding.get("revision")):
        raise RunnerError("model.inspect did not read back the exact project/session/ModelRef/revision")
    return execution["revision"]


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_managed_result_ticket(response: Mapping[str, Any], binding: Mapping[str, Any], *,
                                    request_id: str, idempotency_key: str,
                                    expected_revision: int, label: str,
                                    allow_one_revision_advance: bool = False) -> Mapping[str, Any]:
    execution = response.get("execution")
    if (response.get("success") is not True or not isinstance(execution, Mapping)
            or execution.get("project_id") != binding["project_id"]
            or execution.get("session_id") != binding["session_id"]
            or execution.get("model_ref") != binding["model_ref"]
            or execution.get("request_id") != request_id
            or execution.get("idempotency_key") != idempotency_key
            or not isinstance(execution.get("operation_id"), str) or not execution["operation_id"]
            or not _valid_sha256(execution.get("request_hash"))
            or not isinstance(execution.get("job_id"), str) or not execution["job_id"]
            or type(execution.get("revision")) is not int):
        raise RunnerError(f"{label} omitted or changed its managed request/ticket/model binding")
    revision = execution["revision"]
    allowed = {expected_revision, expected_revision + 1} if allow_one_revision_advance else {expected_revision}
    if revision not in allowed:
        raise RunnerError(f"{label} returned a revision outside its exact bounded revision chain")
    return execution


def _numeric_payload_count(value: Any, *, finite: bool = False) -> int:
    if isinstance(value, bool):
        raise RunnerError("field readback contains a boolean instead of a numeric scalar")
    if isinstance(value, (int, float)):
        if finite:
            try:
                is_finite = math.isfinite(value)
            except (OverflowError, TypeError, ValueError):
                is_finite = False
            if not is_finite:
                raise RunnerError("field readback contains a nonfinite or overflowing numeric value")
        return 1
    if isinstance(value, Mapping):
        return sum(_numeric_payload_count(item, finite=finite) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_numeric_payload_count(item, finite=finite) for item in value)
    raise RunnerError("field readback contains a non-numeric value")


def _nested_shape(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    if not value:
        return (0,)
    child_shapes = [_nested_shape(item) for item in value]
    if any(shape != child_shapes[0] for shape in child_shapes[1:]):
        raise RunnerError("field readback array is ragged")
    return (len(value), *child_shapes[0])


def _resolve_solve_tuple(solution_data: Mapping[str, Any], *,
                         dataset: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve last/last only from a complete public SolutionInfo pair table."""
    from comsol_mcp._w21_native_output import _check_tuple

    try:
        return _check_tuple(
            {"dataset": dataset, "outer": "last", "inner": "last"}, solution_data,
        )
    except Exception as exc:
        raise RunnerError(f"dataset.solution_indices did not resolve one actual tuple: {type(exc).__name__}") from None


def _extract_solve_field(solution_data: Mapping[str, Any], field_data: Mapping[str, Any], *,
                         dataset: str,
                         resolved_tuple: tuple[dict[str, Any], dict[str, Any]] | None = None,
                         field_readback_profile: str = SINGLE_FIELD_PROFILE,
                         ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind complete returned expressions and the exact SolutionInfo tuple."""
    from comsol_mcp._g3_results import (
        W21_FIELD_READBACK_MAX_JSON_BYTES,
        W21_FIELD_READBACK_MAX_NUMERIC_SCALARS,
    )

    selected, binding = resolved_tuple or _resolve_solve_tuple(solution_data, dataset=dataset)
    expected_expressions = _field_readback_expressions(field_readback_profile)

    field = field_data.get("field_array")
    status_witness = field_data.get("status")
    if (not isinstance(status_witness, Mapping) or status_witness.get("ok") is not True
            or field_data.get("dataset") != dataset
            or field_data.get("solution") != selected["solution"]
            or field_data.get("storage") != "inline"
            or not isinstance(field, Mapping)):
        raise RunnerError("result.evaluate did not return the expected successful inline FieldArray")
    result_budget = field_data.get("result_budget")
    if (not isinstance(result_budget, Mapping) or result_budget.get("status") != "PASS"
            or result_budget.get("publish_allowed") is not True
            or not isinstance(result_budget.get("records"), list)
            or not result_budget["records"]
            or any(not isinstance(row, Mapping) or row.get("status") != "PASS"
                   or row.get("allowed") is not True or row.get("publish_allowed") is not True
                   for row in result_budget["records"])):
        raise RunnerError("result.evaluate budget witness does not prove a complete, untruncated payload")
    if field.get("is_complex") is not False:
        raise RunnerError("W21 T readback is not a real-valued FieldArray")
    axes = field.get("axes")
    shape = field.get("shape")
    values = field.get("values")
    coords = field.get("coords")
    metadata = field.get("metadata")
    if (axes != ["expression", "outer", "inner", "point"]
            or not isinstance(values, list) or not isinstance(coords, Mapping)
            or not isinstance(metadata, Mapping) or metadata.get("pair_mapping_complete") is not True
            or not isinstance(metadata.get("solution_pairs"), list)
            or not isinstance(shape, list) or tuple(shape) != _nested_shape(values)
            or len(shape) != 4
            or any(type(size) is not int or size < 1 for size in shape)
            or shape[0] != len(expected_expressions)):
        raise RunnerError("result.evaluate FieldArray axes, shape, or native solution-pair metadata are incomplete")
    response_expressions = field_data.get("expressions")
    coordinate_expressions = coords.get("expression")
    if (not isinstance(response_expressions, list)
            or not isinstance(coordinate_expressions, list)
            or any(not isinstance(name, str) or not name for name in response_expressions)
            or any(not isinstance(name, str) or not name for name in coordinate_expressions)
            or len(response_expressions) != len(set(response_expressions))
            or len(coordinate_expressions) != len(set(coordinate_expressions))
            or set(response_expressions) != set(expected_expressions)
            or set(coordinate_expressions) != set(expected_expressions)
            or response_expressions != coordinate_expressions
            or not isinstance(coords.get("outer"), list)
            or coords["outer"] != binding.get("outer_indices")
            or not isinstance(coords.get("inner"), list)
            or coords["inner"] != binding.get("inner_indices")
            or len(coords["outer"]) != shape[1] or len(coords["inner"]) != shape[2]
            or selected["outer"] not in coords["outer"]
            or selected["inner"] not in coords["inner"]):
        raise RunnerError("result.evaluate FieldArray coordinates do not contain the exact resolved tuple")
    point_coordinates = coords.get("point")
    if (not isinstance(point_coordinates, list)
            or len(point_coordinates) != shape[3]
            or point_coordinates != list(range(1, shape[3] + 1))):
        raise RunnerError("result.evaluate FieldArray point coordinate axis is incomplete")

    actual_pairs = []
    for pair in metadata["solution_pairs"]:
        if not isinstance(pair, Mapping):
            raise RunnerError("result.evaluate FieldArray contains a malformed solution-pair witness")
        actual = {key: pair.get(key) for key in ("outer", "inner", "solnum")}
        if any(type(actual[key]) is not int or actual[key] < 1 for key in actual):
            raise RunnerError("result.evaluate FieldArray contains an invalid solution-pair witness")
        actual_pairs.append(actual)
    solution_pairs = [{key: row[key] for key in ("outer", "inner", "solnum")}
                      for row in binding["solnum_pairs"]]
    if (len(actual_pairs) != len(solution_pairs)
            or actual_pairs != solution_pairs
            or actual_pairs.count({"outer": selected["outer"], "inner": selected["inner"],
                                   "solnum": selected["solnum"]}) != 1):
        raise RunnerError("result.evaluate FieldArray pair mapping differs from dataset.solution_indices")
    outer_offset = coords["outer"].index(selected["outer"])
    inner_offset = coords["inner"].index(selected["inner"])
    expression_offsets = {name: index for index, name in enumerate(coordinate_expressions)}
    selected_fields = {
        expression: values[expression_offsets[expression]][outer_offset][inner_offset]
        for expression in expected_expressions
    }
    if any(not isinstance(row, list) or len(row) != shape[3]
           for row in selected_fields.values()):
        raise RunnerError("resolved solution tuple has incomplete expression field values")
    selected_field = selected_fields["T"]
    if not selected_field:
        raise RunnerError("resolved solution tuple has no nonempty field values")
    count = _numeric_payload_count(values, finite=True)
    spatial_coordinates = coords.get("spatial")
    if spatial_coordinates is not None:
        if (not isinstance(spatial_coordinates, list)
                or len(spatial_coordinates) != shape[3]
                or any(not isinstance(point, list) or not point for point in spatial_coordinates)):
            raise RunnerError("result.evaluate spatial coordinates do not match its point axis")
        coordinate_dimensions = len(spatial_coordinates[0])
        if any(len(point) != coordinate_dimensions for point in spatial_coordinates):
            raise RunnerError("result.evaluate spatial coordinate rows are incomplete")
        count += _numeric_payload_count(spatial_coordinates, finite=True)
    if count < 1 or count > W21_FIELD_READBACK_MAX_NUMERIC_SCALARS:
        raise RunnerError("result.evaluate numeric field/coordinate payload exceeds the W21 output cap")
    try:
        response_bytes = len(canonical_bytes(dict(field_data)))
    except (TypeError, ValueError, OverflowError):
        raise RunnerError("result.evaluate payload is not finite canonical JSON") from None
    if response_bytes > W21_FIELD_READBACK_MAX_JSON_BYTES:
        raise RunnerError("result.evaluate response exceeds the W21 8 MiB output cap")
    if not isinstance(field_data.get("observation_ref"), Mapping):
        raise RunnerError("result.evaluate omitted its persisted observation reference")
    observation_ref = field_data["observation_ref"]
    if (set(observation_ref) != {"observation_id", "sha256"}
            or not isinstance(observation_ref.get("observation_id"), str)
            or not observation_ref["observation_id"]
            or not _valid_sha256(observation_ref.get("sha256"))):
        raise RunnerError("result.evaluate persisted observation reference is incomplete")
    selected_output = {
        "dataset": dataset, "solution": selected["solution"],
        "tuple": {key: selected[key] for key in ("outer", "inner", "solnum")},
        "tuple_source": binding["selection_resolution"]["source"],
        "tuple_binding_sha256": sha256_value(binding),
        "field_array_axes": list(axes), "field_array_shape": list(shape),
        "field_values": selected_field,
        "field_values_sha256": sha256_value(selected_field),
        "field_units": field.get("units"),
        "field_metadata": field.get("metadata"),
        "actual_spatial_coordinates": spatial_coordinates,
        "observation_ref": dict(observation_ref),
        "full_dataset_field_sha256": sha256_value(field),
        "numeric_scalar_count": count,
        "response_json_bytes": response_bytes,
        "unit_acceptance": "UNVERIFIED_CONFIGURED_OR_MODEL_DEPENDENT",
        "coordinate_frame_acceptance": "UNVERIFIED",
        "mesh_intrinsic_identity": "UNVERIFIED",
        "read_scope": "complete returned dataset FieldArray; target tuple extracted by exact native pair witness",
    }
    if field_readback_profile == AUTO_UNIT_CONTROLS_PROFILE:
        unit_table = field.get("units")
        expression_units = (unit_table.get("expression")
                            if isinstance(unit_table, Mapping) else None)
        field_readbacks: dict[str, Any] = {}
        for expression in expected_expressions:
            full_values = values[expression_offsets[expression]]
            selected_values = selected_fields[expression]
            unit = expression_units.get(expression) if isinstance(expression_units, Mapping) else None
            field_readbacks[expression] = {
                "expression": expression,
                "dataset": dataset,
                "solution": selected["solution"],
                "shape": list(shape[1:]),
                "outer_indices": list(coords["outer"]),
                "inner_indices": list(coords["inner"]),
                "tuple": {key: selected[key] for key in ("outer", "inner", "solnum")},
                "tuple_binding_sha256": sha256_value(binding),
                "unit": unit if isinstance(unit, str) else None,
                "full_dataset_values": full_values,
                "full_dataset_values_sha256": sha256_value(full_values),
                "full_dataset_field_sha256": sha256_value(field),
                "selected_tuple_values": selected_values,
                "selected_tuple_values_sha256": sha256_value(selected_values),
            }
        controls = _auto_unit_control_evidence(
            field_readbacks,
            selected_tuple={key: selected[key] for key in ("outer", "inner", "solnum")},
        )
        selected_output["field_readback_profile"] = field_readback_profile
        selected_output["field_expressions"] = list(expected_expressions)
        selected_output["field_readbacks"] = field_readbacks
        selected_output["unit_controls"] = controls
    return selected_output, binding


def _initial_stage_worker_outcome_unknown(response: Mapping[str, Any]) -> bool:
    """Keep an ostensibly successful stage ticket UNKNOWN on uncertain Worker evidence.

    This is deliberately scoped to the initial-stage acceptance envelope.  It
    protects the original solve/save job from cleanup when its own Worker log
    records an unknown/unresponsive reply or an inconsistent response binding.
    """
    data = response.get("data")
    acceptance = data.get("initial_output_acceptance") if isinstance(data, Mapping) else None
    if not isinstance(acceptance, Mapping):
        return False
    evidence = acceptance.get("native_evidence")
    rows = evidence.get("worker_event_rows") if isinstance(evidence, Mapping) else None
    if not isinstance(rows, list) or (not rows and acceptance.get("status") == "PASS"):
        # A positive initial-output claim without its durable Worker ledger is
        # ambiguous about whether solve/save reached the engine.  Preserve the
        # original stage_run ticket for read-only reconciliation.
        return acceptance.get("status") == "PASS"
    execution = response.get("execution")
    job_id = execution.get("job_id") if isinstance(execution, Mapping) else None
    dispatches = evidence.get("solve_save_dispatches") if isinstance(evidence, Mapping) else None
    if acceptance.get("status") == "PASS" and (
            not isinstance(job_id, str) or not job_id
            or not isinstance(dispatches, list) or len(dispatches) != 2
            or any(not isinstance(dispatch, Mapping) for dispatch in dispatches)
            or any(not isinstance(dispatch.get("operation"), str)
                   for dispatch in dispatches if isinstance(dispatch, Mapping))
            or sorted(dispatch.get("operation") for dispatch in dispatches
                      if isinstance(dispatch, Mapping)) != ["run_study", "save_model"]):
        return True
    by_request: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("event") != "worker_request":
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, Mapping):
            return True
        phase = metadata.get("phase")
        kind = metadata.get("kind")
        if isinstance(phase, str) and phase.lower() in {"unknown", "unresponsive"}:
            return True
        if phase not in {"submitted", "observed"} or not isinstance(kind, str) or not kind:
            return True
        request_id = metadata.get("request_id")
        request_hash = metadata.get("request_hash")
        if (not isinstance(request_id, str) or not request_id
                or not _valid_sha256(request_hash)
                or (isinstance(job_id, str) and row.get("job_id") != job_id)):
            return True
        phases = by_request.setdefault(request_id, {})
        if phase in phases:
            return True
        phases[phase] = metadata
        if (not isinstance(metadata.get("metadata"), Mapping)
                or metadata["metadata"].get("request_id") != request_id):
            return True
        if phase == "observed":
            reply = metadata.get("reply")
            reply_status = reply.get("status") if isinstance(reply, Mapping) else None
            well_formed_success = (reply_status == "SUCCEEDED"
                                   and reply.get("ok") is True
                                   and metadata.get("status") == "SUCCEEDED")
            well_formed_failure = (reply_status == "FAILED"
                                   and reply.get("ok") is False
                                   and isinstance(reply.get("failure"), Mapping)
                                   and metadata.get("status") == "FAILED")
            if (not isinstance(reply, Mapping)
                    or reply.get("request_id") != request_id
                    or not (well_formed_success or well_formed_failure)):
                return True
    for request_id, phases in by_request.items():
        submitted = phases.get("submitted")
        observed = phases.get("observed")
        if set(phases) != {"submitted", "observed"}:
            return True
        if submitted is not None and observed is not None:
            submitted_call = submitted.get("metadata")
            observed_call = observed.get("metadata")
            reply = observed.get("reply")
            if (submitted.get("request_hash") != observed.get("request_hash")
                    or submitted.get("operation_id") != observed.get("operation_id")
                    or submitted.get("kind") != observed.get("kind")
                    or submitted.get("metadata") != observed.get("metadata")
                    or not isinstance(submitted_call, Mapping)
                    or not isinstance(reply, Mapping)
                    or (type(submitted_call.get("generation")) is int
                        and reply.get("generation") != submitted_call.get("generation"))):
                return True
    if isinstance(dispatches, list):
        for dispatch in dispatches:
            if not isinstance(dispatch, Mapping) or dispatch.get("operation") not in {"run_study", "save_model"}:
                continue
            request_id = dispatch.get("worker_request_id")
            phases = by_request.get(request_id) if isinstance(request_id, str) else None
            if (not isinstance(phases, Mapping)
                    or not isinstance(phases.get("submitted"), Mapping)
                    or not isinstance(phases.get("observed"), Mapping)
                    or phases["submitted"].get("request_hash") != dispatch.get("worker_request_hash")
                    or phases["observed"].get("request_hash") != dispatch.get("worker_request_hash")):
                return True
            submitted_call = phases["submitted"].get("metadata")
            reply = phases["observed"].get("reply")
            method = "run" if dispatch.get("operation") == "run_study" else "save"
            if (not isinstance(submitted_call, Mapping)
                    or phases["submitted"].get("kind") != "call"
                    or phases["submitted"].get("operation_id") != execution.get("operation_id")
                    or submitted_call.get("method") != method
                    or submitted_call.get("handle") != dispatch.get("worker_receiver")
                    or submitted_call.get("generation") != dispatch.get("worker_generation")
                    or submitted_call.get("args") != dispatch.get("worker_args")
                    or not isinstance(reply, Mapping)
                    or reply.get("generation") != dispatch.get("worker_generation")):
                return True
    return False


def _initial_stage_wait_expired(response: Mapping[str, Any]) -> bool:
    """Recognize ControlDaemon's successful-but-still-pending job.wait result."""
    data = response.get("data")
    return bool(response.get("success") is True and isinstance(data, Mapping)
                and data.get("wait_expired") is True)


def _is_unknown(response: Mapping[str, Any]) -> bool:
    data = response.get("data")
    error = response.get("error")
    status = data.get("status") if isinstance(data, Mapping) else None
    cleanup = data.get("cleanup") if isinstance(data, Mapping) else None
    return bool(_initial_stage_worker_outcome_unknown(response)
                or response.get("execution_state_unknown") is True
                or (isinstance(data, Mapping) and (
                    data.get("execution_state_unknown") is True
                    or data.get("cleanup_failed") is True
                    or data.get("status") == "UNKNOWN"
                    or (isinstance(status, Mapping) and (
                        status.get("execution_state_unknown") is True
                        or status.get("cleanup_failed") is True))
                    or (isinstance(cleanup, Mapping) and cleanup.get("cleanup_failed") is True)))
                or (isinstance(error, Mapping) and (
                    error.get("execution_state_unknown") is True
                    or error.get("code") == "EXECUTION_STATE_UNKNOWN")))


def _public_payload(result: Any) -> dict[str, Any]:
    """Extract only the structured MCP business envelope; text is fallback."""
    if isinstance(result, Mapping):
        if isinstance(result.get("structuredContent"), Mapping):
            payload = dict(result["structuredContent"])
        else:
            payload = dict(result)
        if result.get("isError") is True:
            payload.setdefault("success", False)
        return payload
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        payload = dict(structured)
        if getattr(result, "isError", False):
            payload.setdefault("success", False)
        return payload
    for block in getattr(result, "content", ()) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
    raise RunnerError("stdio MCP returned no structured business envelope")


def _response_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    execution = payload.get("execution") if isinstance(payload.get("execution"), Mapping) else {}
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    # Keep just identity/status fields needed for audit and recovery; never log
    # full process command lines, environment, tokens, source text, or stderr.
    return {
        "success": payload.get("success"), "error_code": error.get("code"),
        "execution_state_unknown": _is_unknown(payload),
        "project_id": data.get("project_id") or execution.get("project_id"),
        "session_id": data.get("session_id") or execution.get("session_id"),
        "job_id": data.get("job_id") or execution.get("job_id") or payload.get("job_id"),
        "status": data.get("status") or data.get("state"),
    }


def _initial_stage_job_identity(job: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the durable stage_run identity from ControlDaemon job rows.

    The daemon's public ``_pending`` envelope omits project_id from ``data``;
    the durable job row carries project/request identity under its operation
    metadata and a job.status response may carry that same row one level down.
    This parser accepts those documented shapes without treating a job id or
    status flag alone as proof of attribution.
    """
    operation_record = job.get("operation")
    job_metadata = job.get("metadata")
    if not isinstance(operation_record, Mapping):
        return None
    operation_metadata = operation_record.get("metadata")
    operation_metadata = operation_metadata if isinstance(operation_metadata, Mapping) else {}
    job_metadata = job_metadata if isinstance(job_metadata, Mapping) else {}
    execution = operation_metadata.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    job_execution = job_metadata.get("execution")
    job_execution = job_execution if isinstance(job_execution, Mapping) else {}
    arguments = operation_metadata.get("arguments")
    arguments = arguments if isinstance(arguments, Mapping) else {}
    outer_operation = operation_record.get("operation")
    if outer_operation in {"operation_call", "registry_call"}:
        nested_operation = arguments.get("operation_id")
        nested_arguments = arguments.get("arguments")
        business_operation = nested_operation if isinstance(nested_operation, str) else None
        arguments = nested_arguments if isinstance(nested_arguments, Mapping) else {}
    else:
        business_operation = operation_metadata.get("operation", outer_operation)

    def consistent_text(field: str, containers: tuple[Mapping[str, Any], ...]) -> str | None:
        values = [container[field] for container in containers if field in container]
        if not values or any(not isinstance(value, str) or not value for value in values):
            return None
        return values[0] if all(value == values[0] for value in values) else None

    request_id = consistent_text("request_id", (job, job_metadata, job_execution,
                                                   operation_record, operation_metadata, execution))
    idempotency_key = consistent_text("idempotency_key", (job, job_metadata, job_execution,
                                                            operation_record, operation_metadata, execution))
    project_values = [container["project_id"] for container in (
        job, job_metadata, job_execution, operation_record, operation_metadata, execution, arguments,
    ) if "project_id" in container]
    if (not isinstance(business_operation, str) or not isinstance(request_id, str)
            or not isinstance(idempotency_key, str) or not project_values
            or any(not isinstance(value, str) or not value for value in project_values)
            or len(set(project_values)) != 1):
        return None
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return None
    return {
        "job_id": job_id, "status": job.get("status"),
        "request_id": request_id, "idempotency_key": idempotency_key,
        "project_id": project_values[0], "operation": business_operation,
        "operation_id": operation_record.get("operation_id"),
    }


def _normalize_probe_request(*, discovery_only: bool = False,
                             feature_info_tag: str | None = None,
                             table_id: str | None = None) -> dict[str, Any]:
    if type(discovery_only) is not bool:
        raise RunnerError("probe discovery flag must be boolean")
    has_tag = feature_info_tag is not None
    has_table = table_id is not None
    if discovery_only and (has_tag or has_table):
        raise RunnerError("probe discovery and targeted table selection are mutually exclusive")
    if has_tag != has_table:
        raise RunnerError("probe FeatureInfo tag and table id must be supplied together")
    if discovery_only:
        return {"mode": "discovery"}
    if not has_tag:
        return {"mode": "full"}
    if (not isinstance(feature_info_tag, str) or not feature_info_tag
            or len(feature_info_tag) > 512):
        raise RunnerError("probe FeatureInfo tag must be non-empty and within the frozen text limit")
    if not isinstance(table_id, str) or table_id not in {"Shape", "Expression"}:
        raise RunnerError("probe table id must be Shape or Expression")
    return {"mode": "targeted", "feature_info_tag": feature_info_tag, "table_id": table_id}


def _frozen_probe_request(plan: Mapping[str, Any]) -> dict[str, Any]:
    request = plan.get("probe_request", {"mode": "full"})
    if not isinstance(request, Mapping):
        raise RunnerError("frozen probe request is malformed")
    mode = request.get("mode")
    if mode == "full" and set(request) == {"mode"}:
        return {"mode": "full"}
    if mode == "discovery" and set(request) == {"mode"}:
        return {"mode": "discovery"}
    if mode == "targeted" and set(request) == {"mode", "feature_info_tag", "table_id"}:
        normalized = _normalize_probe_request(
            feature_info_tag=request.get("feature_info_tag"), table_id=request.get("table_id"))
        if normalized.get("mode") != "targeted":
            raise RunnerError("frozen targeted probe request requires a non-empty tag and table")
        return normalized
    raise RunnerError("frozen probe request does not match an explicit bounded selection")


def _worker_probe_args(request: Mapping[str, Any]) -> dict[str, Any]:
    mode = request["mode"]
    if mode == "full":
        return {}
    if mode == "discovery":
        return {"discovery_only": True}
    return {"feature_info_tag": request["feature_info_tag"], "table_id": request["table_id"]}


def _parse_field_identity_probe(raw_payload: Any, *, expected_model_tag: str,
                                expected_request: Mapping[str, Any] | None = None,
                                allow_table_row_limit: bool = False) -> dict[str, Any]:
    """Preserve the bounded probe JSON without interpreting it as admission evidence."""
    if isinstance(raw_payload, str):
        raw_json = raw_payload
        raw_bytes = raw_json.encode("utf-8")
        if len(raw_bytes) > W21_FIELD_IDENTITY_PROBE_MAX_JSON_UTF8_BYTES:
            raise RunnerError("field identity probe payload exceeds its frozen JSON byte limit")
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise RunnerError("field identity probe returned malformed JSON") from exc
    elif isinstance(raw_payload, Mapping):
        try:
            raw_bytes = canonical_bytes(raw_payload)
        except (TypeError, ValueError) as exc:
            raise RunnerError("field identity probe returned non-JSON data") from exc
        if len(raw_bytes) > W21_FIELD_IDENTITY_PROBE_MAX_JSON_UTF8_BYTES:
            raise RunnerError("field identity probe payload exceeds its frozen JSON byte limit")
        raw_json = raw_bytes.decode("utf-8")
        payload = dict(raw_payload)
    else:
        raise RunnerError("field identity probe returned no structured payload")

    if not isinstance(payload, Mapping):
        raise RunnerError("field identity probe JSON root is not an object")
    identity = payload.get("identity")
    request = _frozen_probe_request({
        "probe_request": expected_request if expected_request is not None else {"mode": "full"}
    })
    request_mode = request["mode"]
    identity_matches = isinstance(identity, Mapping) and identity.get("model_tag") == expected_model_tag
    complete_capture = (
        request_mode == "full"
        and payload.get("probe") == "W21FieldIdentityProbe"
        and payload.get("status") == "STRUCTURE_CAPTURED_ONLY"
        and payload.get("native_admission") == "UNVERIFIED"
        and identity_matches
    )
    old_limited_capture = (
        request_mode == "full" and allow_table_row_limit
        and set(payload) == {"probe", "status", "code", "native_admission", "payload_complete"}
        and payload.get("probe") == "W21FieldIdentityProbe"
        and payload.get("status") == "OUTPUT_LIMIT_EXCEEDED"
        and payload.get("code") == "TABLE_ROW_LIMIT_EXCEEDED"
        and payload.get("native_admission") == "UNVERIFIED"
        and payload.get("payload_complete") is False
    )
    location = payload.get("limit_location")
    detailed_limited_capture = (
        allow_table_row_limit and request_mode == "full"
        and _valid_table_limit_payload(payload, location, expected_tag=None, expected_table=None)
    )
    targeted_limit_capture = (
        request_mode == "targeted"
        and _valid_table_limit_payload(payload, location,
            expected_tag=request["feature_info_tag"], expected_table=request["table_id"])
    )
    discovery_capture = (
        request_mode == "discovery"
        and payload.get("probe") == "W21FieldIdentityProbe"
        and payload.get("status") == "DISCOVERY_ONLY_CAPTURED"
        and payload.get("native_admission") == "UNVERIFIED"
        and payload.get("payload_complete") is False
        and payload.get("metadata_complete") is False
        and payload.get("capture_scope") == "PHYSICS_FIELDS_AND_FEATURE_INFO_TAGS_ONLY"
        and payload.get("feature_info_tables") == "NOT_READ"
        and payload.get("base_unit_system") == "NOT_READ"
        and identity_matches
        and identity.get("physics_tag") == "ht"
        and _valid_feature_info_tags(payload.get("feature_info_tags"))
        and "feature_info" not in payload
    )
    targeted_capture = (
        request_mode == "targeted"
        and payload.get("probe") == "W21FieldIdentityProbe"
        and payload.get("status") == "TARGETED_TABLE_CAPTURED_ONLY"
        and payload.get("native_admission") == "UNVERIFIED"
        and payload.get("payload_complete") is False
        and payload.get("metadata_complete") is False
        and payload.get("capture_scope") == "PHYSICS_FIELDS_AND_ONE_FEATURE_INFO_TABLE_ONLY"
        and payload.get("unselected_feature_info_tables") == "NOT_READ"
        and payload.get("base_unit_system") == "NOT_READ"
        and identity_matches
        and identity.get("physics_tag") == "ht"
        and _valid_selected_table(payload.get("feature_info"), request)
    )
    if not (complete_capture or old_limited_capture or detailed_limited_capture
            or targeted_limit_capture or discovery_capture or targeted_capture):
        raise RunnerError("probe output is incomplete or claims a scope outside metadata capture")
    metadata_complete = bool(complete_capture)
    return {
        "payload": dict(payload),
        "raw_payload_json": raw_json,
        "raw_payload_bytes": len(raw_bytes),
        "raw_payload_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "metadata_complete": metadata_complete,
        "capture_kind": ("complete" if complete_capture else "discovery" if discovery_capture
                         else "targeted_table" if targeted_capture else "table_limit"),
    }


def _valid_feature_info_tags(value: Any) -> bool:
    if not isinstance(value, Mapping) or value.get("status") not in {"AVAILABLE", "EMPTY"}:
        return False
    tags = value.get("tags")
    return (isinstance(tags, list) and type(value.get("count")) is int
            and value["count"] == len(tags) and len(tags) <= 64
            and all(isinstance(tag, str) and tag and len(tag) <= 512 for tag in tags))


def _valid_selected_table(value: Any, request: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping):
        return False
    tag = request["feature_info_tag"]
    table_id = request["table_id"]
    table = value.get("table")
    if (value.get("requested_tag") != tag or value.get("tag") != tag
            or value.get("requested_table_id") != table_id or not isinstance(table, Mapping)
            or table.get("requested_table_id") != table_id or table.get("status") != "AVAILABLE"):
        return False
    rows = table.get("rows")
    row_count = table.get("row_count")
    return (isinstance(rows, list) and type(row_count) is int and row_count == len(rows)
            and 0 <= row_count <= 128)


def _valid_table_limit_payload(payload: Mapping[str, Any], location: Any, *,
                               expected_tag: str | None,
                               expected_table: str | None) -> bool:
    if (set(payload) != {"probe", "status", "code", "native_admission",
                         "payload_complete", "limit_location"}
            or payload.get("probe") != "W21FieldIdentityProbe"
            or payload.get("status") != "OUTPUT_LIMIT_EXCEEDED"
            or payload.get("code") != "TABLE_ROW_LIMIT_EXCEEDED"
            or payload.get("native_admission") != "UNVERIFIED"
            or payload.get("payload_complete") is not False
            or not isinstance(location, Mapping)
            or set(location) != {"physics_tag", "feature_info_tag", "table_id", "actual_rows", "hard_limit"}):
        return False
    return (location.get("physics_tag") == "ht"
            and isinstance(location.get("feature_info_tag"), str)
            and bool(location["feature_info_tag"])
            and location.get("table_id") in {"Shape", "Expression"}
            and type(location.get("actual_rows")) is int and location["actual_rows"] > 128
            and location.get("hard_limit") == 128
            and (expected_tag is None or location.get("feature_info_tag") == expected_tag)
            and (expected_table is None or location.get("table_id") == expected_table))


def _project_relative_file(project_root: Path, source: Path) -> str:
    """Return the exact regular project file as a traversal-free POSIX path."""
    root = project_root.resolve(strict=True)
    if source.is_symlink():
        raise RunnerError("staged Java source must not be a symlink")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise RunnerError("staged Java source is not a regular file")
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RunnerError("staged Java source escaped the frozen project workspace") from exc
    path = relative.as_posix()
    if not path or path == "." or any(part in {"", ".", ".."} for part in relative.parts):
        raise RunnerError("staged Java source did not produce a canonical project-relative path")
    return path


def _validate_java_execution_data(data: Mapping[str, Any], *, expected_source_sha256: str,
                                  expected_entrypoint: str, expected_model_tag: str,
                                  label: str) -> tuple[Any, dict[str, Any]]:
    """Validate the actual G2/Worker response nesting and retain its provenance."""
    if data.get("execution_success") is not True:
        raise RunnerError(f"{label} Java execution did not return execution_success=true")
    if (data.get("source_sha256") != expected_source_sha256
            or data.get("entrypoint") != expected_entrypoint):
        raise RunnerError(f"{label} execution_result source identity differs from the frozen Java input")
    worker_reply = data.get("worker")
    worker_result = worker_reply.get("result") if isinstance(worker_reply, Mapping) else None
    if (not isinstance(worker_reply, Mapping)
            or worker_reply.get("ok", worker_reply.get("success")) is not True
            or not isinstance(worker_result, Mapping)):
        raise RunnerError(f"{label} omitted the successful Worker execution result")
    if data.get("readback") != worker_result:
        raise RunnerError(f"{label} execution_result did not preserve the Worker result envelope")
    if (worker_result.get("executed") is not True
            or worker_result.get("model_tag") != expected_model_tag
            or worker_result.get("source_sha256") != expected_source_sha256
            or worker_result.get("entrypoint") != expected_entrypoint
            or "readback" not in worker_result):
        raise RunnerError(f"{label} Worker identity/source/entrypoint evidence is incomplete or mismatched")
    before, after = data.get("before"), data.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise RunnerError(f"{label} execution_result omitted its before/after Worker observations")
    evidence = {
        "source_sha256": expected_source_sha256,
        "entrypoint": expected_entrypoint,
        "worker": {
            "executed": True,
            "model_tag": expected_model_tag,
            "source_sha256": worker_result["source_sha256"],
            "entrypoint": worker_result["entrypoint"],
        },
        "before": dict(before),
        "after": dict(after),
    }
    return worker_result["readback"], evidence


class _RunState:
    def __init__(self, path: Path, state: dict[str, Any]):
        self.path = path
        self.value = state

    def save(self) -> None:
        temporary = self.path.with_name(self.path.name + ".pending")
        if temporary.exists() or temporary.is_symlink():
            raise RunnerError("unfinished state write exists; refusing to resume or replace")
        raw = json.dumps(self.value, sort_keys=True, indent=2, ensure_ascii=False,
                         allow_nan=False).encode("utf-8") + b"\n"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
        os.replace(temporary, self.path)

    def event(self, action: str, status: str, **extra: Any) -> None:
        row = {"action": action, "status": status,
               "at": datetime.now(timezone.utc).isoformat(), **extra}
        self.value["action_history"].append(row)
        self.save()


class _MCPCalls:
    def __init__(self, session: Any):
        self.session = session

    async def call(self, name: str, params: Mapping[str, Any], *,
                   timeout_s: float = RPC_WAIT_S) -> dict[str, Any]:
        result = await asyncio.wait_for(
            self.session.call_tool(name, dict(params)), timeout=timeout_s,
        )
        return _public_payload(result)


async def _validate_live_tools(client: _MCPCalls, expected: Mapping[str, Any]) -> None:
    listed = await asyncio.wait_for(client.session.list_tools(), timeout=RPC_WAIT_S)
    rows = getattr(listed, "tools", None)
    if rows is None and isinstance(listed, Mapping):
        rows = listed.get("tools")
    actual: dict[str, Any] = {}
    for row in rows or ():
        name = getattr(row, "name", None) if not isinstance(row, Mapping) else row.get("name")
        schema = getattr(row, "inputSchema", None) if not isinstance(row, Mapping) else row.get("inputSchema")
        if isinstance(name, str) and isinstance(schema, Mapping) and name in expected:
            actual[name] = dict(schema)
    if actual != expected:
        raise RunnerError("live stdio tools/list schemas differ from the frozen source registry")


def _operation_params(operation: str, arguments: Mapping[str, Any],
                      execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"operation_id": operation, "arguments": dict(arguments)}
    if execution is not None:
        result["execution"] = dict(execution)
    return result


def _assert_success(response: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if _is_unknown(response):
        raise RunnerError(f"{label} returned UNKNOWN")
    if response.get("success") is not True:
        error = response.get("error")
        code = error.get("code") if isinstance(error, Mapping) else "ROUTE_FAILED"
        raise RunnerError(f"{label} failed deterministically: {code}")
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise RunnerError(f"{label} returned no ActionResult data object")
    return data


def _require_execution_binding(response: Mapping[str, Any], *, label: str,
                               request_id: str, idempotency_key: str | None = None,
                               job_id: str | None = None) -> Mapping[str, Any]:
    execution = response.get("execution")
    if (not isinstance(execution, Mapping)
            or execution.get("request_id") != request_id
            or (idempotency_key is not None
                and execution.get("idempotency_key") != idempotency_key)
            or (job_id is not None and execution.get("job_id") != job_id)):
        raise RunnerError(f"{label} omitted or changed its exact request/job binding")
    return execution


async def _resolve_project_create_response(
        response: Mapping[str, Any], *, request_id: str, idempotency_key: str,
        wait_for_job) -> dict[str, Any]:
    """Resolve only the registered project.create and job.wait response shapes.

    ProjectAuthority returns ``data.project``. If the public route returns its
    documented pending job envelope, wait once on that exact durable job and
    read ``data.result.data.project``. No generic recursive unwrapping or
    operation replay is allowed.
    """
    data = _assert_success(response, "project.create")
    project = data.get("project")
    if isinstance(project, Mapping):
        execution = _require_execution_binding(response, label="project.create",
                                               request_id=request_id,
                                               idempotency_key=idempotency_key)
        if not isinstance(execution.get("operation_id"), str) or not execution["operation_id"]:
            raise RunnerError("project.create omitted its durable operation identity")
        return dict(project)

    job_id = data.get("job_id")
    if (not isinstance(job_id, str) or not job_id
            or data.get("status") not in {"QUEUED", "RUNNING"}):
        raise RunnerError("project.create returned neither data.project nor a pending job envelope")
    pending_execution = _require_execution_binding(
        response, label="project.create pending response", request_id=request_id,
        idempotency_key=idempotency_key, job_id=job_id)
    pending_operation_id = pending_execution.get("operation_id")
    if not isinstance(pending_operation_id, str) or not pending_operation_id:
        raise RunnerError("project.create pending response omitted its durable operation identity")

    wait_response = await wait_for_job(job_id)
    wait_data = _assert_success(wait_response, "project.create job.wait")
    if (wait_data.get("job_id") != job_id
            or wait_data.get("operation_id") != pending_operation_id
            or wait_data.get("status") != "SUCCEEDED"):
        raise RunnerError("project.create job.wait did not return the exact succeeded job")
    operation = wait_data.get("operation")
    if (not isinstance(operation, Mapping)
            or operation.get("operation") != "project.create"
            or operation.get("status") != "SUCCEEDED"
            or operation.get("request_id") != request_id
            or operation.get("idempotency_key") != idempotency_key):
        raise RunnerError("project.create job.wait operation binding differs from the frozen request")
    result = wait_data.get("result")
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise RunnerError("project.create job.wait omitted a successful original business result")
    result_execution = _require_execution_binding(
        result, label="project.create terminal result", request_id=request_id,
        idempotency_key=idempotency_key, job_id=job_id,
    )
    if (operation.get("operation_id") != pending_operation_id
            or result_execution.get("operation_id") != pending_operation_id):
        raise RunnerError("project.create job and terminal result operation identities differ")
    result_data = result.get("data")
    project = result_data.get("project") if isinstance(result_data, Mapping) else None
    if not isinstance(project, Mapping):
        raise RunnerError("project.create terminal result omitted data.project")
    return dict(project)


async def _resolve_initial_stage_run_response(
        response: Mapping[str, Any], *, request_id: str, idempotency_key: str,
        wait_for_job) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve one exact stage_run ticket, waiting at most once on its job."""
    data = _assert_success(response, "experiment.stage_run")
    if isinstance(data.get("attempt"), Mapping):
        return dict(response), None
    job_id = data.get("job_id")
    if (not isinstance(job_id, str) or not job_id
            or data.get("status") not in {"QUEUED", "RUNNING"}):
        raise RunnerError("experiment.stage_run returned neither a terminal attempt nor its pending job")
    pending_execution = _require_execution_binding(
        response, label="experiment.stage_run pending response",
        request_id=request_id, idempotency_key=idempotency_key, job_id=job_id,
    )
    operation_id = pending_execution.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        raise RunnerError("experiment.stage_run pending response omitted its operation identity")
    wait_response = await wait_for_job(job_id)
    terminal = _validate_initial_stage_wait_response(
        wait_response, job_id=job_id, operation_id=operation_id,
        request_id=request_id, idempotency_key=idempotency_key,
    )
    return terminal, dict(wait_response)


def _validate_initial_stage_wait_response(
        wait_response: Mapping[str, Any], *, job_id: str, operation_id: str,
        request_id: str, idempotency_key: str,
        wait_request_id: str | None = None) -> dict[str, Any]:
    """Validate a captured job.wait result against the original stage_run ticket."""
    wait_data = _assert_success(wait_response, "experiment.stage_run job.wait")
    if (wait_data.get("job_id") != job_id
            or wait_data.get("operation_id") != operation_id
            or wait_data.get("status") != "SUCCEEDED"):
        raise RunnerError("experiment.stage_run job.wait did not return the exact succeeded job")
    operation = wait_data.get("operation")
    if (not isinstance(operation, Mapping)
            or operation.get("operation") != "experiment.stage_run"
            or operation.get("operation_id") != operation_id
            or operation.get("status") != "SUCCEEDED"
            or operation.get("request_id") != request_id
            or operation.get("idempotency_key") != idempotency_key):
        raise RunnerError("experiment.stage_run job.wait operation binding differs from the frozen request")
    terminal = wait_data.get("result")
    if not isinstance(terminal, Mapping) or terminal.get("success") is not True:
        raise RunnerError("experiment.stage_run job.wait omitted a successful terminal result")
    terminal_execution = _require_execution_binding(
        terminal, label="experiment.stage_run terminal result",
        request_id=request_id, idempotency_key=idempotency_key, job_id=job_id,
    )
    if terminal_execution.get("operation_id") != operation_id:
        raise RunnerError("experiment.stage_run job and terminal result operation identities differ")
    wait_execution = wait_response.get("execution")
    if wait_execution is not None:
        if (not isinstance(wait_execution, Mapping)
                or (wait_request_id is not None
                    and wait_execution.get("request_id") != wait_request_id)):
            raise RunnerError("experiment.stage_run job.wait response differs from its frozen wait request")
    terminal_data = terminal.get("data")
    if not isinstance(terminal_data, Mapping) or not isinstance(terminal_data.get("attempt"), Mapping):
        raise RunnerError("experiment.stage_run terminal result omitted its durable attempt")
    return dict(terminal)


def _runner_stage_attempt_binding(attempt: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "project_id", "model_ref", "expected_revision", "plan_id", "plan_sha256",
        "definition_sha256", "stage_id", "ordinal", "attempt_id", "operation_id",
        "request_hash", "source_attempt_id",
    )
    return {key: attempt.get(key) for key in fields}


def _validate_initial_stage_worker_dispatches(
        attempt: Mapping[str, Any], rows: Any, *, saved_artifact_path: Path,
        solve_revision: int, output_revision: int) -> list[dict[str, Any]]:
    """Require the two actual submitted Worker mutations bound to this attempt."""
    if not isinstance(rows, list) or len(rows) != 2 or any(not isinstance(row, Mapping) for row in rows):
        raise RunnerError("initial stage lacks exactly one durable solve and save Worker dispatch")
    attempt_rows = [row for row in attempt.get("evidence", [])
                    if isinstance(row, Mapping) and row.get("kind") == "worker-dispatch"]
    normalized = [dict(row) for row in rows]
    if len(attempt_rows) != 2 or normalized != [dict(row) for row in attempt_rows]:
        raise RunnerError("native evidence Worker dispatch rows differ from the durable stage attempt")

    by_operation = {row.get("operation"): row for row in normalized}
    if set(by_operation) != {"run_study", "save_model"}:
        raise RunnerError("initial stage Worker dispatches are not one solve and one save")
    attempt_id = attempt.get("attempt_id")
    solve = by_operation["run_study"]
    save = by_operation["save_model"]
    expected_solve_dispatch_revision = attempt.get("expected_revision")
    if type(expected_solve_dispatch_revision) is not int or solve_revision <= expected_solve_dispatch_revision:
        raise RunnerError("underlying Study.run revision does not advance its exact stage-attempt revision")
    for row, operation, phase, method, target_revision in (
            (solve, "run_study", "solve", "run", expected_solve_dispatch_revision),
            (save, "save_model", "save", "save", output_revision)):
        worker_request_id = row.get("worker_request_id")
        if (row.get("kind") != "worker-dispatch"
                or row.get("phase") != "submitted"
                or row.get("stage_request_id") != f"{attempt_id}:{phase}"
                or row.get("worker_method") != method
                or not isinstance(worker_request_id, str)
                or re.fullmatch(r"wrk-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                worker_request_id) is None
                or not _valid_sha256(row.get("worker_request_hash"))
                or not isinstance(row.get("worker_receiver"), str) or not row.get("worker_receiver")
                or type(row.get("worker_generation")) is not int or row.get("worker_generation") < 1
                or type(row.get("revision")) is not int or row.get("revision") != target_revision):
            raise RunnerError(f"durable {operation} Worker dispatch identity or revision is malformed")
    if (solve.get("worker_request_id") == save.get("worker_request_id")
            or solve.get("worker_generation") != save.get("worker_generation")
            or solve.get("worker_args") != []):
        raise RunnerError("initial solve/save Worker dispatches were reused or do not match the bound study")
    save_args = save.get("worker_args")
    if (not isinstance(save_args, list) or len(save_args) != 2 or save_args[1] is not True
            or not isinstance(save_args[0], str)):
        raise RunnerError("initial save Worker dispatch omitted its atomic temporary path")
    save_temp = Path(save_args[0])
    expected_name = re.compile(rf"\.{re.escape(saved_artifact_path.name)}\.[0-9a-f]{{32}}\.tmp\.mph")
    if (save_temp.parent != saved_artifact_path.parent
            or expected_name.fullmatch(save_temp.name) is None):
        raise RunnerError("initial save Worker dispatch does not target the bound AtomicSave sibling path")
    return normalized


def _count_initial_stage_worker_events(rows: Any, *, job_id: str,
                                       stage_run_operation_id: str,
                                       dispatches: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """Count Worker RPCs and require successful observed pairs for solve/save."""
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise RunnerError("initial stage omitted the durable OperationStore Worker event rows")
    request_ids: dict[str, str] = {}
    events_by_request: dict[str, dict[str, tuple[int, Mapping[str, Any]]]] = {}
    for row_index, row in enumerate(rows):
        metadata = row.get("metadata")
        if (row.get("job_id") != job_id or row.get("event") != "worker_request"
                or not isinstance(metadata, Mapping)):
            raise RunnerError("initial stage Worker event row is not from its exact durable job")
        request_id = metadata.get("request_id")
        request_hash = metadata.get("request_hash")
        phase = metadata.get("phase")
        if (not isinstance(request_id, str) or not request_id
                or re.fullmatch(r"wrk-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                                request_id) is None
                or not _valid_sha256(request_hash)
                or phase not in {"submitted", "observed"}):
            raise RunnerError("initial stage Worker event id, hash, or phase is malformed or duplicated")
        prior_hash = request_ids.setdefault(request_id, request_hash)
        if prior_hash != request_hash:
            raise RunnerError("initial stage Worker event reused one request id with different request hashes")
        phases = events_by_request.setdefault(request_id, {})
        if phase in phases:
            raise RunnerError("initial stage Worker event phase is duplicated")
        phases[phase] = (row_index, metadata)
        if phase == "observed":
            reply = metadata.get("reply")
            reply_status = reply.get("status") if isinstance(reply, Mapping) else None
            valid_success = (reply_status == "SUCCEEDED" and reply.get("ok") is True
                             and metadata.get("status") == "SUCCEEDED")
            valid_failure = (reply_status == "FAILED" and reply.get("ok") is False
                             and isinstance(reply.get("failure"), Mapping)
                             and metadata.get("status") == "FAILED")
            if (not isinstance(reply, Mapping) or reply.get("request_id") != request_id
                    or not (valid_success or valid_failure)):
                raise RunnerError("initial stage Worker observed reply is missing, uncorrelated, or not terminal")
    submitted = {request_id for request_id, phases in events_by_request.items()
                 if "submitted" in phases}
    if set(request_ids) != submitted:
        raise RunnerError("initial stage Worker event row has no unique submitted RPC")
    for request_id, phases in events_by_request.items():
        if set(phases) != {"submitted", "observed"}:
            raise RunnerError("initial stage Worker submitted request lacks exactly one terminal observed event")
        submit_index, submitted_event = phases["submitted"]
        observe_index, observed_event = phases["observed"]
        if (submit_index >= observe_index
                or submitted_event.get("request_hash") != observed_event.get("request_hash")
                or submitted_event.get("kind") != observed_event.get("kind")
                or submitted_event.get("operation_id") != observed_event.get("operation_id")
                or submitted_event.get("metadata") != observed_event.get("metadata")):
            raise RunnerError("initial stage Worker submit/observe evidence is duplicated, reordered, or conflicting")
    for dispatch in dispatches:
        worker_id = dispatch.get("worker_request_id")
        phases = events_by_request.get(worker_id) if isinstance(worker_id, str) else None
        if (not isinstance(worker_id, str) or worker_id not in submitted
                or request_ids.get(worker_id) != dispatch.get("worker_request_hash")
                or not isinstance(phases, Mapping)
                or set(phases) != {"submitted", "observed"}
                or phases["observed"][1].get("request_hash") != dispatch.get("worker_request_hash")):
            raise RunnerError("durable solve/save dispatch lacks one exact successful Worker submit/observe pair")
        submit_index, submitted_event = phases["submitted"]
        observe_index, observed_event = phases["observed"]
        worker_args = submitted_event.get("metadata")
        observed_args = observed_event.get("metadata")
        reply = observed_event.get("reply")
        method = "run" if dispatch.get("operation") == "run_study" else "save"
        generation = dispatch.get("worker_generation")
        if (submit_index >= observe_index
                or submitted_event.get("kind") != "call"
                or observed_event.get("kind") != "call"
                or submitted_event.get("operation_id") != stage_run_operation_id
                or observed_event.get("operation_id") != stage_run_operation_id
                or observed_event.get("status") != "SUCCEEDED"
                or submitted_event.get("request_hash") != observed_event.get("request_hash")
                or submitted_event.get("metadata") != observed_event.get("metadata")
                or not isinstance(worker_args, Mapping)
                or worker_args.get("request_id") != worker_id
                or worker_args.get("method") != method
                or worker_args.get("handle") != dispatch.get("worker_receiver")
                or worker_args.get("generation") != generation
                or worker_args.get("args") != dispatch.get("worker_args")
                or not isinstance(reply, Mapping)
                or reply.get("ok") is not True
                or reply.get("status") != "SUCCEEDED"
                or reply.get("request_id") != worker_id
                or reply.get("generation") != generation):
            raise RunnerError("solve/save Worker event pair differs from the exact submitted RPC or successful correlated reply")
    return len(submitted), len(rows)


def _validate_initial_stage_acceptance(
        response: Mapping[str, Any], *, plan: Mapping[str, Any],
        project_id: str, session_id: str, model_ref: Mapping[str, Any],
        stage_plan_sha256: str, declaration_revision: int,
        request_id: str, idempotency_key: str,
        operation_id: str, request_hash: str) -> dict[str, Any]:
    """Verify one successful stage-run terminal result and its exact saved bytes."""
    data = _assert_success(response, "experiment.stage_run terminal result")
    attempt = data.get("attempt")
    acceptance = data.get("initial_output_acceptance")
    if not isinstance(attempt, Mapping) or not isinstance(acceptance, Mapping):
        raise RunnerError("experiment.stage_run omitted its durable attempt or initial-output acceptance")
    attempt_id = attempt.get("attempt_id")
    if (not isinstance(attempt_id, str)
            or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", attempt_id) is None
            or attempt.get("kind") != "w21_stage_attempt"
            or attempt.get("project_id") != project_id
            or attempt.get("model_ref") != dict(model_ref)
            or attempt.get("expected_revision") != declaration_revision
            or attempt.get("plan_id") != INITIAL_STAGE_PLAN_ID
            or attempt.get("plan_sha256") != stage_plan_sha256
            or attempt.get("definition_sha256") != plan.get("initial_stage_definition_sha256")
            or attempt.get("stage_id") != INITIAL_STAGE_ID
            or attempt.get("ordinal") != 1
            or attempt.get("operation_id") != operation_id
            or attempt.get("request_hash") != request_hash
            or attempt.get("request_id") != request_id
            or attempt.get("idempotency_key") != idempotency_key
            or attempt.get("source_attempt_id") is not None
            or attempt.get("status") != "ACCEPTED"
            or attempt.get("acceptance_status") != "INITIAL_OUTPUT_ACCEPTED"):
        raise RunnerError("stage attempt identity, revision, plan, or acceptance differs from this frozen run")
    result = attempt.get("result")
    if (not isinstance(result, Mapping)
            or result.get("acceptance_scope") != INITIAL_STAGE_ACCEPTANCE_SCOPE
            or data.get("acceptance_status") != "INITIAL_OUTPUT_ACCEPTED"
            or acceptance.get("status") != "PASS"
            or acceptance.get("scope") != INITIAL_STAGE_ACCEPTANCE_SCOPE
            or acceptance.get("attempt_id") != attempt_id):
        raise RunnerError("stage-run terminal result does not state the exact initial-output-only scope")
    evidence = attempt.get("evidence")
    if not isinstance(evidence, list):
        raise RunnerError("accepted stage attempt omitted its durable evidence rows")
    output_rows = [row for row in evidence if isinstance(row, Mapping)
                   and row.get("kind") == "stage-output-readback"]
    artifact_rows = [row for row in evidence if isinstance(row, Mapping)
                     and row.get("kind") == "saved-stage-artifact"]
    native_rows = [row for row in evidence if isinstance(row, Mapping)
                   and row.get("kind") == "initial-output-native-evidence"]
    if len(output_rows) != 1 or len(artifact_rows) != 1 or len(native_rows) != 1:
        raise RunnerError("accepted stage attempt lacks unique durable output, artifact, and native evidence rows")
    output_row = output_rows[0]
    artifact_row = artifact_rows[0]
    native_row = native_rows[0]
    output_tuple = acceptance.get("output_tuple")
    if (output_row.get("status") != "VERIFIED"
            or not isinstance(output_row.get("output_tuple"), Mapping)
            or dict(output_tuple) != dict(output_row["output_tuple"])):
        raise RunnerError("initial-output acceptance tuple differs from durable stage output readback")
    tuple_value = dict(output_tuple)
    if (tuple_value.get("dataset") != "dset1"
            or not isinstance(tuple_value.get("solution"), str) or not tuple_value.get("solution")
            or type(tuple_value.get("outer")) is not int or tuple_value["outer"] < 1
            or type(tuple_value.get("inner")) is not int or tuple_value["inner"] < 1):
        raise RunnerError("initial-output tuple is incomplete or outside the frozen dset1 target")
    stage = plan["initial_stage_definition"]["stages"][0]
    target_selection = stage["target_selection"]
    for key in ("dataset", "solution"):
        if key in target_selection and tuple_value.get(key) != target_selection[key]:
            raise RunnerError("initial-output tuple differs from the frozen stage target selection")
    for axis in ("outer", "inner"):
        selected = target_selection.get(axis)
        if isinstance(selected, list) and len(selected) == 1 and tuple_value.get(axis) != selected[0]:
            raise RunnerError("initial-output tuple differs from the frozen explicit solution index")

    saved = acceptance.get("saved_artifact")
    data_saved = data.get("saved_artifact")
    result_saved = result.get("saved_artifact")
    if (not isinstance(saved, Mapping) or dict(saved) != dict(data_saved or {})
            or dict(saved) != dict(result_saved or {})
            or artifact_row.get("path") != saved.get("path")
            or artifact_row.get("sha256") != saved.get("sha256")
            or artifact_row.get("size") != saved.get("size")):
        raise RunnerError("accepted stage artifact references disagree across the durable attempt and response")
    artifact_path = saved.get("path")
    artifact_hash = saved.get("sha256")
    artifact_size = saved.get("size")
    if (not isinstance(artifact_path, str) or not _valid_sha256(artifact_hash)
            or type(artifact_size) is not int or artifact_size <= 0):
        raise RunnerError("accepted stage artifact path/hash/size receipt is malformed")
    expected_artifact = Path(plan["project_workspace"]) / "stage_outputs" / f"{attempt_id}.mph"
    actual_artifact = Path(artifact_path)
    if (actual_artifact.is_symlink() or not actual_artifact.is_file()
            or actual_artifact.resolve(strict=True) != expected_artifact.resolve()
            or actual_artifact.stat().st_size != artifact_size):
        raise RunnerError("saved stage artifact path or bytes do not match the current run")
    observed_artifact_hash = sha256_file(actual_artifact)
    if observed_artifact_hash != artifact_hash:
        raise RunnerError("saved stage artifact hash differs from the bytes currently on disk")
    actual_budget = _stage_output_windows_path_budget(Path(plan["project_workspace"]))
    if actual_budget != plan.get("stage_output_windows_path_budget"):
        raise RunnerError("saved stage output exceeds or differs from the frozen Windows path budget")

    native_evidence = acceptance.get("native_evidence")
    native_row_payload = native_row.get("native_evidence", native_row.get("evidence"))
    expected_attempt_binding = _runner_stage_attempt_binding(attempt)
    if not isinstance(native_evidence, Mapping) or native_row_payload != dict(native_evidence):
        raise RunnerError("native output evidence differs from its durable stage-attempt evidence row")
    native_output = native_evidence.get("native_output_readback")
    if not isinstance(native_output, Mapping):
        raise RunnerError("native output evidence omitted the full managed initial-output readback")
    revision_chain = output_row.get("revision_chain")
    if (native_evidence.get("stage_attempt_binding") != expected_attempt_binding
            or native_evidence.get("saved_artifact") != dict(saved)
            or native_evidence.get("saved_artifact_observed_sha256") != observed_artifact_hash):
        raise RunnerError("native evidence is not exact-bound to the stage attempt and saved artifact")
    if (not isinstance(revision_chain, list) or not revision_chain
            or native_output.get("revision_chain") != revision_chain
            or native_output.get("output_tuple") != tuple_value):
        raise RunnerError("initial stage output is missing its same-run revision chain")
    output_revision = output_row.get("output_revision")
    artifact_revision = artifact_row.get("model_revision")
    if (type(output_revision) is not int or output_revision < declaration_revision
            or type(artifact_revision) is not int or artifact_revision < output_revision):
        raise RunnerError("initial stage output/artifact revisions do not form a monotonic run chain")
    solve_revision = native_output.get("solve_revision")
    native_output_revision = native_output.get("output_revision")
    if (type(solve_revision) is not int or solve_revision <= declaration_revision
            or type(native_output_revision) is not int or native_output_revision != output_revision):
        raise RunnerError("managed initial output solve/output revisions differ from durable stage evidence")

    dispatches = _validate_initial_stage_worker_dispatches(
        attempt, native_evidence.get("solve_save_dispatches"),
        saved_artifact_path=actual_artifact,
        solve_revision=solve_revision, output_revision=output_revision,
    )
    preflight = native_evidence.get("initial_stage_preflight")
    preflight_record = preflight.get("record") if isinstance(preflight, Mapping) else None
    preflight_chain = preflight_record.get("revision_chain") if isinstance(preflight_record, Mapping) else None
    output_chain = native_output.get("revision_chain")
    if (not isinstance(preflight_chain, list) or not preflight_chain
            or not isinstance(output_chain, list) or not output_chain
            or any(not isinstance(row, Mapping) for row in preflight_chain + output_chain)):
        raise RunnerError("initial-stage internal action accounting lacks its preflight/output chains")
    try:
        from comsol_mcp._w21_stage_backend import initial_stage_managed_action_plan
    except ImportError as exc:
        raise RunnerError("formal W21 initial-stage action planner is unavailable") from exc
    expected_action_plan = initial_stage_managed_action_plan(
        {"definition": plan["initial_stage_definition"]}, stage,
    )
    if (not isinstance(expected_action_plan, list)
            or native_evidence.get("managed_internal_action_plan") != expected_action_plan
            or preflight_record.get("managed_internal_action_plan") != expected_action_plan
            or native_output.get("managed_internal_action_plan") != expected_action_plan):
        raise RunnerError("native evidence managed-observation action plan differs from the frozen stage")
    # Count every managed observation action, including the separately ticketed
    # Variables.xmeshInfo readback already present in the output revision chain.
    # The single mesh.inspect snapshot follows that chain; Study.run and AtomicSave
    # are checked separately as the two mutations.
    internal_read_count = len(preflight_chain) + len(output_chain) + 1
    internal_read_budget = plan.get("budgets", {}).get("stage_output_internal_read_actions_max")
    if (type(internal_read_budget) is not int
            or internal_read_budget != INITIAL_STAGE_OUTPUT_INTERNAL_READ_ACTIONS_MAX
            or internal_read_count > internal_read_budget
            or type(native_evidence.get("internal_read_cap")) is not int
            or native_evidence.get("internal_read_cap") != internal_read_budget
            or type(native_evidence.get("internal_read_count")) is not int
            or native_evidence.get("internal_read_count") != internal_read_count):
        raise RunnerError("initial-stage managed observation action count exceeds or differs from its frozen 12-action budget")
    run_execution = response.get("execution")
    run_job_id = run_execution.get("job_id") if isinstance(run_execution, Mapping) else None
    if not isinstance(run_job_id, str) or not run_job_id:
        raise RunnerError("initial stage Worker event evidence has no exact stage_run job identity")
    worker_rpc_count, worker_event_row_count = _count_initial_stage_worker_events(
        native_evidence.get("worker_event_rows"), job_id=run_job_id,
        stage_run_operation_id=operation_id,
        dispatches=dispatches,
    )
    if (type(native_evidence.get("raw_worker_rpc_count")) is not int
            or native_evidence.get("raw_worker_rpc_count") != worker_rpc_count):
        raise RunnerError("raw Worker RPC count differs from the durable submitted Worker events")
    try:
        from comsol_mcp._w21_stage_backend import (
            validate_initial_output_readback,
            validate_initial_output_acceptance,
        )
    except ImportError as exc:
        raise RunnerError("formal W21 initial-output validators are unavailable") from exc
    readback_valid, readback_errors = validate_initial_output_readback(
        native_output, binding=expected_attempt_binding, stage=stage,
        stage_run_operation_id=operation_id, model_revision=solve_revision,
    )
    if not readback_valid:
        reasons = "; ".join(str(item) for item in readback_errors[:8])
        raise RunnerError(f"managed initial output readback failed formal validation: {reasons}")
    acceptance_valid, acceptance_errors = validate_initial_output_acceptance(
        native_evidence, binding=expected_attempt_binding, stage=stage,
        stage_run_operation_id=operation_id, solve_revision=solve_revision,
        output_revision=output_revision,
        observed_artifact_sha256=observed_artifact_hash,
    )
    if not acceptance_valid:
        reasons = "; ".join(str(item) for item in acceptance_errors[:8])
        raise RunnerError(f"complete initial-output acceptance failed formal validation: {reasons}")
    return {
        "status": "PASS", "scope": INITIAL_STAGE_ACCEPTANCE_SCOPE,
        "attempt_id": attempt_id, "output_tuple": tuple_value,
        "native_evidence": dict(native_evidence), "saved_artifact": dict(saved),
        "stage_plan_sha256": stage_plan_sha256,
        "revision_chain": revision_chain,
        "managed_internal_action_plan": expected_action_plan,
        "solve_revision": solve_revision,
        "output_revision": output_revision,
        "artifact_model_revision": artifact_revision,
        "solve_save_dispatches": dispatches,
        "internal_read_actions": internal_read_count,
        "raw_worker_rpc_count": worker_rpc_count,
        "worker_event_row_count": worker_event_row_count,
        "attempt_sha256": attempt.get("sha256"),
    }


def _revalidate_initial_stage_receipt(path: Path, value: Mapping[str, Any],
                                      state: Mapping[str, Any], *,
                                      expected_source_manifest_sha256: str) -> dict[str, Any]:
    """Rebuild 6.4 initial acceptance from its frozen plan and terminal evidence."""
    freeze_path = path.parent / "freeze.json"
    if freeze_path.is_symlink() or not freeze_path.is_file():
        raise RunnerError("6.4 initial-stage receipt lacks its original frozen plan")
    try:
        frozen_raw = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError("6.4 initial-stage frozen plan is unreadable") from exc
    if not isinstance(frozen_raw, Mapping):
        raise RunnerError("6.4 initial-stage frozen plan is not an object")
    frozen = dict(frozen_raw)
    frozen_hash = frozen.pop("freeze_sha256", None)
    source_manifest = frozen.get("source_manifest")
    if (frozen_hash != value.get("freeze_sha256")
            or state.get("freeze_sha256") != frozen_hash
            or sha256_value(frozen) != frozen_hash
            or frozen.get("mode") != INITIAL_STAGE_MODE
            or frozen.get("kind") != INITIAL_STAGE_KIND
            or frozen.get("schema") != INITIAL_STAGE_SCHEMA
            or frozen.get("requested_version") != "6.4"
            or frozen.get("run_id") != value.get("run_id")
            or Path(frozen.get("run_root", "")).resolve() != path.parent.resolve()
            or frozen.get("source_manifest_sha256") != expected_source_manifest_sha256
            or not isinstance(source_manifest, Mapping)
            or sha256_value(source_manifest) != expected_source_manifest_sha256
            or state.get("run_id") != frozen.get("run_id")
            or value.get("source_manifest_sha256") != expected_source_manifest_sha256):
        raise RunnerError("6.4 receipt, durable state, and original frozen plan/source do not match")
    _validate_frozen_initial_stage(frozen)
    if value.get("freeze_sha256") != frozen_hash:
        raise RunnerError("6.4 initial-stage receipt is not bound to the frozen plan hash")
    selected_comsol = frozen.get("selected_comsol")
    if (not isinstance(selected_comsol, Mapping)
            or not str(selected_comsol.get("version", "")).startswith("6.4")
            or str(selected_comsol.get("build", selected_comsol.get("build_number", ""))) != "293"
            or value.get("selected_comsol") != dict(selected_comsol)):
        raise RunnerError("6.4 initial-stage receipt does not match its frozen COMSOL 6.4.293 runtime")

    initial_stage = value.get("initial_stage")
    if not isinstance(initial_stage, Mapping):
        raise RunnerError("6.4 receipt omits its durable initial-stage record")
    if (initial_stage.get("profile") != INITIAL_STAGE_PROFILE
            or initial_stage.get("definition") != frozen.get("initial_stage_definition")
            or initial_stage.get("definition_sha256") != frozen.get("initial_stage_definition_sha256")
            or initial_stage.get("operations") != frozen.get("initial_stage_operations")):
        raise RunnerError("6.4 receipt stage definition/operations differ from its frozen initial-stage plan")
    stage_define = initial_stage.get("stage_define")
    stage_run = initial_stage.get("stage_run")
    if not isinstance(stage_define, Mapping) or not isinstance(stage_run, Mapping):
        raise RunnerError("6.4 receipt omits stage declaration or terminal run evidence")
    define_data = stage_define.get("data")
    if not isinstance(define_data, Mapping):
        raise RunnerError("6.4 receipt stage declaration response is malformed")
    binding = value.get("model_binding")
    model_ref = binding.get("model_ref") if isinstance(binding, Mapping) else None
    project_id, session_id = value.get("project_id"), value.get("session_id")
    declaration_revision = define_data.get("declaration_revision")
    stage_plan_sha256 = define_data.get("sha256")
    if (not isinstance(model_ref, Mapping) or not isinstance(project_id, str) or not project_id
            or not isinstance(session_id, str) or not session_id
            or define_data.get("plan_id") != INITIAL_STAGE_PLAN_ID
            or define_data.get("project_id") != project_id
            or define_data.get("model_ref") != dict(model_ref)
            or type(declaration_revision) is not int or declaration_revision < 0
            or define_data.get("definition_sha256") != frozen.get("initial_stage_definition_sha256")
            or define_data.get("stage_ids") != [INITIAL_STAGE_ID]
            or define_data.get("registration") not in {"CREATED", "IDEMPOTENT_EXISTING"}
            or define_data.get("declaration_status") != "DECLARED_UNVERIFIED"
            or define_data.get("worker_rpc_performed") is not False
            or define_data.get("solve_started") is not False
            or not _valid_sha256(stage_plan_sha256)):
        raise RunnerError("6.4 stage declaration does not match its original project/model/plan binding")
    define_request = frozen.get("request_ids", {}).get("stage_define")
    define_key = frozen.get("idempotency_keys", {}).get("stage_define")
    define_progress = state.get("stage_define_progress")
    define_action = state.get("actions", {}).get("stage_define")
    if (stage_define.get("request_id") != define_request
            or stage_define.get("idempotency_key") != define_key
            or not isinstance(stage_define.get("operation_id"), str) or not stage_define["operation_id"]
            or not _valid_sha256(stage_define.get("request_hash"))
            or not isinstance(stage_define.get("job_id"), str) or not stage_define["job_id"]
            or not isinstance(define_progress, Mapping)
            or define_progress.get("status") != "CONFIRMED"
            or define_progress.get("request_id") != define_request
            or define_progress.get("idempotency_key") != define_key
            or define_progress.get("operation_id") != stage_define.get("operation_id")
            or define_progress.get("request_hash") != stage_define.get("request_hash")
            or define_progress.get("job_id") != stage_define.get("job_id")
            or define_progress.get("plan_id") != INITIAL_STAGE_PLAN_ID
            or define_progress.get("plan_sha256") != stage_plan_sha256
            or define_progress.get("definition_sha256") != frozen.get("initial_stage_definition_sha256")
            or define_progress.get("declaration_revision") != declaration_revision
            or not isinstance(define_action, Mapping)
            or define_action.get("status") != "RESPONSE_RECORDED"
            or define_action.get("params_sha256") != sha256_value(_operation_params(
                "experiment.stage_define", frozen["initial_stage_operations"][0]["arguments"], {
                    "project_id": project_id, "session_id": session_id,
                    "model_ref": dict(model_ref), "expected_revision": declaration_revision,
                    "request_id": define_request, "idempotency_key": define_key,
                    "rpc_timeout_s": frozen["budgets"]["ordinary_rpc_wait_seconds"],
                }))):
        raise RunnerError("6.4 stage_define report/state does not bind the frozen public request")

    request_id = frozen.get("request_ids", {}).get("stage_run")
    idempotency_key = frozen.get("idempotency_keys", {}).get("stage_run")
    terminal_response = stage_run.get("terminal_response")
    if not isinstance(terminal_response, Mapping):
        raise RunnerError("6.4 receipt omits the original terminal stage_run response")
    terminal_data = terminal_response.get("data")
    attempt = terminal_data.get("attempt") if isinstance(terminal_data, Mapping) else None
    run_execution = terminal_response.get("execution")
    if (not isinstance(attempt, Mapping) or not isinstance(run_execution, Mapping)
            or stage_run.get("request_id") != request_id
            or stage_run.get("idempotency_key") != idempotency_key
            or stage_run.get("operation_id") != run_execution.get("operation_id")
            or stage_run.get("request_hash") != run_execution.get("request_hash")
            or stage_run.get("job_id") != run_execution.get("job_id")
            or run_execution.get("project_id") != project_id
            or run_execution.get("session_id") != session_id
            or run_execution.get("model_ref") != dict(model_ref)
            or run_execution.get("request_id") != request_id
            or run_execution.get("idempotency_key") != idempotency_key
            or run_execution.get("operation_id") != attempt.get("operation_id")
            or run_execution.get("request_hash") != attempt.get("request_hash")
            or type(run_execution.get("revision")) is not int):
        raise RunnerError("6.4 terminal stage_run ticket is not bound to its frozen request and attempt")
    run_progress = state.get("stage_run_progress")
    run_action = state.get("actions", {}).get("stage_run")
    expected_run_params = _operation_params(
        "experiment.stage_run", frozen["initial_stage_operations"][1]["arguments"], {
            "project_id": project_id, "session_id": session_id,
            "model_ref": dict(model_ref), "expected_revision": declaration_revision,
            "request_id": request_id, "idempotency_key": idempotency_key,
            "queue_timeout_s": frozen["budgets"]["stage_run_queue_timeout_seconds"],
            "execution_timeout_s": frozen["budgets"]["stage_run_execution_timeout_seconds"],
            "rpc_timeout_s": frozen["budgets"]["stage_run_rpc_wait_seconds"],
        })
    if (not isinstance(run_progress, Mapping)
            or run_progress.get("status") != "CONFIRMED"
            or run_progress.get("request_id") != request_id
            or run_progress.get("idempotency_key") != idempotency_key
            or run_progress.get("operation_id") != stage_run.get("operation_id")
            or run_progress.get("request_hash") != stage_run.get("request_hash")
            or run_progress.get("job_id") != stage_run.get("job_id")
            or run_progress.get("revision_before") != declaration_revision
            or run_progress.get("revision_after") != run_execution.get("revision")
            or run_progress.get("attempt_id") != attempt.get("attempt_id")
            or not isinstance(run_action, Mapping)
            or run_action.get("status") != "RESPONSE_RECORDED"
            or run_action.get("request_id") != request_id
            or run_action.get("idempotency_key") != idempotency_key
            or run_action.get("params_sha256") != sha256_value(expected_run_params)):
        raise RunnerError("6.4 stage_run receipt/state does not bind the frozen public request")

    acceptance = _validate_initial_stage_acceptance(
        terminal_response, plan=frozen, project_id=project_id,
        session_id=session_id, model_ref=model_ref,
        stage_plan_sha256=stage_plan_sha256,
        declaration_revision=declaration_revision,
        request_id=request_id, idempotency_key=idempotency_key,
        operation_id=run_execution["operation_id"],
        request_hash=run_execution["request_hash"],
    )
    recorded = value.get("initial_stage_acceptance")
    if (acceptance != recorded or initial_stage.get("acceptance") != recorded
            or state.get("initial_stage_acceptance") != recorded
            or state.get("initial_stage_acceptance_sha256") != sha256_value(recorded)):
        raise RunnerError("6.4 receipt/state initial-stage acceptance differs from revalidated native evidence")

    wait_response = stage_run.get("wait_response")
    wait_calls = 1 if wait_response is not None else 0
    wait_status = "CONFIRMED" if wait_calls else "NOT_REQUIRED"
    wait_action = state.get("actions", {}).get("stage_run_wait")
    wait_binding = {
        "calls": wait_calls, "status": wait_status,
        "job_id": run_execution.get("job_id"),
        "stage_run_operation_id": run_execution.get("operation_id"),
        "stage_run_request_id": request_id,
        "stage_run_idempotency_key": idempotency_key,
        "wait_request_id": frozen.get("request_ids", {}).get("stage_run_wait") if wait_calls else None,
        "wait_response_sha256": sha256_value(wait_response) if wait_response is not None else None,
    }
    if wait_calls:
        terminal_from_wait = _validate_initial_stage_wait_response(
            wait_response, job_id=run_execution["job_id"],
            operation_id=run_execution["operation_id"],
            request_id=request_id, idempotency_key=idempotency_key,
            wait_request_id=frozen.get("request_ids", {}).get("stage_run_wait"),
        )
        if terminal_from_wait != dict(terminal_response):
            raise RunnerError("6.4 wait result differs from the preserved terminal stage_run response")
        wait_args = {
            "job_id": run_execution["job_id"],
            "timeout_s": frozen["budgets"]["stage_run_result_wait_seconds"],
            "poll_interval_s": 0.25,
        }
        wait_execution = {
            "request_id": frozen["request_ids"]["stage_run_wait"],
            "rpc_timeout_s": frozen["budgets"]["stage_run_result_wait_seconds"],
        }
        expected_wait_params = _operation_params("job.wait", wait_args, wait_execution)
        if (not isinstance(wait_action, Mapping)
                or wait_action.get("status") != "RESPONSE_RECORDED"
                or wait_action.get("request_id") != frozen["request_ids"]["stage_run_wait"]
                or wait_action.get("target_job_id") != run_execution["job_id"]
                or wait_action.get("params_sha256") != sha256_value(expected_wait_params)
                or wait_action.get("response") != _response_summary(wait_response)):
            raise RunnerError("6.4 wait response is not bound to one exact original stage_run job.wait")
    elif wait_action is not None:
        raise RunnerError("6.4 receipt records a wait action although no wait response was required")
    if (state.get("stage_run_wait_calls") != wait_calls
            or state.get("stage_run_wait_calls_possible") != wait_calls
            or state.get("stage_run_wait_calls_status", "NOT_DISPATCHED") != (
                "CONFIRMED" if wait_calls else "NOT_DISPATCHED")
            or state.get("stage_run_wait_binding") != wait_binding
            or value.get("stage_run_wait_calls") != wait_calls):
        raise RunnerError("6.4 durable wait accounting differs from the preserved original-job wait response")
    return acceptance


async def run_metadata_protocol(client: _MCPCalls, plan: Mapping[str, Any],
                                state: _RunState, *, clock=time.monotonic,
                                preflight=None) -> dict[str, Any]:
    """Run one prepared route sequence; tests inject only the stdio transport."""
    if state.value.get("status") != "PREPARED" or state.value.get("action_history"):
        raise RunnerError("state is not pristine PREPARED; replay is forbidden")
    mode = plan.get("mode", METADATA_MODE)
    field_profile = _frozen_field_readback_profile(plan)
    _validate_frozen_initial_stage(plan)
    if (plan.get("schema") != _mode_schema(mode)
            or plan.get("kind") not in (None, _mode_kind(mode))
            or plan.get("budgets") != _mode_budgets(mode)):
        raise RunnerError("prepared mode/schema/budget identity is invalid")
    preflight_records = preflight() if preflight is not None else []
    await _validate_live_tools(client, plan["published_tool_schemas"])

    request_ids = plan["request_ids"]
    keys = plan["idempotency_keys"]
    project_workspace = Path(plan["project_workspace"])
    workspace_root = project_workspace.parent
    project_id = session_id = None
    connected: dict[str, Any] | None = None
    binding: dict[str, Any] | None = None
    deadline: float | None = None
    any_unknown = False
    owned_session_verified = False
    initial_stage_record: dict[str, Any] | None = None

    def record_stage_dispatch(name: str, status: str, *, possible: bool = False,
                              confirmed: bool = False) -> None:
        counters = STAGE_READ_ACTION_COUNTERS.get(name, ())
        if mode == INITIAL_STAGE_MODE:
            counters = INITIAL_STAGE_ACTION_COUNTERS.get(name, counters)
        for counter in counters:
            state.value.setdefault(counter, 0)
            state.value.setdefault(f"{counter}_possible", 0)
            if possible:
                state.value[f"{counter}_possible"] = 1
            if confirmed:
                state.value[counter] = 1
            state.value[f"{counter}_status"] = status

    async def query_unknown_once(name: str, request_id: str | None,
                                 response: Mapping[str, Any] | None) -> None:
        """Make one scoped read-only status query; never resubmit the action."""
        summary = _response_summary(response or {})
        reported_job_id = summary.get("job_id")
        reported_project_id = summary.get("project_id")
        query_target_request_id = request_id
        action_record = state.value.get("actions", {}).get(name, {})
        query_target_idempotency_key = (
            action_record.get("idempotency_key")
            if isinstance(action_record, Mapping) else None
        )
        query_target_project_id = project_id
        if name == "session.start":
            if (not isinstance(query_target_request_id, str) or not query_target_request_id
                    or not isinstance(query_target_idempotency_key, str) or not query_target_idempotency_key
                    or not isinstance(project_id, str) or not project_id):
                state.value["recovery"]["read_only_query"] = {
                    "status": "NOT_AVAILABLE_FOR_ACTION", "action": name,
                }
                state.save()
                return
            if reported_project_id is not None and reported_project_id != project_id:
                state.value["recovery"]["read_only_query"] = {
                    "status": "RESPONSE_PROJECT_ID_MISMATCH", "action": name,
                    "reported_project_id": reported_project_id,
                    "expected_project_id": project_id,
                }
                state.save()
                return
            query_target_project_id = project_id
            if (isinstance(reported_job_id, str) and reported_job_id
                    and reported_project_id == project_id):
                operation = "job.status"
                arguments = {"job_id": reported_job_id}
            else:
                # The start request may have dispatched before transport loss.
                # Search this project once; a row is attributable only if its
                # request, key, project, and operation all match exactly.
                operation = "job.list"
                arguments = {"limit": 100, "project_id": project_id}
                reported_job_id = None
        elif name == "project.create":
            query_target_request_id = request_ids["project_create"]
            query_target_idempotency_key = keys["project_create"]
            if isinstance(reported_job_id, str) and reported_job_id:
                operation = "job.status"
                arguments = {"job_id": reported_job_id}
            else:
                # The initial route may time out before returning its job ID.
                # Search once by its unique frozen request identity.
                operation = "job.list"
                arguments = {"limit": 100}
                reported_job_id = None
        elif name == "project.create.wait":
            create_action = state.value.get("actions", {}).get("project.create", {})
            create_summary = (create_action.get("response")
                              if isinstance(create_action, Mapping) else None)
            original_job_id = (create_summary.get("job_id")
                               if isinstance(create_summary, Mapping) else None)
            if (isinstance(reported_job_id, str) and reported_job_id
                    and isinstance(original_job_id, str) and reported_job_id != original_job_id):
                state.value["recovery"]["read_only_query"] = {
                    "status": "RESPONSE_JOB_ID_MISMATCH", "action": name,
                    "reported_job_id": reported_job_id,
                    "original_job_id": original_job_id,
                }
                state.save()
                return
            reported_job_id = original_job_id
            query_target_request_id = request_ids["project_create"]
            query_target_idempotency_key = keys["project_create"]
            query_target_project_id = None
            if isinstance(reported_job_id, str) and reported_job_id:
                operation = "job.status"
                arguments = {"job_id": reported_job_id}
            else:
                state.value["recovery"]["read_only_query"] = {
                    "status": "NOT_AVAILABLE_FOR_ACTION", "action": name,
                }
                state.save()
                return
        elif name in {"stage_run", "stage_run_wait"}:
            # A pending experiment.stage_run is an _pending envelope: data has
            # job_id/status while the original request/key/operation live in
            # execution; project_id is intentionally absent. A transport loss
            # before that envelope arrives is reconciled once through a
            # project-scoped job.list query. A job.wait timeout targets the
            # original stage_run job, never the read-only wait request.
            run_action = state.value.get("actions", {}).get("stage_run", {})
            if not isinstance(run_action, Mapping):
                run_action = {}
            original_request = run_action.get("request_id")
            original_key = run_action.get("idempotency_key")
            if (not isinstance(original_request, str) or not original_request
                    or not isinstance(original_key, str) or not original_key
                    or not isinstance(project_id, str) or not project_id):
                state.value["recovery"]["read_only_query"] = {
                    "status": "NOT_AVAILABLE_FOR_ACTION", "action": name,
                    "original_operation": "experiment.stage_run",
                }
                state.save()
                return
            query_target_request_id = original_request
            query_target_idempotency_key = original_key
            query_target_project_id = project_id
            if name == "stage_run_wait":
                wait_action = action_record if isinstance(action_record, Mapping) else {}
                original_job_id = wait_action.get("target_job_id")
                if (not isinstance(original_job_id, str) or not original_job_id
                        or (isinstance(reported_job_id, str) and reported_job_id
                            and reported_job_id != original_job_id)):
                    state.value["recovery"]["read_only_query"] = {
                        "status": "WAIT_TARGET_JOB_ID_MISSING_OR_MISMATCHED", "action": name,
                        "reported_job_id": reported_job_id,
                        "original_job_id": original_job_id,
                    }
                    state.save()
                    return
                reported_job_id = original_job_id
            if isinstance(reported_job_id, str) and reported_job_id:
                operation = "job.status"
                arguments = {"job_id": reported_job_id}
            else:
                operation = "job.list"
                # The logical job.list catalog caps a page at 500 rows. Keep
                # recovery to one read-only page and fail closed if that page
                # does not prove completeness; never over-request the schema.
                arguments = {"limit": 500, "project_id": project_id}
        elif (isinstance(reported_job_id, str) and reported_job_id
                and isinstance(project_id, str) and project_id
                and reported_project_id == project_id):
            operation = "job.status"
            arguments = {"job_id": reported_job_id}
        elif name in {"fixture.execute", "probe.execute", "study.solve",
                      "solution_indices", "result.evaluate"} and isinstance(project_id, str):
            operation = "job.list"
            arguments = {"limit": 100, "project_id": project_id}
        else:
            state.value["recovery"]["read_only_query"] = {
                "status": "NOT_AVAILABLE_FOR_ACTION", "action": name,
            }
            state.save()
            return

        query_id = request_ids.get("unknown_query")
        query_execution = {"request_id": query_id, "rpc_timeout_s": RPC_WAIT_S}
        if isinstance(query_target_project_id, str) and query_target_project_id:
            query_execution["project_id"] = query_target_project_id
        if isinstance(session_id, str):
            query_execution["session_id"] = session_id
        params = _operation_params(operation, arguments, query_execution)
        query_record = {
            "status": "READ_ONLY_QUERY_INTENT", "operation": operation,
            "action": name,
            "params_sha256": sha256_value(params), "request_id": query_id,
            "original_request_id": query_target_request_id,
            "original_idempotency_key": query_target_idempotency_key,
            "original_operation": ("experiment.stage_run" if name in {"stage_run", "stage_run_wait"}
                                    else name),
            "original_project_id": query_target_project_id,
            "job_id": (reported_job_id if isinstance(reported_job_id, str)
                       and name != "session.start" else None),
        }
        if name == "session.start" and operation == "job.status":
            query_record["query_job_id"] = reported_job_id
        if name == "session.start":
            query_record["exact_job_identity_confirmed"] = False
            if operation == "job.list":
                query_record["query_complete"] = False
                query_record["matching_job_rows"] = []
                query_record["match_resolution"] = "NOT_QUERIED"
        state.value["recovery"]["read_only_query"] = query_record
        state.save()
        query_timeout = float(plan.get("budgets", {}).get("ordinary_rpc_wait_seconds", RPC_WAIT_S))
        if deadline is not None:
            query_timeout = min(query_timeout, deadline - CLEANUP_RESERVE_S - clock())
        if query_timeout <= 0:
            query_record.update({"status": "NOT_RUN_BUDGET_RESERVE", "exception_type": None})
            state.save()
            return
        try:
            query_response = await client.call("operation_call", params, timeout_s=query_timeout)
        except BaseException as exc:
            query_record.update({"status": "QUERY_UNKNOWN", "exception_type": type(exc).__name__})
            state.save()
            return

        query_record["response"] = _response_summary(query_response)
        query_record["status"] = "QUERY_RESPONSE_RECORDED"
        query_data = query_response.get("data")
        if operation == "job.status":
            observed_job_id = (query_data.get("job_id")
                               if isinstance(query_data, Mapping) else None)
            if name in {"stage_run", "stage_run_wait"}:
                identity = (_initial_stage_job_identity(query_data)
                            if isinstance(query_data, Mapping) else None)
                exact = bool(
                    query_response.get("success") is True
                    and isinstance(identity, Mapping)
                    and identity.get("job_id") == reported_job_id
                    and identity.get("request_id") == query_target_request_id
                    and identity.get("idempotency_key") == query_target_idempotency_key
                    and identity.get("project_id") == query_target_project_id
                    and identity.get("operation") == "experiment.stage_run"
                )
                query_record["observed_operation"] = (
                    identity.get("operation") if isinstance(identity, Mapping) else None
                )
                query_record["exact_job_identity_confirmed"] = exact
                query_record["match_resolution"] = (
                    "UNIQUE_EXACT_MATCH" if exact else "JOB_STATUS_IDENTITY_MISMATCH"
                )
                if isinstance(identity, Mapping):
                    query_record["observed_job"] = identity
                if exact:
                    query_record["job_id"] = reported_job_id
                    if reported_job_id not in state.value.setdefault("job_ids", []):
                        state.value["job_ids"].append(reported_job_id)
            else:
                observed_operation_record = (query_data.get("operation")
                                             if isinstance(query_data, Mapping) else None)
                observed_operation = (observed_operation_record.get("operation")
                                      if isinstance(observed_operation_record, Mapping)
                                      else observed_operation_record)
                observed_request_id = (observed_operation_record.get("request_id")
                                       if isinstance(observed_operation_record, Mapping) else None)
                observed_idempotency_key = (observed_operation_record.get("idempotency_key")
                                            if isinstance(observed_operation_record, Mapping) else None)
                observed_project_id = (query_data.get("project_id")
                                       if isinstance(query_data, Mapping) else None)
                if name == "session.start":
                    metadata = query_data.get("metadata") if isinstance(query_data, Mapping) else None
                    metadata = metadata if isinstance(metadata, Mapping) else {}
                    operation_metadata = (observed_operation_record.get("metadata")
                                          if isinstance(observed_operation_record, Mapping) else None)
                    operation_metadata = operation_metadata if isinstance(operation_metadata, Mapping) else {}
                    observed_project_candidates = [value for value in (
                        observed_project_id, metadata.get("project_id"),
                        (metadata.get("execution") or {}).get("project_id")
                        if isinstance(metadata.get("execution"), Mapping) else None,
                        operation_metadata.get("project_id"),
                        (operation_metadata.get("execution") or {}).get("project_id")
                        if isinstance(operation_metadata.get("execution"), Mapping) else None,
                    ) if isinstance(value, str) and value]
                    observed_project_id = (
                        observed_project_candidates[0]
                        if observed_project_candidates
                        and len(set(observed_project_candidates)) == 1 else None
                    )
                    exact = bool(
                        query_response.get("success") is True
                        and isinstance(query_target_request_id, str) and query_target_request_id
                        and isinstance(query_target_idempotency_key, str) and query_target_idempotency_key
                        and isinstance(query_target_project_id, str) and query_target_project_id
                        and observed_job_id == reported_job_id
                        and observed_request_id == query_target_request_id
                        and observed_idempotency_key == query_target_idempotency_key
                        and observed_operation == "session.start"
                        and observed_project_id == query_target_project_id
                    )
                    query_record["observed_operation"] = observed_operation
                    query_record["exact_job_identity_confirmed"] = exact
                    if exact:
                        query_record["job_id"] = reported_job_id
                        if reported_job_id not in state.value.setdefault("job_ids", []):
                            state.value["job_ids"].append(reported_job_id)
                else:
                    project_matches = (
                        query_target_project_id is None
                        or (isinstance(query_target_project_id, str) and query_target_project_id
                            and observed_project_id == query_target_project_id)
                    )
                    query_record["exact_job_identity_confirmed"] = bool(
                        isinstance(query_target_request_id, str) and query_target_request_id
                        and isinstance(query_target_idempotency_key, str) and query_target_idempotency_key
                        and observed_job_id == reported_job_id
                        and observed_request_id == query_target_request_id
                        and observed_idempotency_key == query_target_idempotency_key
                        and project_matches)
                query_record["observed_request_id"] = observed_request_id
                query_record["observed_idempotency_key"] = observed_idempotency_key
                query_record["observed_project_id"] = observed_project_id
        if operation == "job.list" and name in {"stage_run", "stage_run_wait"}:
            rows = query_data.get("jobs") if isinstance(query_data, Mapping) else None
            rows = rows if isinstance(rows, list) else []
            total = query_data.get("total") if isinstance(query_data, Mapping) else None
            has_more = query_data.get("has_more") if isinstance(query_data, Mapping) else None
            next_cursor = query_data.get("next_cursor") if isinstance(query_data, Mapping) else None
            query_complete = bool(
                query_response.get("success") is True
                and isinstance(query_data, Mapping)
                and isinstance(query_data.get("jobs"), list)
                and type(total) is int and total == len(rows)
                and has_more is False and next_cursor in (None, "")
            )
            exact_rows = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                identity = _initial_stage_job_identity(row)
                if (isinstance(identity, Mapping)
                        and identity.get("request_id") == query_target_request_id
                        and identity.get("idempotency_key") == query_target_idempotency_key
                        and identity.get("project_id") == query_target_project_id
                        and identity.get("operation") == "experiment.stage_run"):
                    exact_rows.append(identity)
            query_record.update({
                "query_complete": query_complete,
                "matching_job_rows": exact_rows,
                "exact_job_identity_confirmed": False,
                "match_resolution": "INCOMPLETE_QUERY" if not query_complete else "NO_EXACT_MATCH",
            })
            if query_complete and len(exact_rows) == 1:
                matched_job_id = exact_rows[0].get("job_id")
                query_record.update({
                    "job_id": matched_job_id,
                    "exact_job_identity_confirmed": True,
                    "match_resolution": "UNIQUE_EXACT_MATCH",
                })
                if matched_job_id not in state.value.setdefault("job_ids", []):
                    state.value["job_ids"].append(matched_job_id)
            elif query_complete and len(exact_rows) > 1:
                query_record["match_resolution"] = "AMBIGUOUS_EXACT_MATCH"
        elif operation == "job.list" and name == "session.start":
            rows = query_data.get("jobs") if isinstance(query_data, Mapping) else None
            rows = rows if isinstance(rows, list) else []
            has_more = query_data.get("has_more") if isinstance(query_data, Mapping) else None
            next_cursor = query_data.get("next_cursor") if isinstance(query_data, Mapping) else None
            totals = []
            if isinstance(query_data, Mapping):
                totals = [query_data[key] for key in ("total_count", "total")
                          if key in query_data]
            totals_valid = all(type(total) is int and total == len(rows) for total in totals)
            query_complete = bool(
                query_response.get("success") is True
                and isinstance(query_data, Mapping)
                and isinstance(query_data.get("jobs"), list)
                and (has_more is False or (has_more is None and bool(totals)))
                and (next_cursor is None or next_cursor == "")
                and totals_valid
                and (bool(totals) or has_more is False)
            )

            def identity_values(containers: list[Mapping[str, Any]], field: str) -> list[str]:
                values: list[str] = []
                for container in containers:
                    if field not in container:
                        continue
                    value = container.get(field)
                    if value is None:
                        continue
                    if not isinstance(value, str) or not value:
                        return []
                    values.append(value)
                return values

            exact_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                metadata_execution = (metadata.get("execution")
                                      if isinstance(metadata.get("execution"), Mapping) else {})
                operation_record = row.get("operation") if isinstance(row.get("operation"), Mapping) else {}
                operation_metadata = (operation_record.get("metadata")
                                      if isinstance(operation_record.get("metadata"), Mapping) else {})
                operation_execution = (operation_metadata.get("execution")
                                       if isinstance(operation_metadata.get("execution"), Mapping) else {})
                containers = [row, metadata, metadata_execution, operation_record,
                              operation_metadata, operation_execution]
                request_values = identity_values(containers, "request_id")
                idempotency_values = identity_values(containers, "idempotency_key")
                project_values = identity_values(containers, "project_id")
                raw_operation = row.get("operation")
                observed_operation = (raw_operation.get("operation")
                                      if isinstance(raw_operation, Mapping) else raw_operation)
                job_id = row.get("job_id")
                if (request_values and set(request_values) == {query_target_request_id}
                        and idempotency_values
                        and set(idempotency_values) == {query_target_idempotency_key}
                        and project_values and set(project_values) == {query_target_project_id}
                        and observed_operation == "session.start"):
                    exact_rows.append({
                        "job_id": job_id if isinstance(job_id, str) and job_id else None,
                        "status": row.get("status"),
                        "request_id": query_target_request_id,
                        "idempotency_key": query_target_idempotency_key,
                        "project_id": query_target_project_id,
                        "operation": observed_operation,
                    })
            query_record["query_complete"] = query_complete
            query_record["matching_job_rows"] = exact_rows
            query_record["exact_job_identity_confirmed"] = False
            query_record["match_resolution"] = "INCOMPLETE_QUERY" if not query_complete else "NO_EXACT_MATCH"
            if query_complete and len(exact_rows) == 1 and isinstance(exact_rows[0]["job_id"], str):
                job_id = exact_rows[0]["job_id"]
                query_record["job_id"] = job_id
                query_record["exact_job_identity_confirmed"] = True
                query_record["match_resolution"] = "UNIQUE_EXACT_MATCH"
                if job_id not in state.value.setdefault("job_ids", []):
                    state.value["job_ids"].append(job_id)
            elif query_complete and len(exact_rows) > 1:
                query_record["match_resolution"] = "AMBIGUOUS_EXACT_MATCH"
            elif query_complete and len(exact_rows) == 1:
                query_record["match_resolution"] = "MATCH_MISSING_JOB_ID"
        elif operation == "job.list" and isinstance(query_data, Mapping):
            rows = query_data.get("jobs")
            matches = []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
                    execution = metadata.get("execution") if isinstance(metadata.get("execution"), Mapping) else {}
                    operation_record = row.get("operation") if isinstance(row.get("operation"), Mapping) else {}
                    observed_request = (row.get("request_id") or metadata.get("request_id")
                                        or execution.get("request_id") or operation_record.get("request_id"))
                    observed_idempotency = (row.get("idempotency_key")
                                            or metadata.get("idempotency_key")
                                            or execution.get("idempotency_key")
                                            or operation_record.get("idempotency_key"))
                    if (query_target_request_id and observed_request == query_target_request_id
                            and (query_target_idempotency_key is None
                                 or observed_idempotency == query_target_idempotency_key)):
                        matches.append(row)
            query_record["matching_job_rows"] = [
                {key: row.get(key) for key in (
                    "job_id", "status", "request_id", "idempotency_key", "project_id")}
                for row in matches
            ]
            for row in matches:
                job_id = row.get("job_id")
                if isinstance(job_id, str) and job_id and job_id not in state.value.setdefault("job_ids", []):
                    state.value["job_ids"].append(job_id)
        state.value["recovery"]["job_ids"] = list(state.value.get("job_ids", []))
        state.save()

    async def dispatch(name: str, tool: str, params: Mapping[str, Any], *,
                       counted_server_birth=False, counted_worker_birth=False) -> dict[str, Any]:
        nonlocal any_unknown, deadline
        if any_unknown or state.value.get("status") == "UNKNOWN":
            raise RunnerError("UNKNOWN is terminal; no retry, cleanup, or later mutation is allowed")
        cleanup_action = (name in {"session.disconnect", "session.stop"}
                          or name.startswith("failure_cleanup."))
        if deadline is not None:
            limit = deadline if cleanup_action else deadline - CLEANUP_RESERVE_S
            if clock() >= limit:
                raise RunnerError("15-minute birth budget reached its work/cleanup boundary")
        # The request identity and possible birth are durable before MCP can
        # dispatch. A transport failure after this line is never replayed.
        request_id = request_ids.get(name)
        idempotency_key = keys.get(name)
        operation_arguments = params.get("arguments")
        execution_binding = params.get("execution")
        for fields in (execution_binding, operation_arguments):
            if isinstance(fields, Mapping):
                if request_id is None and isinstance(fields.get("request_id"), str):
                    request_id = fields["request_id"]
                if idempotency_key is None and isinstance(fields.get("idempotency_key"), str):
                    idempotency_key = fields["idempotency_key"]
        intent = {"tool": tool, "params_sha256": sha256_value(params),
                  "request_id": request_id, "idempotency_key": idempotency_key}
        if name == "stage_run_wait":
            intent["target_job_id"] = (
                operation_arguments.get("job_id")
                if isinstance(operation_arguments, Mapping) else None
            )
        state.value["actions"] = state.value.get("actions", {})
        if name in state.value["actions"]:
            raise RunnerError(f"action already has a durable intent: {name}")
        initial_stage_action = (mode == INITIAL_STAGE_MODE
                                and name in INITIAL_STAGE_ACTION_COUNTERS)
        stage_action = name in STAGE_READ_ACTION_COUNTERS or initial_stage_action
        if initial_stage_action:
            frozen_operation = next((row for row in plan.get("initial_stage_operations", [])
                                     if isinstance(row, Mapping)
                                     and row.get("operation_id") == params.get("operation_id")), None)
            expected_operation = ("experiment.stage_define" if name == "stage_define"
                                  else "experiment.stage_run" if name == "stage_run" else None)
            if (expected_operation is not None
                    and (tool != "operation_call" or not isinstance(frozen_operation, Mapping)
                         or params.get("operation_id") != expected_operation
                         or params.get("arguments") != frozen_operation.get("arguments")
                         or frozen_operation.get("arguments_sha256") != sha256_value({
                             "operation_id": expected_operation,
                             "arguments": frozen_operation.get("arguments"),
                         }))):
                raise RunnerError(f"{name} differs from its exact frozen public operation and arguments")
            if deadline is None:
                raise RunnerError(f"{name} cannot dispatch before the owned Server birth starts the run budget")
        if stage_action:
            execution = params.get("execution")
            budgets = plan.get("budgets", {})
            queue_timeout = execution.get("queue_timeout_s") if isinstance(execution, Mapping) else None
            execution_timeout = execution.get("execution_timeout_s") if isinstance(execution, Mapping) else None
            if name == "stage_run":
                expected_queue = budgets.get("stage_run_queue_timeout_seconds")
                expected_execution = budgets.get("stage_run_execution_timeout_seconds")
            else:
                expected_queue = budgets.get("queue_timeout_seconds")
                expected_execution = budgets.get("execution_timeout_seconds")
            if (not stage_action or (mode == SOLVE_READBACK_MODE and deadline is None)
                    or (mode == INITIAL_STAGE_MODE and name == "stage_run"
                        and (deadline is None or type(queue_timeout) is not int
                             or type(execution_timeout) is not int
                             or queue_timeout != expected_queue
                             or execution_timeout != expected_execution))
                    or (mode == SOLVE_READBACK_MODE
                        and (type(queue_timeout) is not int or type(execution_timeout) is not int
                             or queue_timeout != expected_queue or execution_timeout != expected_execution))):
                state.value["actions"][name] = {
                    "status": "NOT_DISPATCHED_SERVER_BUDGET", **intent,
                }
                record_stage_dispatch(name, "NOT_DISPATCHED_BUDGET")
                state.save()
                raise RunnerError(f"{name} lacks its exact frozen server execution budget")
            if name in {"stage.solve", "study.solve", "solution_indices", "result.evaluate", "stage_run"}:
                remaining_server_window = deadline - CLEANUP_RESERVE_S - clock()
            else:
                remaining_server_window = float("inf")
            if ((mode == SOLVE_READBACK_MODE or name == "stage_run")
                    and queue_timeout + execution_timeout > remaining_server_window):
                state.value["actions"][name] = {
                    "status": "NOT_DISPATCHED_SERVER_BUDGET", **intent,
                    "server_queue_timeout_s": queue_timeout,
                    "server_execution_timeout_s": execution_timeout,
                    "remaining_work_window_s": max(0.0, remaining_server_window),
                }
                record_stage_dispatch(name, "NOT_DISPATCHED_BUDGET")
                state.save()
                raise RunnerError(f"{name} server queue+execution budget exceeds the remaining work window")
        state.value["actions"][name] = {"status": "DISPATCH_INTENT", **intent}
        if stage_action:
            record_stage_dispatch(name, "DISPATCH_INTENT", possible=True)
        if counted_server_birth:
            state.value["server_births_possible"] = 1
            deadline = clock() + RUN_BUDGET_S
        if counted_worker_birth:
            state.value["worker_births_possible"] = 1
        state.save()
        rpc_timeout = float(plan.get("budgets", {}).get("ordinary_rpc_wait_seconds", RPC_WAIT_S))
        if mode == INITIAL_STAGE_MODE and name == "stage_run_wait":
            rpc_timeout = float(plan["budgets"]["stage_run_result_wait_seconds"])
        if deadline is not None:
            limit = deadline if cleanup_action else deadline - CLEANUP_RESERVE_S
            rpc_timeout = min(rpc_timeout, limit - clock())
        if rpc_timeout <= 0:
            state.value["actions"][name]["status"] = "NOT_DISPATCHED_BUDGET"
            record_stage_dispatch(name, "NOT_DISPATCHED_BUDGET")
            if stage_action:
                for counter in (STAGE_READ_ACTION_COUNTERS.get(name, ())
                                + INITIAL_STAGE_ACTION_COUNTERS.get(name, ())):
                    state.value[f"{counter}_possible"] = 0
            state.save()
            raise RunnerError("remaining W21 birth window cannot cover another bounded RPC")
        try:
            response = await client.call(tool, params, timeout_s=rpc_timeout)
        except BaseException as exc:
            state.value["actions"][name].update({"status": "UNKNOWN", "exception_type": type(exc).__name__})
            record_stage_dispatch(name, "UNKNOWN")
            state.value["status"] = "UNKNOWN"
            state.value["unknown_action"] = name
            state.value["recovery"] = {"request_id": request_id,
                                        "idempotency_key": idempotency_key, "job_ids": list(state.value.get("job_ids", [])),
                                        "replay_permitted": False, "cleanup_permitted": False}
            state.save()
            any_unknown = True
            await query_unknown_once(name, request_id, None)
            raise RunnerError(f"{name} transport outcome is UNKNOWN; original IDs preserved") from None
        summary = _response_summary(response)
        state.value["actions"][name]["response"] = summary
        job_id = summary.get("job_id")
        unknown_response = (_is_unknown(response)
                            or (mode == INITIAL_STAGE_MODE and name == "stage_run_wait"
                                and _initial_stage_wait_expired(response)))
        if (isinstance(job_id, str) and job_id
                and not (name == "session.start" and unknown_response)
                and job_id not in state.value.setdefault("job_ids", [])):
            state.value["job_ids"].append(job_id)
        if unknown_response:
            state.value["actions"][name]["status"] = "UNKNOWN"
            record_stage_dispatch(name, "UNKNOWN")
            state.value["status"] = "UNKNOWN"
            state.value["unknown_action"] = name
            state.value["recovery"] = {"request_id": request_id,
                                        "idempotency_key": idempotency_key, "job_ids": list(state.value["job_ids"]),
                                        "replay_permitted": False, "cleanup_permitted": False}
            state.save()
            any_unknown = True
            await query_unknown_once(name, request_id, response)
            raise RunnerError(f"{name} returned UNKNOWN; no later mutation is allowed")
        state.value["actions"][name]["status"] = "RESPONSE_RECORDED"
        record_stage_dispatch(name, "RESPONSE_RECEIVED")
        state.save()
        return response

    def check_budget_cleanup() -> None:
        if deadline is not None and clock() >= deadline:
            raise RunnerError("15-minute Server/Worker birth budget expired")

    async def cleanup_after_deterministic_failure() -> None:
        existing = state.value.get("actions", {})
        if any(name in existing for name in (
                "session.disconnect", "session.stop",
                "failure_cleanup.session_disconnect", "failure_cleanup.session_stop")):
            state.value["failure_cleanup"] = {"status": "NOT_RETRIED_EXISTING_CLEANUP_INTENT"}
            state.save()
            return
        if (not owned_session_verified or not isinstance(project_id, str)
                or not isinstance(session_id, str)):
            state.value["failure_cleanup"] = {"status": "NOT_POSSIBLE_UNVERIFIED_SESSION_BINDING"}
            state.save()
            return

        cleanup = {"status": "DISCONNECT_INTENT"}
        state.value["failure_cleanup"] = cleanup
        state.save()
        try:
            disconnected_response = await dispatch("failure_cleanup.session_disconnect", "operation_call",
                _operation_params("session.disconnect", {
                    "project_id": project_id, "session_id": session_id, "retire_worker": True,
                    "idempotency_key": keys["failure_session_disconnect"],
                    "request_id": request_ids["failure_session_disconnect"],
                }, {"project_id": project_id, "session_id": session_id,
                    "idempotency_key": keys["failure_session_disconnect"],
                    "request_id": request_ids["failure_session_disconnect"],
                    "rpc_timeout_s": RPC_WAIT_S}))
            disconnected = _assert_success(disconnected_response, "failure cleanup session.disconnect")
            if (disconnected.get("project_id") != project_id
                    or disconnected.get("session_id") != session_id
                    or disconnected.get("worker_handle_preserved") is not False):
                raise RunnerError("failure cleanup lacked exact Worker retirement proof")
            cleanup.update({"status": "STOP_INTENT", "worker_retired": True})
            state.save()
            stopped_response = await dispatch("failure_cleanup.session_stop", "operation_call",
                _operation_params("session.stop", {
                    "project_id": project_id, "session_id": session_id,
                    "authorization_ref": f"Task-scoped W21 {plan.get('mode', METADATA_MODE)} cleanup {plan['run_id']}",
                    "idempotency_key": keys["failure_session_stop"],
                    "request_id": request_ids["failure_session_stop"],
                }, {"project_id": project_id, "session_id": session_id,
                    "idempotency_key": keys["failure_session_stop"],
                    "request_id": request_ids["failure_session_stop"],
                    "rpc_timeout_s": RPC_WAIT_S}))
            stopped = _assert_success(stopped_response, "failure cleanup session.stop")
            if (stopped.get("project_id") != project_id
                    or stopped.get("session_id") != session_id
                    or stopped.get("state") != "STOPPED"
                    or stopped.get("server_stopped") is not True):
                raise RunnerError("failure cleanup lacked exact owned Server stop proof")
            cleanup.update({"status": "CLEANUP_COMPLETE", "server_stopped": True})
            isolation_path = Path(plan["isolation_receipt"])
            if isolation_path.is_file() and not isolation_path.is_symlink():
                _mark_isolation_receipt_stopped(isolation_path)
            state.save()
        except Exception as cleanup_error:
            cleanup["status"] = ("UNKNOWN_NO_FURTHER_ACTION" if any_unknown
                                  else "INCOMPLETE_NO_RETRY")
            cleanup["error_type"] = type(cleanup_error).__name__
            state.save()

    state.value["status"] = "RUNNING"
    state.save()
    try:
        # The fixed helper returns only process Name/PID/missing flags and ran
        # before the first MCP request above.
        state.value["process_preflight"] = preflight_records

        env_project = {"request_id": request_ids["project_create"],
                       "idempotency_key": keys["project_create"]}
        create_response = await dispatch("project.create", "operation_call", _operation_params(
            "project.create", {"label": f"W21 {plan['requested_version']} field probe {plan['run_id']}",
                                "workspace": str(project_workspace.relative_to(workspace_root)),
                                "policy": {"permissions": _project_policy_permissions(mode)},
                                "idempotency_key": keys["project_create"],
                                "request_id": request_ids["project_create"]}, env_project))
        async def wait_for_project_create(job_id: str) -> Mapping[str, Any]:
            wait_id = request_ids["project_create_wait"]
            timeout_s = plan.get("budgets", {}).get("project_create_wait_timeout_seconds")
            max_waits = plan.get("budgets", {}).get("project_create_wait_calls_max")
            if (type(timeout_s) is not int or not 1 <= timeout_s <= RPC_WAIT_S
                    or max_waits != 1):
                raise RunnerError("frozen project.create wait budget is invalid")
            arguments = {"job_id": job_id, "timeout_s": timeout_s, "poll_interval_s": 0.05}
            return await dispatch("project.create.wait", "operation_call", _operation_params(
                "job.wait", arguments, {"request_id": wait_id, "rpc_timeout_s": RPC_WAIT_S}))

        project_record = await _resolve_project_create_response(
            create_response, request_id=request_ids["project_create"],
            idempotency_key=keys["project_create"], wait_for_job=wait_for_project_create)
        project_id = project_record.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise RunnerError("project.create omitted its minted project_id")
        if (project_record.get("workspace") != str(project_workspace.resolve())
                or not project_workspace.is_dir() or project_workspace.is_symlink()):
            raise RunnerError("project.create did not create the exact task-owned workspace")

        start_args = {"project_id": project_id, "runtime_id": plan["selected_comsol"]["runtime_id"],
                      "options": {}, "resources": {}, "idempotency_key": keys["session_start"],
                      "request_id": request_ids["session_start"]}
        start_response = await dispatch("session.start", "operation_call",
            _operation_params("session.start", start_args,
                              {"project_id": project_id, "request_id": request_ids["session_start"],
                               "idempotency_key": keys["session_start"],
                               "rpc_timeout_s": RPC_WAIT_S}), counted_server_birth=True)
        started = _assert_success(start_response, "session.start")
        session_id = started.get("session_id")
        endpoint = started.get("endpoint")
        server_identity = started.get("server_process_identity")
        if (started.get("project_id") != project_id or started.get("runtime_id") != plan["selected_comsol"]["runtime_id"]
                or started.get("server_ownership") != "mcp_managed" or started.get("loopback_only_verified") is not True
                or not isinstance(session_id, str) or not session_id
                or not isinstance(endpoint, Mapping) or endpoint.get("host") != "127.0.0.1"
                or type(endpoint.get("port")) is not int or not isinstance(server_identity, Mapping)):
            raise RunnerError("session.start did not return an exact owned loopback Server/session identity")
        owned_session_verified = True

        connect_args = {"project_id": project_id, "session_id": session_id,
                        "runtime_id": plan["selected_comsol"]["runtime_id"], "endpoint": dict(endpoint),
                        "idempotency_key": keys["session_connect"], "request_id": request_ids["session_connect"]}
        connect_response = await dispatch("session.connect", "operation_call",
            _operation_params("session.connect", connect_args,
                              {"project_id": project_id, "session_id": session_id,
                               "request_id": request_ids["session_connect"],
                               "idempotency_key": keys["session_connect"], "rpc_timeout_s": RPC_WAIT_S}),
            counted_worker_birth=True)
        connected = dict(_assert_success(connect_response, "session.connect"))
        worker_epoch = connected.get("worker_epoch")
        server_instance = connected.get("server_instance_id")
        if (connected.get("project_id") != project_id or connected.get("session_id") != session_id
                or connected.get("endpoint") != dict(endpoint) or connected.get("server_ownership") != "mcp_managed"
                or not isinstance(server_instance, str) or not server_instance
                or not isinstance(connected.get("worker_instance_id"), str)
                or type(worker_epoch) is not int or worker_epoch <= 0):
            raise RunnerError("session.connect identity/version/build differs from the frozen Server/runtime")
        remote_engine_identity = _resolve_remote_comsol_identity(
            connected, plan["selected_comsol"],
        )

        # model_create has no ModelRef yet, but is explicitly session-scoped.
        model_response = await dispatch("model_create", "model_create", {
            "name": f"W21_{plan['run_id']}",
            "execution": {"project_id": project_id, "session_id": session_id,
                          "request_id": request_ids["model_create"]},
        })
        binding = validate_model_binding(model_response, project_id=project_id,
                                         session_id=session_id, server_instance_id=server_instance,
                                         worker_epoch=worker_epoch)
        inspect_response = await dispatch("model.inspect", "operation_call", _operation_params(
            "model.inspect", {"detail": "summary"},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "request_id": request_ids["model_inspect_before_fixture"],
             "rpc_timeout_s": RPC_WAIT_S}))
        binding["revision"] = _validate_model_inspect(inspect_response, binding)

        fixture_dir = project_workspace / "fixtures"
        fixture_dir.mkdir(mode=0o700)
        staged: dict[str, str] = {}
        staged_relative: dict[str, str] = {}
        source_items = (("W21Fixture.java", FIXTURE), ("W21FieldIdentityProbe.java", PROBE))
        for short_name, source in source_items:
            source_hash = plan["source_manifest"][f"tools/java/{short_name}"]
            if sha256_file(source) != source_hash:
                raise RunnerError("Java source changed after prepare; no execution is allowed")
            destination = fixture_dir / short_name
            shutil.copyfile(source, destination)
            if sha256_file(destination) != source_hash:
                raise RunnerError("project-local Java source copy failed exact hash verification")
            staged[short_name] = str(destination)
            staged_relative[short_name] = _project_relative_file(project_workspace, destination)

        async def register_source(label: str, key: str, request_id: str,
                                  absolute_path: str, relative_path: str) -> dict[str, Any]:
            response = await dispatch(label, "operation_call", _operation_params(
                "artifact.register", {"project_id": project_id, "path": relative_path,
                    "role": "w21_probe_source", "classification": "task_owned_frozen_java_source",
                    "idempotency_key": key, "request_id": request_id},
                {"project_id": project_id, "session_id": session_id,
                 "idempotency_key": key, "request_id": request_id,
                 "rpc_timeout_s": RPC_WAIT_S}))
            result = _assert_success(response, label)
            if result.get("sha256") != sha256_file(Path(absolute_path)):
                raise RunnerError("artifact.register hash differs from staged frozen Java source")
            return response

        await register_source("fixture.register", keys["fixture_register"],
                              request_ids["fixture_register"], staged["W21Fixture.java"],
                              staged_relative["W21Fixture.java"])

        # Production code.execute_java requires an independently checked owned
        # Server isolation receipt. It is configured before the stdio child
        # starts; this bridge records only the exact start response and an OS
        # command hash, never a process command line.
        _write_isolation_receipt(Path(plan["isolation_receipt"]), server_identity, endpoint)

        fixture_response = await dispatch("fixture.execute", "operation_call", _operation_params(
            "code.execute_java", {"source_artifact": staged_relative["W21Fixture.java"],
                "entrypoint": "W21Fixture", "arguments": {}, "mode": "trusted", "timeout_s": 240},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "idempotency_key": keys["fixture_execute"],
             "request_id": request_ids["fixture_execute"], "rpc_timeout_s": RPC_WAIT_S}))
        fixture_data = _assert_success(fixture_response, "code.execute_java(W21Fixture)")
        fixture_readback, fixture_execution_proof = _validate_java_execution_data(
            fixture_data, expected_source_sha256=plan["fixture_sha256"],
            expected_entrypoint="W21Fixture", expected_model_tag=binding["model_tag"],
            label="fixture",
        )
        if not isinstance(fixture_readback, Mapping) or fixture_readback.get("status") != "BUILT_NOT_SOLVED":
            raise RunnerError("fixture did not prove BUILT_NOT_SOLVED")
        fixture_execution = fixture_response.get("execution")
        if (not isinstance(fixture_execution, Mapping)
                or fixture_execution.get("project_id") != project_id
                or fixture_execution.get("session_id") != session_id
                or fixture_execution.get("model_ref") != binding["model_ref"]
                or type(fixture_execution.get("revision")) is not int
                or fixture_execution["revision"] != binding["revision"] + 1):
            raise RunnerError("fixture reply omitted its exact model binding or resulting revision")
        binding["revision"] = fixture_execution["revision"]
        state.value["geometry_run"] = 1
        state.value["mesh_run"] = 1
        state.save()

        after_fixture = await dispatch("model.inspect.after_fixture", "operation_call", _operation_params(
            "model.inspect", {"detail": "summary"},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": binding["revision"], "request_id": request_ids["model_inspect_after_fixture"],
             "rpc_timeout_s": RPC_WAIT_S}))
        binding["revision"] = _validate_model_inspect(after_fixture, binding)
        mode = plan.get("mode", METADATA_MODE)
        probe: dict[str, Any] | None = None
        probe_observation: dict[str, Any] | None = None
        solve_readback = None
        tuple_readback = None

        # Capture the same bounded, read-only Java metadata in both modes so
        # the solve-readback receipt can bind exploratory model structure to
        # the exact model revision used by the subsequent solve. The probe
        # never runs geometry, mesh, a Study, or a result field read.
        await register_source("probe.register", keys["probe_register"],
                              request_ids["probe_register"], staged["W21FieldIdentityProbe.java"],
                              staged_relative["W21FieldIdentityProbe.java"])
        probe_revision_before = binding["revision"]
        probe_request = _frozen_probe_request(plan)
        probe_arguments = {"expected_model_tag": binding["model_tag"], "physics_tag": "ht"}
        probe_arguments.update(_worker_probe_args(probe_request))
        probe_response = await dispatch("probe.execute", "operation_call", _operation_params(
            "code.execute_java", {"source_artifact": staged_relative["W21FieldIdentityProbe.java"],
                "entrypoint": "W21FieldIdentityProbe", "arguments": probe_arguments,
                "mode": "trusted", "timeout_s": 240},
            {"project_id": project_id, "session_id": session_id, "model_ref": binding["model_ref"],
             "expected_revision": probe_revision_before, "idempotency_key": keys["probe_execute"],
             "request_id": request_ids["probe_execute"], "rpc_timeout_s": RPC_WAIT_S}))
        probe_data = _assert_success(probe_response, "code.execute_java(W21FieldIdentityProbe)")
        probe_payload, probe_execution_proof = _validate_java_execution_data(
            probe_data, expected_source_sha256=plan["probe_sha256"],
            expected_entrypoint="W21FieldIdentityProbe", expected_model_tag=binding["model_tag"],
            label="field identity probe",
        )
        probe_execution = probe_response.get("execution")
        if (not isinstance(probe_execution, Mapping)
                or probe_execution.get("project_id") != project_id
                or probe_execution.get("session_id") != session_id
                or probe_execution.get("model_ref") != binding["model_ref"]
                or type(probe_execution.get("revision")) is not int
                or probe_execution.get("revision") != probe_revision_before + 1):
            raise RunnerError("probe reply omitted the exact project/session/ModelRef/revision")
        binding["revision"] = probe_execution["revision"]
        parsed_probe = _parse_field_identity_probe(
            probe_payload, expected_model_tag=binding["model_tag"],
            expected_request=probe_request,
            allow_table_row_limit=(mode == SOLVE_READBACK_MODE),
        )
        if (not parsed_probe["metadata_complete"] and probe_request["mode"] == "full"
                and not (mode == SOLVE_READBACK_MODE and parsed_probe["capture_kind"] == "table_limit")):
            raise RunnerError("probe output is incomplete or claims a scope outside metadata capture")
        probe = parsed_probe["payload"]
        probe_observation = {
            "status": "RAW_UNINTERPRETED" if mode == SOLVE_READBACK_MODE
                      else "CAPTURED_ONLY_NOT_ADMISSION",
            "source_sha256": plan["probe_sha256"],
            "raw_payload_sha256": parsed_probe["raw_payload_sha256"],
            "semantic_payload_sha256": sha256_value(probe),
            "raw_payload_bytes": parsed_probe["raw_payload_bytes"],
            "request_scope": dict(probe_request),
            "project_id": project_id, "session_id": session_id,
            "model_ref": dict(binding["model_ref"]),
            "revision": probe_execution["revision"],
            "request_id": request_ids["probe_execute"],
            "idempotency_key": keys["probe_execute"],
            "worker_execution": probe_execution_proof,
            "capture_kind": parsed_probe["capture_kind"],
            "capture_status": probe.get("status"),
            "capture_completeness": "COMPLETE" if parsed_probe["metadata_complete"] else "PARTIAL",
            "metadata_complete": parsed_probe["metadata_complete"],
        }
        if not parsed_probe["metadata_complete"]:
            probe_observation["capture_code"] = probe.get("code")
        if mode == SOLVE_READBACK_MODE:
            probe_observation["raw_payload_json"] = parsed_probe["raw_payload_json"]
        state.value["probe_capture"] = {
            "status": "RAW_UNINTERPRETED" if mode == SOLVE_READBACK_MODE else "CAPTURED",
            "source_sha256": plan["probe_sha256"],
            "raw_payload_sha256": parsed_probe["raw_payload_sha256"],
            "semantic_payload_sha256": sha256_value(probe),
            "project_id": project_id, "session_id": session_id,
            "model_ref": dict(binding["model_ref"]),
            "revision": probe_execution["revision"],
            "request_id": request_ids["probe_execute"],
            "idempotency_key": keys["probe_execute"],
            "worker_execution": probe_execution_proof,
            "request_scope": dict(probe_request),
            "capture_kind": parsed_probe["capture_kind"],
            "capture_status": probe.get("status"),
            "capture_completeness": "COMPLETE" if parsed_probe["metadata_complete"] else "PARTIAL",
            "metadata_complete": parsed_probe["metadata_complete"],
        }
        if not parsed_probe["metadata_complete"]:
            state.value["probe_capture"]["capture_code"] = probe.get("code")
        if mode == SOLVE_READBACK_MODE:
            # Keep the raw bounded JSON durable before any solve intent can be
            # written. If the later solve becomes UNKNOWN, this observation
            # remains evidence but never changes admission status.
            state.value["probe_capture"]["raw_payload_json"] = parsed_probe["raw_payload_json"]
        else:
            state.value["probe_capture"]["payload_sha256"] = sha256_value(probe)
        state.save()

        if mode == METADATA_MODE:
            status = "FIELD_PROBE_CAPTURED_ONLY_NOT_ADMISSION"
        elif mode == SOLVE_READBACK_MODE:
            budgets = plan["budgets"]
            if (budgets.get("study_dispatch") != 1 or budgets.get("solver_dispatch") != 1
                    or budgets.get("solution_tuple_reads") != 1 or budgets.get("field_reads") != 1
                    or budgets.get("queue_timeout_seconds") != 30
                    or budgets.get("execution_timeout_seconds") != 240
                    or budgets.get("ordinary_rpc_wait_seconds") != 45
                    or budgets.get("cleanup_reserve_seconds") != 60):
                raise RunnerError("frozen solve-readback action budgets differ from the runner contract")
            solve_request = request_ids["study_solve"]
            solve_key = keys["study_solve"]
            solve_revision_before = binding["revision"]
            solve_params = {
                "study_tag": "std1",
                "execution": {
                    "project_id": project_id, "session_id": session_id,
                    "model_ref": binding["model_ref"], "expected_revision": solve_revision_before,
                    "request_id": solve_request, "idempotency_key": solve_key,
                    "queue_timeout_s": budgets["queue_timeout_seconds"],
                    "execution_timeout_s": budgets["execution_timeout_seconds"],
                    "rpc_timeout_s": budgets["ordinary_rpc_wait_seconds"],
                },
            }
            solve_response = await dispatch("study.solve", "run_study", solve_params)
            solve_data = _assert_success(solve_response, "run_study(std1)")
            if solve_data.get("study_tag") != "std1":
                raise RunnerError("public run_study did not confirm the frozen std1 target")
            solve_ticket = _validate_managed_result_ticket(
                solve_response, binding, request_id=solve_request, idempotency_key=solve_key,
                expected_revision=solve_revision_before, label="run_study(std1)",
                allow_one_revision_advance=True,
            )
            if solve_ticket["revision"] != solve_revision_before + 1:
                raise RunnerError("run_study(std1) did not advance the exact ModelRef revision once")
            binding["revision"] = solve_ticket["revision"]
            record_stage_dispatch("study.solve", "CONFIRMED", confirmed=True)
            state.value["study_solve_progress"] = {
                "status": "CONFIRMED",
                "request_id": solve_request, "idempotency_key": solve_key,
                "operation_id": solve_ticket["operation_id"],
                "request_hash": solve_ticket["request_hash"], "job_id": solve_ticket["job_id"],
                "revision_before": solve_revision_before,
                "revision_after": solve_ticket["revision"],
            }
            state.save()

            tuple_request = request_ids["solution_indices"]
            tuple_key = keys["solution_indices"]
            tuple_params = _operation_params("dataset.solution_indices", {"path": "dset1"}, {
                "project_id": project_id, "session_id": session_id,
                "model_ref": binding["model_ref"], "expected_revision": binding["revision"],
                "request_id": tuple_request, "idempotency_key": tuple_key,
                "queue_timeout_s": budgets["queue_timeout_seconds"],
                "execution_timeout_s": budgets["execution_timeout_seconds"],
                "rpc_timeout_s": budgets["ordinary_rpc_wait_seconds"],
            })
            tuple_response = await dispatch("solution_indices", "operation_call", tuple_params)
            tuple_data = _assert_success(tuple_response, "dataset.solution_indices(dset1)")
            tuple_ticket = _validate_managed_result_ticket(
                tuple_response, binding, request_id=tuple_request, idempotency_key=tuple_key,
                expected_revision=binding["revision"], label="dataset.solution_indices(dset1)",
            )
            target_solution = tuple_data.get("solution")
            if not isinstance(target_solution, str) or not target_solution:
                raise RunnerError("dataset.solution_indices omitted the actual solution tag")
            resolved_tuple = _resolve_solve_tuple(tuple_data, dataset="dset1")
            selected_tuple, exact_binding = resolved_tuple
            if selected_tuple.get("solution") != target_solution:
                raise RunnerError("dataset.solution_indices tuple resolved against another solution tag")
            record_stage_dispatch("solution_indices", "CONFIRMED", confirmed=True)
            state.value["solution_tuple_readback_progress"] = {
                "status": "VERIFIED", "dataset": "dset1", "solution": target_solution,
                "tuple": {key: selected_tuple[key] for key in ("outer", "inner", "solnum")},
                "tuple_binding_sha256": sha256_value(exact_binding),
                "request_id": tuple_request, "idempotency_key": tuple_key,
                "operation_id": tuple_ticket["operation_id"],
                "request_hash": tuple_ticket["request_hash"], "job_id": tuple_ticket["job_id"],
                "revision": tuple_ticket["revision"],
            }
            state.save()

            result_request = request_ids["result_evaluate"]
            result_key = keys["result_evaluate"]
            result_revision_before = binding["revision"]
            expressions = list(_field_readback_expressions(field_profile or SINGLE_FIELD_PROFILE))
            result_params = _operation_params("result.evaluate", {
                "spec": {
                    "expressions": expressions,
                    "solution": {"dataset": "dset1", "solution": target_solution},
                    "aggregate": "none", "complex_mode": "preserve", "storage": "inline",
                },
            }, {
                "project_id": project_id, "session_id": session_id,
                "model_ref": binding["model_ref"], "expected_revision": result_revision_before,
                "request_id": result_request, "idempotency_key": result_key,
                "queue_timeout_s": budgets["queue_timeout_seconds"],
                "execution_timeout_s": budgets["execution_timeout_seconds"],
                "rpc_timeout_s": budgets["ordinary_rpc_wait_seconds"],
            })
            result_response = await dispatch("result.evaluate", "operation_call", result_params)
            result_data = _assert_success(result_response, "result.evaluate(field profile)")
            result_ticket = _validate_managed_result_ticket(
                result_response, binding, request_id=result_request, idempotency_key=result_key,
                expected_revision=result_revision_before, label="result.evaluate(field profile)",
                allow_one_revision_advance=True,
            )
            cleanup = result_data.get("cleanup")
            if (not isinstance(cleanup, Mapping) or cleanup.get("cleanup_failed") is not False
                    or result_data.get("execution_state_unknown") is True):
                raise RunnerError("result.evaluate temporary-node cleanup is incomplete or unknown")
            solve_readback, exact_binding = _extract_solve_field(
                tuple_data, result_data, dataset="dset1", resolved_tuple=resolved_tuple,
                field_readback_profile=field_profile or SINGLE_FIELD_PROFILE,
            )
            if exact_binding.get("selection_resolution", {}).get("status") != "VERIFIED":
                raise RunnerError("result field tuple did not retain its exact SolutionInfo selection witness")
            binding["revision"] = result_ticket["revision"]
            record_stage_dispatch("result.evaluate", "CONFIRMED", confirmed=True)
            state.value["field_readback_progress"] = {
                "status": "VERIFIED", "observation_ref": solve_readback["observation_ref"],
                "field_sha256": solve_readback["full_dataset_field_sha256"],
                "selected_tuple_field_sha256": solve_readback["field_values_sha256"],
                "request_id": result_request, "idempotency_key": result_key,
                "operation_id": result_ticket["operation_id"],
                "request_hash": result_ticket["request_hash"], "job_id": result_ticket["job_id"],
                "revision_before": result_revision_before,
                "revision_after": result_ticket["revision"],
            }
            if field_profile == AUTO_UNIT_CONTROLS_PROFILE:
                state.value["field_readback_progress"].update({
                    "field_readback_profile": field_profile,
                    "unit_controls_sha256": sha256_value(solve_readback["unit_controls"]),
                    "unit_controls_status": solve_readback["unit_controls"]["status"],
                })
            state.save()
            tuple_readback = {
                "status": "VERIFIED", "dataset": "dset1", "solution": target_solution,
                "target_tuple": solve_readback["tuple"],
                "source": exact_binding["selection_resolution"]["source"],
                "operation": {
                    "request_id": tuple_request, "idempotency_key": tuple_key,
                    "operation_id": tuple_ticket["operation_id"],
                    "request_hash": tuple_ticket["request_hash"], "job_id": tuple_ticket["job_id"],
                    "revision": tuple_ticket["revision"],
                    "raw_binding_sha256": sha256_value(tuple_data),
                },
            }
            result_evidence = {
                "project_id": project_id, "session_id": session_id,
                "model_ref": dict(binding["model_ref"]),
                "request_id": result_request, "idempotency_key": result_key,
                "operation_id": result_ticket["operation_id"],
                "request_hash": result_ticket["request_hash"], "job_id": result_ticket["job_id"],
                "revision_before": result_revision_before,
                "revision_after": result_ticket["revision"],
                "worker_observation_ref": solve_readback["observation_ref"],
                "field_sha256": solve_readback["full_dataset_field_sha256"],
                "selected_tuple_field_sha256": solve_readback["field_values_sha256"],
            }
            for field_readback in solve_readback.get("field_readbacks", {}).values():
                field_readback["field_operation"] = dict(result_evidence)
            solve_readback.update({
                "status": "CAPTURED", "study_tag": "std1",
                "field_readback_profile": field_profile,
                "field_sha256": solve_readback["full_dataset_field_sha256"],
                "solve_operation": {
                    "request_id": solve_request, "idempotency_key": solve_key,
                    "operation_id": solve_ticket["operation_id"],
                    "request_hash": solve_ticket["request_hash"], "job_id": solve_ticket["job_id"],
                    "revision_before": solve_revision_before,
                    "revision_after": solve_ticket["revision"],
                },
                "tuple_readback": tuple_readback,
                "field_operation": result_evidence,
                "cleanup": dict(cleanup),
            })
            status = "SOLVE_READBACK_CAPTURED_ONLY_NOT_ADMISSION"
            state.value["solve_readback_capture"] = {
                "status": "CAPTURED", "field_sha256": solve_readback["full_dataset_field_sha256"],
                "selected_tuple_field_sha256": solve_readback["field_values_sha256"],
                "tuple": solve_readback["tuple"], "request_id": result_request,
                "operation_id": result_ticket["operation_id"],
            }
            state.save()
        elif mode == INITIAL_STAGE_MODE:
            budgets = plan["budgets"]
            if (budgets.get("study_dispatch") != 1 or budgets.get("solver_dispatch") != 1
                    or budgets.get("stage_define_dispatch") != 1
                    or budgets.get("stage_run_dispatch") != 1
                    or budgets.get("stage_run_wait_calls_max") != 1
                    or budgets.get("stage_run_queue_timeout_seconds") != 30
                    or budgets.get("stage_run_execution_timeout_seconds") != 240
                    or budgets.get("stage_run_rpc_wait_seconds") != RPC_WAIT_S
                    or budgets.get("stage_run_result_wait_seconds") != 240
                    or budgets.get("cleanup_reserve_seconds") != CLEANUP_RESERVE_S):
                raise RunnerError("frozen initial-stage action budgets differ from the bounded runner contract")
            _validate_frozen_initial_stage(plan)
            stage_define_spec, stage_run_spec = plan["initial_stage_operations"]
            if (stage_define_spec.get("operation_id") != "experiment.stage_define"
                    or stage_run_spec.get("operation_id") != "experiment.stage_run"):
                raise RunnerError("frozen initial-stage operation order is invalid")
            declaration_revision = binding["revision"]
            define_request = request_ids["stage_define"]
            define_key = keys["stage_define"]
            define_params = _operation_params(
                "experiment.stage_define", stage_define_spec["arguments"], {
                    "project_id": project_id, "session_id": session_id,
                    "model_ref": binding["model_ref"], "expected_revision": declaration_revision,
                    "request_id": define_request, "idempotency_key": define_key,
                    "rpc_timeout_s": RPC_WAIT_S,
                })
            define_response = await dispatch("stage_define", "operation_call", define_params)
            define_data = _assert_success(define_response, "experiment.stage_define")
            define_ticket = _validate_managed_result_ticket(
                define_response, binding, request_id=define_request,
                idempotency_key=define_key, expected_revision=declaration_revision,
                label="experiment.stage_define",
            )
            if (define_data.get("plan_id") != INITIAL_STAGE_PLAN_ID
                    or define_data.get("project_id") != project_id
                    or define_data.get("model_ref") != binding["model_ref"]
                    or define_data.get("declaration_revision") != declaration_revision
                    or define_data.get("definition_sha256") != plan["initial_stage_definition_sha256"]
                    or not _valid_sha256(define_data.get("sha256"))
                    or define_data.get("stage_ids") != [INITIAL_STAGE_ID]
                    or define_data.get("registration") not in {"CREATED", "IDEMPOTENT_EXISTING"}
                    or define_data.get("declaration_status") != "DECLARED_UNVERIFIED"
                    or define_data.get("worker_rpc_performed") is not False
                    or define_data.get("solve_started") is not False):
                raise RunnerError("experiment.stage_define did not confirm the exact frozen declaration")
            stage_plan_sha256 = define_data["sha256"]
            record_stage_dispatch("stage_define", "CONFIRMED", confirmed=True)
            state.value["stage_define_progress"] = {
                "status": "CONFIRMED", "request_id": define_request,
                "idempotency_key": define_key,
                "operation_id": define_ticket["operation_id"],
                "request_hash": define_ticket["request_hash"], "job_id": define_ticket["job_id"],
                "plan_id": INITIAL_STAGE_PLAN_ID,
                "plan_sha256": stage_plan_sha256,
                "definition_sha256": plan["initial_stage_definition_sha256"],
                "declaration_revision": declaration_revision,
            }
            state.save()

            run_request = request_ids["stage_run"]
            run_key = keys["stage_run"]
            run_params = _operation_params(
                "experiment.stage_run", stage_run_spec["arguments"], {
                    "project_id": project_id, "session_id": session_id,
                    "model_ref": binding["model_ref"], "expected_revision": declaration_revision,
                    "request_id": run_request, "idempotency_key": run_key,
                    "queue_timeout_s": budgets["stage_run_queue_timeout_seconds"],
                    "execution_timeout_s": budgets["stage_run_execution_timeout_seconds"],
                    "rpc_timeout_s": budgets["stage_run_rpc_wait_seconds"],
                })
            run_response = await dispatch("stage_run", "operation_call", run_params)
            async def wait_for_stage_run(job_id: str) -> Mapping[str, Any]:
                if (budgets.get("stage_run_wait_calls_max") != 1
                        or budgets.get("stage_run_result_wait_seconds") != 240):
                    raise RunnerError("frozen stage-run wait budget is invalid")
                arguments = {
                    "job_id": job_id,
                    "timeout_s": budgets["stage_run_result_wait_seconds"],
                    "poll_interval_s": 0.25,
                }
                execution = {
                    "request_id": request_ids["stage_run_wait"],
                    "rpc_timeout_s": budgets["stage_run_result_wait_seconds"],
                }
                wait_response = await dispatch(
                    "stage_run_wait", "operation_call",
                    _operation_params("job.wait", arguments, execution),
                )
                return wait_response

            terminal_response, wait_response = await _resolve_initial_stage_run_response(
                run_response, request_id=run_request, idempotency_key=run_key,
                wait_for_job=wait_for_stage_run,
            )
            terminal_data = _assert_success(terminal_response, "experiment.stage_run terminal result")
            attempt = terminal_data.get("attempt")
            run_execution = terminal_response.get("execution")
            if (not isinstance(attempt, Mapping) or not isinstance(run_execution, Mapping)
                    or run_execution.get("project_id") != project_id
                    or run_execution.get("session_id") != session_id
                    or run_execution.get("model_ref") != binding["model_ref"]
                    or run_execution.get("request_id") != run_request
                    or run_execution.get("idempotency_key") != run_key
                    or run_execution.get("operation_id") != attempt.get("operation_id")
                    or run_execution.get("request_hash") != attempt.get("request_hash")
                    or not _valid_sha256(run_execution.get("request_hash"))
                    or not isinstance(run_execution.get("job_id"), str)
                    or not run_execution.get("job_id")
                    or type(run_execution.get("revision")) is not int):
                raise RunnerError("experiment.stage_run terminal ticket differs from its durable attempt")
            acceptance = _validate_initial_stage_acceptance(
                terminal_response, plan=plan, project_id=project_id,
                session_id=session_id, model_ref=binding["model_ref"],
                stage_plan_sha256=stage_plan_sha256,
                declaration_revision=declaration_revision,
                request_id=run_request, idempotency_key=run_key,
                operation_id=run_execution["operation_id"],
                request_hash=run_execution["request_hash"],
            )
            if run_execution["revision"] != acceptance["artifact_model_revision"]:
                raise RunnerError("stage-run terminal revision differs from the saved artifact revision")
            binding["revision"] = run_execution["revision"]
            record_stage_dispatch("stage_run", "CONFIRMED", confirmed=True)
            if wait_response is not None:
                record_stage_dispatch("stage_run_wait", "CONFIRMED", confirmed=True)
            wait_call_count = 1 if wait_response is not None else 0
            wait_action = state.value.get("actions", {}).get("stage_run_wait")
            if wait_call_count:
                wait_args = {
                    "job_id": run_execution["job_id"],
                    "timeout_s": budgets["stage_run_result_wait_seconds"],
                    "poll_interval_s": 0.25,
                }
                wait_execution = {
                    "request_id": request_ids["stage_run_wait"],
                    "rpc_timeout_s": budgets["stage_run_result_wait_seconds"],
                }
                expected_wait_params = _operation_params("job.wait", wait_args, wait_execution)
                if (not isinstance(wait_action, Mapping)
                        or wait_action.get("status") != "RESPONSE_RECORDED"
                        or wait_action.get("request_id") != request_ids["stage_run_wait"]
                        or wait_action.get("target_job_id") != run_execution["job_id"]
                        or wait_action.get("params_sha256") != sha256_value(expected_wait_params)
                        or wait_action.get("response") != _response_summary(wait_response)):
                    raise RunnerError("captured stage_run wait differs from its durable exact-job dispatch intent")
            elif wait_action is not None:
                raise RunnerError("terminal stage_run unexpectedly has a durable stage_run wait action")
            stage_run_wait_binding = {
                "calls": wait_call_count,
                "status": "CONFIRMED" if wait_call_count else "NOT_REQUIRED",
                "job_id": run_execution["job_id"],
                "stage_run_operation_id": run_execution["operation_id"],
                "stage_run_request_id": run_request,
                "stage_run_idempotency_key": run_key,
                "wait_request_id": request_ids["stage_run_wait"] if wait_call_count else None,
                "wait_response_sha256": sha256_value(wait_response) if wait_response is not None else None,
            }
            state.value.update({
                "status": "RUNNING",
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "stage_run_progress": {
                    "status": "CONFIRMED", "request_id": run_request,
                    "idempotency_key": run_key,
                    "operation_id": run_execution["operation_id"],
                    "request_hash": run_execution["request_hash"],
                    "job_id": run_execution["job_id"],
                    "revision_before": declaration_revision,
                    "revision_after": run_execution["revision"],
                    "attempt_id": acceptance["attempt_id"],
                    "attempt_sha256": acceptance["attempt_sha256"],
                    "saved_artifact": acceptance["saved_artifact"],
                    "output_tuple": acceptance["output_tuple"],
                },
                "stage_run_wait_binding": stage_run_wait_binding,
                "initial_stage_acceptance": acceptance,
                "initial_stage_acceptance_sha256": sha256_value(acceptance),
            })
            state.save()
            status = "INITIAL_STAGE_OUTPUT_ACCEPTED_WITHIN_SCOPE"
            initial_stage_record = {
                "profile": INITIAL_STAGE_PROFILE,
                "definition": plan["initial_stage_definition"],
                "definition_sha256": plan["initial_stage_definition_sha256"],
                "operations": plan["initial_stage_operations"],
                "stage_define": {
                    "request_id": define_request, "idempotency_key": define_key,
                    "operation_id": define_ticket["operation_id"],
                    "request_hash": define_ticket["request_hash"],
                    "job_id": define_ticket["job_id"], "data": dict(define_data),
                },
                "stage_run": {
                    "request_id": run_request, "idempotency_key": run_key,
                    "operation_id": run_execution["operation_id"],
                    "request_hash": run_execution["request_hash"],
                    "job_id": run_execution["job_id"],
                    "terminal_response": dict(terminal_response),
                    "wait_response": wait_response,
                },
                "acceptance": acceptance,
            }
            if len(canonical_bytes(initial_stage_record)) > W21_FIELD_READBACK_MAX_JSON_BYTES:
                raise RunnerError("initial-stage receipt evidence exceeds its frozen JSON byte limit")
        else:
            raise RunnerError("prepared mode is unsupported")

        report = {
            "schema": plan["schema"], "mode": mode, "kind": plan["kind"],
            "field_readback_profile": field_profile,
            "run_id": plan["run_id"], "freeze_sha256": plan["freeze_sha256"],
            "status": status, "requested_version": plan["requested_version"],
            "selected_comsol": plan["selected_comsol"], "selected_jdk": plan["selected_jdk"],
            "project_id": project_id, "session_id": session_id,
            "owned_server": {"pid": server_identity.get("pid"),
                             "birth": server_identity.get("birth"),
                             "host": endpoint.get("host"), "port": endpoint.get("port")},
            "server_instance_id": server_instance, "worker_instance_id": connected["worker_instance_id"],
            "remote_engine_version": connected["remote_engine_version"],
            "remote_engine_build": connected["remote_engine_build"],
            "remote_engine_build_source": connected.get("remote_engine_build_source"),
            "remote_engine_identity": remote_engine_identity,
            "worker_epoch": worker_epoch, "model_binding": binding,
            "fixture_sha256": plan["fixture_sha256"],
            "fixture_readback": dict(fixture_readback),
            "fixture_worker_execution": fixture_execution_proof,
            "budgets": dict(plan["budgets"]), "native_admission": "UNVERIFIED",
            "physical_validation": "UNVERIFIED",
            "study_dispatch": state.value.get("study_dispatch", 0),
            "solver_dispatch": state.value.get("solver_dispatch", 0),
            "stage_define_dispatch": state.value.get("stage_define_dispatch", 0),
            "stage_run_dispatch": state.value.get("stage_run_dispatch", 0),
            "underlying_solve_dispatch": state.value.get("underlying_solve_dispatch", 0),
            "stage_run_wait_calls": state.value.get("stage_run_wait_calls", 0),
            "cleanup": {"status": "PENDING"},
        }
        if mode == INITIAL_STAGE_MODE:
            report.update({
                "initial_stage_profile": INITIAL_STAGE_PROFILE,
                "acceptance_scope": INITIAL_STAGE_ACCEPTANCE_SCOPE,
                "source_manifest_sha256": plan["source_manifest_sha256"],
                "initial_stage_acceptance": state.value["initial_stage_acceptance"],
                "state_transfer": "NOT_RUN", "continuity": "NOT_RUN",
                "conservation": "NOT_RUN", "scientific_validation": "NOT_RUN",
                "physical_validation": "NOT_RUN",
            })
        if mode == METADATA_MODE:
            report.update({
                "probe_sha256": plan["probe_sha256"], "probe_readback": probe,
                "probe_capture": {
                    "request_scope": dict(probe_request),
                    "capture_kind": parsed_probe["capture_kind"],
                    "capture_status": probe.get("status"),
                    "capture_completeness": "COMPLETE" if parsed_probe["metadata_complete"] else "PARTIAL",
                    "metadata_complete": parsed_probe["metadata_complete"],
                    "raw_payload_sha256": parsed_probe["raw_payload_sha256"],
                    "raw_payload_bytes": parsed_probe["raw_payload_bytes"],
                },
            })
        elif mode == SOLVE_READBACK_MODE:
            report.update({"probe_observation": probe_observation,
                           "solution_tuple_readback": tuple_readback,
                           "solve_readback": solve_readback})
        elif mode == INITIAL_STAGE_MODE:
            report.update({"probe_observation": probe_observation,
                           "initial_stage": initial_stage_record})
        state.save()

        # Do not issue lifecycle cleanup after any ambiguous operation. The
        # exact public lifecycle routes own quiescence and process retirement.
        check_budget_cleanup()
        disconnect_args = {"project_id": project_id, "session_id": session_id, "retire_worker": True,
                           "idempotency_key": keys["session_disconnect"],
                           "request_id": request_ids["session_disconnect"]}
        disconnect = await dispatch("session.disconnect", "operation_call",
            _operation_params("session.disconnect", disconnect_args,
                              {"project_id": project_id, "session_id": session_id,
                               "idempotency_key": keys["session_disconnect"],
                               "request_id": request_ids["session_disconnect"],
                               "rpc_timeout_s": RPC_WAIT_S}))
        disconnected = _assert_success(disconnect, "session.disconnect(retire_worker=true)")
        if (disconnected.get("project_id") != project_id or disconnected.get("session_id") != session_id
                or disconnected.get("worker_handle_preserved") is not False):
            raise RunnerError("session.disconnect did not prove exact Worker retirement")

        stop_ref = f"Task-scoped W21 {mode} cleanup {plan['run_id']}"
        stop_args = {"project_id": project_id, "session_id": session_id,
                     "authorization_ref": stop_ref, "idempotency_key": keys["session_stop"],
                     "request_id": request_ids["session_stop"]}
        stopped = await dispatch("session.stop", "operation_call", _operation_params(
            "session.stop", stop_args,
            {"project_id": project_id, "session_id": session_id,
             "idempotency_key": keys["session_stop"], "request_id": request_ids["session_stop"],
             "rpc_timeout_s": RPC_WAIT_S}))
        stopped_data = _assert_success(stopped, "session.stop")
        if (stopped_data.get("project_id") != project_id or stopped_data.get("session_id") != session_id
                or stopped_data.get("state") != "STOPPED" or stopped_data.get("server_stopped") is not True):
            raise RunnerError("session.stop did not prove owned Server stop")
        _mark_isolation_receipt_stopped(Path(plan["isolation_receipt"]))
        report["cleanup"] = {"status": "CLEANUP_COMPLETE", "worker_retired": True,
                             "owned_server_stopped": True, "server_stop_evidence": stopped_data.get("stop_evidence")}
        report_path = Path(plan["run_root"]) / (
            "field_probe_receipt.json" if mode == METADATA_MODE else
            "solve_readback_receipt.json" if mode == SOLVE_READBACK_MODE else
            "initial_stage_receipt.json"
        )
        # run_root is derived from the frozen evidence paths, not a caller path.
        write_json_atomic(report_path, report)
        state.value["status"] = report["status"]
        state.value["receipt_path"] = str(report_path)
        state.value["receipt_sha256"] = sha256_file(report_path)
        state.save()
        return report
    except RunnerError:
        if not any_unknown:
            state.value["status"] = "FAILED"
            state.value["failure"] = "deterministic refusal; no implicit replay"
            state.save()
            await cleanup_after_deterministic_failure()
        raise
    except Exception as exc:
        if not any_unknown:
            state.value["status"] = "FAILED"
            state.value["failure"] = {"kind": "UNEXPECTED_LOCAL_ERROR",
                                      "error_type": type(exc).__name__}
            state.save()
            await cleanup_after_deterministic_failure()
        raise RunnerError(f"runner local failure: {type(exc).__name__}; state receipt preserved") from None


def _write_isolation_receipt(path: Path, server_identity: Mapping[str, Any],
                             endpoint: Mapping[str, Any]) -> None:
    """Bind G2 trusted Java to the daemon-created exact live Windows Server."""
    from comsol_mcp._g2_isolation import _windows_process_snapshot, _windows_socket_rows
    from comsol_mcp._platform_process import process_identity

    pid = server_identity.get("pid")
    birth = server_identity.get("birth")
    port = endpoint.get("port")
    if (type(pid) is not int or pid <= 1 or not isinstance(birth, str)
            or not birth.startswith("start_epoch_ms:") or type(port) is not int):
        raise RunnerError("session.start identity cannot support the required G2 isolation proof")
    try:
        birth_ms = int(birth.split(":", 1)[1])
    except ValueError as exc:
        raise RunnerError("session.start process birth is malformed") from exc
    before = process_identity(pid, platform_name="nt")
    observed = _windows_process_snapshot(pid)
    after = process_identity(pid, platform_name="nt")
    if (not isinstance(before, Mapping) or before.get("alive") is not True
            or before.get("start_epoch_ms") != birth_ms
            or not isinstance(after, Mapping) or after.get("alive") is not True
            or after.get("start_epoch_ms") != birth_ms
            or not isinstance(observed, Mapping)
            or not isinstance(observed.get("command_sha256"), str)):
        raise RunnerError("live OS process identity differs from the exact session.start birth")
    command = observed.get("command", "")
    if not isinstance(command, str) or "comsol" not in command.casefold() or "mphserver" not in command.casefold():
        raise RunnerError("session.start PID is not verified as COMSOL Server")
    rows = _windows_socket_rows(port)
    listeners = [row for row in rows if row.get("state") == "LISTEN"]
    if (len(listeners) != 1 or listeners[0].get("pid") != pid
            or listeners[0].get("endpoint") != f"127.0.0.1:{port}"):
        raise RunnerError("live listener no longer matches the session.start loopback identity")
    write_json_atomic(path, {"status": "RUNNING", "process": {
        "pid": pid, "birth": observed.get("birth"),
        "command_sha256": observed["command_sha256"], "port": port,
    }, "proof_scope": "daemon session.start identity + live Windows PID/birth/loopback listener"})


def _mark_isolation_receipt_stopped(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise RunnerError("owned Server isolation receipt disappeared before cleanup completion")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["status"] = "STOPPED"
    write_path = path.with_name(path.name + ".stopped")
    if write_path.exists() or write_path.is_symlink():
        raise RunnerError("stopped isolation receipt destination already exists")
    write_path.write_bytes(json.dumps(value, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    os.replace(write_path, path)


def _load_plan(path: Path, expected: str) -> tuple[dict[str, Any], _RunState]:
    if path.is_symlink() or not path.is_file():
        raise RunnerError("freeze plan must be a regular file")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(plan, dict)
            or plan.get("schema") != _mode_schema(plan.get("mode", METADATA_MODE))):
        raise RunnerError("unsupported freeze plan")
    verify_plan(plan, expected_sha256=expected, source_root=Path(plan["source_root"]))
    state_path = path.parent / "state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise RunnerError("prepared state receipt is missing or aliased")
    state_value = json.loads(state_path.read_text(encoding="utf-8"))
    if state_value.get("freeze_sha256") != expected:
        raise RunnerError("state receipt belongs to a different frozen plan")
    if state_value.get("status") != "PREPARED" or state_value.get("action_history"):
        raise RunnerError("state is not pristine PREPARED; replay is forbidden")
    actual_run_root = path.parent.resolve(strict=True)
    if os.path.normcase(os.path.abspath(str(Path(plan["run_root"])))) != os.path.normcase(str(actual_run_root)):
        raise RunnerError("freeze plan is not located in its exact prepared run root")
    plan["run_root"] = str(actual_run_root)
    return plan, _RunState(state_path, state_value)


def _process_preflight() -> list[dict[str, Any]]:
    if platform.system() != "Windows":
        raise RunnerError("native execute is supported only on Windows")
    helper_spec_name = "w21_windows_preflight_helper"
    import importlib.util
    spec = importlib.util.spec_from_file_location(helper_spec_name, PROCESS_HELPER)
    if spec is None or spec.loader is None:
        raise RunnerError("fixed Windows process preflight helper could not be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.assert_no_existing_comsol_processes()
    # Only safe, allowlisted process fields are kept in receipts.
    output = []
    for row in rows:
        if (not isinstance(row, Mapping) or type(row.get("process_id")) is not int
                or not isinstance(row.get("name"), str)
                or type(row.get("path_missing")) is not bool
                or type(row.get("command_line_missing")) is not bool):
            raise RunnerError("fixed process preflight returned an invalid safe projection")
        output.append({key: row[key] for key in (
            "process_id", "name", "path_missing", "command_line_missing")})
    return output


async def _execute_stdio(plan: dict[str, Any], state: _RunState) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RunnerError("execute refuses non-Windows hosts before any MCP process is started")
    if os.environ.get("COMSOL_MCP_HOST_CONTROL", "").casefold() not in {"1", "true", "yes"}:
        raise RunnerError("COMSOL_MCP_HOST_CONTROL grant must be explicitly present in the parent environment")
    if os.environ.get("COMSOL_MCP_TRUSTED_CODE", "").casefold() not in {"1", "true", "yes"}:
        raise RunnerError("COMSOL_MCP_TRUSTED_CODE grant must be explicitly present in the parent environment")

    _validate_frozen_server_home(plan)
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    source_root = Path(plan["source_root"]).resolve(strict=True)
    run_root = Path(plan["run_root"])
    server_home = Path(plan["server_home"])
    for dirname in ("stdio-home", "scratch", "prefs"):
        (run_root / dirname).mkdir(mode=0o700, exist_ok=True)
    server_home.mkdir(mode=0o700, parents=False, exist_ok=False)
    _validate_frozen_server_home(plan, require_absent=False)
    env_names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    child_env = {name: os.environ[name] for name in env_names if name in os.environ}
    child_env.update({
        "COMSOL_ROOT": plan["selected_comsol"]["root"],
        "COMSOL_SERVER_VERSION": plan["selected_comsol"]["version"],
        "JAVA_HOME": plan["selected_jdk"]["home"],
        "COMSOL_JAVA_HOME": plan["selected_jdk"]["home"],
        "COMSOL_SERVER_MCP_HOME": str(server_home),
        "COMSOL_PROJECT_ROOT": str(Path(plan["project_workspace"]).parent),
        "COMSOL_PREFS_DIR": str(run_root / "prefs"),
        "COMSOL_MCP_ISOLATION_RECEIPT": plan["isolation_receipt"],
        "COMSOL_MCP_TOOL_PROFILE": "full",
        "COMSOL_MCP_HOST_CONTROL": os.environ["COMSOL_MCP_HOST_CONTROL"],
        "COMSOL_MCP_TRUSTED_CODE": os.environ["COMSOL_MCP_TRUSTED_CODE"],
        "PYTHONPATH": str(source_root), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    # The subprocess rehashes the whole frozen source closure before and after
    # imports, then proves that the public stdio server came from this checkout.
    bootstrap = textwrap.dedent(f"""\
        import hashlib, json, sys
        from pathlib import Path
        root = Path({str(source_root)!r})
        plan_path = Path({str(Path(plan['run_root']) / 'freeze.json')!r})
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        def hash_file(relative):
            digest = hashlib.sha256()
            with (root / relative).open('rb') as stream:
                for block in iter(lambda: stream.read(1048576), b''):
                    digest.update(block)
            return digest.hexdigest()
        def check_manifest():
            observed = {{name: hash_file(name) for name in plan['source_manifest']}}
            encoded = json.dumps(observed, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
            return observed == plan['source_manifest'] and hashlib.sha256(encoded).hexdigest() == plan['source_manifest_sha256']
        if not check_manifest():
            raise SystemExit('frozen source closure changed before stdio import')
        sys.path.insert(0, str(root))
        import comsol_mcp
        if Path(comsol_mcp.__file__).resolve() != root / 'comsol_mcp/__init__.py':
            raise SystemExit('stdio child imported comsol_mcp from a different source')
        import comsol_mcp.mcp_server as server
        if not check_manifest():
            raise SystemExit('frozen source closure changed during stdio import')
        server.main()
    """)
    server = StdioServerParameters(
        command=sys.executable, args=["-B", "-c", bootstrap], env=child_env,
        cwd=str(source_root),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await run_metadata_protocol(_MCPCalls(session), plan, state,
                                               preflight=_process_preflight)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="freeze source/runtime identity; never launches MCP or COMSOL")
    prep.add_argument("--version", choices=("6.4", "6.3"), required=True)
    prep.add_argument("--mode", choices=(METADATA_MODE, SOLVE_READBACK_MODE, INITIAL_STAGE_MODE), default=METADATA_MODE)
    prep.add_argument("--field-readback-profile", choices=FIELD_READBACK_PROFILES,
                      help="solve-readback expression profile; default is the legacy T-only capture")
    prep.add_argument("--comsol-root", type=Path, required=True)
    prep.add_argument("--jdk-home", type=Path, required=True)
    prep.add_argument("--evidence-root", type=Path, required=True)
    prep.add_argument("--source-root", type=Path, default=REPOSITORY)
    prep.add_argument("--server-home-root", type=Path,
                      help="optional dedicated directory inside --evidence-root for short task-owned runtime homes")
    prep.add_argument("--probe-discovery-only", action="store_true",
                      help="freeze a read-only physics-field/FeatureInfo-tag discovery request")
    prep.add_argument("--probe-feature-info-tag",
                      help="freeze one exact FeatureInfo tag for bounded table capture")
    prep.add_argument("--probe-table-id", choices=("Shape", "Expression"),
                      help="freeze one exact FeatureInfo table; requires --probe-feature-info-tag")
    prep.add_argument("--prerequisite-64-receipt", type=Path)
    run = commands.add_parser("execute", help="one explicitly frozen W21 public MCP run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--freeze-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(version=args.version, comsol_root=args.comsol_root,
                             jdk_home=args.jdk_home, evidence_root=args.evidence_root,
                             source_root=args.source_root,
                             prerequisite_64_receipt=args.prerequisite_64_receipt,
                             server_home_root=args.server_home_root,
                             mode=args.mode,
                             probe_discovery_only=args.probe_discovery_only,
                             probe_feature_info_tag=args.probe_feature_info_tag,
                             probe_table_id=args.probe_table_id,
                             field_readback_profile=args.field_readback_profile)
        else:
            plan, state = _load_plan(args.plan, args.freeze_sha256)
            result = asyncio.run(_execute_stdio(plan, state))
        print(json.dumps({key: value for key, value in result.items()
                          if key not in {"plan", "probe_readback", "fixture_readback", "solve_readback"}},
                         ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        payload = {"status": "FAILED" if not isinstance(exc, RunnerError) else "REFUSED_OR_FAILED",
                   "error_type": type(exc).__name__,
                   "message": (str(exc)[:500] if isinstance(exc, RunnerError)
                               else "unexpected local failure; inspect the durable run state")}
        causes = _safe_runner_error_causes(exc)
        if causes:
            payload["cause_summary"] = causes
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
