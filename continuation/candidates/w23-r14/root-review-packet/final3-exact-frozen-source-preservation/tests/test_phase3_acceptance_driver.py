"""Focused offline checks for the production Phase-3 stdio acceptance driver.

These tests deliberately do not start COMSOL or an MCP subprocess.  They pin
the driver boundary, typed-value helpers, fail-closed identity handling, and
the reviewed Java source artifacts used by the real serial acceptance run.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
DRIVER_PATH = ROOT / "tools" / "phase3_run_mcp.py"
_SPEC = importlib.util.spec_from_file_location("phase3_acceptance_driver", DRIVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
driver = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = driver
_SPEC.loader.exec_module(driver)


def test_public_evidence_redacts_docs_and_serialized_text_mirror():
    payload = {"success": True, "data": {"snippet": "licensed help excerpt", "content": "local help body"}}
    public = driver._redact({"payload": payload, "content": [{"type": "text", "text": json.dumps(payload)}]})
    assert public["payload"]["data"]["snippet"]["redacted_document_text"] is True
    assert public["payload"]["data"]["snippet"]["characters"] == 21
    assert "licensed help excerpt" not in json.dumps(public)
    assert "local help body" not in json.dumps(public)
    assert public["payload"]["success"] is True


def _args():
    return driver.build_parser().parse_args([])


def _ref():
    return {
        "session_id": "session-1",
        "server_instance_id": "server-1",
        "model_tag": "model-1",
        "generation": 1,
    }


class _FakeHost:
    def __init__(self, tools):
        self.tools = tools
        self.transcript = []
        self.calls = []

    async def call(self, name, arguments=None):
        self.calls.append((name, dict(arguments or {})))
        return {
            "success": True,
            "data": {"ok": True},
            "execution": {"model_ref": _ref(), "revision": 4, "operation_id": "op-1"},
            "_outer_isError": False,
        }


def test_driver_is_production_stdio_only_and_has_no_direct_comsol_bypass():
    source = DRIVER_PATH.read_text(encoding="utf-8")
    assert "stdio_client" in source
    assert "ClientSession" in source
    assert '"-m", "comsol_mcp.mcp_server"' in source
    for forbidden in (
        "ModelUtil.connect",
        "com.comsol.model.ModelUtil",
        "_control_client",
        "_java_worker",
        "_managed_backend",
        "subprocess.Popen",
    ):
        assert forbidden not in source


def test_driver_defaults_to_explicit_live_fixture_and_records_scope():
    args = _args()
    assert args.live is False
    assert args.fixture_model_name
    assert args.fixture_component == "phase3comp"
    assert args.fixture_geometry == "phase3geom"
    plan = driver._request_plan(args)
    assert plan["stdio_only"] is True
    assert plan["starts_or_stops_comsol"] is False
    assert plan["attaches_existing_endpoint_only_when_live"] is False
    assert plan["production_entrypoint"][0] == args.python
    assert "tools/java/Phase3Fixture.java" in plan["source_artifacts"]


def test_case_status_precedence_is_fail_then_blocked_then_not_run():
    case = driver.Case("case", "W", ("T",))
    case.subcase("a", "NOT_RUN")
    case.subcase("b", "BLOCKED")
    assert case.finish() == "BLOCKED"
    case.subcase("c", "FAIL")
    assert case.finish() == "FAIL"

    clean = driver.Case("clean", "W", ("T",))
    clean.subcase("a", "PASS")
    assert clean.finish() == "PASS"


def test_model_identity_and_revision_parsing_fail_closed():
    ref = _ref()
    assert driver._same_ref(ref, dict(ref))
    assert not driver._same_ref(ref, {key: value for key, value in ref.items() if key != "generation"})
    parsed_ref, parsed_revision = driver._payload_execution({"execution": {"model_ref": ref, "revision": 3}})
    assert parsed_ref == ref and parsed_revision == 3
    assert driver._payload_execution({"execution": {"model_ref": ref, "revision": True}}) == (ref, None)


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "boolean", "shape": [], "data": True},
        {"kind": "int32", "shape": [], "data": 2},
        {"kind": "float64", "shape": [], "data": 1.5},
        {"kind": "string", "shape": [1], "data": ["1"]},
        {"kind": "float64", "shape": [2, 2], "data": [[0.0, 1.0], [2.0, 3.0]]},
    ],
)
def test_different_typed_value_changes_data_without_changing_kind_or_shape(value):
    changed = driver._different_typed_value(value)
    assert changed is not None
    assert changed["kind"] == value["kind"]
    assert changed["shape"] == value["shape"]
    assert changed["data"] != value["data"]


def test_different_typed_value_does_not_invent_empty_property_data():
    value = {"kind": "string", "shape": [0], "data": []}
    assert driver._different_typed_value(value) is None


def test_property_rows_use_actual_inspect_values_and_get_properties():
    inspect = {
        "success": True,
        "data": {
            "values": [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": True}}],
            "properties": [{"name": "flag", "type": "boolean"}],
        },
    }
    get = {"success": True, "data": {"properties": [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": False}}]}}
    assert driver._property_value_rows(inspect)[0]["value"]["data"] is True
    assert driver._property_value_rows(get)[0]["value"]["data"] is False


def test_worker_compile_diagnostics_are_flattened_but_must_keep_line_data():
    rows = driver._diagnostic_rows({"artifact": "classes", "diagnostics": {"diagnostics": [{"line": 10, "message": "illegal start"}]}})
    assert rows == [{"line": 10, "message": "illegal start"}]
    assert driver._diagnostic_rows({"diagnostics": []}) == []


def test_execution_readback_extracts_the_reviewed_nested_worker_result_only():
    payload = {
        "success": True,
        "data": {
            "source_sha256": "a" * 64,
            "entrypoint": "Phase3NoWrapper#run",
            "readback": {
                "executed": True,
                "model_tag": "mcp4",
                "readback": {
                    "before_comments": "",
                    "after_comments": "phase3-marker",
                    "marker": "phase3-marker",
                    "parameter_count": 3,
                },
            },
        },
    }
    assert driver._execution_readback(payload) == {
        "before_comments": "",
        "after_comments": "phase3-marker",
        "marker": "phase3-marker",
        "parameter_count": 3,
    }
    assert driver._execution_readback({"success": True, "data": {"readback": {"executed": True}}}) == {"executed": True}


def test_trial_scope_property_map_selects_matching_nested_path():
    path = {"segments": [{"collection": "geom", "tag": "g1"}, {"collection": "feature", "tag": "wp3"}]}
    sibling = {"segments": [{"collection": "geom", "tag": "g1"}, {"collection": "feature", "tag": "other"}]}
    rows = [
        {"operation": "node.property_set", "path": sibling, "properties": [
            {"name": "size", "value": {"kind": "float64", "shape": [2], "data": [9.0, 9.0]}}
        ]},
        {"operation": "node.property_set", "path": path, "properties": [
            {"name": "size", "value": {"kind": "float64", "shape": [2], "data": [1.0, 1.0]}},
            {"name": "planetype", "value": {"kind": "string", "shape": [], "data": "quick"}},
        ]},
    ]
    assert driver._property_value_map(rows, requested_path=path) == {
        "size": {"kind": "float64", "shape": [2], "data": [1.0, 1.0]},
        "planetype": {"kind": "string", "shape": [], "data": "quick"},
    }


def test_reconciliation_requires_unknown_quiescent_terminal_rows_and_no_replay():
    base = {
        "success": True,
        "data": {
            "job_id": "job-1",
            "status": "UNKNOWN",
            "metadata": {
                "reconciled_quiescent": True,
                "replay_performed": False,
                "reconciliation": [{"request_id": "req-1", "status": "FAILED"}],
            },
        },
    }
    assert driver._reconciliation_quiescent(base)
    details = driver._reconciliation_details(base)
    assert details["status"] == "UNKNOWN"
    assert details["replay_performed"] is False
    assert details["rows"][0]["status"] == "FAILED"
    not_quiescent = json.loads(json.dumps(base))
    not_quiescent["data"]["metadata"]["reconciled_quiescent"] = False
    assert not driver._reconciliation_quiescent(not_quiescent)
    running = json.loads(json.dumps(base))
    running["data"]["metadata"]["reconciliation"][0]["status"] = "RUNNING"
    assert not driver._reconciliation_quiescent(running)
    replayed = json.loads(json.dumps(base))
    replayed["data"]["metadata"]["replay_performed"] = True
    assert not driver._reconciliation_quiescent(replayed)


def test_action_client_uses_operation_call_fallback_with_bound_execution_identity():
    args = _args()
    args.project_id = "phase3-test"
    host = _FakeHost({"operation_call": {"inputSchema": {"type": "object"}}})
    state = {"ref": _ref(), "revision": 4}
    client = driver.ActionClient(host, args, state)
    result = asyncio.run(client.action("node.property_get", {"path": {"segments": []}, "names": []}, key="test-key"))
    assert result["success"] is True
    assert len(host.calls) == 1
    name, body = host.calls[0]
    assert name == "operation_call"
    assert body["operation_id"] == "node.property_get"
    assert body["arguments"]["project_id"] == "phase3-test"
    assert body["arguments"]["model_ref"] == _ref()
    assert body["arguments"]["expected_revision"] == 4
    assert body["execution"]["model_ref"] == _ref()
    assert body["execution"]["expected_revision"] == 4
    assert state["revision"] == 4


def test_action_client_prefers_published_alias_over_registry_fallback():
    args = _args()
    host = _FakeHost({"node_property_get": {"inputSchema": {"type": "object"}}, "operation_call": {}})
    client = driver.ActionClient(host, args, {"ref": _ref(), "revision": 1})
    asyncio.run(client.action("node.property_get", {"path": {"segments": []}, "names": []}))
    assert host.calls[0][0] == "node_property_get"


def test_action_client_stops_bound_cascade_after_unknown_but_keeps_offline_calls():
    class UnknownHost(_FakeHost):
        async def call(self, name, arguments=None):
            self.calls.append((name, dict(arguments or {})))
            if len(self.calls) == 1:
                return {
                    "success": False,
                    "data": {},
                    "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "reconcile"},
                    "_outer_isError": True,
                }
            return {"success": True, "data": {"compiled": True}, "_outer_isError": False}

    args = _args()
    host = UnknownHost({"operation_call": {}})
    client = driver.ActionClient(host, args, {"ref": _ref(), "revision": 1})
    first = asyncio.run(client.action("node.inspect", {"path": {"segments": []}}))
    assert driver._error_code(first) == "EXECUTION_STATE_UNKNOWN"
    with pytest.raises(driver.CapabilityUnavailable):
        asyncio.run(client.action("node.property_get", {"path": {"segments": []}, "names": ["x"]}))
    offline = asyncio.run(client.action("code.compile_java", {}, require_model=False))
    assert offline["success"] is True and len(host.calls) == 2


def test_action_client_allows_explicit_checkpoint_recovery_after_unknown():
    class RecoveryHost(_FakeHost):
        async def call(self, name, arguments=None):
            self.calls.append((name, dict(arguments or {})))
            if len(self.calls) == 1:
                return {
                    "success": False,
                    "data": {},
                    "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "reconcile"},
                    "_outer_isError": True,
                }
            if len(self.calls) == 2:
                return {
                    "success": True,
                    "data": {
                        "job_id": "job-1",
                        "status": "UNKNOWN",
                        "metadata": {
                            "reconciled_quiescent": True,
                            "replay_performed": False,
                            "reconciliation": [{"request_id": "req-1", "status": "FAILED"}],
                        },
                    },
                    "_outer_isError": False,
                }
            return {
                "success": True,
                "data": {"restored": True},
                "execution": {"model_ref": {**_ref(), "model_tag": "restored-1"}, "revision": 5},
                "_outer_isError": False,
            }

    args = _args()
    host = RecoveryHost({"operation_call": {}})
    client = driver.ActionClient(host, args, {"ref": _ref(), "revision": 4})
    asyncio.run(client.action("node.property_set", {"path": {"segments": []}, "properties": []}))
    with pytest.raises(driver.CapabilityUnavailable):
        asyncio.run(client.action("checkpoint.restore", {"checkpoint_id": "checkpoint-1", "authorization_ref": "test"}))
    reconciled = asyncio.run(client.action("job_reconcile", {"job_id": "job-1"}, require_model=False))
    assert driver._reconciliation_quiescent(reconciled)
    # The driver records this only after checking the durable result; the
    # original UNKNOWN marker remains until the replacement ref is verified.
    client.state["_job_reconciled"] = True
    assert "_execution_state_unknown" in client.state
    restored = asyncio.run(client.action("checkpoint.restore", {"checkpoint_id": "checkpoint-1", "authorization_ref": "test"}))
    assert restored["success"] is True
    assert "_execution_state_unknown" not in client.state
    assert [row[1]["operation_id"] for row in host.calls] == ["node.property_set", "job_reconcile", "checkpoint.restore"]


def test_action_client_does_not_restore_after_nonquiescent_reconcile():
    class NonQuiescentHost(_FakeHost):
        async def call(self, name, arguments=None):
            self.calls.append((name, dict(arguments or {})))
            if len(self.calls) == 1:
                return {
                    "success": False,
                    "data": {},
                    "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "reconcile"},
                    "_outer_isError": True,
                }
            return {
                "success": True,
                "data": {
                    "job_id": "job-1",
                    "status": "UNKNOWN",
                    "metadata": {
                        "reconciled_quiescent": False,
                        "replay_performed": False,
                        "reconciliation": [{"request_id": "req-1", "status": "RUNNING"}],
                    },
                },
                "_outer_isError": False,
            }

    args = _args()
    host = NonQuiescentHost({"operation_call": {}})
    client = driver.ActionClient(host, args, {"ref": _ref(), "revision": 1})
    asyncio.run(client.action("node.property_set", {"path": {"segments": []}, "properties": []}))
    reconciled = asyncio.run(client.action("job_reconcile", {"job_id": "job-1"}, require_model=False))
    assert not driver._reconciliation_quiescent(reconciled)
    with pytest.raises(driver.CapabilityUnavailable):
        asyncio.run(client.action("checkpoint.restore", {"checkpoint_id": "checkpoint-1", "authorization_ref": "test"}))
    assert len(host.calls) == 2


def test_environment_sets_endpoint_and_trusted_code_without_touching_process_lifecycle(tmp_path):
    args = _args()
    args.host = "127.0.0.1"
    args.port = 56388
    args.trusted_code = True
    args.private_home = tmp_path / "private"
    host = driver.ProductionHost(args, tmp_path, [])
    env = host._environment()
    assert env["COMSOL_SERVER_HOST"] == "127.0.0.1"
    assert env["COMSOL_SERVER_PORT"] == "56388"
    assert env["COMSOL_MCP_TRUSTED_CODE"] == "1"
    assert env["COMSOL_MCP_TOOL_PROFILE"] == "full"
    assert env["COMSOL_SERVER_MCP_HOME"] == str((args.private_home).resolve())


def test_profile_publication_requires_real_shortened_sets_and_fallback_tools():
    rows = {
        "full": {"tool_names": ["registry_list", "registry_describe", "registry_manifest", "registry_call", "operation_describe", "operation_call", "save_model", "workflow_info", "configure_single_main_workflow", "code_compile_java"]},
        "domain": {"tool_names": ["registry_list", "registry_describe", "registry_manifest", "registry_call", "operation_describe", "operation_call", "workflow_info"]},
        "expert": {"tool_names": ["registry_list", "registry_describe", "registry_manifest", "registry_call", "operation_describe", "operation_call", "code_compile_java"]},
    }
    summary = driver._profile_publication_summary(rows)
    assert summary["pass"] is True
    assert summary["strict_subsets"]["domain_of_full"] is True
    assert summary["strict_subsets"]["expert_of_full"] is True
    assert driver._profile_hidden_legacy_candidate(rows["expert"]["tool_names"]) == ("configure_single_main_workflow", True)
    assert driver._profile_hidden_legacy_candidate(rows["full"]["tool_names"]) == ("workflow_info", False)


def test_profile_hosts_get_distinct_run_owned_private_homes(tmp_path):
    args = _args()
    full = driver.ProductionHost(args, tmp_path, [], label="profile-full", profile="full")
    expert = driver.ProductionHost(args, tmp_path, [], label="profile-expert", profile="expert")
    assert full.private_home != expert.private_home
    assert full.private_home == driver.ROOT / ".phase1-private" / "g2-acceptance" / tmp_path.name / "profile-full" / "mcp-home"
    assert expert._environment()["COMSOL_MCP_TOOL_PROFILE"] == "expert"


def test_reviewed_java_sources_use_injected_model_and_real_public_api():
    fixture = (ROOT / "tools/java/Phase3Fixture.java").read_text(encoding="utf-8")
    no_wrapper = (ROOT / "tools/java/Phase3NoWrapper.java").read_text(encoding="utf-8")
    readback = (ROOT / "tools/java/Phase3Readback.java").read_text(encoding="utf-8")
    partial = (ROOT / "tools/java/Phase3PartialFailure.java").read_text(encoding="utf-8")
    syntax = (ROOT / "tools/java/Phase3SyntaxFailure.java").read_text(encoding="utf-8")
    assert 'geometry.create(workPlaneTag, "WorkPlane")' in fixture
    assert 'local.create("rectA", "Rectangle")' in fixture
    assert 'local.create("rectB", "Rectangle")' in fixture
    assert 'new double[][]{{0.0, 1.0}, {0.0, 1.0}}' in fixture
    assert "model.comments()" in no_wrapper and "model.comments(marker)" in no_wrapper
    assert "model.param().set(name" in no_wrapper
    assert "parameter_count" in no_wrapper
    assert "phase3_code_guard_present" in readback and "model.param().varnames()" in readback
    assert "phase3 deliberate partial failure" in partial
    assert "model.label(;" in syntax
    for source in (fixture, no_wrapper, readback, partial, syntax):
        assert "ModelUtil.connect" not in source
        assert "ModelUtil.create" not in source


def test_redaction_removes_private_doc_fixture_and_credentials():
    value = {
        "authorization": "Bearer secret",
        "source": "/tmp/comsol-mcp-phase3-doc-abcd/file.md",
        "ordinary": "safe",
    }
    redacted = driver._redact(value)
    assert redacted["authorization"] == "REDACTED"
    assert redacted["source"] == "PRIVATE_DOC_FIXTURE"
    assert redacted["ordinary"] == "safe"
