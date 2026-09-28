"""Pure-Python numerical gates for the frozen W24 cure-history campaign.

This module consumes explicit native receipts. It never infers COMSOL field
names, coordinate identities, time values, or the sign of a heat flux.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


STRESS_ROLES = ("radial_normal", "hoop_normal", "axial_normal", "rz_shear")


class AcceptanceError(ValueError):
    """A native receipt is incomplete, ambiguous, or outside a frozen gate."""


def _finite(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AcceptanceError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise AcceptanceError(f"{label} is not finite")
    return result


def analytic_alpha_iso(times_s: Sequence[float], *, alpha0: float, t0_k: float,
                       pre_exponential_s: float, activation_j_mol: float,
                       gas_constant_j_mol_k: float, uv_rate_s: float,
                       uv_duration_s: float) -> list[float]:
    """Independent first-order reference with UV limited to the frozen window."""
    alpha0 = _finite(alpha0, "alpha0")
    t0_k = _finite(t0_k, "t0_k")
    pre_exponential_s = _finite(pre_exponential_s, "pre_exponential_s")
    activation_j_mol = _finite(activation_j_mol, "activation_j_mol")
    gas_constant_j_mol_k = _finite(gas_constant_j_mol_k, "gas_constant_j_mol_k")
    uv_rate_s = _finite(uv_rate_s, "uv_rate_s")
    uv_duration_s = _finite(uv_duration_s, "uv_duration_s")
    if not 0 <= alpha0 <= 1:
        raise AcceptanceError("alpha0 must be in [0, 1]")
    if min(pre_exponential_s, activation_j_mol, gas_constant_j_mol_k, t0_k) <= 0:
        raise AcceptanceError("Arrhenius inputs must be positive")
    if uv_rate_s < 0 or uv_duration_s < 0:
        raise AcceptanceError("UV rate and duration cannot be negative")
    kt0 = pre_exponential_s * math.exp(-activation_j_mol /
                                        (gas_constant_j_mol_k * t0_k))
    values: list[float] = []
    for i, raw_time in enumerate(times_s):
        time_s = _finite(raw_time, f"times_s[{i}]")
        if time_s < 0:
            raise AcceptanceError("time cannot be negative")
        exposure = uv_rate_s * min(time_s, uv_duration_s)
        values.append(1.0 - (1.0 - alpha0) * math.exp(-kt0 * time_s - exposure))
    return values


def validate_stored_times(stored_times: Sequence[float], requested_times: Sequence[float],
                          *, max_step_s: float, exact_tolerance_s: float = 1e-12) -> None:
    """Require unique exact requested outputs and a bounded accepted-step gap."""
    stored = [_finite(t, f"stored_times[{i}]") for i, t in enumerate(stored_times)]
    requested = [_finite(t, f"requested_times[{i}]") for i, t in enumerate(requested_times)]
    if not stored or not requested:
        raise AcceptanceError("stored and requested time vectors must be nonempty")
    if any(b <= a for a, b in zip(stored, stored[1:])):
        raise AcceptanceError("stored solution times must be strictly increasing")
    if any(b <= a for a, b in zip(requested, requested[1:])):
        raise AcceptanceError("requested output times must be strictly increasing")
    if not math.isfinite(max_step_s) or max_step_s <= 0:
        raise AcceptanceError("maximum accepted-step gap must be positive and finite")
    if not math.isfinite(exact_tolerance_s) or exact_tolerance_s < 0:
        raise AcceptanceError("exact-time tolerance must be finite and nonnegative")
    for target in requested:
        matches = [time for time in stored if abs(time - target) <= exact_tolerance_s]
        if len(matches) != 1:
            raise AcceptanceError(f"requested time {target:.17g} s has {len(matches)} stored matches")
    max_gap = max((b - a for a, b in zip(stored, stored[1:])), default=0.0)
    if max_gap > max_step_s + exact_tolerance_s:
        raise AcceptanceError(f"accepted-step gap {max_gap:.17g} s exceeds {max_step_s:.17g} s")


def dof_value_map(frame: Mapping[str, Any]) -> dict[tuple[Any, ...], tuple[str, float, float]]:
    """Create exact XmeshInfoDofs keys from documented zero-based indices.

    ``coords`` is the documented coordinate-by-DOF matrix. ``solVectorInds``
    are documented zero-based indices into ``u_real`` and ``u_imag``.
    """
    meta = frame.get("dofs")
    if not isinstance(meta, Mapping):
        raise AcceptanceError("frame is missing XmeshInfoDofs metadata")
    names = meta.get("dofNames")
    schema = meta.get("snapshot_schema")
    if schema == "W24-DOF-SNAPSHOT-3" and meta.get("complete_xmesh_internal_dof_capture") is not True:
        raise AcceptanceError("V3 DOF-value mapping is incomplete or has an unmapped native vector entry")
    if not isinstance(names, list) or not names or not all(isinstance(x, str) for x in names):
        raise AcceptanceError("dofNames must be a nonempty string vector")
    geom_nums = meta.get("geomNums")
    nodes = meta.get("nodes")
    coords = meta.get("coords")
    name_inds = meta.get("nameInds")
    vector_inds = meta.get("solVectorInds")
    real = frame.get("u_real")
    imag = frame.get("u_imag")
    vectors = (geom_nums, nodes, coords, name_inds, vector_inds, real, imag)
    if any(not isinstance(x, list) for x in vectors):
        raise AcceptanceError("DOF metadata and solution vectors must be arrays")
    axes = meta.get("coordinate_axes")
    if axes is not None and (isinstance(axes, bool) or axes != len(coords)):
        raise AcceptanceError("coordinate_axes does not equal the number of actual coordinate rows")
    if schema == "W24-DOF-SNAPSHOT-3" and (not isinstance(coords, list) or len(coords) not in (2, 3)):
        raise AcceptanceError("V3 Xmesh coordinates must contain the exact 2D or 3D coordinate rows")
    count = len(geom_nums)
    if count == 0 or len(nodes) != count or len(name_inds) != count or len(vector_inds) != count:
        raise AcceptanceError("XmeshInfoDofs index arrays have inconsistent lengths")
    if not coords or any(not isinstance(axis, list) or len(axis) != count for axis in coords):
        raise AcceptanceError("coords must have one equal-length row per coordinate axis")
    out: dict[tuple[Any, ...], tuple[str, float, float]] = {}
    for i in range(count):
        name_index = name_inds[i]
        vector_index = vector_inds[i]
        if isinstance(name_index, bool) or not isinstance(name_index, int) or not 0 <= name_index < len(names):
            raise AcceptanceError(f"nameInds[{i}] is not a valid documented zero-based index")
        if (isinstance(vector_index, bool) or not isinstance(vector_index, int) or
                not 0 <= vector_index < len(real) or vector_index >= len(imag)):
            raise AcceptanceError(f"solVectorInds[{i}] is not a valid documented zero-based solution index")
        coordinate_key = tuple(_finite(axis[i], f"coords[{j}][{i}]").hex()
                               for j, axis in enumerate(coords))
        key = (int(geom_nums[i]), int(nodes[i]), coordinate_key, names[name_index], name_index)
        if schema == "W24-DOF-SNAPSHOT-3":
            key = (*key, i)
        if key in out:
            raise AcceptanceError(f"duplicate exact DOF key at row {i}")
        out[key] = (names[name_index], _finite(real[vector_index], f"u_real[{vector_index}]"),
                    _finite(imag[vector_index], f"u_imag[{vector_index}]"))
    return out


def _field_value_map(mapping: Mapping[tuple[Any, ...], tuple[str, float, float]],
                     accepted_names: Sequence[str], label: str, *, component: str = "real",
                     offset: float = 0.0) -> dict[tuple[Any, ...], float]:
    if not accepted_names or not all(isinstance(name, str) and name for name in accepted_names):
        raise AcceptanceError(f"native identity mapping for {label} is missing")
    index = 1 if component == "real" else 2
    accepted = set(accepted_names)
    selected = {key: row[index] - offset for key, row in mapping.items()
                if row[0] in accepted}
    if not selected:
        raise AcceptanceError(f"native DOF identities contain no members for {label}")
    return selected


def _matched_field_vectors(candidate: Mapping[tuple[Any, ...], tuple[str, float, float]],
                           reference: Mapping[tuple[Any, ...], tuple[str, float, float]],
                           accepted_names: Sequence[str], label: str, *, component: str = "real",
                           candidate_offset: float = 0.0,
                           reference_offset: float = 0.0) -> tuple[list[float], list[float]]:
    a = _field_value_map(candidate, accepted_names, label, component=component,
                         offset=candidate_offset)
    b = _field_value_map(reference, accepted_names, label, component=component,
                         offset=reference_offset)
    if set(a) != set(b):
        raise AcceptanceError(f"exact DOF key sets differ for {label}: "
                              f"missing={len(set(b)-set(a))}, extra={len(set(a)-set(b))}")
    keys = sorted(a)
    return [a[key] for key in keys], [b[key] for key in keys]


def normalized_l2_error(candidate: Sequence[float], reference: Sequence[float],
                        *, floor: float) -> float:
    if len(candidate) != len(reference) or not candidate:
        raise AcceptanceError("L2 vectors must be nonempty and have identical lengths")
    a = [_finite(v, f"candidate[{i}]") for i, v in enumerate(candidate)]
    b = [_finite(v, f"reference[{i}]") for i, v in enumerate(reference)]
    floor_value = _finite(floor, "floor")
    if floor_value <= 0:
        raise AcceptanceError("L2 floor must be positive")
    numerator = math.sqrt(math.fsum((x - y) ** 2 for x, y in zip(a, b)))
    denominator = max(math.sqrt(math.fsum(y * y for y in b)), floor_value * math.sqrt(len(b)))
    return numerator / denominator


def validate_monotone_series(times_s: Sequence[float], values: Sequence[float], *,
                             minimum: float, maximum: float, label: str,
                             monotone_tolerance: float = 1e-12) -> list[float]:
    if len(times_s) != len(values) or not times_s:
        raise AcceptanceError(f"{label} needs matching nonempty time/value arrays")
    times = [_finite(value, f"{label}.time[{i}]") for i, value in enumerate(times_s)]
    result = [_finite(value, f"{label}.value[{i}]") for i, value in enumerate(values)]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise AcceptanceError(f"{label} times must be strictly increasing")
    low = _finite(minimum, f"{label}.minimum")
    high = _finite(maximum, f"{label}.maximum")
    tolerance = _finite(monotone_tolerance, f"{label}.monotone_tolerance")
    if low > high or tolerance < 0:
        raise AcceptanceError(f"{label} bounds or monotonicity tolerance are invalid")
    for i, value in enumerate(result):
        if value < low - tolerance or value > high + tolerance:
            raise AcceptanceError(f"{label}[{i}]={value} is outside [{low}, {high}]")
    if any(b < a - tolerance for a, b in zip(result, result[1:])):
        raise AcceptanceError(f"{label} is not monotone nondecreasing")
    return result


def validate_native_cure_metrics(metrics: Mapping[str, Any], *, max_step_s: float = 1.0,
                                requested_times_s: Sequence[float] | None = None,
                                analytic_tolerance: float = 1e-5,
                                final_alpha_minimum: float = 0.95,
                                final_qpost_minimum: float = 0.90) -> dict[str, Any]:
    """Validate native adhesive averages and signed energy series for one case."""
    try:
        times = metrics["stored_times_s"]
        iso = metrics["alpha_iso_mean"]
        alpha = metrics["alpha_mean"]
        qpost = metrics["qpost_mean"]
    except (KeyError, TypeError) as exc:
        raise AcceptanceError("native cure metrics are missing required time-averaged fields") from exc
    if not isinstance(times, list) or not isinstance(iso, list) or not isinstance(alpha, list) or not isinstance(qpost, list):
        raise AcceptanceError("native cure time and average fields must be arrays")
    requested = (list(requested_times_s) if requested_times_s is not None
                 else times)
    validate_stored_times(times, requested, max_step_s=max_step_s)
    iso_values = validate_monotone_series(times, iso, minimum=0.20, maximum=1.0,
                                          label="alpha_iso_mean")
    alpha_values = validate_monotone_series(times, alpha, minimum=0.20, maximum=1.0,
                                            label="alpha_mean")
    qpost_values = validate_monotone_series(times, qpost, minimum=0.0, maximum=1.0,
                                            label="qpost_mean")
    if len(iso_values) != len(times):
        raise AcceptanceError("alpha_iso mean and stored-time counts differ")
    if abs(_finite(times[0], "stored_times_s[0]")) > 1e-12 or abs(
            _finite(times[-1], f"stored_times_s[{len(times)-1}]") - 1500.0) > 1e-12:
        raise AcceptanceError("native cure history must cover the exact 0..1500 s interval")
    checkpoints = [0, 10, 30, 47, 60, 120, 240, 960, 1500]
    analytic = analytic_alpha_iso(
        checkpoints, alpha0=0.20, t0_k=298.15, pre_exponential_s=1e5,
        activation_j_mol=55e3, gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2, uv_duration_s=120,
    )
    checked: list[dict[str, float]] = []
    for time_s, expected in zip(checkpoints, analytic):
        matched = [i for i, stored in enumerate(times) if abs(float(stored) - time_s) <= 1e-12]
        if len(matched) != 1:
            raise AcceptanceError(f"analytic checkpoint {time_s} s has {len(matched)} exact stored matches")
        observed = iso_values[matched[0]]
        error = abs(observed - expected)
        if error > analytic_tolerance:
            raise AcceptanceError(f"alpha_iso mean error {error} exceeds {analytic_tolerance} at {time_s} s")
        checked.append({"time_s": float(time_s), "observed": observed,
                        "analytic": expected, "absolute_error": error})
    if alpha_values[-1] < final_alpha_minimum:
        raise AcceptanceError(f"final adhesive alpha mean {alpha_values[-1]} is below {final_alpha_minimum}")
    if qpost_values[-1] < final_qpost_minimum:
        raise AcceptanceError(f"final adhesive qpost mean {qpost_values[-1]} is below {final_qpost_minimum}")
    energy_samples = metrics.get("energy_samples")
    if not isinstance(energy_samples, list) or len(energy_samples) != len(times):
        raise AcceptanceError("energy samples must cover every stored accepted solution time exactly once")
    for i, (stored_time, energy_row) in enumerate(zip(times, energy_samples)):
        if not isinstance(energy_row, Mapping):
            raise AcceptanceError(f"energy_samples[{i}] is not an object")
        energy_time = _finite(energy_row.get("time_s"), f"energy_samples[{i}].time_s")
        if abs(energy_time - _finite(stored_time, f"stored_times_s[{i}]")) > 1e-12:
            raise AcceptanceError(
                f"energy samples do not match the complete stored-time vector at index {i}")
    energy = heat_balance_residuals(energy_samples, max_gap_s=max_step_s)
    return {"stored_times_count": len(times), "exact_analytic_checkpoints": checked,
            "final_alpha_mean": alpha_values[-1], "final_qpost_mean": qpost_values[-1],
            "energy_sample_count": len(energy), "max_energy_relative_residual":
            max(row["relative_residual"] for row in energy)}


def validate_mechanics_benchmark(metrics: Mapping[str, Any], *, case_id: str,
                                 displacement_relative_tolerance: float = 1e-3,
                                 stress_relative_tolerance: float = 1e-3,
                                 free_stress_max_pa: float = 100.0,
                                 fixed_displacement_max_m: float = 1e-12,
                                 fixed_shear_max_pa: float = 100.0) -> dict[str, Any]:
    """Check the two homogeneous axisymmetric eigenstrain benchmarks.

    The free model uses one uniform material, one uniform volumetric strain,
    and an origin point gauge. The fully fixed model constrains only the three
    physical exterior edges; the r=0 axis retains its native symmetry role.
    Stress components must have been mapped from the native Equation View
    descriptions before this numerical gate is called.
    """
    if case_id not in {"mechanics_free_expansion", "mechanics_fully_fixed"}:
        raise AcceptanceError(f"unknown mechanics benchmark case {case_id!r}")
    if metrics.get("schema") != "W24_NATIVE_MECHANICS_METRICS_V1" or metrics.get("case_id") != case_id:
        raise AcceptanceError("mechanics metrics artifact has the wrong schema or benchmark identity")
    if metrics.get("native") is not True or metrics.get("native_study_run_calls") != 0:
        raise AcceptanceError("mechanics metrics must be native post-solve readbacks with no extra study run")
    times = metrics.get("stored_times_s")
    if not isinstance(times, list) or len(times) != 1:
        raise AcceptanceError("stationary mechanics benchmark must contain exactly one stored solution")
    _finite(times[0], "mechanics.stored_times_s[0]")
    descriptors = metrics.get("stress_component_descriptors")
    if not isinstance(descriptors, Mapping) or set(descriptors) != set(STRESS_ROLES):
        raise AcceptanceError("mechanics metrics omit one or more mapped stress roles")
    for role in STRESS_ROLES:
        descriptor = descriptors[role]
        if (not isinstance(descriptor, Mapping) or descriptor.get("unit") != "Pa" or
                not descriptor.get("expression") or not descriptor.get("description")):
            raise AcceptanceError(f"native {role} stress descriptor lacks expression, description, or Pa unit")

    means = metrics.get("stress_mean_pa")
    maxima = metrics.get("stress_abs_max_pa")
    if not isinstance(means, Mapping) or set(means) != set(STRESS_ROLES):
        raise AcceptanceError("mechanics metrics omit native domain-mean stress components")
    if not isinstance(maxima, Mapping) or set(maxima) != set(STRESS_ROLES):
        raise AcceptanceError("mechanics metrics omit native absolute stress maxima")
    mean_values = {role: _finite(means[role], f"stress_mean_pa.{role}") for role in STRESS_ROLES}
    max_values = {role: _finite(maxima[role], f"stress_abs_max_pa.{role}") for role in STRESS_ROLES}
    displacement_max = metrics.get("displacement_abs_max_m")
    probe = metrics.get("displacement_probe_m")
    if (not isinstance(displacement_max, list) or len(displacement_max) != 2 or
            not isinstance(probe, list) or len(probe) != 2):
        raise AcceptanceError("mechanics metrics omit native radial/axial displacement values")
    displacement_max_values = [_finite(value, f"displacement_abs_max_m[{i}]")
                               for i, value in enumerate(displacement_max)]
    probe_values = [_finite(value, f"displacement_probe_m[{i}]")
                    for i, value in enumerate(probe)]
    if metrics.get("displacement_probe_coordinate_m") != [100e-6, 200e-6]:
        raise AcceptanceError("mechanics displacement probe is not bound to the frozen cylinder corner")

    if case_id == "mechanics_free_expansion":
        expected = (1e-4 * 100e-6, 1e-4 * 200e-6)
        for label, observed, reference in zip(("ur(R)", "uz(H)"), probe_values, expected):
            relative = abs(observed - reference) / abs(reference)
            if relative > displacement_relative_tolerance:
                raise AcceptanceError(f"free expansion {label} relative error {relative} exceeds "
                                      f"{displacement_relative_tolerance}")
        for role, value in max_values.items():
            if value >= free_stress_max_pa:
                raise AcceptanceError(f"free expansion native max |{role}|={value} Pa exceeds "
                                      f"{free_stress_max_pa} Pa")
        return {"status": "PASS", "case_id": case_id,
                "expected_displacement_m": list(expected),
                "observed_displacement_m": probe_values,
                "max_abs_stress_pa": max_values,
                "acceptance_scope": "homogeneous free-expansion analytic mechanics benchmark"}

    bulk_modulus_pa = 2e9 / (3.0 * (1.0 - 2.0 * 0.35))
    expected_pressure_pa = -bulk_modulus_pa * 3e-4
    normal_roles = ("radial_normal", "hoop_normal", "axial_normal")
    for role in normal_roles:
        relative = abs(mean_values[role] - expected_pressure_pa) / abs(expected_pressure_pa)
        if relative > stress_relative_tolerance:
            raise AcceptanceError(f"fully fixed mean {role} stress relative error {relative} exceeds "
                                  f"{stress_relative_tolerance}")
    if max(displacement_max_values) >= fixed_displacement_max_m:
        raise AcceptanceError("fully fixed benchmark displacement exceeds the frozen absolute limit")
    if max_values["rz_shear"] >= fixed_shear_max_pa:
        raise AcceptanceError("fully fixed benchmark native maximum |rz shear| exceeds the frozen limit")
    return {"status": "PASS", "case_id": case_id,
            "expected_mean_normal_stress_pa": expected_pressure_pa,
            "observed_mean_normal_stress_pa": {role: mean_values[role] for role in normal_roles},
            "maximum_displacement_m": max(displacement_max_values),
            "max_abs_shear_pa": max_values["rz_shear"],
            "acceptance_scope": "homogeneous fully restrained analytic mechanics benchmark"}


def validate_boundary_jump(jumps: Mapping[str, float], *,
                           limits: Mapping[str, float]) -> dict[str, float]:
    if set(jumps) != set(limits) or not jumps:
        raise AcceptanceError("stage handoff jump fields must exactly match the frozen limit set")
    checked: dict[str, float] = {}
    for name, limit in limits.items():
        value = abs(_finite(jumps[name], f"handoff_jump.{name}"))
        maximum = _finite(limit, f"handoff_limit.{name}")
        if maximum < 0 or value > maximum:
            raise AcceptanceError(f"stage handoff {name} jump {value} exceeds {maximum}")
        checked[name] = value
    return checked


def compare_full_dof_frames(candidate: Mapping[str, Any], reference: Mapping[str, Any],
                            field_names: Mapping[str, Sequence[str]], *, t0_k: float,
                            temperature_l2_floor_k: float = 1.0,
                            displacement_l2_floor_m: float = 1e-8) -> dict[str, float]:
    """Compare staged/continuous fields at one exact common output time."""
    ta = _finite(candidate.get("time_s"), "candidate.time_s")
    tb = _finite(reference.get("time_s"), "reference.time_s")
    if abs(ta - tb) > 1e-12:
        raise AcceptanceError("candidate and reference times are not an exact common output")
    a = dof_value_map(candidate)
    b = dof_value_map(reference)
    if set(a) != set(b):
        missing = len(set(b) - set(a)); extra = len(set(a) - set(b))
        raise AcceptanceError(f"exact DOF key sets differ: missing={missing}, extra={extra}")

    a_t, b_t = _matched_field_vectors(a, b, field_names.get("temperature", ()),
                                     "temperature", candidate_offset=t0_k,
                                     reference_offset=t0_k)
    a_alpha, b_alpha = _matched_field_vectors(a, b, field_names.get("alpha", ()), "alpha")
    a_q, b_q = _matched_field_vectors(a, b, field_names.get("qpost", ()), "qpost")
    a_ur, b_ur = _matched_field_vectors(a, b, field_names.get("displacement_r", ()),
                                        "displacement_r")
    a_uz, b_uz = _matched_field_vectors(a, b, field_names.get("displacement_z", ()),
                                        "displacement_z")
    return {
        "temperature_offset_l2": normalized_l2_error(a_t, b_t, floor=temperature_l2_floor_k),
        "displacement_l2": normalized_l2_error(a_ur + a_uz, b_ur + b_uz,
                                                floor=displacement_l2_floor_m),
        "alpha_max_abs": max(abs(x - y) for x, y in zip(a_alpha, b_alpha)),
        "qpost_max_abs": max(abs(x - y) for x, y in zip(a_q, b_q)),
    }


def heat_balance_residuals(samples: Sequence[Mapping[str, Any]], *, max_gap_s: float,
                           residual_tolerance: float = 0.03,
                           denominator_floor_j: float = 1e-12) -> list[dict[str, float]]:
    """Signed composite-trapezoid energy balance at all stored accepted times."""
    if len(samples) < 2:
        raise AcceptanceError("energy balance needs at least two native accepted-time samples")
    if not math.isfinite(max_gap_s) or max_gap_s <= 0:
        raise AcceptanceError("maximum energy sample gap must be positive and finite")
    if not math.isfinite(residual_tolerance) or residual_tolerance < 0:
        raise AcceptanceError("energy residual tolerance must be finite and nonnegative")
    if not math.isfinite(denominator_floor_j) or denominator_floor_j <= 0:
        raise AcceptanceError("energy denominator floor must be positive and finite")
    times: list[float] = []
    energies: list[float] = []
    reaction_power: list[float] = []
    outward_power: list[float] = []
    for i, row in enumerate(samples):
        if row.get("outward_sign") != "positive_outward":
            raise AcceptanceError("boundary heat flux sign must be explicitly positive outward")
        times.append(_finite(row.get("time_s"), f"samples[{i}].time_s"))
        energies.append(_finite(row.get("stored_energy_j"), f"samples[{i}].stored_energy_j"))
        reaction_power.append(_finite(row.get("reaction_power_w"), f"samples[{i}].reaction_power_w"))
        outward_power.append(_finite(row.get("outward_power_w"), f"samples[{i}].outward_power_w"))
    if any(b <= a for a, b in zip(times, times[1:])):
        raise AcceptanceError("energy times must be strictly increasing")
    if abs(times[0]) > 1e-12:
        raise AcceptanceError("energy balance samples must begin at t=0 for E(0)")
    q_rxn = 0.0
    q_out = 0.0
    out: list[dict[str, float]] = []
    base_energy = energies[0]
    for i, time_s in enumerate(times):
        if i:
            dt = time_s - times[i - 1]
            if dt > max_gap_s + 1e-12:
                raise AcceptanceError(f"energy sample gap {dt} s exceeds {max_gap_s} s")
            q_rxn += dt * (reaction_power[i - 1] + reaction_power[i]) / 2.0
            q_out += dt * (outward_power[i - 1] + outward_power[i]) / 2.0
        delta_e = energies[i] - base_energy
        residual_j = delta_e - q_rxn + q_out
        denominator_j = max(abs(delta_e) + abs(q_rxn) + abs(q_out), denominator_floor_j)
        relative = abs(residual_j) / denominator_j
        out.append({"time_s": time_s, "delta_energy_j": delta_e,
                    "reaction_energy_j": q_rxn, "outward_energy_j": q_out,
                    "signed_residual_j": residual_j,
                    "relative_residual": relative})
        if relative > residual_tolerance:
            raise AcceptanceError(f"energy balance relative residual {relative} exceeds {residual_tolerance} at {time_s} s")
    return out


def require_control_delta(baseline: float, control: float, *, minimum: float, label: str) -> float:
    delta = _finite(control, f"{label}.control") - _finite(baseline, f"{label}.baseline")
    if delta < minimum:
        raise AcceptanceError(f"{label} delta {delta} is below required {minimum}")
    return delta
