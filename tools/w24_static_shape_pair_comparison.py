"""Descriptive flat-versus-step comparison for captured W24 shape histories.

This module compares two already captured records. It never builds a model,
changes a solution, runs a study, or starts a native process. A passing
per-case stationary window does not turn this cross-geometry description into
a native or scientific acceptance result.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.w24_static_shape_capture import (
    StaticShapeCaptureError,
    decode_native_history,
)
from tools.w24_static_shape_metrics import (
    ShapeMetricsError,
    extract_substrate_contact_line,
    extract_upper_interface_profile,
)


BASELINE_EPSILON_M = 8e-6
BASELINE_CONTACT_SPACING_M = BASELINE_EPSILON_M / 4.0
TIME_TOLERANCE_S = 1e-12
EXPECTED_SCIENCE_STATUS = "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED"


def compare_flat_step_captures(
    flat_capture: Mapping[str, Any],
    step_capture: Mapping[str, Any],
    *,
    expected_project_id: str | None = None,
    expected_workspace: Path | None = None,
) -> dict[str, Any]:
    """Return final observables and a descriptive comparison for two captures.

    The two shapes deliberately have different substrate topology and can
    reach different equilibrium profiles. Therefore this function applies no
    flat-versus-step pass threshold; each model's own stationary-history gate
    remains separate, while sensitivity and independent review remain open.
    """
    flat_raw, flat_identity = _load_case_capture(
        flat_capture, expected_case="flat", expected_project_id=expected_project_id,
        expected_workspace=expected_workspace)
    step_raw, step_identity = _load_case_capture(
        step_capture, expected_case="step", expected_project_id=expected_project_id,
        expected_workspace=expected_workspace)
    if flat_identity["model_tag"] == step_identity["model_tag"]:
        raise StaticShapeCaptureError("flat and step captures must identify distinct COMSOL model tags")
    if (flat_identity["project_id"] != step_identity["project_id"] or
            flat_identity["session_id"] != step_identity["session_id"] or
            flat_identity["server_instance_id"] != step_identity["server_instance_id"] or
            flat_identity["generation"] != step_identity["generation"]):
        raise StaticShapeCaptureError("flat and step captures must be bound to one project and Worker epoch")
    if (not math.isfinite(float(flat_raw.times_s[-1])) or
            not math.isfinite(float(step_raw.times_s[-1])) or
            abs(float(flat_raw.times_s[-1]) - float(step_raw.times_s[-1])) > TIME_TOLERANCE_S):
        raise StaticShapeCaptureError("flat and step captures do not cover the same final stored time")
    if flat_raw.radii_m.tolist() != step_raw.radii_m.tolist():
        raise StaticShapeCaptureError("flat and step captures do not use the exact same fixed radial grid")

    per_case: dict[str, Any] = {}
    for case_id, capture, raw, identity in (
            ("flat", flat_capture, flat_raw, flat_identity),
            ("step", step_capture, step_raw, step_identity)):
        try:
            final_profile = extract_upper_interface_profile(raw.profile_columns_at(raw.time_count - 1))
            final_contact = extract_substrate_contact_line(
                raw.substrate_samples_at(raw.time_count - 1),
                maximum_sample_spacing_m=identity["maximum_contact_spacing_m"])
            contact_rz = _interpolated_contact_coordinate(
                raw.substrate_samples_at(raw.time_count - 1), final_contact)
        except ShapeMetricsError as exc:
            per_case[case_id] = {
                "status": "FINAL_OBSERVABLE_EXTRACTION_FAILED",
                "error": str(exc),
                "model_tag": identity["model_tag"],
                "stable_window_status": identity["stable_window_status"],
                "raw_capture": identity["raw_capture"],
            }
            continue
        per_case[case_id] = {
            "status": "FINAL_OBSERVABLES_EXTRACTED",
            "model_tag": identity["model_tag"],
            "stable_window_status": identity["stable_window_status"],
            "final_time_s": float(raw.times_s[-1]),
            "final_phase1_volume_m3": float(raw.phase1_volume_m3[-1]),
            "final_maximum_speed_m_s": float(raw.maximum_speed_m_s[-1]),
            "final_contact_line_arclength_m": final_contact["arclength_m"],
            "final_contact_line_rz_m": contact_rz,
            "final_profile": final_profile,
            "raw_capture": identity["raw_capture"],
            "native_result_feature_readbacks": capture.get("native_result_feature_readbacks"),
        }

    both_extracted = all(row.get("status") == "FINAL_OBSERVABLES_EXTRACTED"
                         for row in per_case.values())
    both_stable = both_extracted and all(
        row["stable_window_status"] == "STABLE_WINDOW_PASS" for row in per_case.values())
    if not both_extracted:
        comparison = {"status": "NOT_COMPARABLE_FINAL_EXTRACTION_FAILED",
                      "cross_shape_threshold_applied": False}
    elif not both_stable:
        comparison = {"status": "DESCRIPTIVE_ONLY_CASE_NOT_STABLE",
                      "cross_shape_threshold_applied": False}
    else:
        comparison = _describe_profile_pair(per_case["flat"]["final_profile"],
                                            per_case["step"]["final_profile"])
        flat_volume = per_case["flat"]["final_phase1_volume_m3"]
        step_volume = per_case["step"]["final_phase1_volume_m3"]
        if flat_volume <= 0.0:
            raise StaticShapeCaptureError("flat capture final phase-1 volume is not positive")
        flat_contact = per_case["flat"]["final_contact_line_rz_m"]
        step_contact = per_case["step"]["final_contact_line_rz_m"]
        comparison.update({
            "final_relative_phase1_volume_difference": abs(step_volume - flat_volume) / flat_volume,
            "final_contact_line_euclidean_distance_m": math.hypot(
                step_contact[0] - flat_contact[0], step_contact[1] - flat_contact[1]),
        })

    return {
        "schema": "W24_FLAT_STEP_CAPTURE_COMPARISON_V1",
        "status": "DESCRIPTIVE_COMPARISON_ONLY",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED",
        "cross_shape_acceptance_gate": "NOT_DEFINED_FOR_DISTINCT_GEOMETRIES",
        "sensitivity_status": "NOT_RUN",
        "total_free_energy_status": "NOT_COMPUTED",
        "per_case": per_case,
        "flat_step_description": comparison,
        "comparison_contract": {
            "radial_grid": "identical fixed cell-centered coordinates over 0 <= r < Rbox; exact match required",
            "interface_support": "report common wet indices descriptively; no minimum common-support pass gate",
            "contact_line": "report each arclength separately and compare physical r,z coordinates",
            "volume": "report final relative difference separately",
            "cross_shape_pass_fail_threshold": None,
        },
    }


def _load_case_capture(capture: Mapping[str, Any], *, expected_case: str,
                       expected_project_id: str | None,
                       expected_workspace: Path | None):
    if not isinstance(capture, Mapping) or capture.get("schema") != "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V1":
        raise StaticShapeCaptureError(f"{expected_case} capture has an unsupported analysis schema")
    if (capture.get("status") != "NATIVE_CAPTURE_DECODED" or
            capture.get("native_acceptance") != "NOT_ESTABLISHED_CAPTURE_ONLY" or
            capture.get("scientific_acceptance") != EXPECTED_SCIENCE_STATUS):
        raise StaticShapeCaptureError(f"{expected_case} capture is not a decoded native capture-only record")
    if capture.get("case_id") != expected_case:
        raise StaticShapeCaptureError(f"capture case identity is not {expected_case}")
    model_tag = capture.get("model_tag")
    raw_record = capture.get("raw_capture")
    gate = capture.get("shape_history_gate")
    managed = capture.get("managed_binding")
    if (not isinstance(model_tag, str) or not model_tag or not isinstance(raw_record, Mapping) or
            not isinstance(gate, Mapping) or not isinstance(gate.get("status"), str) or
            not isinstance(managed, Mapping)):
        raise StaticShapeCaptureError(f"{expected_case} capture lacks model/raw-history/stability identity")
    model_ref = managed.get("model_ref")
    project_id, session_id = managed.get("project_id"), managed.get("session_id")
    if (not isinstance(project_id, str) or not project_id or
            not isinstance(session_id, str) or not session_id or
            not isinstance(model_ref, Mapping) or model_ref.get("model_tag") != model_tag or
            model_ref.get("session_id") != session_id or
            not isinstance(model_ref.get("server_instance_id"), str) or
            not model_ref.get("server_instance_id") or
            isinstance(model_ref.get("generation"), bool) or
            type(model_ref.get("generation")) is not int or model_ref["generation"] < 1):
        raise StaticShapeCaptureError(f"{expected_case} capture has an invalid managed ModelRef identity")
    if expected_project_id is not None and project_id != expected_project_id:
        raise StaticShapeCaptureError(f"{expected_case} capture is bound to a different registered project")
    if gate.get("status") not in {"STABLE_WINDOW_PASS", "FAIL"}:
        raise StaticShapeCaptureError(f"{expected_case} capture has an unknown stable-window status")
    # W24SHAP1/v1 captures are baseline-only: the decoder and fixed native
    # sample grid were verified for epsilon=8 um. Sensitivity variants must
    # use a separately versioned variable-grid capture contract.
    parameters = capture.get("parameters_and_units")
    epsilon = parameters.get("epsPF") if isinstance(parameters, Mapping) else None
    grid_protocol = capture.get("capture_grid_protocol")
    if (not isinstance(epsilon, Mapping) or epsilon.get("unit") != "m" or
            not _finite_number(epsilon.get("value_si")) or
            abs(float(epsilon["value_si"]) - 8e-6) > 1e-15 or
            not isinstance(grid_protocol, Mapping) or
            grid_protocol.get("schema") != "W24SHAP1/v1" or
            not _finite_number(grid_protocol.get("epsilon_m")) or
            abs(float(grid_protocol["epsilon_m"]) - float(epsilon["value_si"])) > 1e-15 or
            abs(float(grid_protocol.get("maximum_spacing_m", float("nan"))) - 2e-6) > 1e-15):
        raise StaticShapeCaptureError("W24SHAP1/v1 flat-step comparison only accepts the verified 8 um baseline capture")
    raw_path = raw_record.get("path")
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        raise StaticShapeCaptureError(f"{expected_case} raw-history path must be absolute")
    if expected_workspace is not None:
        if expected_workspace.is_symlink():
            raise StaticShapeCaptureError("registered comparison workspace must not be a symlink")
        try:
            workspace = expected_workspace.resolve(strict=True)
            canonical_raw = Path(raw_path).resolve(strict=True)
        except OSError as exc:
            raise StaticShapeCaptureError("registered comparison workspace/raw path is unavailable") from exc
        if not workspace.is_dir() or not canonical_raw.is_relative_to(workspace):
            raise StaticShapeCaptureError(f"{expected_case} raw-history path escaped the registered workspace")
    raw = decode_native_history(
        Path(raw_path),
        expected_sha256=str(raw_record.get("sha256", "")),
        expected_size_bytes=raw_record.get("size_bytes"))
    if (raw.time_count != raw_record.get("stored_time_count") or
            raw.radial_count != raw_record.get("radial_count") or
            raw.profile_point_count != raw_record.get("profile_point_count") or
            raw.wall_point_count != raw_record.get("wall_point_count")):
        raise StaticShapeCaptureError(f"{expected_case} raw dimensions differ from the analysis receipt")
    return raw, {
        "model_tag": model_tag, "stable_window_status": gate["status"],
        "project_id": project_id, "session_id": session_id,
        "server_instance_id": model_ref["server_instance_id"],
        "generation": model_ref["generation"],
        "maximum_contact_spacing_m": float(epsilon["value_si"]) / 4.0,
        "raw_capture": dict(raw_record),
    }


def _describe_profile_pair(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    radii_a, radii_b = first.get("radius_m"), second.get("radius_m")
    heights_a, heights_b = first.get("interface_height_m"), second.get("interface_height_m")
    support_a, support_b = first.get("support"), second.get("support")
    if not all(isinstance(value, Sequence) and not isinstance(value, (str, bytes))
               for value in (radii_a, radii_b, heights_a, heights_b, support_a, support_b)):
        raise StaticShapeCaptureError("flat-step final profiles have malformed radial/height/support arrays")
    lengths = {len(value) for value in (radii_a, radii_b, heights_a, heights_b, support_a, support_b)}
    if len(lengths) != 1 or len(radii_a) < 2:
        raise StaticShapeCaptureError("flat-step final profile arrays have inconsistent lengths")
    allowed_support = {"WET_INTERFACE", "DRY_SUPPORT"}
    if any(label not in allowed_support for label in (*support_a, *support_b)):
        raise StaticShapeCaptureError("flat-step profiles contain an unknown wet/dry support label")
    for index, (radius_a, radius_b) in enumerate(zip(radii_a, radii_b)):
        if (not _finite_number(radius_a) or not _finite_number(radius_b) or
                abs(float(radius_a) - float(radius_b)) > 1e-12):
            raise StaticShapeCaptureError(f"flat-step radial grids differ at index {index}")
    for index, (height, label) in enumerate(zip(heights_a, support_a)):
        if label == "WET_INTERFACE" and not _finite_number(height):
            raise StaticShapeCaptureError(f"flat profile wet height {index} is missing or nonfinite")
        if label == "DRY_SUPPORT" and height is not None:
            raise StaticShapeCaptureError(f"flat profile dry height {index} must be null")
    for index, (height, label) in enumerate(zip(heights_b, support_b)):
        if label == "WET_INTERFACE" and not _finite_number(height):
            raise StaticShapeCaptureError(f"step profile wet height {index} is missing or nonfinite")
        if label == "DRY_SUPPORT" and height is not None:
            raise StaticShapeCaptureError(f"step profile dry height {index} must be null")
    wet_a = {index for index, label in enumerate(support_a) if label == "WET_INTERFACE"}
    wet_b = {index for index, label in enumerate(support_b) if label == "WET_INTERFACE"}
    common = sorted(wet_a & wet_b)
    differences = []
    for index in common:
        a, b = heights_a[index], heights_b[index]
        if not _finite_number(a) or not _finite_number(b):
            raise StaticShapeCaptureError(f"wet flat-step profile height {index} is missing or nonfinite")
        differences.append(abs(float(a) - float(b)))
    union = wet_a | wet_b
    return {
        "status": "DESCRIPTIVE_PROFILE_DIFFERENCE" if common else "NO_COMMON_WET_PROFILE_SUPPORT",
        "cross_shape_threshold_applied": False,
        "interpolation_used": False,
        "wet_support_counts": {"flat": len(wet_a), "step": len(wet_b)},
        "common_wet_support_count": len(common),
        "common_fraction_of_smaller_wet_support": (
            len(common) / min(len(wet_a), len(wet_b)) if wet_a and wet_b else None),
        "common_fraction_of_union_support": len(common) / len(union) if union else None,
        "common_support_radius_range_m": ([float(radii_a[common[0]]), float(radii_a[common[-1]])]
                                           if common else None),
        "max_abs_height_difference_m": max(differences) if differences else None,
        "rms_height_difference_m": (math.sqrt(sum(value * value for value in differences) / len(differences))
                                    if differences else None),
        "interpretation": "descriptive only; flat and stepped substrates are distinct geometries",
    }


def _interpolated_contact_coordinate(samples: Sequence[Mapping[str, Any]],
                                     contact: Mapping[str, Any]) -> list[float]:
    indices = contact.get("bracket_indices")
    if not isinstance(indices, list) or len(indices) != 2:
        raise StaticShapeCaptureError("native contact-line result omitted its two-sample bracket")
    left, right = indices
    if any(isinstance(value, bool) or type(value) is not int for value in indices) or \
            left < 0 or right != left + 1 or right >= len(samples):
        raise StaticShapeCaptureError("native contact-line bracket indices are invalid")
    arc0 = _finite(samples[left].get("arclength_m"), "contact left arclength")
    arc1 = _finite(samples[right].get("arclength_m"), "contact right arclength")
    target = _finite(contact.get("arclength_m"), "contact crossing arclength")
    if not arc0 <= target <= arc1 or arc1 <= arc0:
        raise StaticShapeCaptureError("native contact crossing escaped its physical bracket")
    fraction = (target - arc0) / (arc1 - arc0)
    r0, r1 = _finite(samples[left].get("r_m"), "contact left radius"), _finite(samples[right].get("r_m"), "contact right radius")
    z0, z1 = _finite(samples[left].get("z_m"), "contact left height"), _finite(samples[right].get("z_m"), "contact right height")
    return [r0 + fraction * (r1 - r0), z0 + fraction * (z1 - z0)]


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _finite(value: Any, label: str) -> float:
    if not _finite_number(value):
        raise StaticShapeCaptureError(f"{label} must be a finite number")
    return float(value)


def capture_solved_flat_step_pair(
    managed_runner: Any,
    bindings: Mapping[str, Any],
    *,
    expected_capture_source_sha256: str,
) -> dict[str, Any]:
    """Capture two already-solved, managed models in one fail-closed sequence.

    This read-only orchestration does not call ``Study.run``. Both bindings
    are checked before the first Worker dispatch so a malformed second model
    cannot leave a one-sided pair capture. The per-case capture helper writes
    each immutable raw-history receipt; a failure on the second case returns
    an explicit partial record and does not retry the first case.
    """
    from uuid import uuid4

    from tools.w24_static_shape_capture import capture_static_shape_history

    if not isinstance(bindings, Mapping) or set(bindings) != {"flat", "step"}:
        raise StaticShapeCaptureError("flat-step capture requires exactly the two managed case bindings")
    if not isinstance(expected_capture_source_sha256, str) or len(expected_capture_source_sha256) != 64:
        raise StaticShapeCaptureError("capture orchestration requires the reviewed Java source SHA-256")
    project_id = getattr(managed_runner, "project_id", None)
    if not isinstance(project_id, str) or not project_id:
        raise StaticShapeCaptureError("capture runner lacks its authoritative registered project id")
    normalized: dict[str, Any] = {}
    for case_id in ("flat", "step"):
        binding = bindings[case_id]
        record = binding.as_record() if callable(getattr(binding, "as_record", None)) else binding
        if not isinstance(record, Mapping):
            raise StaticShapeCaptureError(f"{case_id} case has no managed ModelRef record")
        model_ref = record.get("model_ref")
        if (record.get("project_id") != project_id or
                not isinstance(record.get("session_id"), str) or
                not isinstance(model_ref, Mapping) or
                model_ref.get("model_tag") != record.get("model_tag") or
                model_ref.get("session_id") != record.get("session_id") or
                not isinstance(model_ref.get("server_instance_id"), str) or
                isinstance(model_ref.get("generation"), bool) or
                type(model_ref.get("generation")) is not int or model_ref["generation"] < 1):
            raise StaticShapeCaptureError(f"{case_id} case is not an exact project-bound Worker-epoch ModelRef")
        normalized[case_id] = (binding, dict(record))
    flat_record, step_record = normalized["flat"][1], normalized["step"][1]
    if (flat_record["model_tag"] == step_record["model_tag"] or
            dict(flat_record["model_ref"]) == dict(step_record["model_ref"])):
        raise StaticShapeCaptureError("flat and step cases must identify distinct managed models")
    flat_ref, step_ref = flat_record["model_ref"], step_record["model_ref"]
    if (flat_record["session_id"] != step_record["session_id"] or
            flat_ref["server_instance_id"] != step_ref["server_instance_id"] or
            flat_ref["generation"] != step_ref["generation"]):
        raise StaticShapeCaptureError("flat and step cases must use one current Worker epoch")
    for case_id in ("flat", "step"):
        verifier = getattr(managed_runner, "_verify_persisted_binding", None)
        if not callable(verifier):
            raise StaticShapeCaptureError("managed runner lacks persisted-binding verification")
        verifier(normalized[case_id][0])

    captures: dict[str, Any] = {}
    updated_bindings: dict[str, Any] = {}
    for case_id in ("flat", "step"):
        try:
            updated, capture = capture_static_shape_history(
                managed_runner, normalized[case_id][0], case_id=case_id,
                expected_capture_source_sha256=expected_capture_source_sha256)
        except BaseException as exc:
            return {
                "schema": "W24_MANAGED_FLAT_STEP_CAPTURE_SEQUENCE_V1",
                "status": "PARTIAL_OR_UNKNOWN_NO_RETRY",
                "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
                "study_run_submissions": "UNCHANGED_READ_ONLY_CAPTURE",
                "completed_cases": sorted(captures),
                "captures": captures,
                "updated_bindings": {key: value.as_record() for key, value in updated_bindings.items()},
                "failed_case": case_id,
                "error": f"{type(exc).__name__}: {exc}",
            }
        updated_record = updated.as_record()
        if (capture.get("case_id") != case_id or
                capture.get("model_tag") != updated_record.get("model_tag") or
                capture.get("managed_binding") != updated_record):
            return {
                "schema": "W24_MANAGED_FLAT_STEP_CAPTURE_SEQUENCE_V1",
                "status": "IDENTITY_MISMATCH_NO_RETRY",
                "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
                "study_run_submissions": "UNCHANGED_READ_ONLY_CAPTURE",
                "completed_cases": sorted(captures),
                "captures": captures,
                "updated_bindings": {key: value.as_record() for key, value in updated_bindings.items()},
                "failed_case": case_id,
                "error": "native capture receipt differs from its updated managed ModelRef",
            }
        updated_bindings[case_id] = updated
        captures[case_id] = capture

    comparison = compare_flat_step_captures(
        captures["flat"], captures["step"], expected_project_id=project_id,
        expected_workspace=Path(managed_runner.workspace))
    return {
        "schema": "W24_MANAGED_FLAT_STEP_CAPTURE_SEQUENCE_V1",
        "sequence_id": uuid4().hex,
        "status": "BOTH_CASES_CAPTURED_DESCRIPTIVE_ONLY",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "study_run_submissions": "UNCHANGED_READ_ONLY_CAPTURE",
        "completed_cases": ["flat", "step"],
        "captures": captures,
        "updated_bindings": {key: value.as_record() for key, value in updated_bindings.items()},
        "comparison": comparison,
    }
