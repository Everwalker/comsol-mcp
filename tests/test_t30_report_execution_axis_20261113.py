"""A generated report is not an UNKNOWN execution merely because its data is unverified.

Pure in-process classification against caller-authored input; no report file,
model, Worker, database, SSH or COMSOL is involved.
"""
from __future__ import annotations

from comsol_mcp._domain_outcome import STATE_SUCCEEDED, classify
from comsol_mcp._g3_w20_validation import STATUS_UNVERIFIED, validate_report


def test_caller_report_retains_successful_execution_and_unverified_numerics():
    data = validate_report(None, "synthetic-model", {
        "data": {"error_tolerance_data": {"status": "PASS", "numerical_verification_status": "PASS"}}
    })
    outcome = classify("validate.report", data)

    assert outcome.state == STATE_SUCCEEDED
    assert outcome.execution_state_unknown is False
    assert data["numerical_verification_status"] == STATUS_UNVERIFIED
    assert data["model_validated"] is False
