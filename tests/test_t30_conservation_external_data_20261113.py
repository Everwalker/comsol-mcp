"""U09 external-data labeling for caller-only conservation checks.

The numerical inputs are fabricated and evaluated entirely in memory. They
must remain useful for their own calculation without certifying a live model.
"""
from __future__ import annotations

from comsol_mcp._g3_w20_validation import STATUS_UNVERIFIED, validate_conservation


def test_caller_conservation_is_labeled_external_not_current_model():
    result = validate_conservation(None, "synthetic-model", {
        "definition": {"inflow": 5.0, "outflow": 5.0, "normalization": 10.0, "tolerance": 0.01}
    })

    assert result["passed"] is True
    assert result["residual"] == 0.0
    assert result["scope"] == "EXTERNAL_DATA_ONLY"
    assert result["model_validated"] is False
    assert result["numerical_verification_status"] == STATUS_UNVERIFIED
    assert result["physical_validation_status"] == STATUS_UNVERIFIED
