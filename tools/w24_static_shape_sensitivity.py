"""Frozen, non-executing W24 flat/step sensitivity configuration contract.

The configurations are a plan only. Every row represents one flat and one
stepped Phase Initialization + transient Study.run, so the seven-config plan
requires fourteen Study.run submissions. No native budget is implied here.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


R_DROP_M = 500e-6
R_BOX_M = 1.25e-3
H_BOX_M = 0.75e-3
R_MESA_M = 300e-6
H_MESA_M = 40e-6
BASELINE_EPSILON_M = 8e-6
BASELINE_HMAX_M = BASELINE_EPSILON_M / 2.0
BASELINE_HMIN_M = BASELINE_EPSILON_M / 4.0
BASELINE_MAX_STEP_OVER_TC = 0.10
BASELINE_CAPILLARY_TIME_S = 1.0 / 60.0
COMMON_CAPTURE_GRID_MAX_SPACING_M = 6e-6 / 4.0
SENSITIVITY_CAPTURE_PROTOCOL = "W24SHAP1/v2-common-grid"
SENSITIVITY_CASE_ORDER = ("flat", "step")


@dataclass(frozen=True)
class StaticShapeSensitivityConfiguration:
    configuration_id: str
    factor: str
    epsilon_m: float
    mesh_hmax_m: float
    mesh_hmin_m: float
    maximum_step_over_capillary_time: float
    capture_spacing_limit_m: float
    capture_grid_protocol: str
    interpretation: str

    @property
    def maximum_step_s(self) -> float:
        return self.maximum_step_over_capillary_time * BASELINE_CAPILLARY_TIME_S

    @property
    def common_capture_grid_maximum_spacing_m(self) -> float:
        return COMMON_CAPTURE_GRID_MAX_SPACING_M

    def as_record(self) -> dict[str, Any]:
        row = asdict(self)
        row["maximum_step_s"] = self.maximum_step_s
        row["common_capture_grid_maximum_spacing_m"] = COMMON_CAPTURE_GRID_MAX_SPACING_M
        row["case_ids"] = ["flat", "step"]
        row["study_run_count"] = 2
        row["phase_initialization_included"] = True
        return row


def sensitivity_configurations() -> tuple[StaticShapeSensitivityConfiguration, ...]:
    """Return the preregistered baseline plus six one-factor controls."""
    definitions = (
        ("baseline", "baseline", BASELINE_EPSILON_M, BASELINE_HMAX_M,
         BASELINE_HMIN_M, BASELINE_MAX_STEP_OVER_TC,
         "baseline reference: eps=8 um, hmax/eps=1/2, dtmax/Tc=0.10"),
        ("mesh_ratio_1_3", "mesh_resolution", BASELINE_EPSILON_M,
         BASELINE_EPSILON_M / 3.0, BASELINE_HMIN_M, BASELINE_MAX_STEP_OVER_TC,
         "mesh-only change: eps and dtmax fixed; hmax/eps=1/3"),
        ("mesh_ratio_1_1", "mesh_resolution", BASELINE_EPSILON_M,
         BASELINE_EPSILON_M, BASELINE_HMIN_M, BASELINE_MAX_STEP_OVER_TC,
         "mesh-only change: eps and dtmax fixed; hmax/eps=1"),
        ("epsilon_6um", "interface_width", 6e-6, 3e-6, 1.5e-6,
         BASELINE_MAX_STEP_OVER_TC,
         "physical interface-width change; hmax and hmin follow eps/2 and eps/4 to preserve interface resolution"),
        ("epsilon_10um", "interface_width", 10e-6, 5e-6, 2.5e-6,
         BASELINE_MAX_STEP_OVER_TC,
         "physical interface-width change; hmax and hmin follow eps/2 and eps/4 to preserve interface resolution"),
        ("step_0_05Tc", "maximum_time_step", BASELINE_EPSILON_M,
         BASELINE_HMAX_M, BASELINE_HMIN_M, 0.05,
         "time-step-only change: geometry, epsilon, and mesh fixed"),
        ("step_0_20Tc", "maximum_time_step", BASELINE_EPSILON_M,
         BASELINE_HMAX_M, BASELINE_HMIN_M, 0.20,
         "time-step-only change: geometry, epsilon, and mesh fixed"),
    )
    rows = tuple(StaticShapeSensitivityConfiguration(
        configuration_id=identifier,
        factor=factor,
        epsilon_m=epsilon,
        mesh_hmax_m=hmax,
        mesh_hmin_m=hmin,
        maximum_step_over_capillary_time=max_step,
        capture_spacing_limit_m=epsilon / 4.0,
        capture_grid_protocol=SENSITIVITY_CAPTURE_PROTOCOL,
        interpretation=interpretation,
    ) for identifier, factor, epsilon, hmax, hmin, max_step, interpretation in definitions)
    validate_sensitivity_configurations(rows)
    return rows


def sensitivity_submission_slots() -> tuple[dict[str, Any], ...]:
    """Return the exact frozen configuration-major flat/step order for 14 slots."""
    slots: list[dict[str, Any]] = []
    for configuration in sensitivity_configurations():
        for case_id in SENSITIVITY_CASE_ORDER:
            slots.append({
                "submission_index": len(slots) + 1,
                "configuration_id": configuration.configuration_id,
                "case_id": case_id,
                "phase_initialization_included": True,
                "study_run_calls": 1,
                "capture_grid_protocol": configuration.capture_grid_protocol,
            })
    return tuple(slots)


def sensitivity_capture_resource_estimate() -> dict[str, Any]:
    """Exact W24SHAP1/v2 bytes for the preregistered grid and all 14 histories."""
    time_count = 41
    radial_count = math.ceil(R_BOX_M / COMMON_CAPTURE_GRID_MAX_SPACING_M)
    radial_spacing = R_BOX_M / radial_count
    mesa_columns = sum(
        1 for index in range(radial_count)
        if (index + 0.5) * radial_spacing < R_MESA_M)
    flat_profile_points = radial_count * (math.ceil(H_BOX_M / COMMON_CAPTURE_GRID_MAX_SPACING_M) + 1)
    step_profile_points = (
        mesa_columns * (math.ceil((H_BOX_M - H_MESA_M) / COMMON_CAPTURE_GRID_MAX_SPACING_M) + 1) +
        (radial_count - mesa_columns) * (math.ceil(H_BOX_M / COMMON_CAPTURE_GRID_MAX_SPACING_M) + 1))
    flat_wall_points = math.ceil(R_BOX_M / COMMON_CAPTURE_GRID_MAX_SPACING_M) + 1
    step_wall_points = (
        math.ceil(R_MESA_M / COMMON_CAPTURE_GRID_MAX_SPACING_M) + 1 +
        math.ceil(H_MESA_M / COMMON_CAPTURE_GRID_MAX_SPACING_M) +
        math.ceil((R_BOX_M - R_MESA_M) / COMMON_CAPTURE_GRID_MAX_SPACING_M))

    def binary_size(profile_points: int, wall_points: int) -> int:
        doubles = (3 * time_count + 2 * radial_count + profile_points +
                   time_count * profile_points + 3 * wall_points + time_count * wall_points)
        return 28 + 4 * radial_count + 8 * doubles

    flat_bytes = binary_size(flat_profile_points, flat_wall_points)
    step_bytes = binary_size(step_profile_points, step_wall_points)
    return {
        "schema": "W24SHAP1_V2_CAPTURE_RESOURCE_ESTIMATE_V1",
        "time_count_per_history": time_count,
        "histories": 14,
        "radial_count": radial_count,
        "mesa_floor_columns_in_step_history": mesa_columns,
        "flat": {"profile_points_per_time": flat_profile_points,
                 "wall_points": flat_wall_points, "exact_binary_bytes": flat_bytes},
        "step": {"profile_points_per_time": step_profile_points,
                 "wall_points": step_wall_points, "exact_binary_bytes": step_bytes},
        "maximum_single_history_bytes": max(flat_bytes, step_bytes),
        "total_raw_capture_bytes": 7 * (flat_bytes + step_bytes),
        "per_history_hard_limit_bytes": 1024 * 1024 * 1024,
        "all_requested_times_and_double_precision_retained": True,
    }


def validate_sensitivity_configurations(
    configurations: tuple[StaticShapeSensitivityConfiguration, ...],
) -> None:
    if len(configurations) != 7:
        raise ValueError("the frozen sensitivity contract requires exactly seven configurations including baseline")
    if len({row.configuration_id for row in configurations}) != len(configurations):
        raise ValueError("sensitivity configuration ids must be unique")
    by_id = {row.configuration_id: row for row in configurations}
    expected_ids = {"baseline", "mesh_ratio_1_3", "mesh_ratio_1_1", "epsilon_6um",
                    "epsilon_10um", "step_0_05Tc", "step_0_20Tc"}
    if set(by_id) != expected_ids:
        raise ValueError("sensitivity configuration ids differ from the preregistered seven-case matrix")
    baseline = by_id["baseline"]
    if (baseline.factor != "baseline" or baseline.epsilon_m != BASELINE_EPSILON_M or
            baseline.mesh_hmax_m != BASELINE_HMAX_M or baseline.mesh_hmin_m != BASELINE_HMIN_M or
            baseline.maximum_step_over_capillary_time != BASELINE_MAX_STEP_OVER_TC or
            baseline.capture_grid_protocol != SENSITIVITY_CAPTURE_PROTOCOL):
        raise ValueError("baseline configuration differs from the reviewed native fixture contract")
    for row in configurations:
        numbers = (row.epsilon_m, row.mesh_hmax_m, row.mesh_hmin_m,
                   row.maximum_step_over_capillary_time, row.capture_spacing_limit_m)
        if any(isinstance(value, bool) or not math.isfinite(value) or value <= 0.0
               for value in numbers):
            raise ValueError(f"configuration {row.configuration_id} has nonpositive or nonfinite controls")
        if abs(row.capture_spacing_limit_m - row.epsilon_m / 4.0) > 1e-15:
            raise ValueError(f"configuration {row.configuration_id} capture spacing is not eps/4")
        if row.common_capture_grid_maximum_spacing_m > row.capture_spacing_limit_m * (1.0 + 1e-12):
            raise ValueError(f"configuration {row.configuration_id} common grid exceeds its epsilon/4 capture limit")
        if row.capture_grid_protocol != SENSITIVITY_CAPTURE_PROTOCOL:
            raise ValueError(f"configuration {row.configuration_id} requires the common-grid W24SHAP1/v2 protocol")
    for identifier, ratio in (("mesh_ratio_1_3", 1.0 / 3.0), ("mesh_ratio_1_1", 1.0)):
        row = by_id[identifier]
        if (row.epsilon_m != BASELINE_EPSILON_M or
                abs(row.mesh_hmax_m / row.epsilon_m - ratio) > 1e-12 or
                row.mesh_hmin_m != BASELINE_HMIN_M or
                row.maximum_step_over_capillary_time != BASELINE_MAX_STEP_OVER_TC):
            raise ValueError(f"configuration {identifier} changes more than the frozen mesh-resolution factor")
    for identifier, epsilon in (("epsilon_6um", 6e-6), ("epsilon_10um", 10e-6)):
        row = by_id[identifier]
        if (row.epsilon_m != epsilon or
                abs(row.mesh_hmax_m / row.epsilon_m - 0.5) > 1e-12 or
                abs(row.mesh_hmin_m / row.epsilon_m - 0.25) > 1e-12 or
                row.maximum_step_over_capillary_time != BASELINE_MAX_STEP_OVER_TC):
            raise ValueError(f"configuration {identifier} must preserve the reviewed mesh/epsilon resolution ratios")
    for identifier, ratio in (("step_0_05Tc", 0.05), ("step_0_20Tc", 0.20)):
        row = by_id[identifier]
        if (row.epsilon_m != BASELINE_EPSILON_M or row.mesh_hmax_m != BASELINE_HMAX_M or
                row.mesh_hmin_m != BASELINE_HMIN_M or
                row.maximum_step_over_capillary_time != ratio):
            raise ValueError(f"configuration {identifier} changes more than the frozen time-step factor")


def build_sensitivity_campaign_plan() -> dict[str, Any]:
    """Create a non-authorizing 14-run matrix and its capture prerequisites."""
    configs = sensitivity_configurations()
    rows = [row.as_record() for row in configs]
    slots = sensitivity_submission_slots()
    return {
        "schema": "W24_STATIC_SHAPE_SENSITIVITY_PLAN_V1",
        "status": "PLAN_ONLY_NOT_AUTHORIZED_OR_EXECUTED",
        "native_acceptance": "NOT_RUN",
        "configurations": rows,
        "shape_cases_per_configuration": ["flat", "step"],
        "planned_study_run_submissions": len(rows) * 2,
        "ordered_submission_slots": list(slots),
        "capture_resource_estimate": sensitivity_capture_resource_estimate(),
        "study_run_scope": "one Study.run per case; each run includes PhaseInitialization then transient stdShape; PhaseInitialization is charged as part of that submission",
        "configuration_order": [row.configuration_id for row in configs],
        "capture_contract": {
            "protocol": SENSITIVITY_CAPTURE_PROTOCOL,
            "baseline": "the baseline is recaptured under v2; existing v1 captures remain backward-compatible but cannot enter this sensitivity comparison",
            "common_grid": {
                "maximum_spacing_m": COMMON_CAPTURE_GRID_MAX_SPACING_M,
                "grid_definition": "fixed cell-centered radial grid shared by all seven configurations; per-column vertical and substrate segment grids are exact-endpoint ceil partitions; every actual segment is no larger than the strictest 6 um epsilon/4 limit",
                "preserved_extents": ["0 <= r < Rbox", "each substrate floor <= z <= Hbox", "all step corners are explicit samples"],
            },
            "native_result": "retain all accepted times and raw phase field; no interpolation to compare histories",
        },
        "per_case_gates_before_sensitivity_comparison": [
            "full-history phase-1 volume relative to analytic initial geometry <= 1e-3",
            "last 4 Tc stationary window: interface displacement <= 0.25 epsilon",
            "last 4 Tc contact-line travel <= 0.25 epsilon",
            "last 4 Tc maximum speed <= 1e-3 sigma/muGlue",
        ],
        "comparison_limits_after_each_case_passes": {
            "maximum_interface_height_difference_m": "0.02 * Rdrop",
            "contact_line_distance_m": "0.5 * variant epsilon",
            "relative_final_phase1_volume_difference": 1e-3,
            "bulk_phasefield_energy": "diagnostic only, normalized by sigma*pi*Rdrop^2; not total free energy",
        },
        "not_in_scope_of_this_plan": [
            "not a native budget or permission to start COMSOL",
            "no solve count is borrowed from the separate W24 staged-cure ten-slot campaign",
            "no inference that 14 Study.run submissions fit a previously reviewed 10-run budget",
            "no scientific acceptance before each stable window, all variant gates, and independent review",
        ],
    }


def require_capture_protocol(configuration: StaticShapeSensitivityConfiguration,
                             observed_protocol: str) -> None:
    """Require the common-coordinate v2 capture for every sensitivity row."""
    if observed_protocol != configuration.capture_grid_protocol:
        raise ValueError(
            f"{configuration.configuration_id} requires {configuration.capture_grid_protocol}; "
            f"observed {observed_protocol!r}"
        )
