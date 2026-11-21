"""Narrow shape guard for the compacted-job solver reader compatibility path.

Synthetic mappings only. A cleanup marker is not project authorization or
proof that compaction occurred. The real compactor removes ``execution`` from
the compacted job view; an otherwise incomplete ordinary execution mapping
must not be promoted to a compacted claim merely because a marker is present.
This test does not use a real ledger, principal, Worker, network or model.
"""
from __future__ import annotations

import pytest

from comsol_mcp._g3_solver_ops import _job_project_binding

PROJECT = "t35-compact-shape-synthetic-project"
OPERATION = "t35-compact-shape-synthetic-operation"


def _job(metadata: dict, *, status: str = "FAILED") -> dict:
    return {
        "status": status,
        "metadata": metadata,
        "operation_id": OPERATION,
        "operation": {
            "operation_id": OPERATION,
            "metadata": {"execution": {"project_id": PROJECT}},
        },
    }


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        pytest.param(_job({"project_id": PROJECT, "cleanup": {"metadata_compacted": True}}),
                     True, id="compactor-shape-terminal"),
        pytest.param(_job({"project_id": PROJECT}), False, id="no-cleanup-marker"),
        pytest.param(_job({"project_id": PROJECT, "cleanup": {"metadata_compacted": False}}),
                     False, id="false-cleanup-marker"),
        pytest.param(_job({"project_id": PROJECT, "cleanup": {"metadata_compacted": True}},
                          status="RUNNING"), False, id="nonterminal"),
        pytest.param(_job({"project_id": PROJECT, "execution": {},
                           "cleanup": {"metadata_compacted": True}}),
                     False, id="incomplete-present-execution"),
        pytest.param(_job({"project_id": PROJECT, "execution": {"project_id": PROJECT},
                           "cleanup": {"metadata_compacted": True}}),
                     True, id="existing-complete-nested-claim"),
    ],
)
def test_compacted_job_binding_shape(job: dict, expected: bool) -> None:
    assert _job_project_binding(job, PROJECT) is expected
