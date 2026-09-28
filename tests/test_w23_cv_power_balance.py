from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tests.test_w23_full3d_science import (
    CASE,
    MODEL_REF,
    OUTPUT_PLANE,
    _bma_mapping_route_result,
    _cohort_snapshot,
    _raw,
)
from tests.test_w23_radiation_geometry_v2 import _software_java_flat_cv_readback
from tools.w23_full3d_science import (
    CV_FIELD_JAVA_SOURCE,
    CV_POWER_BALANCE_POLICY,
    CV_RADIATION_JAVA_SOURCE,
    Full3DScienceError,
    build_full3d_control_volume_power_terms_dispatch,
    build_full3d_control_volume_topology_dispatch,
    build_raw_field_contract,
    build_raw_field_dispatch,
    circular_port_quadrature,
    recompute_full3d_control_volume_power_terms,
    validate_full3d_control_volume_power_terms_route,
    validate_full3d_control_volume_topology_route,
)
from tools.w23_radiation_geometry_v2 import (
    CV_NATIVE_INTEGRAL_DIAGNOSTIC_POLICY,
    control_volume_face_contract,
    normalize_control_volume_topology_readback,
)


PROJECT = "project-cv"
RADIATION_ARTIFACT = CV_RADIATION_JAVA_SOURCE["source_artifact"]
FIELD_ARTIFACT = CV_FIELD_JAVA_SOURCE["source_artifact"]
TIMEOUT_CAP = 180.0
SOURCE = {"dataset_id": "dset3d", "solution_id": "sol3d",
          "outer_index": 1, "inner_index": 1, "solnum": 1}


def _topology_readback(*, native: bool = False) -> dict:
    raw = _software_java_flat_cv_readback()
    for row in raw["faces"]:
        row["adjacent_domain_ids"] = [1, 2]
        row["adjacent_domain_orientation"] = [1, -1]
        row["adjacent_domain_bbox_um_by_id"] = {
            "1": [-19.0, 19.0, -5.5, 5.5, -5.5, 5.5],
            "2": [-20.0, 20.0, -6.0, 6.0, -6.0, 6.0],
        }
        row["adjacent_material_tags"] = ["matAir", "matAir"]
        row["adjacent_domain_pml_by_id"] = {"1": False, "2": False}
        row["touches_pml"] = False
        row["touches_port"] = False
    raw.update({
        "evidence_scope": "COMSOL_NATIVE_PARTITION_DOMAINS" if native else "SOFTWARE_FIXTURE",
        "topology_source": "GeomInfo.getAdj/getAdjOrient+GeomMeasure" if native else "software fixture only",
        "partition_feature_type": "PartitionDomains",
        "actual_partition_feature_readback": native,
        "partition_source_face_ids": [901, 902, 903, 904, 905, 906],
        "partition_target_domain_ids": [1, 2],
        "boundary_ids": [row["boundary_id"] for row in raw["faces"]],
        "geometry_domain_count": 2,
        "domain_inventory": [
            {"domain_id": 1, "bounding_box_um": [-19.0, 19.0, -5.5, 5.5, -5.5, 5.5],
             "material_tag": "matAir", "is_pml": False, "physical_domain_role": "NON_PML"},
            {"domain_id": 2, "bounding_box_um": [-20.0, 20.0, -6.0, 6.0, -6.0, 6.0],
             "material_tag": "matAir", "is_pml": False, "physical_domain_role": "NON_PML"},
        ],
        "cv_interior_domain_ids": [1],
        "cv_box_um": {"x": [-19.0, 19.0], "y": [-5.5, 5.5], "z": [-5.5, 5.5]},
        "native_readback_only": True,
        "native_surface_integral_of_one": "NOT_RUN",
        "power_balance": "NOT_RUN",
        "area_unit": "UNVERIFIED_GEOMETRY_MEASURE_OUTPUT_UNIT",
    })
    return raw


def _source_contract_and_raw():
    contract = build_raw_field_contract(
        case=CASE, role="signal", source=SOURCE, plane=OUTPUT_PLANE,
        radial_intervals=2, angular_points=8)
    quadrature = circular_port_quadrature(
        contract["plane"]["center_xyz_m"], contract["plane"]["axis_xyz"],
        contract["plane"]["sample_radius_m"], radial_intervals=2, angular_points=8)
    raw = _raw(contract, quadrature, (0j, 1 + 0j, 0j), (0j, 0j, 1 + 0j))
    return contract, quadrature, raw


def _power_readback(topology: dict, contract: dict, source_raw: dict, *, native=False) -> dict:
    native_result = "COMSOL_NATIVE_CV_POWER_TERMS_READBACK" if native else "SOFTWARE_TEST_FIXTURE"
    source_snapshot = source_raw["source_cohort"]["before"]
    normalized = normalize_control_volume_topology_readback(topology) \
        if topology["faces"] and "patches" not in topology["faces"][0] else topology
    surface_terms = []
    features = []
    areas = {}
    fluxes = {}
    for index, face in enumerate(control_volume_face_contract(), start=1):
        role = face["role"]
        axis = role[0]
        ids = sorted(patch["boundary_id"] for patch in next(
            item for item in normalized["faces"]
            if item.get("role") == role)["patches"])
        area = face["area_um2"] * 1e-12
        flux = float(index) * (-1.0 if role.endswith("minus") else 1.0) * 0.125
        expression = ("-" if role.endswith("minus") else "") + f"ewfd.Poav{axis}"
        surface_terms.append({"role": role, "boundary_ids": ids,
            "normal_direction": ("-" if role.endswith("minus") else "+") + axis,
            "raw_normal_source": "same registered native CV topology readback",
            "expression": expression, "unit": "W", "integral_w": flux,
            "area_expression": "1", "area_unit": "m^2", "area_m2": area})
        areas[role] = area
        fluxes[role] = flux
        for suffix, expr, unit, value in (("a", "1", "m^2", area),
                                          ("f", expression, "W", flux)):
            features.append({"tag": f"cv_{role}_{suffix}", "feature_type": "IntSurface",
                "dataset": SOURCE["dataset_id"], "expression": [expr], "unit": unit,
                "innerinput": "manual", "solnum": str(SOURCE["inner_index"]),
                "outerinput": "manual", "outersolnum": str(SOURCE["outer_index"]),
                "solrepresentation": "solnum", "geometry": "geom3d", "entity_dimension": 2,
                "entity_ids": ids, "complex": False, "result_shape": [1, 1], "value": value})
    interior = [1]
    volume = 4598e-18
    pin = 2.0
    features.append({"tag": "cv_volume", "feature_type": "IntVolume",
        "dataset": SOURCE["dataset_id"], "expression": ["1"], "unit": "m^3",
        "innerinput": "manual", "solnum": str(SOURCE["inner_index"]),
        "outerinput": "manual", "outersolnum": str(SOURCE["outer_index"]),
        "solrepresentation": "solnum", "geometry": "geom3d", "entity_dimension": 3,
        "entity_ids": interior, "complex": False, "result_shape": [1, 1], "value": volume})
    features.append({"tag": "cv_pin", "feature_type": "EvalGlobal",
        "dataset": SOURCE["dataset_id"], "expression": ["ewfd.Pin"], "unit": "W",
        "innerinput": "manual", "solnum": str(SOURCE["inner_index"]),
        "outerinput": "manual", "outersolnum": str(SOURCE["outer_index"]),
        "solrepresentation": "solnum", "geometry": "GLOBAL", "entity_dimension": -1,
        "entity_ids": [], "complex": False, "result_shape": [1, 1], "value": pin})
    return {
        "schema_id": "urn:comsol-mcp:w23:cv-power-terms:1.0.0",
        "native_result": native_result, "study_or_solver_invoked": False,
        "status": "RAW_TERMS_READBACK_COMPLETE_QABS_AND_PRODUCER_UNVERIFIED",
        "control_volume_topology": copy.deepcopy(topology),
        "source_identity": {"dataset": source_snapshot["dataset"],
                             "stored_solution": source_snapshot["stored_solution"]},
        "surface_terms": surface_terms,
        "surface_area_by_role_m2": areas,
        "signed_surface_flux_by_role_w": fluxes,
        "interior_domain_ids": interior,
        "volume_integral_of_one": {"value": volume, "unit": "m^3", "expression": ["1"]},
        "positive_incident_power": {"value": pin, "unit": "W", "expression": ["ewfd.Pin"]},
        "q_abs_volume_integral": {"status": "NOT_AVAILABLE_NATIVE_PHYSICS_FIELD_MAPPING_REQUIRED",
                                  "unit": "W", "value": None, "expression": "NOT_PROVIDED"},
        "q_abs_field_mapping": {"status": "UNVERIFIED", "expression": "NOT_PROVIDED",
                                "unit": "NOT_PROVIDED"},
        "normalization": "surface flux / positive native ewfd.Pin",
        "absolute_balance_tolerance": "NOT_FROZEN", "producer_step_binding": "UNVERIFIED",
        "temporary_numerical_features": features,
        "cleanup": {"created_count": 14, "removed_count": 14, "removed": True,
                    "cleanup_failed": False, "remaining_tags": [], "error": ""},
        "operation_failure": "", "balance_residual": None,
        "scientific_acceptance": "NOT_RUN_QABS_MAPPING_PRODUCER_BINDING_AND_ABSOLUTE_TOLERANCE_OPEN",
    }


def _software_inputs():
    topology = _topology_readback(native=False)
    contract, quadrature, source_raw = _source_contract_and_raw()
    source_raw["native_result"] = "SOFTWARE_TEST_FIXTURE"
    power = _power_readback(topology, contract, source_raw, native=False)
    return topology, contract, source_raw, power


def _native_route_bundle():
    topology = _topology_readback(native=True)
    topology_request = build_full3d_control_volume_topology_dispatch(
        source_artifact=RADIATION_ARTIFACT, project_id=PROJECT, model_ref=MODEL_REF,
        model_tag="M1", revision=4, request_id="cv-topology-request",
        idempotency_key="cv-topology-key")
    topology_data = {"source_sha256": CV_RADIATION_JAVA_SOURCE["source_sha256"],
                    "entrypoint": CV_RADIATION_JAVA_SOURCE["entrypoint"],
                    "worker": {"ok": True, "status": "SUCCEEDED",
                                "result": {"source_sha256": CV_RADIATION_JAVA_SOURCE["source_sha256"],
                                           "entrypoint": CV_RADIATION_JAVA_SOURCE["entrypoint"],
                                           "readback": topology}},
                    "readback": {"executed": True, "readback": topology}}
    topology_route = _bma_mapping_route_result(
        topology_request, label="cv-topology", response_data=topology_data,
        revision_delta=1)
    topology_route["readback"] = topology

    contract, quadrature, source_raw = _source_contract_and_raw()
    source_request = build_raw_field_dispatch(
        contract, source_artifact=FIELD_ARTIFACT, quadrature=quadrature,
        project_id=PROJECT, model_ref=MODEL_REF, model_tag="M1", revision=5,
        request_id="cv-field-request", idempotency_key="cv-field-key")
    source_data = {"source_sha256": CV_FIELD_JAVA_SOURCE["source_sha256"],
                   "entrypoint": CV_FIELD_JAVA_SOURCE["entrypoint"],
                   "worker": {"ok": True, "status": "SUCCEEDED",
                              "result": {"source_sha256": CV_FIELD_JAVA_SOURCE["source_sha256"],
                                         "entrypoint": CV_FIELD_JAVA_SOURCE["entrypoint"],
                                         "readback": source_raw}},
                   "readback": {"executed": True, "readback": source_raw}}
    source_route = _bma_mapping_route_result(
        source_request, label="cv-fields", response_data=source_data,
        revision_delta=1)
    source_route["readback"] = source_raw
    topology_evidence = {"request": topology_request, "route_result": topology_route,
                         "max_execution_timeout_s": TIMEOUT_CAP}
    source_evidence = {"request": source_request, "route_result": source_route,
                       "contract": contract, "quadrature": quadrature,
                       "max_execution_timeout_s": TIMEOUT_CAP}
    terms_request = build_full3d_control_volume_power_terms_dispatch(
        topology_evidence=topology_evidence, source_field_evidence=source_evidence,
        request_id="cv-integral-request", idempotency_key="cv-integral-key")
    assert terms_request["arguments"]["arguments"]["source_artifact"] == RADIATION_ARTIFACT
    power = _power_readback(topology, contract, source_raw, native=True)
    power_data = {"source_sha256": CV_RADIATION_JAVA_SOURCE["source_sha256"],
                  "entrypoint": CV_RADIATION_JAVA_SOURCE["entrypoint"],
                  "worker": {"ok": True, "status": "SUCCEEDED",
                             "result": {"source_sha256": CV_RADIATION_JAVA_SOURCE["source_sha256"],
                                        "entrypoint": CV_RADIATION_JAVA_SOURCE["entrypoint"],
                                        "readback": power}},
                  "readback": {"executed": True, "readback": power}}
    power_route = _bma_mapping_route_result(
        terms_request, label="cv-integrals", response_data=power_data,
        revision_delta=1)
    power_route["readback"] = power
    return topology_evidence, source_evidence, terms_request, power_route, power


def _mutate_terminal_java_metadata(route: dict, name: str, value) -> None:
    """Keep the durable terminal operation envelope internally consistent."""
    for response in (route["response"], route["job_wait_responses"][-1]["data"]["result"]):
        data = response["data"]
        data[name] = value
        data["worker"]["result"][name] = value


def _mutate_terminal_worker_java_metadata(route: dict, name: str, value) -> None:
    """Tamper only the Java Worker result while preserving durable job identity."""
    for response in (route["response"], route["job_wait_responses"][-1]["data"]["result"]):
        response["data"]["worker"]["result"][name] = value


def test_cv_balance_policy_keeps_qabs_and_acceptance_open() -> None:
    assert CV_NATIVE_INTEGRAL_DIAGNOSTIC_POLICY["status"] == "MAIN_APPROVED_PRE_NATIVE_DIAGNOSTIC"
    assert CV_POWER_BALANCE_POLICY["q_abs_expression"] == "NOT_PROVIDED"
    assert CV_POWER_BALANCE_POLICY["q_abs_generic_expression_interface"] == "NOT_IMPLEMENTED"
    assert CV_POWER_BALANCE_POLICY["q_abs_authenticated_mapping"] == "NOT_AVAILABLE"
    assert CV_POWER_BALANCE_POLICY["absolute_balance_tolerance"] == "NOT_FROZEN"
    assert CV_POWER_BALANCE_POLICY["scientific_acceptance"] == "NOT_RUN"


def test_software_cv_terms_recompute_partial_flux_without_native_or_balance_pass() -> None:
    topology, contract, source_raw, power = _software_inputs()
    result = recompute_full3d_control_volume_power_terms(
        power, topology_readback=topology, source_contract=contract,
        source_field_readback=source_raw)
    assert result["native_result"] == "NOT_RUN"
    assert result["scientific_acceptance"] == "NOT_RUN"
    assert result["q_abs_volume_integral"] == "NOT_AVAILABLE"
    assert result["q_abs_generic_expression_interface"] == "NOT_IMPLEMENTED"
    assert result["power_balance_residual_over_Pin"] is None
    assert result["volume_integral_of_one_m3"] == pytest.approx(4598e-18)
    assert result["signed_surface_flux_sum_over_Pin"] == pytest.approx(0.1875)


@pytest.mark.parametrize("mutation", [
    "missing_face", "duplicate_face", "wrong_outward", "touch_port", "touch_pml",
    "foreign_source", "wrong_inner", "nonpositive_pin", "missing_area", "wrong_area_unit",
    "duplicate_area_integral", "duplicate_volume_integral", "area_outside_tolerance",
    "volume_outside_tolerance",
    "missing_volume", "duplicate_interior_domain", "foreign_dataset", "wrong_solnum",
    "zero_qabs", "claim_residual", "cleanup_failed", "extra_port_patch",
])
def test_cv_recomputation_rejects_missing_or_conflicting_evidence(mutation: str) -> None:
    topology, contract, source_raw, power = _software_inputs()
    topology = copy.deepcopy(topology)
    source_raw = copy.deepcopy(source_raw)
    power = copy.deepcopy(power)
    if mutation == "missing_face":
        power["surface_terms"].pop()
    elif mutation == "duplicate_face":
        power["surface_terms"][1] = copy.deepcopy(power["surface_terms"][0])
    elif mutation == "wrong_outward":
        power["surface_terms"][0]["normal_direction"] = "+x"
    elif mutation == "touch_port":
        topology["faces"][0]["touches_port"] = True
    elif mutation == "touch_pml":
        topology["domain_inventory"][0]["is_pml"] = True
    elif mutation == "foreign_source":
        power["source_identity"]["dataset"] = {"tag": "foreign"}
    elif mutation == "wrong_inner":
        contract["source"]["inner_index"] = 2
    elif mutation == "nonpositive_pin":
        power["positive_incident_power"]["value"] = 0
        power["temporary_numerical_features"][-1]["value"] = 0
    elif mutation == "missing_area":
        power["temporary_numerical_features"] = power["temporary_numerical_features"][1:]
        power["cleanup"]["created_count"] = 13
        power["cleanup"]["removed_count"] = 13
    elif mutation == "wrong_area_unit":
        power["temporary_numerical_features"][0]["unit"] = "um^2"
    elif mutation == "duplicate_area_integral":
        power["temporary_numerical_features"][-1] = copy.deepcopy(power["temporary_numerical_features"][0])
        power["temporary_numerical_features"][-1]["tag"] = "cv_duplicate_area"
    elif mutation == "duplicate_volume_integral":
        power["temporary_numerical_features"][-1] = copy.deepcopy(power["temporary_numerical_features"][-2])
        power["temporary_numerical_features"][-1]["tag"] = "cv_duplicate_volume"
    elif mutation == "area_outside_tolerance":
        role = "x_minus"
        actual = control_volume_face_contract()[0]["area_um2"] * 1e-12 * (1.0 + 1.1e-6)
        power["temporary_numerical_features"][0]["value"] = actual
        power["surface_area_by_role_m2"][role] = actual
        power["surface_terms"][0]["area_m2"] = actual
    elif mutation == "volume_outside_tolerance":
        actual = 4598e-18 * (1.0 + 1.1e-6)
        power["temporary_numerical_features"][-2]["value"] = actual
        power["volume_integral_of_one"]["value"] = actual
    elif mutation == "missing_volume":
        power["temporary_numerical_features"] = power["temporary_numerical_features"][:-2]
        power["cleanup"]["created_count"] = 12
        power["cleanup"]["removed_count"] = 12
    elif mutation == "duplicate_interior_domain":
        topology["cv_interior_domain_ids"] = [1, 1]
        power["control_volume_topology"] = copy.deepcopy(topology)
    elif mutation == "foreign_dataset":
        power["temporary_numerical_features"][0]["dataset"] = "foreign"
    elif mutation == "wrong_solnum":
        power["temporary_numerical_features"][0]["solnum"] = "2"
    elif mutation == "zero_qabs":
        power["q_abs_volume_integral"]["value"] = 0
    elif mutation == "claim_residual":
        power["balance_residual"] = 0.0
    elif mutation == "cleanup_failed":
        power["cleanup"]["removed"] = False
        power["cleanup"]["cleanup_failed"] = True
        power["cleanup"]["remaining_tags"] = ["cv-leftover"]
    elif mutation == "extra_port_patch":
        power["surface_terms"][0]["boundary_ids"].append(900)
        power["temporary_numerical_features"][0]["entity_ids"].append(900)
    with pytest.raises(Full3DScienceError):
        recompute_full3d_control_volume_power_terms(
            power, topology_readback=topology, source_contract=contract,
            source_field_readback=source_raw)


def test_area_and_volume_within_proposed_diagnostic_tolerances_are_admitted() -> None:
    topology, contract, source_raw, power = _software_inputs()
    area = control_volume_face_contract()[0]["area_um2"] * 1e-12 * (1.0 + 0.5e-6)
    power["temporary_numerical_features"][0]["value"] = area
    power["surface_area_by_role_m2"]["x_minus"] = area
    power["surface_terms"][0]["area_m2"] = area
    volume = 4598e-18 * (1.0 + 0.5e-6)
    power["temporary_numerical_features"][-2]["value"] = volume
    power["volume_integral_of_one"]["value"] = volume
    result = recompute_full3d_control_volume_power_terms(
        power, topology_readback=topology, source_contract=contract,
        source_field_readback=source_raw)
    assert result["native_result"] == "NOT_RUN"


def test_topology_route_and_three_stage_revision_chain_remain_unverified() -> None:
    topology_evidence, source_evidence, terms_request, terms_route, power = _native_route_bundle()
    topo = validate_full3d_control_volume_topology_route(
        topology_evidence["request"], topology_evidence["route_result"],
        max_execution_timeout_s=TIMEOUT_CAP)
    assert topo["native_result"] == "UNVERIFIED_PENDING_NATIVE_REVIEW"
    result = validate_full3d_control_volume_power_terms_route(
        topology_evidence=topology_evidence, source_field_evidence=source_evidence,
        request=terms_request, route_result=terms_route,
        max_execution_timeout_s=TIMEOUT_CAP)
    assert result["native_result"] == "UNVERIFIED_PENDING_NATIVE_RUN_AND_INDEPENDENT_REVIEW"
    assert result["revision_chain"] == {"topology": [4, 5], "signal_fields": [5, 6], "integrals": [6, 7]}
    assert result["power_balance_residual_over_Pin"] is None
    assert result["q_abs_status"] == "UNVERIFIED_NATIVE_PHYSICS_FIELD_MAPPING_REQUIRED"
    assert result["scientific_acceptance"] == "NOT_RUN"
    assert topology_evidence["request"]["arguments"]["arguments"]["source_artifact"] == RADIATION_ARTIFACT
    assert source_evidence["request"]["arguments"]["arguments"]["source_artifact"] == FIELD_ARTIFACT
    assert terms_request["arguments"]["arguments"]["source_artifact"] == RADIATION_ARTIFACT
    assert topology_evidence["route_result"]["response"]["data"]["source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    assert source_evidence["route_result"]["response"]["data"]["source_sha256"] == CV_FIELD_JAVA_SOURCE["source_sha256"]
    assert terms_route["response"]["data"]["source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    assert result["java_source_identity"]["source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    assert result["java_source_identity"]["worker_source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    source_routes = result["java_source_routes"]
    assert source_routes["topology"]["source_identity"]["source_artifact"] == RADIATION_ARTIFACT
    assert source_routes["signal_field_snapshot"]["source_identity"]["source_artifact"] == FIELD_ARTIFACT
    assert source_routes["integrals"]["source_identity"]["source_artifact"] == RADIATION_ARTIFACT
    assert source_routes["topology"]["source_identity"]["source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    assert source_routes["signal_field_snapshot"]["source_identity"]["source_sha256"] == CV_FIELD_JAVA_SOURCE["source_sha256"]
    assert source_routes["integrals"]["source_identity"]["source_sha256"] == CV_RADIATION_JAVA_SOURCE["source_sha256"]
    assert source_routes["topology"]["revision_after"] == source_routes["signal_field_snapshot"]["revision_before"]
    assert source_routes["signal_field_snapshot"]["revision_after"] == source_routes["integrals"]["revision_before"]


@pytest.mark.parametrize("mutation", [
    "route_readback_detached", "revision_gap", "foreign_artifact", "missing_route_field",
    "topology_source_sha", "field_source_sha", "integral_source_sha",
    "topology_entrypoint", "field_entrypoint", "integral_entrypoint",
    "topology_worker_source_sha", "field_worker_source_sha", "integral_worker_source_sha",
])
def test_power_terms_route_rejects_detached_or_nonconsecutive_inputs(mutation: str) -> None:
    topology_evidence, source_evidence, terms_request, terms_route, power = _native_route_bundle()
    topology_evidence = copy.deepcopy(topology_evidence)
    source_evidence = copy.deepcopy(source_evidence)
    terms_request = copy.deepcopy(terms_request)
    terms_route = copy.deepcopy(terms_route)
    if mutation == "route_readback_detached":
        source_evidence["route_result"]["readback"]["expressions"][0]["real"][0] = 17.0
    elif mutation == "revision_gap":
        source_evidence["request"]["execution"]["expected_revision"] = 8
    elif mutation == "foreign_artifact":
        terms_request["arguments"]["arguments"]["source_artifact"] = "/other.java"
    elif mutation == "missing_route_field":
        terms_route["readback"] = {"missing": True}
    elif mutation == "topology_source_sha":
        _mutate_terminal_java_metadata(topology_evidence["route_result"], "source_sha256", "0" * 64)
    elif mutation == "field_source_sha":
        _mutate_terminal_java_metadata(source_evidence["route_result"], "source_sha256", "0" * 64)
    elif mutation == "integral_source_sha":
        _mutate_terminal_java_metadata(terms_route, "source_sha256", "0" * 64)
    elif mutation == "topology_entrypoint":
        _mutate_terminal_java_metadata(topology_evidence["route_result"], "entrypoint", "NativeW23Full3DFixture#run")
    elif mutation == "field_entrypoint":
        _mutate_terminal_java_metadata(source_evidence["route_result"], "entrypoint", "NativeW23RadiationGeometryV2#run")
    elif mutation == "integral_entrypoint":
        _mutate_terminal_java_metadata(terms_route, "entrypoint", "NativeW23Full3DFixture#run")
    elif mutation == "topology_worker_source_sha":
        _mutate_terminal_worker_java_metadata(topology_evidence["route_result"], "source_sha256", "0" * 64)
    elif mutation == "field_worker_source_sha":
        _mutate_terminal_worker_java_metadata(source_evidence["route_result"], "source_sha256", "0" * 64)
    elif mutation == "integral_worker_source_sha":
        _mutate_terminal_worker_java_metadata(terms_route, "source_sha256", "0" * 64)
    with pytest.raises(Full3DScienceError):
        validate_full3d_control_volume_power_terms_route(
            topology_evidence=topology_evidence, source_field_evidence=source_evidence,
            request=terms_request, route_result=terms_route,
            max_execution_timeout_s=TIMEOUT_CAP)


def test_cv_routes_use_two_individually_described_single_java_sources() -> None:
    from comsol_mcp._g2_code import describe_source, read_source

    java_root = Path(__file__).resolve().parents[1] / "tools" / "java"
    specs = (CV_RADIATION_JAVA_SOURCE, CV_FIELD_JAVA_SOURCE)
    descriptions = []
    source_texts = []
    for spec in specs:
        description = describe_source(
            java_root, spec["source_artifact"], spec["entrypoint"])
        path, source, digest = read_source(java_root, spec["source_artifact"])
        assert description["source_sha256"] == spec["source_sha256"] == digest
        assert Path(description["source_artifact"]).resolve() == path.resolve()
        assert description["entrypoint"] == spec["entrypoint"]
        descriptions.append(description)
        source_texts.append(source)

    assert descriptions[0]["source_artifact"] != descriptions[1]["source_artifact"]
    assert "class NativeW23RadiationGeometryV2" in source_texts[0]
    assert "class NativeW23RadiationGeometryV2" not in source_texts[1]
    assert "class NativeW23Full3DFixture" in source_texts[1]
    assert "class NativeW23Full3DFixture" not in source_texts[0]
