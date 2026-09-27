from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tools.w24_static_shape_capture import HEADER, MAGIC, StaticShapeCaptureError
from tools.w24_static_shape_pair_comparison import (
    _describe_profile_pair,
    capture_solved_flat_step_pair,
    compare_flat_step_captures,
)


def _binary_history() -> bytes:
    times = (0.0, 1.0)
    radii = (0.5e-6, 1.5e-6, 2.5e-6)
    columns = ((0.0, 1e-6, 2e-6),) * 3
    profile = (-1.0, -1.0, 1.0) * (len(times) * len(radii))
    wall = ((0.0, 0.0, 0.0), (1e-6, 1e-6, 0.0), (2e-6, 2e-6, 0.0))
    wall_phi = (-1.0, -1.0, 1.0) * len(times)
    parts = [HEADER.pack(MAGIC, 1, 2, 3, 9, 3)]

    def doubles(values):
        parts.extend(struct.pack(">d", float(value)) for value in values)

    doubles(times)
    doubles(radii)
    for column in columns:
        doubles((column[0],))
        parts.append(struct.pack(">i", len(column)))
        doubles(column)
    doubles(profile)
    for arc, radius, height in wall:
        doubles((arc, radius, height))
    doubles(wall_phi)
    doubles((1e-12, 1e-12))
    doubles((0.0, 0.0))
    return b"".join(parts)


def _binding(case: str, *, epoch: str = "server-e1") -> dict[str, Any]:
    tag = f"model-{case}"
    return {
        "project_id": "project-shape",
        "session_id": "session-shape",
        "revision": 3,
        "model_tag": tag,
        "model_ref": {"model_tag": tag, "session_id": "session-shape",
                      "server_instance_id": epoch, "generation": 1},
    }


def _analysis(path: Path, case: str, binding: dict[str, Any], *, stable="STABLE_WINDOW_PASS"):
    payload = path.read_bytes()
    return {
        "schema": "W24_STATIC_SHAPE_NATIVE_CAPTURE_ANALYSIS_V1",
        "status": "NATIVE_CAPTURE_DECODED",
        "native_acceptance": "NOT_ESTABLISHED_CAPTURE_ONLY",
        "scientific_acceptance": "NOT_ESTABLISHED_SENSITIVITY_AND_INDEPENDENT_REVIEW_REQUIRED",
        "case_id": case,
        "model_tag": binding["model_tag"],
        "managed_binding": binding,
        "shape_history_gate": {"status": stable},
        "parameters_and_units": {"epsPF": {"value_si": 8e-6, "unit": "m"}},
        "capture_grid_protocol": {"schema": "W24SHAP1/v1", "epsilon_m": 8e-6,
                                   "maximum_spacing_m": 2e-6},
        "raw_capture": {
            "path": str(path), "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload), "stored_time_count": 2,
            "radial_count": 3, "profile_point_count": 9, "wall_point_count": 3,
        },
        "native_result_feature_readbacks": {"profile": {"expression": "pf.phipf"}},
    }


def test_descriptive_pair_reports_support_and_height_without_cross_shape_gate():
    first = {"radius_m": [1.0, 2.0, 3.0],
             "interface_height_m": [4.0, 5.0, None],
             "support": ["WET_INTERFACE", "WET_INTERFACE", "DRY_SUPPORT"]}
    second = {"radius_m": [1.0, 2.0, 3.0],
              "interface_height_m": [4.5, 5.5, 6.0],
              "support": ["WET_INTERFACE", "WET_INTERFACE", "WET_INTERFACE"]}
    result = _describe_profile_pair(first, second)
    assert result["status"] == "DESCRIPTIVE_PROFILE_DIFFERENCE"
    assert result["common_wet_support_count"] == 2
    assert result["max_abs_height_difference_m"] == pytest.approx(0.5)
    assert result["cross_shape_threshold_applied"] is False


@pytest.mark.parametrize("height,label", [
    (1.0, "UNKNOWN_SUPPORT"),
    (float("nan"), "WET_INTERFACE"),
    (1.0, "DRY_SUPPORT"),
])
def test_descriptive_pair_rejects_invalid_support_or_height(height, label):
    first = {"radius_m": [1.0, 2.0], "interface_height_m": [1.0, None],
             "support": ["WET_INTERFACE", "DRY_SUPPORT"]}
    second = {"radius_m": [1.0, 2.0], "interface_height_m": [height, None],
              "support": [label, "DRY_SUPPORT"]}
    with pytest.raises(StaticShapeCaptureError):
        _describe_profile_pair(first, second)


def test_pair_capture_metadata_is_project_and_worker_epoch_bound(tmp_path):
    raw = _binary_history()
    path = tmp_path / "sample.w24bin"
    path.write_bytes(raw)
    flat = _analysis(path, "flat", _binding("flat"))
    step = _analysis(path, "step", _binding("step"))
    result = compare_flat_step_captures(
        flat, step, expected_project_id="project-shape", expected_workspace=tmp_path)
    assert result["status"] == "DESCRIPTIVE_COMPARISON_ONLY"
    assert result["native_acceptance"] == "NOT_ESTABLISHED_CAPTURE_ONLY"
    assert result["flat_step_description"]["cross_shape_threshold_applied"] is False
    assert result["flat_step_description"]["final_relative_phase1_volume_difference"] == 0.0

    step["managed_binding"] = _binding("step", epoch="server-e2")
    with pytest.raises(StaticShapeCaptureError, match="one project and Worker epoch"):
        compare_flat_step_captures(flat, step)


def test_pair_comparison_refuses_capture_from_sensitivity_variant_on_v1_decoder(tmp_path):
    raw = _binary_history()
    path = tmp_path / "sample.w24bin"
    path.write_bytes(raw)
    flat = _analysis(path, "flat", _binding("flat"))
    step = _analysis(path, "step", _binding("step"))
    step["parameters_and_units"]["epsPF"]["value_si"] = 6e-6
    step["capture_grid_protocol"]["epsilon_m"] = 6e-6
    with pytest.raises(StaticShapeCaptureError, match="only accepts the verified 8 um baseline"):
        compare_flat_step_captures(flat, step)


@dataclass(frozen=True)
class _ManagedBinding:
    record: dict[str, Any]

    @property
    def model_tag(self):
        return self.record["model_tag"]

    @property
    def project_id(self):
        return self.record["project_id"]

    @property
    def session_id(self):
        return self.record["session_id"]

    @property
    def model_ref(self):
        return self.record["model_ref"]

    def as_record(self):
        return dict(self.record)


class _Runner:
    project_id = "project-shape"

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.verified: list[str] = []

    def _verify_persisted_binding(self, binding):
        self.verified.append(binding.model_tag)


def test_managed_pair_capture_prevalidates_both_bindings_and_captures_sequentially(tmp_path, monkeypatch):
    import tools.w24_static_shape_capture as capture_module

    raw = _binary_history()
    paths = {}
    for case in ("flat", "step"):
        paths[case] = tmp_path / f"{case}.w24bin"
        paths[case].write_bytes(raw)
    calls = []

    def fake_capture(runner, binding, *, case_id, expected_capture_source_sha256):
        calls.append(case_id)
        record = binding.as_record()
        return binding, _analysis(paths[case_id], case_id, record)

    monkeypatch.setattr(capture_module, "capture_static_shape_history", fake_capture)
    runner = _Runner(tmp_path)
    bindings = {case: _ManagedBinding(_binding(case)) for case in ("flat", "step")}
    result = capture_solved_flat_step_pair(
        runner, bindings, expected_capture_source_sha256="a" * 64)
    assert result["status"] == "BOTH_CASES_CAPTURED_DESCRIPTIVE_ONLY"
    assert calls == ["flat", "step"]
    assert runner.verified == ["model-flat", "model-step"]
    assert result["study_run_submissions"] == "UNCHANGED_READ_ONLY_CAPTURE"


def test_managed_pair_capture_refuses_bad_second_binding_before_any_capture(tmp_path, monkeypatch):
    import tools.w24_static_shape_capture as capture_module

    calls = []
    monkeypatch.setattr(capture_module, "capture_static_shape_history",
                        lambda *args, **kwargs: calls.append("capture"))
    bindings = {"flat": _ManagedBinding(_binding("flat")),
                "step": _ManagedBinding(_binding("step", epoch="server-e2"))}
    with pytest.raises(StaticShapeCaptureError, match="one current Worker epoch"):
        capture_solved_flat_step_pair(
            _Runner(tmp_path), bindings, expected_capture_source_sha256="a" * 64)
    assert calls == []


def test_managed_pair_capture_stops_without_retry_after_second_case_failure(tmp_path, monkeypatch):
    import tools.w24_static_shape_capture as capture_module

    raw = _binary_history()
    path = tmp_path / "flat.w24bin"
    path.write_bytes(raw)
    calls = []

    def fake_capture(runner, binding, *, case_id, expected_capture_source_sha256):
        calls.append(case_id)
        if case_id == "step":
            raise RuntimeError("synthetic lost Worker result")
        return binding, _analysis(path, "flat", binding.as_record())

    monkeypatch.setattr(capture_module, "capture_static_shape_history", fake_capture)
    result = capture_solved_flat_step_pair(
        _Runner(tmp_path), {case: _ManagedBinding(_binding(case)) for case in ("flat", "step")},
        expected_capture_source_sha256="a" * 64)
    assert result["status"] == "PARTIAL_OR_UNKNOWN_NO_RETRY"
    assert result["failed_case"] == "step"
    assert result["completed_cases"] == ["flat"]
    assert calls == ["flat", "step"]
