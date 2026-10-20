from __future__ import annotations

import cmath
import copy
import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from tools import w23_te_science_protocol as protocol
from tools import run_native_w23_saved_mph_diagnostic as saved_mph

_W23_R13_ORIGINAL_BASE = saved_mph.science.W23_R13_BASE_ROOT


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
    from tools.run_native_w23_saved_mph_diagnostic import plane_field_expressions
    names = list(plane_field_expressions("2" if plane=="output_x8" else "1"))
    values: dict[str, list[complex]] = {name: [0j] * n for name in names}
    if plane == "output_x8":
        values["down(emw.Ez)"] = [phase * (2 + 1j)] * n
        values["down(emw.Hy)"] = [phase * (-1 + 0.5j)] * n
        values["emw.Emodez_2"] = [rotate_mode * (1 - 0.25j)] * n
        values["emw.Hmodey_2"] = [rotate_mode * (-0.5 - 0.75j)] * n
    else:
        values["emw.Emodez_1"] = [phase * (1 + 0.5j)] * n
        values["emw.Hmodey_1"] = [phase * (-0.75 + 0.25j)] * n
    values["emw.nx"] = [complex(normal_x)] * n
    values["emw.ny"] = [0j] * n
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
    assert result["status"] == "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON"
    assert result["raw_parameter"]["real"] == pytest.approx(1.9341448903225803e14)
    assert result["physical_frequency_hz"] == "NOT_CAPTURED"
    assert "frequency_hz" not in result
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


def _requested_unit_application(expressions, units):
    return {"status": "VERIFIED_REQUESTED_UNITS_BEFORE_AND_AFTER_NUMERIC_READ",
            "requested_expressions": list(expressions), "requested_units": list(units),
            "native_default_unit_expressions": [expression for expression, unit in zip(expressions, units) if unit == ""],
            "setter": "actual Interp NumericalFeature.setIndex(unit,requested,index)",
            "no_external_scaling_or_relabeling": True,
            **{key: {"stage": stage, "status": "VERIFIED_EXACT_NATIVE_ARRAYS",
                     "expressions": list(expressions), "units": list(units),
                     "source": "same actual Interp NumericalFeature.getStringArray(expr/unit)"}
               for key, stage in (("before", "before_numeric_read"), ("after", "after_numeric_read"))}}


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
            "status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "native_is_complex": True, "dataset": dataset,
            "solution": solution, "sample_count": 1,
            "requested_unit_application": _requested_unit_application(protocol.bma_point_expressions(port, field_scheme="emw_down"), ["1/m", "1", "1/m"]),
            "expression_units": {f"emw.beta_{port}": "1/m", f"emw.neff_{port}": "1", "emw.k0": "1/m"},
            "expressions": [
                {"expression": f"emw.beta_{port}", "real": [k0 * neff], "imag": [0.0]},
                {"expression": f"emw.neff_{port}", "real": [neff], "imag": [0.0]},
                {"expression": "emw.k0", "real": [k0], "imag": [0.0]},
            ],
        }
    main = {"status": "PASS_NATIVE_NUMERICAL_FREQUENCY_HZ",
            "frequency_hz": frequency, "frequency_unit": "Hz"}
    result = protocol.reconcile_bma_mode_point_readbacks(bindings, points, main, field_scheme="emw_down")
    assert result["status"] == "PASS_BMA_FORMULA_CROSSCHECK_ONLY"
    assert all(row["science_acceptance"] == "NOT_EVALUATED" for row in result["bindings"])

    points["bmaOutput"]["expression_units"]["emw.beta_2"] = "V/m"
    with pytest.raises(protocol.ScienceProtocolError, match="units"):
        protocol.reconcile_bma_mode_point_readbacks(bindings, points, main, field_scheme="emw_down")


def test_bma_neff_native_default_is_exact_and_legacy_dimensionless_request_stays_valid():
    frequency = 1.9341448903225803e14
    k0 = 2 * math.pi * frequency / 299_792_458.0
    neff = 1.540405036791
    bindings = {"status": "PASS_BMA_STUDY_STORE_DATASET_BINDINGS", "bindings": []}
    points = {}
    for step, port, dataset, solution in (
            ("bmaInput", "1", "dset2", "sol2"),
            ("bmaOutput", "2", "dset3", "sol3")):
        lam = complex(0.0, -k0 * neff)
        expressions = protocol.bma_point_expressions(port, field_scheme="emw_down")
        bindings["bindings"].append({
            "study_step_tag": step, "port_name": port, "dataset_tag": dataset,
            "store_solution_reference": solution, "mode_frequency_expression": "f0",
            "sequence_parameter_observation": {"values": [{"parameters": [{
                "name": "lambda", "real": lam.real, "imag": lam.imag, "unit": "1/m"}]}]},
        })
        # Keep one existing consumer on the legacy explicit unit array; the fresh runner
        # requests COMSOL's native default only for the exact built-in neff expression.
        units = ["1/m", "1", "1/m"] if port == "1" else ["1/m", "", "1/m"]
        points[step] = {
            "status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "native_is_complex": True,
            "dataset": dataset, "solution": solution, "sample_count": 1,
            "requested_unit_application": _requested_unit_application(expressions, units),
            "expression_units": dict(zip(expressions, units)),
            "expressions": [
                {"expression": expressions[0], "real": [k0 * neff], "imag": [0.0]},
                {"expression": expressions[1], "real": [neff], "imag": [0.0]},
                {"expression": expressions[2], "real": [k0], "imag": [0.0]},
            ],
        }
    main = {"status": "PASS_NATIVE_NUMERICAL_FREQUENCY_HZ", "frequency_hz": frequency, "frequency_unit": "Hz"}
    result = protocol.reconcile_bma_mode_point_readbacks(bindings, points, main, field_scheme="emw_down")
    assert result["status"] == "PASS_BMA_FORMULA_CROSSCHECK_ONLY"
    assert points["bmaInput"]["requested_unit_application"]["requested_units"] == ["1/m", "1", "1/m"]
    assert points["bmaOutput"]["requested_unit_application"]["requested_units"] == ["1/m", "", "1/m"]

    malformed = copy.deepcopy(points)
    malformed["bmaOutput"]["requested_unit_application"] = _requested_unit_application(
        protocol.bma_point_expressions("2", field_scheme="emw_down"), ["", "", "1/m"])
    with pytest.raises(protocol.ScienceProtocolError, match="requested-unit"):
        protocol.reconcile_bma_mode_point_readbacks(bindings, malformed, main, field_scheme="emw_down")
    malformed = copy.deepcopy(points)
    malformed["bmaOutput"]["expression_units"]["emw.neff_2"] = "1"
    with pytest.raises(protocol.ScienceProtocolError, match="units"):
        protocol.reconcile_bma_mode_point_readbacks(bindings, malformed, main, field_scheme="emw_down")


def test_requested_native_default_allowlist_includes_only_exact_neff_and_xy_normals():
    allowed = ["emw.neff_1", "emw.neff_2", "emw.nx", "emw.ny"]
    for expression in allowed:
        application = _requested_unit_application([expression], [""])
        protocol._require_requested_unit_application(application, [expression], [""])
        legacy_or_incomplete = copy.deepcopy(application)
        legacy_or_incomplete.pop("native_default_unit_expressions")
        with pytest.raises(protocol.ScienceProtocolError, match="not bound to its exact expressions"):
            protocol._require_requested_unit_application(legacy_or_incomplete, [expression], [""])
    for expression in ["nx", "emw.Nx", "emw.nx ", "emw.beta_1", "freq"]:
        application = _requested_unit_application([expression], [""])
        with pytest.raises(protocol.ScienceProtocolError, match="allowlist"):
            protocol._require_requested_unit_application(application, [expression], [""])
    inconsistent_nonblank = _requested_unit_application(["emw.beta_1"], ["1/m"])
    inconsistent_nonblank["native_default_unit_expressions"] = ["emw.neff_1"]
    with pytest.raises(protocol.ScienceProtocolError, match="for a nonblank unit request"):
        protocol._require_requested_unit_application(inconsistent_nonblank, ["emw.beta_1"], ["1/m"])


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
        "point_count": 2, "expressions": ["down(emw.Ez)", "down(emw.Hy)"],
        "complex_mode": "preserve", "is_complex": True,
        "expression_units": {"down(emw.Ez)": "V/m", "down(emw.Hy)": "A/m"},
        "values": [
            [[{"real": 1.0, "imag": 2.0}, {"real": 3.0, "imag": 4.0}]],
            [[{"real": -2.0, "imag": 0.5}, {"real": -1.0, "imag": 0.25}]],
        ],
    }
    raw = protocol.decode_production_complex_point_readback(
        result, dataset_tag="dset3", solution_tag="sol3",
        expressions=["down(emw.Ez)", "down(emw.Hy)"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
        expected_solution_count=1, plane_id="output_x8", field_scheme="emw_down")
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
            expressions=["down(emw.Ez)", "down(emw.Hy)"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8", field_scheme="emw_down")

    malformed_count = copy.deepcopy(result)
    malformed_count["solution_axes"]["numeric_count_observation"]["numeric_solution_count"] = 2
    with pytest.raises(protocol.ScienceProtocolError, match="numeric singleton"):
        protocol.decode_production_complex_point_readback(
            malformed_count, dataset_tag="dset3", solution_tag="sol3",
            expressions=["down(emw.Ez)", "down(emw.Hy)"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8", field_scheme="emw_down")

    malformed_axes = copy.deepcopy(result)
    malformed_axes["solution_axes"]["pair_mapping_complete"] = True
    with pytest.raises(protocol.ScienceProtocolError, match="numeric singleton"):
        protocol.decode_production_complex_point_readback(
            malformed_axes, dataset_tag="dset3", solution_tag="sol3",
            expressions=["down(emw.Ez)", "down(emw.Hy)"], expected_points=[[8.0, -1.0], [8.0, 1.0]],
            expected_solution_count=1, plane_id="output_x8", field_scheme="emw_down")


def _offline_delta_validator_fixture(root: Path, monkeypatch, *, add_unallowlisted: bool = False):
    science = saved_mph.science
    original = _W23_R13_ORIGINAL_BASE
    base_root = root / "pinned-r13"
    (base_root / "prepare/offline_gates").mkdir(parents=True)
    for relative in (
            "prepare/source_manifest.json",
            "prepare/offline_gates/activity024_focused_junit.xml",
            "prepare/offline_gates/compile.json"):
        shutil.copyfile(original / relative, base_root / relative)
    shutil.copytree(original / "prepare/offline_gates/compile",
                    base_root / "prepare/offline_gates/compile")
    monkeypatch.setattr(science, "W23_R13_BASE_ROOT", base_root)
    base_manifest = json.loads((base_root / "prepare/source_manifest.json").read_text())
    current_files = copy.deepcopy(base_manifest["source_files"])
    for relative in science.W23_R13_ALLOWED_COMMON_SOURCE_DELTA:
        row = current_files[relative]
        row["bytes"] += 1
        row["sha256"] = hashlib.sha256(("current:" + relative).encode()).hexdigest()
        row["path"] = str(root / "source" / relative)
    added_path = "tests/test_interp_native_default_units.py"
    current_files[added_path] = {
        "path": str(root / "source" / added_path), "bytes": 17,
        "sha256": hashlib.sha256(b"supplemental-test").hexdigest(),
    }
    if add_unallowlisted:
        extra = "tests/test_unreviewed_delta.py"
        current_files[extra] = {
            "path": str(root / "source" / extra), "bytes": 8,
            "sha256": hashlib.sha256(b"unreviewed").hexdigest(),
        }

    candidate = root / "candidate/prepare"
    offline = candidate / "offline_gates"
    offline.mkdir(parents=True)
    junit = offline / "focused_delta_junit.xml"
    cases = "".join(f'<testcase classname="focused" name="case{i}" />' for i in range(30))
    junit.write_text(f'<testsuites><testsuite tests="30" failures="0" errors="0" skipped="0">{cases}</testsuite></testsuites>')
    guard = offline / "diagnostic-network-guard.jsonl"
    guard.write_text(json.dumps({"event": "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"}) + "\n")
    manifest_sha = science._source_manifest_sha256(current_files)
    test_receipt_path = offline / "diagnostic_junit_receipt.json"
    required_tests = [
        "tests/test_interp_native_default_units.py",
        "tests/test_native_w23_te_science_protocol.py",
        "tests/test_native_w23_saved_mph_diagnostic.py",
        "tests/test_native_w23_te_science_runner.py",
        "tests/test_g3_results.py",
    ]
    test_receipt = {
        "status": "PASS_FOCUSED_STRICT_GUARDED_JUNIT", "returncode": 0,
        "candidate_source_manifest_sha256": manifest_sha,
        "junit_path": str(junit), "junit_sha256": science.sha256_file(junit),
        "test_paths": [current_files[name]["path"] for name in required_tests],
        "network_guard": {
            "status": "ACTIVE_NO_DENY_OR_SETUP_ERROR",
            "path": str(guard), "sha256": science.sha256_file(guard), "event_count": 1,
        },
    }
    test_receipt_path.write_text(json.dumps(test_receipt))
    focused = {
        "receipt_path": "offline_gates/diagnostic_junit_receipt.json",
        "receipt_sha256": science.sha256_file(test_receipt_path),
        "junit_path": "offline_gates/focused_delta_junit.xml",
        "junit_sha256": science.sha256_file(junit),
        "network_guard_path": "offline_gates/diagnostic-network-guard.jsonl",
        "network_guard_sha256": science.sha256_file(guard),
    }
    delta_receipt = {
        "schema_version": 1,
        "status": "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA",
        "candidate_id": saved_mph.CANDIDATE_ID,
        "candidate_revision": saved_mph.CANDIDATE_REVISION,
        "activity_id": saved_mph.ACTIVITY_ID,
        "candidate_source_manifest_sha256": manifest_sha,
        "inherited_base": saved_mph._r13_inherited_base_record(),
        "source_delta": saved_mph._source_delta_from_r13(current_files),
        "focused_tests": focused,
    }
    delta_path = offline / "focused_delta_receipt.json"
    delta_path.write_text(json.dumps(delta_receipt))
    freeze = {
        "activity_id": saved_mph.ACTIVITY_ID,
        "candidate_id": saved_mph.CANDIDATE_ID,
        "candidate_revision": saved_mph.CANDIDATE_REVISION,
        "source_files": current_files,
        "source_manifest_sha256": manifest_sha,
        "offline_preflight": {
            "status": "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA",
            "focused_delta_receipt": {
                "path": "offline_gates/focused_delta_receipt.json",
                "sha256": science.sha256_file(delta_path),
            },
        },
    }
    return candidate / "freeze.json", freeze, base_root


def test_inherited_offline_delta_validator_positive_and_negative_controls(tmp_path, monkeypatch):
    science = saved_mph.science
    valid_path, valid_freeze, _valid_base = _offline_delta_validator_fixture(tmp_path / "valid", monkeypatch)
    result = science._validate_inherited_offline_delta(valid_path, valid_freeze)
    assert result["status"] == "PASS_OFFLINE_BASE_INHERITED_PLUS_FOCUSED_DELTA"
    assert result["focused_test_counts"]["tests"] == 30

    inherited_compile = science.W23_R13_BASE_ROOT / "prepare/offline_gates/compile.json"
    compile_receipt = json.loads(inherited_compile.read_text())
    legacy_candidate = tmp_path / "legacy-pass-all"
    legacy_offline = legacy_candidate / "offline_gates"
    legacy_offline.mkdir(parents=True)
    shutil.copytree(science.W23_R13_BASE_ROOT / "prepare/offline_gates/compile",
                    legacy_offline / "compile")
    legacy_freeze = {
        "offline_preflight": {"status": "PASS_ALL_OFFLINE_GATES",
                              "compile": compile_receipt},
    }
    legacy_result = science._check_offline_receipts(legacy_candidate / "freeze.json", legacy_freeze)
    assert legacy_result["status"] == "PASS_FROZEN_CLASSPATH_AND_CLASS_OUTPUT_REVALIDATED"
    assert "inherited_offline_delta" not in legacy_result

    receipt_path = valid_path.parent / "offline_gates/diagnostic_junit_receipt.json"
    receipt_path.write_text(receipt_path.read_text() + " ")
    with pytest.raises(RuntimeError, match="changed after freezing"):
        science._validate_inherited_offline_delta(valid_path, valid_freeze)

    tampered_path, tampered_freeze, tampered_base = _offline_delta_validator_fixture(tmp_path / "tampered-base", monkeypatch)
    manifest_path = tampered_base / "prepare/source_manifest.json"
    manifest_path.write_text(manifest_path.read_text() + " ")
    with pytest.raises(RuntimeError, match="tampered"):
        science._validate_inherited_offline_delta(tampered_path, tampered_freeze)

    delta_path, delta_freeze, _delta_base = _offline_delta_validator_fixture(
        tmp_path / "unallowlisted-delta", monkeypatch, add_unallowlisted=True)
    with pytest.raises(RuntimeError, match="outside the exact pre-frozen allowlist"):
        science._validate_inherited_offline_delta(delta_path, delta_freeze)


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
            "parameter_rows": [{"name": "freq", "real": witness["raw_parameter"]["real"],
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
        "point_count": 2, "expressions": ["emw.nx", "emw.ny"],
        "complex_mode": "preserve", "is_complex": True,
        "expression_units": {"emw.nx": "1", "emw.ny": "1"},
        "values": [
            [[{"real": 1.0, "imag": 0.0}, {"real": 1.0, "imag": 0.0}]],
            [[{"real": 0.0, "imag": 0.0}, {"real": 0.0, "imag": 0.0}]],
        ],
    }
    decoded = protocol.decode_production_complex_point_readback(
        result, dataset_tag="dset1", solution_tag="sol1", expressions=["emw.nx", "emw.ny"],
        expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
        plane_id="output_x8", expected_count_witness=witness,
        expected_expression_units={"emw.nx": "1", "emw.ny": "1"},
        require_x_plane_normal=True, field_scheme="emw_down")
    assert decoded["normal_status"] == "VERIFIED_FROM_NATIVE_NX_NY"
    assert decoded["native_normal_x"] == 1
    assert decoded["science_acceptance"] == "NOT_EVALUATED"

    malformed = copy.deepcopy(result)
    malformed["solution_axes"]["numeric_count_observation"]["observation"][
        "solution_info_count_scope"]["getLevelNames"] = ["freq", "modeIndex"]
    with pytest.raises(protocol.ScienceProtocolError, match="attached result.at_points singleton"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset1", solution_tag="sol1", expressions=["emw.nx", "emw.ny"],
            expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
            plane_id="output_x8", expected_count_witness=witness,
            expected_expression_units={"emw.nx": "1", "emw.ny": "1"},
            require_x_plane_normal=True, field_scheme="emw_down")

    malformed = copy.deepcopy(result)
    malformed["expression_units"]["emw.nx"] = "m"
    with pytest.raises(protocol.ScienceProtocolError, match="unit"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset1", solution_tag="sol1", expressions=["emw.nx", "emw.ny"],
            expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
            plane_id="output_x8", expected_count_witness=witness,
            expected_expression_units={"emw.nx": "1", "emw.ny": "1"},
            require_x_plane_normal=True, field_scheme="emw_down")

    malformed = copy.deepcopy(result)
    malformed["expression_units"]["emw.ny"] = ""
    with pytest.raises(protocol.ScienceProtocolError, match="unit"):
        protocol.decode_production_complex_point_readback(
            malformed, dataset_tag="dset1", solution_tag="sol1", expressions=["emw.nx", "emw.ny"],
            expected_points=[[8.0, -1.0], [8.0, 1.0]], expected_solution_count=1,
            plane_id="output_x8", expected_count_witness=witness,
            expected_expression_units={"emw.nx": "1", "emw.ny": "1"},
            require_x_plane_normal=True, field_scheme="emw_down")


def test_simpson_and_native_integral_reconciliation_use_registered_metre_grid():
    coordinates = [-8e-6 + 16e-6 * index / (protocol.SAMPLE_COUNT - 1)
                   for index in range(protocol.SAMPLE_COUNT)]
    integral = protocol.simpson_integral([1.0] * protocol.SAMPLE_COUNT, coordinates)
    assert integral.real == pytest.approx(16e-6, rel=1e-12)
    assert integral.imag == 0

    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    independent = protocol.independent_integrals(output, incident, field_scheme="emw_down")
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
    result = protocol.independent_capture_aperture_flux(output, field_scheme="emw_down")
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
    independent = protocol.independent_capture_aperture_flux(output, field_scheme="emw_down")
    independent_integrals = protocol.independent_integrals(output, incident, field_scheme="emw_down")
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
        if row["expression"] == "down(emw.Hy)":
            for index, coordinate in enumerate(y):
                if (protocol.CAPTURE_Y_MIN_M - 1e-12 <= coordinate
                        <= protocol.CAPTURE_Y_MAX_M + 1e-12):
                    row["real"][index], row["imag"][index] = 1.0, -0.5
    reverse_aperture = {**output, "expressions": rows}
    incident = _raw("input_xminus10", -10e-6, -1, sample_count=protocol.REFINED_SAMPLE_COUNT)
    independent = protocol.independent_capture_aperture_flux(reverse_aperture, field_scheme="emw_down")
    independent_integrals = protocol.independent_integrals(reverse_aperture, incident, field_scheme="emw_down")
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
        protocol.independent_capture_aperture_flux(missing_bound, field_scheme="emw_down")
    reversed_normal = _raw("output_x8", 8e-6, -1, normal_sign=1,
                           sample_count=protocol.REFINED_SAMPLE_COUNT)
    with pytest.raises(protocol.ScienceProtocolError, match="physical \\+x"):
        protocol.independent_capture_aperture_flux(reversed_normal, field_scheme="emw_down")


def test_normal_sign_and_native_normal_flip_preserve_physical_forward_power():
    output_a = _raw("output_x8", 8e-6, 1, normal_sign=1)
    input_a = _raw("input_xminus10", -10e-6, -1, normal_sign=-1)
    output_b = _raw("output_x8", 8e-6, -1, normal_sign=-1)
    input_b = _raw("input_xminus10", -10e-6, 1, normal_sign=1)
    positive_x = protocol.independent_integrals(output_a, input_a, field_scheme="emw_down")
    flipped_geometry_normal = protocol.independent_integrals(output_b, input_b, field_scheme="emw_down")
    for key in ("signal_power", "reference_mode_power", "incident_reference_power"):
        assert flipped_geometry_normal[key] == pytest.approx(positive_x[key], rel=1e-12)
    assert flipped_geometry_normal["reciprocal_overlap_numerator"] == pytest.approx(
        positive_x["reciprocal_overlap_numerator"], rel=1e-12)


def test_reference_mode_complex_phase_rotates_cross_term_conjugately():
    baseline = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1), _raw("input_xminus10", -10e-6, -1), field_scheme="emw_down")
    reference_rotated = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, rotate_mode=1j), _raw("input_xminus10", -10e-6, -1), field_scheme="emw_down")
    assert reference_rotated["reference_mode_power"] == pytest.approx(baseline["reference_mode_power"], rel=1e-12)
    z0 = complex(**baseline["reciprocal_overlap_numerator"])
    z1 = complex(**reference_rotated["reciprocal_overlap_numerator"])
    assert z1 / z0 == pytest.approx(-1j, rel=1e-12)


def test_software_signal_global_phase_control_rotates_cross_integral_only():
    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    receipt = protocol.software_global_phase_control(output, incident, field_scheme="emw_down")
    assert receipt["status"] == "PASS_SOFTWARE_SIGNAL_GLOBAL_PHASE_CONTROL"
    assert receipt["scope"].startswith("software-only")
    assert receipt["native_input_phase_solve_still_required"] is True
    assert complex(**receipt["observed_cross_integral_ratio"]) == pytest.approx(1j, rel=1e-12)
    assert max(receipt["invariant_relative_errors"].values()) < 1e-12


def test_321_to_641_quadrature_refinement_remains_an_explicit_full_science_gate():
    coarse = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=protocol.SAMPLE_COUNT),
        _raw("input_xminus10", -10e-6, -1, sample_count=protocol.SAMPLE_COUNT), field_scheme="emw_down")
    fine = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=protocol.REFINED_SAMPLE_COUNT),
        _raw("input_xminus10", -10e-6, -1, sample_count=protocol.REFINED_SAMPLE_COUNT), field_scheme="emw_down")
    receipt = protocol.compare_quadrature_refinement(coarse, fine)
    assert receipt["status"] == "PASS_321_TO_641_QUADRATURE_REFINEMENT"
    assert receipt["coarse_points"] == 321 and receipt["fine_points"] == 641


def test_saved_mph_641_to_1281_quadrature_refinement_is_a_separate_explicit_gate():
    coarse = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=protocol.SAVED_MPH_SAMPLE_COUNT),
        _raw("input_xminus10", -10e-6, -1, sample_count=protocol.SAVED_MPH_SAMPLE_COUNT), field_scheme="emw_down")
    fine = protocol.independent_integrals(
        _raw("output_x8", 8e-6, 1, sample_count=protocol.SAVED_MPH_REFINED_SAMPLE_COUNT),
        _raw("input_xminus10", -10e-6, -1, sample_count=protocol.SAVED_MPH_REFINED_SAMPLE_COUNT), field_scheme="emw_down")
    receipt = protocol.compare_quadrature_refinement(
        coarse, fine, sample_counts=protocol.SAVED_MPH_QUADRATURE_SAMPLE_COUNTS)
    assert receipt["status"] == "PASS_641_TO_1281_QUADRATURE_REFINEMENT"
    assert receipt["coarse_points"] == 641 and receipt["fine_points"] == 1281


def test_coordinate_and_boundary_normal_readback_fail_closed():
    output = _raw("output_x8", 8e-6, 1)
    incident = _raw("input_xminus10", -10e-6, -1)
    output["coordinates_m"][0][0] = 8.0
    with pytest.raises(protocol.ScienceProtocolError, match="x plane"):
        protocol.independent_integrals(output, incident, field_scheme="emw_down")

    output = _raw("output_x8", 8e-6, 1)
    expression = next(row for row in output["expressions"] if row["expression"] == "emw.ny")
    expression["real"][12] = 1.0
    with pytest.raises(protocol.ScienceProtocolError, match="nonzero y component"):
        protocol.independent_integrals(output, incident, field_scheme="emw_down")

    output = _raw("output_x8", 8e-6, 1.2)
    with pytest.raises(protocol.ScienceProtocolError, match="unit x normal"):
        protocol.independent_integrals(output, incident, field_scheme="emw_down")

    output = _raw("output_x8", 8e-6, 1)
    output["expressions"].append(dict(output["expressions"][0]))
    with pytest.raises(protocol.ScienceProtocolError, match="duplicate expression"):
        protocol.independent_integrals(output, incident, field_scheme="emw_down")


def test_native_phase_control_requires_input_only_rotation_and_fixed_output_mode():
    output0 = _raw("output_x8", 8e-6, 1, phase=1)
    output90 = _raw("output_x8", 8e-6, 1, phase=1j)
    incident0 = _raw("input_xminus10", -10e-6, -1, phase=1)
    incident90 = _raw("input_xminus10", -10e-6, -1, phase=1j)
    q0 = protocol.independent_integrals(output0, incident0, field_scheme="emw_down")
    q90 = protocol.independent_integrals(output90, incident90, field_scheme="emw_down")
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
    assert protocol.verify_native_phase_pair(case0, case90, field_scheme="emw_down")["status"] == "PASS_NATIVE_INPUT_PHASE_CONTROL"

    # If the output reference rotates with the signal, the phase comparison
    # is unidentifiable and must not be counted as a native phase pass.
    case90_both_rotated = {**case90, "output_fields": _raw("output_x8", 8e-6, 1,
                                                            phase=1j, rotate_mode=1j)}
    with pytest.raises(protocol.ScienceProtocolError, match="output reference mode"):
        protocol.verify_native_phase_pair(case0, case90_both_rotated, field_scheme="emw_down")

    mismatched_grid = {**case90, "output_fields": _raw("output_x8", 8e-6, 1,
                                                        phase=1j, sample_count=641)}
    with pytest.raises(protocol.ScienceProtocolError, match="different quadrature sample counts"):
        protocol.verify_native_phase_pair(case0, mismatched_grid, field_scheme="emw_down")

    shifted_grid_raw = _raw("output_x8", 8e-6, 1, phase=1j)
    shifted_grid_raw["coordinates_m"][1][80] += 1e-9
    shifted_grid = {**case90, "output_fields": shifted_grid_raw}
    with pytest.raises(protocol.ScienceProtocolError, match="same coordinates"):
        protocol.verify_native_phase_pair(case0, shifted_grid, field_scheme="emw_down")

    changed_power = {**case90, "native_overlap": {**native90, "integrals": {
        **native90["integrals"], "signal_power": {"real": q90["signal_power"] * 1.1,
                                                  "imag": 0, "unit": "W/m"}}}}
    with pytest.raises(protocol.ScienceProtocolError, match="altered signal_power"):
        protocol.verify_native_phase_pair(case0, changed_power, field_scheme="emw_down")


def test_software_negative_controls_run_and_are_never_native_evidence():
    receipt = protocol.run_negative_controls()
    assert receipt["status"] == "PASS_SOFTWARE_NEGATIVE_CONTROLS"
    assert receipt["scope"] == "schema-only; no native dispatch"
    assert all(row["passed"] for row in receipt["controls"])


def test_real_activity011_raw_getters_are_only_count_proof():
    import hashlib
    import json
    from pathlib import Path
    path = Path(__file__).parent / "fixtures/w23_activity011_normalized_saved_mph_inventory.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "ead5c898adf8106faacc81ee5aa8b645ff4adbb459dda9ccfdffc4930888a03a"
    inventory = json.loads(path.read_text())
    count = protocol.resolve_main_frequency_dataset(inventory["study_inventory"], inventory["dataset_inventory"])
    assert count["status"] == "PASS_EXACT_ATTACHED_MAIN_COUNT_SINGLETON"
    assert count["raw_parameter"]["real"] == 193414489032258.03
    assert count["raw_parameter"]["imag"] == 0.0
    assert count["raw_parameter"]["unit"] == "GHz"
    assert count["physical_frequency_hz"] == "NOT_CAPTURED"
    assert "frequency_hz" not in count
    assert count["parameters_complete"] is False
    assert count["outer_indices_inferred"] is False
    bindings = protocol.resolve_bma_stored_dataset_bindings(inventory["study_inventory"], inventory["dataset_inventory"])
    assert {row["port_name"] for row in bindings["bindings"]} == {"1", "2"}
    assert all(row["sequence_parameter_observation"]["values"][0]["parameters"][0]["unit"] == "1" for row in bindings["bindings"])
    with pytest.raises(protocol.ScienceProtocolError):
        protocol.reconcile_bma_mode_point_readbacks(bindings, {}, count, field_scheme="emw_down")


@pytest.mark.parametrize("mutation", [None, "unit", "dataset", "solution", "expression", "shape", "imag", "negative"])
def test_physical_frequency_is_independent_native_hz_scalar(mutation):
    study, inventory = _main_frequency_inventory()
    count = protocol.resolve_main_frequency_dataset(study, inventory)
    point = {"status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "native_is_complex": True, "dataset": "dset1",
             "solution": "sol1", "sample_count": 1,
             "requested_unit_application": _requested_unit_application(["freq"], ["Hz"]),
             "expression_units": {"freq": "Hz"},
             "expressions": [{"expression": "freq", "real": [193414489032258.03], "imag": [0.0]}]}
    if mutation == "unit": point["expression_units"]["freq"] = "GHz"
    elif mutation == "dataset": point["dataset"] = "dset2"
    elif mutation == "solution": point["solution"] = "sol2"
    elif mutation == "expression": point["expressions"][0]["expression"] = "f0"
    elif mutation == "shape": point["expressions"][0]["real"].append(1.0)
    elif mutation == "imag": point["expressions"][0]["imag"][0] = 1.0
    elif mutation == "negative": point["expressions"][0]["real"][0] = -1.0
    if mutation:
        with pytest.raises(protocol.ScienceProtocolError):
            protocol.resolve_physical_frequency_point_readback(point, count)
    else:
        physical = protocol.resolve_physical_frequency_point_readback(point, count)
        assert physical["frequency_hz"] == 193414489032258.03
        assert physical["frequency_unit"] == "Hz"
        assert physical["getter_unit_conversion_applied"] is False


def test_bma_formula_rejects_port1_expressions_for_port2_even_identical_values():
    frequency = 193414489032258.03
    k0 = 2 * math.pi * frequency / 299792458.0
    neff = 1.54
    bindings = {"status": "PASS_BMA_STUDY_STORE_DATASET_BINDINGS", "bindings": []}
    points = {}
    for step, port, dataset, solution in (("bmaInput", "1", "dset2", "sol2"), ("bmaOutput", "2", "dset3", "sol3")):
        bindings["bindings"].append({"study_step_tag": step, "port_name": port, "dataset_tag": dataset,
            "store_solution_reference": solution, "mode_frequency_expression": "f0",
            "sequence_parameter_observation": {"values": [{"parameters": [{"name": "lambda", "real": 0.0, "imag": -k0*neff, "unit": "1"}]}]}})
        beta, index, vacuum = protocol.bma_point_expressions(port, field_scheme="emw_down")
        points[step] = {"status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "native_is_complex": True, "dataset": dataset,
            "solution": solution, "sample_count": 1,
            "requested_unit_application": _requested_unit_application([beta, index, vacuum], ["1/m", "1", "1/m"]),
            "expression_units": {beta: "1/m", index: "1", vacuum: "1/m"},
            "expressions": [{"expression": beta, "real": [k0*neff], "imag": [0.0]},
                            {"expression": index, "real": [neff], "imag": [0.0]},
                            {"expression": vacuum, "real": [k0], "imag": [0.0]}]}
    physical = {"status": "PASS_NATIVE_NUMERICAL_FREQUENCY_HZ", "frequency_hz": frequency, "frequency_unit": "Hz"}
    result = protocol.reconcile_bma_mode_point_readbacks(bindings, points, physical, field_scheme="emw_down")
    assert result["bindings"][1]["expressions"] == ["emw.beta_2", "emw.neff_2", "emw.k0"]
    assert result["bindings"][1]["lambda_raw_getter"]["unit_label"] == "1"
    points["bmaOutput"]["expressions"][0]["expression"] = "emw.beta_1"
    points["bmaOutput"]["expressions"][1]["expression"] = "emw.neff_1"
    with pytest.raises(protocol.ScienceProtocolError, match="wrong-port"):
        protocol.reconcile_bma_mode_point_readbacks(bindings, points, physical, field_scheme="emw_down")



def _frequency_scaling_pair():
    hz, ghz = [], []
    points = []
    for unit, real, imag in (("Hz", 193414489032258.03, 1.0), ("GHz", 193414.48903225803, 1e-9)):
        points.append({"status": "PRODUCTION_NATIVE_COMPLEX_POINT_READBACK", "native_is_complex": True, "dataset": "dset1", "solution": "sol1",
            "sample_count": 1, "coordinates_m": [[-1e-5], [0.0]], "expression_units": {"freq": unit},
            "requested_unit_application": _requested_unit_application(["freq"], [unit]),
            "expressions": [{"expression": "freq", "real": [real], "imag": [imag]}]})
    ref = {"model_tag": "mcp_test", "session_id": "session_test", "server_instance_id": "server_test", "generation": 1}
    epoch = {"instance_id": "worker_test", "generation": 2, "server": "127.0.0.1:1234"}
    context = {"model_ref_before": ref, "model_ref_after": copy.deepcopy(ref), "revision_before": 0, "revision_after": 0,
               "worker_epoch_before": epoch, "worker_epoch_after": copy.deepcopy(epoch)}
    return points[0], points[1], [copy.deepcopy(context), copy.deepcopy(context)]


def test_frequency_dual_units_requires_actual_numeric_scaling_and_preserves_raw_returns():
    hz, ghz, contexts = _frequency_scaling_pair()
    before_hz, before_ghz = copy.deepcopy(hz), copy.deepcopy(ghz)
    receipt = protocol.verify_frequency_unit_scaling(hz, ghz, contexts)
    assert receipt["status"] == "PASS_ACTUAL_NATIVE_FREQ_HZ_GHZ_SCALING"
    assert receipt["native_raw_values"] == [
        {"unit": "Hz", "real": 193414489032258.03, "imag": 1.0},
        {"unit": "GHz", "real": 193414.48903225803, "imag": 1e-9}]
    assert receipt["converted_or_relabeled_data_created"] is False
    assert hz == before_hz and ghz == before_ghz


@pytest.mark.parametrize("mutation", ["same_raw", "real_ratio", "imag_ratio", "model_between", "model_during", "epoch_between",
                                     "epoch_during", "revision", "coordinate", "getter_label", "missing_proof", "getter_drift",
                                     "shape", "nonfinite", "negative"])
def test_frequency_dual_units_refuses_label_only_results_or_changed_native_identity(mutation):
    hz, ghz, contexts = _frequency_scaling_pair()
    if mutation == "same_raw": ghz["expressions"][0]["real"] = hz["expressions"][0]["real"][:]
    elif mutation == "real_ratio": ghz["expressions"][0]["real"][0] *= 1.01
    elif mutation == "imag_ratio": ghz["expressions"][0]["imag"][0] *= 2
    elif mutation == "model_between":
        contexts[1]["model_ref_before"]["model_tag"] = "other"
        contexts[1]["model_ref_after"]["model_tag"] = "other"
    elif mutation == "model_during": contexts[1]["model_ref_after"]["model_tag"] = "other"
    elif mutation == "epoch_between":
        contexts[1]["worker_epoch_before"]["generation"] = 3
        contexts[1]["worker_epoch_after"]["generation"] = 3
    elif mutation == "epoch_during": contexts[1]["worker_epoch_after"]["generation"] = 3
    elif mutation == "revision": contexts[1]["revision_after"] = 1
    elif mutation == "coordinate": ghz["coordinates_m"][0][0] += 1e-6
    elif mutation == "getter_label": ghz["expression_units"]["freq"] = "Hz"
    elif mutation == "missing_proof": ghz.pop("requested_unit_application")
    elif mutation == "getter_drift": ghz["requested_unit_application"]["after"]["units"] = ["Hz"]
    elif mutation == "shape": ghz["expressions"][0]["real"].append(1)
    elif mutation == "nonfinite": ghz["expressions"][0]["real"][0] = float("nan")
    else: ghz["expressions"][0]["real"][0] = -1
    with pytest.raises(protocol.ScienceProtocolError):
        protocol.verify_frequency_unit_scaling(hz, ghz, contexts)



def _current_full_offline_receipt_fixture(root, monkeypatch):
    import hashlib
    from pathlib import Path
    from comsol_mcp import _java_worker
    from tools import run_native_w23_saved_mph_diagnostic as saved

    science = saved.science
    candidate = root / "candidate"
    offline = candidate / "offline_gates"
    full_root = offline / "current_full"
    compile_root = full_root / "compile"
    test_root = root / "source_files"
    source_files = {}
    required = (
        "comsol_mcp/worker_java/PersistentComsolWorker.java",
        "tools/java/NativeW23TEScienceFixture.java",
        "tests/test_w23_r21_direction_discovery.py",
        "tests/test_unit_a_node_public_api.py",
        "tests/test_native_w23_te_science_protocol.py",
        "tests/test_native_w23_saved_mph_diagnostic.py",
        "tests/test_native_w23_te_science_runner.py",
        "tests/test_g3_results.py",
        "tests/test_interp_native_default_units.py",
        "tests/test_g2_registry_packaging.py",
        "tests/test_g2_security_guards.py",
    )
    for relative in required:
        path = test_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = ("candidate source " + relative + "\n").encode()
        path.write_bytes(payload)
        source_files[relative] = {
            "path": str(path.resolve()), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    source_map_sha = science._source_manifest_sha256(source_files)

    compiled_sources = {}
    for label, relative in (
            ("persistent_worker", "comsol_mcp/worker_java/PersistentComsolWorker.java"),
            ("science_fixture", "tools/java/NativeW23TEScienceFixture.java")):
        class_path = compile_root / label / "classes" / (label + ".class")
        class_path.parent.mkdir(parents=True, exist_ok=True)
        class_payload = ("compiled " + label).encode()
        class_path.write_bytes(class_payload)
        source_row = source_files[relative]
        compiled_sources[label] = {
            "source": source_row["path"], "source_sha256": source_row["sha256"],
            "classes": {class_path.name: {
                "bytes": len(class_payload),
                "sha256": hashlib.sha256(class_payload).hexdigest(),
            }},
        }
    compile_receipt = {
        "status": "PASS_COMSOL_64_JDK11_COMPILE_AND_JAVAP",
        "classpath_manifest_sha256": "a" * 64,
        "classpath_jar_count": 2,
        "classpath_jar_content_fingerprint_sha256": "b" * 64,
        "compiled_sources": compiled_sources,
    }
    gate_rows = {
        "runtime_imports": {"status": "PASS_EXECUTE_HELPER_IMPORTS"},
        "software_negative_controls": {"status": "PASS_SOFTWARE_NEGATIVE_CONTROLS"},
        "pip_check": {"status": "PASS_FRESH_RUNTIME_BOUND_PIP_CHECK", "returncode": 0},
        "stdio_initialize_tools_list": {
            "status": "PASS_REAL_PRODUCTION_STDIO_INITIALIZE_TOOLS_LIST",
            "entrypoint": "python -m comsol_mcp.mcp_server",
        },
        "comsol64_jdk11_javac_javap": compile_receipt,
    }
    full = {
        "status": "PASS_ALL_OFFLINE_GATES",
        "mode": science.CURRENT_FULL_OFFLINE_MODE,
        "source_manifest_sha256": source_map_sha,
        "source_count": len(source_files),
        "gates": gate_rows,
        "native_process_started": False,
        "native_study_run_calls": 0,
        "runtime_imports": gate_rows["runtime_imports"],
        "software_negative_controls": gate_rows["software_negative_controls"],
        "pip_check": gate_rows["pip_check"],
        "stdio": gate_rows["stdio_initialize_tools_list"],
        "compile": compile_receipt,
        "gate_source_manifest_sha256": {
            name: source_map_sha for name in gate_rows
        },
        "gate_receipt_sha256": {
            name: science.sha256_json(value) for name, value in gate_rows.items()
        },
    }
    science.write_json(full_root / "offline_preflight_progress.json", full)
    science.write_json(full_root / "compile.json", compile_receipt)

    junit_path = offline / "focused_delta_junit.xml"
    junit_path.parent.mkdir(parents=True, exist_ok=True)
    junit_path.write_text(
        '<testsuite tests="25" failures="0" errors="0" skipped="0"></testsuite>\n',
        encoding="utf-8")
    guard_path = offline / "diagnostic-network-guard.jsonl"
    guard_path.write_text(
        json.dumps({"event": "GUARD_ACTIVE_BEFORE_CHILD_ENTRYPOINT"}) + "\n",
        encoding="utf-8")
    test_paths = [source_files[name]["path"] for name in required[2:]]
    excluded = [
        "tests/test_w23_r21_direction_discovery.py::"
        "test_science_dispatch_uses_actual_registered_session_runtime_after_public_model_load"
    ]
    test_receipt = {
        "status": "PASS_FOCUSED_STRICT_GUARDED_JUNIT",
        "returncode": 0,
        "candidate_source_manifest_sha256": source_map_sha,
        "test_paths": test_paths,
        "excluded_test_paths": excluded,
        "junit_path": str(junit_path),
        "junit_sha256": science.sha256_file(junit_path),
        "network_guard": {
            "status": "ACTIVE_NO_DENY_OR_SETUP_ERROR",
            "path": str(guard_path),
            "sha256": science.sha256_file(guard_path),
            "event_count": 1,
        },
    }
    science.write_json(offline / "diagnostic_junit_receipt.json", test_receipt)
    focused_receipt = {
        "schema_version": 1,
        "status": "PASS_FOCUSED_DELTA_CURRENT_FULL_SOURCE_MAP",
        "candidate_source_manifest_sha256": source_map_sha,
        "focused_tests": {
            "receipt_path": "offline_gates/diagnostic_junit_receipt.json",
            "receipt_sha256": science.sha256_file(offline / "diagnostic_junit_receipt.json"),
            "junit_path": "offline_gates/focused_delta_junit.xml",
            "junit_sha256": science.sha256_file(junit_path),
            "network_guard_path": "offline_gates/diagnostic-network-guard.jsonl",
            "network_guard_sha256": science.sha256_file(guard_path),
        },
        "registered_core_loopback": {
            "status": "NOT_RUN_DEFERRED_POST_W24_TERMINAL",
            "test_path": excluded[0],
            "strict_network_guard_enabled": False,
            "counted_in_guarded_junit": False,
        },
    }
    focused_path = offline / "focused_delta_receipt.json"
    science.write_json(focused_path, focused_receipt)
    current_progress_path = full_root / "offline_preflight_progress.json"
    compile_path = full_root / "compile.json"
    freeze = {
        "source_files": source_files,
        "source_manifest_sha256": source_map_sha,
        "offline_preflight": {
            "status": "PASS_ALL_OFFLINE_GATES",
            "mode": science.CURRENT_FULL_OFFLINE_MODE,
            "source_manifest_sha256": source_map_sha,
            "source_count": len(source_files),
            "current_full_gate_receipt": full,
            "current_full_receipt": {
                "path": "offline_gates/current_full/offline_preflight_progress.json",
                "sha256": science.sha256_file(current_progress_path),
            },
            "compile": compile_receipt,
            "runtime_imports": full["runtime_imports"],
            "software_negative_controls": full["software_negative_controls"],
            "pip_check": full["pip_check"],
            "stdio": full["stdio"],
            "compile_receipt": {
                "path": "offline_gates/current_full/compile.json",
                "sha256": science.sha256_file(compile_path),
            },
            "compile_artifact_root": "offline_gates/current_full/compile",
            "focused_delta_receipt": {
                "path": "offline_gates/focused_delta_receipt.json",
                "sha256": science.sha256_file(focused_path),
            },
            "registered_core_loopback": focused_receipt["registered_core_loopback"],
        },
    }

    class FakeJavaWorkerPaths:
        def __init__(self, *_args, **_kwargs):
            pass

        def classpath(self):
            return ("candidate-classpath", "a" * 64, 2, "b" * 64)

    monkeypatch.setattr(_java_worker, "JavaWorkerPaths", FakeJavaWorkerPaths)
    return science, freeze, candidate / "freeze.json", full, source_files, focused_path


def test_current_full_offline_receipt_positive_and_negative_controls(tmp_path, monkeypatch):
    science, freeze, freeze_path, full, source_files, focus_path = _current_full_offline_receipt_fixture(
        tmp_path / "valid", monkeypatch)
    result = science._check_offline_receipts(freeze_path, freeze)
    assert result["status"] == "PASS_FROZEN_CLASSPATH_AND_CLASS_OUTPUT_REVALIDATED"
    assert result["current_full_offline_gates"]["status"] == "PASS_CURRENT_FULL_SOURCE_BOUND_OFFLINE_GATES"
    assert result["current_full_offline_gates"]["registered_core_loopback"] == "NOT_RUN_DEFERRED_POST_W24_TERMINAL"

    science, tampered, freeze_path, full, _files, _focus = _current_full_offline_receipt_fixture(
        tmp_path / "mixed-gate-map", monkeypatch)
    bad = dict(full)
    bad["gate_source_manifest_sha256"] = dict(full["gate_source_manifest_sha256"])
    bad["gate_source_manifest_sha256"]["stdio_initialize_tools_list"] = "0" * 64
    progress = freeze_path.parent / "offline_gates/current_full/offline_preflight_progress.json"
    science.write_json(progress, bad)
    tampered["offline_preflight"]["current_full_gate_receipt"] = bad
    tampered["offline_preflight"]["current_full_receipt"]["sha256"] = science.sha256_file(progress)
    with pytest.raises(RuntimeError, match="not bound to the frozen source map"):
        science._check_offline_receipts(freeze_path, tampered)

    science, tampered, freeze_path, full, _files, _focus = _current_full_offline_receipt_fixture(
        tmp_path / "changed-java-source", monkeypatch)
    bad_compile = copy.deepcopy(full["compile"])
    bad_compile["compiled_sources"]["persistent_worker"]["source_sha256"] = "0" * 64
    bad = copy.deepcopy(full)
    bad["compile"] = bad_compile
    bad["gates"]["comsol64_jdk11_javac_javap"] = bad_compile
    bad["gate_receipt_sha256"]["comsol64_jdk11_javac_javap"] = science.sha256_json(bad_compile)
    progress = freeze_path.parent / "offline_gates/current_full/offline_preflight_progress.json"
    science.write_json(progress, bad)
    compile_path = freeze_path.parent / "offline_gates/current_full/compile.json"
    science.write_json(compile_path, bad_compile)
    outer = tampered["offline_preflight"]
    outer.update({
        "current_full_gate_receipt": bad,
        "current_full_receipt": {"path": "offline_gates/current_full/offline_preflight_progress.json",
                                 "sha256": science.sha256_file(progress)},
        "compile": bad_compile,
        "compile_receipt": {"path": "offline_gates/current_full/compile.json",
                            "sha256": science.sha256_file(compile_path)},
    })
    with pytest.raises(RuntimeError, match="differs from frozen Java source"):
        science._check_offline_receipts(freeze_path, tampered)

    science, tampered, freeze_path, _full, _files, focus_path = _current_full_offline_receipt_fixture(
        tmp_path / "focused-map-mismatch", monkeypatch)
    focus = json.loads(focus_path.read_text())
    focus["candidate_source_manifest_sha256"] = "0" * 64
    science.write_json(focus_path, focus)
    tampered["offline_preflight"]["focused_delta_receipt"]["sha256"] = science.sha256_file(focus_path)
    with pytest.raises(RuntimeError, match="current full source map"):
        science._check_offline_receipts(freeze_path, tampered)

    science, tampered, freeze_path, _full, source_files, _focus = _current_full_offline_receipt_fixture(
        tmp_path / "mutated-source", monkeypatch)
    changed = Path(source_files["comsol_mcp/worker_java/PersistentComsolWorker.java"]["path"])
    changed.write_text("changed after current source map\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="differs from its frozen map"):
        science._check_offline_receipts(freeze_path, tampered)



def test_offline_preflight_current_full_binds_gate_hashes_same_map(tmp_path, monkeypatch):
    from tools import run_native_w23_te_science as science
    from tools import w23_te_science_protocol as protocol_module

    source_path = tmp_path / "candidate-source.py"
    source_path.write_text("synthetic source identity only\n", encoding="utf-8")
    source_files = {
        "candidate-source.py": {
            "path": str(source_path.resolve()),
            "bytes": source_path.stat().st_size,
            "sha256": science.sha256_file(source_path),
        }
    }
    source_map_sha = science._source_manifest_sha256(source_files)
    binding = {"binding_sha256": "f" * 64}
    budget = {"scope": "offline-wrapper-test-only"}
    calls = []

    monkeypatch.setattr(science, "_runtime_helper_imports",
                        lambda: {"resolved": "production import gate invoked"})
    monkeypatch.setattr(protocol_module, "run_negative_controls",
                        lambda: {"status": "PASS_SOFTWARE_NEGATIVE_CONTROLS",
                                 "controls": [{"control": "test"}]})

    def pip_check(*, runtime_binding=None, runtime_budget=None):
        calls.append((runtime_binding, runtime_budget))
        if runtime_binding is None:
            return {"status": "PASS_TASK_VENV_PIP_CHECK"}
        return {"status": "PASS_FRESH_RUNTIME_BOUND_PIP_CHECK",
                "runtime_binding_sha256": runtime_binding["binding_sha256"]}

    monkeypatch.setattr(science, "_pip_dependency_check", pip_check)
    monkeypatch.setattr(science, "_actual_stdio_preflight",
                        lambda _path: {"status": "PASS_REAL_PRODUCTION_STDIO_INITIALIZE_TOOLS_LIST"})
    monkeypatch.setattr(science, "_compile_sources",
                        lambda _path: {"status": "PASS_COMSOL_64_JDK11_COMPILE_AND_JAVAP",
                                       "compiled_sources": {}})

    current = science.offline_preflight(
        tmp_path / "current-full",
        current_source_files=source_files,
        runtime_binding=binding,
        runtime_budget=budget)
    assert current["status"] == "PASS_ALL_OFFLINE_GATES"
    assert current["mode"] == science.CURRENT_FULL_OFFLINE_MODE
    assert current["source_manifest_sha256"] == source_map_sha
    assert current["source_count"] == 1
    assert set(current["gate_source_manifest_sha256"]) == set(science.CURRENT_FULL_OFFLINE_GATES)
    assert set(current["gate_receipt_sha256"]) == set(science.CURRENT_FULL_OFFLINE_GATES)
    assert all(value == source_map_sha
               for value in current["gate_source_manifest_sha256"].values())
    assert all(current["gate_receipt_sha256"][name] == science.sha256_json(current["gates"][name])
               for name in science.CURRENT_FULL_OFFLINE_GATES)
    assert calls == [(binding, budget)]
    saved = json.loads((tmp_path / "current-full/offline_preflight_progress.json").read_text())
    assert saved == current

    legacy = science.offline_preflight(tmp_path / "legacy-default")
    assert legacy["status"] == "PASS_ALL_OFFLINE_GATES"
    assert "mode" not in legacy
    assert "gate_source_manifest_sha256" not in legacy
    assert "gate_receipt_sha256" not in legacy
    assert calls[-1] == (None, None)
