from __future__ import annotations

import cmath
import copy
import math

import pytest

from tools import w23_te_science_protocol as protocol


def _stored_solver(tag: str, value_real: float, value_imag: float):
    return {
        "tag": tag, "sequence_type": "Stored", "solution_type": "Eigenfrequency",
        "study_tag": "std1", "attached": False, "get_size": [100, 1],
        "pnames": ["lambda"],
        "parameter_values_status": "RAW_TYPED_COMPLEX_VALUES_CAPTURED_NOT_INTERPRETED",
        "parameter_values_by_solnum": [{
            "solnum": 1, "real": [value_real], "imaginary": [value_imag],
            "parameter_units": [{"name": "lambda", "status": "OBSERVED", "value": "1/m"}],
        }],
    }


def _stored_dataset_receipt(dataset_tag: str, solution_tag: str,
                            value_real: float, value_imag: float):
    return {
        "dataset": {"tag": dataset_tag, "type_id": "Solution"},
        "solution_indices": {
            "dataset": dataset_tag, "solution": solution_tag,
            "binding_complete": False, "axis_metadata_complete": False,
            "parameters_complete": False,
            "dataset_binding": {"binding_complete": True, "solution": solution_tag,
                                 "dataset": dataset_tag, "dataset_type": "Solution"},
            "read_errors": [{"code": "SOLUTION_AXIS_METADATA_UNAVAILABLE"}],
            "solver_sequence_parameter_observation": {
                "status": "OBSERVED_TYPED_COMPLEX_PARAMETER_VALUES",
                "solution_tag": solution_tag, "sequence_type": "Stored",
                "solution_type": "Eigenfrequency", "study": "std1", "attached": False,
                "get_size": [100, 1], "pnames": ["lambda"],
                "solution_values": [{"solnum": 1,
                    "real_getter": {"raw": [value_real]},
                    "imag_getter": {"raw": [value_imag]}}],
                "unit_observations": [{"name": "lambda", "value": "1/m"}],
                "parameters_complete": False,
            },
        },
    }


def _saved_bma_inventory():
    bindings = [
        {"path": "bmaInput", "tree_order": 0, "feature_type": "StudyStep",
         "study": "std1", "studystep": "bmaInput", "solver_sequence": "sol1"},
        {"path": "bmaOutput", "tree_order": 2, "feature_type": "StudyStep",
         "study": "std1", "studystep": "bmaOutput", "solver_sequence": "sol1"},
        {"path": "freq", "tree_order": 4, "feature_type": "StudyStep",
         "study": "std1", "studystep": "freq", "solver_sequence": "sol1"},
    ]
    return {
        "study_tag": "std1",
        "study_steps_in_configured_order": [
            {"tag": "bmaInput", "feature_type": "BoundaryModeAnalysis",
             "PortName": "1", "modeFreq": "f0"},
            {"tag": "bmaOutput", "feature_type": "BoundaryModeAnalysis",
             "PortName": "2", "modeFreq": "f0"},
            {"tag": "freq", "feature_type": "Frequency", "plist": "f0"},
        ],
        "solver_sequences_for_study": [
            {"tag": "sol1", "sequence_type": "SolverSequence", "solution_type": "Parametric",
             "study_tag": "std1", "attached": True,
             "solver_tree_features": [
                {"path": "bmaInput", "tree_order": 0, "feature_type": "StudyStep"},
                {"path": "su1", "tree_order": 1, "feature_type": "StoreSolution",
                 "stored_solution_reference": "sol2",
                 "stored_solution_reference_getter": "StoreSolution.getString('sol')"},
                {"path": "bmaOutput", "tree_order": 2, "feature_type": "StudyStep"},
                {"path": "su2", "tree_order": 3, "feature_type": "StoreSolution",
                 "stored_solution_reference": "sol3",
                 "stored_solution_reference_getter": "StoreSolution.getString('sol')"},
                {"path": "freq", "tree_order": 4, "feature_type": "StudyStep"},
            ],
             "study_step_bindings_in_solver_tree_order": bindings},
            _stored_solver("sol2", 0.0, -6244288.968380504),
            _stored_solver("sol3", -1.6359882555849607e-8, -6244288.968380514),
        ],
    }


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


def test_bma_stored_solution_roles_follow_study_tree_store_and_dataset_bindings():
    inventory = _saved_bma_inventory()
    datasets = {"solution_index_receipts": [
        _stored_dataset_receipt("dset2", "sol2", 0.0, -6244288.968380504),
        _stored_dataset_receipt("dset3", "sol3", -1.6359882555849607e-8, -6244288.968380514),
    ]}
    result = protocol.resolve_bma_stored_dataset_bindings(inventory, datasets)
    assert result["status"] == "PASS_BMA_STUDY_STORE_DATASET_BINDINGS"
    assert result["science_acceptance"] == "NOT_EVALUATED"
    assert [(row["study_step_tag"], row["store_solution_reference"], row["dataset_tag"])
            for row in result["bindings"]] == [
                ("bmaInput", "sol2", "dset2"), ("bmaOutput", "sol3", "dset3")]
    assert all(row["solution_info_axis_metadata_complete"] is False for row in result["bindings"])
    assert all(row["solution_info_parameters_complete"] is False for row in result["bindings"])
    assert result["bindings"][1]["sequence_parameter_observation"]["values"][0]["parameters"][0] == {
        "name": "lambda", "real": -1.6359882555849607e-8,
        "imag": -6244288.968380514, "unit": "1/m"}


@pytest.mark.parametrize("mutate", ["missing_store", "duplicate_dataset", "wrong_sequence_value"])
def test_bma_stored_solution_binding_is_fail_closed(mutate):
    inventory = _saved_bma_inventory()
    datasets = {"solution_index_receipts": [
        _stored_dataset_receipt("dset2", "sol2", 0.0, -6244288.968380504),
        _stored_dataset_receipt("dset3", "sol3", -1.6359882555849607e-8, -6244288.968380514),
    ]}
    if mutate == "missing_store":
        inventory["solver_sequences_for_study"][0]["solver_tree_features"][1]["stored_solution_reference"] = None
    elif mutate == "duplicate_dataset":
        datasets["solution_index_receipts"].append(
            _stored_dataset_receipt("dset3_duplicate", "sol3", -1.6359882555849607e-8,
                                    -6244288.968380514))
    else:
        datasets["solution_index_receipts"][1]["solution_indices"][
            "solver_sequence_parameter_observation"]["solution_values"][0][
                "imag_getter"]["raw"][0] += 1.0
    with pytest.raises(protocol.ScienceProtocolError):
        protocol.resolve_bma_stored_dataset_bindings(inventory, datasets)


def _main_frequency_inventory():
    frequency = 1.9341448903225803e14
    scope = {"getLevelNames": ["freq"], "getLevels": 1,
             "getOuterSolnum": [], "getPNamesOuter": [],
             "getPUnitsOuter": [], "read_errors": []}
    java = {
        "tag": "sol1", "sequence_type": "SolverSequence",
        "solution_type": "Parametric", "study_tag": "std1", "attached": True,
        "get_size": [100, 1], "get_size_multi": [1, 1], "pnames": ["freq"],
        "parameter_values_status": "RAW_TYPED_COMPLEX_VALUES_CAPTURED_NOT_INTERPRETED",
        "parameter_values_by_solnum": [{
            "solnum": 1, "real": [frequency], "imaginary": [0.0],
            "parameter_units": [{"name": "freq", "status": "OBSERVED", "value": "Hz"}],
        }],
        "solution_info_count_scope": scope,
        "study_step_bindings_in_solver_tree_order": [{
            "study": "std1", "studystep": "freq", "solver_sequence": "sol1",
            "tree_order": 2, "path": "freq",
        }],
        "solver_tree_features": [{"feature_type": "StudyStep", "path": "freq",
                                  "tree_order": 2}],
    }
    worker = {
        "status": "OBSERVED_TYPED_COMPLEX_PARAMETER_VALUES",
        "solution_tag": "sol1", "sequence_type": "SolverSequence",
        "solution_type": "Parametric", "study": "std1", "attached": True,
        "get_size": [100, 1], "get_size_multi": [1, 1], "pnames": ["freq"],
        "solution_info_count_scope": dict(scope),
        "solution_values": [{"solnum": 1,
            "real_getter": {"raw": [frequency]}, "imag_getter": {"raw": [0.0]}}],
        "unit_observations": [{"name": "freq", "value": "Hz"}],
        "parameters_complete": False,
    }
    return (
        {"study_tag": "std1", "study_steps_in_configured_order": [
            {"tag": "freq", "feature_type": "Frequency", "plist": "f0"}],
         "solver_sequences_for_study": [java]},
        {"solution_index_receipts": [{
            "dataset": {"tag": "dset1", "type_id": "Solution", "solution": "sol1"},
            "solution_indices": {
                "dataset_binding": {"binding_complete": True, "dataset": "dset1",
                                     "dataset_type": "Solution", "solution": "sol1"},
                "solver_sequence_parameter_observation": worker,
                "parameters_complete": False, "axis_metadata_complete": False,
            },
        }]},
    )


def test_main_frequency_singleton_is_bound_to_exact_attached_sequence_and_one_freq_level():
    study, datasets = _main_frequency_inventory()
    result = protocol.resolve_main_frequency_dataset(study, datasets)
    assert result["status"] == "PASS_EXACT_ATTACHED_MAIN_FREQUENCY_SINGLETON"
    assert result["frequency_hz"] == pytest.approx(1.9341448903225803e14)
    assert result["solution_info_axis_metadata_complete"] is False
    assert result["parameters_complete"] is False
    assert result["outer_indices_inferred"] is False


@pytest.mark.parametrize("mutate", [
    "extra_parameter", "extra_level", "outer_values", "multi_state",
    "wrong_dataset_binding", "wrong_study_binding", "frequency_imaginary",
])
def test_main_frequency_singleton_rejects_unproven_or_multistate_axes(mutate):
    study, datasets = _main_frequency_inventory()
    seq = study["solver_sequences_for_study"][0]
    receipt = datasets["solution_index_receipts"][0]["solution_indices"]
    worker = receipt["solver_sequence_parameter_observation"]
    if mutate == "extra_parameter":
        seq["pnames"] = ["freq", "modeIndex"]
    elif mutate == "extra_level":
        seq["solution_info_count_scope"]["getLevelNames"] = ["freq", "modeIndex"]
    elif mutate == "outer_values":
        worker["solution_info_count_scope"]["getOuterSolnum"] = [1]
    elif mutate == "multi_state":
        seq["get_size_multi"] = [2, 2]
    elif mutate == "wrong_dataset_binding":
        receipt["dataset_binding"]["solution"] = "sol9"
    elif mutate == "wrong_study_binding":
        seq["study_step_bindings_in_solver_tree_order"][0]["study"] = "std9"
    else:
        seq["parameter_values_by_solnum"][0]["imaginary"] = [1.0]
    with pytest.raises(protocol.ScienceProtocolError):
        protocol.resolve_main_frequency_dataset(study, datasets)


def test_bma_mode_formula_readback_requires_exact_store_binding_units_and_values():
    frequency = 1.9341448903225803e14
    k0 = 2 * math.pi * frequency / 299_792_458.0
    neff = 1.540405036791
    bindings = {"status": "PASS_BMA_STUDY_STORE_DATASET_BINDINGS", "bindings": []}
    points = {}
    for step, port, dataset, solution in (
            ("bmaInput", "1", "dset2", "sol2"),
            ("bmaOutput", "2", "dset3", "sol3")):
        lam = complex(0.0, -k0 * neff)
        bindings["bindings"].append({
            "study_step_tag": step, "port_name": port, "dataset_tag": dataset,
            "store_solution_reference": solution, "mode_frequency_expression": "f0",
            "sequence_parameter_observation": {"values": [{"parameters": [{
                "name": "lambda", "real": lam.real, "imag": lam.imag, "unit": "1/m"}]}]},
        })
        points[step] = {
            "status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "dataset": dataset,
            "solution": solution, "sample_count": 1,
            "expression_units": {"ewfd.beta_1": "1/m", "ewfd.neff_1": "1", "ewfd.k0": "1/m"},
            "expressions": [
                {"expression": "ewfd.beta_1", "real": [k0 * neff], "imag": [0.0]},
                {"expression": "ewfd.neff_1", "real": [neff], "imag": [0.0]},
                {"expression": "ewfd.k0", "real": [k0], "imag": [0.0]},
            ],
        }
    main = {"status": "PASS_EXACT_ATTACHED_MAIN_FREQUENCY_SINGLETON",
            "frequency_hz": frequency, "parameter_unit": "Hz"}
    result = protocol.reconcile_bma_mode_point_readbacks(bindings, points, main)
    assert result["status"] == "PASS_BMA_FORMULA_CROSSCHECK_ONLY"
    assert all(row["science_acceptance"] == "NOT_EVALUATED" for row in result["bindings"])

    points["bmaOutput"]["expression_units"]["ewfd.beta_1"] = "V/m"
    with pytest.raises(protocol.ScienceProtocolError, match="units"):
        protocol.reconcile_bma_mode_point_readbacks(bindings, points, main)


def test_production_complex_point_readback_preserves_exact_dataset_shape_and_coordinates():
    result = {
        "status": {"ok": True, "cleanup_failed": False},
        "cleanup": {"cleanup_failed": False},
        "dataset": "dset3", "solution": "sol3",
        "dataset_binding": {"binding_complete": True, "solution": "sol3"},
        "solution_axes": {
            "pair_mapping_complete": False,
            "numeric_count_observation": {
                "status": "VERIFIED_SINGLE_STORED_NUMERIC_COUNT",
                "numeric_solution_count": 1,
                "dataset": "dset3", "solution": "sol3",
                "source": "exact dataset binding + Stored getSize()[1]=1 + typed complex lambda row",
            },
        },
        "result_budget": {"status": "PASS", "records": [{
            "status": "PASS", "allowed": True,
            "solution_axes_verification_scope": "numeric_singleton_count_only",
            "solution_axes_pair_mapping_complete": False,
        }]},
        "field_array": None,
        "feature_readback": {"data": "dset3", "data_status": "VERIFIED"},
        "coordinate_readback": {"status": "VERIFIED",
                                 "coordinates": [[8.0, 8.0], [-1.0, 1.0]], "unit": "um"},
        "point_count": 2, "expressions": ["ewfd.Ez", "ewfd.Hy"],
        "complex_mode": "preserve", "is_complex": True,
        "expression_units": {"ewfd.Ez": "V/m", "ewfd.Hy": "A/m"},
        "values": [
            [[{"real": 1.0, "imag": 2.0}, {"real": 3.0, "imag": 4.0}]],
            [[{"real": -2.0, "imag": 0.5}, {"real": -1.0, "imag": 0.25}]],
        ],
    }
    raw = protocol.decode_production_complex_point_readback(
        result, dataset_tag="dset3", solution_tag="sol3",
        expressions=["ewfd.Ez", "ewfd.Hy"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
        expected_solution_count=1, plane_id="output_x8")
    assert raw["status"] == "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK"
    assert raw["science_acceptance"] == "NOT_EVALUATED"
    assert raw["coordinates_m"] == [[8e-6, 8e-6], [-1e-6, 1e-6]]
    assert raw["normal_status"] == "NOT_CAPTURED_BY_POINT_INTERPOLATION"
    assert raw["expressions"][0]["imag"] == [2.0, 4.0]

    malformed = copy.deepcopy(result)
    malformed["values"][0][0].pop()
    with pytest.raises(protocol.ScienceProtocolError, match="shape"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset3", solution_tag="sol3",
            expressions=["ewfd.Ez", "ewfd.Hy"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8")

    malformed_count = copy.deepcopy(result)
    malformed_count["solution_axes"]["numeric_count_observation"]["numeric_solution_count"] = 2
    with pytest.raises(protocol.ScienceProtocolError, match="numeric singleton"):
        protocol.decode_production_complex_point_readback(
            malformed_count, dataset_tag="dset3", solution_tag="sol3",
            expressions=["ewfd.Ez", "ewfd.Hy"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8")

    malformed_axes = copy.deepcopy(result)
    malformed_axes["solution_axes"]["pair_mapping_complete"] = True
    with pytest.raises(protocol.ScienceProtocolError, match="numeric singleton"):
        protocol.decode_production_complex_point_readback(
            malformed_axes, dataset_tag="dset3", solution_tag="sol3",
            expressions=["ewfd.Ez", "ewfd.Hy"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8")


def test_attached_frequency_count_witness_binds_native_point_readback_without_axis_completion():
    study, inventory = _main_frequency_inventory()
    witness = protocol.resolve_main_frequency_dataset(study, inventory)
    observation = inventory["solution_index_receipts"][0]["solution_indices"][
        "solver_sequence_parameter_observation"]
    count_observation = {
        "status": "VERIFIED_SINGLE_ATTACHED_PARAMETRIC_NUMERIC_COUNT",
        "numeric_solution_count": 1, "dataset": "dset1", "solution": "sol1",
        "source": "exact attached Parametric SolverSequence freq singleton",
        "observation": {
            "sequence_type": observation["sequence_type"],
            "solution_type": observation["solution_type"], "attached": observation["attached"],
            "study": observation["study"], "solution_identity_matches_dataset_study": True,
            "get_size": observation["get_size"], "get_size_multi": observation["get_size_multi"],
            "pnames": observation["pnames"],
            "solution_info_count_scope": observation["solution_info_count_scope"],
            "parameter_rows": [{"name": "freq", "real": witness["frequency_hz"],
                                "imag": 0.0, "unit": "Hz"}],
        },
    }
    result = {
        "status": {"ok": True, "cleanup_failed": False},
        "cleanup": {"cleanup_failed": False},
        "dataset": "dset1", "solution": "sol1",
        "dataset_binding": {"binding_complete": True, "solution": "sol1"},
        "solution_axes": {"pair_mapping_complete": False,
                          "numeric_count_observation": count_observation},
        "result_budget": {"status": "PASS", "records": [{
            "status": "PASS", "allowed": True,
            "solution_axes_verification_scope": "numeric_singleton_count_only",
            "solution_axes_pair_mapping_complete": False}]},
        "field_array": None,
        "feature_readback": {"data": "dset1", "data_status": "VERIFIED"},
        "coordinate_readback": {"status": "VERIFIED",
                                 "coordinates": [[8.0, 8.0], [-1.0, 1.0]], "unit": "um"},
        "point_count": 2, "expressions": ["nx", "ny"],
        "complex_mode": "preserve", "is_complex": True,
        "expression_units": {"nx": "1", "ny": ""},
        "values": [
            [[{"real": 1.0, "imag": 0.0}, {"real": 1.0, "imag": 0.0}]],
            [[{"real": 0.0, "imag": 0.0}, {"real": 0.0, "imag": 0.0}]],
        ],
    }
    decoded = protocol.decode_production_complex_point_readback(
        result, dataset_tag="dset1", solution_tag="sol1", expressions=["nx", "ny"],
        expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
        plane_id="output_x8", expected_count_witness=witness,
        expected_expression_units={"nx": ("1", ""), "ny": ("1", "")},
        require_x_plane_normal=True)
    assert decoded["normal_status"] == "VERIFIED_FROM_NATIVE_NX_NY"
    assert decoded["native_normal_x"] == 1
    assert decoded["science_acceptance"] == "NOT_EVALUATED"

    malformed = copy.deepcopy(result)
    malformed["solution_axes"]["numeric_count_observation"]["observation"][
        "solution_info_count_scope"]["getLevelNames"] = ["freq", "modeIndex"]
    with pytest.raises(protocol.ScienceProtocolError, match="attached result.at_points singleton"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset1", solution_tag="sol1", expressions=["nx", "ny"],
            expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
            plane_id="output_x8", expected_count_witness=witness,
            expected_expression_units={"nx": ("1", ""), "ny": ("1", "")},
            require_x_plane_normal=True)

    malformed = copy.deepcopy(result)
    malformed["expression_units"]["nx"] = "m"
    with pytest.raises(protocol.ScienceProtocolError, match="unit"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset1", solution_tag="sol1", expressions=["nx", "ny"],
            expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
            plane_id="output_x8", expected_count_witness=witness,
            expected_expression_units={"nx": ("1", ""), "ny": ("1", "")},
            require_x_plane_normal=True)


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
