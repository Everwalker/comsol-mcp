from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._g2_code import compile_result, describe_source, execution_result
from comsol_mcp._g2_docs import OfflineDocsIndex
from comsol_mcp._g2_engine import property_get, property_index_set, property_set
from comsol_mcp._g2_contract import typed_value_from_engine
from comsol_mcp._g2_registry import operation_describe, registry_manifest, validate_call
from comsol_mcp._g2_transactions import TransactionStore, preview_transaction, run_transaction
from comsol_mcp._managed_backend import ManagedBackend


class _Node:
    def __init__(self):
        self.values = {"flag": True, "empty": [], "matrix": [[1.0, 2.0]], "expr": "a+b"}
        self.calls = []
        self.properties_calls = 0

    def properties(self):
        self.properties_calls += 1
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
    assert node.properties_calls == 0


def test_typed_get_bounds_metadata_to_requested_names_and_preserves_unknown_rejection():
    node = _Node()
    result = property_get(_Worker(node), "m", {"segments": []}, ["flag", "flag"])
    assert [row["name"] for row in result["properties"]] == ["flag", "flag"]
    assert node.properties_calls == 0
    assert node.calls == [("getBoolean", "flag"), ("getBoolean", "flag")]

    class Unknown(_Node):
        def getValueType(self, _name):
            return "FutureComsolType"

    unknown = Unknown()
    with pytest.raises(ExecutionContractError) as exc:
        property_get(_Worker(unknown), "m", {"segments": []}, ["flag"])
    assert exc.value.code == "API_UNSUPPORTED"
    assert unknown.properties_calls == 0


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (float("nan"), "float64"),
        ([1.0, float("inf")], "float64"),
        ([[0.0], [float("-inf")]], "float64"),
        ({"real": float("nan"), "imag": 0.0}, "complex128"),
    ],
)
def test_nonfinite_engine_readbacks_fail_closed_before_json_serialization(value, kind):
    with pytest.raises(ExecutionContractError) as exc:
        typed_value_from_engine(value, kind=kind)
    assert exc.value.code == "API_UNSUPPORTED"
    assert "non-finite" in str(exc.value)


def test_nonfinite_property_get_has_no_write_and_serializable_error():
    class NonFinite(_Node):
        def __init__(self):
            super().__init__()
            self.values["invalid"] = [float("nan")]

        def getValueType(self, name):
            if name == "invalid":
                return "DoubleArray"
            return super().getValueType(name)

        def getDoubleArray(self, name):
            self.calls.append(("getDoubleArray", name))
            return self.values[name]

    node = NonFinite()
    with pytest.raises(ExecutionContractError) as exc:
        property_get(_Worker(node), "m", {"segments": []}, ["invalid"])
    assert exc.value.code == "API_UNSUPPORTED"
    assert not any(call[0] == "set" for call in node.calls)
    # The structured error produced before the wire boundary contains no
    # non-finite value and is therefore safe for the production serializer.
    import json
    json.dumps({"success": False, "data": {}, "error": {"code": exc.value.code, "message": str(exc.value)}}, allow_nan=False)


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


@pytest.mark.parametrize("value", [False, True])
def test_boolean_on_off_metadata_accepts_typed_json_boolean_without_set_string_conversion(value):
    class BooleanEnum(_Node):
        def getAllowedPropertyValues(self, name):
            return ["on", "off"] if name == "flag" else None

    node = BooleanEnum()
    result = property_set(
        _Worker(node), "m", {"segments": []},
        [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": value}}],
    )
    assert result["success"] is True
    assert result["data"]["applied"][0]["readback"]["data"] is value
    assert [call[0:2] for call in node.calls] == [("set", "flag"), ("getBoolean", "flag")]


@pytest.mark.parametrize(("allowed", "value"), [(["on"], True), (["off"], False)])
def test_boolean_single_allowed_on_off_value_accepts_only_its_corresponding_bool(allowed, value):
    class BooleanEnum(_Node):
        def getAllowedPropertyValues(self, name):
            return list(allowed) if name == "flag" else None

    node = BooleanEnum()
    result = property_set(
        _Worker(node), "m", {"segments": []},
        [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": value}}],
    )
    assert result["success"] is True
    assert node.calls[0][0:2] == ("set", "flag")


@pytest.mark.parametrize(
    ("allowed", "value", "expected_code"),
    [
        (["off"], True, "INVALID_PROPERTY_VALUE"),
        (["yes", "no"], False, "INVALID_PROPERTY_VALUE"),
        ([0, 1], False, "INVALID_PROPERTY_VALUE"),
        (["on", "off"], "on", "PROPERTY_TYPE_MISMATCH"),
        (["on", "off"], 1, "PROPERTY_TYPE_MISMATCH"),
    ],
)
def test_boolean_allowed_values_remain_fail_closed_before_set(allowed, value, expected_code):
    class BooleanEnum(_Node):
        def getAllowedPropertyValues(self, name):
            return list(allowed) if name == "flag" else None

    node = BooleanEnum()
    with pytest.raises(ExecutionContractError) as exc:
        property_set(
            _Worker(node), "m", {"segments": []},
            [{"name": "flag", "value": {"kind": "boolean", "shape": [], "data": value}}],
        )
    assert exc.value.code == expected_code
    assert not any(call[0] == "set" for call in node.calls)


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


def test_java_description_hashes_bounded_original_utf8_bytes_without_newline_translation(tmp_path):
    cases = (
        ("crlf", b"public class NewTask {\r\n  // task code\r\n}\r\n"),
        ("lf", b"public class NewTask {\n  // task code\n}\n"),
        ("mixed", "public class NewTask {\r\n  // 温度 Ω\n}\r".encode("utf-8")),
    )
    for name, raw in cases:
        source = tmp_path / f"{name}.java"
        source.write_bytes(raw)
        described = describe_source(tmp_path, source.name, "NewTask")
        assert described["bytes"] == len(raw)
        assert described["source_sha256"] == hashlib.sha256(raw).hexdigest()
        normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if name in {"crlf", "mixed"}:
            assert described["source_sha256"] != hashlib.sha256(normalized).hexdigest()
    assert cases[0][1] and len(cases[0][1]) == 43
    assert hashlib.sha256(cases[0][1]).hexdigest() == "2bc2059d87cefd465f5f01125637b0cb93671e896c4e019cb7492bdb3dd8481b"


def test_source_snapshot_byte_limit_and_invalid_utf8(tmp_path):
    from comsol_mcp._g2_code import MAX_JAVA_SOURCE_BYTES, read_source_snapshot

    source = tmp_path / "Bounded.java"
    raw = b"x" * MAX_JAVA_SOURCE_BYTES
    source.write_bytes(raw)
    snapshot = read_source_snapshot(tmp_path, source.name)
    assert snapshot.raw_bytes == raw
    assert snapshot.sha256 == hashlib.sha256(raw).hexdigest()
    source.write_bytes(raw + b"x")
    with pytest.raises(ExecutionContractError) as oversized:
        read_source_snapshot(tmp_path, source.name)
    assert oversized.value.code == "INVALID_REQUEST"
    source.write_bytes(b"\xff")
    with pytest.raises(ExecutionContractError) as invalid:
        read_source_snapshot(tmp_path, source.name)
    assert invalid.value.code == "INVALID_REQUEST"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO behavior; Windows native behavior is NOT_RUN")
def test_source_snapshot_rejects_fifo_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "Blocked.java")
    program = '''\nimport sys\nfrom comsol_mcp._g2_code import read_source_snapshot\nfrom comsol_mcp._execution_contract import ExecutionContractError\ntry:\n    read_source_snapshot(sys.argv[1], "Blocked.java")\nexcept ExecutionContractError as exc:\n    print(exc.code)\nelse:\n    raise SystemExit("FIFO was accepted")\n'''
    # subprocess.run kills and reaps only this test-owned child on timeout.
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ARTIFACT_MISSING"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory identity fixture; Windows native behavior is NOT_RUN")
def test_source_snapshot_retains_bound_root_during_replacement_and_restore(tmp_path):
    from comsol_mcp._g2_code import read_source_snapshot
    from comsol_mcp._project_root import open_project_root_binding

    project = tmp_path / "project"
    project.mkdir()
    original = b"public class Original {}\r\n"
    (project / "Original.java").write_bytes(original)
    binding = open_project_root_binding(project)
    moved = tmp_path / "original-root"
    try:
        project.rename(moved)
        project.mkdir()
        (project / "Original.java").write_bytes(b"public class Replaced {}")
        assert read_source_snapshot(binding, "Original.java").raw_bytes == original
        (project / "Original.java").unlink()
        project.rmdir()
        moved.rename(project)
        assert read_source_snapshot(binding, "Original.java").raw_bytes == original
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd/no-follow race fixture; Windows native behavior is NOT_RUN")
@pytest.mark.parametrize("replace_final", [False, True])
def test_source_snapshot_rejects_symlink_replacement_after_path_selection(tmp_path, monkeypatch, replace_final):
    import comsol_mcp._g2_code as code
    from comsol_mcp._project_root import open_project_root_binding

    project = tmp_path / "project"
    source_dir = project / "sub"
    source_dir.mkdir(parents=True)
    source = source_dir / "NewTask.java"
    source.write_bytes(b"public class NewTask {}\r\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_source = outside / "NewTask.java"
    marker = b"OUTSIDE_PROJECT_SOURCE_SECRET"
    outside_source.write_bytes(b"public class OutsideProject { String marker = \"" + marker + b"\"; }")
    binding = open_project_root_binding(project)
    original_select = code._relative_source_path

    def replace_after_selection(root, requested):
        selected = original_select(root, requested)
        if replace_final:
            source.unlink()
            source.symlink_to(outside_source)
        else:
            source_dir.rename(project / "sub-original")
            source_dir.symlink_to(outside, target_is_directory=True)
        return selected

    monkeypatch.setattr(code, "_relative_source_path", replace_after_selection)
    try:
        with pytest.raises(ExecutionContractError) as raised:
            code.read_source_snapshot(binding, "sub/NewTask.java")
        assert raised.value.code in {"ACCESS_VIOLATION", "ARTIFACT_MISSING", "PERMISSION_DENIED"}
        assert marker.decode() not in str(raised.value)
    finally:
        binding.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd/no-follow root binding fixture; Windows native behavior is NOT_RUN")
def test_dispatch_project_root_binding_rejects_replaced_workspace_root(tmp_path):
    from comsol_mcp._project_root import open_project_root_binding

    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "project"
    project.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "NewTask.java").write_text("public class OutsideProject {}", encoding="utf-8")
    authorized = open_project_root_binding(workspace_root)
    identity = authorized.identity
    authorized.close()
    moved = tmp_path / "workspaces-original"
    workspace_root.rename(moved)
    workspace_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ExecutionContractError):
        open_project_root_binding(project, authorized_root=workspace_root,
                                  expected_authorized_root_identity=identity)


def test_java_result_helpers_reject_unbound_and_incomplete_worker_success():
    description = {"source_sha256": "a" * 64, "entrypoint": "Recipe"}
    bad_compile = compile_result(description=description, worker_reply={
        "ok": True, "status": "SUCCEEDED",
        "result": {"compiled": True, "source_sha256": "b" * 64, "entrypoint": "Recipe"},
    })
    assert bad_compile["success"] is False
    assert bad_compile["error"]["worker_failure_code"] == "WORKER_SOURCE_BINDING_MISMATCH"
    missing_compile_identity = compile_result(description=description, worker_reply={
        "ok": True, "status": "SUCCEEDED", "result": {"compiled": True, "entrypoint": "Recipe"},
    })
    assert missing_compile_identity["success"] is False

    for result in (
        {"executed": True, "source_sha256": "b" * 64, "entrypoint": "Recipe", "model_tag": "m"},
        {"executed": True, "source_sha256": "a" * 64, "entrypoint": "Other", "model_tag": "m"},
        {"executed": True, "source_sha256": "a" * 64, "entrypoint": "Recipe"},
    ):
        execution = execution_result(
            description=description, expected_model_tag="m",
            worker_reply={"ok": True, "status": "SUCCEEDED", "result": result},
            before={"fingerprint": "same"}, after={"fingerprint": "same"},
        )
        assert execution["success"] is False
        assert execution["execution_success"] is False
        assert execution["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert execution["error"]["safe_retry"] is False
        assert execution["data"]["partial_change"] is True


def test_managed_execute_mismatch_is_unknown_without_post_action_worker_reads(tmp_path):
    source = tmp_path / "Recipe.java"
    source.write_bytes(b"public class Recipe { public static Object run() { return 42; } }\r\n")

    class Worker:
        def __init__(self):
            self.calls = []

        def backend_snapshot(self, tag):
            self.calls.append(("snapshot", tag))
            return {"model_tag": tag, "fingerprint": "same"}

        def execute_java(self, tag, path, entrypoint, arguments, **kwargs):
            self.calls.append(("execute", tag))
            assert kwargs["expected_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
            assert kwargs["source_bytes"] == source.read_bytes()
            return {"ok": True, "status": "SUCCEEDED", "result": {
                "executed": True, "model_tag": tag, "source_sha256": "b" * 64,
                "entrypoint": entrypoint, "readback": 42,
            }}

    worker = Worker()
    backend = object.__new__(ManagedBackend)
    backend.worker = worker
    backend._base_project_root = tmp_path
    backend._project_root_context = ContextVar("managed_test_project_root", default=None)
    backend._project_root_binding_context = ContextVar("managed_test_project_binding", default=None)
    result = backend._run_g2_action("code.execute_java", "m", {
        "source_artifact": source.name, "entrypoint": "Recipe", "arguments": {}, "mode": "trusted",
    }, "mismatch-request")

    assert result["success"] is False
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["engine_state"] == "UNKNOWN"
    assert result["error"]["safe_retry"] is False
    assert result["data"]["execution_state_unknown"] is True
    assert result["data"]["partial_change"] is True
    assert worker.calls == [("snapshot", "m"), ("execute", "m")]


@pytest.mark.parametrize("failure_kind", ["pre_dispatch", "compile_error"])
def test_managed_execute_preserves_proven_pre_model_failures_without_after_snapshot(tmp_path, failure_kind):
    source = tmp_path / "Recipe.java"
    source.write_text("public class Recipe {}\n", encoding="utf-8")

    class WorkerFailure(Exception):
        def __init__(self, reply):
            super().__init__("synthetic Worker rejection")
            self.reply = reply

    class Worker:
        def __init__(self):
            self.calls = []

        def backend_snapshot(self, tag):
            self.calls.append(("snapshot", tag))
            return {"model_tag": tag, "fingerprint": "same"}

        def execute_java(self, tag, path, entrypoint, arguments, **kwargs):
            self.calls.append(("execute", tag))
            if failure_kind == "pre_dispatch":
                raise WorkerFailure({"ok": False, "status": "FAILED", "code": "WORKER_SOURCE_BINDING_UNAVAILABLE",
                    "source_operation_dispatched": False, "execution_state_unknown": False,
                    "failure": {"code": "WORKER_SOURCE_BINDING_UNAVAILABLE",
                                "source_operation_dispatched": False, "execution_state_unknown": False}})
            return {"ok": False, "status": "FAILED", "failure": {
                "code": "COMPILE_ERROR", "message": "syntax error", "diagnostics": [],
                "execution_state_unknown": False,
            }}

    worker = Worker()
    backend = object.__new__(ManagedBackend)
    backend.worker = worker
    backend._base_project_root = tmp_path
    backend._project_root_context = ContextVar("managed_test_project_root", default=None)
    backend._project_root_binding_context = ContextVar("managed_test_project_binding", default=None)
    result = backend._run_g2_action("code.execute_java", "m", {
        "source_artifact": source.name, "entrypoint": "Recipe", "arguments": {}, "mode": "trusted",
    }, f"known-{failure_kind}")

    assert result["success"] is False
    assert result["data"]["execution_state_unknown"] is False
    assert result["error"]["safe_retry"] is (failure_kind == "compile_error")
    assert result["error"]["code"] == ("COMPILE_ERROR" if failure_kind == "compile_error"
                                         else "WORKER_SOURCE_BINDING_UNAVAILABLE")
    assert worker.calls == [("snapshot", "m"), ("execute", "m")]
