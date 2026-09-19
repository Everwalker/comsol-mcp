from __future__ import annotations

from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_code import compile_result, describe_source, execution_result
from comsol_mcp._g2_docs import OfflineDocsIndex
from comsol_mcp._g2_engine import property_get, property_index_set, property_set
from comsol_mcp._g2_registry import operation_describe, registry_manifest, validate_call
from comsol_mcp._g2_transactions import TransactionStore, preview_transaction, run_transaction


class _Node:
    def __init__(self):
        self.values = {"flag": True, "empty": [], "matrix": [[1.0, 2.0]], "expr": "a+b"}
        self.calls = []

    def properties(self):
        return list(self.values)

    def getValueType(self, name):
        return {"flag": "Boolean", "empty": "DoubleArray", "matrix": "DoubleMatrix", "expr": "String"}[name]

    def getAllowedPropertyValues(self, _name):
        return None

    def getType(self):
        return "FakeFeature"

    def getBoolean(self, name):
        self.calls.append(("getBoolean", name)); return self.values[name]

    def getDoubleArray(self, name):
        self.calls.append(("getDoubleArray", name)); return self.values[name]

    def getDoubleMatrix(self, name):
        self.calls.append(("getDoubleMatrix", name)); return self.values[name]

    def getString(self, name):
        self.calls.append(("getString", name)); return self.values[name]

    def set(self, name, typed):
        self.calls.append(("set", name, typed)); self.values[name] = typed["data"]

    def setIndex(self, name, typed, index, *second):
        self.calls.append(("setIndex", name, typed, index, *second))


class _Client:
    def __init__(self, node): self.node = node
    def model(self, _tag): return self.node


class _Worker:
    def __init__(self, node): self.node = node
    def client(self): return _Client(self.node)


def test_typed_get_preserves_empty_singleton_matrix_and_uses_authoritative_getters():
    node = _Node()
    result = property_get(_Worker(node), "m", {"segments": []}, ["empty", "matrix", "expr"])
    values = {row["name"]: row["value"] for row in result["properties"]}
    assert values["empty"]["kind"] == "float64" and values["empty"]["shape"] == [0]
    assert values["matrix"]["shape"] == [1, 2]
    assert values["expr"]["kind"] == "string" and values["expr"]["data"] == "a+b"
    assert node.calls == [("getDoubleArray", "empty"), ("getDoubleMatrix", "matrix"), ("getString", "expr")]


def test_typed_set_and_index_use_java_signature_and_comsol_argument_order():
    node = _Node()
    path = {"segments": []}
    property_set(_Worker(node), "m", path, [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": False}}])
    property_index_set(_Worker(node), "m", path, "matrix", [3], {"kind": "float64", "shape": [1], "data": [4.0]})
    assert node.calls[0][0:2] == ("set", "flag")
    assert node.calls[0][2]["java_signature"] == "boolean"
    set_index_call = next(call for call in node.calls if call[0] == "setIndex")
    assert set_index_call[0:2] == ("setIndex", "matrix")
    assert set_index_call[3] == 3
    assert set_index_call[2]["java_signature"] == "double[]"


def test_unknown_metadata_and_int64_writes_fail_closed():
    class Unknown(_Node):
        def getValueType(self, _name): return "FutureComsolType"
    with pytest.raises(ExecutionContractError) as exc:
        property_set(_Worker(Unknown()), "m", {"segments": []}, [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": True}}])
    assert exc.value.code == "API_UNSUPPORTED"
    node = _Node()
    with pytest.raises(ExecutionContractError) as exc:
        property_set(_Worker(node), "m", {"segments": []}, [{"name": "flag", "value": {"kind": "int64", "shape": [], "data": 1}}])
    assert exc.value.code in {"PROPERTY_TYPE_MISMATCH", "API_UNSUPPORTED"}


def test_registry_profiles_and_compile_missing_reply_are_truthful():
    manifest = registry_manifest("expert")
    assert {row["operation_id"] for row in manifest["operations"]} >= {"code.compile_java", "code.execute_java"}
    property_schema = next(row for row in manifest["operations"] if row["operation_id"] == "node.property_get")
    assert property_schema["input_schema"]["type"] == "object"
    assert property_schema["output_schema"]["required"] == ["success", "data"]
    assert property_schema["catalog_input_schema"]["properties"]["model_ref"]["type"] == "string"
    assert property_schema["input_schema"]["properties"]["model_ref"]["type"] == "object"
    validate_call("node.property_get", {"path": {"segments": []}, "names": ["x"]}, allow_unbound_identity=True)
    with pytest.raises(ExecutionContractError) as exc:
        validate_call("node.property_get", {"path": {"segments": []}, "names": ["x"]})
    assert exc.value.code == "INVALID_REQUEST"
    with pytest.raises(ExecutionContractError) as exc:
        validate_call("node.property_get", {"path": {"segments": []}, "names": []})
    assert exc.value.code == "INVALID_REQUEST"
    description = {"source_sha256": "a" * 64, "entrypoint": "X"}
    assert compile_result(description=description, worker_reply=None)["success"] is False
    unknown = execution_result(
        description=description,
        worker_reply={"ok": False, "status": "FAILED", "failure": {"execution_state_unknown": True, "message": "runtime"}},
        before={"fingerprint": "same"}, after={"fingerprint": "same"},
    )
    assert unknown["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert unknown["data"]["execution_state_unknown"] is True
    legacy = operation_describe("set_parameters")
    assert legacy["operation_id"] == "set_parameters" and legacy["executable"] is True


def test_legacy_fallback_uses_registered_fastmcp_schema_and_rejects_unknown_fields():
    workflow = operation_describe("workflow_info")
    assert workflow["input_schema"]["properties"] == {}
    assert workflow["input_schema"]["additionalProperties"] is False
    validate_call("workflow_info", {})
    with pytest.raises(ExecutionContractError) as exc:
        validate_call("workflow_info", {"unexpected": True})
    assert exc.value.code == "INVALID_REQUEST"

    configure = operation_describe("configure_single_main_workflow")
    properties = configure["input_schema"]["properties"]
    assert set(properties) == {
        "current_main_model_path", "snapshot_dir", "snapshot_prefix",
        "model_dimension", "notes",
    }
    validate_call("configure_single_main_workflow", {"current_main_model_path": "model.mph"})
    with pytest.raises(ExecutionContractError) as exc:
        validate_call("configure_single_main_workflow", {"current_main_model_path": "model.mph", "unknown": 1})
    assert exc.value.code == "INVALID_REQUEST"
    with pytest.raises(ExecutionContractError) as exc:
        validate_call("configure_single_main_workflow", {"current_main_model_path": "model.mph", "model_dimension": "2"})
    assert exc.value.code == "INVALID_REQUEST"


def test_hidden_legacy_fallback_routes_valid_workflow_and_rejects_bad_arguments(tmp_path):
    daemon = ControlDaemon(tmp_path)
    try:
        valid = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "workflow_info", "arguments": {}},
        })
        assert valid["success"] is True and "workflow" in valid["data"]

        configured = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {
                "operation_id": "configure_single_main_workflow",
                "arguments": {"current_main_model_path": "model.mph"},
            },
        })
        assert configured["success"] is True

        unknown = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {"operation_id": "workflow_info", "arguments": {"unexpected": True}},
        })
        assert unknown["success"] is False and unknown["error"]["code"] == "INVALID_REQUEST"

        wrong_type = daemon.dispatch({
            "operation": "operation_call",
            "arguments": {
                "operation_id": "configure_single_main_workflow",
                "arguments": {"current_main_model_path": "model.mph", "model_dimension": "2"},
            },
        })
        assert wrong_type["success"] is False and wrong_type["error"]["code"] == "INVALID_REQUEST"
    finally:
        daemon.close()


def test_gateway_publication_profiles_do_not_remove_backend_fallback(monkeypatch):
    from comsol_mcp._mcp_gateway import GatewayRegistry

    class FakeMcp:
        def __init__(self): self.names = []
        def add_tool(self, routed, **_): self.names.append(routed.__name__)

    def set_parameters(): pass
    def code_compile_java(): pass
    def operation_call(): pass
    def session_health(): pass
    def registry_list(): pass

    for profile in ("full", "domain", "expert"):
        monkeypatch.setenv("COMSOL_MCP_TOOL_PROFILE", profile)
        fake = FakeMcp()
        gateway = GatewayRegistry(fake, dispatcher=lambda *_: {})
        for fn in (set_parameters, code_compile_java, operation_call, session_health, registry_list):
            gateway.add_tool(fn)
        assert {"operation_call", "session_health", "registry_list"}.issubset(fake.names)
        if profile == "full":
            assert {"set_parameters", "code_compile_java"}.issubset(fake.names)
        elif profile == "domain":
            assert "set_parameters" in fake.names and "code_compile_java" not in fake.names
        else:
            assert "set_parameters" not in fake.names and "code_compile_java" in fake.names


def test_offline_docs_are_version_separated_and_source_hashed(tmp_path):
    source = tmp_path / "COMSOL-6.4-guide.md"
    source.write_text("# PropFeature\ngetDoubleArray and setIndex", encoding="utf-8")
    index = OfflineDocsIndex(tmp_path / "index.sqlite3")
    indexed = index.index(runtime_id="COMSOL 6.4", sources=[str(source)])
    assert indexed["indexed_count"] == 1
    hit = index.search(query="getDoubleArray", version="6.4")["results"][0]
    assert hit["source_sha256"] and hit["content_sha256"] and hit["version"] == "6.4"
    assert index.search(query="getDoubleArray", version="6.3")["status"] == "UNAVAILABLE"
    missing = index.search(query="does_not_exist_in_index", version="6.4")
    assert missing["status"] == "NOT_FOUND" and missing["results"] == []


def test_preview_and_partial_transaction_keep_not_executed_rows():
    validate_call("transaction.preview", {"project_id": "p", "actions": [
        {"operation_id": "node.inspect", "arguments": {"path": {"segments": []}}}
    ]})
    preview = preview_transaction([{"operation_id": "node.property_set", "arguments": {"path": {"segments": []}, "properties": []}}])
    assert preview["static_only"] is True and preview["engine_called"] is False
    inspect_args = {"path": {"segments": []}}
    result = run_transaction([{"operation_id": "node.inspect", "arguments": inspect_args}, {"operation_id": "node.inspect", "arguments": inspect_args}, {"operation_id": "node.inspect", "arguments": inspect_args}],
                             runner=lambda _op, _args, index: {"success": index == 0})
    assert result.status == "PARTIAL" and len(result.not_executed) == 1


def test_java_describe_rejects_static_initializer(tmp_path):
    source = tmp_path / "Unsafe.java"
    source.write_text("class Unsafe { static { System.exit(1); } }", encoding="utf-8")
    with pytest.raises(ExecutionContractError) as exc:
        describe_source(tmp_path, "Unsafe.java", "Unsafe")
    assert exc.value.code == "TRUSTED_CODE_REJECTED"
