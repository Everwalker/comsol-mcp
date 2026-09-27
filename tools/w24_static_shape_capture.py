"""Managed read-only capture and decode of W24 static-shape solution history.

The Java capture entrypoint only reads an already-solved model; it never calls
Study.run or rewrites solution data.  This module verifies the managed project
binding, hashes the raw artifact, decodes the compact binary format, and feeds
the native samples to the previously defined fail-closed shape metrics.  It
does not promote the result to scientific acceptance: sensitivity cases and
independent review remain separate requirements.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import shutil
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from tools.w24_static_shape_metrics import (
    ShapeMetricsError,
    evaluate_shape_history,
    extract_substrate_contact_line,
    extract_upper_interface_profile,
    validate_stored_time_grid,
)


MAGIC = b"W24SHAP1"
FORMAT_VERSION = 1
HEADER = struct.Struct(">8siiiii")
MAX_CAPTURE_BYTES = 1024 * 1024 * 1024
MAX_TIMES = 200_000
MAX_RADIAL_COLUMNS = 10_000
MAX_PROFILE_POINTS = 5_000_000
MAX_WALL_POINTS = 1_000_000
MAX_FIELD_SAMPLES = 125_000_000
TIME_TOLERANCE_S = 1e-12
CAPTURE_SOURCE = Path(__file__).resolve().parents[1] / "tools/java/W24StaticShapeHistoryCapture.java"


class StaticShapeCaptureError(RuntimeError):
    """The managed capture, bytes, metadata, or native field mapping is invalid."""


@dataclass(frozen=True)
class RawStaticShapeHistory:
    """Verified compact arrays from one raw Java capture artifact."""

    path: Path
    sha256: str
    size_bytes: int
    times_s: array.array
    radii_m: array.array
    floors_m: array.array
    z_columns_m: tuple[array.array, ...]
    profile_offsets: tuple[int, ...]
    profile_phi: array.array
    wall_arclength_m: array.array
    wall_r_m: array.array
    wall_z_m: array.array
    wall_phi: array.array
    phase1_volume_m3: array.array
    maximum_speed_m_s: array.array

    @property
    def time_count(self) -> int:
        return len(self.times_s)

    @property
    def radial_count(self) -> int:
        return len(self.radii_m)

    @property
    def profile_point_count(self) -> int:
        return len(self.profile_phi) // self.time_count

    @property
    def wall_point_count(self) -> int:
        return len(self.wall_arclength_m)

    def profile_columns_at(self, time_index: int) -> list[dict[str, Any]]:
        _check_index(time_index, self.time_count, "time_index")
        row_start = time_index * self.profile_point_count
        columns: list[dict[str, Any]] = []
        for column_index, (radius, floor, z_values, offset) in enumerate(zip(
                self.radii_m, self.floors_m, self.z_columns_m, self.profile_offsets)):
            end = offset + len(z_values)
            columns.append({
                "radius_m": float(radius),
                "floor_m": float(floor),
                "z_m": list(z_values),
                "phi": list(self.profile_phi[row_start + offset:row_start + end]),
                "column_index": column_index,
            })
        return columns

    def substrate_samples_at(self, time_index: int) -> list[dict[str, float]]:
        _check_index(time_index, self.time_count, "time_index")
        start = time_index * self.wall_point_count
        return [{
            "arclength_m": float(self.wall_arclength_m[i]),
            "r_m": float(self.wall_r_m[i]),
            "z_m": float(self.wall_z_m[i]),
            "phi": float(self.wall_phi[start + i]),
        } for i in range(self.wall_point_count)]


def decode_native_history(
    path: Path,
    *,
    expected_sha256: str,
    expected_size_bytes: int | None = None,
    maximum_bytes: int = MAX_CAPTURE_BYTES,
) -> RawStaticShapeHistory:
    """Decode and validate a W24SHAP1 artifact without trusting its dimensions."""
    supplied = Path(path)
    if supplied.is_symlink():
        raise StaticShapeCaptureError("raw capture artifact must not be a symlink")
    try:
        canonical = supplied.resolve(strict=True)
        metadata = canonical.stat(follow_symlinks=False)
    except OSError as exc:
        raise StaticShapeCaptureError("raw capture artifact is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise StaticShapeCaptureError("raw capture artifact must be a regular file")
    if (type(maximum_bytes) is not int or maximum_bytes < HEADER.size or
            maximum_bytes > MAX_CAPTURE_BYTES):
        raise StaticShapeCaptureError("maximum_bytes must be a positive bounded integer")
    if metadata.st_size < HEADER.size or metadata.st_size > maximum_bytes:
        raise StaticShapeCaptureError("raw capture file size is outside the decoder safety bounds")
    if expected_size_bytes is not None and (
            isinstance(expected_size_bytes, bool) or type(expected_size_bytes) is not int or
            expected_size_bytes != metadata.st_size):
        raise StaticShapeCaptureError("raw artifact byte count differs from native manifest")
    if not _is_sha256(expected_sha256):
        raise StaticShapeCaptureError("native manifest SHA-256 is missing or malformed")
    actual_sha = _sha256_file(canonical)
    if actual_sha != expected_sha256:
        raise StaticShapeCaptureError("raw artifact SHA-256 differs from native manifest")
    if array.array("d").itemsize != 8:
        raise StaticShapeCaptureError("host double array representation is not IEEE-width compatible")

    try:
        with canonical.open("rb") as stream:
            raw_header = _read_exact(stream, HEADER.size)
            magic, version, time_count, radial_count, profile_point_count, wall_point_count = HEADER.unpack(raw_header)
            if magic != MAGIC or version != FORMAT_VERSION:
                raise StaticShapeCaptureError("raw artifact has an unsupported W24 binary signature/version")
            if not (2 <= time_count <= MAX_TIMES and
                    2 <= radial_count <= MAX_RADIAL_COLUMNS and
                    radial_count <= profile_point_count <= MAX_PROFILE_POINTS and
                    3 <= wall_point_count <= MAX_WALL_POINTS):
                raise StaticShapeCaptureError("raw artifact dimensions exceed the frozen decoder bounds")
            if time_count * profile_point_count > MAX_FIELD_SAMPLES or time_count * wall_point_count > MAX_FIELD_SAMPLES:
                raise StaticShapeCaptureError("raw field sample count exceeds the decoder safety bound")

            times = _read_doubles(stream, time_count)
            radii = _read_doubles(stream, radial_count)
            floors = array.array("d")
            z_columns: list[array.array] = []
            offsets: list[int] = []
            points_sum = 0
            for _ in range(radial_count):
                floor = _read_one_double(stream)
                z_count = _read_one_int(stream)
                if not (3 <= z_count <= MAX_PROFILE_POINTS) or points_sum + z_count > MAX_PROFILE_POINTS:
                    raise StaticShapeCaptureError("raw profile contains an invalid vertical-column length")
                floors.append(floor)
                offsets.append(points_sum)
                z_columns.append(_read_doubles(stream, z_count))
                points_sum += z_count
            if points_sum != profile_point_count:
                raise StaticShapeCaptureError("raw profile column lengths do not sum to the declared field width")

            expected_length = _expected_binary_size(
                time_count, radial_count, profile_point_count, wall_point_count, points_sum)
            if expected_length != metadata.st_size:
                raise StaticShapeCaptureError("raw artifact exact byte length does not match its dimensions")

            profile_values = _read_doubles(stream, time_count * profile_point_count)
            wall_arclength = array.array("d")
            wall_r = array.array("d")
            wall_z = array.array("d")
            for _ in range(wall_point_count):
                wall_arclength.append(_read_one_double(stream))
                wall_r.append(_read_one_double(stream))
                wall_z.append(_read_one_double(stream))
            wall_values = _read_doubles(stream, time_count * wall_point_count)
            volumes = _read_doubles(stream, time_count)
            speeds = _read_doubles(stream, time_count)
            if stream.read(1):
                raise StaticShapeCaptureError("raw artifact has trailing bytes beyond the declared history")
    except StaticShapeCaptureError:
        raise
    except (OSError, EOFError, OverflowError, ValueError, struct.error) as exc:
        raise StaticShapeCaptureError(f"raw artifact is truncated or malformed: {exc}") from exc

    for label, values in (
            ("stored time", times), ("radius", radii), ("substrate floor", floors),
            ("profile phi", profile_values), ("wall arclength", wall_arclength),
            ("wall radius", wall_r), ("wall height", wall_z), ("wall phi", wall_values),
            ("phase-1 volume", volumes), ("maximum speed", speeds)):
        _require_finite(values, label)
    for column, values in enumerate(z_columns):
        _require_finite(values, f"profile z column {column}")
    if not _strictly_increasing(times) or times[0] < -TIME_TOLERANCE_S:
        raise StaticShapeCaptureError("raw stored-time values must be finite, nonnegative, and strictly increasing")
    if not _strictly_increasing(radii) or radii[0] < 0.0:
        raise StaticShapeCaptureError("raw profile radii must be nonnegative and strictly increasing")
    if not _strictly_increasing(wall_arclength) or abs(wall_arclength[0]) > 1e-12:
        raise StaticShapeCaptureError("raw substrate path arclength must start at zero and increase strictly")
    for column, (floor, z_values) in enumerate(zip(floors, z_columns)):
        if floor < 0.0 or not _strictly_increasing(z_values) or abs(z_values[0] - floor) > 1e-12:
            raise StaticShapeCaptureError(f"raw profile column {column} does not begin at its substrate floor")
    for index in range(1, wall_point_count):
        segment = math.hypot(wall_r[index] - wall_r[index - 1], wall_z[index] - wall_z[index - 1])
        if segment <= 0.0 or abs((wall_arclength[index] - wall_arclength[index - 1]) - segment) > 1e-12:
            raise StaticShapeCaptureError("raw wall arclength does not match adjacent physical coordinates")
    if any(value <= 0.0 for value in volumes) or any(value < 0.0 for value in speeds):
        raise StaticShapeCaptureError("raw physical metrics contain a nonpositive volume or negative speed")

    return RawStaticShapeHistory(
        path=canonical, sha256=actual_sha, size_bytes=metadata.st_size,
        times_s=times, radii_m=radii, floors_m=floors,
        z_columns_m=tuple(z_columns), profile_offsets=tuple(offsets),
        profile_phi=profile_values, wall_arclength_m=wall_arclength,
        wall_r_m=wall_r, wall_z_m=wall_z, wall_phi=wall_values,
        phase1_volume_m3=volumes, maximum_speed_m_s=speeds)


def analyze_native_capture(
    capture_result: Mapping[str, Any],
    raw: RawStaticShapeHistory,
    *,
    expected_case_id: str,
) -> dict[str, Any]:
    """Bind raw columns/metrics to their native case metadata and evaluate gates."""
    if not isinstance(capture_result, Mapping):
        raise StaticShapeCaptureError("native capture result must be a mapping")
    if (capture_result.get("schema") != "W24_NATIVE_STATIC_SHAPE_RAW_HISTORY_V1" or
            capture_result.get("status") != "NATIVE_RAW_STATIC_SHAPE_HISTORY_CAPTURED" or
            capture_result.get("native_acceptance") != "NOT_ESTABLISHED_CAPTURE_ONLY" or
            capture_result.get("native_study_run_calls_this_action") != 0):
        raise StaticShapeCaptureError("native capture status/schema is incomplete or makes an invalid acceptance claim")
    if capture_result.get("case_id") != expected_case_id or expected_case_id not in {"flat", "step"}:
        raise StaticShapeCaptureError("raw native capture case identity does not match the requested case")
    model_tag = capture_result.get("model_tag")
    if not isinstance(model_tag, str) or not model_tag:
        raise StaticShapeCaptureError("native capture omitted the actual model tag")
    artifact = capture_result.get("binary_artifact")
    if (not isinstance(artifact, Mapping) or artifact.get("path") != str(raw.path) or
            artifact.get("sha256") != raw.sha256 or artifact.get("size_bytes") != raw.size_bytes or
            artifact.get("layout") != "W24SHAP1/v1 big-endian doubles; see decoder contract"):
        raise StaticShapeCaptureError("decoded artifact does not match the native capture receipt")

    parameters = _validate_capture_parameters(capture_result)
    flat_volume = math.pi * parameters["Rdrop"] ** 2 * parameters["hFlat"]
    expected_volume = (flat_volume if expected_case_id == "flat" else
                       math.pi * (parameters["Rdrop"] ** 2 * parameters["zStepTop"] -
                                 parameters["Rmesa"] ** 2 * parameters["hMesa"]))
    for key in ("analytic_initial_glue_volume_m3", "paired_flat_analytic_volume_m3"):
        value = capture_result.get(key)
        expected = expected_volume if key == "analytic_initial_glue_volume_m3" else flat_volume
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(float(value)) or
                abs(float(value) - expected) > max(1e-18, abs(expected) * 1e-13)):
            raise StaticShapeCaptureError(f"native analytic geometry volume readback is invalid: {key}")
    geometry = capture_result.get("geometry")
    if not isinstance(geometry, Mapping):
        raise StaticShapeCaptureError("native capture omitted geometry and selection provenance")
    glue_ids = _positive_id_list(geometry.get("glue_domain_ids"), "glue_domain_ids")
    gas_ids = _positive_id_list(geometry.get("gas_domain_ids"), "gas_domain_ids")
    all_domains = _positive_id_list(geometry.get("all_fluid_domain_ids"), "all_fluid_domain_ids")
    axis_ids = _positive_id_list(geometry.get("axis_boundary_ids"), "axis_boundary_ids")
    boundary_count = geometry.get("boundary_count")
    wetting = geometry.get("wetted_boundary_ids")
    if (geometry.get("dimension") != 2 or geometry.get("axisymmetric") is not True or
            geometry.get("domain_count") != 2 or len(glue_ids) != 1 or len(gas_ids) != 1 or
            set(glue_ids).intersection(gas_ids) or set(all_domains) != set(glue_ids + gas_ids) or
            isinstance(boundary_count, bool) or type(boundary_count) is not int or boundary_count < 1 or
            not isinstance(wetting, Mapping) or not wetting):
        raise StaticShapeCaptureError("native geometry/domain identity failed the 2-D axisymmetric two-domain gate")
    if expected_case_id == "flat":
        expected_wetting_names = {"sel_wet_flat_base"}
    else:
        expected_wetting_names = {"sel_wet_mesa_top", "sel_wet_mesa_side", "sel_wet_lower_base"}
    if set(wetting) != expected_wetting_names:
        raise StaticShapeCaptureError("native wetted-boundary selection names differ from the requested fixture")
    wet_ids: list[int] = []
    for name, raw_ids in wetting.items():
        ids = _positive_id_list(raw_ids, f"wetted boundary {name}")
        wet_ids.extend(ids)
    if (len(set(wet_ids)) != len(wet_ids) or set(wet_ids).intersection(axis_ids) or
            any(value > boundary_count for value in axis_ids + wet_ids)):
        raise StaticShapeCaptureError("wetting boundary selections overlap, escape geometry, or include the axis")
    union_wet = sorted(wet_ids)

    _validate_native_feature(capture_result.get("profile_field"), role="profile", expression="pf.phipf",
                             unit="1", dimension=2, entity_ids=all_domains)
    _validate_native_feature(capture_result.get("substrate_field"), role="substrate", expression="pf.phipf",
                             unit="1", dimension=1, entity_ids=union_wet)
    _validate_native_feature(capture_result.get("phase1_volume"), role="phase1_volume",
                             expression="(1-pf.phipf)/2", unit="m^3", dimension=2,
                             entity_ids=all_domains, axisymmetric_property=("intvolume", "on"))
    _validate_native_feature(capture_result.get("maximum_speed"), role="maximum_speed",
                             expression="sqrt(spf.u^2+spf.w^2)", unit="m/s", dimension=2,
                             entity_ids=all_domains)

    profile_meta = capture_result["profile_field"]
    if (profile_meta.get("radial_support") != "cell-centered fixed samples within 0 <= r < Rbox" or
            profile_meta.get("radial_support_upper_exclusive_m") != parameters["Rbox"] or
            profile_meta.get("radial_point_count") != raw.radial_count or
            profile_meta.get("profile_point_count") != raw.profile_point_count or
            profile_meta.get("shape") != [1, raw.time_count, raw.profile_point_count] or
            raw.radii_m[-1] >= parameters["Rbox"]):
        raise StaticShapeCaptureError("native profile does not use the required fixed radial support 0 <= r < Rbox")
    _validate_profile_grid(raw, expected_case_id, parameters)
    substrate_meta = capture_result["substrate_field"]
    if (substrate_meta.get("entity_ids") != union_wet or
            substrate_meta.get("entity_dimension") != 1 or
            substrate_meta.get("shape") != [1, raw.time_count, raw.wall_point_count]):
        raise StaticShapeCaptureError("native substrate samples are not bound to every physical wetting boundary")
    if capture_result["phase1_volume"].get("entity_ids") != all_domains:
        raise StaticShapeCaptureError("native phase-1 volume is not bound to both evolving-fluid domains")
    if capture_result["maximum_speed"].get("entity_ids") != all_domains:
        raise StaticShapeCaptureError("native maximum speed is not bound to both fluid domains")
    if (capture_result["phase1_volume"].get("axisymmetric_measure") != "intvolume=on" or
            capture_result["phase1_volume"].get("shape") != [1, raw.time_count] or
            capture_result["maximum_speed"].get("shape") != [1, raw.time_count]):
        raise StaticShapeCaptureError("native volume/speed result shape or axisymmetric measure is invalid")

    _validate_wall_geometry(raw, expected_case_id, parameters)
    requested_times = capture_result.get("requested_times_s")
    stored_metadata_times = capture_result.get("stored_times_s")
    if not isinstance(requested_times, list) or not isinstance(stored_metadata_times, list):
        raise StaticShapeCaptureError("native capture omitted requested/stored time vectors")
    if not _vectors_match(stored_metadata_times, raw.times_s, TIME_TOLERANCE_S):
        raise StaticShapeCaptureError("binary time vector differs from native receipt stored_times_s")
    try:
        time_grid = validate_stored_time_grid(raw.times_s, requested_times,
                                              tolerance_s=TIME_TOLERANCE_S)
    except ShapeMetricsError as exc:
        raise StaticShapeCaptureError(f"native output times fail the exact requested-time gate: {exc}") from exc
    requested_expected = [index * 0.5 * parameters["tCapillary"] for index in range(41)]
    if not _vectors_match(requested_times, requested_expected, TIME_TOLERANCE_S):
        raise StaticShapeCaptureError("native requested grid is not the frozen 41-point 0..20Tc grid")

    samples: list[dict[str, Any]] = []
    metric_errors: list[str] = []
    for time_index, time_s in enumerate(raw.times_s):
        try:
            profile = extract_upper_interface_profile(raw.profile_columns_at(time_index))
            contact = extract_substrate_contact_line(
                raw.substrate_samples_at(time_index),
                maximum_sample_spacing_m=parameters["epsPF"] / 4.0)
            samples.append({
                "time_s": float(time_s),
                "phase1_volume_m3": float(raw.phase1_volume_m3[time_index]),
                "contact_line_arclength_m": contact["arclength_m"],
                "maximum_speed_m_s": float(raw.maximum_speed_m_s[time_index]),
                "upper_profile": profile,
                "contact_line_readback": contact,
            })
        except ShapeMetricsError as exc:
            metric_errors.append(f"time_index={time_index},time_s={float(time_s):.17g}: {exc}")
            break
    if metric_errors:
        shape_gate: dict[str, Any] = {"status": "FAIL", "errors": metric_errors,
                                      "failure_scope": "native raw field mapping/branch extraction"}
    else:
        try:
            shape_gate = evaluate_shape_history(
                samples,
                capillary_time_s=parameters["tCapillary"],
                analytic_initial_volume_m3=expected_volume,
                epsilon_m=parameters["epsPF"],
                surface_tension_n_per_m=parameters["sigma0"],
                glue_viscosity_pa_s=parameters["muGlue"],
                time_tolerance_s=TIME_TOLERANCE_S)
        except (ShapeMetricsError, TypeError, ValueError) as exc:
            shape_gate = {"status": "FAIL", "errors": [str(exc)],
                          "failure_scope": "registered history gate evaluation"}

    return {
        "schema": "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V1",
        "status": "NATIVE_CAPTURE_DECODED",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED",
        "model_tag": model_tag,
        "case_id": expected_case_id,
        "raw_capture": {"path": str(raw.path), "sha256": raw.sha256,
                        "size_bytes": raw.size_bytes, "stored_time_count": raw.time_count,
                        "radial_count": raw.radial_count,
                        "profile_point_count": raw.profile_point_count,
                        "wall_point_count": raw.wall_point_count},
        "time_grid": time_grid,
        # Carry the native parameter values/units through the analysis
        # boundary. The v1 capture decoder is explicitly baseline-only; this
        # metadata prevents later sensitivity captures from being silently
        # interpreted with the 8 um grid/resolution contract.
        "parameters_and_units": {
            name: {"value_si": float(parameters[name]),
                  "unit": str(capture_result["parameters_and_units"][name]["unit"])}
            for name in parameters
        },
        "capture_grid_protocol": {
            "schema": "W24SHAP1/v1",
            "epsilon_m": parameters["epsPF"],
            "maximum_spacing_m": parameters["epsPF"] / 4.0,
            "grid_policy": "baseline-only fixed spacing; sensitivity variants require a versioned variable-grid decoder",
        },
        "analytic_initial_glue_volume_m3": capture_result.get("analytic_initial_glue_volume_m3"),
        "paired_flat_analytic_volume_m3": capture_result.get("paired_flat_analytic_volume_m3"),
        "shape_history_gate": shape_gate,
        "native_result_feature_readbacks": {
            "profile": dict(profile_meta["feature_readback"]),
            "substrate": dict(substrate_meta["feature_readback"]),
            "phase1_volume": dict(capture_result["phase1_volume"]["feature_readback"]),
            "maximum_speed": dict(capture_result["maximum_speed"]["feature_readback"]),
        },
    }


def capture_static_shape_history(
    managed_runner: Any,
    binding: Any,
    *,
    case_id: str,
    expected_capture_source_sha256: str,
) -> tuple[Any, dict[str, Any]]:
    """Dispatch the read-only capture through an already-registered managed runner.

    The supplied runner must already have passed its production session,
    project, Worker-epoch, and ModelRef checks. Source is copied to the exact
    registered project workspace without overwrite, and its frozen SHA must
    match before any Worker request is submitted.
    """
    if case_id not in {"flat", "step"}:
        raise StaticShapeCaptureError("case_id must be exactly flat or step")
    if not _is_sha256(expected_capture_source_sha256):
        raise StaticShapeCaptureError("an exact frozen capture-source SHA-256 is required")
    source = CAPTURE_SOURCE
    if source.is_symlink() or not source.is_file() or _sha256_file(source) != expected_capture_source_sha256:
        raise StaticShapeCaptureError("live capture Java source does not match the caller's frozen SHA")
    workspace = Path(managed_runner.workspace)
    if workspace.is_symlink():
        raise StaticShapeCaptureError("registered project workspace must not be a symlink")
    workspace = workspace.resolve(strict=True)
    outputs = workspace / "outputs"
    if outputs.is_symlink() or not outputs.is_dir() or outputs.resolve(strict=True) != outputs:
        raise StaticShapeCaptureError("registered outputs directory is missing or aliased")
    copied_source = _copy_project_source(workspace, source, expected_capture_source_sha256)
    capture_id = uuid4().hex
    output_path = outputs / f"static_shape_history_{case_id}_{capture_id}.w24bin"
    if output_path.exists() or output_path.is_symlink():
        raise StaticShapeCaptureError("unique native history output path already exists")

    response = managed_runner._dispatch("operation_call", {
        "operation_id": "code.execute_java",
        "arguments": {
            "source_artifact": copied_source.name,
            "entrypoint": "W24StaticShapeHistoryCapture#run",
            "arguments": {"action": "capture", "case_id": case_id,
                          "workspace_path": str(workspace), "path": str(output_path)},
            "mode": "trusted",
        },
    }, binding=binding, worker_required=True)
    native_result = managed_runner.setup_runner._java_action_readback(
        response, "W24 static-shape raw history capture")
    updated_binding = managed_runner._updated_binding(binding, response)
    if (native_result.get("status") != "NATIVE_RAW_STATIC_SHAPE_HISTORY_CAPTURED" or
            native_result.get("model_tag") != binding.model_tag or
            native_result.get("native_study_run_calls_this_action") != 0):
        raise StaticShapeCaptureError("managed Java action did not return the exact read-only capture result")
    artifact = native_result.get("binary_artifact")
    if not isinstance(artifact, Mapping) or artifact.get("path") != str(output_path):
        raise StaticShapeCaptureError("native Java result did not bind the unique project output path")
    raw_path = _project_child(workspace, Path(str(artifact["path"])), must_exist=True)
    raw = decode_native_history(
        raw_path, expected_sha256=str(artifact.get("sha256", "")),
        expected_size_bytes=artifact.get("size_bytes"))
    analysis = analyze_native_capture(native_result, raw, expected_case_id=case_id)
    analysis["managed_binding"] = updated_binding.as_record()
    analysis["source_copy"] = {"path": str(copied_source),
                               "sha256": expected_capture_source_sha256}
    evidence_dir = Path(managed_runner.evidence_dir) / "raw_history_capture"
    evidence_dir.mkdir(mode=0o700, exist_ok=True)
    evidence_file = evidence_dir / f"capture_{capture_id}.json"
    _write_json_exclusive(evidence_file, analysis)
    analysis["analysis_receipt_path"] = str(evidence_file)
    return updated_binding, analysis


def _copy_project_source(workspace: Path, source: Path, expected_sha256: str) -> Path:
    destination = _project_child(workspace, workspace / source.name, must_exist=False)
    if destination.exists():
        if destination.is_symlink() or not destination.is_file() or _sha256_file(destination) != expected_sha256:
            raise StaticShapeCaptureError("project already contains a different capture Java source; refusing overwrite")
        return destination
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
    if _sha256_file(destination) != expected_sha256:
        raise StaticShapeCaptureError("project capture Java source copy failed its frozen SHA check")
    return destination


def _validate_capture_parameters(receipt: Mapping[str, Any]) -> dict[str, float]:
    raw = receipt.get("parameters_and_units")
    if not isinstance(raw, Mapping):
        raise StaticShapeCaptureError("native capture omitted parameter value/unit readbacks")
    required = {
        "Rdrop": (500e-6, "m"), "hFlat": (100e-6, "m"), "Rbox": (1.25e-3, "m"),
        "Hbox": (0.75e-3, "m"), "epsPF": (8e-6, "m"), "Rmesa": (300e-6, "m"),
        "hMesa": (40e-6, "m"), "zStepTop": (114.4e-6, "m"),
        "tCapillary": (1.0 / 60.0, "s"), "rhoGlue": (1200.0, "kg/m^3"),
        "muGlue": (1.0, "Pa*s"), "rhoGas": (1.2, "kg/m^3"),
        "muGas": (0.018, "Pa*s"), "sigma0": (0.03, "N/m"),
    }
    values: dict[str, float] = {}
    for name, (expected, unit) in required.items():
        row = raw.get(name)
        value = row.get("value_si") if isinstance(row, Mapping) else None
        if (not isinstance(row, Mapping) or row.get("unit") != unit or
                isinstance(value, bool) or not isinstance(value, (int, float)) or
                not math.isfinite(float(value)) or
                abs(float(value) - expected) > max(1e-14, abs(expected) * 1e-12)):
            raise StaticShapeCaptureError(f"native parameter value/unit readback differs from the frozen baseline: {name}")
        values[name] = float(value)
    expected_step = values["hFlat"] + (values["Rmesa"] / values["Rdrop"]) ** 2 * values["hMesa"]
    if abs(values["zStepTop"] - expected_step) > 1e-14:
        raise StaticShapeCaptureError("native step top does not preserve the equal-volume geometry formula")
    return values


def _validate_native_feature(value: Any, *, role: str, expression: str, unit: str,
                            dimension: int, entity_ids: list[int],
                            axisymmetric_property: tuple[str, str] | None = None) -> None:
    if not isinstance(value, Mapping) or value.get("expression") != expression or value.get("unit") != unit:
        raise StaticShapeCaptureError(f"native {role} metric expression/unit provenance is invalid")
    feature = value.get("feature_readback")
    expected_type = {"profile": "Interp", "substrate": "Interp",
                     "phase1_volume": "IntVolume", "maximum_speed": "MaxVolume"}.get(role)
    if (not isinstance(feature, Mapping) or feature.get("type") != expected_type or
            feature.get("expression") != [expression] or feature.get("unit") != [unit] or
            feature.get("solnum") != "all" or feature.get("complex") is not False or
            feature.get("entity_dimension") != dimension or feature.get("entity_ids") != entity_ids):
        raise StaticShapeCaptureError(f"native {role} numerical feature readback is incomplete or misselected")
    if axisymmetric_property is not None:
        key, expected = axisymmetric_property
        if feature.get("measure_property") != key or feature.get("measure_value") != expected:
            raise StaticShapeCaptureError(f"native {role} axisymmetric measure property is not read back")


def _validate_wall_geometry(raw: RawStaticShapeHistory, case_id: str,
                            parameters: Mapping[str, float]) -> None:
    r = raw.wall_r_m
    z = raw.wall_z_m
    spacing = parameters["epsPF"] / 4.0
    for i in range(1, len(r)):
        if math.hypot(r[i] - r[i - 1], z[i] - z[i - 1]) > spacing * (1.0 + 1e-12):
            raise StaticShapeCaptureError("native wall sample spacing exceeds the frozen epsilon/4 resolution")
    if case_id == "flat":
        if any(abs(value) > 1e-12 for value in z) or r[0] != 0.0 or abs(r[-1] - parameters["Rbox"]) > 1e-12:
            raise StaticShapeCaptureError("flat native wall path does not cover the complete base from axis to box edge")
        if any(r[i] <= r[i - 1] for i in range(1, len(r))):
            raise StaticShapeCaptureError("flat native wall path is not strictly radial")
        return
    r_mesa, h_mesa, r_box = parameters["Rmesa"], parameters["hMesa"], parameters["Rbox"]
    top_corners = [i for i, (ri, zi) in enumerate(zip(r, z))
                   if abs(ri - r_mesa) <= 1e-12 and abs(zi - h_mesa) <= 1e-12]
    base_corners = [i for i, (ri, zi) in enumerate(zip(r, z))
                    if abs(ri - r_mesa) <= 1e-12 and abs(zi) <= 1e-12]
    if (r[0] != 0.0 or abs(z[0] - h_mesa) > 1e-12 or
            abs(r[-1] - r_box) > 1e-12 or abs(z[-1]) > 1e-12 or
            len(top_corners) != 1 or len(base_corners) != 1 or
            not 0 < top_corners[0] < base_corners[0] < len(r) - 1):
        raise StaticShapeCaptureError("step native wall path omits the mesa corners or complete substrate endpoints")
    top, base = top_corners[0], base_corners[0]
    if (any(abs(z[i] - h_mesa) > 1e-12 for i in range(top + 1)) or
            any(r[i] <= r[i - 1] for i in range(1, top + 1)) or
            any(abs(r[i] - r_mesa) > 1e-12 for i in range(top, base + 1)) or
            any(z[i] >= z[i - 1] for i in range(top + 1, base + 1)) or
            any(abs(z[i]) > 1e-12 for i in range(base, len(z))) or
            any(r[i] <= r[i - 1] for i in range(base + 1, len(r)))):
        raise StaticShapeCaptureError("step native wall path is not monotone top→side→lower-base")


def _validate_profile_grid(raw: RawStaticShapeHistory, case_id: str,
                           parameters: Mapping[str, float]) -> None:
    spacing = parameters["epsPF"] / 4.0
    intervals = round(parameters["Rbox"] / spacing)
    if abs(intervals * spacing - parameters["Rbox"]) > 1e-12 or raw.radial_count != intervals:
        raise StaticShapeCaptureError("native radial sample count does not cover the exact fixed 0<=r<Rbox grid")
    for column, radius in enumerate(raw.radii_m):
        expected_radius = (column + 0.5) * spacing
        expected_floor = parameters["hMesa"] if case_id == "step" and expected_radius < parameters["Rmesa"] else 0.0
        expected_z_count = round((parameters["Hbox"] - expected_floor) / spacing) + 1
        if (abs(radius - expected_radius) > 1e-12 or
                abs(raw.floors_m[column] - expected_floor) > 1e-12 or
                len(raw.z_columns_m[column]) != expected_z_count):
            raise StaticShapeCaptureError(f"native profile column {column} differs from the frozen fixed grid/floor contract")
        for level, z_value in enumerate(raw.z_columns_m[column]):
            expected_z = expected_floor + level * spacing
            if abs(z_value - expected_z) > 1e-12:
                raise StaticShapeCaptureError(f"native profile column {column} has a noncanonical z sample")


def _expected_binary_size(time_count: int, radial_count: int, profile_count: int,
                          wall_count: int, total_vertical_count: int) -> int:
    doubles = (3 * time_count + 2 * radial_count + total_vertical_count +
               time_count * profile_count + 3 * wall_count + time_count * wall_count)
    return HEADER.size + 4 * radial_count + 8 * doubles


def _read_exact(stream: Any, count: int) -> bytes:
    raw = stream.read(count)
    if len(raw) != count:
        raise EOFError(f"expected {count} bytes, received {len(raw)}")
    return raw


def _read_one_int(stream: Any) -> int:
    return struct.unpack(">i", _read_exact(stream, 4))[0]


def _read_one_double(stream: Any) -> float:
    return struct.unpack(">d", _read_exact(stream, 8))[0]


def _read_doubles(stream: Any, count: int) -> array.array:
    values = array.array("d")
    left = count
    chunk_values = 16_384
    while left:
        amount = min(left, chunk_values)
        raw = _read_exact(stream, amount * 8)
        chunk = array.array("d")
        chunk.frombytes(raw)
        if sys.byteorder == "little":
            chunk.byteswap()
        values.extend(chunk)
        left -= amount
    return values


def _require_finite(values: array.array, label: str) -> None:
    for value in values:
        if not math.isfinite(value):
            raise StaticShapeCaptureError(f"raw {label} values contain NaN or infinity")


def _strictly_increasing(values: array.array) -> bool:
    return all(right > left for left, right in zip(values, values[1:]))


def _vectors_match(first: Any, second: Any, tolerance: float) -> bool:
    if not isinstance(first, (list, tuple, array.array)) or len(first) != len(second):
        return False
    for a, b in zip(first, second):
        if (isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(float(a)) or
                abs(float(a) - float(b)) > tolerance):
            return False
    return True


def _positive_id_list(values: Any, label: str) -> list[int]:
    if not isinstance(values, list) or not values:
        raise StaticShapeCaptureError(f"native {label} must be a nonempty ID list")
    if any(isinstance(value, bool) or type(value) is not int or value <= 0 for value in values):
        raise StaticShapeCaptureError(f"native {label} contains an invalid entity ID")
    if len(set(values)) != len(values):
        raise StaticShapeCaptureError(f"native {label} contains duplicate IDs")
    return list(values)


def _check_index(index: int, length: int, label: str) -> None:
    if isinstance(index, bool) or type(index) is not int or not 0 <= index < length:
        raise IndexError(f"{label} is outside the raw history")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _project_child(root: Path, path: Path, *, must_exist: bool) -> Path:
    if path.is_symlink():
        raise StaticShapeCaptureError("project artifact path must not be a symlink")
    candidate = path.resolve(strict=must_exist)
    base = root.resolve(strict=True)
    if candidate == base or not candidate.is_relative_to(base):
        raise StaticShapeCaptureError("project artifact path escapes the registered workspace")
    if must_exist and (not candidate.is_file() or candidate.is_symlink()):
        raise StaticShapeCaptureError("project raw artifact is not a regular file")
    return candidate


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
