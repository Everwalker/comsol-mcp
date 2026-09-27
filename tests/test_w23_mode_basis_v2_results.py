"""Software-only tests for the isolated W23 v2 native integral adapter.

The low-level COMSOL reads/integrals are replaced with typed test evidence.
These cases verify routing/plumbing contracts only; native status remains
NOT_RUN until a separately approved managed invocation is performed.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from comsol_mcp import _g3_common, _w23_results
from comsol_mcp._g2_contract import ExecutionContractError
from comsol_mcp._w23_basis_v2_results import (
    NATIVE_RESULT_SCHEMA,
    OPERATION_ID,
    RESULT_SCHEMA_ID,
    build_definition,
    result_mode_overlap_basis_v2,
    validate_request_shape,
)
from comsol_mcp._observation_store import observation_context
from tests.test_w23_mode_basis_v2_native_plan import _request


def _definition():
    request = _request()
    axes = [
        {"mode_id": row["mode_id"], "parameter": "lambda"}
        for row in request["basis_modes"]
    ]
    return request, build_definition(request, axes)


def _invoke(request, definition, *, model_tag="model1", project_id=None,
            model_ref=None, revision=None):
    with observation_context(
        None,
        request["model_ref"] if model_ref is None else model_ref,
        request["model_revision"] if revision is None else revision,
        "test-managed-operation",
        project_id=request["project_id"] if project_id is None else project_id,
    ):
        return result_mode_overlap_basis_v2(object(), model_tag, {"definition": definition})


def _install_native_test_doubles(monkeypatch, request, *, mutate_source=None,
                                 mutate_selection=None, area_override=None,
                                 cleanup_removed=True, complex_flag=True):
    calls = []
    monkeypatch.setattr(_g3_common, "bound_model", lambda _worker, tag: {"model_tag": tag})

    def solution_info(_model, source, *, label):
        if mutate_source:
            source_binding, axes = mutate_source(label, source)
            if source_binding is not None:
                return source_binding, axes
        surface = request["input_surface"] if label == "incident" else request["output_surface"]
        names = ["freq"]
        pairs = {"freq": (request["frequency_hz"], "Hz")}
        if label.startswith("mode_"):
            names.append("lambda")
            pairs["lambda"] = (1.55e-6 + source["mode_index"] * 1e-9, "m")
        return ({
            "binding_complete": True,
            "dataset": source["dataset_id"],
            "solution": source["solution_id"],
            "component": surface["component"],
            "geometry": surface["geometry"],
        }, {
            "outer_index": source["outer_index"],
            "inner_index": source["inner_index"],
            "solnum": source["solnum"],
            "frequency": {"parameter": "freq", "value": request["frequency_hz"],
                          "unit": "Hz", "frequency_hz": request["frequency_hz"]},
            "parameter_names": names,
            "parameters_by_pair": pairs,
        })

    def source_selection(_worker, _tag, source_binding, selection, *, label):
        if mutate_selection:
            replacement = mutate_selection(label, source_binding, selection)
            if replacement is not None:
                return replacement
        return {
            "component": selection["component"],
            "geometry": selection["geometry"],
            "selection_tag": selection["tag"],
            "entity_dimension": 2,
            "entity_ids": [17, 18] if selection["tag"] == request["output_surface"]["selection_tag"] else [3],
            "readback_source": "synthetic_test_double_only",
        }

    monkeypatch.setattr(_w23_results, "_solution_info", solution_info)
    monkeypatch.setattr(_w23_results, "_source_selection", source_selection)
    monkeypatch.setattr(_w23_results, "_space_dimension", lambda *_args, **_kwargs: 3)

    values = {
        "G00": 1.0 + 0.0j,
        "G01": 0.1 + 0.2j,
        "G10": 0.1 - 0.2j,
        "G11": 1.0 + 0.0j,
        "b0": 0.3 + 0.4j,
        "b1": 0.2 - 0.1j,
        "P_signal": 2.0 + 0.0j,
        "P_incident": 1.0 + 0.0j,
    }

    def native_integral(_worker, _tag, source, selection, _readback, expression, *, label):
        term_id = label.removeprefix("W23 v2 ")
        calls.append({"term_id": term_id, "source": dict(source),
                      "selection": dict(selection), "expression": expression})
        area = area_override(term_id, selection) if area_override else (
            request["input_surface"]["surface_area_m2"]
            if selection["tag"] == request["input_surface"]["selection_tag"]
            else request["output_surface"]["surface_area_m2"])
        return {
            "value": values[term_id], "unit": "W", "is_complex": complex_flag,
            "expression": expression, "dataset": source["dataset_id"],
            "solution": source["solution_id"], "selection_measure": area,
            "selection_measure_source": "COMSOL engine integral of 1 over named selection",
            "cleanup": {"created": True, "removed": cleanup_removed,
                        "cleanup_failed": not cleanup_removed, "type_id": "IntSurface",
                        "tag": "tmp_w23_v2"},
        }

    monkeypatch.setattr(_w23_results, "_native_integral", native_integral)
    return calls


def test_definition_schema_is_digest_bound_and_accepts_only_one_definition():
    request, definition = _definition()
    assert definition["schema_id"].endswith("definition:1.0.0")
    validate_request_shape({"definition": definition})
    with pytest.raises(ExecutionContractError, match="managed execution identity only"):
        validate_request_shape({"definition": definition, "fields": []})

    altered = copy.deepcopy(definition)
    altered["mode_axis_parameters"][0]["parameter"] = "lambda2"
    with pytest.raises(ExecutionContractError) as caught:
        validate_request_shape({"definition": altered})
    assert caught.value.code == "IDENTITY_DIGEST_MISMATCH"
    assert request["request_id"] == definition["basis_request"]["request_id"]


def test_handler_integrates_exact_gram_couplings_and_powers_on_bound_named_surfaces(monkeypatch):
    request, definition = _definition()
    calls = _install_native_test_doubles(monkeypatch, request)

    result = _invoke(request, definition)

    assert result["schema_id"] == RESULT_SCHEMA_ID
    assert result["operation_id"] == OPERATION_ID
    assert result["status"] == "SUCCEEDED"
    assert result["result_status"] == "COMPUTED_NATIVE_TWO_MODE_INTEGRALS"
    assert result["native_result"] == "COMSOL_NATIVE_RAW"
    assert result["quadrature_comparison"] == "NOT_RUN"
    assert result["projection_acceptance"] == "NOT_RUN"
    assert result["production_route_status"] == "ROUTE_REGISTERED_NATIVE_INTEGRATION_ONLY"
    from jsonschema import validate
    validate(instance=result, schema=NATIVE_RESULT_SCHEMA)
    assert result["study_or_solver_invoked"] is False
    assert result["caller_field_arrays_accepted"] is False
    assert len(calls) == 8
    assert {call["term_id"] for call in calls} == {
        "G00", "G01", "G10", "G11", "b0", "b1", "P_signal", "P_incident",
    }
    assert result["native_integrals"]["gram_matrix"][0][1] == {"real": 0.1, "imag": 0.2, "unit": "W", "expression": calls[1]["expression"], "dataset_id": "dModeA", "solution_id": "sModeA", "feature_type": "IntSurface", "cleanup": {"created": True, "removed": True, "cleanup_failed": False, "type_id": "IntSurface", "tag": "tmp_w23_v2"}, "selection_measure_m2": request["output_surface"]["surface_area_m2"], "selection_measure_source": "COMSOL engine integral of 1 over named selection", "is_complex": True}

    terms = {call["term_id"]: call for call in calls}
    assert "0.25*(1)*" in terms["G01"]["expression"]
    assert "withsol('sModeB',ewfd.Emodex_2,setval(freq," in terms["G01"]["expression"]
    assert "setind(lambda,1)" in terms["G01"]["expression"]
    assert "withsol('sModeB',ewfd.Hmodez_2,setval(freq," in terms["G01"]["expression"]
    assert "conj(ewfd.Hmodez_2)" in terms["G01"]["expression"]
    assert "conj(ewfd.Emodey_2)" in terms["G01"]["expression"]
    assert terms["G01"]["source"]["solution_id"] == "sModeA"
    assert terms["P_signal"]["expression"].startswith("0.5*real((1)*")
    assert terms["P_incident"]["expression"].startswith("0.5*real((-1)*")
    assert result["identity"]["mode_indices"] == [1, 2]
    assert result["source_readbacks"]["mode_1"]["solution_axes"]["inner_index"] == 1
    assert result["source_readbacks"]["mode_1"]["numeric_port_mode_index"] == {
        "value": 2, "origin": "canonical_request_provenance_only",
        "native_port_mode_index_readback": "NOT_PROVIDED",
    }
    assert all(row["entity_dimension"] == 2 for row in result["term_bindings"].values())


@pytest.mark.parametrize("mutation, expected_code", [
    ("wrong_inner", "SOLUTION_INDEX_MISMATCH"),
    ("wrong_frequency", "FREQUENCY_MISMATCH"),
    ("wrong_model", "MODEL_IDENTITY_MISMATCH"),
])
def test_handler_rejects_native_source_mismatch_before_integrating(monkeypatch, mutation, expected_code):
    request, definition = _definition()

    def mutate_source(label, source):
        binding = {"binding_complete": True, "dataset": source["dataset_id"],
                   "solution": source["solution_id"], "component": request["output_surface"]["component"],
                   "geometry": request["output_surface"]["geometry"]}
        axes = {"outer_index": source["outer_index"], "inner_index": source["inner_index"],
                "solnum": source["solnum"],
                "frequency": {"value": request["frequency_hz"], "unit": "Hz",
                              "frequency_hz": request["frequency_hz"]},
                "parameter_names": ["freq", "lambda"] if label.startswith("mode_") else ["freq"],
                "parameters_by_pair": {"freq": (request["frequency_hz"], "Hz")}}
        if label.startswith("mode_"):
            axes["parameters_by_pair"]["lambda"] = (1.55e-6, "m")
        if label == "mode_1":
            if mutation == "wrong_inner":
                axes["inner_index"] += 1
            elif mutation == "wrong_frequency":
                axes["frequency"]["frequency_hz"] *= 1.01
            elif mutation == "wrong_model":
                source = dict(source)
                source["model_ref"] = {**source["model_ref"], "model_tag": "other"}
        return binding, axes

    calls = _install_native_test_doubles(monkeypatch, request, mutate_source=mutate_source)
    model_tag = "other" if mutation == "wrong_model" else "model1"
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition, model_tag=model_tag)
    assert caught.value.code == expected_code
    assert calls == []


def test_handler_rejects_selection_identity_dimension_and_area_drift(monkeypatch):
    request, definition = _definition()

    def mutate_selection(label, _binding, selection):
        if label != "signal":
            return None
        return {"component": selection["component"], "geometry": selection["geometry"],
                "selection_tag": selection["tag"], "entity_dimension": 1,
                "entity_ids": [17], "readback_source": "synthetic"}

    calls = _install_native_test_doubles(monkeypatch, request, mutate_selection=mutate_selection)
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition)
    assert caught.value.code == "SELECTION_DIMENSION_MISMATCH"
    assert calls == []

    def wrong_area(term_id, selection):
        expected = request["output_surface"]["surface_area_m2"]
        return expected * 1.01 if term_id == "G00" else expected

    calls = _install_native_test_doubles(monkeypatch, request, area_override=wrong_area)
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition)
    assert caught.value.code == "SELECTION_MEASURE_MISMATCH"
    assert len(calls) == 1


def test_handler_rejects_basis_sources_that_resolve_to_different_boundary_ids(monkeypatch):
    request, definition = _definition()

    def mutate_selection(label, _binding, selection):
        if label == "mode_1":
            return {"component": selection["component"], "geometry": selection["geometry"],
                    "selection_tag": selection["tag"], "entity_dimension": 2,
                    "entity_ids": [18, 19], "readback_source": "synthetic"}
        return None

    calls = _install_native_test_doubles(monkeypatch, request, mutate_selection=mutate_selection)
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition)
    assert caught.value.code == "SELECTION_READBACK_MISMATCH"
    assert calls == []


def test_handler_requires_complex_status_for_reciprocal_integrals(monkeypatch):
    request, definition = _definition()
    calls = _install_native_test_doubles(monkeypatch, request, complex_flag=False)
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition)
    assert caught.value.code == "COMPLEX_DATA_ERROR"
    assert len(calls) == 1


@pytest.mark.parametrize("context_change, expected_code", [
    ("foreign_project", "PROJECT_IDENTITY_MISMATCH"),
    ("foreign_server", "MODEL_IDENTITY_MISMATCH"),
    ("stale_generation", "MODEL_IDENTITY_MISMATCH"),
    ("stale_revision", "REVISION_CONFLICT"),
])
def test_handler_binds_payload_to_active_managed_project_model_and_revision(
        monkeypatch, context_change, expected_code):
    request, definition = _definition()
    calls = _install_native_test_doubles(monkeypatch, request)
    monkeypatch.setattr(_g3_common, "bound_model",
                        lambda *_args, **_kwargs: pytest.fail("managed identity must be checked before Worker reads"))
    model_ref = dict(request["model_ref"])
    project_id = request["project_id"]
    revision = request["model_revision"]
    if context_change == "foreign_project":
        project_id = "different-project"
    elif context_change == "foreign_server":
        model_ref["server_instance_id"] = "different-server"
    elif context_change == "stale_generation":
        model_ref["generation"] += 1
    elif context_change == "stale_revision":
        revision -= 1

    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition, project_id=project_id, model_ref=model_ref,
                revision=revision)
    assert caught.value.code == expected_code
    assert calls == []


def test_handler_requires_authoritative_managed_execution_context(monkeypatch):
    request, definition = _definition()
    calls = _install_native_test_doubles(monkeypatch, request)
    with pytest.raises(ExecutionContractError) as caught:
        result_mode_overlap_basis_v2(object(), "model1", {"definition": definition})
    assert caught.value.code == "PROVENANCE_CONTEXT_REQUIRED"
    assert calls == []


def test_installed_package_contract_has_no_dependency_on_development_tools_tree(tmp_path):
    request, _definition_value = _definition()
    package_root = tmp_path / "isolated_source"
    package_dir = package_root / "comsol_mcp"
    package_dir.mkdir(parents=True)
    repo_root = Path(__file__).resolve().parents[1]
    for name in (
        "__init__.py", "_execution_contract.py", "_g2_contract.py",
        "_w23_basis_v2_contract.py", "_w23_basis_v2_results.py",
    ):
        shutil.copy2(repo_root / "comsol_mcp" / name, package_dir / name)
    request_path = tmp_path / "basis_request.json"
    request_path.write_text(json.dumps(request, allow_nan=False), encoding="utf-8")
    code = r"""
import importlib.abc
import json
from pathlib import Path
import sys

class BlockDevelopmentTools(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tools" or fullname.startswith("tools."):
            raise AssertionError("production W23 package attempted to import development tools")
        return None

sys.meta_path.insert(0, BlockDevelopmentTools())
from comsol_mcp import _w23_basis_v2_results as result_module
from comsol_mcp._w23_basis_v2_results import build_definition, validate_request_shape

isolated_root = Path(sys.argv[1]).resolve()
assert Path(result_module.__file__).resolve().is_relative_to(isolated_root / "comsol_mcp")
request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
axes = [{"mode_id": row["mode_id"], "parameter": "lambda"} for row in request["basis_modes"]]
definition = build_definition(request, axes)
validate_request_shape({"definition": definition})
assert definition["basis_request"]["request_id"] == request["request_id"]
print("ISOLATED_PACKAGE_VALIDATION_PASS")
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(package_root)
    completed = subprocess.run(
        [sys.executable, "-c", code, str(package_root), str(request_path)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ISOLATED_PACKAGE_VALIDATION_PASS" in completed.stdout


def test_handler_preserves_unknown_integral_cleanup_and_stops_without_retry(monkeypatch):
    request, definition = _definition()
    calls = _install_native_test_doubles(monkeypatch, request, cleanup_removed=False)
    with pytest.raises(ExecutionContractError) as caught:
        _invoke(request, definition)
    assert caught.value.code == "EXECUTION_STATE_UNKNOWN"
    assert caught.value.stage == "post_dispatch"
    assert len(calls) == 1
