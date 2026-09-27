from __future__ import annotations

import copy
import math

import pytest

from tools.w23_mode_basis_v2 import (
    BASIS_POLICY,
    BASIS_SCHEMA_ID,
    TwoModeBasisError,
    build_two_mode_basis_field_contracts,
    build_two_mode_basis_request,
    change_basis_coordinates,
    compare_two_mode_native_integrals_to_field_reference,
    compute_two_mode_basis_projection,
    independent_two_mode_basis_from_native_field_readbacks,
    independent_two_mode_basis_from_samples,
    validate_two_mode_native_integral_response,
)


MODEL_REF = {
    "schema_version": 1, "session_id": "session-1", "server_instance_id": "epoch-1",
    "model_tag": "model1", "generation": 4,
}
CASE = {"case_id": "case-baseline", "case_identity_sha256": "a" * 64}
FREQUENCY = 193.414489032258e12
COORDINATE_FRAME = "geom3d-global-xyz-revision-12"


def _source(*, label: str, port: str, mode_index: int | None = None, **changes):
    source = {
        "dataset_id": f"d-{label}", "solution_id": f"sol-{label}",
        "outer_index": 1, "inner_index": 1, "solnum": 1,
        "port_id": port, "frequency_hz": FREQUENCY,
        "project_id": "project-w23", "model_ref": dict(MODEL_REF),
        "model_revision": 12, "geometry_revision": 12,
        "coordinate_frame": COORDINATE_FRAME,
    }
    if mode_index is not None:
        source["mode_index"] = mode_index
    source.update(changes)
    return source


def _surface(plane_id: str, port_id: str, center_x_um: float, *, area_m2: float,
             normal_sign: int, **changes):
    result = {
        "plane_id": plane_id, "port_id": port_id, "component": "comp3d",
        "geometry": "geom3d", "selection_tag": "selOutput" if port_id == "2" else "selInput",
        "entity_dimension": 2, "frame_id": COORDINATE_FRAME,
        "coordinate_unit": "um", "measure_unit": "m^2",
        "center_xyz_um": [center_x_um, 0.0, 0.0], "axis_xyz": [1.0, 0.0, 0.0],
        "native_normal_sign": normal_sign,
        "aperture_id": "output-aperture" if port_id == "2" else "input-aperture",
        "aperture_shape": "circular" if port_id == "2" else "rectangle",
        "surface_area_m2": area_m2,
    }
    if port_id == "2":
        result["sample_radius_um"] = 2.5
    else:
        result["half_widths_uv_um"] = [8.0, 8.0]
    result.update(changes)
    return result


def _request(**changes):
    output_area = math.pi * (2.5e-6) ** 2
    input_area = 256e-12
    args = {
        "basis_id": "fiber-he11-subspace", "case": dict(CASE),
        "project_id": "project-w23", "model_ref": dict(MODEL_REF),
        "model_revision": 12, "geometry_revision": 12,
        "frequency_hz": FREQUENCY, "coordinate_frame": COORDINATE_FRAME,
        "output_surface": _surface("receiver_port", "2", 20.0, area_m2=output_area, normal_sign=1),
        "input_surface": _surface("input_port", "1", -20.0, area_m2=input_area, normal_sign=-1),
        "signal_source": _source(label="signal", port="2"),
        "mode_sources": [
            {"mode_id": "bma-mode-solution-a", "mode_index": 1,
             "source": _source(label="mode-a", port="2", mode_index=1)},
            {"mode_id": "bma-mode-solution-b", "mode_index": 2,
             "source": _source(label="mode-b", port="2", mode_index=2)},
        ],
        "incident_source": _source(label="incident", port="1"),
        "applicability": {"reciprocal": True, "lossless": True,
                          "forward_propagating": True, "non_evanescent": True,
                          "non_leaky": True},
    }
    args.update(changes)
    return build_two_mode_basis_request(**args)


def _field(electric, magnetic):
    return {"E": [tuple(complex(v) for v in electric)],
            "H": [tuple(complex(v) for v in magnetic)]}


def _scale(field, scale):
    return {key: [tuple(scale * value for value in vector) for vector in field[key]]
            for key in ("E", "H")}


def _combine(first, second, a, b):
    return {key: [tuple(a * first[key][0][i] + b * second[key][0][i] for i in range(3))]
            for key in ("E", "H")}


def _raw_readback(contract, quadrature, fields):
    role = contract["role"]
    suffix = "" if role == "signal" else ("_1" if role == "incident_reference" else "_2")
    prefix = "" if role == "signal" else "mode"
    values = {}
    for expression in contract["field_names"]:
        if expression in {"nx", "ny", "nz"}:
            axis = {"nx": 0, "ny": 1, "nz": 2}[expression]
            value = contract["plane"]["native_normal_sign"] * contract["plane"]["axis_xyz"][axis]
            samples = [complex(value, 0.0)] * len(quadrature["coordinates_m"])
        else:
            component = "E" if ".E" in expression else "H"
            axis_name = expression[-1] if not suffix else expression[-len(suffix)-1]
            axis = {"x": 0, "y": 1, "z": 2}[axis_name]
            samples = [fields[component][0][axis]] * len(quadrature["coordinates_m"])
        values[expression] = {"expression": expression,
                              "real": [v.real for v in samples],
                              "imag": [v.imag for v in samples]}
    coordinates = [[point[axis] for point in quadrature["coordinates_m"]] for axis in range(3)]
    return {
        "contract_id": contract["contract_id"],
        "quadrature_sha256": contract["quadrature_sha256"],
        "source": copy.deepcopy(contract["source"]),
        "plane": copy.deepcopy(contract["plane"]),
        "native_result": "COMSOL_NATIVE_RAW", "study_or_solver_invoked": False,
        "complex_readback": True, "cleanup": {"created": True, "removed": True,
                                                  "cleanup_failed": False},
        "synthetic_test_fixture_only": True,
        "sample_count": len(coordinates[0]), "coordinates_m": coordinates,
        "expressions": list(values.values()),
    }


def _synthetic_field_contract_bundle():
    request = _request()
    contracts = build_two_mode_basis_field_contracts(request)
    signal, modes, incident, _, _ = _analytic_fields()
    output_q = contracts["quadratures"]["output"]
    input_q = contracts["quadratures"]["input"]
    raws = {
        "signal": _raw_readback(contracts["output"]["signal"], output_q, signal),
        "modes": [_raw_readback(contract, output_q, mode)
                  for contract, mode in zip(contracts["output"]["modes"], modes)],
        "incident": _raw_readback(contracts["incident"], input_q, incident),
    }
    return request, contracts, raws


def _synthetic_integral_response(request, independent):
    gram = independent["gram_raw"]
    coupling = independent["coupling"]
    binding_keys = (
        "schema_id", "schema_version", "profile", "algorithm_id", "basis_id", "case",
        "project_id", "model_ref", "model_revision", "geometry_revision", "frequency_hz",
        "coordinate_frame", "power_unit", "field_units", "phasor_convention",
        "output_surface", "input_surface", "signal_source", "basis_modes",
        "incident_source", "applicability", "policy", "quadratures",
        "native_verification", "native_result", "dispatchable", "production_route_status",
        "native_integral_plan",
    )
    return {
        "schema_id": request["schema_id"], "status": "SUCCEEDED",
        "result_status": "COMPUTED_NATIVE_BASIS_INTEGRALS",
        "origin": "COMSOL_NATIVE_INTEGRATION_FEATURES",
        "request_id": request["request_id"],
        "binding": {key: request[key] for key in binding_keys},
        "integrals": {
            "gram": {f"{i}{j}": {**gram[i][j], "unit": "W"} for i in range(2) for j in range(2)},
            "coupling": {str(i): {**coupling[i], "unit": "W"} for i in range(2)},
            "signal_power": {"real": independent["signal_power_w"], "imag": 0.0, "unit": "W"},
            "incident_power": {"real": independent["incident_power_w"], "imag": 0.0, "unit": "W"},
        },
    }


def _request_reference_identity(request):
    return {
        "request_id": request["request_id"], "basis_id": request["basis_id"],
        "project_id": request["project_id"], "model_ref": request["model_ref"],
        "model_revision": request["model_revision"], "geometry_revision": request["geometry_revision"],
        "frequency_hz": request["frequency_hz"], "coordinate_frame": request["coordinate_frame"],
        "case": request["case"], "output_aperture_id": request["output_surface"]["aperture_id"],
        "input_aperture_id": request["input_surface"]["aperture_id"],
        "mode_sources": [row["source"] for row in request["basis_modes"]],
        "signal_source": request["signal_source"], "incident_source": request["incident_source"],
    }


def _analytic_fields():
    # Unit-area +x flux orthogonal basis; each mode carries 0.5 W.
    mode_x = _field((0, 1, 0), (0, 0, 1))
    mode_y = _field((0, 0, 1), (0, -1, 0))
    alpha, beta = 2 + 1j, 0.5 - 2j
    signal = _combine(mode_x, mode_y, alpha, beta)
    incident = _scale(mode_x, math.sqrt(20.0))
    return signal, [mode_x, mode_y], incident, alpha, beta


def _project_from_analytic(signal=None, modes=None, incident=None, normals=None):
    default_signal, default_modes, default_incident, _, _ = _analytic_fields()
    return independent_two_mode_basis_from_samples(
        signal=signal or default_signal, modes=modes or default_modes,
        incident=incident or default_incident,
        output_normals_xyz=normals or [(1, 0, 0)], output_weights_m2=[1.0],
        input_normals_xyz=normals or [(1, 0, 0)], input_weights_m2=[1.0],
        source_identity={"case_id": CASE["case_id"], "basis_id": "fiber-he11-subspace"})


def test_v2_request_binds_two_native_modes_and_same_surface_integral_plan_without_dispatch():
    request = _request()
    assert request["schema_id"] == BASIS_SCHEMA_ID
    assert request["schema_version"] == "2.0.0"
    assert request["dispatchable"] is False
    assert request["production_route_status"] == "NOT_REGISTERED"
    assert request["native_integral_plan"]["origin_required"] == "COMSOL_NATIVE_INTEGRATION_FEATURES"
    assert len(request["native_integral_plan"]["terms"]) == 8
    assert {term["unit"] for term in request["native_integral_plan"]["terms"]} == {"W"}
    assert request["native_result"] == "NOT_RUN"
    assert request["study_or_solver_invoked"] is False
    for term in request["native_integral_plan"]["terms"][:4]:
        assert term["surface_id"] == request["output_surface"]["aperture_id"]
    assert request["basis_modes"][0]["source"]["mode_index"] == 1
    assert request["basis_modes"][1]["source"]["mode_index"] == 2


def test_v2_raw_field_contracts_keep_exact_two_mode_solution_identity_and_grid():
    request = _request()
    contracts = build_two_mode_basis_field_contracts(request)
    signal = contracts["output"]["signal"]
    mode_a, mode_b = contracts["output"]["modes"]
    incident = contracts["incident"]
    assert signal["quadrature_sha256"] == mode_a["quadrature_sha256"] == mode_b["quadrature_sha256"]
    assert mode_a["contract_id"] != mode_b["contract_id"]
    assert mode_a["identity"]["mode_index"] == 1
    assert mode_b["identity"]["mode_index"] == 2
    assert mode_a["source"]["solution_id"] != mode_b["source"]["solution_id"]
    assert incident["plane"]["plane_id"] == "input_port"
    assert signal["quadrature"]["sample_count"] == 2049
    assert contracts["native_result"] == "NOT_RUN"


def test_v2_independent_field_reference_revalidates_each_exact_native_source_and_grid():
    request, contracts, raws = _synthetic_field_contract_bundle()
    result = independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raws["signal"], mode_raws=raws["modes"],
        incident_raw=raws["incident"])
    assert result["status"] == "SOFTWARE_RECOMPUTED_TWO_MODE_BASIS_FROM_FIELD_SAMPLES"
    assert result["native_result"] == "NOT_RUN"
    assert all(row.get("synthetic_test_fixture_only") is True for row in raws["modes"])
    assert raws["signal"]["synthetic_test_fixture_only"] is True
    assert raws["incident"]["synthetic_test_fixture_only"] is True
    assert result["field_readback_validations"].keys() == {"signal", "mode_0", "mode_1", "incident"}
    output_area = math.fsum(contracts["quadratures"]["output"]["weights_m2"])
    input_area = math.fsum(contracts["quadratures"]["input"]["weights_m2"])
    assert result["projected_power_w_raw"] == pytest.approx(4.625 * output_area)
    assert result["incident_power_w"] == pytest.approx(10.0 * input_area)


def test_v2_integral_response_contract_and_independent_quadrature_comparison_stay_software_only():
    request, contracts, raws = _synthetic_field_contract_bundle()
    independent = independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raws["signal"], mode_raws=raws["modes"],
        incident_raw=raws["incident"])
    response = _synthetic_integral_response(request, independent)
    validated = validate_two_mode_native_integral_response(request, response)
    assert validated["status"] == "SOFTWARE_NATIVE_ENVELOPE_VALIDATION_ONLY"
    assert validated["route_authentication"] == "NOT_PROVIDED"
    comparison = compare_two_mode_native_integrals_to_field_reference(request, response, independent)
    assert comparison["status"] == "SOFTWARE_NATIVE_INTEGRAL_COMPARISON_VALIDATION_ONLY"
    assert comparison["native_result"] == "NOT_RUN"
    assert comparison["comparison_policy"] == BASIS_POLICY


def test_v2_integral_response_refuses_missing_imaginary_unit_or_source_binding():
    request, contracts, raws = _synthetic_field_contract_bundle()
    independent = independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raws["signal"], mode_raws=raws["modes"],
        incident_raw=raws["incident"])
    response = _synthetic_integral_response(request, independent)
    no_imaginary = copy.deepcopy(response)
    no_imaginary["integrals"]["coupling"]["0"].pop("imag")
    with pytest.raises(TwoModeBasisError, match="explicit imaginary component"):
        validate_two_mode_native_integral_response(request, no_imaginary)
    wrong_unit = copy.deepcopy(response)
    wrong_unit["integrals"]["gram"]["00"]["unit"] = "W/m"
    with pytest.raises(TwoModeBasisError, match="explicit imaginary component"):
        validate_two_mode_native_integral_response(request, wrong_unit)
    wrong_source = copy.deepcopy(response)
    wrong_source["binding"]["basis_modes"][0]["source"]["solution_id"] = "foreign"
    with pytest.raises(TwoModeBasisError, match="exact v2 project/model/source/surface"):
        validate_two_mode_native_integral_response(request, wrong_source)


def test_v2_integral_comparison_normalizes_huge_finite_terms_without_product_overflow():
    request = _request()
    independent = compute_two_mode_basis_projection(
        [[1e200, 1e199], [1e199, 1e200]], [1e100, 1e100],
        signal_power_w=1.0, incident_power_w=1.0,
        source_identity=_request_reference_identity(request))
    response = _synthetic_integral_response(request, independent)
    result = compare_two_mode_native_integrals_to_field_reference(request, response, independent)
    assert result["status"] == "SOFTWARE_NATIVE_INTEGRAL_COMPARISON_VALIDATION_ONLY"
    assert math.isfinite(result["native_projection"]["eta_basis_raw"])
    assert result["relative_errors"]["G[0,1]"] == pytest.approx(0.0)

    mismatched = copy.deepcopy(response)
    mismatched["integrals"]["coupling"]["0"]["real"] *= 1.002
    with pytest.raises(TwoModeBasisError, match=r"b\[0\] relative error"):
        compare_two_mode_native_integrals_to_field_reference(request, mismatched, independent)


def test_v2_integral_comparison_rejects_nonfinite_independent_reference_terms():
    request, contracts, raws = _synthetic_field_contract_bundle()
    independent = independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raws["signal"], mode_raws=raws["modes"],
        incident_raw=raws["incident"])
    response = _synthetic_integral_response(request, independent)
    independent["gram_raw"][1][1]["imag"] = float("nan")
    with pytest.raises(TwoModeBasisError, match="finite numeric data"):
        compare_two_mode_native_integrals_to_field_reference(request, response, independent)


def test_v2_zero_and_near_zero_native_coupling_use_frozen_normalized_floor():
    _, modes, incident, _, _ = _analytic_fields()
    independent = independent_two_mode_basis_from_samples(
        signal=modes[1], modes=modes, incident=incident,
        output_normals_xyz=[(1, 0, 0)], output_weights_m2=[1.0],
        input_normals_xyz=[(1, 0, 0)], input_weights_m2=[1.0],
        source_identity={"case_id": "orthogonal-control"})
    request = _request()
    independent["source_identity"] = _request_reference_identity(request)
    response = _synthetic_integral_response(request, independent)
    mode_power = independent["gram_raw"][0][0]["real"]
    signal_power = independent["signal_power_w"]
    scale = math.sqrt(mode_power * signal_power)
    absolute_floor = BASIS_POLICY["native_integral_absolute_normalized_tolerance"]
    response["integrals"]["coupling"]["0"] = {
        "real": scale * absolute_floor * 0.5, "imag": 0.0, "unit": "W"}
    accepted = compare_two_mode_native_integrals_to_field_reference(request, response, independent)
    assert accepted["normalized_absolute_errors"]["b[0]"] == pytest.approx(absolute_floor * 0.5)
    response["integrals"]["coupling"]["0"]["real"] = scale * absolute_floor * 1.01
    with pytest.raises(TwoModeBasisError, match=r"b\[0\] normalized absolute error"):
        compare_two_mode_native_integrals_to_field_reference(request, response, independent)


def test_v2_nonzero_native_coupling_keeps_strict_relative_threshold():
    request, contracts, raws = _synthetic_field_contract_bundle()
    independent = independent_two_mode_basis_from_native_field_readbacks(
        request, contracts, signal_raw=raws["signal"], mode_raws=raws["modes"],
        incident_raw=raws["incident"])
    response = _synthetic_integral_response(request, independent)
    response["integrals"]["coupling"]["1"]["real"] *= (
        1 + BASIS_POLICY["native_vs_quadrature_relative_tolerance"] * 1.01)
    response["integrals"]["coupling"]["1"]["imag"] *= (
        1 + BASIS_POLICY["native_vs_quadrature_relative_tolerance"] * 1.01)
    with pytest.raises(TwoModeBasisError, match=r"b\[1\] relative error"):
        compare_two_mode_native_integrals_to_field_reference(request, response, independent)


def test_v2_request_digest_binds_integral_plan_and_source_recipe():
    request = _request()
    tampered = copy.deepcopy(request)
    tampered["frequency_hz"] *= 1.01
    with pytest.raises(TwoModeBasisError, match="identity digest"):
        build_two_mode_basis_field_contracts(tampered)


@pytest.mark.parametrize("mutate", ["source", "coordinate", "imaginary", "cleanup"])
def test_v2_synthetic_field_reference_rejects_foreign_or_incomplete_readback_envelopes(mutate):
    request, contracts, raws = _synthetic_field_contract_bundle()
    raw = copy.deepcopy(raws["modes"][1])
    if mutate == "source":
        raw["source"]["solution_id"] = "other-solution"
    elif mutate == "coordinate":
        raw["coordinates_m"][0][10] += 1e-8
    elif mutate == "imaginary":
        raw["expressions"][0].pop("imag")
    elif mutate == "cleanup":
        raw["cleanup"]["removed"] = False
    mode_raws = [raws["modes"][0], raw]
    with pytest.raises(TwoModeBasisError):
        independent_two_mode_basis_from_native_field_readbacks(
            request, contracts, signal_raw=raws["signal"], mode_raws=mode_raws,
            incident_raw=raws["incident"])


@pytest.mark.parametrize("change", [
    {"signal_source": _source(label="signal", port="2", frequency_hz=FREQUENCY * 1.01)},
    {"signal_source": _source(label="signal", port="2", geometry_revision=13)},
    {"incident_source": _source(label="incident", port="1", coordinate_frame="other-frame")},
])
def test_v2_refuses_mixed_frequency_or_geometry_source_identities(change):
    with pytest.raises(TwoModeBasisError, match="mix project/model revision, geometry frame, or frequency"):
        _request(**change)


def test_v2_refuses_foreign_model_project_wrong_port_and_duplicate_basis_member():
    foreign = _source(label="signal", port="2", project_id="foreign-project")
    with pytest.raises(TwoModeBasisError, match="mix project/model revision"):
        _request(signal_source=foreign)
    wrong_port = _source(label="mode-a", port="1", mode_index=1)
    modes = _request()["basis_modes"]
    modes[0]["source"] = wrong_port
    with pytest.raises(TwoModeBasisError, match="both basis members must come from output Port 2"):
        _request(mode_sources=modes)
    duplicate = _request()["basis_modes"]
    duplicate[1] = copy.deepcopy(duplicate[0])
    with pytest.raises(TwoModeBasisError, match="duplicate basis mode IDs"):
        _request(mode_sources=duplicate)


def test_v2_refuses_wrong_surface_unit_dimension_normal_and_aperture_area():
    base = _request()
    bad_plane = copy.deepcopy(base["output_surface"])
    bad_plane["coordinate_unit"] = "mm"
    with pytest.raises(TwoModeBasisError, match="geometry-unit"):
        _request(output_surface=bad_plane)
    bad_plane = copy.deepcopy(base["output_surface"])
    bad_plane["entity_dimension"] = 1
    with pytest.raises(TwoModeBasisError, match="2-D boundary"):
        _request(output_surface=bad_plane)
    bad_plane = copy.deepcopy(base["output_surface"])
    bad_plane["axis_xyz"] = [2.0, 0.0, 0.0]
    with pytest.raises(TwoModeBasisError, match="normalized 3-vector"):
        _request(output_surface=bad_plane)
    bad_plane = copy.deepcopy(base["output_surface"])
    bad_plane["surface_area_m2"] *= 1.1
    with pytest.raises(TwoModeBasisError, match="quadrature area"):
        _request(output_surface=bad_plane)
    bad_plane = copy.deepcopy(base["output_surface"])
    bad_plane["frame_id"] = "foreign-frame"
    with pytest.raises(TwoModeBasisError, match="exact source coordinate-frame"):
        _request(output_surface=bad_plane)


def test_v2_projection_matches_hand_calculated_complex_orthogonal_basis():
    _, _, _, alpha, beta = _analytic_fields()
    result = _project_from_analytic()
    assert result["status"] == "SOFTWARE_RECOMPUTED_TWO_MODE_BASIS_FROM_FIELD_SAMPLES"
    assert result["native_result"] == "NOT_RUN"
    assert result["coefficients"][0]["real"] == pytest.approx(alpha.real)
    assert result["coefficients"][0]["imag"] == pytest.approx(alpha.imag)
    assert result["coefficients"][1]["real"] == pytest.approx(beta.real)
    assert result["coefficients"][1]["imag"] == pytest.approx(beta.imag)
    expected_power = 0.5 * (abs(alpha) ** 2 + abs(beta) ** 2)
    assert result["projected_power_w_raw"] == pytest.approx(expected_power)
    assert result["eta_basis_raw"] == pytest.approx(expected_power / 10.0)
    assert result["normalized_gram_condition"] == pytest.approx(1.0)
    assert result["power_values_clamped"] is False


def test_v2_exact_self_and_orthogonal_mode_projections_are_basis_invariant():
    _, modes, incident, _, _ = _analytic_fields()
    self_projection = _project_from_analytic(
        signal=_scale(modes[0], 3j))
    assert self_projection["coefficients"][0]["real"] == pytest.approx(0.0, abs=1e-14)
    assert self_projection["coefficients"][0]["imag"] == pytest.approx(3.0)
    assert self_projection["coefficients"][1]["real"] == pytest.approx(0.0, abs=1e-14)
    assert self_projection["coefficients"][1]["imag"] == pytest.approx(0.0, abs=1e-14)

    orthogonal_control = _project_from_analytic(signal=modes[1])
    assert orthogonal_control["coupling"][0]["real"] == pytest.approx(0.0, abs=1e-14)
    assert orthogonal_control["coupling"][0]["imag"] == pytest.approx(0.0, abs=1e-14)
    assert orthogonal_control["coefficients"][1]["real"] == pytest.approx(1.0)
    assert orthogonal_control["projected_power_w_raw"] == pytest.approx(0.5)


def test_v2_subspace_power_is_invariant_under_unitary_and_nonorthogonal_basis_changes():
    base = _project_from_analytic()
    gram = [[complex(**base["gram_raw"][i][j]) for j in range(2)] for i in range(2)]
    coupling = [complex(**row) for row in base["coupling"]]
    theta = 0.61
    cosine, sine = math.cos(theta), math.sin(theta)
    unitary = [[cosine, 1j * sine], [1j * sine, cosine]]
    nonorthogonal = [[1.4, 0.3 + 0.1j], [0.1 - 0.2j, 0.8]]
    for transform in (unitary, nonorthogonal):
        changed_g, changed_b = change_basis_coordinates(gram, coupling, transform)
        result = compute_two_mode_basis_projection(
            changed_g, changed_b, signal_power_w=base["signal_power_w"],
            incident_power_w=base["incident_power_w"], source_identity={"basis_change": True})
        assert result["projected_power_w_raw"] == pytest.approx(base["projected_power_w_raw"], rel=1e-12)
        assert result["eta_basis_raw"] == pytest.approx(base["eta_basis_raw"], rel=1e-12)


def test_v2_global_signal_phase_and_basis_scale_do_not_change_subspace_efficiency():
    base = _project_from_analytic()
    gram = [[complex(**base["gram_raw"][i][j]) for j in range(2)] for i in range(2)]
    coupling = [complex(**row) for row in base["coupling"]]
    phase = complex(math.cos(1.23), math.sin(1.23))
    phase_b = [phase * value for value in coupling]
    phase_result = compute_two_mode_basis_projection(
        gram, phase_b, signal_power_w=base["signal_power_w"],
        incident_power_w=base["incident_power_w"], source_identity={"phase": "signal"})
    assert phase_result["eta_basis_raw"] == pytest.approx(base["eta_basis_raw"], rel=1e-12)
    scaled_g, scaled_b = change_basis_coordinates(gram, coupling, [[2 + 1j, 0], [0, 0.25 - 0.5j]])
    scaled_result = compute_two_mode_basis_projection(
        scaled_g, scaled_b, signal_power_w=base["signal_power_w"],
        incident_power_w=base["incident_power_w"], source_identity={"scale": "basis"})
    assert scaled_result["projected_power_w_raw"] == pytest.approx(base["projected_power_w_raw"], rel=1e-12)
    signed_g, signed_b = change_basis_coordinates(gram, coupling, [[-1, 0], [0, 1]])
    signed_result = compute_two_mode_basis_projection(
        signed_g, signed_b, signal_power_w=base["signal_power_w"],
        incident_power_w=base["incident_power_w"], source_identity={"sign": "basis"})
    assert signed_result["eta_basis_raw"] == pytest.approx(base["eta_basis_raw"], rel=1e-12)


def test_v2_duplicate_singular_and_ill_conditioned_basis_is_rejected():
    source = {"case_id": "duplicate"}
    for gram in (
        [[1, 1], [1, 1]],
        [[1, 1 - 1e-12], [1 - 1e-12, 1]],
    ):
        with pytest.raises(TwoModeBasisError, match="positive definite|condition-number|singular"):
            compute_two_mode_basis_projection(gram, [0.5, 0.5], signal_power_w=1,
                                              incident_power_w=2, source_identity=source)


def test_v2_scaled_solve_handles_large_gram_entries_and_valid_low_power_basis():
    large = compute_two_mode_basis_projection(
        [[1e200, 1e199], [1e199, 1e200]], [1e100, 1e100],
        signal_power_w=1.0, incident_power_w=1.0, source_identity={"scale": "large"})
    assert large["normalized_gram_condition"] == pytest.approx(11.0 / 9.0)
    assert large["projected_power_w_raw"] == pytest.approx(20.0 / 11.0)
    assert large["eta_basis_raw"] == pytest.approx(20.0 / 11.0)
    for row in large["gram_used_for_projection"]:
        for value in row:
            assert math.isfinite(value["real"]) and math.isfinite(value["imag"])
    for value in large["coefficients"]:
        assert math.isfinite(value["real"]) and math.isfinite(value["imag"])

    # All powers exceed the frozen absolute W floor. The basis is well
    # conditioned (kappa=19), so an absolute determinant threshold would
    # incorrectly reject this valid, uniformly rescaled case.
    small = compute_two_mode_basis_projection(
        [[2e-12, 1.8e-12], [1.8e-12, 2e-12]], [1e-12, 1e-12],
        signal_power_w=2e-12, incident_power_w=2e-12, source_identity={"scale": "small"})
    assert small["normalized_gram_condition"] == pytest.approx(19.0)
    assert small["projected_power_w_raw"] == pytest.approx(2e-12 / 3.8)
    assert small["eta_basis_raw"] == pytest.approx(1.0 / 3.8)


def test_v2_hermitian_residual_is_scaled_per_mode_for_extreme_basis_imbalance():
    with pytest.raises(TwoModeBasisError, match="not Hermitian"):
        compute_two_mode_basis_projection(
            [[1e200, 0], [0, 2e-12 + 1e-14j]], [0, 0], signal_power_w=1,
            incident_power_w=1, source_identity={"negative_control": "weak_mode_diagonal_imaginary_error"})


def test_v2_nonhermitian_gram_and_singular_change_of_basis_fail_closed():
    with pytest.raises(TwoModeBasisError, match="not Hermitian"):
        compute_two_mode_basis_projection(
            [[1, 0.2], [0.1, 1]], [0.5, 0.5], signal_power_w=1,
            incident_power_w=2, source_identity={"basis": "bad-hermitian"})
    with pytest.raises(TwoModeBasisError, match="transform must be invertible"):
        change_basis_coordinates([[1, 0], [0, 1]], [1, 0], [[1, 2], [2, 4]])


def test_v2_raw_projected_efficiency_is_not_clamped_to_one():
    result = compute_two_mode_basis_projection(
        [[0.5, 0], [0, 0.5]], [2, 0], signal_power_w=10.0,
        incident_power_w=1.0, source_identity={"outcome": "diagnostic-only"})
    assert result["eta_basis_raw"] == pytest.approx(8.0)
    assert result["power_values_clamped"] is False


def test_v2_reversed_surface_normal_fails_forward_power_gate_without_clamping():
    signal, modes, incident, _, _ = _analytic_fields()
    with pytest.raises(TwoModeBasisError, match="positive signal and incident reference powers"):
        independent_two_mode_basis_from_samples(
            signal=signal, modes=modes, incident=incident,
            output_normals_xyz=[(-1, 0, 0)], output_weights_m2=[1.0],
            input_normals_xyz=[(-1, 0, 0)], input_weights_m2=[1.0],
            source_identity={"normal": "reversed"})


def test_v2_nonreciprocal_lossy_or_nonforward_assumptions_are_not_admissible():
    request = _request()
    for name in ("reciprocal", "lossless", "forward_propagating", "non_evanescent", "non_leaky"):
        assumptions = dict(request["applicability"])
        assumptions[name] = False
        with pytest.raises(TwoModeBasisError, match="restricted to explicitly requested"):
            _request(applicability=assumptions)


def test_v2_power_form_uses_conjugate_linear_first_argument_and_full_xyz_vectors():
    from tools.w23_mode_basis_v2 import power_form_integral

    a = _field((1 + 2j, 2 - 1j, 0), (0, 1 + 1j, 0))
    b = _field((2 - 1j, 1 + 3j, 0), (1 - 2j, 0, 2j))
    forward = power_form_integral(a, b, normals_xyz=[(0, 0, 1)], weights_m2=[3.0])
    reverse = power_form_integral(b, a, normals_xyz=[(0, 0, 1)], weights_m2=[3.0])
    assert reverse == pytest.approx(forward.conjugate(), rel=1e-14, abs=1e-14)


def test_v2_does_not_turn_synthetic_projection_into_native_acceptance():
    result = _project_from_analytic()
    assert result["evidence_scope"].startswith("software quadrature reference")
    assert result["native_result"] == "NOT_RUN"
    assert result["study_or_solver_invoked"] is False
    assert result["algorithm_id"].endswith("two_mode_basis_projection_v2")
