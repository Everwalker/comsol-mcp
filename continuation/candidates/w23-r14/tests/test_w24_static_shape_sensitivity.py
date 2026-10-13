from __future__ import annotations

import math

import pytest

from tools.w24_static_shape_sensitivity import (
    build_sensitivity_campaign_plan,
    require_capture_protocol,
    sensitivity_configurations,
    sensitivity_submission_slots,
    sensitivity_capture_resource_estimate,
    validate_sensitivity_configurations,
)


def test_frozen_matrix_has_baseline_and_six_named_one_factor_controls():
    rows = sensitivity_configurations()
    assert [row.configuration_id for row in rows] == [
        "baseline", "mesh_ratio_1_3", "mesh_ratio_1_1", "epsilon_6um",
        "epsilon_10um", "step_0_05Tc", "step_0_20Tc",
    ]
    assert all(row.capture_spacing_limit_m == row.epsilon_m / 4 for row in rows)
    assert {row.capture_grid_protocol for row in rows} == {"W24SHAP1/v2-common-grid"}
    assert all(row.common_capture_grid_maximum_spacing_m == 1.5e-6 for row in rows)
    assert rows[3].mesh_hmax_m / rows[3].epsilon_m == pytest.approx(0.5)
    assert rows[4].mesh_hmin_m / rows[4].epsilon_m == pytest.approx(0.25)


def test_sensitivity_plan_counts_every_flat_step_initialization_and_solve():
    plan = build_sensitivity_campaign_plan()
    assert plan["status"] == "PLAN_ONLY_NOT_AUTHORIZED_OR_EXECUTED"
    assert plan["native_acceptance"] == "NOT_RUN"
    assert plan["planned_study_run_submissions"] == 14
    assert plan["shape_cases_per_configuration"] == ["flat", "step"]
    assert "PhaseInitialization" in plan["study_run_scope"]
    assert any("not a native budget" in item for item in plan["not_in_scope_of_this_plan"])


def test_sensitivity_slots_are_configuration_major_and_common_grid_resources_are_bounded():
    slots = sensitivity_submission_slots()
    assert len(slots) == 14
    assert [(row["submission_index"], row["configuration_id"], row["case_id"])
            for row in slots] == [
        (index, configuration.configuration_id, case_id)
        for index, (configuration, case_id) in enumerate(
            ((row, case_id) for row in sensitivity_configurations()
             for case_id in ("flat", "step")), 1)
    ]
    estimate = sensitivity_capture_resource_estimate()
    assert estimate["time_count_per_history"] == 41
    assert estimate["radial_count"] == 834
    assert estimate["maximum_single_history_bytes"] < 1024 * 1024 * 1024
    assert estimate["total_raw_capture_bytes"] == 1_957_689_832
    assert estimate["all_requested_times_and_double_precision_retained"] is True


def test_variant_capture_contract_cannot_fall_back_to_baseline_decoder():
    rows = {row.configuration_id: row for row in sensitivity_configurations()}
    with pytest.raises(ValueError, match="requires W24SHAP1/v2-common-grid"):
        require_capture_protocol(rows["baseline"], "W24SHAP1/v1-baseline")
    require_capture_protocol(rows["baseline"], "W24SHAP1/v2-common-grid")
    require_capture_protocol(rows["epsilon_6um"], "W24SHAP1/v2-common-grid")


def test_configuration_validator_rejects_ratio_drift_and_nonfinite_input():
    rows = list(sensitivity_configurations())
    rows[1] = rows[1].__class__(**{**rows[1].__dict__, "mesh_hmax_m": 4e-6})
    with pytest.raises(ValueError, match="mesh-resolution factor"):
        validate_sensitivity_configurations(tuple(rows))

    rows = list(sensitivity_configurations())
    rows[3] = rows[3].__class__(**{**rows[3].__dict__, "epsilon_m": math.nan})
    with pytest.raises(ValueError, match="nonpositive or nonfinite"):
        validate_sensitivity_configurations(tuple(rows))
