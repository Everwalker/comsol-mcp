"""Published job.status schema matches actual scoped status replies."""
import json
from jsonschema import Draft202012Validator
import pytest

from comsol_mcp._g2_registry import CATALOG_PATH, operation_describe
from tests.test_t35_bounded_status_wait_20261123 import daemon_projects, _begin_job, _call_job


def _validate_status_reply(response):
    envelope_schema = operation_describe("job.status")["output_schema"]
    assert not list(Draft202012Validator(envelope_schema).iter_errors(response))
    catalog = json.loads(CATALOG_PATH.read_text())
    data_schema = next(row["output_schema"] for row in catalog["operations"]
                       if row["operation_id"] == "job.status")
    errors = list(Draft202012Validator(data_schema).iter_errors(response["data"]))
    assert not errors, [(list(error.path), error.message) for error in errors]


@pytest.mark.parametrize("status", ["QUEUED", "STARTING", "SUCCEEDED", "EXPIRED", "LOST"])
def test_job_status_output_schema_accepts_real_status_shape(daemon_projects, status):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "schema-" + status.lower())
    if status == "STARTING":
        daemon.store.update_job(record["job_id"], status)
    elif status != "QUEUED":
        accepted, observed = daemon.store.finish(
            record["operation_id"], status=status, result={"success": status == "SUCCEEDED"}
        )
        assert accepted and observed == status
    response = _call_job(daemon, "job.status", record["job_id"], project_id)
    assert response["success"] is True
    assert response["data"]["status"] == status
    _validate_status_reply(response)


def test_job_status_output_schema_accepts_explicitly_omitted_large_result(daemon_projects):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "schema-large")
    accepted, observed = daemon.store.finish(
        record["operation_id"], status="SUCCEEDED", result={"success": True, "payload": "x" * 300_000}
    )
    assert accepted and observed == "SUCCEEDED"
    response = _call_job(daemon, "job.status", record["job_id"], project_id)
    assert response["success"] is True
    assert response["data"]["result_omitted"] is True
    _validate_status_reply(response)
