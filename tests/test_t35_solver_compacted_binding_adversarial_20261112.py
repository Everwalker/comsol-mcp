"""T35 narrow solver binding cases around a compacted job projection.

Pure synthetic mappings only: no store, model, process, Worker, principal,
network or existing job. These cases supplement the separate in-memory
compaction integration test and cannot prove authorization, concurrency or
persistence. The compacted positive is proposed and was initially RED against
the prior nested-only solver reader; the other cases guard project claims and
operation identity against being loosened when that reader is repaired.
"""
from __future__ import annotations

import pytest

from comsol_mcp._g3_solver_ops import _job_project_binding

PROJECT_A = "t35-binding-synthetic-project-A"
PROJECT_B = "t35-binding-synthetic-project-B"
OPERATION = "t35-binding-synthetic-operation"


def _job(job_metadata: object, operation_metadata: object, *, linked_id: object = OPERATION) -> dict:
    return {
        "status": "FAILED",
        "metadata": job_metadata,
        "operation_id": OPERATION,
        "operation": {"operation_id": linked_id, "metadata": operation_metadata},
    }


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        pytest.param(
            _job({"execution": {"project_id": PROJECT_A}},
                 {"execution": {"project_id": PROJECT_A}}),
            True, id="nested-control",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "cleanup": {"metadata_compacted": True}},
                 {"execution": {"project_id": PROJECT_A}}),
            True, id="compacted-flat-job-nested-operation",
        ),
        pytest.param(
            _job({"project_id": PROJECT_B, "cleanup": {"metadata_compacted": True}},
                 {"execution": {"project_id": PROJECT_A}}),
            False, id="compacted-job-flat-conflict",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "cleanup": {"metadata_compacted": True}},
                 {"execution": {"project_id": PROJECT_B}}),
            False, id="compacted-operation-nested-conflict",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "execution": None,
                  "cleanup": {"metadata_compacted": True}},
                 {"execution": {"project_id": PROJECT_A}}),
            False, id="compacted-job-malformed-execution",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "cleanup": {"metadata_compacted": True}},
                 {"project_id": PROJECT_A, "execution": None}),
            False, id="operation-malformed-execution",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "cleanup": {"metadata_compacted": True}}, {}),
            False, id="operation-no-independent-claim",
        ),
        pytest.param(
            _job({}, {"execution": {"project_id": PROJECT_A}}),
            False, id="job-no-independent-claim",
        ),
        pytest.param(
            _job({"project_id": PROJECT_A, "cleanup": {"metadata_compacted": True}},
                 {"execution": {"project_id": PROJECT_A}}, linked_id="different-operation"),
            False, id="linked-operation-id-conflict",
        ),
        pytest.param(
            _job({"project_id": PROJECT_B, "execution": {"project_id": PROJECT_A}},
                 {"execution": {"project_id": PROJECT_A}}),
            False, id="job-flat-nested-conflict",
        ),
        pytest.param(
            _job({"execution": {"project_id": PROJECT_A}},
                 {"project_id": PROJECT_B, "execution": {"project_id": PROJECT_A}}),
            False, id="operation-flat-nested-conflict",
        ),
    ],
)
def test_solver_job_project_binding_compacted_and_adversarial(job: dict, expected: bool) -> None:
    assert _job_project_binding(job, PROJECT_A) is expected
