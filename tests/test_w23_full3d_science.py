from __future__ import annotations

import copy
import math

import pytest

from tools.w23_full3d_science import (
    FULL3D_COMPARISON_POLICY,
    Full3DScienceError,
    ManagedRouteOutcomeError,
    bind_full3d_connection_response,
    bind_full3d_model_create_response,
    bind_full3d_project_create_response,
    build_full3d_dataset_indices_dispatch,
    build_full3d_dataset_list_dispatch,
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
    rectangular_port_quadrature,
    validate_native_field_readback,
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
        expressions.append({"expression": name,
                            "real": [real] * len(coordinates),
                            "imag": [imag] * len(coordinates)})
    return {"native_result": "COMSOL_NATIVE_RAW", "study_or_solver_invoked": False,
            "complex_readback": True, "contract_id": contract["contract_id"],
            "quadrature_sha256": contract["quadrature_sha256"],
            "source": contract["source"], "plane": contract["plane"],
            "sample_count": len(coordinates),
            "coordinates_m": [[point[axis] for point in coordinates] for axis in range(3)],
            "expressions": expressions,
            "cleanup": {"created": True, "removed": True, "cleanup_failed": False}}


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
