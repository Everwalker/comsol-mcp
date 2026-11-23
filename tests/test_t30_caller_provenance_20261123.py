"""T30 caller-only values cannot certify a model; no file or Worker IO."""
import json

import pytest  # pyright: ignore[reportMissingImports]

from comsol_mcp._domain_outcome import classify
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g3_ops import dispatch


def test_report_caller_pass_is_not_model_validation():
    result = dispatch("validate.report", None, "synthetic-model", {
        "data": {"status": "PASS", "numerical_verification_status": "PASS",
                 "raw_observations": {"temperature": 7}},
    })
    assert result["status"] == "INCOMPLETE"
    assert result["execution_status"] == "PASS"
    assert result["numerical_verification_status"] == "UNVERIFIED"
    assert result["physical_validation_status"] == "UNVERIFIED"
    assert result["model_validated"] is False
    assert json.loads(result["report_json"])["report_evidence_provenance"] == "CALLER_SUPPLIED_NOT_AUTHENTICATED"
    assert classify("validate.report", result).state == "succeeded"


@pytest.mark.parametrize("data", [{"status": "UNVERIFIED"}, {}])
def test_unverified_report_is_successful_execution_with_unverified_numerics(data):
    result = dispatch("validate.report", None, "synthetic-model", {"data": data})
    assert result["status"] == "INCOMPLETE"
    assert result["execution_status"] == "PASS"
    assert result["numerical_verification_status"] == "UNVERIFIED"
    assert classify("validate.report", result).state == "succeeded"


def test_conservation_external_math_remains_useful_but_not_model_validation():
    result = dispatch("validate.conservation", None, "synthetic-model", {
        "definition": {"inflow": 4, "outflow": 4, "tolerance": 0.01},
    })
    assert result["passed"] is True and result["residual"] == 0
    assert result["external_numerical_verification_status"] == "PASS"
    assert result["scope"] == "EXTERNAL_DATA_ONLY"
    assert result["model_validated"] is False
    assert result["numerical_verification_status"] == "UNVERIFIED"


def test_solution_caller_values_do_not_certify_model_numerics():
    result = dispatch("validate.solution", None, "synthetic-model", {
        "criteria": {"values": [1.0, 2.0], "range": [0.0, 3.0]},
    })
    assert result["status"] == "PASS"
    assert result["scope"] == "EXTERNAL_DATA_ONLY"
    assert result["model_validated"] is False
    assert result["external_numerical_verification_status"] == "PASS"
    assert result["numerical_verification_status"] == "UNVERIFIED"


@pytest.mark.parametrize("status", ["FAIL", "ERROR", "BLOCKED", "UNSUPPORTED"])
def test_caller_report_nonpass_states_not_promoted(status):
    result = dispatch("validate.report", None, "synthetic-model", {"data": {"status": status}})
    assert result["status"] == status
    assert result["numerical_verification_status"] != "PASS"


def test_report_destination_requires_backend_owned_project_context(tmp_path):
    destination = tmp_path / "caller-selected" / "report.json"
    with pytest.raises(ExecutionContractError) as exc:
        dispatch("validate.report", None, "synthetic-model", {
            "destination": str(destination), "data": {"status": "PASS"},
        })
    assert exc.value.code == "MANAGED_CONTEXT_REQUIRED"
    assert not destination.exists()
    assert not destination.parent.exists()
