from __future__ import annotations

import cmath
import math

import pytest

from tools import w23_te_science_protocol as protocol


def _event(rpc_id: str, phase: str, status: str | None = None):
    row = {"rpc_id": rpc_id, "phase": phase}
    if status is not None:
        row["status"] = status
    return row


def _raw(plane: str, x: float, normal_x: float, *, phase: complex = 1.0,
         rotate_mode: complex = 1.0, sample_count: int = protocol.SAMPLE_COUNT,
         normal_sign: int | None = None):
    n = sample_count
    if normal_sign is None:
        normal_sign = 1 if plane == "output_x8" else -1
    y = [-8e-6 + 16e-6 * index / (n - 1) for index in range(n)]
    coordinates = [[x] * n, y]
    names = [
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Hx", "ewfd.Hy", "ewfd.Hz",
        "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2",
        "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2",
        "ewfd.Emodex_1", "ewfd.Emodey_1", "ewfd.Emodez_1",
        "ewfd.Hmodex_1", "ewfd.Hmodey_1", "ewfd.Hmodez_1", "nx", "ny",
    ]
    values: dict[str, list[complex]] = {name: [0j] * n for name in names}
    if plane == "output_x8":
        values["ewfd.Ez"] = [phase * (2 + 1j)] * n
        values["ewfd.Hy"] = [phase * (-1 + 0.5j)] * n
        values["ewfd.Emodez_2"] = [rotate_mode * (1 - 0.25j)] * n
        values["ewfd.Hmodey_2"] = [rotate_mode * (-0.5 - 0.75j)] * n
    else:
        values["ewfd.Emodez_1"] = [phase * (1 + 0.5j)] * n
        values["ewfd.Hmodey_1"] = [phase * (-0.75 + 0.25j)] * n
    values["nx"] = [complex(normal_x)] * n
    values["ny"] = [0j] * n
    expressions = [{"expression": name,
                    "real": [value.real for value in values[name]],
                    "imag": [value.imag for value in values[name]]}
                   for name in names]
    return {"status": "RAW_NATIVE_COMPLEX_FIELDS_READ", "plane": plane,
            "sample_count": n, "complex_readback": True, "normal_sign": normal_sign,
            "coordinates_m": coordinates, "expressions": expressions}


def _native_from_quadrature(independent):
    return {
        "status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "integrals": {
            "signal_power": {"real": independent["signal_power"], "imag": 0, "unit": "W/m"},
            "reference_mode_power": {"real": independent["reference_mode_power"], "imag": 0, "unit": "W/m"},
            "incident_reference_power": {"real": independent["incident_reference_power"], "imag": 0, "unit": "W/m"},
            "reciprocal_overlap_numerator": {**independent["reciprocal_overlap_numerator"], "unit": "W/m"},
        },
    }


def test_direct_rpc_gate_requires_every_direct_worker_request_terminal():
    passed = protocol.reconcile_direct_rpc_events([
        _event("rpc-1", "submitted"), _event("rpc-1", "observed", "FAILED"),
        _event("rpc-2", "submitted"), _event("rpc-2", "observed", "SUCCEEDED"),
    ])
    assert passed["safe_for_owned_cleanup"] is True
    assert passed["rpc_count"] == 2

    for events in (
        [_event("rpc-1", "submitted")],
        [_event("rpc-1", "submitted"), _event("rpc-1", "unknown", "UNKNOWN")],
        [_event("rpc-1", "observed", "SUCCEEDED")],
        [_event("rpc-1", "submitted"), _event("rpc-1", "observed", "RUNNING")],
        [_event("rpc-1", "submitted"), _event("rpc-1", "observed", "FAILED"),
         _event("rpc-1", "observed", "FAILED")],
    ):
        assert protocol.reconcile_direct_rpc_events(events)["safe_for_owned_cleanup"] is False


def test_cleanup_gate_requires_managed_and_direct_ledgers_and_preserves_unknown():
    direct = {"safe_for_owned_cleanup": True}
    managed_unknown = {"safe_for_owned_cleanup": True,
                       "durable_result": {"domain_outcome": "UNKNOWN", "domain_state_was_not_rewritten": True}}
    combined = protocol.combine_cleanup_gates(managed_unknown, direct)
    assert combined["safe_for_owned_cleanup"] is True
    assert combined["managed_domain_unknown_preserved"]["domain_outcome"] == "UNKNOWN"

    assert not protocol.combine_cleanup_gates(
        {"safe_for_owned_cleanup": False}, direct)["safe_for_owned_cleanup"]
    assert not protocol.combine_cleanup_gates(
        managed_unknown, {"safe_for_owned_cleanup": False})["safe_for_owned_cleanup"]


def test_simpson_and_native_integral_reconciliation_use_registered_metre_grid():
    coordinates = [-8e-6 + 16e-6 * index / 320 for index in range(321)]
    integral = protocol.simpson_integral([1.0] * 321, coordinates)
    assert integral.real == pytest.approx(16e-6, rel=1e-12)
    assert integral.imag == 0

    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    independent = protocol.independent_integrals(output, incident)
    assert independent["unit"] == "W/m"
    assert independent["output_native_normal_x"] == 1
    assert independent["output_normal_sign"] == 1
    assert independent["incident_native_normal_x"] == -1
    assert independent["incident_normal_sign"] == -1
    # Hand-calculated from constant TE phasors over the 16 um line. These
    # fixed expected values catch suffix/conjugation/normal mistakes even if
    # the synthetic native receipt is generated from the same quadrature.
    assert independent["signal_power"] == pytest.approx(12e-6, rel=1e-12)
    assert independent["reference_mode_power"] == pytest.approx(2.5e-6, rel=1e-12)
    assert independent["incident_reference_power"] == pytest.approx(5e-6, rel=1e-12)
    assert independent["reciprocal_overlap_numerator"]["real"] == pytest.approx(46e-6, rel=1e-12)
    assert independent["reciprocal_overlap_numerator"]["imag"] == pytest.approx(-20e-6, rel=1e-12)
    fixed_native = {
        "status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "integrals": {
            "signal_power": {"real": 12e-6, "imag": 0, "unit": "W/m"},
            "reference_mode_power": {"real": 2.5e-6, "imag": 0, "unit": "W/m"},
            "incident_reference_power": {"real": 5e-6, "imag": 0, "unit": "W/m"},
            "reciprocal_overlap_numerator": {"real": 46e-6, "imag": -20e-6, "unit": "W/m"},
        },
    }
    receipt = protocol.compare_native_integrals(fixed_native, independent)
    assert receipt["status"] == "PASS_NATIVE_VS_INDEPENDENT_QUADRATURE"
    assert max(receipt["relative_errors"].values()) < 1e-12


def test_core_capture_aperture_flux_has_independent_hand_computed_reference():
    output = _raw("output_x8", 8e-6, 1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    result = protocol.independent_capture_aperture_flux(output)
    assert result["status"] == "SOFTWARE_RECOMPUTED_NATIVE_CAPTURE_FLUX"
    assert result["aperture_id"] == "receiver_core_aperture"
    assert result["selection_tag"] == "selCoreCaptureX8"
    assert result["sample_count"] == 41
    # Ez=(2+i), Hy=(-1+0.5i), so Sx=0.5*Re(-Ez*conj(Hy))=0.75 W/m^2.
    # The separately registered core aperture is 1 um tall.
    assert result["value"] == pytest.approx(0.75e-6, rel=1e-12)
    assert result["unit"] == "W/m"


def test_native_capture_quadrature_comparison_binds_region_denominator_and_sign():
    output = _raw("output_x8", 8e-6, 1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    incident = _raw("input_xminus10", -10e-6, -1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    independent = protocol.independent_capture_aperture_flux(output)
    independent_integrals = protocol.independent_integrals(output, incident)
    native = {
        "status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "identity": {"incident_reference": {"input_plane_id": "input_xminus10"}},
        "normalization_power_floor": {"value": 1e-12, "unit": "W/m"},
        "integrals": {
            "incident_reference_power": {"real": 5e-6, "imag": 0.0, "unit": "W/m",
                                          "reference_id": "input_port_mode_1"},
            "capture_aperture_signal_flux": {"real": 0.75e-6, "imag": 0.0, "unit": "W/m"},
        },
        "eta_capture": {
            "status": "COMPUTED_NATIVE_APERTURE_FLUX",
            "value": 0.15,
            "region": {"aperture_id": "receiver_core_aperture", "plane_id": "output_x8",
                       "selection": {"selection_tag": "selCoreCaptureX8"}, "normal_sign": 1},
            "numerator": {"real": 0.75e-6, "imag": 0.0, "unit": "W/m"},
            "denominator": {"reference_id": "input_port_mode_1", "input_plane_id": "input_xminus10",
                            "value": 5e-6, "unit": "W/m"},
        },
    }
    receipt = protocol.compare_native_capture_flux(native, independent, independent_integrals)
    assert receipt["status"] == "PASS_NATIVE_CAPTURE_QUADRATURE_MATCH"
    assert receipt["relative_error"] == pytest.approx(0.0, abs=1e-12)
    assert receipt["sample_count"] == 41
    assert receipt["native_eta_capture"] == pytest.approx(0.15)
    assert receipt["independent_eta_capture"] == pytest.approx(0.15)
    assert receipt["eta_relative_error"] == pytest.approx(0.0, abs=1e-12)

    wrong = {**native, "eta_capture": {**native["eta_capture"],
              "region": {**native["eta_capture"]["region"], "normal_sign": -1}}}
    with pytest.raises(protocol.ScienceProtocolError, match="region identity"):
        protocol.compare_native_capture_flux(wrong, independent, independent_integrals)
    wrong_denominator = {**native, "eta_capture": {**native["eta_capture"],
                        "denominator": {"reference_id": "other", "input_plane_id": "input_xminus10",
                                        "value": 5e-6, "unit": "W/m"}}}
    with pytest.raises(protocol.ScienceProtocolError, match="incident reference"):
        protocol.compare_native_capture_flux(wrong_denominator, independent, independent_integrals)
    wrong_denominator_value = {**native, "eta_capture": {**native["eta_capture"],
                                  "denominator": {**native["eta_capture"]["denominator"], "value": 4e-6}}}
    with pytest.raises(protocol.ScienceProtocolError, match="denominator value"):
        protocol.compare_native_capture_flux(wrong_denominator_value, independent, independent_integrals)
    wrong_eta = {**native, "eta_capture": {**native["eta_capture"], "value": 0.5}}
    with pytest.raises(protocol.ScienceProtocolError, match="eta_capture"):
        protocol.compare_native_capture_flux(wrong_eta, independent, independent_integrals)
    wrong_incident_record = {**native, "integrals": {
        **native["integrals"],
        "incident_reference_power": {**native["integrals"]["incident_reference_power"], "real": 4e-6}}}
    with pytest.raises(protocol.ScienceProtocolError, match="denominator value"):
        protocol.compare_native_capture_flux(wrong_incident_record, independent, independent_integrals)


def test_native_capture_comparison_preserves_reverse_flux_and_negative_eta():
    output = _raw("output_x8", 8e-6, 1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    rows = [dict(row) for row in output["expressions"]]
    y = output["coordinates_m"][1]
    for row in rows:
        if row["expression"] == "ewfd.Hy":
            for index, coordinate in enumerate(y):
                if (protocol.CAPTURE_Y_MIN_M - 1e-12 <= coordinate
                        <= protocol.CAPTURE_Y_MAX_M + 1e-12):
                    row["real"][index], row["imag"][index] = 1.0, -0.5
    reverse_aperture = {**output, "expressions": rows}
    incident = _raw("input_xminus10", -10e-6, -1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    independent = protocol.independent_capture_aperture_flux(reverse_aperture)
    independent_integrals = protocol.independent_integrals(reverse_aperture, incident)
    assert independent["value"] == pytest.approx(-0.75e-6, rel=1e-12)
    assert independent_integrals["signal_power"] > 0  # The full-plane signal remains forward-power normalized.

    native = {
        "status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
        "identity": {"incident_reference": {"input_plane_id": "input_xminus10"}},
        "normalization_power_floor": {"value": 1e-12, "unit": "W/m"},
        "integrals": {
            "incident_reference_power": {"real": 5e-6, "imag": 0.0, "unit": "W/m",
                                          "reference_id": "input_port_mode_1"},
            "capture_aperture_signal_flux": {"real": -0.75e-6, "imag": 0.0, "unit": "W/m"},
        },
        "eta_capture": {
            "status": "COMPUTED_NATIVE_APERTURE_FLUX", "value": -0.15, "unit": "1",
            "region": {"aperture_id": "receiver_core_aperture", "plane_id": "output_x8",
                       "selection": {"selection_tag": "selCoreCaptureX8"}, "normal_sign": 1},
            "numerator": {"real": -0.75e-6, "imag": 0.0, "unit": "W/m"},
            "denominator": {"reference_id": "input_port_mode_1", "input_plane_id": "input_xminus10",
                            "value": 5e-6, "unit": "W/m"},
        },
    }
    receipt = protocol.compare_native_capture_flux(native, independent, independent_integrals)
    assert receipt["independent_eta_capture"] == pytest.approx(-0.15)
    assert receipt["native_eta_capture"] == pytest.approx(-0.15)


def test_capture_quadrature_refuses_unverified_aperture_grid_or_normal():
    output = _raw("output_x8", 8e-6, 1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    missing_bound = {**output, "coordinates_m": [output["coordinates_m"][0],
                     [value + 0.01e-6 for value in output["coordinates_m"][1]]]}
    with pytest.raises(protocol.ScienceProtocolError, match="frozen y span"):
        protocol.independent_capture_aperture_flux(missing_bound)
    reversed_normal = _raw("output_x8", 8e-6, -1, normal_sign=1,
                           sample_count=protocol.REFINED_SAMPLE_COUNT)
    with pytest.raises(protocol.ScienceProtocolError, match="physical \\+x"):
        protocol.independent_capture_aperture_flux(reversed_normal)


def test_normal_sign_and_native_normal_flip_preserve_physical_forward_power():
    output_a = _raw("output_x8", 8e-6, 1, normal_sign=1)
    input_a = _raw("input_xminus10", -10e-6, -1, normal_sign=-1)
    output_b = _raw("output_x8", 8e-6, -1, normal_sign=-1)
    input_b = _raw("input_xminus10", -10e-6, 1, normal_sign=1)
    positive_x = protocol.independent_integrals(output_a, input_a)
    flipped_geometry_normal = protocol.independent_integrals(output_b, input_b)
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        assert flipped_geometry_normal[key] == pytest.approx(positive_x[key], rel=1e-12)
    assert flipped_geometry_normal["reciprocal_overlap_numerator"] == pytest.approx(
        positive_x["reciprocal_overlap_numerator"], rel=1e-12)


def test_reference_mode_complex_phase_rotates_cross_term_conjugately():
    baseline = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1), _raw("input_xminus10", -10e-6, -1))
    reference_rotated = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, rotate_mode=1j), _raw("input_xminus10", -10e-6, -1))
    assert reference_rotated["reference_mode_power"] == pytest.approx(baseline["reference_mode_power"], rel=1e-12)
    z0 = complex(**baseline["reciprocal_overlap_numerator"])
    z1 = complex(**reference_rotated["reciprocal_overlap_numerator"])
    assert z1 / z0 == pytest.approx(-1j, rel=1e-12)


def test_software_signal_global_phase_control_rotates_cross_integral_only():
    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    receipt = protocol.software_global_phase_control(output, incident)
    assert receipt["status"] == "PASS_SOFTWARE_SIGNAL_GLOBAL_PHASE_CONTROL"
    assert receipt["scope"].startswith("software-only")
    assert receipt["native_input_phase_solve_still_required"] is True
    assert complex(**receipt["observed_cross_integral_ratio"]) == pytest.approx(1j, rel=1e-12)
    assert max(receipt["invariant_relative_errors"].values()) < 1e-12


def test_321_to_641_quadrature_refinement_is_an_explicit_gate():
    coarse = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=321),
        _raw("input_xminus10", -10e-6, -1, sample_count=321))
    fine = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=641),
        _raw("input_xminus10", -10e-6, -1, sample_count=641))
    receipt = protocol.compare_quadrature_refinement(coarse, fine)
    assert receipt["status"] == "PASS_321_TO_641_QUADRATURE_REFINEMENT"
    assert receipt["coarse_points"] == 321 and receipt["fine_points"] == 641


def test_coordinate_and_boundary_normal_readback_fail_closed():
    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    output["coordinates_m"][0][0] = 8.0
    with pytest.raises(protocol.ScienceProtocolError, match="x plane"):
        protocol.independent_integrals(output, incident)

    output = _raw("output_x8", 8e-6, 1)
    expression = next(row for row in output["expressions"] if row["expression"] == "ny")
    expression["real"][12] = 1.0
    with pytest.raises(protocol.ScienceProtocolError, match="nonzero y component"):
        protocol.independent_integrals(output, incident)

    output = _raw("output_x8", 8e-6, 1.2)
    with pytest.raises(protocol.ScienceProtocolError, match="unit x normal"):
        protocol.independent_integrals(output, incident)

    output = _raw("output_x8", 8e-6, 1)
    output["expressions"].append(dict(output["expressions"][0]))
    with pytest.raises(protocol.ScienceProtocolError, match="duplicate expression"):
        protocol.independent_integrals(output, incident)


def test_native_phase_control_requires_input_only_rotation_and_fixed_output_mode():
    output0 = _raw("output_x8", 8e-6, 1, phase=1)
    output90 = _raw("output_x8", 8e-6, 1, phase=1j)
    incident0 = _raw("input_xminus10", -10e-6, -1, phase=1)
    incident90 = _raw("input_xminus10", -10e-6, -1, phase=1j)
    q0 = protocol.independent_integrals(output0, incident0)
    q90 = protocol.independent_integrals(output90, incident90)
    native0 = _native_from_quadrature(q0)
    native90 = _native_from_quadrature(q90)
    native0.update({"overlap_amplitude": {"real": 1.0, "imag": 0.0},
                    "normalized_overlap": 0.75, "eta_mode": 0.7})
    native90.update({"overlap_amplitude": {"real": 0.0, "imag": 1.0},
                     "normalized_overlap": 0.75, "eta_mode": 0.7})
    case0 = {"phase_value": "0[deg]", "output_port_phase": "0[deg]",
             "output_fields": output0, "native_overlap": native0}
    case90 = {"phase_value": "90[deg]", "output_port_phase": "0[deg]",
              "output_fields": output90, "native_overlap": native90}
    assert protocol.verify_native_phase_pair(case0, case90)["status"] == "PASS_NATIVE_INPUT_PHASE_CONTROL"

    # If the output reference rotates with the signal, the phase comparison
    # is unidentifiable and must not be counted as a native phase pass.
    case90_both_rotated = {**case90, "output_fields": _raw("output_x8", 8e-6, 1,
                                                            phase=1j, rotate_mode=1j)}
    with pytest.raises(protocol.ScienceProtocolError, match="output reference mode"):
        protocol.verify_native_phase_pair(case0, case90_both_rotated)

    mismatched_grid = {**case90, "output_fields": _raw("output_x8", 8e-6, 1,
                                                        phase=1j, sample_count=641)}
    with pytest.raises(protocol.ScienceProtocolError, match="different quadrature sample counts"):
        protocol.verify_native_phase_pair(case0, mismatched_grid)

    shifted_grid_raw = _raw("output_x8", 8e-6, 1, phase=1j)
    shifted_grid_raw["coordinates_m"][1][80] += 1e-9
    shifted_grid = {**case90, "output_fields": shifted_grid_raw}
    with pytest.raises(protocol.ScienceProtocolError, match="same coordinates"):
        protocol.verify_native_phase_pair(case0, shifted_grid)

    changed_power = {**case90, "native_overlap": {**native90, "integrals": {
        **native90["integrals"], "signal_power": {"real": q90["signal_power"] * 1.1,
                                                  "imag": 0, "unit": "W/m"}}}}
    with pytest.raises(protocol.ScienceProtocolError, match="altered signal_power"):
        protocol.verify_native_phase_pair(case0, changed_power)


def test_software_negative_controls_run_and_are_never_native_evidence():
    receipt = protocol.run_negative_controls()
    assert receipt["status"] == "PASS_SOFTWARE_NEGATIVE_CONTROLS"
    assert receipt["scope"] == "schema-only; no native dispatch"
    assert all(row["passed"] for row in receipt["controls"])
