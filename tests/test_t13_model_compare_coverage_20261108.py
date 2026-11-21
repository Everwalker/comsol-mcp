"""Synthetic model.compare completeness after metric augmentation.

Public-interface OBSERVED coverage must bind the projected path, receiver
receipt and actual supported runtime; it is not by itself native verification.
"""
import copy
from types import SimpleNamespace

import pytest

from comsol_mcp import _g2_model_compare as compare


@pytest.fixture
def synthetic_projection():
    path = {"segments": []}
    version = "6.4.0.293"
    interface = "com.comsol.model.ModelEntity"
    node = {"path": path, "fields": {"public_interfaces": [interface]}}
    coverage = [{"side": "left", "path": path, "field": "public_interfaces",
                 "status": "OBSERVED", "getter": {"method": "describe_public",
                                                    "runtime_version": version}, "reason": None}]
    receipts = [{"path": path, "descriptor": {"runtime_version": version,
                                                 "interfaces": [interface]}}]
    reader = SimpleNamespace(role="left", nodes={"root": node}, coverage=coverage,
                             receiver_receipts=receipts, runtime_version=version,
                             errors=[], unknown=False)
    projection = {"nodes": [node], "coverage": coverage, "receiver_receipts": receipts,
                  "runtime_version": version, "errors": [], "unknown": False, "complete": False}
    return reader, projection


def finalize(reader, projection):
    compare._add_metrics(reader, projection, None, {"metrics": []})
    return projection


def test_matching_interface_observation_survives_metrics_recalculation(synthetic_projection):
    reader, projection = synthetic_projection
    result = finalize(reader, projection)
    assert result["complete"] is True
    assert result["coverage"][0]["status"] == "OBSERVED"


@pytest.mark.parametrize("fault", ["wrong_field", "wrong_method", "wrong_getter_build", "no_receipt",
                                   "duplicate_receipt", "receipt_build", "mismatched_interface",
                                   "unsupported_build", "unreadable_value"])
def test_unbound_or_unreadable_observation_stays_incomplete(synthetic_projection, fault):
    reader, projection = synthetic_projection
    row = reader.coverage[0]
    receipt = reader.receiver_receipts[0]
    if fault == "wrong_field":
        row["field"] = "another_observed_field"
    elif fault == "wrong_method":
        row["getter"]["method"] = "unknown"
    elif fault == "wrong_getter_build":
        row["getter"]["runtime_version"] = "6.3.0.1"
    elif fault == "no_receipt":
        reader.receiver_receipts.clear()
    elif fault == "duplicate_receipt":
        reader.receiver_receipts.append(copy.deepcopy(receipt))
    elif fault == "receipt_build":
        receipt["descriptor"]["runtime_version"] = "6.3.0.1"
    elif fault == "mismatched_interface":
        receipt["descriptor"]["interfaces"] = ["com.comsol.model.PropFeature"]
    elif fault == "unsupported_build":
        reader.runtime_version = projection["runtime_version"] = "6.5.0.1"
        row["getter"]["runtime_version"] = "6.5.0.1"
        receipt["descriptor"]["runtime_version"] = "6.5.0.1"
    else:
        reader.coverage.append({"side": "left", "path": row["path"], "field": "metric:missing",
                                "status": "UNREADABLE", "getter": None, "reason": "synthetic"})
    assert finalize(reader, projection)["complete"] is False


def test_updated_reader_build_is_retained_after_metric_phase(synthetic_projection):
    reader, projection = synthetic_projection
    # An initially empty paths projection may learn its runtime while resolving
    # a metric. Recalculation must use the actual current reader observation.
    projection["runtime_version"] = None
    result = finalize(reader, projection)
    assert result["runtime_version"] == reader.runtime_version
    assert result["complete"] is True
