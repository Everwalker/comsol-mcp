from __future__ import annotations

import array
import math
from pathlib import Path

import pytest

from tools import w24_static_shape_sensitivity_comparison as comparison
from tools.w24_static_shape_capture import RawStaticShapeHistory
from tools.w24_static_shape_sensitivity import COMMON_CAPTURE_GRID_MAX_SPACING_M


def _raw(case_id: str, *, interface_shift_m: float = 0.0,
         contact_shift_m: float = 0.0, support_radius_m: float = 500e-6,
         wall_contact_radius_m: float | None = None,
         volume_scale: float = 1.0) -> RawStaticShapeHistory:
    r_box, h_box, r_mesa, h_mesa = 1.25e-3, 0.75e-3, 300e-6, 40e-6
    radial_count = math.ceil(r_box / COMMON_CAPTURE_GRID_MAX_SPACING_M)
    radial_spacing = r_box / radial_count
    radii = array.array("d", ((index + 0.5) * radial_spacing for index in range(radial_count)))
    floors = array.array("d", (h_mesa if case_id == "step" and radius < r_mesa else 0.0
                                for radius in radii))
    z_columns = tuple(array.array("d", (floor, 350e-6 + 2.0 * interface_shift_m, h_box))
                      for floor in floors)
    profile_offsets = tuple(3 * index for index in range(radial_count))
    one_profile = array.array("d")
    for radius in radii:
        one_profile.extend((-1.0, -1.0, 1.0) if radius < support_radius_m else (1.0, 1.0, 1.0))
    profile_phi = one_profile * 2

    spacing = COMMON_CAPTURE_GRID_MAX_SPACING_M
    if case_id == "flat":
        count = math.ceil(r_box / spacing)
        wall_r = [index * r_box / count for index in range(count + 1)]
        wall_z = [0.0] * len(wall_r)
    else:
        top_count = math.ceil(r_mesa / spacing)
        side_count = math.ceil(h_mesa / spacing)
        base_count = math.ceil((r_box - r_mesa) / spacing)
        wall_r = [index * r_mesa / top_count for index in range(top_count + 1)]
        wall_z = [h_mesa] * len(wall_r)
        for index in range(1, side_count + 1):
            wall_r.append(r_mesa)
            wall_z.append(h_mesa - index * h_mesa / side_count)
        for index in range(1, base_count + 1):
            wall_r.append(r_mesa + index * (r_box - r_mesa) / base_count)
            wall_z.append(0.0)
    wall_arclength = [0.0]
    for index in range(1, len(wall_r)):
        wall_arclength.append(wall_arclength[-1] + math.hypot(
            wall_r[index] - wall_r[index - 1], wall_z[index] - wall_z[index - 1]))
    contact_radius = support_radius_m if wall_contact_radius_m is None else wall_contact_radius_m
    contact_arc = contact_radius + (h_mesa if case_id == "step" else 0.0) + contact_shift_m
    one_wall = array.array("d", (-1.0 if distance < contact_arc else 1.0
                                  for distance in wall_arclength))
    volume = math.pi * (500e-6) ** 2 * 100e-6 * volume_scale
    return RawStaticShapeHistory(
        path=Path("/tmp/synthetic-sensitivity-history.w24bin"), sha256="0" * 64,
        size_bytes=1, times_s=array.array("d", [0.0, 20.0 / 60.0]),
        radii_m=radii, floors_m=floors, z_columns_m=z_columns,
        profile_offsets=profile_offsets, profile_phi=profile_phi,
        wall_arclength_m=array.array("d", wall_arclength), wall_r_m=array.array("d", wall_r),
        wall_z_m=array.array("d", wall_z), wall_phi=one_wall * 2,
        phase1_volume_m3=array.array("d", [volume, volume]),
        maximum_speed_m_s=array.array("d", [0.0, 0.0]), format_version=2,
    )


def _captured(label: str) -> dict:
    return {"label": label, "shape_history_gate": {"status": "STABLE_WINDOW_PASS"}}


def _install_loader(monkeypatch, baseline: RawStaticShapeHistory, variant: RawStaticShapeHistory,
                    *, case_id: str = "flat"):
    raw_by_label = {"baseline": baseline, "variant": variant}

    def load(capture, *, configuration_id, case_id: str, expected_project_id, expected_workspace):
        label = capture["label"]
        config = next(row for row in comparison.sensitivity_configurations()
                      if row.configuration_id == configuration_id)
        return ({
            "model_tag": f"model-{label}", "session_id": "session-1",
            "server_instance_id": "worker-1", "generation": 2,
            "epsilon_m": config.epsilon_m, "grid_definition_sha256": "1" * 64,
            "capture_sha256": ("2" if label == "baseline" else "3") * 64,
            "raw_sha256": ("4" if label == "baseline" else "5") * 64,
        }, raw_by_label[label])

    monkeypatch.setattr(comparison, "_load_sensitivity_case", load)


def _compare(monkeypatch, baseline_raw, variant_raw, *, case_id="flat"):
    _install_loader(monkeypatch, baseline_raw, variant_raw, case_id=case_id)
    return comparison.compare_sensitivity_case_variant(
        _captured("baseline"), _captured("variant"),
        variant_configuration_id="mesh_ratio_1_3", expected_case_id=case_id,
        expected_project_id="project-1", expected_workspace=Path("/tmp/project-1"))


@pytest.mark.parametrize("case_id", ["flat", "step"])
def test_variant_comparison_reports_full_native_wet_dry_support_without_interpolation(
        monkeypatch, case_id):
    result = _compare(monkeypatch, _raw(case_id), _raw(case_id), case_id=case_id)
    assert result["status"] == "PASS"
    assert result["interpolation_used"] is False
    assert result["comparison"]["final_profile_comparison"]["interpolation_used"] is False
    support = result["full_wet_dry_support"]
    assert support["baseline"]["sample_count"] == 834
    assert support["baseline"]["wet_support_point_count"] > 3
    assert support["baseline"]["dry_support_point_count"] > 0
    assert support["difference"]["status"] == "SAME_WET_SUPPORT"
    assert support["difference"]["changed_support_indices"] == []
    assert result["comparison"]["final_profile_comparison"]["common_fraction_of_smaller_support"] == 1.0


@pytest.mark.parametrize(
    "variant_kwargs,expected_error",
    [
        ({"interface_shift_m": 11e-6}, "final_interface_height_difference_exceeds_limit"),
        ({"contact_shift_m": 5e-6}, "final_contact_line_difference_exceeds_limit"),
        ({"volume_scale": 1.002}, "final_volume_difference_exceeds_limit"),
    ],
)
def test_variant_comparison_keeps_height_contact_and_volume_as_independent_gates(
        monkeypatch, variant_kwargs, expected_error):
    result = _compare(monkeypatch, _raw("flat"), _raw("flat", **variant_kwargs))
    assert result["status"] == "FAIL"
    assert expected_error in result["comparison"]["errors"]
    assert result["comparison"]["contact_line_gate_separate"] is True
    assert result["comparison"]["volume_gate_separate"] is True


def test_variant_comparison_reports_wet_support_change_and_keeps_the_frozen_common_support_gate(monkeypatch):
    result = _compare(monkeypatch, _raw("flat"),
                      _raw("flat", support_radius_m=400e-6, wall_contact_radius_m=500e-6))
    assert result["status"] == "PASS"
    profile = result["comparison"]["final_profile_comparison"]
    assert profile["common_fraction_of_smaller_support"] == 1.0
    difference = result["full_wet_dry_support"]["difference"]
    assert difference["status"] == "WET_SUPPORT_DIFFERENCE_REPORTED"
    assert len(difference["changed_support_indices"]) > 0
