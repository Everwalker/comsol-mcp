from __future__ import annotations

import copy
import hashlib
import math

import pytest

from tools.w23_full3d_science import (
    FULL3D_COMPARISON_POLICY,
    FULL3D_CONVERGENCE_POLICY,
    Full3DScienceError,
    ManagedRouteOutcomeError,
    bind_full3d_connection_response,
    bind_full3d_model_create_response,
    bind_full3d_project_create_response,
    build_full3d_dataset_indices_dispatch,
    build_full3d_dataset_list_dispatch,
    build_full3d_bma_probe_prepare_dispatch,
    build_full3d_bma_probe_run_dispatch,
    build_full3d_bma_basis_mapping_contracts,
    build_full3d_bma_basis_mapping_dispatch,
    build_full3d_convergence_recipe,
    build_full3d_mesh_level_fixture_dispatch,
    build_full3d_model_create_request,
    build_full3d_model_load_request,
    build_full3d_mode_overlap_definition,
    build_full3d_mode_overlap_dispatch,
    build_full3d_project_create_request,
    build_full3d_save_dispatch,
    build_full3d_server_connect_request,
    build_full3d_solution_inventory_dispatch,
    build_full3d_study_run_dispatch,
    build_raw_field_contract,
    build_raw_field_dispatch,
    circular_port_quadrature,
    compare_native_mode_overlap,
    dispatch_public_managed_route,
    execute_full3d_managed_configuration_chain,
    independent_mode_overlap_integrals,
    resolve_full3d_native_sources,
    resolve_full3d_bma_basis_sources,
    rectangular_port_quadrature,
    recompute_full3d_mesh_stability_from_registered_routes,
    validate_native_field_readback,
    validate_native_source_cohort_snapshot,
    validate_full3d_bma_probe_preparation,
    validate_full3d_bma_probe_run_readback,
    validate_full3d_bma_basis_mapping_samples,
)
from tools.w23_full3d import canonical_full3d_recipe


CASE = {"case_id": "case-aligned", "case_identity_sha256": "a" * 64}
OUTPUT_SOURCE = {"dataset_id": "dset_freq", "solution_id": "sol_freq",
                 "outer_index": 1, "inner_index": 1, "solnum": 1}
MODE_SOURCE = {"dataset_id": "dset_bma_out", "solution_id": "sol_bma_out",
               "outer_index": 1, "inner_index": 1, "solnum": 1}
INPUT_SOURCE = {"dataset_id": "dset_bma_in", "solution_id": "sol_bma_in",
                "outer_index": 1, "inner_index": 1, "solnum": 1}
OUTPUT_PLANE = {"plane_id": "receiver_port", "component": "comp3d", "geometry": "geom3d",
                "selection_tag": "sel3dOutputPort", "entity_dimension": 2,
                "coordinate_unit": "um", "center_xyz_um": [20.0, 0.0, 0.0],
                "axis_xyz": [1.0, 0.0, 0.0], "native_normal_sign": 1,
                "aperture_shape": "circular", "sample_radius_um": 2.52}
INPUT_PLANE = {"plane_id": "input_port", "component": "comp3d", "geometry": "geom3d",
               "selection_tag": "sel3dInputPort", "entity_dimension": 2,
               "coordinate_unit": "um", "center_xyz_um": [-20.0, 0.0, 0.0],
               "axis_xyz": [1.0, 0.0, 0.0], "native_normal_sign": -1,
               "aperture_shape": "rectangle", "half_widths_uv_um": [8.0, 8.0]}
CAPTURE_PLANE = {**OUTPUT_PLANE, "selection_tag": "sel3dOutputCoreCapture",
                 "sample_radius_um": 1.2}


def _contracts():
    common = {"radial_intervals": 32, "angular_points": 64}
    signal = build_raw_field_contract(case=CASE, role="signal", source=OUTPUT_SOURCE,
                                      plane=OUTPUT_PLANE, **common)
    mode = build_raw_field_contract(case=CASE, role="reference_mode", source=MODE_SOURCE,
                                    plane=OUTPUT_PLANE, **common)
    incident = build_raw_field_contract(case=CASE, role="incident_reference", source=INPUT_SOURCE,
                                        plane=INPUT_PLANE, **common)
    capture = build_raw_field_contract(case=CASE, role="capture_signal", source=OUTPUT_SOURCE,
                                       plane=CAPTURE_PLANE, **common)
    def disk(contract):
        plane = contract["plane"]
        return circular_port_quadrature(
            plane["center_xyz_m"], plane["axis_xyz"], plane["sample_radius_m"],
            radial_intervals=32, angular_points=64)
    output_q = disk(signal)
    mode_q = disk(mode)
    capture_q = disk(capture)
    input_plane = incident["plane"]
    incident_q = rectangular_port_quadrature(
        input_plane["center_xyz_m"], input_plane["axis_xyz"],
        input_plane["half_widths_uv_m"], u_intervals=32, v_intervals=64)
    return (signal, mode, incident, capture), (output_q, mode_q, incident_q, capture_q)


MODEL_REF = {"schema_version": 1, "session_id": "session-1",
             "server_instance_id": "worker-epoch-1", "model_tag": "M1", "generation": 1}


def _solution_dataset(tag, solution, *, names, values, units, outer=1, inner=1, solnum=1):
    return {"tag": tag, "type_id": "Solution", "solution": solution,
            "component": "comp3d", "geometry": "geom3d"}, {
                "dataset": tag, "solution": solution, "binding_complete": True,
                "parameters": {"by_pair": {f"{outer}:{inner}": {
                    "names": names, "values": values, "units": units, "solnum": solnum}}}}


def _cohort_snapshot(contract):
    source = contract["source"]
    sequence = source["solution_id"]
    tuple_row = {"outer_index": source["outer_index"],
                 "inner_index": source["inner_index"],
                 "solnum": source["solnum"],
                 "solver_sequence_tag": sequence}
    solution_pairs = [tuple_row]
    if contract.get("role") == "bma_basis_mapping_pair":
        solution_pairs = [{"outer_index": source["outer_index"], "inner_index": inner,
                           "solnum": inner, "solver_sequence_tag": sequence}
                          for inner in (1, 2)]

    def props(values):
        return {name: {"has_property_exact": True, "string_readback": value}
                for name, value in values.items()}

    config = {
        "schema_id": "urn:comsol-mcp:w23:fixture-explicit-config:1.0.0",
        "fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
        "parameters": {name: "1" for name in (
            "lambda0", "f0", "w23Ncore", "w23Nclad", "w23Nlens", "w23CoreR",
            "w23CladR", "w23LensR", "w23AirHalfY", "w23AirHalfZ", "w23PmlT",
            "w23XIn", "w23XInEnd", "w23XOutStart", "w23XOut", "w23XDomainMax",
            "w23OutDy", "w23OutDz", "w23ThetaY", "w23ThetaZ")},
        "geometry": {"dimension": 3, "length_unit": "um", "domain_count": 8,
                     "bounding_box": [[-20.0, -8.0, -8.0], [21.0, 8.0, 8.0]]},
        "mesh": {"tag": "mesh3d", "geometry": "geom3d", "elements": 1024,
                 "size_properties": {"requested_properties": props({
                     "custom": "on", "hmax": "lambda0/(5*w23Nlens)",
                     "hmin": "lambda0/(12*w23Nlens)"})}},
        "materials": [{"tag": tag, "selection_tag": selection_tag,
                       "domain_ids": [domain_id],
                       "relative_permittivity": [["1", "0", "0"], ["0", "1", "0"], ["0", "0", "1"]],
                       "relative_permeability": [["1", "0", "0"], ["0", "1", "0"], ["0", "0", "1"]]}
                      for tag, selection_tag, domain_id in (
                          ("matCore3d", "geom3d_coreIn_dom", 1),
                          ("matCoreOut3d", "geom3d_rotCoreOutZ_dom", 2),
                          ("matCladIn3d", "geom3d_cladShellIn_dom", 3),
                          ("matCladOut3d", "geom3d_rotCladShellOutZ_dom", 4),
                          ("matLens3d", "geom3d_lensBall_dom", 5),
                          ("matAir3d", "geom3d_airRemainder_dom", 6),
                          ("matPmlAir3d", "geom3d_pmlShell_dom", 7))],
        "physics": {"tag": "ewfd", "feature_type": "ElectromagneticWaves", "features": [
            {"tag": "portIn3d", "feature_type": "Port", "selection_tag": "sel3dInputPort",
             "selection_dimension": 2, "selection_ids": [11],
             "port_properties": {"requested_properties": props({
                 "PortType": "Numeric", "PortName": "1", "PortModeNumber": "1",
                 "PortOrientation": "ForwardPort"})}},
            {"tag": "portOut3d", "feature_type": "Port", "selection_tag": "sel3dOutputPort",
             "selection_dimension": 2, "selection_ids": [22],
             "port_properties": {"requested_properties": props({
                 "PortType": "Numeric", "PortName": "2", "PortModeNumber": "1",
                 "PortOrientation": "ForwardPort"})}}]},
        "pml": {"requested_properties": props({
            "ScalingType": "Cartesian", "stretchingType": "polynomial",
            "typicalWavelength": "lambda0"})},
        "selections": {
            "sel3dInputPort": {"entity_dimension": 2, "entity_ids": [11]},
            "sel3dOutputPort": {"entity_dimension": 2, "entity_ids": [22]},
            "sel3dOutputCoreCapture": {"entity_dimension": 2, "entity_ids": [22]},
            contract["plane"]["selection_tag"]: {
                "entity_dimension": 2,
                "entity_ids": [11] if contract["plane"]["selection_tag"] == "sel3dInputPort" else [22]},
        },
        "study_steps": {"study_" + sequence: [{"tag": "step"}]},
    }
    snapshot = {
        "dataset": {"tag": source["dataset_id"], "feature_type": "Solution",
                    "properties": {"solution": sequence}},
        "stored_solution": {
            "solution_tag": sequence, "study_tag": "study_" + sequence,
            "computation_date_ms": 100, "computation_version": "6.4.0.293",
            "parameter_axis": [{"name": "f0", "value": 193.414489032258e12}],
            "solution_info": {"is_valid": True, "solver_sequence_is_empty": False,
                              "solution_pairs": solution_pairs},
            "selected_tuple": tuple_row},
        "fixture_explicit_configuration": config,
    }
    return {"schema_id": "urn:comsol-mcp:w23:source-cohort-snapshot:1.0.0",
            "native_result": "COMSOL_NATIVE_SOURCE_COHORT_SNAPSHOTS",
            "before": snapshot, "after": copy.deepcopy(snapshot)}


def _raw(contract, quadrature, electric, magnetic):
    normal_sign = contract["plane"]["native_normal_sign"]
    coordinates = quadrature["coordinates_m"]
    expressions = []
    values = {
        "E": electric, "H": magnetic,
        "Emode_2": electric, "Hmode_2": magnetic,
        "Emode_1": electric, "Hmode_1": magnetic,
        "nx": (normal_sign, 0), "ny": (0, 0), "nz": (0, 0),
    }
    for name in contract["field_names"]:
        if name in {"nx", "ny", "nz"}:
            pair = values[name]
        else:
            suffix = name.rsplit(".", 1)[1]
            if suffix.startswith("Emode"):
                pair = values["Emode_" + suffix.rsplit("_", 1)[1]]["xyz".index(suffix[5])]
            elif suffix.startswith("Hmode"):
                pair = values["Hmode_" + suffix.rsplit("_", 1)[1]]["xyz".index(suffix[5])]
            elif suffix.startswith("E"):
                pair = electric["xyz".index(suffix[1])]
            else:
                pair = magnetic["xyz".index(suffix[1])]
        if isinstance(pair, complex):
            real, imag = pair.real, pair.imag
        else:
            real, imag = pair
        unit_row = next((group_name, group) for group_name, group in contract["unit_groups"].items()
                        if name in group["expressions"])
        expressions.append({"expression": name,
                            "real": [real] * len(coordinates),
                            "imag": [imag] * len(coordinates),
                            "unit_group": unit_row[0], "unit": unit_row[1]["unit"]})
    return {"native_result": "COMSOL_NATIVE_RAW", "study_or_solver_invoked": False,
            "complex_readback": True, "contract_id": contract["contract_id"],
            "quadrature_sha256": contract["quadrature_sha256"],
            "source": contract["source"], "plane": contract["plane"],
            "sample_count": len(coordinates),
            "coordinates_m": [[point[axis] for point in coordinates] for axis in range(3)],
            "expressions": expressions,
            "units_preserved": True,
            "unit_readback": {key: row["unit"] for key, row in contract["unit_groups"].items()},
            "cleanup": {"created_count": 3, "expected_count": 3,
                        "removed": True, "cleanup_failed": False,
                        "tags": ["w23rf" + contract["contract_id"][:10] + suffix
                                 for suffix in ("e", "h", "n")], "error": ""},
            "source_cohort": _cohort_snapshot(contract)}


def _synthetic_native_records():
    (signal, mode, incident, capture), (signal_q, mode_q, incident_q, capture_q) = _contracts()
    signal_raw = _raw(signal, signal_q, (0j, 2 + 1j, 0j), (0j, 0j, 1 + 0j))
    mode_raw = _raw(mode, mode_q, (0j, 3 - 2j, 0j), (0j, 0j, 3 - 2j))
    incident_raw = _raw(incident, incident_q, (0j, 3 + 0j, 0j), (0j, 0j, 2 + 0j))
    capture_raw = _raw(capture, capture_q, (0j, 2 + 1j, 0j), (0j, 0j, 1 + 0j))
    independent = independent_mode_overlap_integrals(
        signal_raw, mode_raw, incident_raw,
        signal_contract=signal, mode_contract=mode, incident_contract=incident,
        signal_quadrature=signal_q, mode_quadrature=mode_q, incident_quadrature=incident_q,
        power_floor_w=1e-15, capture_raw=capture_raw, capture_contract=capture,
        capture_quadrature=capture_q)
    return (signal, mode, incident, capture), (signal_q, mode_q, incident_q, capture_q), \
        (signal_raw, mode_raw, incident_raw, capture_raw), independent


def _native_overlap_from_independent(independent):
    integrals = {}
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        integrals[key] = {"real": independent[key], "imag": 0.0, "unit": "W"}
    cross = independent["reciprocal_overlap_numerator"]
    integrals["reciprocal_overlap_numerator"] = {**cross, "unit": "W"}
    integrals["capture_aperture_signal_flux"] = {
        "real": independent["capture_aperture_signal_flux"], "imag": 0.0, "unit": "W"}
    incident = independent["incident_reference_power"]
    capture = independent["capture_aperture_signal_flux"]
    return {"status": "SUCCEEDED", "result_status": "COMPUTED_NATIVE_INTEGRALS",
            "surface": {"geometry_dimension": 3, "power_unit": "W"},
            "integrals": integrals, "eta_mode": independent["eta_mode"],
            "eta_capture": {"status": "COMPUTED_NATIVE_APERTURE_FLUX",
                            "value": capture / incident, "unit": "1",
                            "numerator": {"real": capture, "imag": 0.0, "unit": "W"},
                            "denominator": {"reference_id": "input-mode", "value": incident, "unit": "W"}}}


def test_full3d_area_quadratures_cover_native_output_disk_and_input_rectangle():
    circle = circular_port_quadrature([0, 0, 0], [1, 0, 0], 2.5e-6,
                                      radial_intervals=32, angular_points=64)
    rectangle = rectangular_port_quadrature([0, 0, 0], [1, 0, 0], [8e-6, 8e-6],
                                             u_intervals=32, v_intervals=64)

    assert math.isclose(circle["weight_sum_m2"], math.pi * (2.5e-6) ** 2, rel_tol=1e-14)
    assert math.isclose(rectangle["weight_sum_m2"], 256e-12, rel_tol=1e-14)
    assert len(circle["coordinates_m"]) == 1 + 32 * 64
    assert len(rectangle["coordinates_m"]) == 33 * 65


def test_full3d_independent_complex_power_and_reciprocity_match_hand_calculation():
    (signal, mode, incident, capture), (signal_q, mode_q, incident_q, capture_q), _, result = _synthetic_native_records()
    area_out = math.pi * (2.52e-6) ** 2
    area_capture = math.pi * (1.2e-6) ** 2
    area_input = 256e-12

    assert math.isclose(result["signal_power"], 1.0 * area_out, rel_tol=1e-13)
    assert math.isclose(result["reference_mode_power"], 6.5 * area_out, rel_tol=1e-13)
    assert math.isclose(result["incident_reference_power"], 3.0 * area_input, rel_tol=1e-13)
    assert math.isclose(result["reciprocal_overlap_numerator"]["real"], 7.0 * area_out, rel_tol=1e-13)
    assert math.isclose(result["reciprocal_overlap_numerator"]["imag"], 9.0 * area_out, rel_tol=1e-13)
    assert math.isclose(result["capture_aperture_signal_flux"], 1.0 * area_capture, rel_tol=1e-13)
    assert result["native_result"] == "NOT_RUN"

    native = _native_overlap_from_independent(result)
    comparison = compare_native_mode_overlap(native, result)
    assert comparison["status"] == "SOFTWARE_NATIVE_VS_INDEPENDENT_FULL3D_COMPARISON_VALID"
    assert comparison["native_result"] == "NOT_RUN"
    assert comparison["comparison_policy"] == FULL3D_COMPARISON_POLICY


def test_full3d_mesh_level_dispatch_binds_the_frozen_setup_before_any_study_run():
    recipe = build_full3d_convergence_recipe(canonical_full3d_recipe())
    for level in recipe["mesh_levels"]:
        request = build_full3d_mesh_level_fixture_dispatch(
            recipe, mesh_level_id=level["level_id"], mesh_scale_factor=level["scale_factor"],
            source_artifact="NativeW23Full3DFixture.java", project_id="project-1",
            model_ref=MODEL_REF, model_tag="M1", revision=0,
            request_id=f"build-{level['level_id']}", idempotency_key=f"build-idem-{level['level_id']}")
        java_args = request["arguments"]["arguments"]["arguments"]
        assert java_args["phase"] == "build"
        assert java_args["recipe_sha256"] == canonical_full3d_recipe()["recipe_sha256"]
        assert java_args["mesh_level_id"] == level["level_id"]
        assert java_args["mesh_scale_factor"] == level["scale_factor"]
        assert request["execution"]["expected_revision"] == 0
        assert request["native_result"] == "NOT_RUN"
        assert request["study_or_solver_invoked"] is False
    with pytest.raises(Full3DScienceError, match="mesh-level setup ID and factor"):
        build_full3d_mesh_level_fixture_dispatch(
            recipe, mesh_level_id="mesh2", mesh_scale_factor=0.9,
            source_artifact="NativeW23Full3DFixture.java", project_id="project-1",
            model_ref=MODEL_REF, model_tag="M1", revision=0,
            request_id="bad-build", idempotency_key="bad-build-idem")


def test_full3d_zero_and_near_zero_overlap_use_frozen_power_normalized_absolute_floor():
    _contracts_, _quadratures, _raws, independent = _synthetic_native_records()
    zero = copy.deepcopy(independent)
    zero["reciprocal_overlap_numerator"] = {"real": 0.0, "imag": 0.0}
    zero["eta_mode"] = 0.0
    native = _native_overlap_from_independent(zero)
    power_scale = 4 * math.sqrt(zero["signal_power"] * zero["reference_mode_power"])
    cross_atol = FULL3D_COMPARISON_POLICY["cross_absolute_normalized_tolerance"]
    eta_atol = FULL3D_COMPARISON_POLICY["eta_absolute_tolerance"]
    native["integrals"]["reciprocal_overlap_numerator"]["imag"] = power_scale * cross_atol * 0.5
    native["eta_mode"] = eta_atol * 0.5

    accepted = compare_native_mode_overlap(native, zero)
    assert accepted["normalized_absolute_errors"]["reciprocal_overlap_numerator"] == pytest.approx(cross_atol * 0.5)
    assert accepted["efficiency_absolute_errors"]["eta_mode"] == pytest.approx(eta_atol * 0.5)

    near_zero = copy.deepcopy(independent)
    near_zero["reciprocal_overlap_numerator"] = {
        "real": power_scale * cross_atol * 0.5, "imag": 0.0}
    near_zero["eta_mode"] = eta_atol * 0.4
    near_native = _native_overlap_from_independent(near_zero)
    near_native["integrals"]["reciprocal_overlap_numerator"]["real"] += power_scale * cross_atol * 0.5
    near_native["eta_mode"] += eta_atol * 0.5
    compare_native_mode_overlap(near_native, near_zero)


def test_full3d_zero_channel_errors_over_frozen_absolute_limits_fail_closed():
    _contracts_, _quadratures, _raws, independent = _synthetic_native_records()
    zero = copy.deepcopy(independent)
    zero["reciprocal_overlap_numerator"] = {"real": 0.0, "imag": 0.0}
    zero["eta_mode"] = 0.0
    native = _native_overlap_from_independent(zero)
    power_scale = 4 * math.sqrt(zero["signal_power"] * zero["reference_mode_power"])
    native["integrals"]["reciprocal_overlap_numerator"]["real"] = (
        power_scale * FULL3D_COMPARISON_POLICY["cross_absolute_normalized_tolerance"] * 1.01)
    with pytest.raises(Full3DScienceError, match="normalized absolute error"):
        compare_native_mode_overlap(native, zero)

    native = _native_overlap_from_independent(zero)
    native["eta_mode"] = FULL3D_COMPARISON_POLICY["eta_absolute_tolerance"] * 1.01
    with pytest.raises(Full3DScienceError, match="eta_mode absolute error"):
        compare_native_mode_overlap(native, zero)


def test_full3d_nonzero_overlap_retains_strict_relative_threshold():
    _contracts_, _quadratures, _raws, independent = _synthetic_native_records()
    native = _native_overlap_from_independent(independent)
    perturbation = 1 + FULL3D_COMPARISON_POLICY["relative_tolerance"] * 1.01
    native["integrals"]["reciprocal_overlap_numerator"]["real"] *= perturbation
    native["integrals"]["reciprocal_overlap_numerator"]["imag"] *= perturbation
    with pytest.raises(Full3DScienceError, match="reciprocal overlap relative error"):
        compare_native_mode_overlap(native, independent)


def test_full3d_native_aperture_denominator_and_eta_corruption_fail_closed():
    _contracts_, _quadratures, _raws, independent = _synthetic_native_records()
    native = _native_overlap_from_independent(independent)
    wrong_denominator = copy.deepcopy(native)
    wrong_denominator["eta_capture"]["denominator"]["value"] *= 2
    with pytest.raises(Full3DScienceError, match="denominator value differs"):
        compare_native_mode_overlap(wrong_denominator, independent)

    wrong_eta = copy.deepcopy(native)
    wrong_eta["eta_capture"]["value"] *= 2
    with pytest.raises(Full3DScienceError, match="does not equal"):
        compare_native_mode_overlap(wrong_eta, independent)


def test_full3d_input_normal_is_outward_minus_x_but_integrated_forward_plus_x():
    (signal, mode, incident, capture), quadratures, raws, result = _synthetic_native_records()
    assert incident["plane"]["native_normal_sign"] == -1
    assert incident["plane"]["physical_forward_normal_xyz"] == [1.0, 0.0, 0.0]
    assert raws[2]["expressions"][6]["real"][0] == -1
    assert result["incident_reference_power"] > 0
    bad = copy.deepcopy(raws[2])
    bad["expressions"][6]["real"] = [1] * bad["sample_count"]
    with pytest.raises(Full3DScienceError, match="outward along"):
        validate_native_field_readback(incident, bad, expected_quadrature=quadratures[2])


def test_native_source_cohort_snapshot_is_bound_to_exact_solution_and_pre_after_config():
    (signal, _mode, _incident, _capture), (signal_q, _mode_q, _incident_q, _capture_q) = _contracts()
    raw = _raw(signal, signal_q, (0j, 2 + 1j, 0j), (0j, 0j, 1 + 0j))
    checked = validate_native_source_cohort_snapshot(signal, raw)
    assert checked["status"] == "NATIVE_SOURCE_COHORT_SNAPSHOT_VALIDATED"
    assert checked["computation_version"] == "6.4.0.293"
    assert checked["selected_tuple"]["inner_index"] == signal["source"]["inner_index"]
    assert checked["fixture_explicit_configuration_sha256"]

    changed = copy.deepcopy(raw)
    changed["source_cohort"]["after"]["fixture_explicit_configuration"]["mesh"]["elements"] += 1
    with pytest.raises(Full3DScienceError, match="changed during"):
        validate_native_source_cohort_snapshot(signal, changed)

    wrong_binding = copy.deepcopy(raw)
    wrong_binding["source_cohort"]["before"]["dataset"]["properties"]["solution"] = "foreign-sol"
    wrong_binding["source_cohort"]["after"] = copy.deepcopy(wrong_binding["source_cohort"]["before"])
    with pytest.raises(Full3DScienceError, match="dataset/solution"):
        validate_native_source_cohort_snapshot(signal, wrong_binding)

    missing_storage = copy.deepcopy(raw)
    missing_storage["source_cohort"]["before"]["stored_solution"]["computation_date_ms"] = 0
    missing_storage["source_cohort"]["after"] = copy.deepcopy(missing_storage["source_cohort"]["before"])
    with pytest.raises(Full3DScienceError, match="computation date/version"):
        validate_native_source_cohort_snapshot(signal, missing_storage)

    wrong_tuple = copy.deepcopy(raw)
    wrong_tuple["source_cohort"]["before"]["stored_solution"]["selected_tuple"]["solnum"] += 1
    wrong_tuple["source_cohort"]["after"] = copy.deepcopy(wrong_tuple["source_cohort"]["before"])
    with pytest.raises(Full3DScienceError, match="selected tuple"):
        validate_native_source_cohort_snapshot(signal, wrong_tuple)


def test_full3d_managed_field_readback_dispatch_binds_registered_native_contract():
    (signal, _mode, _incident, _capture), quadratures = _contracts()
    route = build_raw_field_dispatch(
        signal, source_artifact="NativeW23Full3DFixture.java", quadrature=quadratures[0],
        project_id="project-1", model_ref={**MODEL_REF, "session_id": "s1"},
        model_tag="M1", revision=9, request_id="req1", idempotency_key="idem1")
    assert route["operation"] == "operation_call"
    assert route["arguments"]["operation_id"] == "code.execute_java"
    assert route["execution"]["project_id"] == "project-1"
    assert route["execution"]["session_id"] == "s1"
    assert route["execution"]["expected_revision"] == 9
    assert route["arguments"]["arguments"]["arguments"]["contract"]["contract_id"] == signal["contract_id"]
    assert len(route["arguments"]["arguments"]["arguments"]["coordinates_m"]) == signal["quadrature"]["sample_count"]
    assert route["dispatch_scope"].startswith("one managed native Interp")
    assert route["arguments"]["arguments"]["arguments"]["study_or_solver_invoked"] is False


def test_full3d_public_project_session_and_model_lifecycle_is_authoritative(tmp_path):
    request = build_full3d_project_create_request(
        label="W23 owned 3D science", request_id="project-create-1", idempotency_key="project-idem-1")
    assert request["operation"] == "project.create"
    assert request["arguments"]["workspace"] == "science"
    assert request["arguments"]["policy"]["permissions"] == [
        "inspect", "project_write", "compute", "trusted_code"]
    assert request["execution"] == {
        "request_id": "project-create-1", "idempotency_key": "project-idem-1"}

    science = tmp_path / "science"
    science.mkdir()
    project = bind_full3d_project_create_response({"success": True, "data": {"project": {
        "project_id": "project-w23", "workspace": str(science), "schema_version": 1,
        "revision": 1, "label": "W23 owned 3D science", "contract": {},
        "policy": {"permissions": request["arguments"]["policy"]["permissions"]}}}},
        authorized_container=str(tmp_path))
    assert project["workspace"] == str(science.resolve())
    assert project["native_result"] == "NOT_RUN"

    connect = build_full3d_server_connect_request(
        project_id=project["project_id"], host="127.0.0.1", port=54321,
        request_id="connect-1", idempotency_key="connect-idem-1")
    assert connect["execution"]["project_id"] == "project-w23"
    session = bind_full3d_connection_response({"success": True, "data": {
        "endpoint": "127.0.0.1:54321", "session_id": "session-1",
        "server_instance_id": "worker-epoch-1", "worker": {"generation": 4}}},
        expected_endpoint="127.0.0.1:54321")
    assert session["worker_epoch"] == 4

    create = build_full3d_model_create_request(
        project_id=project["project_id"], session_id=session["session_id"], name="W23 full3D",
        request_id="model-create-1", idempotency_key="model-idem-1")
    assert create["execution"]["session_id"] == "session-1"
    model = bind_full3d_model_create_response({"success": True, "execution": {
        "model_ref": MODEL_REF, "revision": 0}}, project_id=project["project_id"], session=session)
    assert model["model_ref"] == MODEL_REF
    assert model["worker_epoch"] == 4
    assert model["native_result"] == "NOT_RUN"

    moved = tmp_path / "elsewhere"
    moved.mkdir()
    with pytest.raises(Full3DScienceError, match="exact authorized child"):
        bind_full3d_project_create_response({"success": True, "data": {"project": {
            "project_id": "foreign", "workspace": str(moved)}}}, authorized_container=str(tmp_path))
    wrong_epoch_ref = {**MODEL_REF, "server_instance_id": "stale-epoch"}
    with pytest.raises(Full3DScienceError, match="bound to the observed session epoch"):
        bind_full3d_model_create_response({"success": True, "execution": {
            "model_ref": wrong_epoch_ref, "revision": 0}},
            project_id=project["project_id"], session=session)


def test_full3d_managed_routes_keep_project_session_model_revision_and_no_caller_arrays():
    binding = {"project_id": "project-1", "model_ref": MODEL_REF,
               "model_tag": "M1", "revision": 8}
    calls = [
        build_full3d_study_run_dispatch(**binding, request_id="study-1",
                                        idempotency_key="study-idem-1", timeout_s=120),
        build_full3d_solution_inventory_dispatch(
            source_artifact="NativeW23Full3DFixture.java", **binding,
            request_id="inventory-1", idempotency_key="inventory-idem-1"),
        build_full3d_dataset_list_dispatch(**binding, request_id="datasets-1",
                                           idempotency_key="datasets-idem-1"),
        build_full3d_dataset_indices_dispatch(
            "dset1", **binding, request_id="indices-1", idempotency_key="indices-idem-1"),
        build_full3d_save_dispatch(
            source_artifact="NativeW23Full3DFixture.java", path="case.mph", **binding,
            request_id="save-1", idempotency_key="save-idem-1"),
    ]
    for call in calls:
        assert call["operation"] == "operation_call"
        assert call["execution"]["project_id"] == "project-1"
        assert call["execution"]["session_id"] == "session-1"
        assert call["execution"]["model_ref"] == MODEL_REF
        assert call["execution"]["expected_revision"] == 8
        assert "field_arrays" not in call["arguments"]["arguments"]
        assert call.get("native_result", "NOT_RUN") == "NOT_RUN"
    load = build_full3d_model_load_request(
        path="case.mph", project_id="project-1", session_id="session-1",
        request_id="load-1", idempotency_key="load-idem-1")
    assert load["execution"] == {"project_id": "project-1", "session_id": "session-1",
                                  "request_id": "load-1", "idempotency_key": "load-idem-1"}


def _bma_probe_prepared_readback():
    return {
        "fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
        "managed_identity": {"project_id": "project-1", "model_tag": "M1",
                             "model_ref": dict(MODEL_REF), "expected_revision": 8},
        "status": "BMA_OUTPUT_PROBE_CONFIGURED_NOT_SOLVED",
        "native_result": "COMSOL_NATIVE_BMA_PROBE_CONFIGURATION_READBACK",
        "study_or_solver_invoked": False,
        "producer_status": "PREPARED_ONLY_NOT_PRODUCER_EVIDENCE",
        "field_mapping_status": "UNVERIFIED",
        "receiver_port": {"feature_tag": "portOut3d", "feature_type": "Port",
            "port_type": "Numeric", "port_name": "2", "port_mode_number_readback": "1",
            "selection_tag": "sel3dOutputPort", "entity_dimension": 2,
            "boundary_ids": [17]},
        "original_std3d_steps": [
            {"tag": "bmaInput3d", "feature_type": "BoundaryModeAnalysis",
             "PortName": "1", "modeFreq": "f0", "neigs": 2},
            {"tag": "bmaOutput3d", "feature_type": "BoundaryModeAnalysis",
             "PortName": "2", "modeFreq": "f0", "neigs": 2},
            {"tag": "freq3d", "feature_type": "Frequency", "plist": "f0"}],
        "probe_study": {"study_tag": "std3dBmaOutputProbe", "study_steps": [
            {"tag": "bmaOutputProbe", "feature_type": "BoundaryModeAnalysis",
             "PortName": "2", "modeFreq": "f0", "neigs": 2}]},
        "solver_sequence": {"tag": "sol3dBmaProbe", "feature_type": "SolverSequence",
            "parent_study": "std3dBmaOutputProbe",
            "solver_tree_features": [
                {"path": "st1", "feature_type": "StudyStep"},
                {"path": "st1/v1", "feature_type": "Variables"},
                {"path": "st1/e1", "feature_type": "Eigenvalue"},
                {"path": "st1/d1", "feature_type": "StoreSolution"}],
            "study_step_bindings_in_solver_tree_order": [
                {"path": "st1", "feature_type": "StudyStep",
                 "study": "std3dBmaOutputProbe", "studystep": "bmaOutputProbe"}]},
        "pre_solve_solution_state": {"is_valid": False, "solver_sequence_is_empty": True, "outer_solnums": [],
                                      "solution_pairs": [], "pair_count": 0},
    }


def _validated_bma_probe_preparation():
    return validate_full3d_bma_probe_preparation(
        _bma_probe_prepared_readback(), project_id="project-1", model_tag="M1",
        model_ref=MODEL_REF)


def _bma_probe_run_readback(preparation):
    pairs = [
        {"outer_index": 1, "inner_index": 1, "solnum": 1,
         "solver_sequence_tag": "sol3dBmaProbe"},
        {"outer_index": 1, "inner_index": 2, "solnum": 2,
         "solver_sequence_tag": "sol3dBmaProbe"},
    ]
    return {
        "fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
        "managed_identity": {"project_id": "project-1", "model_tag": "M1",
                             "model_ref": dict(MODEL_REF), "expected_revision": 9},
        "status": "BMA_OUTPUT_PROBE_SOLVER_SEQUENCE_RETURNED_TWO_SOLUTION_ROWS",
        "native_result": "COMSOL_NATIVE_BMA_PRODUCER_RUN_READBACK",
        "study_or_solver_invoked": True, "solver_calls": 1, "study_run_calls": 0,
        "producer_status": "CONTROLLED_SINGLE_STEP_BMA_PRODUCER_VERIFIED",
        "field_mapping_status": "UNVERIFIED",
        "invocation": {"method": "SolverSequence.runAll",
            "solver_sequence_tag": "sol3dBmaProbe",
            "parent_study_tag": "std3dBmaOutputProbe",
            "parent_study_step_tag": "bmaOutputProbe", "method_returned": True},
        "probe_study": copy.deepcopy(preparation["probe_study"]),
        "solver_sequence": copy.deepcopy(preparation["solver_sequence"]),
        "pre_solve_solution_state": copy.deepcopy(preparation["pre_solve_solution_state"]),
        "post_solve_solution_state": {"is_valid": True, "solver_sequence_is_empty": False, "outer_solnums": [1],
            "solution_pairs": pairs, "pair_count": 2},
        "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
    }


def _bma_probe_run_request(preparation, *, revision=9):
    return build_full3d_bma_probe_run_dispatch(
        source_artifact="fixture-source",
        solver_sequence_tag=preparation["solver_sequence"]["tag"],
        project_id="project-1", model_ref=MODEL_REF, model_tag="M1", revision=revision,
        request_id="bma-run-request", idempotency_key="bma-run-idempotency")


def _bma_probe_public_route_result(readback, request):
    from comsol_mcp._execution_contract import canonical_request_hash

    submitted_request = copy.deepcopy(request)
    logical_execution = submitted_request["execution"]
    request_id = logical_execution["request_id"]
    idempotency_key = logical_execution["idempotency_key"]
    operation_instance_id = "op-bma-run"
    job_id = "job-bma-run"
    submitted_execution = {**submitted_request["execution"],
        "execution_timeout_s": 600.0, "queue_timeout_s": 60.0, "rpc_timeout_s": 30.0}
    submitted_request["execution"] = submitted_execution
    nested = submitted_request["arguments"]
    request_hash = canonical_request_hash(
        nested["operation_id"], nested["arguments"], submitted_execution["model_ref"],
        submitted_execution["expected_revision"],
        project_id=submitted_execution["project_id"],
        session_id=submitted_execution["session_id"],
        queue_timeout_s=submitted_execution["queue_timeout_s"],
        execution_timeout_s=submitted_execution["execution_timeout_s"],
        no_progress_warning_s=None)
    operation_response = {
        "success": True,
        "execution": {"session_id": "session-1", "model_ref": dict(MODEL_REF),
            "revision": logical_execution["expected_revision"] + 1,
            "dirty": False, "server_ownership": "mcp_managed",
            "model_ownership": "mcp_owned", "cas_limit": "managed revision is not a COMSOL cross-client atomic CAS",
            "request_id": request_id,
            "idempotency_key": idempotency_key, "operation_id": operation_instance_id,
            "request_hash": request_hash, "job_id": job_id},
        "error": None,
        "data": {
            "worker": {"ok": True, "status": "SUCCEEDED",
                       "result": {"readback": copy.deepcopy(readback)}},
            "readback": {"executed": True, "readback": copy.deepcopy(readback)},
        },
    }
    return {
        "outcome": "SUCCEEDED", "retry_forbidden": True, "job_id": job_id,
        "submitted_request": submitted_request,
        "dispatch_response": {"success": True, "data": {"job_id": job_id, "status": "QUEUED"},
            "execution": {"request_id": request_id,
                "idempotency_key": idempotency_key, "operation_id": operation_instance_id,
                "request_hash": request_hash, "job_id": job_id}},
        "job_wait_responses": [{"success": True, "data": {
            "job_id": job_id, "operation_id": operation_instance_id, "status": "SUCCEEDED",
            "metadata": {"operation": "operation_call", "arguments": {}, "execution": {}},
            "effective_timeouts": {"queue_timeout_s": 60.0, "execution_timeout_s": 600.0,
                                   "rpc_timeout_s": 30.0, "no_progress_warning_s": None},
            "result": operation_response, "operation": {
                "operation_id": operation_instance_id, "request_id": request_id,
                "idempotency_key": idempotency_key, "request_hash": request_hash,
                "operation": "operation_call", "status": "SUCCEEDED",
                "metadata": {"operation": "operation_call", "arguments": {}, "execution": {}},
                "effective_timeouts": {"queue_timeout_s": 60.0, "execution_timeout_s": 600.0,
                                       "rpc_timeout_s": 30.0, "no_progress_warning_s": None}}}}],
        "response": operation_response,
    }


def test_single_step_bma_probe_dispatches_are_separately_revision_bound():
    binding = {"project_id": "project-1", "model_ref": MODEL_REF,
               "model_tag": "M1", "revision": 8}
    prepare = build_full3d_bma_probe_prepare_dispatch(
        source_artifact="fixture-source", **binding,
        request_id="bma-prepare", idempotency_key="bma-prepare-key")
    assert prepare["operation"] == "operation_call"
    assert prepare["execution"]["expected_revision"] == 8
    assert prepare["arguments"]["arguments"]["arguments"]["phase"] == "prepare_bma_output_probe"
    assert prepare["study_or_solver_invoked"] is False
    assert prepare["native_result"] == "NOT_RUN"

    run = build_full3d_bma_probe_run_dispatch(
        source_artifact="fixture-source", solver_sequence_tag="sol3dBmaProbe",
        **{**binding, "revision": 9}, request_id="bma-run",
        idempotency_key="bma-run-key")
    java_args = run["arguments"]["arguments"]["arguments"]
    assert java_args["phase"] == "run_bma_output_probe"
    assert java_args["study_tag"] == "std3dBmaOutputProbe"
    assert java_args["solver_sequence_tag"] == "sol3dBmaProbe"
    assert run["execution"]["expected_revision"] == 9
    assert run["study_or_solver_invoked"] is False
    assert run["planned_solver_calls"] == 1
    assert run["native_result"] == "NOT_RUN"


def test_isolated_bma_prepare_and_run_verifies_producer_only_after_solution_readback():
    preparation = _validated_bma_probe_preparation()
    readback = _bma_probe_run_readback(preparation)
    request = _bma_probe_run_request(preparation)
    route_result = _bma_probe_public_route_result(readback, request)
    assert preparation["status"] == "ISOLATED_BMA_SEQUENCE_PREPARED_NOT_SOLVED"
    assert preparation["producer_status"].startswith("UNVERIFIED")
    result = validate_full3d_bma_probe_run_readback(
        readback, preparation=preparation, project_id="project-1", model_tag="M1",
        model_ref=MODEL_REF, run_request=request, route_result=route_result)
    assert result["producer_step_binding"].startswith("VERIFIED_BY_ISOLATED_ONE_STEP")
    assert result["eigensolution_row_count"] == 2
    assert result["managed_request_id"] == "bma-run-request"
    assert result["managed_idempotency_key"] == "bma-run-idempotency"
    assert result["managed_job_id"] == "job-bma-run"
    assert [row["inner_index"] for row in result["eigensolution_solution_pairs"]] == [1, 2]
    assert result["numeric_port_mode_field_mapping"] == "UNVERIFIED"
    assert result["basis_ordinal_assignment"] == "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED"


@pytest.mark.parametrize("mutation", [
    "missing_step_binding", "duplicate_step_binding", "foreign_step_binding",
    "missing_probe_study_step", "duplicate_probe_study_step", "foreign_probe_study_step",
    "empty_solver_tree", "configuration_only_tree", "missing_variables",
    "missing_eigenvalue", "missing_store_solution", "solution_present_before_run",
])
def test_bma_probe_preparation_fails_closed_on_incomplete_or_ambiguous_evidence(mutation):
    readback = _bma_probe_prepared_readback()
    if mutation == "missing_step_binding":
        readback["solver_sequence"]["study_step_bindings_in_solver_tree_order"] = []
    elif mutation == "duplicate_step_binding":
        readback["solver_sequence"]["study_step_bindings_in_solver_tree_order"].append(
            copy.deepcopy(readback["solver_sequence"]["study_step_bindings_in_solver_tree_order"][0]))
    elif mutation == "foreign_step_binding":
        readback["solver_sequence"]["study_step_bindings_in_solver_tree_order"][0]["study"] = "std3d"
    elif mutation == "missing_probe_study_step":
        readback["probe_study"]["study_steps"] = []
    elif mutation == "duplicate_probe_study_step":
        readback["probe_study"]["study_steps"].append(
            copy.deepcopy(readback["probe_study"]["study_steps"][0]))
    elif mutation == "foreign_probe_study_step":
        readback["probe_study"]["study_steps"][0]["PortName"] = "1"
    elif mutation == "empty_solver_tree":
        readback["solver_sequence"]["solver_tree_features"] = []
    elif mutation == "configuration_only_tree":
        readback["solver_sequence"]["solver_tree_features"] = [
            {"path": "st1", "feature_type": "StudyStep"}]
    elif mutation.startswith("missing_") and mutation in {
            "missing_variables", "missing_eigenvalue", "missing_store_solution"}:
        feature_type = {"missing_variables": "Variables", "missing_eigenvalue": "Eigenvalue",
                        "missing_store_solution": "StoreSolution"}[mutation]
        readback["solver_sequence"]["solver_tree_features"] = [
            row for row in readback["solver_sequence"]["solver_tree_features"]
            if row["feature_type"] != feature_type]
    else:
        readback["pre_solve_solution_state"] = {"is_valid": True, "solver_sequence_is_empty": False,
            "outer_solnums": [1], "solution_pairs": [{"outer_index": 1,
            "inner_index": 1, "solnum": 1, "solver_sequence_tag": "sol3dBmaProbe"}],
            "pair_count": 1}
    with pytest.raises(Full3DScienceError):
        validate_full3d_bma_probe_preparation(
            readback, project_id="project-1", model_tag="M1", model_ref=MODEL_REF)


@pytest.mark.parametrize("mutation", [
    "configuration_only", "empty_solution", "duplicate_pair", "foreign_sequence",
    "repeated_inner_across_outer", "multiple_outer_axis",
    "tree_changed_after_prepare", "wrong_invocation", "missing_public_job",
    "dispatch_job_mismatch", "terminal_job_mismatch", "wrong_request_revision",
    "wrong_request_sequence", "terminal_readback_mismatch", "foreign_request_id",
    "foreign_idempotency_key", "initial_operation_mismatch", "final_operation_mismatch",
    "foreign_job", "missing_initial_request_id", "missing_initial_idempotency_key",
    "missing_initial_operation_id", "missing_initial_job_id", "missing_initial_request_hash",
    "missing_terminal_request_id", "missing_terminal_idempotency_key",
    "missing_terminal_operation_id", "missing_terminal_job_id", "missing_terminal_request_hash",
    "missing_stored_operation", "missing_stored_request_id", "missing_stored_idempotency_key",
    "missing_stored_operation_id", "missing_stored_request_hash", "request_hash_mismatch",
    "revision_skip",
])
def test_bma_probe_run_never_promotes_configuration_or_incomplete_solution_to_producer(mutation):
    preparation = _validated_bma_probe_preparation()
    readback = _bma_probe_run_readback(preparation)
    request = _bma_probe_run_request(preparation)
    route_result = _bma_probe_public_route_result(readback, request)
    if mutation == "configuration_only":
        readback["study_or_solver_invoked"] = False
        readback["solver_calls"] = 0
    elif mutation == "empty_solution":
        readback["status"] = "BMA_OUTPUT_PROBE_READBACK_INCOMPLETE"
        readback["producer_status"] = "UNVERIFIED_SOLUTION_COUNT_OR_AXIS_READBACK"
        readback["post_solve_solution_state"] = {"is_valid": True, "solver_sequence_is_empty": True,
            "outer_solnums": [], "solution_pairs": [], "pair_count": 0}
    elif mutation == "duplicate_pair":
        readback["post_solve_solution_state"]["solution_pairs"][1]["inner_index"] = 1
        readback["post_solve_solution_state"]["solution_pairs"][1]["solnum"] = 1
    elif mutation == "repeated_inner_across_outer":
        readback["post_solve_solution_state"]["outer_solnums"] = [1, 2]
        readback["post_solve_solution_state"]["solution_pairs"][1]["outer_index"] = 2
        readback["post_solve_solution_state"]["solution_pairs"][1]["inner_index"] = 1
        readback["post_solve_solution_state"]["solution_pairs"][1]["solnum"] = 1
    elif mutation == "multiple_outer_axis":
        readback["post_solve_solution_state"]["outer_solnums"] = [1, 2]
        readback["post_solve_solution_state"]["solution_pairs"][1]["outer_index"] = 2
    elif mutation == "foreign_sequence":
        readback["post_solve_solution_state"]["solution_pairs"][0]["solver_sequence_tag"] = "solOther"
    elif mutation == "missing_public_job":
        route_result["job_id"] = None
    elif mutation == "dispatch_job_mismatch":
        route_result["dispatch_response"]["execution"]["job_id"] = "job-other"
    elif mutation == "terminal_job_mismatch":
        route_result["job_wait_responses"][0]["data"]["job_id"] = "job-other"
    elif mutation == "foreign_job":
        route_result["job_id"] = "job-foreign"
    elif mutation == "wrong_request_revision":
        request["execution"]["expected_revision"] = 8
    elif mutation == "wrong_request_sequence":
        request["arguments"]["arguments"]["arguments"]["solver_sequence_tag"] = "solOther"
    elif mutation == "terminal_readback_mismatch":
        route_result["response"]["data"]["readback"]["readback"]["invocation"]["method"] = "Study.run"
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "foreign_request_id":
        request["execution"]["request_id"] = "foreign-request"
    elif mutation == "foreign_idempotency_key":
        request["execution"]["idempotency_key"] = "foreign-idempotency"
    elif mutation == "initial_operation_mismatch":
        route_result["dispatch_response"]["execution"]["operation_id"] = "op-other"
    elif mutation == "final_operation_mismatch":
        route_result["response"]["execution"]["operation_id"] = "op-other"
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "missing_initial_request_id":
        del route_result["dispatch_response"]["execution"]["request_id"]
    elif mutation == "missing_initial_idempotency_key":
        del route_result["dispatch_response"]["execution"]["idempotency_key"]
    elif mutation == "missing_initial_operation_id":
        del route_result["dispatch_response"]["execution"]["operation_id"]
    elif mutation == "missing_initial_job_id":
        del route_result["dispatch_response"]["execution"]["job_id"]
    elif mutation == "missing_initial_request_hash":
        del route_result["dispatch_response"]["execution"]["request_hash"]
    elif mutation == "missing_terminal_request_id":
        del route_result["response"]["execution"]["request_id"]
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "missing_terminal_idempotency_key":
        del route_result["response"]["execution"]["idempotency_key"]
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "missing_terminal_operation_id":
        del route_result["job_wait_responses"][0]["data"]["operation_id"]
    elif mutation == "missing_terminal_job_id":
        del route_result["response"]["execution"]["job_id"]
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "missing_terminal_request_hash":
        del route_result["response"]["execution"]["request_hash"]
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "missing_stored_operation":
        del route_result["job_wait_responses"][0]["data"]["operation"]
    elif mutation == "missing_stored_request_id":
        del route_result["job_wait_responses"][0]["data"]["operation"]["request_id"]
    elif mutation == "missing_stored_idempotency_key":
        del route_result["job_wait_responses"][0]["data"]["operation"]["idempotency_key"]
    elif mutation == "missing_stored_operation_id":
        del route_result["job_wait_responses"][0]["data"]["operation"]["operation_id"]
    elif mutation == "missing_stored_request_hash":
        del route_result["job_wait_responses"][0]["data"]["operation"]["request_hash"]
    elif mutation == "request_hash_mismatch":
        route_result["response"]["execution"]["request_hash"] = "0" * 64
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "revision_skip":
        route_result["response"]["execution"]["revision"] = 11
        route_result["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(route_result["response"])
    elif mutation == "tree_changed_after_prepare":
        readback["solver_sequence"]["solver_tree_features"].append(
            {"path": "freq1", "feature_type": "Frequency"})
    else:
        readback["invocation"]["method"] = "Study.run"
    with pytest.raises(Full3DScienceError):
        validate_full3d_bma_probe_run_readback(
            readback, preparation=preparation,
            project_id="project-1", model_tag="M1", model_ref=MODEL_REF,
            run_request=request, route_result=route_result)


def _bma_basis_mapping_bundle():
    preparation = _validated_bma_probe_preparation()
    readback = _bma_probe_run_readback(preparation)
    request = _bma_probe_run_request(preparation)
    route_result = _bma_probe_public_route_result(readback, request)
    producer = validate_full3d_bma_probe_run_readback(
        readback, preparation=preparation, project_id="project-1", model_tag="M1",
        model_ref=MODEL_REF, run_request=request, route_result=route_result)
    rows = [{"tag": "dset_bma_probe", "type_id": "Solution",
             "solution": "sol3dBmaProbe", "component": "comp3d", "geometry": "geom3d"}]
    index = {"dataset": "dset_bma_probe", "solution": "sol3dBmaProbe",
        "binding_complete": True, "axis_metadata_complete": True,
        "pair_mapping_complete": True, "parameters_complete": True,
        "outer_indices": [1], "inner_indices": [1, 2], "solution_count": 2,
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1},
                         {"outer": 1, "inner": 2, "solnum": 2}],
        "parameters": {"by_pair": {
            "1:1": {"solnum": 1, "names": ["freq", "modeIndex"],
                    "values": [193.414489032258, 1], "units": ["THz", "1"]},
            "1:2": {"solnum": 2, "names": ["freq", "modeIndex"],
                    "values": [193.414489032258, 2], "units": ["THz", "1"]},
        }}}
    binding = resolve_full3d_bma_basis_sources(
        rows, {"dset_bma_probe": index}, producer_evidence=producer)
    contracts = build_full3d_bma_basis_mapping_contracts(
        case=CASE, preparation=preparation, producer_evidence=producer,
        basis_binding=binding, plane=OUTPUT_PLANE,
        radial_intervals=4, angular_points=8)
    quadratures = [circular_port_quadrature(
        row["plane"]["center_xyz_m"], row["plane"]["axis_xyz"],
        row["plane"]["sample_radius_m"], radial_intervals=4, angular_points=8)
        for row in contracts]
    return preparation, producer, binding, contracts, quadratures


def _bma_basis_raw(contract, quadrature, *, basis_e, basis_h,
                   port_e=(0j, 1 + 0j, 0j), port_h=(0j, 0j, 1 + 0j)):
    count = len(quadrature["coordinates_m"])
    normal = contract["plane"]["native_normal_sign"]
    values = {
        "ewfd.Ex": basis_e[0], "ewfd.Ey": basis_e[1], "ewfd.Ez": basis_e[2],
        "ewfd.Hx": basis_h[0], "ewfd.Hy": basis_h[1], "ewfd.Hz": basis_h[2],
        "ewfd.Emodex_2": port_e[0], "ewfd.Emodey_2": port_e[1], "ewfd.Emodez_2": port_e[2],
        "ewfd.Hmodex_2": port_h[0], "ewfd.Hmodey_2": port_h[1], "ewfd.Hmodez_2": port_h[2],
        "nx": complex(normal, 0), "ny": 0j, "nz": 0j,
    }
    unit_by_name = {
        **{name: ("electric", "V/m") for name in (
            "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2")},
        **{name: ("magnetic", "A/m") for name in (
            "ewfd.Hx", "ewfd.Hy", "ewfd.Hz", "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2")},
        **{name: ("normal", "1") for name in ("nx", "ny", "nz")},
    }
    rows = []
    for name in contract["field_names"]:
        value = values[name]
        rows.append({"expression": name, "real": [complex(value).real] * count,
                     "imag": [complex(value).imag] * count,
                     "unit_group": unit_by_name[name][0], "unit": unit_by_name[name][1]})
    return {"native_result": "COMSOL_NATIVE_RAW", "study_or_solver_invoked": False,
        "complex_readback": True, "units_preserved": True,
        "contract_id": contract["contract_id"],
        "quadrature_sha256": contract["quadrature_sha256"],
        "source": contract["source"], "plane": contract["plane"],
        "basis_axis": contract["basis_axis"], "port_mode_axis": contract["port_mode_axis"],
        "sample_count": count,
        "coordinates_m": [[point[axis] for point in quadrature["coordinates_m"]]
                           for axis in range(3)],
        "unit_readback": {key: value["unit"] for key, value in contract["unit_groups"].items()},
        "expressions": rows,
        "source_cohort": _cohort_snapshot(contract),
        "cleanup": {"created_count": 3, "expected_count": 3,
                    "removed": True, "cleanup_failed": False,
                    "tags": ["w23bm" + contract["contract_id"][:10] + suffix
                             for suffix in ("e", "h", "n")], "error": ""},
        "field_mapping_status": "UNVERIFIED",
        "basis_ordinal_mapping": "UNVERIFIED_NATIVE_FIELD_MAPPING_REQUIRED",
        "numeric_port_mode_field_mapping": "UNVERIFIED"}


def _bma_mapping_route_result(request, *, label, response_data, revision_delta):
    """Build the durable public ControlDaemon/OperationStore envelope shape."""
    import uuid
    from comsol_mcp._execution_contract import canonical_request_hash

    nested = copy.deepcopy(request["arguments"])
    logical = request["execution"]
    timeout = 90.0 if "dataset" in label else 180.0
    bounded_execution = {**logical, "execution_timeout_s": timeout,
                         "queue_timeout_s": min(60.0, timeout),
                         "rpc_timeout_s": min(30.0, timeout)}
    submitted = {**request, "arguments": nested, "execution": bounded_execution}
    operation_id = nested["operation_id"]
    operation_args = nested["arguments"]
    request_hash = canonical_request_hash(
        operation_id, operation_args, logical["model_ref"], logical["expected_revision"],
        project_id=logical["project_id"], session_id=logical["session_id"],
        queue_timeout_s=bounded_execution["queue_timeout_s"],
        execution_timeout_s=bounded_execution["execution_timeout_s"],
        no_progress_warning_s=None)
    operation_instance_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    response_execution = {
        "session_id": logical["session_id"], "model_ref": copy.deepcopy(logical["model_ref"]),
        "revision": logical["expected_revision"] + revision_delta,
        "dirty": False, "server_ownership": "mcp_managed", "model_ownership": "mcp_owned",
        "cas_limit": "managed revision is not a COMSOL cross-client atomic CAS",
        "request_id": logical["request_id"], "idempotency_key": logical["idempotency_key"],
        "operation_id": operation_instance_id, "request_hash": request_hash, "job_id": job_id,
    }
    response = {"success": True, "data": copy.deepcopy(response_data),
                "execution": response_execution, "error": None}
    operation_metadata = {"operation": "operation_call", "arguments": nested,
                          "execution": bounded_execution}
    stored_operation = {
        "operation_id": operation_instance_id, "request_id": logical["request_id"],
        "idempotency_key": logical["idempotency_key"], "request_hash": request_hash,
        "operation": "operation_call", "status": "SUCCEEDED",
        "metadata": operation_metadata, "effective_timeouts": {
            "execution_timeout_s": timeout, "queue_timeout_s": min(60.0, timeout),
            "rpc_timeout_s": min(30.0, timeout), "no_progress_warning_s": None},
    }
    job = {"job_id": job_id, "operation_id": operation_instance_id, "status": "SUCCEEDED",
           "metadata": copy.deepcopy(operation_metadata), "result": response,
           "operation": stored_operation}
    initial = {"success": True, "data": {"job_id": job_id, "status": "QUEUED"},
               "execution": {"request_id": logical["request_id"],
                             "idempotency_key": logical["idempotency_key"],
                             "operation_id": operation_instance_id,
                             "request_hash": request_hash, "job_id": job_id}}
    return {"outcome": "SUCCEEDED", "retry_forbidden": True, "job_id": job_id,
            "submitted_request": submitted, "dispatch_response": initial,
            "job_wait_responses": [{"success": True, "data": job}], "response": response}


def _mesh_stability_route_bundle(monkeypatch):
    import copy as copy_module

    from comsol_mcp._w23_basis_v2_results import build_definition
    from tests.test_w23_mode_basis_v2 import _request as base_v2_request
    from tests.test_w23_mode_basis_v2_results import (
        _install_native_test_doubles, _invoke,
    )
    from tools.w23_full3d_science import (
        build_full3d_convergence_recipe, build_full3d_study_run_dispatch,
    )
    from tools.w23_mode_basis_v2 import build_two_mode_basis_request

    recipe = build_full3d_convergence_recipe(canonical_full3d_recipe())
    preparation, _producer, _binding, contracts, quadratures = _bma_basis_mapping_bundle()
    template = base_v2_request()
    frame_id = template["coordinate_frame"]
    frequency_hz = template["frequency_hz"]
    mesh_runs = []
    native_results = []
    for level_index, level in enumerate(recipe["mesh_levels"], start=1):
        model_ref = {**MODEL_REF, "model_tag": f"M{level_index}", "generation": level_index}
        project_id = "project-1"
        model_tag = model_ref["model_tag"]

        def source(template_source, *, dataset, solution, port, inner=1, solnum=1,
                   mode_index=None):
            result = copy_module.deepcopy(template_source)
            result.update({
                "dataset_id": dataset, "solution_id": solution, "outer_index": 1,
                "inner_index": inner, "solnum": solnum, "port_id": port,
                "project_id": project_id, "model_ref": dict(model_ref),
                "model_revision": 3, "geometry_revision": 0,
                "coordinate_frame": frame_id, "frequency_hz": frequency_hz,
            })
            if mode_index is not None:
                result["mode_index"] = mode_index
            else:
                result.pop("mode_index", None)
            return result

        basis_request = build_two_mode_basis_request(
            basis_id="w23-two-eigensolution-test-basis", case=CASE,
            project_id=project_id, model_ref=model_ref, model_revision=3,
            geometry_revision=0, frequency_hz=frequency_hz,
            coordinate_frame=frame_id,
            output_surface=copy_module.deepcopy(template["output_surface"]),
            input_surface=copy_module.deepcopy(template["input_surface"]),
            signal_source=source(template["signal_source"], dataset="dset_std3d_freq",
                                 solution="sol3dStdFrequency", port="2"),
            mode_sources=[
                {"mode_id": "bma-eigensolution-1", "mode_index": 1,
                 "source": source(template["basis_modes"][0]["source"],
                     dataset="dset_bma_probe", solution="sol3dBmaProbe", port="2",
                     inner=1, solnum=1, mode_index=1)},
                {"mode_id": "bma-eigensolution-2", "mode_index": 2,
                 "source": source(template["basis_modes"][1]["source"],
                     dataset="dset_bma_probe", solution="sol3dBmaProbe", port="2",
                     inner=2, solnum=2, mode_index=2)},
            ],
            incident_source=source(template["incident_source"], dataset="dset_std3d_input",
                                   solution="sol3dStdInput", port="1"),
            applicability=template["applicability"],
            radial_intervals=4, angular_points=8,
        )
        definition = build_definition(basis_request, [
            {"mode_id": row["mode_id"], "parameter": "lambda"}
            for row in basis_request["basis_modes"]])
        _install_native_test_doubles(monkeypatch, basis_request)
        native_result = _invoke(
            basis_request, definition, model_tag=model_tag, project_id=project_id,
            model_ref=model_ref, revision=3)
        native_results.append(native_result)

        fixture_recipe = canonical_full3d_recipe()
        build_request = build_full3d_mesh_level_fixture_dispatch(
            recipe, source_artifact="NativeW23Full3DFixture.java",
            project_id=project_id, model_ref=model_ref, model_tag=model_tag,
            revision=0, request_id=f"{level['level_id']}-build-request",
            idempotency_key=f"{level['level_id']}-build-idem",
            mesh_level_id=level["level_id"], mesh_scale_factor=level["scale_factor"])
        elements = 1024 * level_index
        size_properties = {"properties": ["custom", "hmax", "hmin"],
            "requested_properties": {
                "custom": {"has_property_exact": True, "string_readback": "on"},
                "hmax": {"has_property_exact": True,
                         "string_readback": level["hmax_expression"]},
                "hmin": {"has_property_exact": True,
                         "string_readback": level["hmin_expression"]},
            }}
        build_readback = {
            "fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
            "recipe_sha256": fixture_recipe["recipe_sha256"],
            "status": "BUILT_CONFIGURED_NOT_SOLVED", "native_result": "NOT_RUN",
            "study_or_solver_invoked": False,
            "managed_identity": {"project_id": project_id, "model_ref": dict(model_ref),
                "model_tag": model_tag, "expected_revision": 0},
            "mesh": {"tag": "mesh3d", "geometry": "geom3d",
                "mesh_level_id": level["level_id"], "mesh_scale_factor": level["scale_factor"],
                "hmax": level["hmax_expression"], "hmin": level["hmin_expression"],
                "elements": elements, "size_properties": size_properties},
        }
        build_response_data = {
            "worker": {"ok": True, "status": "SUCCEEDED",
                       "result": {"readback": copy_module.deepcopy(build_readback)}},
            "readback": {"executed": True, "readback": copy_module.deepcopy(build_readback)},
        }
        build_route = _bma_mapping_route_result(
            build_request, label="fixture-build", response_data=build_response_data,
            revision_delta=1)

        study_request = build_full3d_study_run_dispatch(
            project_id=project_id, model_ref=model_ref, model_tag=model_tag,
            revision=1, request_id=f"{level['level_id']}-study-request",
            idempotency_key=f"{level['level_id']}-study-idem", timeout_s=600)
        study_route = _bma_mapping_route_result(
            study_request, label="std3d", response_data={"status": "SUCCEEDED"}, revision_delta=1)
        field_request = build_full3d_bma_basis_mapping_dispatch(
            contracts[0], source_artifact="NativeW23Full3DFixture.java",
            quadrature=quadratures[0], project_id=project_id, model_ref=model_ref,
            model_tag=model_tag, revision=2,
            request_id=f"{level['level_id']}-field-request",
            idempotency_key=f"{level['level_id']}-field-idem")
        field_readback = _bma_basis_raw(
            contracts[0], quadratures[0], basis_e=(0j, 1 + 0j, 0j),
            basis_h=(0j, 0j, 1 + 0j))
        mesh = field_readback["source_cohort"]["before"]["fixture_explicit_configuration"]["mesh"]
        mesh["elements"] = elements
        properties = mesh["size_properties"]["requested_properties"]
        properties["hmax"]["string_readback"] = level["hmax_expression"]
        properties["hmin"]["string_readback"] = level["hmin_expression"]
        field_readback["source_cohort"]["after"] = copy_module.deepcopy(
            field_readback["source_cohort"]["before"])
        field_response_data = {
            "worker": {"ok": True, "status": "SUCCEEDED",
                       "result": {"readback": copy_module.deepcopy(field_readback)}},
            "readback": {"executed": True, "readback": copy_module.deepcopy(field_readback)},
        }
        field_route = _bma_mapping_route_result(
            field_request, label="mesh-probe", response_data=field_response_data, revision_delta=1)

        overlap_request = {
            "operation": "operation_call",
            "arguments": {"operation_id": "result.mode_overlap_basis_v2",
                          "arguments": {"definition": definition}},
            "execution": {"project_id": project_id, "session_id": model_ref["session_id"],
                          "model_ref": dict(model_ref), "expected_revision": 3,
                          "request_id": f"{level['level_id']}-overlap-request",
                          "idempotency_key": f"{level['level_id']}-overlap-idem"},
        }
        overlap_route = _bma_mapping_route_result(
            overlap_request, label="registered-overlap", response_data=native_result,
            revision_delta=0)
        mesh_runs.append({
            "mesh_level_id": level["level_id"],
            "build_request": build_request, "build_route_result": build_route,
            "build_max_execution_timeout_s": 180,
            "study_request": study_request, "study_route_result": study_route,
            "study_max_execution_timeout_s": 180,
            "mesh_probe_request": field_request, "mesh_probe_route_result": field_route,
            "mesh_probe_max_execution_timeout_s": 180,
            "overlap_request": overlap_request, "overlap_route_result": overlap_route,
            "overlap_max_execution_timeout_s": 180,
            "caller_delta": -999, "caller_status": "PASS",
        })
    return recipe, mesh_runs, native_results


def _bma_receiver_readback():
    section = {
        "evidence_scope": "COMSOL_NATIVE_GEOMETRY_READBACK", "tag": "sel3dOutputPort",
        "selection_type": "Cylinder", "entity_dimension": 2, "coordinate_unit": "um",
        "normal_basis": "global_xyz", "entity_ids": [17],
        "selection_axis_xyz": [1.0, 0.0, 0.0], "selection_center_um": [20.0, 0.0, 0.0],
        "nominal_aperture_radius_um": 2.52, "selection_radius_um": 2.52,
        "selection_margin_um": 0.0, "area_um2": math.pi * 2.52**2,
        "expected_circle_area_um2": math.pi * 2.52**2, "area_relative_error": 0.0,
        "native_face_oriented_axis_sign": 1,
        "faces": [{"boundary_id": 17, "unit_normal_xyz": [1.0, 0.0, 0.0], "axis_dot": 1.0}],
    }
    return {"receiver_port_section": section,
            "receiver_transform": {"axis_xyz": [1.0, 0.0, 0.0],
                                   "center_xyz_um": [20.0, 0.0, 0.0]}}


def test_full3d_mesh_stability_recipe_recomputes_metrics_from_terminal_managed_routes(monkeypatch):
    recipe, mesh_runs, _native_results = _mesh_stability_route_bundle(monkeypatch)

    result = recompute_full3d_mesh_stability_from_registered_routes(recipe, mesh_runs)

    assert [row["hmax_expression"] for row in recipe["mesh_levels"]] == [
        "lambda0/(5*w23Nlens)",
        "(lambda0/(5*w23Nlens))*0.8",
        "(lambda0/(5*w23Nlens))*0.64",
    ]
    assert [row["hmin_expression"] for row in recipe["mesh_levels"]] == [
        "lambda0/(12*w23Nlens)",
        "(lambda0/(12*w23Nlens))*0.8",
        "(lambda0/(12*w23Nlens))*0.64",
    ]
    assert recipe["study_or_solver_invoked"] is False
    assert result["caller_metrics_or_deltas_accepted"] is False
    assert result["basis_and_signed_power_thresholds"] == "WITHIN_LIMITS"
    assert result["adjacent_deltas"][0]["eta_basis_absolute_delta"] == pytest.approx(0)
    assert result["adjacent_deltas"][0]["signed_signal_power_over_pin_absolute_delta"] == pytest.approx(0)
    assert result["adjacent_deltas"][0]["basis_diagonal_power_used_as_gate"] is False
    assert result["basis_diagonal_power_diagnostic"].startswith(
        "G00_AND_G11_OVER_PIN_ARE_GAUGE_DEPENDENT")
    assert result["eta_capture_convergence"] == "NOT_RUN_MISSING_ROUTE_BOUND_CORE_CAPTURE_INTEGRALS"
    assert result["quadrature_convergence"] == "NOT_RUN_MISSING_ROUTE_BOUND_THREE_LEVEL_RAW_FIELD_SERIES"
    assert result["power_balance"] == "NOT_RUN_MISSING_NATIVE_CLOSED_CONTROL_VOLUME_READBACK"
    assert result["producer_step_solution_lineage"] == "UNVERIFIED"
    assert result["numeric_port_mode_field_mapping"] == "UNVERIFIED"
    assert result["native_convergence_status"] == "NOT_RUN"
    assert result["scientific_acceptance"] == "NOT_RUN"


def test_full3d_mesh_stability_gate_is_invariant_to_independent_basis_rescaling(monkeypatch):
    recipe, mesh_runs, native_results = _mesh_stability_route_bundle(monkeypatch)
    route = mesh_runs[1]["overlap_route_result"]
    result = route["response"]["data"]
    terms = result["native_integrals"]["terms"]
    scales = (10.0 + 0j, 0.1 + 0j)
    for i in range(2):
        for j in range(2):
            term = terms[f"G{i}{j}"]
            value = complex(term["real"], term["imag"]) * scales[i].conjugate() * scales[j]
            term["real"], term["imag"] = value.real, value.imag
        term = terms[f"b{i}"]
        value = complex(term["real"], term["imag"]) * scales[i].conjugate()
        term["real"], term["imag"] = value.real, value.imag
    result["native_integrals"]["gram_matrix"] = [
        [copy.deepcopy(terms["G00"]), copy.deepcopy(terms["G01"])],
        [copy.deepcopy(terms["G10"]), copy.deepcopy(terms["G11"])],
    ]
    result["native_integrals"]["coupling_vector"] = [
        copy.deepcopy(terms["b0"]), copy.deepcopy(terms["b1"])]
    route["job_wait_responses"][0]["data"]["result"]["data"] = copy.deepcopy(result)

    recomputed = recompute_full3d_mesh_stability_from_registered_routes(recipe, mesh_runs)

    assert recomputed["basis_and_signed_power_thresholds"] == "WITHIN_LIMITS"
    assert recomputed["adjacent_deltas"][0]["eta_basis_absolute_delta"] == pytest.approx(0, abs=1e-12)
    diagnostic = recomputed["adjacent_deltas"][0][
        "basis_diagonal_power_over_pin_gauge_dependent_diagnostic_deltas"]
    assert max(diagnostic) > 1.0


def test_full3d_mesh_stability_recomputes_signed_power_delta_from_terminal_terms(monkeypatch):
    recipe, mesh_runs, _native_results = _mesh_stability_route_bundle(monkeypatch)
    route = mesh_runs[1]["overlap_route_result"]
    result = route["response"]["data"]
    signal_term = result["native_integrals"]["terms"]["P_signal"]
    signal_term["real"] *= 1.02
    result["native_integrals"]["signal_power"] = copy.deepcopy(signal_term)
    route["job_wait_responses"][0]["data"]["result"]["data"] = copy.deepcopy(result)

    checked = recompute_full3d_mesh_stability_from_registered_routes(recipe, mesh_runs)

    assert checked["adjacent_deltas"][0]["signed_signal_power_over_pin_absolute_delta"] > 1e-3
    assert checked["basis_and_signed_power_thresholds"] == "LIMIT_EXCEEDED"
    assert checked["native_convergence_status"] == "NOT_RUN"


@pytest.mark.parametrize("mutation", [
    "study_revision_skip", "probe_on_stale_revision", "mesh_expression_mismatch",
    "mesh_setup_scale_mismatch", "mesh_setup_revision_skip", "study_after_setup_stale",
    "mesh_setup_property_unreadable", "postsolve_mesh_drift", "foreign_study_selection",
    "duplicate_model_ref", "foreign_overlap_model", "caller_fake_deltas",
])
def test_full3d_mesh_stability_route_chain_rejects_detached_or_inconsistent_sources(monkeypatch, mutation):
    recipe, mesh_runs, _native_results = _mesh_stability_route_bundle(monkeypatch)
    rows = copy.deepcopy(mesh_runs)
    if mutation == "study_revision_skip":
        rows[0]["study_route_result"]["response"]["execution"]["revision"] += 1
        rows[0]["study_route_result"]["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(
            rows[0]["study_route_result"]["response"])
    elif mutation == "probe_on_stale_revision":
        rows[1]["mesh_probe_request"]["execution"]["expected_revision"] = 0
    elif mutation == "mesh_expression_mismatch":
        response_data = rows[2]["mesh_probe_route_result"]["response"]["data"]
        changed = response_data["readback"]["readback"]
        for phase in ("before", "after"):
            changed["source_cohort"][phase]["fixture_explicit_configuration"]["mesh"][
                "size_properties"]["requested_properties"]["hmax"]["string_readback"] = "lambda0/(9*w23Nlens)"
        response_data["worker"]["result"]["readback"] = copy.deepcopy(changed)
        rows[2]["mesh_probe_route_result"]["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(
            rows[2]["mesh_probe_route_result"]["response"])
    elif mutation == "mesh_setup_scale_mismatch":
        rows[1]["build_request"]["arguments"]["arguments"]["arguments"]["mesh_scale_factor"] = 0.7
    elif mutation == "mesh_setup_revision_skip":
        rows[0]["build_route_result"]["response"]["execution"]["revision"] += 1
        rows[0]["build_route_result"]["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(
            rows[0]["build_route_result"]["response"])
    elif mutation == "study_after_setup_stale":
        rows[1]["study_request"]["execution"]["expected_revision"] = 0
    elif mutation == "mesh_setup_property_unreadable":
        response_data = rows[1]["build_route_result"]["response"]["data"]
        readback = response_data["readback"]["readback"]
        readback["mesh"]["size_properties"]["requested_properties"]["hmin"]["has_property_exact"] = False
        response_data["worker"]["result"]["readback"] = copy.deepcopy(readback)
        rows[1]["build_route_result"]["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(
            rows[1]["build_route_result"]["response"])
    elif mutation == "postsolve_mesh_drift":
        response_data = rows[1]["mesh_probe_route_result"]["response"]["data"]
        readback = response_data["readback"]["readback"]
        for phase in ("before", "after"):
            readback["source_cohort"][phase]["fixture_explicit_configuration"]["mesh"][
                "size_properties"]["requested_properties"]["hmin"]["string_readback"] = "lambda0/(12*w23Nlens)"
        response_data["worker"]["result"]["readback"] = copy.deepcopy(readback)
        rows[1]["mesh_probe_route_result"]["job_wait_responses"][0]["data"]["result"] = copy.deepcopy(
            rows[1]["mesh_probe_route_result"]["response"])
    elif mutation == "foreign_study_selection":
        rows[2]["study_request"]["arguments"]["arguments"]["study"] = {
            "segments": [{"collection": "study", "tag": "std3dBmaOutputProbe"}]}
    elif mutation == "duplicate_model_ref":
        rows[2]["study_request"]["execution"]["model_ref"] = copy.deepcopy(
            rows[1]["study_request"]["execution"]["model_ref"])
    elif mutation == "foreign_overlap_model":
        rows[1]["overlap_request"]["execution"]["model_ref"]["generation"] += 1
    elif mutation == "caller_fake_deltas":
        for row in rows:
            row["eta_basis_delta"] = 0.0
            row["signal_power_delta"] = 0.0
            row["status"] = "PASS"
        # Caller assertions do not become inputs, so the route-bound result remains computed.
        checked = recompute_full3d_mesh_stability_from_registered_routes(recipe, rows)
        assert checked["caller_metrics_or_deltas_accepted"] is False
        assert checked["adjacent_deltas"][0]["eta_basis_absolute_delta"] == pytest.approx(0)
        return
    with pytest.raises(Full3DScienceError):
        recompute_full3d_mesh_stability_from_registered_routes(recipe, rows)


def test_bma_basis_mapping_keeps_numeric_port_and_eigensolution_axes_separate_and_unverified():
    _, producer, binding, contracts, quadratures = _bma_basis_mapping_bundle()
    assert binding["basis_sources"][0]["basis_ordinal"] == 1
    assert binding["basis_sources"][1]["basis_ordinal"] == 2
    assert binding["basis_sources"][0]["source"]["inner_index"] != binding["basis_sources"][1]["source"]["inner_index"]
    assert producer["numeric_port_mode_field_mapping"] == "UNVERIFIED"
    assert contracts[0]["basis_axis"]["ordinal"] == 1
    assert contracts[1]["basis_axis"]["ordinal"] == 2
    assert all(row["port_mode_axis"]["port_name"] == "2"
               and row["port_mode_axis"]["port_mode_number_readback"] == "1"
               for row in contracts)
    assert all(row["field_suffix_semantics"]["claim"].endswith("not the BMA eigensolution ordinal.")
               for row in (contracts[0]["port_mode_axis"],))
    assert contracts[0]["quadrature_sha256"] == contracts[1]["quadrature_sha256"]
    assert contracts[0]["field_names"] == [
        "ewfd.Ex", "ewfd.Ey", "ewfd.Ez", "ewfd.Hx", "ewfd.Hy", "ewfd.Hz",
        "ewfd.Emodex_2", "ewfd.Emodey_2", "ewfd.Emodez_2",
        "ewfd.Hmodex_2", "ewfd.Hmodey_2", "ewfd.Hmodez_2", "nx", "ny", "nz"]
    raw = [
        _bma_basis_raw(contracts[0], quadratures[0], basis_e=(0j, 1 + 0j, 0j), basis_h=(0j, 0j, 1 + 0j)),
        _bma_basis_raw(contracts[1], quadratures[1], basis_e=(0j, 0j, 1 + 0j), basis_h=(0j, 1 + 0j, 0j)),
    ]
    checked = validate_full3d_bma_basis_mapping_samples(contracts, raw, quadratures)
    assert checked["status"] == "SOFTWARE_PAIRED_BMA_FIELD_SAMPLE_DIAGNOSTICS_VALID"
    assert checked["candidate_fixed_component_basis_ordinals"] == [1]
    assert checked["one_to_one_candidate_basis_ordinal"] == 1
    assert checked["native_result"] == "NOT_RUN"
    assert checked["field_mapping_status"] == "UNVERIFIED"
    assert checked["per_basis_fixed_component_diagnostics"][0]["E"]["unadjusted_normalized_residual"] == 0
    assert checked["per_basis_fixed_component_diagnostics"][1]["fixed_component_candidate_match"] is False
    assert checked["phase_rotation_or_component_permutation_applied"] is False
    assert all(row["applied_to_samples"] is False for row in checked["common_phase_diagnostics"])
    assert all(row["use"] == "DIAGNOSTIC_ONLY_NOT_A_ONE_TO_ONE_MAPPING_GATE"
               for row in checked["subspace_projection_diagnostics"])


@pytest.mark.parametrize("mutation", [
    "duplicate_dataset", "wrong_dataset_solution", "incomplete_binding",
    "foreign_inner_index", "wrong_solnum", "duplicate_solnum_pair",
    "missing_parameter_row", "port_axis_confusion", "wrong_plane_selection",
])
def test_bma_basis_mapping_source_binding_rejects_ambiguous_or_axis_mixed_evidence(mutation):
    preparation, producer, binding, _, _ = _bma_basis_mapping_bundle()
    rows = [{"tag": "dset_bma_probe", "type_id": "Solution",
             "solution": "sol3dBmaProbe", "component": "comp3d", "geometry": "geom3d"}]
    index = copy.deepcopy({"dset_bma_probe": {
        "dataset": "dset_bma_probe", "solution": "sol3dBmaProbe",
        "binding_complete": True, "axis_metadata_complete": True,
        "pair_mapping_complete": True, "parameters_complete": True,
        "outer_indices": [1], "inner_indices": [1, 2], "solution_count": 2,
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1},
                         {"outer": 1, "inner": 2, "solnum": 2}],
        "parameters": {"by_pair": {
            "1:1": {"solnum": 1, "names": ["freq"], "values": [193.4], "units": ["THz"]},
            "1:2": {"solnum": 2, "names": ["freq"], "values": [193.4], "units": ["THz"]}}}}})
    if mutation == "duplicate_dataset":
        rows.append({**rows[0], "tag": "dset_duplicate"})
    elif mutation == "wrong_dataset_solution":
        rows[0]["solution"] = "solForeign"
    elif mutation == "incomplete_binding":
        index["dset_bma_probe"]["binding_complete"] = False
    elif mutation == "foreign_inner_index":
        index["dset_bma_probe"]["inner_indices"] = [1, 3]
    elif mutation == "wrong_solnum":
        index["dset_bma_probe"]["solnum_pairs"][1]["solnum"] = 3
    elif mutation == "duplicate_solnum_pair":
        index["dset_bma_probe"]["solnum_pairs"][1]["inner"] = 1
    elif mutation == "missing_parameter_row":
        del index["dset_bma_probe"]["parameters"]["by_pair"]["1:2"]
    elif mutation == "port_axis_confusion":
        preparation["receiver_port"]["port_mode_number_readback"] = "2"
        with pytest.raises(Full3DScienceError):
            build_full3d_bma_basis_mapping_contracts(
                case=CASE, preparation=preparation, producer_evidence=producer,
                basis_binding=binding, plane=OUTPUT_PLANE, radial_intervals=4, angular_points=8)
        return
    elif mutation == "wrong_plane_selection":
        plane = dict(OUTPUT_PLANE, selection_tag="sel3dInputPort")
        with pytest.raises(Full3DScienceError):
            build_full3d_bma_basis_mapping_contracts(
                case=CASE, preparation=preparation, producer_evidence=producer,
                basis_binding=binding, plane=plane, radial_intervals=4, angular_points=8)
        return
    with pytest.raises(Full3DScienceError):
        resolve_full3d_bma_basis_sources(rows, index, producer_evidence=producer)


@pytest.mark.parametrize("mutation", [
    "foreign_port_mode", "contract_hash", "quadrature", "source_tuple",
    "basis_axis", "port_axis", "missing_expression", "duplicate_expression",
    "wrong_unit", "missing_unit_readback", "coordinates", "zero_electric_group",
])
def test_bma_basis_mapping_sample_validation_fails_closed(mutation):
    _, _, _, contracts, quadratures = _bma_basis_mapping_bundle()
    raws = [
        _bma_basis_raw(contracts[0], quadratures[0], basis_e=(0j, 1 + 0j, 0j), basis_h=(0j, 0j, 1 + 0j)),
        _bma_basis_raw(contracts[1], quadratures[1], basis_e=(0j, 0j, 1 + 0j), basis_h=(0j, 1 + 0j, 0j)),
    ]
    contracts = copy.deepcopy(contracts)
    raws = copy.deepcopy(raws)
    quadratures = copy.deepcopy(quadratures)
    if mutation == "foreign_port_mode":
        raws[0]["port_mode_axis"]["port_mode_number_readback"] = "2"
    elif mutation == "contract_hash":
        contracts[0]["basis_axis"]["ordinal"] = 2
    elif mutation == "quadrature":
        quadratures[1]["weights_m2"][1] *= 1.01
    elif mutation == "source_tuple":
        raws[1]["source"]["inner_index"] = 1
    elif mutation == "basis_axis":
        raws[1]["basis_axis"]["ordinal"] = 1
    elif mutation == "port_axis":
        contracts[1]["port_mode_axis"]["port_name"] = "1"
        identity = {key: value for key, value in contracts[1].items()
                    if key not in {"contract_id", "native_result", "study_or_solver_invoked"}}
        contracts[1]["contract_id"] = __import__("hashlib").sha256(
            __import__("json").dumps(identity, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        raws[1]["port_mode_axis"] = copy.deepcopy(contracts[1]["port_mode_axis"])
    elif mutation == "missing_expression":
        raws[0]["expressions"].pop()
    elif mutation == "duplicate_expression":
        raws[0]["expressions"].append(copy.deepcopy(raws[0]["expressions"][0]))
    elif mutation == "wrong_unit":
        raws[0]["expressions"][0]["unit"] = "A/m"
    elif mutation == "missing_unit_readback":
        raws[0]["units_preserved"] = False
    elif mutation == "coordinates":
        raws[1]["coordinates_m"][0][0] += 1e-6
    elif mutation == "zero_electric_group":
        raws[0] = _bma_basis_raw(contracts[0], quadratures[0], basis_e=(0j, 0j, 0j), basis_h=(0j, 0j, 1 + 0j))
    with pytest.raises(Full3DScienceError):
        validate_full3d_bma_basis_mapping_samples(contracts, raws, quadratures)


def test_bma_basis_mapping_requires_both_e_and_h_unadjusted_residuals_to_match():
    _, _, _, contracts, quadratures = _bma_basis_mapping_bundle()
    raws = [
        _bma_basis_raw(contracts[0], quadratures[0], basis_e=(0j, 1 + 0j, 0j), basis_h=(1 + 0j, 0j, 0j)),
        _bma_basis_raw(contracts[1], quadratures[1], basis_e=(0j, 0j, 1 + 0j), basis_h=(0j, 1 + 0j, 0j)),
    ]
    checked = validate_full3d_bma_basis_mapping_samples(contracts, raws, quadratures)
    row = checked["per_basis_fixed_component_diagnostics"][0]
    assert row["E"]["fixed_component_candidate_match"] is True
    assert row["H"]["fixed_component_candidate_match"] is False
    assert row["fixed_component_candidate_match"] is False
    assert checked["candidate_fixed_component_basis_ordinals"] == []
    assert checked["field_mapping_status"] == "UNVERIFIED"


def test_bma_basis_mapping_dispatch_is_one_same_grid_paired_read_not_a_solve():
    _, _, _, contracts, quadratures = _bma_basis_mapping_bundle()
    route = build_full3d_bma_basis_mapping_dispatch(
        contracts[0], source_artifact="NativeW23Full3DFixture.java",
        quadrature=quadratures[0], project_id="project-1", model_ref=MODEL_REF,
        model_tag="M1", revision=12, request_id="basis-fields-1",
        idempotency_key="basis-fields-idem-1")
    arguments = route["arguments"]["arguments"]["arguments"]
    assert arguments["phase"] == "bma_basis_fields"
    assert arguments["study_or_solver_invoked"] is False
    assert arguments["contract"]["unit_groups"]["electric"]["unit"] == "V/m"
    assert arguments["contract"]["unit_groups"]["magnetic"]["unit"] == "A/m"
    assert arguments["contract"]["unit_groups"]["normal"]["unit"] == "1"
    assert route["execution"]["request_id"] == "basis-fields-1"
    assert "field_arrays" not in route["arguments"]["arguments"]
    assert "three same-grid unit groups" in route["dispatch_scope"]
    tampered = copy.deepcopy(contracts[0])
    tampered["field_names"].pop()
    with pytest.raises(Full3DScienceError):
        build_full3d_bma_basis_mapping_dispatch(
            tampered, source_artifact="NativeW23Full3DFixture.java",
            quadrature=quadratures[0], project_id="project-1", model_ref=MODEL_REF,
            model_tag="M1", revision=12, request_id="basis-fields-2",
            idempotency_key="basis-fields-idem-2")


@pytest.mark.parametrize("mutation", [
    "foreign_request", "foreign_idempotency", "foreign_operation", "foreign_job",
    "missing_request_metadata", "revision_jump",
])
def test_bma_mapping_route_binding_uses_real_durable_request_and_job_identity(mutation):
    from tools.w23_full3d_science import validate_full3d_bma_mapping_route_result

    _, _, _, contracts, quadratures = _bma_basis_mapping_bundle()
    request = build_full3d_bma_basis_mapping_dispatch(
        contracts[0], source_artifact="NativeW23Full3DFixture.java", quadrature=quadratures[0],
        project_id="project-1", model_ref=MODEL_REF, model_tag="M1", revision=12,
        request_id="mapping-req-1", idempotency_key="mapping-idem-1")
    response_data = {"worker": {"ok": True, "status": "SUCCEEDED", "result": {"readback": {}}},
                     "readback": {"executed": True, "readback": {}}}
    route = _bma_mapping_route_result(request, label="bma_basis_fields_ordinal1",
                                      response_data=response_data, revision_delta=1)
    kwargs = {"expected_revision_delta": 1, "max_execution_timeout_s": 180}
    if mutation == "foreign_request":
        route["submitted_request"]["execution"]["request_id"] = "foreign-request"
    elif mutation == "foreign_idempotency":
        route["job_wait_responses"][0]["data"]["operation"]["metadata"]["execution"]["idempotency_key"] = "foreign-idem"
        route["job_wait_responses"][0]["data"]["metadata"]["execution"]["idempotency_key"] = "foreign-idem"
    elif mutation == "foreign_operation":
        route["job_wait_responses"][0]["data"]["operation_id"] = "foreign-operation-instance"
    elif mutation == "foreign_job":
        route["job_wait_responses"][0]["data"]["job_id"] = "foreign-job"
    elif mutation == "missing_request_metadata":
        del route["job_wait_responses"][0]["data"]["operation"]["metadata"]["execution"]["request_id"]
    elif mutation == "revision_jump":
        response = route["response"]
        response["execution"]["revision"] += 1
        route["job_wait_responses"][0]["data"]["result"] = response
    with pytest.raises(Full3DScienceError):
        validate_full3d_bma_mapping_route_result(request, route, **kwargs)


def test_bma_mapping_stage_integrates_two_basis_samples_after_exact_dataset_routes(tmp_path):
    from tools.run_native_w23_full3d_setup import _execute_bma_basis_mapping_stage

    preparation = _validated_bma_probe_preparation()
    producer_readback = _bma_probe_run_readback(preparation)
    producer_readback["managed_identity"]["expected_revision"] = 19
    producer_request = _bma_probe_run_request(preparation, revision=19)
    producer_route = _bma_probe_public_route_result(producer_readback, producer_request)
    producer = validate_full3d_bma_probe_run_readback(
        producer_readback, preparation=preparation, project_id="project-1", model_tag="M1",
        model_ref=MODEL_REF, run_request=producer_request, route_result=producer_route)
    producer_route_evidence = {"preparation": preparation, "request": producer_request,
        "route_result": producer_route, "readback": producer_readback}
    baseline = {**CASE, "factor": "baseline", "receiver_transform": {
        "axis_xyz": [1.0, 0.0, 0.0], "center_xyz_um": [20.0, 0.0, 0.0]}}
    source_path = tmp_path / "NativeW23Full3DFixture.java"
    source_path.write_bytes(b"synthetic frozen fixture source")
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    staged_source = {"status": "PROJECT_LOCAL_SOURCE_STAGED",
        "source_artifact": source_path.name, "workspace": str(tmp_path),
        "path": str(source_path), "sha256": source_hash}
    owner = {"candidate_owned_server": True, "gui_attached": False,
        "project_id": "project-1", "server_process_identity": {
            "pid": 77123, "start_epoch_ms": 1790000000000},
        "listener": {"status": "LOOPBACK_LISTENER_VERIFIED_BEFORE_WORKER",
                     "pid": 77123, "port": 55991, "endpoint": "127.0.0.1:55991"},
        "session_identity": {"project_id": "project-1", "session_id": "session-1",
            "server_instance_id": "worker-epoch-1", "worker_instance_id": "worker-1",
            "worker_epoch": 1, "endpoint": "127.0.0.1:55991"},
        "model_ref": dict(MODEL_REF)}
    apply_readback = _bma_receiver_readback()
    result = {"field_sample_readbacks_validated": 0}
    model = {"model_ref": dict(MODEL_REF), "model_tag": "M1", "revision": 20}
    rows = [{"tag": "dset_bma_probe", "type_id": "Solution",
             "solution": "sol3dBmaProbe", "component": "comp3d", "geometry": "geom3d"}]
    indices = {"dataset": "dset_bma_probe", "solution": "sol3dBmaProbe",
        "binding_complete": True, "axis_metadata_complete": True,
        "pair_mapping_complete": True, "parameters_complete": True,
        "outer_indices": [1], "inner_indices": [1, 2], "solution_count": 2,
        "solnum_pairs": [{"outer": 1, "inner": 1, "solnum": 1},
                         {"outer": 1, "inner": 2, "solnum": 2}],
        "parameters": {"by_pair": {
            "1:1": {"solnum": 1, "names": ["freq", "modeIndex"],
                    "values": [193.414489032258, 1], "units": ["THz", "1"]},
            "1:2": {"solnum": 2, "names": ["freq", "modeIndex"],
                    "values": [193.414489032258, 2], "units": ["THz", "1"]}}}}
    current_revision = 20
    calls = []
    failure_label = None

    def route(request, label, cap):
        nonlocal current_revision
        logical = request["execution"]
        assert logical["expected_revision"] == current_revision
        calls.append((label, copy.deepcopy(request), cap))
        if label == "bma_basis_dataset_list":
            data, delta = {"datasets": rows, "count": 1,
                           "tags": ["dset_bma_probe"], "read_errors": []}, 0
        elif label == "bma_basis_dataset_solution_indices":
            data, delta = indices, 0
        else:
            if label == failure_label:
                raise ManagedRouteOutcomeError(
                    label, "UNKNOWN", "injected no-replay timeout after dispatch",
                    job_id="unknown-job", response={"outcome": "UNKNOWN"})
            nested = request["arguments"]["arguments"]["arguments"]
            contract = nested["contract"]
            assert nested["phase"] == "bma_basis_fields"
            assert nested["study_or_solver_invoked"] is False
            axis = contract["basis_axis"]["ordinal"]
            quad = circular_port_quadrature(
                contract["plane"]["center_xyz_m"], contract["plane"]["axis_xyz"],
                contract["plane"]["sample_radius_m"], radial_intervals=32, angular_points=64)
            basis_e = (0j, 1 + 0j, 0j) if axis == 1 else (0j, 0j, 1 + 0j)
            basis_h = (0j, 0j, 1 + 0j) if axis == 1 else (0j, 1 + 0j, 0j)
            raw = _bma_basis_raw(contract, quad, basis_e=basis_e, basis_h=basis_h,
                                 port_e=basis_e, port_h=basis_h)
            data, delta = {"worker": {"ok": True, "status": "SUCCEEDED",
                                      "result": {"readback": raw}},
                           "readback": {"executed": True, "readback": raw}}, 1
        observed = _bma_mapping_route_result(
            request, label=label, response_data=data, revision_delta=delta)
        current_revision += delta
        return observed

    finished = _execute_bma_basis_mapping_stage(
        route, result=result, project_id="project-1", model=model,
        source_artifact="NativeW23Full3DFixture.java", preparation=preparation,
        producer_evidence=producer, producer_route_evidence=producer_route_evidence,
        apply_readback=apply_readback,
        baseline_case=baseline, staged_source_proof=staged_source,
        execution_owner=owner)
    assert [label for label, _, _ in calls] == [
        "bma_basis_dataset_list", "bma_basis_dataset_solution_indices",
        "bma_basis_fields_ordinal1", "bma_basis_fields_ordinal2"]
    assert [request["execution"]["expected_revision"] for _, request, _ in calls] == [20, 20, 20, 21]
    assert [cap for _, _, cap in calls] == [90, 90, 180, 180]
    samples = finished["evidence"]["field_samples"]
    assert [sample["basis_ordinal"] for sample in samples] == [1, 2]
    assert [sample["validated_binding"]["revision_delta"] for sample in samples] == [1, 1]
    assert samples[0]["readback"]["source"]["inner_index"] != samples[1]["readback"]["source"]["inner_index"]
    assert finished["model"]["revision"] == 22
    assert result["field_sample_attempts"] == 2
    assert result["field_sample_calls"] == 2
    assert result["field_sample_readbacks_validated"] == 2
    assert finished["evidence"]["status"] == "TWO_RAW_BMA_BASIS_SAMPLES_VALIDATED_MAPPING_UNVERIFIED"
    assert finished["evidence"]["field_mapping_status"] == "UNVERIFIED"
    assert finished["evidence"]["study_or_solver_invoked_by_sampling"] is False
    cohort_chain = finished["evidence"]["source_cohort_revision_chain"]
    assert cohort_chain["status"] == "CONTROLLED_SAMPLING_COHORT_CHAIN_VALIDATED"
    assert cohort_chain["revision_start"] == 20 and cohort_chain["revision_end"] == 22
    assert cohort_chain["offline_stored_solution_cohort"] == "ELIGIBLE_FOR_INDEPENDENT_SCIENCE_COMPARISON"
    assert cohort_chain["scientific_acceptance"] == "UNVERIFIED_PENDING_NATIVE_ARTIFACT_AND_REVIEW"

    from tools.w23_full3d_science import validate_full3d_sampling_cohort_revision_chain
    route_rows = [{"label": label, **copy.deepcopy(row)}
                  for label, row in finished["evidence"]["routes"].items()]
    stage_contracts = finished["evidence"]["contracts"]
    stage_quadratures = [circular_port_quadrature(
        contract["plane"]["center_xyz_m"], contract["plane"]["axis_xyz"],
        contract["plane"]["sample_radius_m"], radial_intervals=32, angular_points=64)
        for contract in stage_contracts]
    sample_rows = [{"label": f"bma_basis_fields_ordinal{ordinal}",
                    "contract": stage_contracts[ordinal - 1],
                    "quadrature": stage_quadratures[ordinal - 1],
                    "readback": copy.deepcopy(samples[ordinal - 1]["readback"])}
                   for ordinal in (1, 2)]
    cohort_args = {"project_id": "project-1", "model_ref": MODEL_REF,
        "start_revision": 20, "source_artifact": "NativeW23Full3DFixture.java",
        "staged_source_proof": staged_source, "execution_owner": owner,
        "producer_evidence": producer,
        "producer_route_evidence": producer_route_evidence}
    broken_revision = copy.deepcopy(route_rows)
    broken_revision[3]["request"]["execution"]["expected_revision"] = 22
    with pytest.raises(Full3DScienceError, match="revision chain"):
        validate_full3d_sampling_cohort_revision_chain(
            broken_revision, sample_rows, **cohort_args)
    foreign_worker = copy.deepcopy(route_rows)
    foreign_worker[2]["request"]["execution"]["model_ref"]["server_instance_id"] = "foreign-worker"
    with pytest.raises(Full3DScienceError, match="exact project/Worker/ModelRef"):
        validate_full3d_sampling_cohort_revision_chain(
            foreign_worker, sample_rows, **cohort_args)
    bad_cleanup = copy.deepcopy(sample_rows)
    bad_cleanup[1]["readback"]["cleanup"]["removed"] = False
    with pytest.raises(Full3DScienceError, match="sample readback"):
        validate_full3d_sampling_cohort_revision_chain(
            route_rows, bad_cleanup, **cohort_args)
    cleanup_route_failure = copy.deepcopy(route_rows)
    cleanup_route = cleanup_route_failure[3]
    cleanup_response = cleanup_route["route_result"]["response"]
    cleanup_raw = cleanup_response["data"]["readback"]["readback"]
    cleanup_raw["cleanup"]["removed"] = False
    cleanup_raw["cleanup"]["cleanup_failed"] = True
    cleanup_response["data"]["worker"]["result"]["readback"] = copy.deepcopy(cleanup_raw)
    cleanup_route["readback"] = copy.deepcopy(cleanup_raw)
    cleanup_route["route_result"]["job_wait_responses"][-1]["data"]["result"] = \
        copy.deepcopy(cleanup_response)
    with pytest.raises(Full3DScienceError, match="sample readback"):
        validate_full3d_sampling_cohort_revision_chain(
            cleanup_route_failure, sample_rows, **cohort_args)
    detached_source = copy.deepcopy(sample_rows)
    detached_source[0]["readback"]["source_cohort"]["before"]["stored_solution"][
        "computation_date_ms"] += 12345
    with pytest.raises(Full3DScienceError, match="sample readback"):
        validate_full3d_sampling_cohort_revision_chain(
            route_rows, detached_source, **cohort_args)
    detached_complex = copy.deepcopy(sample_rows)
    detached_complex[0]["readback"]["expressions"][0]["real"][0] += 0.125
    with pytest.raises(Full3DScienceError, match="sample readback"):
        validate_full3d_sampling_cohort_revision_chain(
            route_rows, detached_complex, **cohort_args)
    detached_dataset = copy.deepcopy(route_rows)
    detached_dataset[0]["readback"]["datasets"][0]["solution"] = "foreign-solution"
    with pytest.raises(Full3DScienceError):
        validate_full3d_sampling_cohort_revision_chain(
            detached_dataset, sample_rows, **cohort_args)
    detached_indices = copy.deepcopy(route_rows)
    detached_indices[1]["readback"]["solnum_pairs"][0]["solnum"] = 99
    with pytest.raises(Full3DScienceError):
        validate_full3d_sampling_cohort_revision_chain(
            detached_indices, sample_rows, **cohort_args)
    detached_producer_summary = copy.deepcopy(cohort_args)
    detached_producer_summary["producer_evidence"]["managed_job_id"] = "foreign-job"
    with pytest.raises(Full3DScienceError, match="producer summary"):
        validate_full3d_sampling_cohort_revision_chain(
            route_rows, sample_rows, **detached_producer_summary)
    detached_producer_snapshot = copy.deepcopy(cohort_args)
    detached_producer_snapshot["producer_route_evidence"]["readback"][
        "post_solve_solution_state"]["solution_pairs"][0]["solnum"] = 7
    with pytest.raises(Full3DScienceError):
        validate_full3d_sampling_cohort_revision_chain(
            route_rows, sample_rows, **detached_producer_snapshot)
    changed_source_bytes = source_path.read_bytes()
    source_path.write_bytes(changed_source_bytes + b"changed")
    try:
        with pytest.raises(Full3DScienceError, match="frozen staged hash"):
            validate_full3d_sampling_cohort_revision_chain(
                route_rows, sample_rows, **cohort_args)
    finally:
        source_path.write_bytes(changed_source_bytes)

    failure_label = "bma_basis_fields_ordinal2"
    current_revision = 20
    calls.clear()
    partial = {"solver_call_attempts": 1, "solver_calls": 1,
        "native_scientific_result": "BMA_PRODUCER_COMPLETED_FIELD_MAPPING_UNKNOWN",
        "field_sample_attempts": 0, "field_sample_calls": 0,
        "field_sample_readbacks_validated": 0}
    with pytest.raises(ManagedRouteOutcomeError):
        _execute_bma_basis_mapping_stage(
            route, result=partial, project_id="project-1", model=model,
            source_artifact="NativeW23Full3DFixture.java", preparation=preparation,
            producer_evidence=producer, producer_route_evidence=producer_route_evidence,
            apply_readback=apply_readback,
            baseline_case=baseline, staged_source_proof=staged_source,
            execution_owner=owner)
    from tools.run_native_w23_full3d_setup import _finalize_science_counters
    _finalize_science_counters(partial, "bma_mapping_probe")
    assert partial["field_sample_attempts"] == 2
    assert partial["field_sample_calls"] == 1
    assert partial["field_sample_readbacks_validated"] == 0
    assert partial["field_sampling_status"] == "UNKNOWN_OR_PARTIAL"
    assert partial["native_scientific_result"] == "BMA_PRODUCER_COMPLETED_FIELD_MAPPING_UNKNOWN"
    assert partial["numeric_port_mode_field_mapping"] == "UNVERIFIED"


def test_dataset_read_public_control_daemon_routes_do_not_advance_managed_revision(tmp_path):
    from contextlib import nullcontext

    from comsol_mcp._control_daemon import ControlDaemon
    from comsol_mcp._execution_contract import SessionLedger
    from comsol_mcp._execution_service import ExecutionService
    from tools.w23_full3d_science import validate_full3d_bma_mapping_route_result

    class Collection:
        def __init__(self, items=None):
            self.items = dict(items or {})

        def tags(self):
            return list(self.items)

        def get(self, tag):
            return self.items[tag]

    class Dataset:
        def getType(self):
            return "Solution"

        def properties(self):
            return ["solution", "data", "comp", "geom"]

        def getString(self, name):
            return {"solution": "sol1", "data": "sol1",
                    "comp": "comp1", "geom": "geom1"}.get(name)

    class SolutionInfo:
        def getOuterSolnum(self):
            return [1]

        def getSolnum(self, outer, strict):
            assert outer == 1 and strict is True
            return [1, 2, 3]

        def getPNames(self, pairs):
            return [["freq"] for _ in pairs]

        def getPvals(self, pairs):
            return [[193.414489032258] for _ in pairs]

        def getUnits(self, pairs):
            return [["THz"] for _ in pairs]

        def getLevelNames(self):
            return ["outer", "inner"]

    class Solver:
        def getPVals(self):
            return [193.414489032258]

        def study(self):
            return "std1"

        def getSolutioninfo(self):
            return SolutionInfo()

    class StudyFeature:
        def getType(self):
            return "Frequency"

    class Study:
        def feature(self):
            return Collection({"freq": StudyFeature()})

    class Geometry:
        pass

    class Component:
        def geom(self, tag=None):
            geometries = Collection({"geom1": Geometry()})
            return geometries if tag is None else geometries.get(tag)

    class Model:
        def __init__(self):
            self.datasets = Collection({"dset1": Dataset()})
            self.solutions = Collection({"sol1": Solver()})
            self.components = Collection({"comp1": Component()})
            self.model_nodes = Collection({"comp1": object()})

        def result(self):
            return type("Results", (), {"dataset": lambda _self: self.datasets})()

        def sol(self, tag=None):
            return self.solutions if tag is None else self.solutions.get(tag)

        def modelNode(self):
            return self.model_nodes

        def component(self, tag=None):
            return self.components if tag is None else self.components.get(tag)

        def study(self, tag):
            assert tag == "std1"
            return Study()

    class Worker:
        def __init__(self):
            model = Model()
            self._client = type("Client", (), {"model": lambda _self, _tag: model})()

        def client(self):
            return self._client

    class SnapshotAdapter:
        def model_snapshot(self, model_tag):
            return {"model_tag": model_tag, "server_instance_id": "w23-test-server",
                    "fingerprint": "w23-dataset-read-stable", "external_event_counter": 0}

    project_root = tmp_path / "projects"
    project_root.mkdir()
    daemon = ControlDaemon(tmp_path / "control", project_root=project_root)
    try:
        project = daemon.dispatch({"operation": "project.create", "arguments": {
            "label": "w23-dataset-read-route", "workspace": "w23-dataset-read-route",
            "policy": {"permissions": ["inspect", "project_write", "compute"]}},
            "execution": {"request_id": "w23-project-create", "idempotency_key": "w23-project-create"}})
        assert project["success"] is True, project
        project_id = project["data"]["project"]["project_id"]
        ledger = SessionLedger("w23-dataset-session", "w23-test-server")
        service = ExecutionService(ledger, SnapshotAdapter(), project_root=project_root)
        model_readback = service.bind_model("M1")
        model_ref = model_readback["execution"]["model_ref"]
        worker = Worker()
        worker.operation_context = lambda *_args, **_kwargs: nullcontext()
        backend = daemon.backend
        backend.service = service
        backend.worker = worker
        backend.worker_identity = {"runtime_id": "w23-test-runtime", "worker_instance_id": "w23-test-worker",
                                  "connection_epoch": 1, "server_instance_id": "w23-test-server",
                                  "endpoint": "127.0.0.1:52001"}
        backend._bind_model_project(model_ref, project_id)
        backend.persist()
        list_request = build_full3d_dataset_list_dispatch(
            project_id=project_id, model_ref=model_ref, model_tag="M1", revision=0,
            request_id="w23-datasets-read", idempotency_key="w23-datasets-read")
        list_submitted = {**list_request, "execution": {**list_request["execution"],
            "execution_timeout_s": 10.0, "queue_timeout_s": 10.0, "rpc_timeout_s": 10.0}}
        list_result = dispatch_public_managed_route(
            daemon, list_submitted, label="bma_basis_dataset_list", timeout_s=10, poll_interval_s=0.01)
        list_binding = validate_full3d_bma_mapping_route_result(
            list_request, {**list_result, "submitted_request": {
                **list_submitted}},
            expected_revision_delta=0, max_execution_timeout_s=90)
        assert list_result["response"]["data"]["count"] == 1
        assert list_binding["revision_delta"] == 0
        assert list_result["response"]["execution"]["revision"] == 0
        indices_request = build_full3d_dataset_indices_dispatch(
            "dset1", project_id=project_id, model_ref=model_ref, model_tag="M1", revision=0,
            request_id="w23-indices-read", idempotency_key="w23-indices-read")
        indices_submitted = {**indices_request, "execution": {**indices_request["execution"],
            "execution_timeout_s": 10.0, "queue_timeout_s": 10.0, "rpc_timeout_s": 10.0}}
        indices_result = dispatch_public_managed_route(
            daemon, indices_submitted, label="bma_basis_dataset_solution_indices",
            timeout_s=10, poll_interval_s=0.01)
        indices_route = {**indices_result, "submitted_request": indices_submitted}
        indices_binding = validate_full3d_bma_mapping_route_result(
            indices_request, indices_route, expected_revision_delta=0, max_execution_timeout_s=90)
        assert indices_result["response"]["data"]["binding_complete"] is True
        assert indices_result["response"]["data"]["solution_count"] == 3
        assert indices_binding["revision_delta"] == 0
        assert indices_result["response"]["execution"]["revision"] == 0
        assert service.ledger.revision(__import__("comsol_mcp._execution_contract", fromlist=["model_ref_from_mapping"])
                                       .model_ref_from_mapping(model_ref)) == 0
    finally:
        daemon.close()


def test_public_managed_route_waits_only_on_same_job_and_never_replays_unknown():
    class QueuedDaemon:
        def __init__(self, terminal_status="SUCCEEDED"):
            self.requests = []
            self.terminal_status = terminal_status

        def dispatch(self, request):
            self.requests.append(copy.deepcopy(request))
            if request["operation"] == "operation_call":
                return {"success": True, "data": {"job_id": "job-1", "status": "QUEUED"},
                        "execution": {"job_id": "job-1"}}
            assert request["operation"] == "job.wait"
            result = {"success": True, "data": {"worker": {"ok": True}}}
            return {"success": True, "data": {"job_id": "job-1", "status": self.terminal_status,
                                                   "result": result}}

    daemon = QueuedDaemon()
    result = dispatch_public_managed_route(daemon, {"operation": "operation_call"}, label="readback")
    assert result["outcome"] == "SUCCEEDED"
    assert result["job_id"] == "job-1"
    assert [row["operation"] for row in daemon.requests] == ["operation_call", "job.wait"]

    unknown = QueuedDaemon(terminal_status="UNKNOWN")
    with pytest.raises(ManagedRouteOutcomeError, match="terminal status UNKNOWN") as raised:
        dispatch_public_managed_route(unknown, {"operation": "operation_call"}, label="unknown")
    assert raised.value.outcome == "UNKNOWN"
    assert raised.value.retry_forbidden is True
    assert [row["operation"] for row in unknown.requests] == ["operation_call", "job.wait"]


def test_full3d_managed_configuration_chain_uses_project_child_and_stops_before_study_run(tmp_path):
    import hashlib
    from pathlib import Path

    project_root = tmp_path / "container"
    project_root.mkdir()
    workspace = project_root / "science"
    workspace.mkdir()
    project_response = {"success": True, "data": {"project": {
        "project_id": "project-3d", "workspace": str(workspace), "schema_version": 1,
        "revision": 1, "label": "W23", "contract": {},
        "policy": {"permissions": ["inspect", "project_write", "compute", "trusted_code"]}}}}
    connection = {"success": True, "data": {"endpoint": "127.0.0.1:54321",
        "session_id": "session-1", "server_instance_id": "worker-epoch-1",
        "worker": {"generation": 1}}}
    recipe = canonical_full3d_recipe()
    source = Path(__file__).resolve().parents[1] / "tools/java/NativeW23Full3DFixture.java"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    calls = []

    def java_result(phase, revision, **extra):
        if phase == "build":
            readback = {"fixture_id": recipe["fixture_id"], "recipe_sha256": recipe["recipe_sha256"],
                "status": "BUILT_CONFIGURED_NOT_SOLVED", "native_result": "NOT_RUN",
                "study_or_solver_invoked": False,
                "managed_identity": {"project_id": "project-3d", "model_ref": MODEL_REF,
                                     "model_tag": "M1", "expected_revision": 0}}
        else:
            readback = {"fixture_id": recipe["fixture_id"], "recipe_sha256": recipe["recipe_sha256"],
                "status": "GEOMETRY_CASE_CONFIGURED_NOT_SOLVED", "native_result": "NOT_RUN",
                "study_or_solver_invoked": False,
                "managed_identity": {"project_id": "project-3d", "model_ref": MODEL_REF,
                                     "model_tag": "M1", "expected_revision": 1},
                "case_id": extra["case"]["case_id"],
                "case_identity_sha256": extra["case"]["case_identity_sha256"],
                "experiment_id": extra["case"]["experiment_id"],
                "factor": extra["case"]["factor"], "factor_value": extra["case"]["value"]}
        return {"success": True, "data": {"worker": {"ok": True, "status": "SUCCEEDED",
            "result": {"readback": readback}}, "readback": {"executed": True, "readback": readback}},
            "execution": {"model_ref": MODEL_REF, "revision": revision}}

    class FakeDaemon:
        def dispatch(self, request):
            calls.append(copy.deepcopy(request))
            op = request["operation"]
            if op == "model_create":
                return {"success": True, "execution": {"model_ref": MODEL_REF, "revision": 0}}
            assert op == "operation_call"
            nested = request["arguments"]
            phase = nested["arguments"]["arguments"]["phase"]
            if phase == "apply_case":
                case = nested["arguments"]["arguments"]["case"]
                return java_result(phase, 2, case=case)
            assert phase == "build"
            return java_result(phase, 1)

    result = execute_full3d_managed_configuration_chain(
        FakeDaemon(), project_create_response=project_response, authorized_container=project_root,
        connection_response=connection, expected_endpoint="127.0.0.1:54321",
        fixture_source_path=source, fixture_source_sha256=source_hash, recipe=recipe,
        experiment_id="exp-3d-1", model_name="W23 full3D baseline", wait_timeout_s=5)
    assert result["status"] == "FULL3D_CONFIGURED_STUDY_READY_NOT_RUN"
    assert result["native_result"] == "NOT_RUN"
    assert result["study_or_solver_invoked"] is False
    assert result["model"]["revision"] == 2
    assert result["staged_fixture"]["path"] == str(workspace / "NativeW23Full3DFixture.java")
    assert result["study_run_request"]["arguments"]["operation_id"] == "study.run"
    assert [row["operation"] for row in calls] == ["model_create", "operation_call", "operation_call"]
    assert all(row["execution"]["project_id"] == "project-3d" for row in calls)


def test_full3d_native_source_resolver_uses_solutioninfo_solnum_not_guessed_inner_index():
    rows_and_axes = [
        _solution_dataset("d_freq", "sol_freq", names=["freq"], values=[193.414489032258],
                          units=["THz"], inner=1, solnum=6),
        _solution_dataset("d_bma_out", "sol_bma_out", names=["freq", "modeIndex"],
                          values=[193.414489032258, 1], units=["THz", "1"], inner=1, solnum=8),
        _solution_dataset("d_bma_in", "sol_bma_in", names=["freq", "modeIndex"],
                          values=[193.414489032258, 1], units=["THz", "1"], inner=1, solnum=11),
    ]
    dataset_rows = [row for row, _ in rows_and_axes]
    index_by_tag = {row["tag"]: axes for row, axes in rows_and_axes}
    step_map = {"freq3d": ["sol_freq"], "bmaOutput3d": ["sol_bma_out"],
                "bmaInput3d": ["sol_bma_in"]}
    resolved = resolve_full3d_native_sources(
        dataset_rows, index_by_tag, step_map, frequency_hz=193.414489032258e12)
    assert resolved["status"] == "SOFTWARE_UNIQUE_FULL3D_NATIVE_SOURCE_BINDINGS"
    assert {name: row["solnum"] for name, row in resolved["sources"].items()} == {
        "signal": 6, "reference_mode": 8, "incident_reference": 11}
    definition = build_full3d_mode_overlap_definition(
        resolved["sources"], output_normal_sign=1, input_normal_sign=-1,
        capture_normal_sign=1, power_floor_w=1e-12)
    from comsol_mcp._w23_results import validate_definition_shape
    checked = validate_definition_shape(definition)
    assert checked["output_surface"]["selection"]["tag"] == "sel3dOutputPort"
    assert checked["reference_mode"]["source"]["inner_index"] == 1
    assert checked["power_floor"]["unit"] == "W"
    request = build_full3d_mode_overlap_dispatch(
        definition, project_id="project-1", model_ref=MODEL_REF, model_tag="M1", revision=8,
        request_id="overlap-1", idempotency_key="overlap-idem-1")
    assert request["arguments"]["operation_id"] == "result.mode_overlap"
    assert "field_arrays" not in request["arguments"]["arguments"]
    assert request["execution"]["session_id"] == "session-1"

    incomplete = dict(index_by_tag)
    incomplete["d_freq"] = {**incomplete["d_freq"], "binding_complete": False}
    with pytest.raises(Full3DScienceError, match="axes are incomplete"):
        resolve_full3d_native_sources(dataset_rows, incomplete, step_map,
                                      frequency_hz=193.414489032258e12)
