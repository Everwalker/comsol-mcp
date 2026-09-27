from __future__ import annotations

import copy
import math

import pytest

from tests.test_w23_mode_basis_v2 import (
    COORDINATE_FRAME,
    FREQUENCY,
    MODEL_REF,
    _request as _synthetic_basis_request,
)
from tools.w23_mode_basis_v2 import build_two_mode_basis_request
from tools.w23_mode_basis_v2_native_plan import (
    NATIVE_PLAN_SCHEMA_ID,
    NativePlanError,
    build_two_mode_comsol_integral_plan,
)


def _request():
    """Use legal COMSOL node tags; the shared math fixture uses display-like IDs."""
    original = _synthetic_basis_request()
    source_tags = {
        "signal_source": ("dSignal", "sSignal"),
        "incident_source": ("dIncident", "sIncident"),
    }
    inputs = {name: dict(original[name]) for name in source_tags}
    for name, (dataset, solution) in source_tags.items():
        inputs[name]["dataset_id"] = dataset
        inputs[name]["solution_id"] = solution
    modes = []
    for index, row in enumerate(original["basis_modes"]):
        source = dict(row["source"])
        letter = "A" if index == 0 else "B"
        source.update(dataset_id=f"dMode{letter}", solution_id=f"sMode{letter}")
        modes.append({"mode_id": row["mode_id"], "mode_index": row["mode_index"],
                      "source": source})
    return build_two_mode_basis_request(
        basis_id=original["basis_id"], case=original["case"],
        project_id=original["project_id"], model_ref=original["model_ref"],
        model_revision=original["model_revision"],
        geometry_revision=original["geometry_revision"],
        frequency_hz=original["frequency_hz"],
        coordinate_frame=original["coordinate_frame"],
        output_surface=original["output_surface"], input_surface=original["input_surface"],
        signal_source=inputs["signal_source"], mode_sources=modes,
        incident_source=inputs["incident_source"],
        applicability=original["applicability"],
    )


def _solution_readback(request, role, source, *, mode=False, **changes):
    output = request["output_surface"] if role != "incident" else request["input_surface"]
    parameters = {"freq": (FREQUENCY, "Hz")}
    mode_axis = "lambda" if mode else None
    if mode:
        parameters[mode_axis] = (1.55e-6 + source["mode_index"] * 1e-9, "m")
    axes = {
        "pair_mapping_complete": True,
        "outer_index": source["outer_index"], "inner_index": source["inner_index"],
        "solnum": source["solnum"], "parameter_names": list(parameters),
        "parameters_by_pair": parameters,
        "frequency": {"parameter": "freq", "value": FREQUENCY, "unit": "Hz",
                      "frequency_hz": FREQUENCY},
    }
    result = {
        "dataset_binding": {
            "dataset": source["dataset_id"], "solution": source["solution_id"],
            "component": output["component"], "geometry": output["geometry"],
            "binding_complete": True,
        },
        "solution_axes": axes,
        "source_context": {
            "project_id": request["project_id"], "model_ref": dict(MODEL_REF),
            "model_revision": request["model_revision"],
            "geometry_revision": request["geometry_revision"],
            "coordinate_frame": COORDINATE_FRAME,
        },
        "mode_axis_parameter": mode_axis,
    }
    for key, value in changes.items():
        if key == "solution_axes":
            result[key].update(value)
        elif key == "dataset_binding":
            result[key].update(value)
        elif key == "source_context":
            result[key].update(value)
        else:
            result[key] = value
    return result


def _native_inputs(request):
    modes = request["basis_modes"]
    return {
        "solution_readbacks": {
            "signal": _solution_readback(request, "signal", request["signal_source"]),
            "mode_0": _solution_readback(request, "mode_0", modes[0]["source"], mode=True),
            "mode_1": _solution_readback(request, "mode_1", modes[1]["source"], mode=True),
            "incident": _solution_readback(request, "incident", request["incident_source"]),
        },
        "surface_readbacks": {
            "output": {**copy.deepcopy(request["output_surface"]), "entity_ids": [17, 18],
                       "readback_source": "SYNTHETIC_TEST_FIXTURE_ONLY"},
            "input": {**copy.deepcopy(request["input_surface"]), "entity_ids": [3],
                      "readback_source": "SYNTHETIC_TEST_FIXTURE_ONLY"},
        },
    }


def _plan():
    request = _request()
    return request, build_two_mode_comsol_integral_plan(request, **_native_inputs(request))


def test_v2_comsol_plan_contains_exact_eight_surface_integrals_and_stays_not_run():
    request, plan = _plan()
    assert plan["schema_id"] == NATIVE_PLAN_SCHEMA_ID
    assert plan["basis_request_id"] == request["request_id"]
    assert len(plan["terms"]) == 8
    assert {row["term_id"] for row in plan["terms"]} == {
        "G00", "G01", "G10", "G11", "b0", "b1", "P_signal", "P_incident"}
    assert {row["expected_unit"] for row in plan["terms"]} == {"W"}
    assert {row["native_feature_type"] for row in plan["terms"]} == {"IntSurface"}
    assert all(row["selection"]["entity_dimension"] == 2 for row in plan["terms"])
    assert all(row["solution"]["dataset"] and row["solution"]["solution"] for row in plan["terms"])
    assert plan["managed_operation_id"] == "result.mode_overlap_basis_v2"
    assert plan["native_result"] == "NOT_RUN"
    assert plan["study_or_solver_invoked"] is False
    assert plan["dispatchable"] is False
    assert plan["production_route_status"] == "ROUTE_REGISTERED_NOT_DISPATCHED"

    terms = {row["term_id"]: row for row in plan["terms"]}
    cross = terms["G01"]
    assert cross["expression_current_solution"] == "sModeA"
    assert cross["expression_secondary_solution"] == "sModeB"
    assert "withsol('sModeB',ewfd.Emodex_2,setind(lambda,1),setval(freq," in cross["expression"]
    assert "withsol('sModeB',ewfd.Hmodez_2,setind(lambda,1),setval(freq," in cross["expression"]
    assert "conj(ewfd.Hmodez_2)" in cross["expression"]
    assert "conj(ewfd.Emodey_2)" in cross["expression"]
    assert all(f"*n{axis}" in cross["expression"] for axis in "xyz")
    assert "0.25*" in cross["expression"]

    signal_coupling = terms["b0"]
    assert signal_coupling["expression_current_solution"] == "sModeA"
    assert signal_coupling["expression_secondary_solution"] == "sSignal"
    assert "withsol('sSignal',ewfd.Ex,setval(freq," in signal_coupling["expression"]
    assert "setind(" not in signal_coupling["expression"].split("withsol('sSignal'", 1)[1]

    # COMSOL's native outward normal differs from the propagation direction
    # on the input face. The plan must apply the frozen -1 orientation there.
    assert terms["P_signal"]["expression"].startswith("(1)*(0.25*")
    assert terms["P_incident"]["expression"].startswith("(-1)*(0.25*")
    assert terms["P_incident"]["integration_surface"] == "input"
    assert terms["P_incident"]["expected_result_binding"]["entity_ids"] == [3]


@pytest.mark.parametrize("mutation, match", [
    ("foreign_dataset", "dataset binding"),
    ("wrong_pair", "SolutionInfo outer_index"),
    ("incomplete_pair_map", "pair mapping is not complete"),
    ("foreign_frequency", "frequency differs"),
    ("wrong_frequency_units", "native frequency unit"),
    ("wrong_source_context", "source context project_id"),
        ("mode_axis_missing", "mode-axis selection"),
        ("mode_index_mismatch", "SolutionInfo inner_index"),
    ("unsafe_mode_axis", "valid COMSOL tag"),
])
def test_v2_comsol_plan_rejects_unbound_solution_readbacks(mutation, match):
    request = _request()
    inputs = _native_inputs(request)
    if mutation == "foreign_dataset":
        inputs["solution_readbacks"]["mode_0"]["dataset_binding"]["dataset"] = "d-other"
    elif mutation == "wrong_pair":
        inputs["solution_readbacks"]["signal"]["solution_axes"]["outer_index"] = 2
    elif mutation == "incomplete_pair_map":
        inputs["solution_readbacks"]["incident"]["solution_axes"]["pair_mapping_complete"] = False
    elif mutation == "foreign_frequency":
        axes = inputs["solution_readbacks"]["mode_1"]["solution_axes"]
        axes["frequency"]["frequency_hz"] *= 1.01
    elif mutation == "wrong_frequency_units":
        axes = inputs["solution_readbacks"]["mode_1"]["solution_axes"]
        axes["parameters_by_pair"]["freq"] = (FREQUENCY, "mHz")
        axes["frequency"]["unit"] = "mHz"
    elif mutation == "wrong_source_context":
        inputs["solution_readbacks"]["signal"]["source_context"]["project_id"] = "foreign"
    elif mutation == "mode_axis_missing":
        inputs["solution_readbacks"]["mode_0"]["mode_axis_parameter"] = "missing"
    elif mutation == "mode_index_mismatch":
        inputs["solution_readbacks"]["mode_1"]["solution_axes"]["inner_index"] = 2
    elif mutation == "unsafe_mode_axis":
        inputs["solution_readbacks"]["mode_0"]["mode_axis_parameter"] = "lambda),1),evil"
    with pytest.raises(NativePlanError, match=match):
        build_two_mode_comsol_integral_plan(request, **inputs)


@pytest.mark.parametrize("mutation, match", [
    ("wrong_dimension", "not a 3-D boundary"),
    ("empty_selection", "nonempty unique boundary IDs"),
    ("duplicate_ids", "nonempty unique boundary IDs"),
    ("foreign_selection", "selection_tag differs"),
    ("wrong_frame", "frame_id differs"),
    ("area_mismatch", "area differs"),
    ("normal_flip", "normal sign differs"),
    ("center_shift", "center_xyz_um differs"),
    ("axis_not_unit", "axis_xyz differs"),
])
def test_v2_comsol_plan_rejects_unbound_surface_readbacks(mutation, match):
    request = _request()
    inputs = _native_inputs(request)
    surface = inputs["surface_readbacks"]["output"]
    if mutation == "wrong_dimension":
        surface["entity_dimension"] = 1
    elif mutation == "empty_selection":
        surface["entity_ids"] = []
    elif mutation == "duplicate_ids":
        surface["entity_ids"] = [2, 2]
    elif mutation == "foreign_selection":
        surface["selection_tag"] = "sel-foreign"
    elif mutation == "wrong_frame":
        surface["frame_id"] = "frame-foreign"
    elif mutation == "area_mismatch":
        surface["surface_area_m2"] *= 1.01
    elif mutation == "normal_flip":
        surface["native_normal_sign"] *= -1
    elif mutation == "center_shift":
        surface["center_xyz_um"][0] += 1e-6
    elif mutation == "axis_not_unit":
        surface["axis_xyz"][0] = 0.9
    with pytest.raises(NativePlanError, match=match):
        build_two_mode_comsol_integral_plan(request, **inputs)


def test_v2_comsol_plan_refuses_tampered_request_and_duplicate_parameters():
    request = _request()
    tampered = copy.deepcopy(request)
    tampered["basis_modes"][1]["source"]["solution_id"] = "foreign-solution"
    with pytest.raises(Exception, match="identity digest"):
        build_two_mode_comsol_integral_plan(tampered, **_native_inputs(request))

    inputs = _native_inputs(request)
    axes = inputs["solution_readbacks"]["signal"]["solution_axes"]
    axes["parameter_names"] = ["freq", "FREQ"]
    axes["parameters_by_pair"]["FREQ"] = axes["parameters_by_pair"]["freq"]
    with pytest.raises(NativePlanError, match="incomplete or duplicated"):
        build_two_mode_comsol_integral_plan(request, **inputs)
