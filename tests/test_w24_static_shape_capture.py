from __future__ import annotations

import array
import hashlib
import math
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from tools.w24_static_shape_capture import (
    HEADER,
    MAGIC,
    RawStaticShapeHistory,
    StaticShapeCaptureError,
    VARIABLE_GRID_FORMAT_VERSION,
    _grid_definition_digest,
    _validate_common_v2_profile_grid,
    _validate_profile_grid,
    _validate_wall_geometry,
    analyze_native_capture,
    decode_native_history,
)
from tools.w24_static_shape_sensitivity import COMMON_CAPTURE_GRID_MAX_SPACING_M
from tools.w24_static_shape_metrics import extract_upper_interface_profile


def _history_bytes(*, version=1, times=(0.0, 1.0), z_columns=((0.0, 0.5, 1.0), (0.0, 0.5, 1.0)), profile_values=None,
                   wall_arclength=(0.0, 1.0, 2.0), wall_z=(0.0, 0.0, 0.0),
                   volume=(2.0, 2.0), speed=(0.0, 0.0)) -> bytes:
    radial = (0.25, 0.75)
    profile_point_count = sum(len(column) for column in z_columns)
    if profile_values is None:
        profile_values = tuple(float(i) for i in range(len(times) * profile_point_count))
    wall_r = (0.0, 1.0, 2.0)
    wall_point_count = len(wall_r)
    parts = [HEADER.pack(MAGIC, version, len(times), len(radial), profile_point_count, wall_point_count)]

    def put_doubles(values):
        parts.extend(struct.pack(">d", float(value)) for value in values)

    put_doubles(times)
    put_doubles(radial)
    for z in z_columns:
        put_doubles((z[0],))
        parts.append(struct.pack(">i", len(z)))
        put_doubles(z)
    put_doubles(profile_values)
    for arc, radius, height in zip(wall_arclength, wall_r, wall_z):
        put_doubles((arc, radius, height))
    put_doubles([value for _ in times for value in (-1.0, 0.0, 1.0)])
    put_doubles(volume)
    put_doubles(speed)
    return b"".join(parts)


def _write_history(tmp_path: Path, raw_bytes: bytes, *, expected_sha: str | None = None):
    path = tmp_path / "sample.w24bin"
    path.write_bytes(raw_bytes)
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    return path, actual_sha if expected_sha is None else expected_sha


def test_decoder_preserves_native_time_order_columns_and_contact_path(tmp_path):
    payload = _history_bytes()
    path, sha = _write_history(tmp_path, payload)
    history = decode_native_history(path, expected_sha256=sha, expected_size_bytes=len(payload))

    assert list(history.times_s) == [0.0, 1.0]
    assert list(history.radii_m) == [0.25, 0.75]
    assert history.profile_columns_at(1)[1]["phi"] == [9.0, 10.0, 11.0]
    assert history.substrate_samples_at(0)[-1] == {
        "arclength_m": 2.0, "r_m": 2.0, "z_m": 0.0, "phi": 1.0}
    assert list(history.phase1_volume_m3) == [2.0, 2.0]
    assert list(history.maximum_speed_m_s) == [0.0, 0.0]


def test_decoder_accepts_v2_binary_layout_and_keeps_version_identity(tmp_path):
    payload = _history_bytes(version=VARIABLE_GRID_FORMAT_VERSION)
    path, sha = _write_history(tmp_path, payload)
    history = decode_native_history(path, expected_sha256=sha, expected_size_bytes=len(payload))
    assert history.format_version == VARIABLE_GRID_FORMAT_VERSION
    assert history.radial_count == 2


def _common_v2_grid_raw(case_id: str) -> RawStaticShapeHistory:
    limit = COMMON_CAPTURE_GRID_MAX_SPACING_M
    r_box, h_box, r_mesa, h_mesa = 1.25e-3, 0.75e-3, 300e-6, 40e-6
    radial_count = math.ceil(r_box / limit)
    radial_step = r_box / radial_count
    radii = array.array("d", ((index + 0.5) * radial_step for index in range(radial_count)))
    floors = array.array("d", (h_mesa if case_id == "step" and radius < r_mesa else 0.0
                                for radius in radii))
    z_columns = []
    offsets = []
    total = 0
    for floor in floors:
        intervals = math.ceil((h_box - floor) / limit)
        step = (h_box - floor) / intervals
        column = array.array("d", (h_box if level == intervals else floor + level * step
                                    for level in range(intervals + 1)))
        offsets.append(total)
        z_columns.append(column)
        total += len(column)
    return RawStaticShapeHistory(
        path=Path("/tmp/common-grid.w24bin"), sha256="0" * 64, size_bytes=1,
        times_s=array.array("d", [0.0, 1.0]), radii_m=radii, floors_m=floors,
        z_columns_m=tuple(z_columns), profile_offsets=tuple(offsets),
        profile_phi=array.array("d", [0.0]) * (2 * total),
        wall_arclength_m=array.array("d", [0.0, 1.0]),
        wall_r_m=array.array("d", [0.0, 1.0]), wall_z_m=array.array("d", [0.0, 0.0]),
        wall_phi=array.array("d", [0.0, 0.0]),
        phase1_volume_m3=array.array("d", [1.0, 1.0]),
        maximum_speed_m_s=array.array("d", [0.0, 0.0]),
        format_version=VARIABLE_GRID_FORMAT_VERSION)


@pytest.mark.parametrize("case_id", ["flat", "step"])
def test_common_v2_grid_uses_same_radial_coordinates_and_valid_exact_floor_partition(case_id):
    raw = _common_v2_grid_raw(case_id)
    _validate_common_v2_profile_grid(raw, case_id, {
        "Rbox": 1.25e-3, "Hbox": 0.75e-3, "Rmesa": 300e-6, "hMesa": 40e-6,
    })
    assert raw.radial_count == 834
    assert raw.radii_m[-1] < 1.25e-3
    same_case_variant = _common_v2_grid_raw(case_id)
    assert _grid_definition_digest(raw, True) == _grid_definition_digest(same_case_variant, True)
    assert max(right - left for left, right in zip(raw.radii_m, raw.radii_m[1:])) <= 1.5e-6


def test_common_v2_grid_rejects_radius_drift_and_step_floor_mismatch():
    raw = _common_v2_grid_raw("step")
    raw.radii_m[11] += 0.2e-6
    with pytest.raises(StaticShapeCaptureError, match="profile column"):
        _validate_common_v2_profile_grid(raw, "step", {
            "Rbox": 1.25e-3, "Hbox": 0.75e-3, "Rmesa": 300e-6, "hMesa": 40e-6,
        })


@pytest.mark.parametrize("raw_bytes,expected_error", [
    (_history_bytes()[:-1], "truncated|byte length"),
    (_history_bytes() + b"x", "byte length|trailing"),
    (_history_bytes(times=(0.0, float("nan"))), "NaN|nonfinite"),
    (_history_bytes(z_columns=((0.0, float("nan"), 1.0), (0.0, 0.5, 1.0))), "NaN|nonfinite"),
    (_history_bytes(wall_arclength=(0.0, 1.1, 2.0)), "arclength"),
    (_history_bytes(wall_z=(0.0, 0.1, 0.0)), "arclength"),
])
def test_decoder_rejects_corrupt_or_nonphysical_history(tmp_path, raw_bytes, expected_error):
    path, sha = _write_history(tmp_path, raw_bytes)
    with pytest.raises(StaticShapeCaptureError, match=expected_error):
        decode_native_history(path, expected_sha256=sha)


def test_decoder_binds_native_manifest_size_and_digest(tmp_path):
    payload = _history_bytes()
    path, sha = _write_history(tmp_path, payload)
    with pytest.raises(StaticShapeCaptureError, match="SHA-256"):
        decode_native_history(path, expected_sha256="0" * 64)
    with pytest.raises(StaticShapeCaptureError, match="byte count"):
        decode_native_history(path, expected_sha256=sha, expected_size_bytes=len(payload) + 8)


def _fake_raw_for_grid(*, change_radius=False):
    spacing = 2e-6
    radial_count = 625
    radii = array.array("d", ((index + 0.5) * spacing for index in range(radial_count)))
    if change_radius:
        radii[17] += 0.5e-6
    floors = array.array("d", [0.0] * radial_count)
    z = array.array("d", (index * spacing for index in range(376)))
    z_columns = tuple(array.array("d", z) for _ in range(radial_count))
    wall_r = array.array("d", [0.0, 2e-6, 4e-6])
    wall_z = array.array("d", [0.0, 0.0, 0.0])
    return RawStaticShapeHistory(
        path=Path("/tmp/fake.w24bin"), sha256="0" * 64, size_bytes=1,
        times_s=array.array("d", [0.0, 1.0]), radii_m=radii, floors_m=floors,
        z_columns_m=z_columns, profile_offsets=tuple(i * len(z) for i in range(radial_count)),
        profile_phi=array.array("d", [0.0]) * (2 * radial_count * len(z)),
        wall_arclength_m=array.array("d", [0.0, 2e-6, 4e-6]),
        wall_r_m=wall_r, wall_z_m=wall_z, wall_phi=array.array("d", [0.0] * 6),
        phase1_volume_m3=array.array("d", [1.0, 1.0]),
        maximum_speed_m_s=array.array("d", [0.0, 0.0]))


def test_profile_grid_requires_all_frozen_radial_and_vertical_samples():
    parameters = {"epsPF": 8e-6, "Rdrop": 500e-6, "hMesa": 40e-6,
                  "Rmesa": 300e-6, "Hbox": 750e-6}
    parameters["Rbox"] = 1.25e-3
    _validate_profile_grid(_fake_raw_for_grid(), "flat", parameters)
    with pytest.raises(StaticShapeCaptureError, match="fixed grid"):
        _validate_profile_grid(_fake_raw_for_grid(change_radius=True), "flat", parameters)


def _wall_raw(r_values, z_values, arclength):
    return RawStaticShapeHistory(
        path=Path("/tmp/wall.w24bin"), sha256="0" * 64, size_bytes=1,
        times_s=array.array("d", [0.0, 1.0]), radii_m=array.array("d", [1e-6, 3e-6]),
        floors_m=array.array("d", [0.0, 0.0]), z_columns_m=(array.array("d", [0.0, 1.0, 2.0]),) * 2,
        profile_offsets=(0, 3), profile_phi=array.array("d", [0.0] * 12),
        wall_arclength_m=array.array("d", arclength), wall_r_m=array.array("d", r_values),
        wall_z_m=array.array("d", z_values), wall_phi=array.array("d", [0.0] * (2 * len(r_values))),
        phase1_volume_m3=array.array("d", [1.0, 1.0]), maximum_speed_m_s=array.array("d", [0.0, 0.0]))


def test_flat_and_stepped_wall_paths_cover_physical_segments_and_corners():
    spacing = 2e-6
    params = {"epsPF": 8e-6, "Rbox": 1.25e-3, "Rmesa": 300e-6, "hMesa": 40e-6}
    flat_r = [index * spacing for index in range(626)]
    flat_z = [0.0] * len(flat_r)
    flat_arc = [index * spacing for index in range(len(flat_r))]
    _validate_wall_geometry(_wall_raw(flat_r, flat_z, flat_arc), "flat", params)

    r_values = [index * spacing for index in range(151)]
    z_values = [40e-6] * len(r_values)
    for index in range(1, 21):
        r_values.append(300e-6)
        z_values.append(40e-6 - index * spacing)
    for index in range(1, 476):
        r_values.append(300e-6 + index * spacing)
        z_values.append(0.0)
    arclength = [0.0]
    for index in range(1, len(r_values)):
        arclength.append(arclength[-1] + math.hypot(
            r_values[index] - r_values[index - 1], z_values[index] - z_values[index - 1]))
    step_raw = _wall_raw(r_values, z_values, arclength)
    _validate_wall_geometry(step_raw, "step", params)
    with pytest.raises(StaticShapeCaptureError, match="mesa corners"):
        _validate_wall_geometry(_wall_raw(r_values[1:], z_values[1:], arclength[1:]), "step", params)


@pytest.mark.parametrize("case_id", ["flat", "step"])
def test_synthetic_raw_capture_flows_through_native_shape_adapter_without_claiming_acceptance(tmp_path, case_id):
    """Exercise the complete adapter using synthetic fields, never native evidence."""
    spacing = 2e-6
    times = array.array("d", (index * 0.5 / 60.0 for index in range(41)))
    radii = array.array("d", ((index + 0.5) * spacing for index in range(625)))
    h_mesa = 40e-6
    r_mesa = 300e-6
    r_drop = 500e-6
    h_flat = 100e-6
    z_step_top = h_flat + (r_mesa / r_drop) ** 2 * h_mesa
    floors = array.array("d", (
        h_mesa if case_id == "step" and radius < r_mesa else 0.0 for radius in radii))
    z_columns = tuple(array.array("d", (
        floors[column] + index * spacing
        for index in range(round((750e-6 - floors[column]) / spacing) + 1)))
        for column in range(len(radii)))
    offsets = []
    profile_count = 0
    for z in z_columns:
        offsets.append(profile_count)
        profile_count += len(z)

    contact_radius = 600e-6
    flat_volume = math.pi * r_drop ** 2 * h_flat
    interface_height = (flat_volume / (math.pi * contact_radius ** 2) if case_id == "flat" else
                        (flat_volume / math.pi + r_mesa ** 2 * h_mesa) / contact_radius ** 2)
    profile_phi = array.array("d")
    for _ in times:
        for radius, z_values in zip(radii, z_columns):
            for z_value in z_values:
                if radius >= contact_radius:
                    profile_phi.append(1.0)
                elif z_value < interface_height:
                    profile_phi.append(-1.0)
                elif z_value > interface_height:
                    profile_phi.append(1.0)
                else:
                    profile_phi.append(0.0)

    wall_r: list[float] = []
    wall_z: list[float] = []
    if case_id == "flat":
        wall_r = [index * spacing for index in range(626)]
        wall_z = [0.0] * len(wall_r)
    else:
        wall_r = [index * spacing for index in range(151)]
        wall_z = [h_mesa] * len(wall_r)
        for index in range(1, 21):
            wall_r.append(r_mesa)
            wall_z.append(h_mesa - index * spacing)
        for index in range(1, 476):
            wall_r.append(r_mesa + index * spacing)
            wall_z.append(0.0)
    wall_arc = [0.0]
    for index in range(1, len(wall_r)):
        wall_arc.append(wall_arc[-1] + math.hypot(
            wall_r[index] - wall_r[index - 1], wall_z[index] - wall_z[index - 1]))
    contact_arc = contact_radius if case_id == "flat" else contact_radius + h_mesa
    one_wall_row = array.array("d", (
        -1.0 if value < contact_arc else (0.0 if abs(value - contact_arc) <= 1e-15 else 1.0)
        for value in wall_arc))
    wall_phi = one_wall_row * len(times)
    initial_volume = (flat_volume if case_id == "flat" else
                      math.pi * (r_drop ** 2 * z_step_top - r_mesa ** 2 * h_mesa))
    volume = array.array("d", [initial_volume]) * len(times)
    speed = array.array("d", [0.0]) * len(times)
    source_bytes = b"synthetic-only; no COMSOL result"
    raw_path = tmp_path / f"synthetic-{case_id}.w24bin"
    raw_path.write_bytes(source_bytes)
    raw_sha = hashlib.sha256(source_bytes).hexdigest()
    raw = RawStaticShapeHistory(
        path=raw_path, sha256=raw_sha, size_bytes=len(source_bytes),
        times_s=times, radii_m=radii, floors_m=floors,
        z_columns_m=z_columns, profile_offsets=tuple(offsets), profile_phi=profile_phi,
        wall_arclength_m=array.array("d", wall_arc), wall_r_m=array.array("d", wall_r),
        wall_z_m=array.array("d", wall_z), wall_phi=wall_phi,
        phase1_volume_m3=volume, maximum_speed_m_s=speed)

    parameters = {
        "Rdrop": (r_drop, "m"), "hFlat": (h_flat, "m"), "Rbox": (1.25e-3, "m"),
        "Hbox": (750e-6, "m"), "epsPF": (8e-6, "m"), "Rmesa": (r_mesa, "m"),
        "hMesa": (h_mesa, "m"), "zStepTop": (z_step_top, "m"),
        "tCapillary": (1.0 / 60.0, "s"), "rhoGlue": (1200.0, "kg/m^3"),
        "muGlue": (1.0, "Pa*s"), "rhoGas": (1.2, "kg/m^3"),
        "muGas": (0.018, "Pa*s"), "sigma0": (0.03, "N/m"),
    }
    parameter_readback = {
        name: {"value_si": value, "unit": unit} for name, (value, unit) in parameters.items()}
    wetting = ({"sel_wet_flat_base": [2]} if case_id == "flat" else {
        "sel_wet_mesa_top": [2], "sel_wet_mesa_side": [3], "sel_wet_lower_base": [4]})
    glue_ids, gas_ids, all_ids = [1], [2], [1, 2]
    wet_union = sorted(value for values in wetting.values() for value in values)

    def feature(expression, unit, dimension, ids, feature_type):
        return {
            "type": feature_type, "expression": [expression], "unit": [unit],
            "solnum": "all", "complex": False, "entity_dimension": dimension,
            "entity_ids": ids, "measure_property": "intvolume" if feature_type == "IntVolume" else None,
            "measure_value": "on" if feature_type == "IntVolume" else None,
        }

    profile_meta = {
        "expression": "pf.phipf", "unit": "1",
        "shape": [1, len(times), len(profile_phi) // len(times)],
        "radial_point_count": len(radii), "profile_point_count": len(profile_phi) // len(times),
        "radial_support": "cell-centered fixed samples within 0 <= r < Rbox",
        "radial_support_upper_exclusive_m": 1.25e-3,
        "feature_readback": feature("pf.phipf", "1", 2, all_ids, "Interp"),
    }
    substrate_meta = {
        "expression": "pf.phipf", "unit": "1", "entity_dimension": 1,
        "entity_ids": wet_union, "shape": [1, len(times), len(wall_arc)],
        "feature_readback": feature("pf.phipf", "1", 1, wet_union, "Interp"),
    }
    volume_meta = {
        "expression": "(1-pf.phipf)/2", "unit": "m^3", "entity_ids": all_ids,
        "axisymmetric_measure": "intvolume=on", "shape": [1, len(times)],
        "feature_readback": feature("(1-pf.phipf)/2", "m^3", 2, all_ids, "IntVolume"),
    }
    speed_meta = {
        "expression": "sqrt(spf.u^2+spf.w^2)", "unit": "m/s", "entity_ids": all_ids,
        "shape": [1, len(times)],
        "feature_readback": feature("sqrt(spf.u^2+spf.w^2)", "m/s", 2, all_ids, "MaxVolume"),
    }
    native = {
        "schema": "W24_NATIVE_STATIC_SHAPE_RAW_HISTORY_V1",
        "status": "NATIVE_RAW_STATIC_SHAPE_HISTORY_CAPTURED",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "native_study_run_calls_this_action": 0, "model_tag": "synthetic_model",
        "case_id": case_id,
        "binary_artifact": {
            "path": str(raw_path), "sha256": raw_sha, "size_bytes": len(source_bytes),
            "layout": "W24SHAP1/v1 big-endian doubles; see decoder contract",
        },
        "parameters_and_units": parameter_readback,
        "analytic_initial_glue_volume_m3": initial_volume,
        "paired_flat_analytic_volume_m3": flat_volume,
        "geometry": {
            "dimension": 2, "axisymmetric": True, "domain_count": 2, "boundary_count": 8,
            "glue_domain_ids": glue_ids, "gas_domain_ids": gas_ids,
            "all_fluid_domain_ids": all_ids, "axis_boundary_ids": [1],
            "wetted_boundary_ids": wetting,
        },
        "profile_field": profile_meta, "substrate_field": substrate_meta,
        "phase1_volume": volume_meta, "maximum_speed": speed_meta,
        "requested_times_s": list(times), "stored_times_s": list(times),
    }
    result = analyze_native_capture(native, raw, expected_case_id=case_id)

    assert result["shape_history_gate"]["status"] == "STABLE_WINDOW_PASS"
    stable_profile = extract_upper_interface_profile(raw.profile_columns_at(32))
    assert any(radius > r_drop and support == "WET_INTERFACE"
               for radius, support in zip(stable_profile["radius_m"], stable_profile["support"]))
    assert result["native_acceptance"] == "NOT_ESTABLISHED_CAPTURE_ONLY"
    assert result["scientific_acceptance"] == "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED"

    disturbed_values = array.array("d", raw.profile_phi)
    disturbed_time_index = 33
    row_start = disturbed_time_index * raw.profile_point_count
    for radius, z_values, offset in zip(raw.radii_m, raw.z_columns_m, raw.profile_offsets):
        if r_drop < radius < contact_radius:
            moved_height = interface_height + 5e-6
            for level, z_value in enumerate(z_values):
                disturbed_values[row_start + offset + level] = (
                    -1.0 if z_value < moved_height else (1.0 if z_value > moved_height else 0.0))
    disturbed_raw = replace(raw, profile_phi=disturbed_values)
    disturbed = analyze_native_capture(native, disturbed_raw, expected_case_id=case_id)
    assert disturbed["shape_history_gate"]["status"] == "STABLE_WINDOW_FAIL"
    assert any(error.startswith("interface_displacement_exceeds_limit:")
               for error in disturbed["shape_history_gate"]["errors"])

    glue_only = dict(native)
    glue_only_volume = dict(volume_meta)
    glue_only_volume["entity_ids"] = glue_ids
    glue_only_readback = dict(volume_meta["feature_readback"])
    glue_only_readback["entity_ids"] = glue_ids
    glue_only_volume["feature_readback"] = glue_only_readback
    glue_only["phase1_volume"] = glue_only_volume
    with pytest.raises(StaticShapeCaptureError, match="misselected"):
        analyze_native_capture(glue_only, raw, expected_case_id=case_id)
