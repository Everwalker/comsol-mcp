"""Fail-closed extraction helpers for W24 native static-shape fields.

The helpers consume raw native point samples. They do not estimate a shape,
interpolate missing radial stations, or treat the bulk Phase Field energy as a
total energy. A radial station with gas everywhere above the substrate is an
expected dry-support point; a malformed or multi-branch column is an error.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


class ShapeMetricsError(ValueError):
    """A native shape field cannot be mapped to the frozen metric contract."""


def validate_stored_time_grid(
    stored_times_s: Sequence[float],
    requested_times_s: Sequence[float],
    *,
    tolerance_s: float = 1e-12,
) -> dict[str, Any]:
    """Require every requested output time to exist exactly in stored data.

    Extra accepted solver times are retained and allowed. No nearest-time
    substitution or interpolation is used to satisfy a requested time.
    """
    stored = _finite_vector(stored_times_s, "stored_times_s", min_length=2)
    requested = _finite_vector(requested_times_s, "requested_times_s", min_length=2)
    if not math.isfinite(tolerance_s) or tolerance_s < 0:
        raise ShapeMetricsError("time tolerance must be finite and nonnegative")
    if not _strictly_increasing(stored) or not _strictly_increasing(requested):
        raise ShapeMetricsError("stored and requested times must be strictly increasing without duplicates")
    if abs(stored[0] - requested[0]) > tolerance_s or abs(stored[-1] - requested[-1]) > tolerance_s:
        raise ShapeMetricsError("stored data must include the requested initial and final times")

    matched: list[dict[str, float]] = []
    for target in requested:
        nearest = min(stored, key=lambda value: abs(value - target))
        error = abs(nearest - target)
        if error > tolerance_s:
            raise ShapeMetricsError(
                f"requested time {target:.17g}s is absent from stored native times; nearest differs by {error:.3g}s"
            )
        matched.append({"requested_s": target, "stored_s": nearest, "absolute_error_s": error})
    return {
        "status": "EXACT_REQUESTED_TIMES_PRESENT",
        "stored_time_count": len(stored),
        "requested_time_count": len(requested),
        "additional_stored_time_count": len(stored) - len(requested),
        "matched_requested_times": matched,
        "interpolation_used": False,
        "stored_times_s": stored,
    }


def extract_upper_interface_profile(
    columns: Sequence[Mapping[str, Any]],
    *,
    zero_tolerance: float = 1e-8,
    floor_tolerance_m: float = 1e-12,
) -> dict[str, Any]:
    """Extract a single upper phi=0 branch on a fixed radial support.

    Each column has ``radius_m``, ``floor_m``, ``z_m`` and ``phi``. Values are
    ordered from substrate upward and must represent fluid-domain samples.
    Columns that remain gas-positive are explicitly marked ``DRY_SUPPORT``.
    A column containing liquid must start on the liquid side and have exactly
    one negative-to-positive upper crossing. Multiple branches, reversed phase
    order, truncated columns, interior tangencies, and malformed samples fail.
    """
    if not math.isfinite(zero_tolerance) or zero_tolerance <= 0:
        raise ShapeMetricsError("zero_tolerance must be positive and finite")
    if not math.isfinite(floor_tolerance_m) or floor_tolerance_m < 0:
        raise ShapeMetricsError("floor_tolerance_m must be finite and nonnegative")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)) or len(columns) < 2:
        raise ShapeMetricsError("at least two fixed radial columns are required")

    radii: list[float] = []
    heights: list[float | None] = []
    support: list[str] = []
    for index, column in enumerate(columns):
        if not isinstance(column, Mapping):
            raise ShapeMetricsError(f"column {index} is not a mapping")
        radius = _finite_scalar(column.get("radius_m"), f"column {index} radius_m")
        floor = _finite_scalar(column.get("floor_m"), f"column {index} floor_m")
        z = _finite_vector(column.get("z_m"), f"column {index} z_m", min_length=3)
        phi = _finite_vector(column.get("phi"), f"column {index} phi", min_length=3)
        if len(z) != len(phi):
            raise ShapeMetricsError(f"column {index} z_m/phi lengths differ")
        if radius < 0 or floor < 0 or not _strictly_increasing(z):
            raise ShapeMetricsError(f"column {index} has invalid radius/floor or unordered vertical samples")
        if abs(z[0] - floor) > floor_tolerance_m:
            raise ShapeMetricsError(
                f"column {index} first vertical sample does not coincide with its bound physical substrate; "
                "a raised start could conceal a truncated wet column as dry support"
            )
        height, classification = _upper_column_crossing(
            z, phi, floor=floor, zero_tolerance=zero_tolerance,
            floor_tolerance_m=floor_tolerance_m, column_index=index,
        )
        radii.append(radius)
        heights.append(height)
        support.append(classification)
    if not _strictly_increasing(radii):
        raise ShapeMetricsError("radial sample coordinates must be strictly increasing and unique")

    wet_indices = [i for i, kind in enumerate(support) if kind == "WET_INTERFACE"]
    dry_indices = [i for i, kind in enumerate(support) if kind == "DRY_SUPPORT"]
    return {
        "status": "PROFILE_EXTRACTED",
        "branch_policy": "one substrate-to-gas negative-to-positive crossing per wet column; gas-only columns are dry support",
        "zero_tolerance": zero_tolerance,
        "radius_m": radii,
        "interface_height_m": heights,
        "support": support,
        "wet_support_index_range": _index_range(wet_indices),
        "dry_support_index_range": _index_range(dry_indices),
        "wet_support_point_count": len(wet_indices),
        "dry_support_point_count": len(dry_indices),
    }


def compare_upper_interface_profiles(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    minimum_common_fraction_of_smaller_support: float = 0.95,
    minimum_common_points: int = 3,
    radius_tolerance_m: float = 1e-12,
) -> dict[str, Any]:
    """Compare adjacent shapes only on the explicit wet-support intersection."""
    if (not math.isfinite(minimum_common_fraction_of_smaller_support) or
            not 0 < minimum_common_fraction_of_smaller_support <= 1):
        raise ShapeMetricsError("minimum common-support fraction must be in (0, 1]")
    if type(minimum_common_points) is not int or minimum_common_points < 1:
        raise ShapeMetricsError("minimum_common_points must be a positive integer")
    radii_a = _finite_vector(first.get("radius_m"), "first.radius_m", min_length=2)
    radii_b = _finite_vector(second.get("radius_m"), "second.radius_m", min_length=2)
    heights_a = first.get("interface_height_m")
    heights_b = second.get("interface_height_m")
    support_a = first.get("support")
    support_b = second.get("support")
    if not all(isinstance(v, Sequence) and not isinstance(v, (str, bytes))
               for v in (heights_a, heights_b, support_a, support_b)):
        raise ShapeMetricsError("profile heights and support arrays are required")
    if not (len(radii_a) == len(radii_b) == len(heights_a) == len(heights_b) ==
            len(support_a) == len(support_b)):
        raise ShapeMetricsError("profile arrays have different lengths")
    if not math.isfinite(radius_tolerance_m) or radius_tolerance_m < 0:
        raise ShapeMetricsError("radius_tolerance_m must be finite and nonnegative")
    for i, (ra, rb) in enumerate(zip(radii_a, radii_b)):
        if abs(ra - rb) > radius_tolerance_m:
            raise ShapeMetricsError(f"profile radial grids differ at index {i}; interpolation is forbidden")

    wet_a = {i for i, item in enumerate(support_a) if item == "WET_INTERFACE"}
    wet_b = {i for i, item in enumerate(support_b) if item == "WET_INTERFACE"}
    if not wet_a or not wet_b:
        raise ShapeMetricsError("both profiles must contain a wet interface support")
    common = sorted(wet_a & wet_b)
    smaller_count = min(len(wet_a), len(wet_b))
    fraction = len(common) / smaller_count
    if (len(common) < minimum_common_points or
            fraction < minimum_common_fraction_of_smaller_support):
        raise ShapeMetricsError("profiles do not have enough common wet support for a stable-window comparison")

    differences: list[float] = []
    for index in common:
        a = _finite_scalar(heights_a[index], f"first interface height {index}")
        b = _finite_scalar(heights_b[index], f"second interface height {index}")
        differences.append(abs(a - b))
    union_count = len(wet_a | wet_b)
    return {
        "status": "COMMON_WET_SUPPORT_COMPARISON",
        "interpolation_used": False,
        "common_support_indices": common,
        "common_support_radius_range_m": [radii_a[common[0]], radii_a[common[-1]]],
        "common_point_count": len(common),
        "smaller_wet_support_point_count": smaller_count,
        "common_fraction_of_smaller_support": fraction,
        "common_fraction_of_union_support": len(common) / union_count,
        "max_abs_interface_displacement_m": max(differences),
        "rms_interface_displacement_m": math.sqrt(sum(value * value for value in differences) / len(differences)),
        "comparison_rule": "only indices classified WET_INTERFACE in both raw profiles; dry support is not a missing-crossing failure",
    }


def evaluate_shape_history(
    samples: Sequence[Mapping[str, Any]],
    *,
    capillary_time_s: float,
    analytic_initial_volume_m3: float,
    epsilon_m: float,
    surface_tension_n_per_m: float,
    glue_viscosity_pa_s: float,
    end_capillary_times: float = 20.0,
    stable_window_capillary_times: float = 4.0,
    time_tolerance_s: float = 1e-12,
    volume_drift_limit: float = 1e-3,
    initial_volume_limit: float = 1e-3,
    interface_displacement_fraction: float = 0.25,
    contact_line_motion_fraction: float = 0.25,
    maximum_speed_fraction: float = 1e-3,
) -> dict[str, Any]:
    """Apply the preregistered full-history and last-window shape gates.

    Every accepted native time contributes to the volume-drift gate. The
    stable window is the final ``stable_window_capillary_times`` through the
    requested end time. Interface displacement, contact-line travel, and
    maximum speed are checked separately so similar wet supports alone cannot
    pass a shape comparison.
    """
    positive_inputs = {
        "capillary_time_s": capillary_time_s,
        "analytic_initial_volume_m3": analytic_initial_volume_m3,
        "epsilon_m": epsilon_m,
        "surface_tension_n_per_m": surface_tension_n_per_m,
        "glue_viscosity_pa_s": glue_viscosity_pa_s,
        "end_capillary_times": end_capillary_times,
        "stable_window_capillary_times": stable_window_capillary_times,
    }
    for name, value in positive_inputs.items():
        if not math.isfinite(value) or value <= 0:
            raise ShapeMetricsError(f"{name} must be positive and finite")
    for name, value in {
        "time_tolerance_s": time_tolerance_s,
        "volume_drift_limit": volume_drift_limit,
        "initial_volume_limit": initial_volume_limit,
        "interface_displacement_fraction": interface_displacement_fraction,
        "contact_line_motion_fraction": contact_line_motion_fraction,
        "maximum_speed_fraction": maximum_speed_fraction,
    }.items():
        if not math.isfinite(value) or value < 0:
            raise ShapeMetricsError(f"{name} must be finite and nonnegative")
    if stable_window_capillary_times >= end_capillary_times:
        raise ShapeMetricsError("stable window must be shorter than the full simulated history")
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)) or len(samples) < 3:
        raise ShapeMetricsError("shape history must contain initial, stable-window, and final samples")

    rows: list[dict[str, Any]] = []
    times: list[float] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise ShapeMetricsError(f"history sample {index} is not a mapping")
        time_s = _finite_scalar(sample.get("time_s"), f"sample {index} time_s")
        volume = _finite_scalar(sample.get("phase1_volume_m3"), f"sample {index} phase1_volume_m3")
        contact = _finite_scalar(sample.get("contact_line_arclength_m"),
                                  f"sample {index} contact_line_arclength_m")
        speed = _finite_scalar(sample.get("maximum_speed_m_s"), f"sample {index} maximum_speed_m_s")
        profile = sample.get("upper_profile")
        if not isinstance(profile, Mapping):
            raise ShapeMetricsError(f"sample {index} upper_profile is not a mapping")
        if volume <= 0 or contact < 0 or speed < 0:
            raise ShapeMetricsError(f"sample {index} has a nonphysical negative/zero metric")
        times.append(time_s)
        rows.append({"time_s": time_s, "phase1_volume_m3": volume,
                     "contact_line_arclength_m": contact,
                     "maximum_speed_m_s": speed, "upper_profile": profile})
    if not _strictly_increasing(times):
        raise ShapeMetricsError("native solution times must be strictly increasing and unique")

    expected_end = end_capillary_times * capillary_time_s
    stable_start = expected_end - stable_window_capillary_times * capillary_time_s
    errors: list[str] = []
    if abs(times[0]) > time_tolerance_s:
        errors.append("history_does_not_start_at_t0")
    if abs(times[-1] - expected_end) > time_tolerance_s:
        errors.append("history_does_not_reach_requested_end_time")
    stable_start_indices = [i for i, value in enumerate(times)
                            if abs(value - stable_start) <= time_tolerance_s]
    if len(stable_start_indices) != 1:
        errors.append("stable_window_start_time_missing_or_ambiguous")
        stable_rows = [row for row in rows if stable_start - time_tolerance_s <= row["time_s"] <= expected_end + time_tolerance_s]
    else:
        stable_rows = rows[stable_start_indices[0]:]
    if len(stable_rows) < 2:
        errors.append("stable_window_has_fewer_than_two_stored_times")

    initial_volume = rows[0]["phase1_volume_m3"]
    initial_relative_error = abs(initial_volume - analytic_initial_volume_m3) / analytic_initial_volume_m3
    if initial_relative_error > initial_volume_limit:
        errors.append("post_initialization_volume_differs_from_analytic_geometry")
    volume_drifts = [abs(row["phase1_volume_m3"] - initial_volume) / initial_volume for row in rows]
    maximum_volume_drift = max(volume_drifts)
    if maximum_volume_drift > volume_drift_limit:
        errors.append("phase1_volume_drift_exceeds_limit")

    speed_limit = maximum_speed_fraction * surface_tension_n_per_m / glue_viscosity_pa_s
    stable_speeds = [row["maximum_speed_m_s"] for row in stable_rows]
    maximum_stable_speed = max(stable_speeds) if stable_speeds else None
    if maximum_stable_speed is None:
        errors.append("stable_window_has_no_speed_samples")
    elif maximum_stable_speed > speed_limit:
        errors.append("stable_window_maximum_speed_exceeds_limit")

    interface_limit = interface_displacement_fraction * epsilon_m
    contact_line_limit = contact_line_motion_fraction * epsilon_m
    pair_results: list[dict[str, Any]] = []
    for left, right in zip(stable_rows, stable_rows[1:]):
        pair: dict[str, Any] = {"from_time_s": left["time_s"], "to_time_s": right["time_s"]}
        try:
            profile_delta = compare_upper_interface_profiles(
                left["upper_profile"], right["upper_profile"])
            pair["profile_comparison"] = profile_delta
            if profile_delta["max_abs_interface_displacement_m"] > interface_limit:
                pair["interface_gate"] = "FAIL"
                errors.append(f"interface_displacement_exceeds_limit:{left['time_s']:.17g}:{right['time_s']:.17g}")
            else:
                pair["interface_gate"] = "PASS"
        except ShapeMetricsError as exc:
            pair["interface_gate"] = "FAIL"
            pair["interface_error"] = str(exc)
            errors.append(f"interface_support_invalid:{left['time_s']:.17g}:{right['time_s']:.17g}:{exc}")
        contact_motion = abs(right["contact_line_arclength_m"] - left["contact_line_arclength_m"])
        pair["contact_line_motion_m"] = contact_motion
        pair["contact_line_gate"] = "PASS" if contact_motion <= contact_line_limit else "FAIL"
        if contact_motion > contact_line_limit:
            errors.append(f"contact_line_motion_exceeds_limit:{left['time_s']:.17g}:{right['time_s']:.17g}")
        pair_results.append(pair)

    return {
        "status": "STABLE_WINDOW_PASS" if not errors else "STABLE_WINDOW_FAIL",
        "errors": errors,
        "time_range_s": [times[0], times[-1]],
        "stored_time_count": len(times),
        "stable_window_s": [stable_start, expected_end],
        "stable_window_sample_count": len(stable_rows),
        "analytic_initial_volume_m3": analytic_initial_volume_m3,
        "post_initialization_volume_m3": initial_volume,
        "initial_volume_relative_error": initial_relative_error,
        "maximum_all_history_volume_drift": maximum_volume_drift,
        "volume_drift_limit": volume_drift_limit,
        "maximum_stable_speed_m_s": maximum_stable_speed,
        "maximum_stable_speed_limit_m_s": speed_limit,
        "maximum_interface_displacement_limit_m": interface_limit,
        "maximum_contact_line_motion_limit_m": contact_line_limit,
        "stable_window_adjacent_pairs": pair_results,
        "contact_line_gate_separate_from_profile_support": True,
        "volume_gate_separate_from_profile_support": True,
    }


def compare_converged_shape_variants(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
    *,
    drop_radius_m: float,
    epsilon_m: float,
    maximum_height_fraction_of_radius: float = 0.02,
    maximum_contact_line_motion_fraction_of_epsilon: float = 0.5,
    maximum_relative_volume_difference: float = 1e-3,
) -> dict[str, Any]:
    """Compare two already-stable sensitivity cases with independent gates."""
    if baseline.get("status") != "STABLE_WINDOW_PASS" or variant.get("status") != "STABLE_WINDOW_PASS":
        return {"status": "FAIL", "errors": ["both baseline and variant must pass stable-window gates first"]}
    for name, value in {
        "drop_radius_m": drop_radius_m,
        "epsilon_m": epsilon_m,
        "maximum_height_fraction_of_radius": maximum_height_fraction_of_radius,
        "maximum_contact_line_motion_fraction_of_epsilon": maximum_contact_line_motion_fraction_of_epsilon,
        "maximum_relative_volume_difference": maximum_relative_volume_difference,
    }.items():
        if not math.isfinite(value) or value <= 0:
            raise ShapeMetricsError(f"{name} must be positive and finite")
    errors: list[str] = []
    profile_metrics: dict[str, Any] | None = None
    try:
        profile_metrics = compare_upper_interface_profiles(
            baseline["final_profile"], variant["final_profile"])
        if profile_metrics["max_abs_interface_displacement_m"] > drop_radius_m * maximum_height_fraction_of_radius:
            errors.append("final_interface_height_difference_exceeds_limit")
    except (KeyError, ShapeMetricsError) as exc:
        errors.append(f"final_interface_comparison_invalid:{exc}")

    try:
        baseline_contact = _finite_scalar(baseline.get("final_contact_line_arclength_m"),
                                          "baseline.final_contact_line_arclength_m")
        variant_contact = _finite_scalar(variant.get("final_contact_line_arclength_m"),
                                         "variant.final_contact_line_arclength_m")
        contact_difference = abs(baseline_contact - variant_contact)
        contact_limit = epsilon_m * maximum_contact_line_motion_fraction_of_epsilon
        if contact_difference > contact_limit:
            errors.append("final_contact_line_difference_exceeds_limit")
    except ShapeMetricsError as exc:
        errors.append(f"final_contact_line_comparison_invalid:{exc}")
        contact_difference = None
        contact_limit = epsilon_m * maximum_contact_line_motion_fraction_of_epsilon

    try:
        baseline_volume = _finite_scalar(baseline.get("final_volume_m3"), "baseline.final_volume_m3")
        variant_volume = _finite_scalar(variant.get("final_volume_m3"), "variant.final_volume_m3")
        if baseline_volume <= 0 or variant_volume <= 0:
            raise ShapeMetricsError("final volumes must be positive")
        relative_volume_difference = abs(variant_volume - baseline_volume) / baseline_volume
        if relative_volume_difference > maximum_relative_volume_difference:
            errors.append("final_volume_difference_exceeds_limit")
    except ShapeMetricsError as exc:
        errors.append(f"final_volume_comparison_invalid:{exc}")
        relative_volume_difference = None

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "final_profile_comparison": profile_metrics,
        "maximum_allowed_height_difference_m": drop_radius_m * maximum_height_fraction_of_radius,
        "final_contact_line_difference_m": contact_difference,
        "maximum_allowed_contact_line_difference_m": contact_limit,
        "final_relative_volume_difference": relative_volume_difference,
        "maximum_allowed_relative_volume_difference": maximum_relative_volume_difference,
        "contact_line_gate_separate": True,
        "volume_gate_separate": True,
    }


def extract_substrate_contact_line(
    samples: Sequence[Mapping[str, Any]],
    *,
    maximum_sample_spacing_m: float,
    zero_tolerance: float = 1e-8,
) -> dict[str, Any]:
    """Find the sole outward liquid-to-gas crossing along the wettable wall.

    ``arclength_m`` must be cumulative physical path length along the complete
    wetted substrate path: flat base ``r``; or for the stepped case mesa top,
    mesa side, then lower base. The samples must include every segment corner.
    The returned scalar is stable across the 90-degree step turn; no chord is
    drawn diagonally across a corner.
    """
    if not math.isfinite(maximum_sample_spacing_m) or maximum_sample_spacing_m <= 0:
        raise ShapeMetricsError("maximum_sample_spacing_m must be positive and finite")
    if not math.isfinite(zero_tolerance) or zero_tolerance <= 0:
        raise ShapeMetricsError("zero_tolerance must be positive and finite")
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes)) or len(samples) < 3:
        raise ShapeMetricsError("at least three substrate wall samples are required")

    arclength: list[float] = []
    values: list[float] = []
    coordinates: list[tuple[float, float]] = []
    for index, sample in enumerate(samples):
        if not isinstance(sample, Mapping):
            raise ShapeMetricsError(f"substrate sample {index} is not a mapping")
        arclength.append(_finite_scalar(sample.get("arclength_m"), f"sample {index} arclength_m"))
        coordinates.append((_finite_scalar(sample.get("r_m"), f"sample {index} r_m"),
                            _finite_scalar(sample.get("z_m"), f"sample {index} z_m")))
        values.append(_finite_scalar(sample.get("phi"), f"sample {index} phi"))
    if not _strictly_increasing(arclength):
        raise ShapeMetricsError("substrate arclength samples must be strictly increasing and unique")
    if any(b - a > maximum_sample_spacing_m * (1.0 + 1e-12)
           for a, b in zip(arclength, arclength[1:])):
        raise ShapeMetricsError("substrate sample spacing exceeds the frozen contact-line resolution")

    signs = [_sign(value, zero_tolerance) for value in values]
    if signs[0] != -1 or signs[-1] != 1:
        raise ShapeMetricsError("substrate path must start wet/liquid-negative and end dry/gas-positive")
    crossing = _single_oriented_crossing(arclength, values, signs,
                                         expected=(-1, 1), label="substrate contact line")
    return {
        "status": "SINGLE_SUBSTRATE_CONTACT_LINE",
        "arclength_m": crossing[0],
        "bracket_indices": list(crossing[1]),
        "bracket_arclength_m": [arclength[crossing[1][0]], arclength[crossing[1][1]]],
        "bracket_coordinates_rz_m": [list(coordinates[crossing[1][0]]), list(coordinates[crossing[1][1]])],
        "sample_count": len(samples),
        "maximum_observed_spacing_m": max(b - a for a, b in zip(arclength, arclength[1:])),
        "maximum_allowed_spacing_m": maximum_sample_spacing_m,
        "zero_tolerance": zero_tolerance,
    }


def _upper_column_crossing(z: list[float], phi: list[float], *, floor: float,
                           zero_tolerance: float, floor_tolerance_m: float,
                           column_index: int) -> tuple[float | None, str]:
    signs = [_sign(value, zero_tolerance) for value in phi]
    if all(sign == 1 for sign in signs):
        return None, "DRY_SUPPORT"

    # At the contact-line radius, the wall value can be exactly zero while
    # liquid remains immediately above it. Ignore only that wall endpoint;
    # the upper interface is still the later negative-to-positive crossing.
    if signs[0] == 0 and abs(z[0] - floor) <= floor_tolerance_m:
        signs[0] = -1 if len(signs) > 1 and signs[1] == -1 else signs[0]

    nonzero = [i for i, sign in enumerate(signs) if sign]
    if not nonzero:
        raise ShapeMetricsError(f"column {column_index} is an unresolved all-zero phase field")
    if signs[nonzero[0]] != -1:
        if signs[nonzero[0]] == 1 and not any(sign == -1 for sign in signs):
            zero_indices = [i for i, sign in enumerate(signs) if sign == 0]
            if (len(zero_indices) == 1 and zero_indices[0] == 0 and
                    abs(z[0] - floor) <= floor_tolerance_m):
                return floor, "WET_INTERFACE"
            if zero_indices:
                raise ShapeMetricsError(f"column {column_index} has an unbracketed interior zero/tangent")
            return None, "DRY_SUPPORT"
        raise ShapeMetricsError(f"column {column_index} does not begin on the liquid side of its upper interface")

    crossing = _single_oriented_crossing(z, phi, signs, expected=(-1, 1),
                                         label=f"upper interface in column {column_index}")
    height = crossing[0]
    if height <= floor + floor_tolerance_m:
        raise ShapeMetricsError(f"column {column_index} upper interface collapsed to the substrate")
    if height >= z[-1] - floor_tolerance_m:
        raise ShapeMetricsError(f"column {column_index} upper interface is truncated at the top sample")
    return height, "WET_INTERFACE"


def _single_oriented_crossing(coordinates: list[float], values: list[float], signs: list[int],
                              *, expected: tuple[int, int], label: str) -> tuple[float, tuple[int, int]]:
    nonzero = [i for i, sign in enumerate(signs) if sign]
    if len(nonzero) < 2:
        raise ShapeMetricsError(f"{label} has no bracketing phase samples")
    if nonzero[0] != 0 or nonzero[-1] != len(signs) - 1:
        raise ShapeMetricsError(f"{label} has an unbracketed leading or trailing zero")
    transitions: list[tuple[int, int]] = []
    for left, right in zip(nonzero, nonzero[1:]):
        left_sign, right_sign = signs[left], signs[right]
        zeros_between = [i for i in range(left + 1, right) if signs[i] == 0]
        if left_sign == right_sign:
            if zeros_between:
                raise ShapeMetricsError(f"{label} contains an unbracketed zero/tangent")
            continue
        transitions.append((left, right))
        if (left_sign, right_sign) != expected:
            raise ShapeMetricsError(f"{label} has a reversed phase transition")
        if len(zeros_between) > 1:
            raise ShapeMetricsError(f"{label} has a multi-sample zero plateau")
    if len(transitions) != 1:
        raise ShapeMetricsError(f"{label} must have exactly one oriented zero crossing; observed {len(transitions)}")
    left, right = transitions[0]
    zeros_between = [i for i in range(left + 1, right) if signs[i] == 0]
    if zeros_between:
        crossing = coordinates[zeros_between[0]]
    else:
        denominator = values[right] - values[left]
        if denominator == 0.0:
            raise ShapeMetricsError(f"{label} has a degenerate zero-crossing bracket")
        fraction = -values[left] / denominator
        if not 0.0 <= fraction <= 1.0:
            raise ShapeMetricsError(f"{label} interpolation escaped its native sample bracket")
        crossing = coordinates[left] + fraction * (coordinates[right] - coordinates[left])
    return crossing, (left, right)


def _sign(value: float, tolerance: float) -> int:
    if value < -tolerance:
        return -1
    if value > tolerance:
        return 1
    return 0


def _finite_scalar(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ShapeMetricsError(f"{label} must be a finite number")
    return float(value)


def _finite_vector(values: Any, label: str, *, min_length: int) -> list[float]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) < min_length:
        raise ShapeMetricsError(f"{label} must contain at least {min_length} values")
    return [_finite_scalar(value, f"{label}[{index}]") for index, value in enumerate(values)]


def _strictly_increasing(values: Sequence[float]) -> bool:
    return all(a < b for a, b in zip(values, values[1:]))


def _index_range(indices: list[int]) -> list[int] | None:
    return [indices[0], indices[-1]] if indices else None
