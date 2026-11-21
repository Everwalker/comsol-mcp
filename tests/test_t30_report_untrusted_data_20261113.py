"""U09: caller-authored report data cannot certify a managed model.

These cases call the actual report implementation without a Worker, destination,
model, database, network, or other live resource. The supplied observations and
statuses are deliberately fabricated and must remain presentation-only.
"""
from __future__ import annotations

import json

from comsol_mcp._g3_w20_validation import STATUS_UNVERIFIED, validate_report


def test_caller_supplied_pass_does_not_certify_model_numerics():
    claimed = {
        "source_identity": "synthetic-caller",
        "raw_observations": {"temperature": 300.0},
        "error_tolerance_data": {"status": "PASS", "numerical_verification_status": "PASS"},
    }

    result = validate_report(None, "synthetic-model", {"data": claimed})

    assert result["numerical_verification_status"] == STATUS_UNVERIFIED
    assert result["physical_validation_status"] == STATUS_UNVERIFIED
    assert result["status"] != "PASS"
    presentation = json.loads(result["report_json"])
    assert presentation["raw_observations"] == claimed["raw_observations"]
    assert presentation["error_tolerance_data"] == claimed["error_tolerance_data"]
