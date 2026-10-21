"""No-interpolation W24 sensitivity comparisons over the shared v2 grid."""
from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools.w24_static_shape_capture import (
    StaticShapeCaptureError,
    VARIABLE_GRID_FORMAT_VERSION,
    _coordinate_digest,
    _grid_definition_digest,
    decode_native_history,
)
from tools.w24_static_shape_metrics import (
    ShapeMetricsError,
    compare_converged_shape_variants,
    extract_substrate_contact_line,
    extract_upper_interface_profile,
)
from tools.w24_static_shape_pair_comparison import compare_flat_step_captures
from tools.w24_static_shape_sensitivity import (
    COMMON_CAPTURE_GRID_MAX_SPACING_M,
    SENSITIVITY_CASE_ORDER,
    SENSITIVITY_CAPTURE_PROTOCOL,
    sensitivity_configurations,
)


DROP_RADIUS_M = 500e-6
TIME_TOLERANCE_S = 1e-12


def compare_sensitivity_case_variant(
    baseline_capture: Mapping[str, Any],
    variant_capture: Mapping[str, Any],
    *,
    variant_configuration_id: str,
    expected_case_id: str,
    expected_project_id: str,
    expected_workspace: Path,
) -> dict[str, Any]:
    """Compare one stable variant with baseline without post-capture interpolation."""
    configurations = {row.configuration_id: row for row in sensitivity_configurations()}
    variant_configuration = configurations.get(variant_configuration_id)
    if variant_configuration is None or variant_configuration_id == "baseline":
        raise StaticShapeCaptureError("variant comparison requires one of the six preregistered controls")
    baseline, baseline_raw = _load_sensitivity_case(
        baseline_capture, configuration_id="baseline", case_id=expected_case_id,
        expected_project_id=expected_project_id, expected_workspace=expected_workspace)
    variant, variant_raw = _load_sensitivity_case(
        variant_capture, configuration_id=variant_configuration_id, case_id=expected_case_id,
        expected_project_id=expected_project_id, expected_workspace=expected_workspace)
    if (baseline["model_tag"] == variant["model_tag"] or
            baseline["session_id"] != variant["session_id"] or
            baseline["server_instance_id"] != variant["server_instance_id"] or
            baseline["generation"] != variant["generation"]):
        raise StaticShapeCaptureError("baseline and sensitivity variant must be distinct models on the same Worker epoch")
    if (baseline_raw.radii_m.tolist() != variant_raw.radii_m.tolist() or
            baseline["grid_definition_sha256"] != variant["grid_definition_sha256"]):
        raise StaticShapeCaptureError("variant comparison requires the exact same native v2 coordinates; interpolation is forbidden")
    if (len(baseline_raw.times_s) != len(variant_raw.times_s) or any(
            abs(float(a) - float(b)) > TIME_TOLERANCE_S
            for a, b in zip(baseline_raw.times_s, variant_raw.times_s))):
        raise StaticShapeCaptureError("baseline and variant histories do not contain the exact same requested times")

    try:
        base_profile = extract_upper_interface_profile(
            baseline_raw.profile_columns_at(baseline_raw.time_count - 1))
        variant_profile = extract_upper_interface_profile(
            variant_raw.profile_columns_at(variant_raw.time_count - 1))
        base_contact = extract_substrate_contact_line(
            baseline_raw.substrate_samples_at(baseline_raw.time_count - 1),
            maximum_sample_spacing_m=baseline["epsilon_m"] / 4.0)
        variant_contact = extract_substrate_contact_line(
            variant_raw.substrate_samples_at(variant_raw.time_count - 1),
            maximum_sample_spacing_m=variant["epsilon_m"] / 4.0)
    except ShapeMetricsError as exc:
        raise StaticShapeCaptureError(f"sensitivity final observables could not be extracted: {exc}") from exc

    base_gate = baseline_capture.get("shape_history_gate")
    variant_gate = variant_capture.get("shape_history_gate")
    base_result = {
        "status": base_gate.get("status") if isinstance(base_gate, Mapping) else None,
        "final_profile": base_profile,
        "final_contact_line_arclength_m": base_contact["arclength_m"],
        "final_volume_m3": float(baseline_raw.phase1_volume_m3[-1]),
    }
    variant_result = {
        "status": variant_gate.get("status") if isinstance(variant_gate, Mapping) else None,
        "final_profile": variant_profile,
        "final_contact_line_arclength_m": variant_contact["arclength_m"],
        "final_volume_m3": float(variant_raw.phase1_volume_m3[-1]),
    }
    comparison = compare_converged_shape_variants(
        base_result, variant_result,
        drop_radius_m=DROP_RADIUS_M,
        epsilon_m=variant["epsilon_m"],
        maximum_height_fraction_of_radius=0.02,
        maximum_contact_line_motion_fraction_of_epsilon=0.5,
        maximum_relative_volume_difference=1e-3,
    )
    base_support = _profile_support_report(base_profile)
    variant_support = _profile_support_report(variant_profile)
    base_wet = set(base_support["wet_support_indices"])
    variant_wet = set(variant_support["wet_support_indices"])
    changed = sorted(base_wet ^ variant_wet)
    support_delta = {
        "status": "SAME_WET_SUPPORT" if not changed else "WET_SUPPORT_DIFFERENCE_REPORTED",
        "changed_support_indices": changed,
        "changed_support_radii_m": [base_profile["radius_m"][index] for index in changed],
        "comparison_gate": comparison.get("final_profile_comparison"),
    }
    return {
        "schema": "W24_STATIC_SHAPE_VARIANT_COMPARISON_V1",
        "status": comparison["status"],
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_INDEPENDENT_REVIEW_REQUIRED",
        "case_id": expected_case_id,
        "baseline_configuration_id": "baseline",
        "variant_configuration_id": variant_configuration_id,
        "capture_grid_protocol": SENSITIVITY_CAPTURE_PROTOCOL,
        "grid_definition_sha256": baseline["grid_definition_sha256"],
        "interpolation_used": False,
        "wet_dry_support_gate": "compare_upper_interface_profiles common-wet >=3 and >=0.95 of smaller wet support",
        "full_wet_dry_support": {
            "baseline": base_support,
            "variant": variant_support,
            "difference": support_delta,
        },
        "baseline_capture_sha256": baseline["capture_sha256"],
        "variant_capture_sha256": variant["capture_sha256"],
        "baseline_raw_sha256": baseline["raw_sha256"],
        "variant_raw_sha256": variant["raw_sha256"],
        "comparison": comparison,
    }


def compare_sensitivity_campaign(
    captures: Mapping[str, Mapping[str, Any]],
    *,
    expected_project_id: str,
    expected_workspace: Path,
) -> dict[str, Any]:
    """Evaluate each shape pair and the twelve frozen baseline-vs-variant gates."""
    expected_keys = {f"{row.configuration_id}:{case_id}"
                     for row in sensitivity_configurations()
                     for case_id in SENSITIVITY_CASE_ORDER}
    if not isinstance(captures, Mapping) or set(captures) != expected_keys:
        raise StaticShapeCaptureError("sensitivity comparison requires all fourteen configuration/case captures")
    shape_pairs: dict[str, Any] = {}
    for row in sensitivity_configurations():
        shape_pairs[row.configuration_id] = compare_flat_step_captures(
            captures[f"{row.configuration_id}:flat"],
            captures[f"{row.configuration_id}:step"],
            expected_project_id=expected_project_id,
            expected_workspace=expected_workspace,
            configuration_id=row.configuration_id,
        )
    variants: dict[str, Any] = {}
    for row in sensitivity_configurations()[1:]:
        variants[row.configuration_id] = {}
        for case_id in SENSITIVITY_CASE_ORDER:
            variants[row.configuration_id][case_id] = compare_sensitivity_case_variant(
                captures[f"baseline:{case_id}"],
                captures[f"{row.configuration_id}:{case_id}"],
                variant_configuration_id=row.configuration_id,
                expected_case_id=case_id,
                expected_project_id=expected_project_id,
                expected_workspace=expected_workspace,
            )
    all_variant_pass = all(
        result.get("status") == "PASS"
        for by_case in variants.values() for result in by_case.values())
    all_cases_stable = all(
        capture.get("shape_history_gate", {}).get("status") == "STABLE_WINDOW_PASS"
        for capture in captures.values())
    return {
        "schema": "W24_STATIC_SHAPE_SENSITIVITY_COMPARISON_V1",
        "status": "SENSITIVITY_GATES_PASS" if all_variant_pass and all_cases_stable else "SENSITIVITY_GATES_FAIL_OR_INCOMPLETE",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_INDEPENDENT_REVIEW_REQUIRED",
        "planned_study_run_submissions": 14,
        "shape_pair_comparisons": shape_pairs,
        "baseline_variant_comparisons": variants,
        "preregistered_limits": {
            "maximum_interface_height_difference_m": "0.02 * Rdrop",
            "contact_line_difference_m": "0.5 * variant epsilon",
            "relative_final_phase1_volume_difference": 1e-3,
            "minimum_common_wet_points": 3,
            "minimum_common_fraction_of_smaller_wet_support": 0.95,
            "interpolation_used": False,
        },
        "full_wet_dry_support_reported": True,
    }


def _load_sensitivity_case(
    capture: Mapping[str, Any], *, configuration_id: str, case_id: str,
    expected_project_id: str, expected_workspace: Path,
) -> tuple[dict[str, Any], Any]:
    configuration = next((row for row in sensitivity_configurations()
                          if row.configuration_id == configuration_id), None)
    if configuration is None:
        raise StaticShapeCaptureError("capture configuration is not part of the frozen W24 matrix")
    if (not isinstance(capture, Mapping) or
            capture.get("schema") != "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V2" or
            capture.get("status") != "NATIVE_CAPTURE_DECODED" or
            capture.get("native_acceptance") != "NOT_ESTABLISHED_CAPTURE_ONLY" or
            capture.get("scientific_acceptance") != "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED" or
            capture.get("case_id") != case_id or
            capture.get("configuration_id") != configuration_id):
        raise StaticShapeCaptureError(f"{case_id}/{configuration_id} capture is not an exact v2 sensitivity analysis")
    gate = capture.get("shape_history_gate")
    parameters = capture.get("parameters_and_units")
    epsilon_row = parameters.get("epsPF") if isinstance(parameters, Mapping) else None
    protocol = capture.get("capture_grid_protocol")
    if (not isinstance(gate, Mapping) or gate.get("status") not in {"STABLE_WINDOW_PASS", "FAIL"} or
            not isinstance(epsilon_row, Mapping) or epsilon_row.get("unit") != "m" or
            not _finite(epsilon_row.get("value_si")) or
            abs(float(epsilon_row["value_si"]) - configuration.epsilon_m) > 1e-15 or
            not isinstance(protocol, Mapping) or protocol.get("schema") != SENSITIVITY_CAPTURE_PROTOCOL or
            protocol.get("maximum_spacing_m") != COMMON_CAPTURE_GRID_MAX_SPACING_M or
            not _is_sha256(protocol.get("grid_definition_sha256"))):
        raise StaticShapeCaptureError("capture epsilon, stability, or common-grid identity is invalid")
    binding = capture.get("managed_binding")
    model_ref = binding.get("model_ref") if isinstance(binding, Mapping) else None
    model_tag = capture.get("model_tag")
    if (not isinstance(binding, Mapping) or binding.get("project_id") != expected_project_id or
            not isinstance(model_tag, str) or not model_tag or
            not isinstance(binding.get("session_id"), str) or not binding.get("session_id") or
            not isinstance(model_ref, Mapping) or model_ref.get("model_tag") != model_tag or
            model_ref.get("session_id") != binding.get("session_id") or
            not isinstance(model_ref.get("server_instance_id"), str) or
            not model_ref.get("server_instance_id") or
            isinstance(model_ref.get("generation"), bool) or
            type(model_ref.get("generation")) is not int or model_ref["generation"] < 1):
        raise StaticShapeCaptureError("capture is not bound to a complete registered project/Worker ModelRef")
    raw_record = capture.get("raw_capture")
    raw_path = raw_record.get("path") if isinstance(raw_record, Mapping) else None
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute() or expected_workspace.is_symlink():
        raise StaticShapeCaptureError("raw sensitivity history path/workspace is invalid")
    workspace = expected_workspace.resolve(strict=True)
    raw_file = Path(raw_path).resolve(strict=True)
    if not raw_file.is_relative_to(workspace):
        raise StaticShapeCaptureError("raw sensitivity history escaped its registered workspace")
    raw = decode_native_history(
        raw_file,
        expected_sha256=str(raw_record.get("sha256", "")),
        expected_size_bytes=raw_record.get("size_bytes"),
    )
    if (raw.format_version != VARIABLE_GRID_FORMAT_VERSION or
            raw.time_count != raw_record.get("stored_time_count") or
            raw.radial_count != raw_record.get("radial_count") or
            raw.profile_point_count != raw_record.get("profile_point_count") or
            raw.wall_point_count != raw_record.get("wall_point_count") or
            protocol.get("radial_coordinates_sha256") != _coordinate_digest(raw.radii_m) or
            protocol.get("grid_definition_sha256") != _grid_definition_digest(raw, True)):
        raise StaticShapeCaptureError("raw v2 history dimensions/hash/grid do not match its analysis receipt")
    return {
        "model_tag": model_tag,
        "session_id": binding["session_id"],
        "server_instance_id": model_ref["server_instance_id"],
        "generation": model_ref["generation"],
        "epsilon_m": configuration.epsilon_m,
        "grid_definition_sha256": protocol["grid_definition_sha256"],
        "capture_sha256": _canonical_capture_digest(capture),
        "raw_sha256": raw.sha256,
    }, raw


def _canonical_capture_digest(capture: Mapping[str, Any]) -> str:
    import hashlib
    import json

    try:
        encoded = json.dumps(capture, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StaticShapeCaptureError("analysis receipt is not canonical finite JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _profile_support_report(profile: Mapping[str, Any]) -> dict[str, Any]:
    radii = profile.get("radius_m")
    support = profile.get("support")
    if (not isinstance(radii, list) or not isinstance(support, list) or len(radii) != len(support) or
            any(kind not in {"WET_INTERFACE", "DRY_SUPPORT"} for kind in support)):
        raise StaticShapeCaptureError("upper-interface extraction omitted its complete wet/dry support classification")
    wet = [index for index, kind in enumerate(support) if kind == "WET_INTERFACE"]
    dry = [index for index, kind in enumerate(support) if kind == "DRY_SUPPORT"]
    return {
        "sample_count": len(support),
        "wet_support_point_count": len(wet),
        "dry_support_point_count": len(dry),
        "wet_support_index_range": [wet[0], wet[-1]] if wet else None,
        "dry_support_index_range": [dry[0], dry[-1]] if dry else None,
        "wet_support_indices": wet,
        "dry_support_indices": dry,
        "wet_support_radius_range_m": [radii[wet[0]], radii[wet[-1]]] if wet else None,
        "dry_support_radius_range_m": [radii[dry[0]], radii[dry[-1]]] if dry else None,
    }
