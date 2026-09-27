"""Pre-registered W24 cure-law v2 analytical gates.

This module evaluates frozen synthetic references and continuity receipts. It
does not start COMSOL, infer material semantics, or turn synthetic tests into
native/scientific acceptance.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from tools.w24_science_acceptance import AcceptanceError, dof_value_map


ADHESIVE_Z_MIN_M = 510.0e-6
ADHESIVE_Z_SURFACE_M = 550.0e-6
MU_BASE_M_INV = 1.0 / 40.0e-6
MU_SENSITIVITY_SCALES = (0.5, 1.0, 2.0)
UV_DURATION_S = 120.0
DOSE_FIELD = "comp1.Duv_rel"
ALPHA_FIELD = "comp1.alpha"
QPOST_FIELD = "comp1.qpost"
MAXWELL_CONTROL_REQUIRED_TIMES_S = (1.0, 301.0, 601.0, 901.0)
GEL_STRESS_COMPONENTS = frozenset({
    "solid.sx", "solid.sy", "solid.sz", "solid.sxy", "solid.sxz", "solid.syz",
})


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AcceptanceError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise AcceptanceError(f"{label} is not finite")
    return result


def _adhesive_z(z_m: Any, z_surface_m: Any) -> tuple[float, float]:
    z = _finite(z_m, "z_m")
    z_surface = _finite(z_surface_m, "z_surface_m")
    if abs(z_surface - ADHESIVE_Z_SURFACE_M) > 1e-12:
        raise AcceptanceError("z_surface_m must match the frozen adhesive geometry top at 550 um")
    if z < ADHESIVE_Z_MIN_M or z > z_surface:
        raise AcceptanceError("Beer-Lambert relative intensity is defined only inside the adhesive")
    return z, z_surface


def relative_uv_intensity(time_s: Any, z_m: Any, *, mu_scale: float = 1.0,
                          z_surface_m: float = ADHESIVE_Z_SURFACE_M) -> float:
    """Dimensionless estimated intensity for the adhesive-only UV envelope."""
    time = _finite(time_s, "time_s")
    z, surface = _adhesive_z(z_m, z_surface_m)
    scale = _finite(mu_scale, "mu_scale")
    if time < 0:
        raise AcceptanceError("time_s cannot be negative")
    if scale not in MU_SENSITIVITY_SCALES:
        raise AcceptanceError("mu_scale must be one of the frozen 0.5x, 1x, or 2x variants")
    envelope = 1.0 if time < UV_DURATION_S else 0.0
    mu = MU_BASE_M_INV * scale
    return envelope * math.exp(-mu * (surface - z))


def relative_uv_dose(time_s: Any, z_m: Any, *, mu_scale: float = 1.0,
                     z_surface_m: float = ADHESIVE_Z_SURFACE_M) -> float:
    """Analytic Duv_rel in seconds for S(t)=1 on [0,120) and zero afterward."""
    time = _finite(time_s, "time_s")
    z, surface = _adhesive_z(z_m, z_surface_m)
    scale = _finite(mu_scale, "mu_scale")
    if time < 0:
        raise AcceptanceError("time_s cannot be negative")
    if scale not in MU_SENSITIVITY_SCALES:
        raise AcceptanceError("mu_scale must be one of the frozen 0.5x, 1x, or 2x variants")
    mu = MU_BASE_M_INV * scale
    return min(time, UV_DURATION_S) * math.exp(-mu * (surface - z))


def relative_uv_sensitivity(time_s: Any, z_m: Any, *,
                            z_surface_m: float = ADHESIVE_Z_SURFACE_M) -> dict[str, Any]:
    """Return the preregistered mu/2, mu, and 2mu relative-dose values."""
    time = _finite(time_s, "time_s")
    z, surface = _adhesive_z(z_m, z_surface_m)
    values = {scale: relative_uv_dose(time, z, mu_scale=scale, z_surface_m=surface)
              for scale in MU_SENSITIVITY_SCALES}
    if not values[0.5] + 1e-15 >= values[1.0] >= values[2.0] - 1e-15:
        raise AcceptanceError("relative exposure dose must decrease monotonically with mu")
    return {
        "synthetic_estimated": True,
        "absolute_irradiance": False,
        "unit": "s",
        "z_surface_m": surface,
        "z_m": z,
        "mu_base_m_inv": MU_BASE_M_INV,
        "dose_by_mu_scale": {str(scale): values[scale] for scale in MU_SENSITIVITY_SCALES},
    }


def isothermal_alpha_from_relative_dose(times_s: Sequence[float], *, alpha0: float,
                                        t0_k: float, pre_exponential_s: float,
                                        activation_j_mol: float,
                                        gas_constant_j_mol_k: float,
                                        uv_rate_s: float, z_m: float,
                                        mu_scale: float = 1.0) -> list[float]:
    """Independent constant-T conversion reference for a spatial adhesive point."""
    alpha_init = _finite(alpha0, "alpha0")
    temperature = _finite(t0_k, "t0_k")
    pre_exponential = _finite(pre_exponential_s, "pre_exponential_s")
    activation = _finite(activation_j_mol, "activation_j_mol")
    gas_constant = _finite(gas_constant_j_mol_k, "gas_constant_j_mol_k")
    uv_rate = _finite(uv_rate_s, "uv_rate_s")
    if not 0.0 <= alpha_init <= 1.0:
        raise AcceptanceError("alpha0 must be in [0,1]")
    if min(temperature, pre_exponential, activation, gas_constant) <= 0:
        raise AcceptanceError("isothermal Arrhenius inputs must be positive")
    if uv_rate < 0:
        raise AcceptanceError("uv_rate_s cannot be negative")
    kt = pre_exponential * math.exp(-activation / (gas_constant * temperature))
    values = []
    for index, raw_time in enumerate(times_s):
        time = _finite(raw_time, f"times_s[{index}]")
        if time < 0:
            raise AcceptanceError("time cannot be negative")
        dose = relative_uv_dose(time, z_m, mu_scale=mu_scale)
        values.append(1.0 - (1.0 - alpha_init) * math.exp(-kt * time - uv_rate * dose))
    return values


def elastic_bulk_shear(youngs_modulus_pa: Any, poisson_ratio: Any) -> tuple[float, float]:
    """Compute isotropic bulk and shear moduli from E and nu."""
    young = _finite(youngs_modulus_pa, "youngs_modulus_pa")
    poisson = _finite(poisson_ratio, "poisson_ratio")
    if young <= 0:
        raise AcceptanceError("Young's modulus must be positive")
    if not -1.0 < poisson < 0.5:
        raise AcceptanceError("Poisson ratio must be between -1 and 0.5")
    return young / (3.0 * (1.0 - 2.0 * poisson)), young / (2.0 * (1.0 + poisson))


def maxwell_control_reference(times_s: Sequence[float], *,
                              long_term_youngs_modulus_pa: float = 0.5e9,
                              branch_youngs_modulus_pa: float = 1.5e9,
                              poisson_ratio: float = 0.35,
                              relaxation_time_s: float = 300.0,
                              final_strain: float = 1.0e-3,
                              ramp_duration_s: float = 1.0) -> dict[str, Any]:
    """Closed-form isotropic Maxwell response to a finite linear strain ramp.

    The stress amplitude is computed from the frozen E, nu, and imposed strain;
    no measured initial stress is used to fit the decay.
    """
    k_inf, g_inf = elastic_bulk_shear(long_term_youngs_modulus_pa, poisson_ratio)
    k_branch, g_branch = elastic_bulk_shear(branch_youngs_modulus_pa, poisson_ratio)
    tau = _finite(relaxation_time_s, "relaxation_time_s")
    strain_final = _finite(final_strain, "final_strain")
    ramp = _finite(ramp_duration_s, "ramp_duration_s")
    if tau <= 0 or ramp <= 0 or strain_final <= 0:
        raise AcceptanceError("Maxwell tau, ramp duration, and final strain must be positive")
    xx_inf = k_inf + 4.0 * g_inf / 3.0
    yy_inf = k_inf - 2.0 * g_inf / 3.0
    xx_branch = k_branch + 4.0 * g_branch / 3.0
    yy_branch = k_branch - 2.0 * g_branch / 3.0
    result: dict[str, Any] = {
        "contract": "W24_CURE_LAW_V2_MAXWELL_RAMP_HOLD_V1",
        "input_is_native": False,
        "long_term_moduli_pa": {"K": k_inf, "G": g_inf},
        "branch_moduli_pa": {"K": k_branch, "G": g_branch},
        "coefficients_pa": {
            "xx_long_term": xx_inf, "yy_long_term": yy_inf,
            "xx_branch": xx_branch, "yy_branch": yy_branch,
        },
        "times_s": [], "strain": [],
        "sigma_xx_long_term_pa": [], "sigma_xx_branch_pa": [], "sigma_xx_pa": [],
        "sigma_yy_long_term_pa": [], "sigma_yy_branch_pa": [], "sigma_yy_pa": [],
    }
    for index, raw_time in enumerate(times_s):
        time = _finite(raw_time, f"times_s[{index}]")
        if time < 0:
            raise AcceptanceError("Maxwell control time cannot be negative")
        if time <= ramp:
            strain = strain_final * time / ramp
            branch_factor = tau / ramp * (1.0 - math.exp(-time / tau))
        else:
            strain = strain_final
            ramp_end_factor = tau / ramp * (1.0 - math.exp(-ramp / tau))
            branch_factor = ramp_end_factor * math.exp(-(time - ramp) / tau)
        xx_lt = xx_inf * strain
        yy_lt = yy_inf * strain
        xx_mem = xx_branch * strain_final * branch_factor
        yy_mem = yy_branch * strain_final * branch_factor
        result["times_s"].append(time)
        result["strain"].append(strain)
        result["sigma_xx_long_term_pa"].append(xx_lt)
        result["sigma_xx_branch_pa"].append(xx_mem)
        result["sigma_xx_pa"].append(xx_lt + xx_mem)
        result["sigma_yy_long_term_pa"].append(yy_lt)
        result["sigma_yy_branch_pa"].append(yy_mem)
        result["sigma_yy_pa"].append(yy_lt + yy_mem)
    if not result["times_s"]:
        raise AcceptanceError("Maxwell control times must be nonempty")
    return result


def validate_maxwell_control_capture(capture: Mapping[str, Any], *,
                                     absolute_tolerance_pa: float,
                                     relative_tolerance: float) -> dict[str, Any]:
    """Compare native σxx and σyy amplitudes directly with the analytic response."""
    if not isinstance(capture, Mapping):
        raise AcceptanceError("Maxwell control capture must be a mapping")
    if capture.get("case_id") != "maxwell_ramp_hold_control" or \
            capture.get("native_metrics_readback") is not True or \
            capture.get("stress_unit") != "Pa":
        raise AcceptanceError("Maxwell control acceptance requires a native control-model metrics readback")
    times = capture.get("stored_times_s")
    xx = capture.get("sigma_xx_pa")
    yy = capture.get("sigma_yy_pa")
    if not isinstance(times, list) or not isinstance(xx, list) or not isinstance(yy, list):
        raise AcceptanceError("Maxwell control capture omitted native time or σxx/σyy arrays")
    if len(times) != len(xx) or len(times) != len(yy) or not times:
        raise AcceptanceError("Maxwell control time and stress arrays must have equal nonzero lengths")
    normalized_times = [_finite(value, f"stored_times_s[{i}]") for i, value in enumerate(times)]
    if any(b <= a for a, b in zip(normalized_times, normalized_times[1:])):
        raise AcceptanceError("Maxwell control times must be strictly increasing")
    for target in MAXWELL_CONTROL_REQUIRED_TIMES_S:
        value = _finite(target, "required_control_time_s")
        if sum(abs(time - value) <= 1e-12 for time in normalized_times) != 1:
            raise AcceptanceError(f"Maxwell control lacks exact stored checkpoint {value:g} s")
    abs_tol = _finite(absolute_tolerance_pa, "absolute_tolerance_pa")
    rel_tol = _finite(relative_tolerance, "relative_tolerance")
    if abs_tol < 0 or rel_tol < 0:
        raise AcceptanceError("Maxwell control tolerances cannot be negative")
    expected = maxwell_control_reference(normalized_times)
    errors: dict[str, list[float]] = {}
    for name, actual, reference in (
        ("sigma_xx_pa", xx, expected["sigma_xx_pa"]),
        ("sigma_yy_pa", yy, expected["sigma_yy_pa"]),
    ):
        if len(actual) != len(normalized_times):
            raise AcceptanceError(f"{name} length differs from the stored time vector")
        row = []
        for index, (raw, target) in enumerate(zip(actual, reference)):
            value = _finite(raw, f"{name}[{index}]")
            error = abs(value - target)
            limit = max(abs_tol, rel_tol * abs(target))
            if error > limit:
                raise AcceptanceError(
                    f"{name} absolute-amplitude error {error:g} Pa exceeds {limit:g} Pa at t={normalized_times[index]:g} s")
            row.append(error)
        errors[name] = row
    return {
        "status": "ANALYTIC_VALUES_MATCH_SOURCE_AUTH_REQUIRED",
        "analysis_scope": "caller_supplied_metrics_only",
        "input_native_metrics_readback_assertion": True,
        "source_identity_authenticated": False,
        "native_study_semantics_verified": False,
        "sigma_xx_max_abs_error_pa": max(errors["sigma_xx_pa"]),
        "sigma_yy_max_abs_error_pa": max(errors["sigma_yy_pa"]),
        "uses_measured_sigma0_fit": False,
        "maxwell_tau_s": 300.0,
        "long_term_baseline_checked": True,
        "branch_amplitude_checked": True,
        "branch_decay_checked": True,
    }


def _wasactive_map(frame: Mapping[str, Any]) -> dict[tuple[str, ...], int]:
    observation = frame.get("wasactive")
    if not isinstance(observation, Mapping) or observation.get("native_evaluated") is not True or \
            observation.get("expression") != "solid.wasactive":
        raise AcceptanceError("handoff frame lacks a native solid.wasactive expression readback")
    coordinates = observation.get("coordinates_m")
    values = observation.get("values")
    if not isinstance(coordinates, list) or not isinstance(values, list) or len(coordinates) != len(values) or not coordinates:
        raise AcceptanceError("native wasactive coordinates and values must be nonempty matching arrays")
    out: dict[tuple[str, ...], int] = {}
    for i, (coordinate, raw_value) in enumerate(zip(coordinates, values)):
        if not isinstance(coordinate, list) or not coordinate:
            raise AcceptanceError(f"wasactive coordinates_m[{i}] must be a nonempty vector")
        key = tuple(_finite(axis, f"wasactive.coordinates_m[{i}]").hex() for axis in coordinate)
        value = _finite(raw_value, f"wasactive.values[{i}]")
        if value not in (0.0, 1.0):
            raise AcceptanceError("solid.wasactive native values must be exactly zero or one")
        if key in out:
            raise AcceptanceError("native wasactive observation contains duplicate coordinates")
        out[key] = int(value)
    return out


def compare_v2_history_handoff(source: Mapping[str, Any], target: Mapping[str, Any], *,
                               dof_abs_tolerances: Mapping[str, float],
                               history_abs_tolerances: Mapping[str, float],
                               branch_dof_names: Sequence[str] | None = None) -> dict[str, Any]:
    """Compare all V2 Xmesh DOFs plus explicit thermal/cure/activation histories.

    ``branch_dof_names`` is retained as a compatibility hint only. Caller
    labels cannot authenticate COMSOL's private Maxwell memory variables, so
    the result always keeps that part UNVERIFIED until a public native route
    captures the actual branch state.
    """
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        raise AcceptanceError("handoff frames must be mappings")
    source_time = _finite(source.get("time_s"), "source.time_s")
    target_time = _finite(target.get("time_s"), "target.time_s")
    if abs(source_time - target_time) > 1e-12:
        raise AcceptanceError("handoff frames must refer to the same exact boundary time")
    axes_by_side: dict[str, int] = {}
    for side, frame in (("source", source), ("target", target)):
        metadata = _require_frame_v2_snapshot(frame, side)
        axes = metadata.get("coordinate_axes")
        axes_by_side[side] = axes
        _history_capture_at_boundary(frame, side, source_time)
    if axes_by_side["source"] != axes_by_side["target"]:
        raise AcceptanceError("handoff Xmesh coordinate axis counts differ")
    left = dof_value_map(source)
    right = dof_value_map(target)
    required_full_fields = {"comp1_T", "comp1_alpha", "comp1_Duv_rel", "comp1_qpost"}
    for side, mapping, axes in (("source", left, axes_by_side["source"]),
                                ("target", right, axes_by_side["target"])):
        displacement_fields = {"comp1_u", "comp1_w"} if axes == 2 else {"comp1_u", "comp1_v", "comp1_w"}
        present_names = {row[0] for row in mapping.values()}
        missing_fields = sorted((required_full_fields | displacement_fields) - present_names)
        if missing_fields:
            raise AcceptanceError(
                f"{side} full Xmesh snapshot lacks required cure/displacement DOF members: {missing_fields}"
            )
    if set(left) != set(right):
        raise AcceptanceError("complete handoff DOF key sets differ")
    observed_names = {row[0] for row in left.values()}
    branch_hints: set[str] = set()
    if branch_dof_names is not None:
        if not isinstance(branch_dof_names, Sequence) or isinstance(branch_dof_names, (str, bytes)) or \
                not all(isinstance(name, str) and name for name in branch_dof_names):
            raise AcceptanceError("branch DOF hints must be a sequence of exact observed names")
        branch_hints = set(branch_dof_names)
        if not branch_hints.issubset(observed_names):
            raise AcceptanceError("caller Maxwell branch-name hint is not present in the complete Xmesh snapshot")
    if not isinstance(dof_abs_tolerances, Mapping) or not dof_abs_tolerances:
        raise AcceptanceError("per-field full-DOF handoff tolerances are required")
    if observed_names != set(dof_abs_tolerances):
        raise AcceptanceError("full-DOF tolerance names must exactly cover observed DOF names")
    max_by_name: dict[str, float] = {name: 0.0 for name in observed_names}
    for key in left:
        name, a_real, a_imag = left[key]
        _, b_real, b_imag = right[key]
        tolerance = _finite(dof_abs_tolerances[name], f"dof_abs_tolerances.{name}")
        if tolerance < 0:
            raise AcceptanceError("full-DOF tolerances cannot be negative")
        error = max(abs(a_real - b_real), abs(a_imag - b_imag))
        if error > tolerance:
            raise AcceptanceError(f"full handoff DOF {name} changed by {error:g}, above {tolerance:g}")
        max_by_name[name] = max(max_by_name[name], error)

    required_history = {"T", "alpha", "Duv_rel", "qpost", "u", "w", "solid.isactive", "solid.wasactive"}
    if not isinstance(history_abs_tolerances, Mapping) or set(history_abs_tolerances) != required_history:
        raise AcceptanceError("history tolerance map must exactly cover T/alpha/Duv_rel/qpost/displacement/activation")
    history_left = _history_capture_at_boundary(source, "source", source_time)
    history_right = _history_capture_at_boundary(target, "target", source_time)
    if set(history_left) != required_history or set(history_right) != required_history:
        raise AcceptanceError("history capture is missing a required thermal/cure/displacement/activation expression")
    history_max: dict[str, float] = {}
    for field in sorted(required_history):
        if len(history_left[field]) != len(history_right[field]):
            raise AcceptanceError(f"history probe coordinates differ for {field}")
        tolerance = _finite(history_abs_tolerances[field], f"history_abs_tolerances.{field}")
        if tolerance < 0:
            raise AcceptanceError("history field tolerances cannot be negative")
        jumps = [abs(a - b) for a, b in zip(history_left[field], history_right[field])]
        maximum_jump = max(jumps, default=0.0)
        if maximum_jump > tolerance:
            label = "solid.wasactive activation history reset" if field == "solid.wasactive" else f"native history {field} changed"
            raise AcceptanceError(f"{label}: {maximum_jump:g} exceeds {tolerance:g}")
        history_max[field] = maximum_jump
    for activation_field in ("solid.isactive", "solid.wasactive"):
        if any(value != 1.0 for value in history_left[activation_field]):
            raise AcceptanceError(f"history handoff source is not fully post-gel active in {activation_field}")
        if any(value != 1.0 for value in history_right[activation_field]):
            raise AcceptanceError(f"history handoff target is not fully post-gel active in {activation_field}")
    return {
        "status": "VISIBLE_HISTORY_MATCH_BRANCH_STATE_UNVERIFIED",
        "analysis_scope": "complete_v2_xmesh_and_native_expression_contract;source_auth_still_required",
        "source_identity_authenticated": False,
        "boundary_time_s": source_time,
        "full_dof_count": len(left),
        "branch_state_dof_count": 0,
        "caller_branch_name_hints_ignored_for_acceptance": sorted(branch_hints),
        "branch_state_max_abs_jump": None,
        "max_full_dof_jump": max(max_by_name.values(), default=0.0),
        "history_max_abs_jumps": history_max,
        "activation_coordinates_identical": True,
        "isactive_identical": history_max["solid.isactive"] == 0.0,
        "wasactive_identical": history_max["solid.wasactive"] == 0.0,
        "max_abs_jump_by_dof_name": max_by_name,
        "maxwell_branch_state": "UNVERIFIED_NO_PUBLIC_REFERENCE_STATE_CAPTURE",
        "native_semantics_verified": False,
    }


def _require_frame_v2_snapshot(frame: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    metadata = frame.get("dofs")
    if not isinstance(metadata, Mapping) or metadata.get("snapshot_schema") != "W24-DOF-SNAPSHOT-2" or \
            metadata.get("complete_xmesh_dofs") is not True or metadata.get("coordinate_axes") not in (2, 3):
        raise AcceptanceError(f"{label} requires a complete V2 Xmesh DOF snapshot")
    coordinates = metadata.get("coords")
    if not isinstance(coordinates, list) or len(coordinates) != metadata["coordinate_axes"]:
        raise AcceptanceError(f"{label} V2 coordinate_axes does not match the Xmesh coordinate row count")
    return metadata


def _history_capture_at_boundary(frame: Mapping[str, Any], label: str,
                                 time_s: float) -> dict[str, list[float]]:
    capture = frame.get("history_capture")
    if not isinstance(capture, Mapping) or capture.get("schema") != "W24_CURE_LAW_V2_HISTORY_CAPTURE_V1":
        raise AcceptanceError(f"{label} frame lacks the exact V2 public history capture")
    expressions = capture.get("expressions")
    units = capture.get("units")
    expected_expressions = ["T", "alpha", "Duv_rel", "qpost", "u", "w", "solid.isactive", "solid.wasactive"]
    expected_units = ["K", "1", "s", "1", "m", "m", "1", "1"]
    if expressions != expected_expressions or units != expected_units:
        raise AcceptanceError(f"{label} V2 history capture has missing or reordered expressions/units")
    times = capture.get("stored_times_s")
    data = capture.get("data")
    coordinates = capture.get("coordinates_m")
    if not isinstance(times, list) or not times or not isinstance(data, list) or len(data) != len(expressions) or \
            not isinstance(coordinates, list) or not coordinates:
        raise AcceptanceError(f"{label} V2 history capture omitted complete times, values, or coordinates")
    if coordinates != [[25e-6, 520e-6], [50e-6, 530e-6], [75e-6, 540e-6]]:
        raise AcceptanceError(f"{label} V2 history coordinates differ from the frozen adhesive probes")
    matches = [i for i, raw in enumerate(times) if _finite(raw, f"{label}.stored_times_s") == time_s]
    if len(matches) != 1:
        raise AcceptanceError(f"{label} V2 history capture lacks the exact handoff boundary time")
    time_index = matches[0]
    point_count = len(coordinates)
    result: dict[str, list[float]] = {}
    for exp_index, expression in enumerate(expressions):
        series = data[exp_index]
        if not isinstance(series, list) or len(series) != len(times) or \
                not isinstance(series[time_index], list) or len(series[time_index]) != point_count:
            raise AcceptanceError(f"{label} V2 history values have an incomplete shape for {expression}")
        result[expression] = [_finite(value, f"{label}.{expression}") for value in series[time_index]]
    if not time_s > 2.0:
        raise AcceptanceError(f"{label} history frame is not a post-gel state")
    return result


def validate_gel_stress_free_control(capture: Mapping[str, Any], *,
                                     maximum_abs_stress_pa: float) -> dict[str, Any]:
    """Keep gel stress-free reference validation independent from Maxwell decay."""
    if not isinstance(capture, Mapping) or capture.get("case_id") != "gel_stress_free_control" or \
            capture.get("native_metrics_readback") is not True or \
            capture.get("stress_unit") != "Pa":
        raise AcceptanceError("gel stress-free gate requires its own native control-model capture")
    raw_times = capture.get("stored_times_s")
    if not isinstance(raw_times, list) or not raw_times:
        raise AcceptanceError("gel stress-free control omitted its native stored-time vector")
    times = [_finite(value, f"stored_times_s[{i}]") for i, value in enumerate(raw_times)]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise AcceptanceError("gel stress-free stored times must be strictly increasing")
    post_gel_indices = [i for i, time in enumerate(times) if time >= 2.0]
    for target in (2.0, 3.0):
        if sum(abs(time - target) <= 1e-12 for time in times) != 1:
            raise AcceptanceError(f"gel stress-free control lacks exact post-gel checkpoint {target:g} s")
    activation_samples = capture.get("wasactive_samples")
    if not isinstance(activation_samples, list) or not activation_samples:
        raise AcceptanceError("gel stress-free control omitted native solid.wasactive samples")
    activation_by_time: dict[float, dict[tuple[str, ...], int]] = {}
    for sample_index, sample in enumerate(activation_samples):
        if not isinstance(sample, Mapping) or sample.get("native_evaluated") is not True or \
                sample.get("expression") != "solid.wasactive":
            raise AcceptanceError("gel stress-free activation samples must be native solid.wasactive evaluations")
        sample_time = _finite(sample.get("time_s"), f"wasactive_samples[{sample_index}].time_s")
        if sample_time in activation_by_time:
            raise AcceptanceError("gel stress-free activation samples contain a duplicate time")
        activation_by_time[sample_time] = _wasactive_map({"wasactive": sample})
    activation_match_times: dict[float, float] = {}
    for target in (2.5, 3.0):
        matches = [time for time in activation_by_time if abs(time - target) <= 1e-12]
        if len(matches) != 1:
            raise AcceptanceError(f"gel stress-free control lacks native wasactive checkpoint {target:g} s")
        activation_match_times[target] = matches[0]
    active_coordinates = None
    for target in (2.5, 3.0):
        row = activation_by_time[activation_match_times[target]]
        if not row or any(value != 1 for value in row.values()):
            raise AcceptanceError(f"gel stress-free control is not fully activated at {target:g} s")
        if active_coordinates is None:
            active_coordinates = set(row)
        elif set(row) != active_coordinates:
            raise AcceptanceError("gel stress-free wasactive coordinates changed across post-gel checkpoints")
    stresses = capture.get("stress_components_pa")
    if not isinstance(stresses, Mapping) or set(stresses) != GEL_STRESS_COMPONENTS:
        missing = sorted(GEL_STRESS_COMPONENTS - set(stresses)) if isinstance(stresses, Mapping) else sorted(GEL_STRESS_COMPONENTS)
        unknown = sorted(set(stresses) - GEL_STRESS_COMPONENTS) if isinstance(stresses, Mapping) else []
        raise AcceptanceError(
            f"gel stress-free control requires the exact six native stress components; missing={missing}, unknown={unknown}")
    limit = _finite(maximum_abs_stress_pa, "maximum_abs_stress_pa")
    if limit < 0:
        raise AcceptanceError("stress-free maximum stress threshold cannot be negative")
    maximum = 0.0
    for component, series in stresses.items():
        if not isinstance(series, list) or len(series) != len(times):
            raise AcceptanceError("each required gel stress-free component must have a full stored-time series")
        for index, raw_stress in enumerate(series):
            stress = _finite(raw_stress, f"stress_components_pa.{component}[{index}]")
            if index in post_gel_indices:
                maximum = max(maximum, abs(stress))
    if maximum > limit:
        raise AcceptanceError(f"gel stress-free maximum stress {maximum:g} Pa exceeds {limit:g} Pa")
    return {"status": "GEL_STRESS_FREE_VALUES_MATCH_SOURCE_AUTH_REQUIRED",
            "analysis_scope": "caller_supplied_metrics_only",
            "source_identity_authenticated": False,
            "input_native_metrics_readback_assertion": True,
            "max_abs_stress_pa": maximum,
            "threshold_pa": limit, "independent_from_maxwell_decay": True,
            "post_gel_sample_times_s": [times[i] for i in post_gel_indices],
            "wasactive_post_gel_native": True,
            "wasactive_samples_are_caller_assertions": True,
            "wasactive_sample_count": sum(
                len(activation_by_time[activation_match_times[t]]) for t in (2.5, 3.0)),
            "native_activation_semantics_verified": False}
