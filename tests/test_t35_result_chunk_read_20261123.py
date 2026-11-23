from __future__ import annotations

import base64
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator

from comsol_mcp._g2_registry import CATALOG_PATH, is_implemented
from comsol_mcp._operation_store import _dumps_canonical
from tests.test_t35_bounded_status_wait_20261123 import (
    _begin_job,
    _call_job,
    daemon_projects,
)


def _result_entry() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text())
    return next(row for row in catalog["operations"] if row["operation_id"] == "job.result.read")


def _catalog_entry(operation_id: str) -> dict:
    catalog = json.loads(CATALOG_PATH.read_text())
    return next(row for row in catalog["operations"] if row["operation_id"] == operation_id)


def test_small_job_result_keeps_object_and_solver_success_semantics(daemon_projects):
    daemon, project_id, _, _ = daemon_projects
    for status, result, expected_success in (
        ("SUCCEEDED", {"success": True, "value": 17}, True),
        ("FAILED", {"success": False, "error": "synthetic failure"}, False),
    ):
        record = _begin_job(daemon, project_id, f"small-{status.lower()}")
        accepted, observed = daemon.store.finish(
            record["operation_id"], status=status, result=result,
        )
        assert accepted and observed == status
        response = _call_job(daemon, "job.result", record["job_id"], project_id)
        assert response["success"] is expected_success
        assert response["data"]["result"] == result


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING"])
def test_empty_or_nonterminal_result_keeps_legacy_read_and_has_no_pages(daemon_projects, status):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "empty-" + status.lower())
    if status == "RUNNING":
        daemon.store.update_job(record["job_id"], "RUNNING")

    legacy = _call_job(daemon, "job.result", record["job_id"], project_id)
    assert legacy["success"] is True
    assert legacy["data"]["result"] is None

    page = _call_job(
        daemon, "job.result.read", record["job_id"], project_id,
        offset=0, limit=8,
    )
    assert page["success"] is False
    assert page["error"]["code"] == "RESULT_NOT_AVAILABLE"
    assert "chunk_base64" not in page["data"]


def test_nonterminal_large_result_is_not_readable_until_terminal(daemon_projects, monkeypatch):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "running-large-result")
    daemon.store.update_job(
        record["job_id"], "RUNNING",
        result={"success": True, "payload": "x" * (300 * 1024)},
    )

    def unexpected_materialization(*_args, **_kwargs):
        raise AssertionError("nonterminal large result was fully materialized")

    with monkeypatch.context() as scoped:
        scoped.setattr(daemon.store, "_job", unexpected_materialization)
        scoped.setattr(daemon.store, "_op", unexpected_materialization)
        legacy = _call_job(daemon, "job.result", record["job_id"], project_id)
        assert legacy["success"] is False
        assert legacy["error"]["code"] == "RESULT_TOO_LARGE"
        assert legacy["data"]["status"] == "RUNNING"
        assert legacy["data"]["result_available_via"] == "job.result.read"

        status = _call_job(daemon, "job.status", record["job_id"], project_id)
        assert status["success"] is True
        assert status["data"]["result_available_via"] == "job.result.read"
        assert status["data"]["status"] == "RUNNING"

        page = _call_job(
            daemon, "job.result.read", record["job_id"], project_id,
            offset=0, limit=32,
        )
        assert page["success"] is False
        assert page["error"]["code"] == "RESULT_NOT_READY"
        assert "chunk_base64" not in page["data"]


def test_failed_large_utf8_result_reassembles_across_split_codepoint(daemon_projects, monkeypatch):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "failed-large-utf8")
    prefix = '{"payload":"'
    emoji = "🧪".encode("utf-8")
    emoji_start = 65535
    payload = "a" * (emoji_start - len(prefix.encode("utf-8"))) + "🧪" + "z" * (300 * 1024)
    result = {"success": False, "payload": payload}
    accepted, observed = daemon.store.finish(
        record["operation_id"], status="FAILED", result=result,
    )
    assert accepted and observed == "FAILED"

    # Store a valid UTF-8 spelling so the byte page boundary splits a real
    # four-byte code point. Normal canonical rows are often ASCII escaped.
    raw_text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = raw_text.encode("utf-8")
    assert raw.find(emoji) == emoji_start
    daemon.store.db.execute(
        "UPDATE operations SET result=? WHERE operation_id=?",
        (raw_text, record["operation_id"]),
    )
    total_bytes = len(raw)

    def unexpected_materialization(*_args, **_kwargs):
        raise AssertionError("large result read decoded the complete stored JSON")

    with monkeypatch.context() as scoped:
        scoped.setattr(daemon.store, "_job", unexpected_materialization)
        scoped.setattr(daemon.store, "_op", unexpected_materialization)
        too_large = _call_job(daemon, "job.result", record["job_id"], project_id)
        assert too_large["success"] is False
        assert too_large["error"]["code"] == "RESULT_TOO_LARGE"
        assert too_large["data"]["result_size_bytes"] == total_bytes
        assert too_large["data"]["result_available_via"] == "job.result.read"

        chunks = []
        offset = 0
        digest = None
        first_page = None
        while True:
            arguments = {"offset": offset, "limit": 65536}
            if digest is not None:
                arguments["result_sha256"] = digest
            response = _call_job(
                daemon, "job.result.read", record["job_id"], project_id, **arguments,
            )
            assert response["success"] is True, response
            data = response["data"]
            assert data["total_bytes"] == total_bytes
            if digest is None:
                digest = data["result_sha256"]
                first_page = base64.b64decode(data["chunk_base64"], validate=True)
            else:
                assert data["result_sha256"] == digest
            chunks.append(base64.b64decode(data["chunk_base64"], validate=True))
            offset = data["next_offset"]
            if data["complete"]:
                break

    assert first_page is not None
    assert first_page[-1:] == emoji[:1]
    assert chunks[1][:3] == emoji[1:]
    reconstructed = b"".join(chunks)
    assert reconstructed == raw
    assert hashlib.sha256(reconstructed).hexdigest() == digest
    assert json.loads(reconstructed.decode("utf-8")) == result
    assert json.loads(reconstructed)["success"] is False

    end_page = _call_job(
        daemon, "job.result.read", record["job_id"], project_id,
        offset=total_bytes, limit=1, result_sha256=digest,
    )
    assert end_page["success"] is True
    assert base64.b64decode(end_page["data"]["chunk_base64"], validate=True) == b""
    assert end_page["data"]["complete"] is True


def test_continuation_refuses_changed_result_version(daemon_projects):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "version-change")
    original = {"success": True, "payload": "a" * (300 * 1024)}
    accepted, observed = daemon.store.finish(
        record["operation_id"], status="SUCCEEDED", result=original,
    )
    assert accepted and observed == "SUCCEEDED"
    first = _call_job(
        daemon, "job.result.read", record["job_id"], project_id,
        offset=0, limit=128,
    )
    assert first["success"] is True
    first_digest = first["data"]["result_sha256"]

    changed = {"success": True, "payload": "b" + "a" * (300 * 1024 - 1)}
    assert len(_dumps_canonical(changed)) == len(_dumps_canonical(original))
    daemon.store.db.execute(
        "UPDATE operations SET result=? WHERE operation_id=?",
        (_dumps_canonical(changed), record["operation_id"]),
    )
    continuation = _call_job(
        daemon, "job.result.read", record["job_id"], project_id,
        offset=first["data"]["next_offset"], limit=128,
        result_sha256=first_digest,
    )
    assert continuation["success"] is False
    assert continuation["error"]["code"] == "RESULT_VERSION_CHANGED"
    assert "chunk_base64" not in continuation["data"]


def test_other_project_cannot_read_result_page_before_binding(daemon_projects, monkeypatch):
    daemon, project_a, project_b, _ = daemon_projects
    record = _begin_job(daemon, project_a, "foreign-page")
    accepted, observed = daemon.store.finish(
        record["operation_id"], status="SUCCEEDED",
        result={"success": True, "marker": "project-a-private"},
    )
    assert accepted and observed == "SUCCEEDED"
    calls = []
    real_page = daemon.store.job_result_page_bounded

    def tracked_page(*args, **kwargs):
        calls.append(args[0])
        return real_page(*args, **kwargs)

    monkeypatch.setattr(daemon.store, "job_result_page_bounded", tracked_page)
    response = _call_job(
        daemon, "job.result.read", record["job_id"], project_b,
        offset=0, limit=64,
    )
    assert response["success"] is False
    assert response["error"]["code"] == "NODE_NOT_FOUND"
    assert "project-a-private" not in json.dumps(response)
    assert calls == []


def test_public_legacy_job_result_read_alias_uses_the_scoped_page_route(daemon_projects):
    daemon, project_id, _, _ = daemon_projects
    record = _begin_job(daemon, project_id, "public-alias")
    result = {"success": True, "marker": "public-alias-result"}
    accepted, observed = daemon.store.finish(
        record["operation_id"], status="SUCCEEDED", result=result,
    )
    assert accepted and observed == "SUCCEEDED"
    response = daemon.dispatch({
        "operation": "job_result_read",
        "arguments": {"job_id": record["job_id"], "offset": 0, "limit": 128},
        "execution": {"project_id": project_id},
    })
    assert response["success"] is True
    payload = base64.b64decode(response["data"]["chunk_base64"], validate=True)
    assert json.loads(payload) == result


def test_job_result_read_registry_schemas_are_strict_and_public_tool_is_registered():
    entry = _result_entry()
    assert is_implemented("job.result.read") is True
    input_validator = Draft202012Validator(entry["input_schema"])
    output_validator = Draft202012Validator(entry["output_schema"])
    assert not list(input_validator.iter_errors({"job_id": "j", "offset": 0, "limit": 1}))
    assert list(input_validator.iter_errors({"job_id": "j", "offset": 1, "limit": 1}))
    assert list(input_validator.iter_errors({"job_id": "j", "offset": 0, "limit": 1, "extra": 1}))
    assert list(input_validator.iter_errors({"job_id": "j", "offset": 0, "limit": 65537}))
    output = {
        "job_id": "j", "offset": 0, "limit": 1, "next_offset": 1,
        "total_bytes": 1, "result_sha256": "a" * 64,
        "chunk_base64": "e30=", "complete": True,
    }
    assert not list(output_validator.iter_errors(output))
    assert list(output_validator.iter_errors({**output, "extra": True}))
    for operation_id in ("job.status", "job.wait"):
        hint_schema = _catalog_entry(operation_id)["output_schema"]
        assert hint_schema["properties"]["result_available_via"]["const"] == "job.result.read"
        assert hint_schema["properties"]["result_omitted"]["type"] == "boolean"
        assert hint_schema["properties"]["result_size_bytes"]["type"] == "integer"
    assert "RESULT_TOO_LARGE" in _catalog_entry("job.result")["notes"]

    import comsol_mcp.mcp_server
    from comsol_mcp._server import mcp

    assert "job_result_read" in mcp._tool_manager._tools
    properties = mcp._tool_manager._tools["job_result_read"].parameters["properties"]
    assert {"job_id", "offset", "limit", "result_sha256", "execution"} <= set(properties)
