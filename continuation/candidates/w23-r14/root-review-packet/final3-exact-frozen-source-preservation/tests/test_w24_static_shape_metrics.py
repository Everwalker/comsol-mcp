import pytest

from tools.w24_static_shape_metrics import (
    ShapeMetricsError,
    compare_upper_interface_profiles,
    extract_substrate_contact_line,
    extract_upper_interface_profile,
    validate_stored_time_grid,
)


def _column(radius, phi, *, floor=0.0, z=(0.0, 0.25, 0.5, 0.75, 1.0)):
    return {"radius_m": radius, "floor_m": floor, "z_m": list(z), "phi": list(phi)}


def _profile(radii, heights, wet_count):
    support = ["WET_INTERFACE" if i < wet_count else "DRY_SUPPORT"
               for i in range(len(radii))]
    values = [float(value) if support[i] == "WET_INTERFACE" else None
              for i, value in enumerate(heights)]
    return {"radius_m": list(radii), "interface_height_m": values, "support": support}


def test_upper_profile_treats_gas_only_columns_as_expected_dry_support():
    result = extract_upper_interface_profile([
        _column(0.0, [-1.0, -0.5, 0.25, 0.8, 1.0]),
        _column(0.1, [-1.0, -0.1, 0.0, 0.6, 1.0]),
        _column(0.2, [1.0, 1.0, 1.0, 1.0, 1.0]),
    ])

    assert result["support"] == ["WET_INTERFACE", "WET_INTERFACE", "DRY_SUPPORT"]
    assert result["interface_height_m"][0] == pytest.approx(0.4166666666666667)
    assert result["interface_height_m"][1] == pytest.approx(0.5)
    assert result["interface_height_m"][2] is None
    assert result["dry_support_point_count"] == 1


def test_upper_profile_refuses_gas_only_samples_that_start_above_the_substrate():
    with pytest.raises(ShapeMetricsError, match="does not coincide"):
        extract_upper_interface_profile([
            _column(0.0, [1.0, 1.0, 1.0, 1.0, 1.0], z=(0.1, 0.3, 0.5, 0.7, 0.9)),
            _column(0.1, [-1.0, -0.5, 0.25, 0.8, 1.0]),
        ])


@pytest.mark.parametrize(
    "phi, message",
    [
        ([-1.0, 0.5, -0.5, 0.5, 1.0], "reversed phase transition|exactly one"),
        ([-1.0, -0.5, -0.1, -0.02, -0.01], "exactly one"),
        ([1.0, 0.0, 1.0, 1.0, 1.0], "interior zero"),
        ([-1.0, 0.0, 0.0, 1.0, 1.0], "zero plateau"),
        ([1.0, -0.5, -0.2, 0.5, 1.0], "does not begin"),
    ],
)
def test_upper_profile_rejects_missing_reversed_or_multibranch_columns(phi, message):
    with pytest.raises(ShapeMetricsError, match=message):
        extract_upper_interface_profile([
            _column(0.0, phi),
            _column(0.1, [-1.0, -0.5, 0.25, 0.8, 1.0]),
        ])


def test_profile_comparison_uses_common_wet_support_and_reports_dry_support():
    first = _profile([0.0, 0.1, 0.2, 0.3, 0.4], [1.0, 1.1, 1.2, 1.1, 0.0], 4)
    second = _profile([0.0, 0.1, 0.2, 0.3, 0.4], [1.01, 1.08, 1.19, 1.0, 0.0], 3)

    result = compare_upper_interface_profiles(first, second)

    assert result["status"] == "COMMON_WET_SUPPORT_COMPARISON"
    assert result["common_support_indices"] == [0, 1, 2]
    assert result["common_fraction_of_smaller_support"] == 1.0
    assert result["common_fraction_of_union_support"] == pytest.approx(0.75)
    assert result["max_abs_interface_displacement_m"] == pytest.approx(0.02)
    assert result["interpolation_used"] is False


def test_profile_comparison_refuses_too_little_common_support_or_grid_interpolation():
    first = _profile([0.0, 0.1, 0.2, 0.3], [1, 1, 1, 0], 3)
    almost_disjoint = {
        "radius_m": [0.0, 0.1, 0.2, 0.3],
        "interface_height_m": [1.0, None, None, 1.0],
        "support": ["WET_INTERFACE", "DRY_SUPPORT", "DRY_SUPPORT", "WET_INTERFACE"],
    }
    with pytest.raises(ShapeMetricsError, match="common wet support"):
        compare_upper_interface_profiles(first, almost_disjoint)
    with pytest.raises(ShapeMetricsError, match="radial grids differ"):
        compare_upper_interface_profiles(first, {**first, "radius_m": [0.0, 0.11, 0.2, 0.3]})


def test_contact_line_is_interpolated_once_on_cumulative_substrate_arclength():
    samples = [
        {"arclength_m": s, "r_m": s, "z_m": 0.0, "phi": phi}
        for s, phi in zip([0.0, 0.1, 0.2, 0.3, 0.4], [-1.0, -0.7, -0.1, 0.5, 1.0])
    ]
    result = extract_substrate_contact_line(samples, maximum_sample_spacing_m=0.1)
    assert result["arclength_m"] == pytest.approx(0.21666666666666667)
    assert result["bracket_indices"] == [2, 3]
    assert result["maximum_observed_spacing_m"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    "phis, message",
    [
        ([-1.0, 0.5, -0.5, 0.5, 1.0], "reversed phase transition|exactly one"),
        ([1.0, 1.0, 1.0, 1.0, 1.0], "start wet/liquid-negative"),
        ([-1.0, -1.0, -1.0, -1.0, -1.0], "end dry/gas-positive"),
        ([-1.0, -0.5, 0.0, 0.0, 1.0], "zero plateau"),
    ],
)
def test_contact_line_rejects_absent_reversed_or_multiple_crossings(phis, message):
    samples = [
        {"arclength_m": s, "r_m": s, "z_m": 0.0, "phi": phi}
        for s, phi in zip([0.0, 0.1, 0.2, 0.3, 0.4], phis)
    ]
    with pytest.raises(ShapeMetricsError, match=message):
        extract_substrate_contact_line(samples, maximum_sample_spacing_m=0.1)


def test_contact_line_requires_wall_resolution_and_monotone_arclength():
    samples = [
        {"arclength_m": s, "r_m": s, "z_m": 0.0, "phi": phi}
        for s, phi in zip([0.0, 0.1, 0.3, 0.4, 0.5], [-1.0, -0.7, -0.1, 0.5, 1.0])
    ]
    with pytest.raises(ShapeMetricsError, match="spacing"):
        extract_substrate_contact_line(samples, maximum_sample_spacing_m=0.1)
    samples[2]["arclength_m"] = 0.05
    with pytest.raises(ShapeMetricsError, match="strictly increasing"):
        extract_substrate_contact_line(samples, maximum_sample_spacing_m=0.5)


def test_requested_time_vector_must_be_present_without_interpolation():
    requested = [0.0, 0.5, 1.0]
    result = validate_stored_time_grid([0.0, 0.25, 0.5, 0.75, 1.0], requested)
    assert result["status"] == "EXACT_REQUESTED_TIMES_PRESENT"
    assert result["additional_stored_time_count"] == 2
    assert result["interpolation_used"] is False
    with pytest.raises(ShapeMetricsError, match="absent from stored"):
        validate_stored_time_grid([0.0, 0.25, 0.75, 1.0], requested)
    with pytest.raises(ShapeMetricsError, match="strictly increasing"):
        validate_stored_time_grid([0.0, 0.5, 0.5, 1.0], requested)


def _history_sample(time_s, height, contact, volume, speed):
    return {
        "time_s": time_s,
        "phase1_volume_m3": volume,
        "contact_line_arclength_m": contact,
        "maximum_speed_m_s": speed,
        "upper_profile": _profile([0.0, 0.1, 0.2, 0.3], [height, height, height, 0.0], 3),
    }


def _passing_history():
    return [
        _history_sample(0, 0.1, 0.2, 0.5, 0.0),
        _history_sample(16, 0.1, 0.2, 0.5, 1e-5),
        _history_sample(17, 0.101, 0.2, 0.5, 1e-5),
        _history_sample(18, 0.102, 0.2, 0.5, 1e-5),
        _history_sample(19, 0.103, 0.2, 0.5, 1e-5),
        _history_sample(20, 0.104, 0.2, 0.5, 1e-5),
    ]


def _evaluate_history(samples):
    from tools.w24_static_shape_metrics import evaluate_shape_history

    return evaluate_shape_history(
        samples,
        capillary_time_s=1.0,
        analytic_initial_volume_m3=0.5,
        epsilon_m=0.01,
        surface_tension_n_per_m=0.03,
        glue_viscosity_pa_s=1.0,
    )


def test_full_history_and_stable_window_gate_pass_with_separate_metrics():
    result = _evaluate_history(_passing_history())
    assert result["status"] == "STABLE_WINDOW_PASS"
    assert result["stable_window_s"] == [16.0, 20.0]
    assert result["maximum_all_history_volume_drift"] == 0.0
    assert result["maximum_stable_speed_limit_m_s"] == pytest.approx(3e-5)
    assert result["contact_line_gate_separate_from_profile_support"] is True
    assert result["volume_gate_separate_from_profile_support"] is True


@pytest.mark.parametrize(
    "mutate, expected_error",
    [
        (lambda rows: rows[-1].update(phase1_volume_m3=0.5008), "volume_drift"),
        (lambda rows: rows[2].update(contact_line_arclength_m=0.21), "contact_line_motion"),
        (lambda rows: rows[3].update(maximum_speed_m_s=4e-5), "maximum_speed"),
        (lambda rows: rows[2].update(upper_profile=_profile([0, .1, .2, .3], [.1, .1, .1, 0], 2)), "common wet support"),
    ],
)
def test_stable_window_fails_independent_volume_contact_speed_and_support_gates(mutate, expected_error):
    rows = _passing_history()
    mutate(rows)
    result = _evaluate_history(rows)
    assert result["status"] == "STABLE_WINDOW_FAIL"
    assert any(expected_error in error for error in result["errors"])


def test_stable_window_must_cover_exact_start_time():
    rows = [_history_sample(0, .1, .2, .5, 0),
            _history_sample(15, .1, .2, .5, 0),
            _history_sample(17, .1, .2, .5, 0),
            _history_sample(20, .1, .2, .5, 0)]
    result = _evaluate_history(rows)
    assert result["status"] == "STABLE_WINDOW_FAIL"
    assert "stable_window_start_time_missing_or_ambiguous" in result["errors"]


def test_sensitivity_comparison_gates_height_contact_line_and_volume_separately():
    from tools.w24_static_shape_metrics import compare_converged_shape_variants

    baseline = {"status": "STABLE_WINDOW_PASS",
                "final_profile": _profile([0, .1, .2, .3], [.000100, .000120, .000100, 0], 3),
                "final_contact_line_arclength_m": .0002, "final_volume_m3": .5}
    variant = {"status": "STABLE_WINDOW_PASS",
               "final_profile": _profile([0, .1, .2, .3], [.000105, .000119, .000101, 0], 3),
               "final_contact_line_arclength_m": .000204, "final_volume_m3": .5001}
    result = compare_converged_shape_variants(baseline, variant,
                                              drop_radius_m=.0005, epsilon_m=.000008)
    assert result["status"] == "PASS"
    assert result["contact_line_gate_separate"] is True
    assert result["volume_gate_separate"] is True

    variant["final_contact_line_arclength_m"] = .000205
    variant["final_volume_m3"] = .5006
    result = compare_converged_shape_variants(baseline, variant,
                                              drop_radius_m=.0005, epsilon_m=.000008)
    assert result["status"] == "FAIL"
    assert "final_contact_line_difference_exceeds_limit" in result["errors"]
    assert "final_volume_difference_exceeds_limit" in result["errors"]
