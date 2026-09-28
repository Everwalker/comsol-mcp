from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
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
    QABS_CALIBRATION_CONTROL_EXPRESSIONS,
    QABS_EXPRESSION_CONTRACT_SCHEMA,
    QABS_MAPPING_CERTIFICATE_SCHEMA,
    Full3DScienceError,
    build_full3d_bma_frequency_producer_prepare_dispatch,
    build_full3d_bma_frequency_producer_run_dispatch,
    build_qabs_mapping_calibration_dispatch,
    build_qabs_expression_contract,
    build_full3d_control_volume_power_terms_dispatch,
    build_full3d_control_volume_topology_dispatch,
    build_raw_field_contract,
    build_raw_field_dispatch,
    circular_port_quadrature,
    recompute_full3d_control_volume_power_terms,
    validate_full3d_bma_frequency_producer_prepare_route,
    validate_full3d_bma_frequency_producer_run_readback,
    validate_qabs_mapping_calibration_route,
    validate_full3d_control_volume_power_terms_route,
    validate_full3d_control_volume_topology_route,
)
from tools import w23_full3d_science as science
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
    equation_inventory = {
        "schema_id": "urn:comsol-mcp:w23:physics-equation-view-raw-inventory:1.0.0",
        "physics_tag": "ewfd", "feature_info_table_id": "Expression", "options": ["all"],
        "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE",
        "mapping_authentication": "NOT_AUTHENTICATED",
        "entries": [
            {"parent_path": "ewfd/electric", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "electricInfo", "feature_info_name": "synthetic electric control",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["ewfd.Ex", "V/m"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
            {"parent_path": "ewfd/magnetic", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "magneticInfo", "feature_info_name": "synthetic magnetic control",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["ewfd.Hx", "A/m"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
            {"parent_path": "ewfd/material1", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "lossInfo", "feature_info_name": "synthetic test target",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["test.loss", "W/m^3"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
        ],
    }
    for phase in ("before", "after"):
        raw["source_cohort"][phase]["fixture_explicit_configuration"]["physics"][
            "equation_view_inventory"] = copy.deepcopy(equation_inventory)
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
        "q_abs_volume_integral": {"status": "NOT_AVAILABLE_EXPLICIT_QABS_CONTRACT_REQUIRED",
                                  "unit": "W", "value": None, "expression": "NOT_PROVIDED"},
        "q_abs_field_mapping": {"status": "NO_EXPLICIT_QABS_EXPRESSION_CONTRACT",
                                "integral_authorized": False, "mapping_status": "UNVERIFIED",
                                "equation_inventory_matches_source": True},
        "q_abs_expression_contract": None,
        "q_abs_mapping_certificate": None,
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
    assert CV_POWER_BALANCE_POLICY["q_abs_expression"] == "EXPLICIT_CONTRACT_REQUIRED"
    assert CV_POWER_BALANCE_POLICY["q_abs_generic_expression_interface"] == "IMPLEMENTED_WITH_AUTHENTICATION_GATE"
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
    assert result["q_abs_volume_integral"] is None
    assert result["q_abs_generic_expression_interface"] == "IMPLEMENTED_WITH_AUTHENTICATION_GATE"
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


def _synthetic_test_only_qabs_certificate(source_raw: dict, contract: dict, monkeypatch) -> dict:
    """Test-only schema injection; production registry remains empty and caller cannot dispatch it."""
    inventory = source_raw["source_cohort"]["before"]["fixture_explicit_configuration"][
        "physics"]["equation_view_inventory"]
    columns = {"expression": 0, "unit": 1}
    controls = [dict(row) for row in QABS_CALIBRATION_CONTROL_EXPRESSIONS]
    calibration_sha = "c" * 64
    approval_sha = "d" * 64
    calibration_route_sha = science._sha256({"test_only": True})
    candidate = science._infer_qabs_column_schema_candidate(inventory, contract)
    approved_schema = {"comsol_version": "6.4.0.293",
        "feature_info_table_id": "Expression", "options": ["all"],
        "column_indices": columns, "control_expressions": controls,
        "minimum_distinct_feature_info_owners": 2,
        "calibration_readback_sha256": calibration_sha,
        "calibration_route_binding_sha256": calibration_route_sha,
        "approval_evidence_sha256": approval_sha}
    approval_id = "TEST_ONLY_SYNTHETIC_SCHEMA"
    monkeypatch.setattr(science, "QABS_APPROVED_COLUMN_SCHEMA_REGISTRY",
                        {approval_id: approved_schema})
    inventory_sha = hashlib.sha256(json.dumps(
        inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")).hexdigest()
    return {
        "schema_id": QABS_MAPPING_CERTIFICATE_SCHEMA,
        "status": "PINNED_NATIVE_COLUMN_SCHEMA_EVIDENCE",
        "approved_schema_id": approval_id, "approved_schema": copy.deepcopy(approved_schema),
        "physics_tag": "ewfd", "expression": contract["expression"],
        "density_unit": "W/m^3", "equation_inventory": copy.deepcopy(inventory),
        "equation_inventory_sha256": inventory_sha,
        "feature_info_owner": {"parent_path": "ewfd/material1", "feature_info_tag": "lossInfo"},
        "row_index": 0, "column_indices": columns,
        "calibration": {"status": "NATIVE_CALIBRATION_ROUTE_VALIDATED",
            "readback_sha256": calibration_sha, "comsol_version": "6.4.0.293",
            "schema_candidate": candidate,
            "route_binding": {"test_only": True}},
        "source_route": {"test_only": True}, "native_run_status": "NOT_RUN",
    }


def test_qabs_unit_only_contract_never_dispatches_an_integral() -> None:
    topology, contract, source_raw, power = _software_inputs()
    qabs_contract = build_qabs_expression_contract("test.loss")
    power["q_abs_expression_contract"] = qabs_contract
    power["q_abs_volume_integral"] = {
        "status": "NOT_AVAILABLE_REVIEWED_COLUMN_MAPPING_CERTIFICATE_REQUIRED",
        "unit": "W", "value": None, "expression": "test.loss"}
    power["q_abs_field_mapping"] = {
        "schema_id": QABS_EXPRESSION_CONTRACT_SCHEMA, "status": "UNIT_READBACK_ONLY_MAPPING_CERTIFICATE_REQUIRED",
        "mapping_status": "UNVERIFIED", "integral_authorized": False,
        "expression": "test.loss", "physics_tag": "ewfd",
        "declared_density_unit": "W/m^3", "evaluated_density_unit": "W/m^3"}
    result = recompute_full3d_control_volume_power_terms(
        power, topology_readback=topology, source_contract=contract,
        source_field_readback=source_raw)
    assert result["q_abs_volume_integral"] is None
    assert result["power_balance_residual_over_Pin"] is None
    assert result["scientific_acceptance"] == "NOT_RUN"


def _frequency_producer_evidence_bundle(*, java_output_association=None):
    project_id = "project-frequency-producer"
    model_tag = "M1"
    model_ref = copy.deepcopy(MODEL_REF)
    timeout = TIMEOUT_CAP
    study_tag = "std3dFullBmaFrequencyProducerV1"
    sequence_tag = "solW23FullBmaFreqV1"
    dataset_tag = "dsetW23Full3dBmaFrequencyV1"
    baseline_steps = [
        {"tag": "bmaInput3d", "feature_type": "BoundaryModeAnalysis", "PortName": "1",
         "modeFreq": "f0", "neigs": 2, "eigwhich": "effective_mode_index",
         "shiftactive": "on", "shift": "1.45"},
        {"tag": "bmaOutput3d", "feature_type": "BoundaryModeAnalysis", "PortName": "2",
         "modeFreq": "f0", "neigs": 2, "eigwhich": "effective_mode_index",
         "shiftactive": "on", "shift": "1.45"},
        {"tag": "freq3d", "feature_type": "Frequency", "plist": "f0"},
    ]
    cloned_steps = [dict(step, tag=tag) for step, tag in zip(baseline_steps, (
        "producerBmaInput3d", "producerBmaOutput3d", "producerFreq3d"))]
    solver_tree = [
        {"path": "solW23FullBmaFreqV1/st1", "feature_type": "StudyStep"},
        {"path": "solW23FullBmaFreqV1/st2", "feature_type": "StudyStep"},
        {"path": "solW23FullBmaFreqV1/st3", "feature_type": "StudyStep"},
    ]
    bindings = [
        {"study": study_tag, "studystep": "producerBmaInput3d", "feature_type": "StudyStep",
         "path": solver_tree[0]["path"]},
        {"study": study_tag, "studystep": "producerBmaOutput3d", "feature_type": "StudyStep",
         "path": solver_tree[1]["path"]},
        {"study": study_tag, "studystep": "producerFreq3d", "feature_type": "StudyStep",
         "path": solver_tree[2]["path"]},
    ]
    sequence = {"tag": sequence_tag, "feature_type": "SolverSequence", "parent_study": study_tag,
        "solver_tree_features": solver_tree,
        "study_step_bindings_in_solver_tree_order": bindings,
        "terminal_frequency_binding": bindings[2],
        "output_association_status": "UNVERIFIED_UNTIL_EXACT_NEW_SOLUTIONINFO_AND_DATASET_READBACK",
        "output_path_readback": "DIRECT_SEQUENCE_RESULT_CANDIDATE_NO_POST_FREQUENCY_STORE_SOLUTION",
        "store_solution_feature_paths": [], "store_solution_paths_after_frequency": []}
    dataset = {"tag": dataset_tag, "feature_type": "Solution", "solution": sequence_tag}
    pre_state = {"is_valid": True, "solver_sequence_is_empty": True,
        "outer_solnums": [], "solution_pairs": [], "pair_count": 0}
    prep_raw = {"fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
        "status": "FULL_BMA_FREQUENCY_PRODUCER_CONFIGURED_NOT_SOLVED",
        "native_result": "SOFTWARE_TEST_FIXTURE", "study_or_solver_invoked": False,
        "producer_status": "PREPARED_ONLY_NOT_PRODUCER_EVIDENCE",
        "producer_step_binding": "UNVERIFIED_UNTIL_EXACT_SEQUENCE_RUN_AND_OUTPUT_READBACK",
        "managed_identity": {"project_id": project_id, "model_tag": model_tag,
            "model_ref": model_ref, "expected_revision": 41},
        "original_std3d_steps": baseline_steps,
        "producer_study": {"study_tag": study_tag, "study_steps": cloned_steps},
        "solver_sequence": sequence, "dataset": dataset,
        "pre_solve_solution_state": pre_state,
        "solver_work_plan": {"run_all_calls": 1, "bma_study_steps": 2,
            "frequency_study_steps": 1, "eigensolutions_per_bma_step_readback": [2, 2],
            "study_run_calls": 0, "old_solution_clear_calls": 0}}

    prep_request = build_full3d_bma_frequency_producer_prepare_dispatch(
        source_artifact=FIELD_ARTIFACT, project_id=project_id, model_ref=model_ref,
        model_tag=model_tag, revision=41, request_id="w23-frequency-prepare",
        idempotency_key="w23-frequency-prepare-key")
    prep_data = _managed_java_data(CV_FIELD_JAVA_SOURCE, prep_raw)
    prep_route = _bma_mapping_route_result(prep_request, label="frequency-prepare",
        response_data=prep_data, revision_delta=1)
    prep_route["readback"] = copy.deepcopy(prep_raw)
    prep_checked = validate_full3d_bma_frequency_producer_prepare_route(
        request=prep_request, route_result=prep_route, project_id=project_id,
        model_tag=model_tag, model_ref=model_ref, max_execution_timeout_s=timeout)

    run_request = build_full3d_bma_frequency_producer_run_dispatch(
        source_artifact=FIELD_ARTIFACT, preparation_readback=prep_raw,
        project_id=project_id, model_ref=model_ref, model_tag=model_tag,
        revision=42, request_id="w23-frequency-run", idempotency_key="w23-frequency-run-key")
    pairs = [
        {"outer_index": 1, "inner_index": 1, "solnum": 1, "solver_sequence_tag": sequence_tag,
         "parameters": [{"name": "f0", "value": 299792458000000.0}]},
        {"outer_index": 1, "inner_index": 2, "solnum": 2, "solver_sequence_tag": sequence_tag,
         "parameters": [{"name": "f0", "value": 299792458000000.0}]},
    ]
    post_state = {"is_valid": True, "solver_sequence_is_empty": False,
        "outer_solnums": [1], "solution_pairs": pairs,
        "parameter_values_source": "SolutionInfo.getPNames/getPvals(actual outer-inner tuples)"}
    run_raw = {"fixture_id": "w23_full3d_fiber_ball_lens_vector_pml_v1",
        "status": "FULL_BMA_FREQUENCY_RUN_RETURNED_SOLUTIONINFO_TUPLES",
        "native_result": "SOFTWARE_TEST_FIXTURE", "study_or_solver_invoked": True,
        "solver_calls": 1, "study_run_calls": 0,
        "producer_status": "UNVERIFIED_PENDING_NATIVE_OUTPUT_STEP_ASSOCIATION",
        "producer_step_binding": "UNVERIFIED_PENDING_TERMINAL_FREQUENCY_OUTPUT_READBACK",
        "field_mapping_status": "UNVERIFIED",
        "managed_identity": {"project_id": project_id, "model_tag": model_tag,
            "model_ref": model_ref, "expected_revision": 42},
        "solver_sequence": sequence, "dataset": dataset,
        "pre_solve_solution_state": pre_state,
        "invocation": {"method": "SolverSequence.runAll", "solver_sequence_tag": sequence_tag,
            "parent_study_tag": study_tag, "terminal_study_step_tag": "producerFreq3d",
            "method_returned": True},
        "producer_study": {"study_tag": study_tag, "study_steps": cloned_steps,
            "last_computation_date_ms": 1790000000000, "last_computation_version": "6.4.0.293"},
        "post_solve_solution_state": post_state,
        "solver_work": {"run_all_calls": 1, "bma_study_steps": 2,
            "frequency_study_steps": 1, "study_run_calls": 0, "old_solution_clear_calls": 0},
        "output_association": copy.deepcopy(
            sequence if java_output_association is None else java_output_association)}
    run_route = _bma_mapping_route_result(run_request, label="frequency-run",
        response_data=_managed_java_data(CV_FIELD_JAVA_SOURCE, run_raw), revision_delta=1)
    run_route["readback"] = copy.deepcopy(run_raw)
    validate_full3d_bma_frequency_producer_run_readback(
        run_raw, preparation_readback=prep_raw, project_id=project_id,
        model_tag=model_tag, model_ref=model_ref, run_request=run_request,
        route_result=run_route, max_execution_timeout_s=timeout)
    evidence = {"preparation_request": prep_request,
        "preparation_route_result": prep_route, "run_request": run_request,
        "run_route_result": run_route, "readback": run_raw,
        "project_id": project_id, "model_tag": model_tag, "model_ref": model_ref,
        "selected_tuple": {"outer_index": 1, "inner_index": 1, "solnum": 1},
        "max_execution_timeout_s": timeout}
    return evidence


def test_java_output_association_is_consumed_by_python_frequency_route(tmp_path: Path) -> None:
    javac = shutil.which("javac")
    java = shutil.which("java")
    if javac is None or java is None:
        pytest.skip("NOT_RUN: Java compiler/runtime unavailable for cross-language contract")

    repo = Path(__file__).resolve().parents[1]
    comsol_jars = Path("/Applications/COMSOL64/Multiphysics/apiplugins")
    jars = sorted(comsol_jars.glob("*.jar")) if comsol_jars.is_dir() else []
    if not jars:
        pytest.skip("NOT_RUN: COMSOL Java API classpath unavailable for producer contract")
    classpath = os.pathsep.join(str(path) for path in jars)
    classes = tmp_path / "java-classes"
    classes.mkdir()
    compile_result = subprocess.run(
        [javac, "-encoding", "UTF-8", "-classpath", classpath,
         "-d", str(classes), str(repo / "tools/java/NativeW23Full3DFixture.java"),
         str(repo / "tests/java/W23OutputAssociationContractHarness.java")],
        cwd=repo, capture_output=True, text=True, timeout=60)
    assert compile_result.returncode == 0, compile_result.stderr

    execution = subprocess.run(
        [java, "-cp", classes.__str__() + os.pathsep + classpath,
         "W23OutputAssociationContractHarness"],
        cwd=repo, capture_output=True, text=True, timeout=30)
    assert execution.returncode == 0, execution.stderr
    emitted_association = json.loads(execution.stdout)
    assert isinstance(emitted_association, dict)

    # The Java helper output is passed into the existing Python route validator.
    evidence = _frequency_producer_evidence_bundle(
        java_output_association=emitted_association)
    sequence = evidence["readback"]["solver_sequence"]
    assert evidence["readback"]["output_association"] == sequence
    assert emitted_association["output_association_status"] == (
        "UNVERIFIED_UNTIL_EXACT_NEW_SOLUTIONINFO_AND_DATASET_READBACK")


def _managed_java_data(source_spec: dict, readback: dict) -> dict:
    identity = {"source_sha256": source_spec["source_sha256"],
        "entrypoint": source_spec["entrypoint"], "readback": copy.deepcopy(readback)}
    return {"source_sha256": source_spec["source_sha256"],
        "entrypoint": source_spec["entrypoint"],
        "worker": {"ok": True, "status": "SUCCEEDED", "result": identity},
        "readback": {"executed": True, "readback": copy.deepcopy(readback)}}


def _qabs_calibration_inventory():
    return {"schema_id": "urn:comsol-mcp:w23:physics-equation-view-raw-inventory:1.0.0",
        "physics_tag": "ewfd", "feature_info_table_id": "Expression", "options": ["all"],
        "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE",
        "mapping_authentication": "NOT_AUTHENTICATED", "entries": [
            {"parent_path": "ewfd/electric", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "electricInfo", "feature_info_name": "electric control",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["ewfd.Ex", "V/m"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
            {"parent_path": "ewfd/magnetic", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "magneticInfo", "feature_info_name": "magnetic control",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["ewfd.Hx", "A/m"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
            {"parent_path": "ewfd/material1", "parent_type": "ElectromagneticWaves",
             "feature_info_tag": "lossInfo", "feature_info_name": "candidate loss",
             "table_id": "Expression", "options": ["all"], "row_count": 1,
             "row_widths": [2], "rows": [["test.loss", "W/m^3"]],
             "column_semantics": "NOT_EXPOSED_BY_FEATUREINFO_GETINFOTABLE"},
        ]}


def _qabs_calibration_route(producer_evidence, contract, *, inventory=None,
                            source_identity_delta=0, target_evaluated_unit="W/m^3"):
    request = build_qabs_mapping_calibration_dispatch(
        producer_evidence=producer_evidence, contract=contract,
        request_id="w23-qabs-calibration", idempotency_key="w23-qabs-calibration-key")
    producer_readback = producer_evidence["readback"]
    seq = producer_readback["solver_sequence"]["tag"]
    dataset = producer_readback["dataset"]["tag"]
    selected = dict(producer_evidence["selected_tuple"])
    selected_with_sequence = {**selected, "solver_sequence_tag": seq}
    pair = next(row for row in producer_readback["post_solve_solution_state"]["solution_pairs"]
                if row["outer_index"] == selected["outer_index"]
                and row["inner_index"] == selected["inner_index"])
    source_identity = {"dataset": {"tag": dataset, "feature_type": "Solution",
        "properties": {"solution": seq}},
        "stored_solution": {"solution_tag": seq,
            "study_tag": producer_readback["producer_study"]["study_tag"],
            "computation_date_ms": producer_readback["producer_study"]["last_computation_date_ms"] + source_identity_delta,
            "computation_version": producer_readback["producer_study"]["last_computation_version"],
            "parameter_axis": [{"name": "f0", "value": 299792458000000.0}],
            "solution_info": {"is_valid": True, "solver_sequence_is_empty": False,
                "outer_solnums": [1], "solution_pairs": [copy.deepcopy(row)
                    for row in producer_readback["post_solve_solution_state"]["solution_pairs"]],
                "pair_count": len(producer_readback["post_solve_solution_state"]["solution_pairs"])},
            "selected_tuple": selected_with_sequence}}
    inventory = copy.deepcopy(inventory if inventory is not None else _qabs_calibration_inventory())
    observations, row_candidate = science._qabs_expression_unit_observations(inventory, contract)
    cross_feature = science._qabs_cross_feature_summary(observations)
    target_observation = observations[2] if len(observations) == 3 \
        and "expression_column" in observations[2] else None
    candidate = row_candidate if target_evaluated_unit == "W/m^3" else None
    target_unit = {"expression": contract["expression"], "expected_unit": "W/m^3",
        "evaluated_unit": target_evaluated_unit, "source": "Model.param().evaluateUnit"}
    raw = {"schema_id": "urn:comsol-mcp:w23:qabs-mapping-calibration:1.0.0",
        "native_result": "COMSOL_NATIVE_QABS_MAPPING_CALIBRATION_READBACK",
        "status": "CANDIDATE_SCHEMA_NEEDS_INDEPENDENT_REVIEW" if candidate is not None
            else "CANDIDATE_MAPPING_UNRESOLVED",
        "study_or_solver_invoked": False, "model_mutated": False,
        "managed_identity": dict(request["arguments"]["arguments"]["arguments"]["managed_identity"]),
        "dataset_tag": dataset, "solution_tag": seq, "selected_tuple": selected,
        "native_source_identity": source_identity,
        "comsol_version": producer_readback["producer_study"]["last_computation_version"],
        "q_abs_expression_contract": dict(contract),
        "control_expressions": [dict(row) for row in QABS_CALIBRATION_CONTROL_EXPRESSIONS],
        "control_unit_readbacks": [
            {"expression": "ewfd.Ex", "expected_unit": "V/m", "evaluated_unit": "V/m",
             "source": "Model.param().evaluateUnit"},
            {"expression": "ewfd.Hx", "expected_unit": "A/m", "evaluated_unit": "A/m",
             "source": "Model.param().evaluateUnit"},
        ], "control_row_observations": observations[:2],
        "equation_view_inventory": inventory, "target_unit_readback": target_unit,
        "target_row_observation": target_observation, "cross_feature_comparison": cross_feature,
        "schema_candidate": candidate,
        "production_schema_approval": "NOT_APPROVED_IN_CALIBRATION_ROUTE"}
    if source_identity_delta:
        raw["native_source_identity"]["stored_solution"]["computation_date_ms"] += source_identity_delta
    route = _bma_mapping_route_result(request, label="qabs-calibration",
        response_data=_managed_java_data(CV_RADIATION_JAVA_SOURCE, raw), revision_delta=1)
    route["readback"] = copy.deepcopy(raw)
    evidence = {"producer_evidence": producer_evidence, "contract": contract,
        "request": request, "route_result": route, "max_execution_timeout_s": TIMEOUT_CAP}
    return evidence


def test_full_bma_frequency_producer_and_qabs_calibration_routes_remain_unverified() -> None:
    producer = _frequency_producer_evidence_bundle()
    contract = build_qabs_expression_contract("test.loss")
    calibration_evidence = _qabs_calibration_route(producer, contract)
    result = validate_qabs_mapping_calibration_route(calibration_evidence)
    assert result["native_result"] == "CANDIDATE_CALIBRATION_READBACK_NOT_APPROVED_SCHEMA"
    assert result["mapping_status"] == "UNVERIFIED_PENDING_INDEPENDENT_REVIEW_AND_SOURCE_PIN"
    assert result["producer_step_binding"] == "UNVERIFIED"
    assert result["schema_candidate"]["cross_feature_comparison"]["distinct_owner_count"] == 3
    assert result["scientific_acceptance"] == "NOT_RUN"
    assert science.QABS_APPROVED_COLUMN_SCHEMA_REGISTRY == {}


@pytest.mark.parametrize("mutation", [
    "missing_frequency_step", "reordered_study_steps", "foreign_terminal_binding",
    "nonempty_pre_solve_solution", "foreign_dataset_solution",
])
def test_frequency_producer_preparation_rejects_graph_or_output_detachment(mutation) -> None:
    producer = _frequency_producer_evidence_bundle()
    preparation = copy.deepcopy(producer["preparation_route_result"]["readback"])
    if mutation == "missing_frequency_step":
        preparation["producer_study"]["study_steps"].pop()
    elif mutation == "reordered_study_steps":
        preparation["producer_study"]["study_steps"][0], preparation["producer_study"]["study_steps"][1] = (
            preparation["producer_study"]["study_steps"][1],
            preparation["producer_study"]["study_steps"][0])
    elif mutation == "foreign_terminal_binding":
        binding = preparation["solver_sequence"]["study_step_bindings_in_solver_tree_order"][2]
        binding["studystep"] = "producerBmaOutput3d"
        preparation["solver_sequence"]["terminal_frequency_binding"] = copy.deepcopy(binding)
    elif mutation == "nonempty_pre_solve_solution":
        preparation["pre_solve_solution_state"]["solver_sequence_is_empty"] = False
    else:
        preparation["dataset"]["solution"] = "foreignSequence"
    with pytest.raises(Full3DScienceError):
        science.validate_full3d_bma_frequency_producer_preparation(
            preparation, project_id=producer["project_id"],
            model_tag=producer["model_tag"], model_ref=producer["model_ref"])


@pytest.mark.parametrize("mutation", ["selected_tuple", "computation_date", "request_id", "revision"])
def test_qabs_calibration_rejects_foreign_frequency_output_or_route(mutation) -> None:
    producer = _frequency_producer_evidence_bundle()
    contract = build_qabs_expression_contract("test.loss")
    evidence = _qabs_calibration_route(producer, contract)
    if mutation == "selected_tuple":
        evidence["route_result"]["response"]["data"]["worker"]["result"]["readback"]["selected_tuple"]["inner_index"] = 2
        evidence["route_result"]["response"]["data"]["readback"]["readback"]["selected_tuple"]["inner_index"] = 2
        evidence["route_result"]["readback"]["selected_tuple"]["inner_index"] = 2
    elif mutation == "computation_date":
        evidence = _qabs_calibration_route(producer, contract, source_identity_delta=1)
    elif mutation == "request_id":
        evidence["request"]["execution"]["request_id"] = "foreign-request"
    else:
        evidence["route_result"]["response"]["execution"]["revision"] += 1
    with pytest.raises(Full3DScienceError):
        validate_qabs_mapping_calibration_route(evidence)


def test_qabs_calibration_preserves_unresolved_unit_and_single_feature_diagnostics() -> None:
    producer = _frequency_producer_evidence_bundle()
    contract = build_qabs_expression_contract("test.loss")
    one_owner = _qabs_calibration_inventory()
    combined = [["ewfd.Ex", "V/m"], ["ewfd.Hx", "A/m"], ["test.loss", "W/m^3"]]
    one_owner["entries"] = [{**one_owner["entries"][0], "row_count": 3,
        "row_widths": [2, 2, 2], "rows": combined}]
    evidence = _qabs_calibration_route(producer, contract, inventory=one_owner,
        target_evaluated_unit="V/m")
    result = validate_qabs_mapping_calibration_route(evidence)
    assert result["schema_candidate"] is None
    assert result["readback"]["cross_feature_comparison"]["distinct_owner_count"] == 1
    assert result["mapping_status"] == "UNVERIFIED_PENDING_INDEPENDENT_REVIEW_AND_SOURCE_PIN"
    assert result["native_result"] != "VERIFIED"


def test_qabs_certificate_gated_integral_recomputes_but_never_claims_acceptance(monkeypatch) -> None:
    topology, source_contract, source_raw, power = _software_inputs()
    qabs_contract = build_qabs_expression_contract("test.loss")
    certificate = _synthetic_test_only_qabs_certificate(source_raw, qabs_contract, monkeypatch)
    qabs_row = {"tag": "cv_qabs", "feature_type": "IntVolume",
        "dataset": SOURCE["dataset_id"], "expression": ["test.loss"], "unit": "W",
        "innerinput": "manual", "solnum": str(SOURCE["inner_index"]),
        "outerinput": "manual", "outersolnum": str(SOURCE["outer_index"]),
        "solrepresentation": "solnum", "geometry": "geom3d", "entity_dimension": 3,
        "entity_ids": [1], "complex": False, "result_shape": [1, 1], "value": 0.25}
    power["temporary_numerical_features"].append(qabs_row)
    power["cleanup"].update(created_count=15, removed_count=15)
    power["q_abs_expression_contract"] = qabs_contract
    power["q_abs_mapping_certificate"] = certificate
    power["q_abs_volume_integral"] = copy.deepcopy(qabs_row)
    power["q_abs_field_mapping"] = {
        "schema_id": QABS_EXPRESSION_CONTRACT_SCHEMA,
        "status": "PINNED_COLUMN_CERTIFICATE_AND_NATIVE_UNIT_READBACK_MATCH",
        "mapping_status": "CERTIFICATE_BOUND_TO_CURRENT_NATIVE_EQUATION_VIEW",
        "integral_authorized": True, "expression": "test.loss", "physics_tag": "ewfd",
        "declared_density_unit": "W/m^3", "evaluated_density_unit": "W/m^3",
        "equation_inventory_sha256": certificate["equation_inventory_sha256"]}
    power["balance_residual"] = (
        sum(power["signed_surface_flux_by_role_w"].values()) + 0.25) / power["positive_incident_power"]["value"]
    result = recompute_full3d_control_volume_power_terms(
        power, topology_readback=topology, source_contract=source_contract,
        source_field_readback=source_raw)
    assert result["q_abs_volume_integral"] == pytest.approx(0.25)
    assert result["power_balance_residual_over_Pin"] == pytest.approx(power["balance_residual"])
    assert result["absolute_balance_tolerance"] == "NOT_FROZEN"
    assert result["scientific_acceptance"] == "NOT_RUN"
    assert result["native_result"] == "NOT_RUN"


def test_production_qabs_schema_registry_is_empty_and_fake_review_claim_is_rejected() -> None:
    topology, contract, source_raw, _ = _software_inputs()
    qabs_contract = build_qabs_expression_contract("test.loss")
    inventory = source_raw["source_cohort"]["before"]["fixture_explicit_configuration"][
        "physics"]["equation_view_inventory"]
    forged = {"schema_id": QABS_MAPPING_CERTIFICATE_SCHEMA,
        "status": "PINNED_NATIVE_COLUMN_SCHEMA_EVIDENCE", "approved_schema_id": "caller-approval",
        "physics_tag": "ewfd", "expression": "test.loss", "density_unit": "W/m^3",
        "equation_inventory": inventory}
    assert science.QABS_APPROVED_COLUMN_SCHEMA_REGISTRY == {}
    with pytest.raises(Full3DScienceError):
        science._validate_qabs_mapping_certificate(
            forged, contract=qabs_contract, equation_inventory=inventory)


def test_qabs_column_candidate_requires_independent_controls_and_unique_raw_rows() -> None:
    _, _, source_raw, _ = _software_inputs()
    inventory = source_raw["source_cohort"]["before"]["fixture_explicit_configuration"][
        "physics"]["equation_view_inventory"]
    contract = build_qabs_expression_contract("test.loss")
    candidate = science._infer_qabs_column_schema_candidate(inventory, contract)
    assert candidate is not None
    assert candidate["column_indices"] == {"expression": 0, "unit": 1}
    changed = copy.deepcopy(inventory)
    changed["entries"][0]["rows"][0][1] = "A/m"
    assert science._infer_qabs_column_schema_candidate(changed, contract) is None
    changed = copy.deepcopy(inventory)
    changed["entries"][2]["rows"].append(["test.loss", "W/m^3"])
    assert science._infer_qabs_column_schema_candidate(changed, contract) is None


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
