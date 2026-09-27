import copy
import math

import pytest

from tools.w24_science_acceptance import (
    AcceptanceError,
    analytic_alpha_iso,
    compare_full_dof_frames,
    dof_value_map,
    heat_balance_residuals,
    normalized_l2_error,
    validate_stored_times,
    validate_native_cure_metrics,
)


def _frame(time_s, values, *, vector_order=None):
    names = ["T", "alpha", "qpost", "ur", "uz"]
    n = len(names)
    vector_order = vector_order or list(range(n))
    return {
        "time_s": time_s,
        "dofs": {
            "geomNums": [1] * n,
            "nodes": [10, 11, 12, 13, 14],
            "coords": [[0.0, 0.1, 0.2, 0.3, 0.4], [0.0] * n],
            "dofNames": names,
            "nameInds": list(range(n)),
            "solVectorInds": vector_order,
        },
        "u_real": [values[i] for i in range(n)],
        "u_imag": [0.0] * n,
    }


def _reorder_rows(frame, order):
    out = copy.deepcopy(frame)
    meta = out["dofs"]
    for key in ("geomNums", "nodes", "nameInds", "solVectorInds"):
        meta[key] = [meta[key][i] for i in order]
    meta["coords"] = [[axis[i] for i in order] for axis in meta["coords"]]
    return out


def test_isothermal_reference_reproduces_frozen_47_second_checkpoint():
    actual = analytic_alpha_iso(
        [0, 10, 30, 47, 60, 120, 240, 960, 1500],
        alpha0=0.20,
        t0_k=298.15,
        pre_exponential_s=1e5,
        activation_j_mol=55e3,
        gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2,
        uv_duration_s=120,
    )
    assert actual[3] == pytest.approx(0.5005417487002959, abs=1e-12)


def test_requested_output_must_exist_exactly_and_step_gap_is_bounded():
    valid = list(range(121))
    validate_stored_times(valid, [0, 47, 120], max_step_s=1)
    missing_47 = [time for time in valid if time != 47]
    with pytest.raises(AcceptanceError, match="47.*0 stored matches"):
        validate_stored_times(missing_47, [0, 47, 120], max_step_s=1)
    with pytest.raises(AcceptanceError, match="gap"):
        validate_stored_times([0, 2, 3], [0, 2, 3], max_step_s=1)
    with pytest.raises(AcceptanceError, match="requested output times must be strictly increasing"):
        validate_stored_times([0, 1], [0, 0], max_step_s=1)


def test_dof_map_uses_documented_zero_based_solution_indices_and_exact_keys():
    normal = _frame(0, [300, 0.3, 0.1, 1e-8, 2e-8])
    reordered = _frame(0, [300, 0.3, 0.1, 1e-8, 2e-8], vector_order=[4, 3, 2, 1, 0])
    # Reorder the actual solution vector to match the metadata indices.
    reordered["u_real"] = [2e-8, 1e-8, 0.1, 0.3, 300]
    assert dof_value_map(normal) == dof_value_map(reordered)
    broken = _frame(0, [300, 0.3, 0.1, 1e-8, 2e-8])
    broken["dofs"]["solVectorInds"][0] = 5
    with pytest.raises(AcceptanceError, match="zero-based solution index"):
        dof_value_map(broken)


def test_full_field_comparison_uses_exact_matching_keys_and_frozen_floors():
    ref = _frame(120, [300, 0.80, 0.20, 1e-8, 2e-8])
    candidate = _frame(120, [300.1, 0.80001, 0.20002, 1.01e-8, 1.99e-8])
    names = {
        "temperature": ["T"], "alpha": ["alpha"], "qpost": ["qpost"],
        "displacement_r": ["ur"], "displacement_z": ["uz"],
    }
    result = compare_full_dof_frames(candidate, ref, names, t0_k=298.15)
    assert result["temperature_offset_l2"] == pytest.approx(0.1 / 1.85)
    assert result["alpha_max_abs"] == pytest.approx(1e-5)
    assert result["qpost_max_abs"] == pytest.approx(2e-5)
    displacement_reference_norm = math.sqrt((1e-8) ** 2 + (2e-8) ** 2)
    assert result["displacement_l2"] == pytest.approx(math.sqrt(2) * 1e-10 / displacement_reference_norm)

    reordered = _reorder_rows(candidate, [4, 2, 0, 3, 1])
    reordered_result = compare_full_dof_frames(reordered, ref, names, t0_k=298.15)
    assert reordered_result == pytest.approx(result)

    candidate["dofs"]["nodes"][0] = 999
    with pytest.raises(AcceptanceError, match="key sets differ"):
        compare_full_dof_frames(candidate, ref, names, t0_k=298.15)


def test_l2_normalization_uses_floor_times_sqrt_n():
    assert normalized_l2_error([1, 1], [0, 0], floor=1) == pytest.approx(1)


def test_independent_reference_rejects_invalid_initial_state_and_uv_inputs():
    common = dict(t0_k=298.15, pre_exponential_s=1e5, activation_j_mol=55e3,
                  gas_constant_j_mol_k=8.31446261815324, uv_rate_s=1e-2,
                  uv_duration_s=120)
    with pytest.raises(AcceptanceError, match="alpha0 must be in"):
        analytic_alpha_iso([0], alpha0=1.1, **common)
    with pytest.raises(AcceptanceError, match="UV rate and duration"):
        analytic_alpha_iso([0], alpha0=0.2, **{**common, "uv_rate_s": -1})


def test_energy_balance_uses_signed_outward_flux_and_trapezoidal_time_integral():
    samples = [
        {"time_s": 0, "stored_energy_j": 0, "reaction_power_w": 50,
         "outward_power_w": 10, "outward_sign": "positive_outward"},
        {"time_s": 1, "stored_energy_j": 40, "reaction_power_w": 50,
         "outward_power_w": 10, "outward_sign": "positive_outward"},
    ]
    result = heat_balance_residuals(samples, max_gap_s=1)
    assert result[-1]["reaction_energy_j"] == pytest.approx(50)
    assert result[-1]["outward_energy_j"] == pytest.approx(10)
    assert result[-1]["signed_residual_j"] == pytest.approx(0)
    samples[1]["outward_sign"] = "inward_positive"
    with pytest.raises(AcceptanceError, match="positive outward"):
        heat_balance_residuals(samples, max_gap_s=1)
    samples[0]["outward_sign"] = "positive_outward"
    samples[1]["outward_sign"] = "positive_outward"
    samples[0]["time_s"] = 1
    samples[1]["time_s"] = 2
    with pytest.raises(AcceptanceError, match="begin at t=0"):
        heat_balance_residuals(samples, max_gap_s=1)


def _full_native_cure_metrics():
    times = [float(value) for value in range(1501)]
    iso = analytic_alpha_iso(
        times, alpha0=0.20, t0_k=298.15, pre_exponential_s=1e5,
        activation_j_mol=55e3, gas_constant_j_mol_k=8.31446261815324,
        uv_rate_s=1e-2, uv_duration_s=120,
    )
    return {
        "stored_times_s": times,
        "alpha_iso_mean": iso,
        "alpha_mean": [0.20 + 0.76 * time / 1500 for time in times],
        "qpost_mean": [0.95 * time / 1500 for time in times],
        "energy_samples": [
            {"time_s": time, "stored_energy_j": 0.0, "reaction_power_w": 0.0,
             "outward_power_w": 0.0, "outward_sign": "positive_outward"}
            for time in times
        ],
    }


def test_native_cure_metrics_require_full_matching_energy_time_history():
    metrics = _full_native_cure_metrics()
    result = validate_native_cure_metrics(metrics, max_step_s=1.0)
    assert result["stored_times_count"] == 1501
    assert result["energy_sample_count"] == 1501
    assert result["max_energy_relative_residual"] == 0

    truncated = _full_native_cure_metrics()
    truncated["energy_samples"] = truncated["energy_samples"][:2]
    with pytest.raises(AcceptanceError, match="cover every stored accepted solution time"):
        validate_native_cure_metrics(truncated, max_step_s=1.0)

    missing_time = _full_native_cure_metrics()
    missing_time["energy_samples"].pop(1)
    with pytest.raises(AcceptanceError, match="cover every stored accepted solution time"):
        validate_native_cure_metrics(missing_time, max_step_s=1.0)

    interpolated_time = _full_native_cure_metrics()
    interpolated_time["energy_samples"][1]["time_s"] = 0.5
    with pytest.raises(AcceptanceError, match="do not match the complete stored-time vector"):
        validate_native_cure_metrics(interpolated_time, max_step_s=1.0)


def test_native_cure_metrics_reject_short_or_nonmonotone_full_state_series():
    short = _full_native_cure_metrics()
    short["stored_times_s"] = short["stored_times_s"][:-1]
    short["alpha_iso_mean"] = short["alpha_iso_mean"][:-1]
    short["alpha_mean"] = short["alpha_mean"][:-1]
    short["qpost_mean"] = short["qpost_mean"][:-1]
    short["energy_samples"] = short["energy_samples"][:-1]
    with pytest.raises(AcceptanceError, match="cover the exact 0..1500 s interval"):
        validate_native_cure_metrics(short, max_step_s=1.0)

    nonmonotone = _full_native_cure_metrics()
    nonmonotone["alpha_mean"][100] = nonmonotone["alpha_mean"][99] - 0.01
    with pytest.raises(AcceptanceError, match="alpha_mean is not monotone"):
        validate_native_cure_metrics(nonmonotone, max_step_s=1.0)
