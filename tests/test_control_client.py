import asyncio
import errno
import json
from urllib.error import HTTPError, URLError

import pytest

from comsol_mcp import _control_client as client


def _write_endpoint(home):
    endpoint = {
        "pid": 1234,
        "port": 4321,
        "process_start_epoch_ms": 202,
        "token": "private-token-must-not-leak",
    }
    (home / "control.json").write_text(json.dumps(endpoint), encoding="utf-8")
    return endpoint


def _assert_not_submitted(result, request_id="r", idempotency_key="k"):
    assert result["success"] is False
    assert result["error"]["code"] == "CONTROL_REQUEST_NOT_SUBMITTED"
    assert result["error"]["safe_retry"] is False
    assert result["execution"] == {
        "request_id": request_id,
        "idempotency_key": idempotency_key,
    }
    assert result["data"] == {
        "status": "NOT_SUBMITTED",
        "operation_dispatched": False,
        "execution_state_unknown": False,
        "dispatch_stage": "pre_dispatch",
        "control_startup_outcome": "UNRESOLVED",
    }


def test_transport_lost_response_never_resubmits_and_retains_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(client, "ensure_control", lambda: {"port": 1234, "token": "secret"})
    def lost(*args):
        calls.append(args)
        raise TimeoutError("secret transport detail")
    monkeypatch.setattr(client, "_request", lost)
    result = client.dispatch("run_study", {}, {"request_id": "r", "idempotency_key": "k", "rpc_timeout_s": 0})
    assert len(calls) == 1
    assert result["data"]["status"] == "UNKNOWN"
    assert result["execution"] == {"request_id": "r", "idempotency_key": "k"}
    assert result["error"]["safe_retry"] is False
    assert "secret" not in str(result)


def test_invalid_wait_budget_rejects_before_service_start(monkeypatch):
    monkeypatch.setattr(client, "ensure_control", lambda: (_ for _ in ()).throw(AssertionError("must not start")))
    for invalid in (-1, True, None, "30", 301):
        assert client.dispatch("run_study", {}, {"rpc_timeout_s": invalid})["error"]["code"] == "INVALID_REQUEST"


def test_live_unresponsive_control_is_not_replaced(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_read_endpoint", lambda home: {"pid": 1234})
    monkeypatch.setattr(client, "_request", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(client, "_alive", lambda *args: True)
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("replacement")))
    import pytest
    with pytest.raises(RuntimeError, match="no replacement"):
        client.ensure_control()


def test_control_pid_reuse_is_not_treated_as_the_published_daemon(monkeypatch):
    monkeypatch.setattr(client, "process_identity", lambda pid: {"alive": True, "start_epoch_ms": 202})
    assert client._alive(1234, expected_start_epoch_ms=202) is True
    assert client._alive(1234, expected_start_epoch_ms=201) is False


def test_healthy_existing_windows_control_is_attached_without_spawn(monkeypatch, tmp_path):
    endpoint = {"pid": 1234, "port": 4321, "token": "secret"}
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(client, "_read_endpoint", lambda home: endpoint)
    monkeypatch.setattr(client, "_request", lambda *args: {"success": True})
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("healthy daemon must be attached")))
    assert client.ensure_control() is endpoint


def test_windows_spawn_requests_breakaway_detached_process_group(monkeypatch, tmp_path):
    endpoint = {"pid": 1234, "port": 4321, "token": "secret"}
    popen_calls = []

    class Child:
        pass

    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    reads = iter([None, endpoint])
    monkeypatch.setattr(client, "_read_endpoint", lambda home: next(reads))
    monkeypatch.setattr(client, "_request", lambda *args: {"success": True})
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)) or Child())
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    assert client.ensure_control() is endpoint
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args[0][1:] == ["-m", "comsol_mcp._control_daemon", "--home", str(tmp_path)]
    expected_flags = (
        client._WINDOWS_CREATE_BREAKAWAY_FROM_JOB
        | client._WINDOWS_DETACHED_PROCESS
        | client._WINDOWS_CREATE_NEW_PROCESS_GROUP
    )
    assert kwargs["creationflags"] == expected_flags
    assert "start_new_session" not in kwargs


def test_windows_breakaway_winerror_5_fails_closed_without_transport_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(client, "_read_endpoint", lambda home: None)
    calls = []

    def denied(*args, **kwargs):
        calls.append((args, kwargs))
        error = PermissionError("breakaway denied")
        error.winerror = 5
        raise error

    monkeypatch.setattr(client.subprocess, "Popen", denied)
    import pytest

    with pytest.raises(client.ControlStartupError, match="outside the MCP SDK Job"):
        client.ensure_control()
    assert len(calls) == 1
    expected_flags = (
        client._WINDOWS_CREATE_BREAKAWAY_FROM_JOB
        | client._WINDOWS_DETACHED_PROCESS
        | client._WINDOWS_CREATE_NEW_PROCESS_GROUP
    )
    assert calls[0][1]["creationflags"] == expected_flags
    assert "start_new_session" not in calls[0][1]


def test_dispatch_preserves_windows_prerequisite_as_not_submitted(monkeypatch, tmp_path):
    calls = []

    def denied(*args, **kwargs):
        calls.append((args, kwargs))
        error = PermissionError("breakaway denied")
        error.winerror = 5
        raise error

    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(client.subprocess, "Popen", denied)
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("no health or operation request expected"))
    result = client.dispatch("server_connect", {}, {"request_id": "r", "idempotency_key": "k"})
    _assert_not_submitted(result)
    assert result["error"]["type"] == "ControlStartupError"
    assert "outside the MCP SDK Job" in result["error"]["message"]
    assert "python -m comsol_mcp._control_daemon --home" in result["error"]["action"]
    assert len(calls) == 1


def test_startup_timeout_is_not_submitted_without_claiming_no_control_effect(monkeypatch, tmp_path):
    spawn_calls = []
    popen_calls = []
    clock = iter((0.0, 16.0))
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(client, "_read_endpoint", lambda _home: None)
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("no endpoint or operation RPC"))
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *args: spawn_calls.append(args) or object())
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)) or pytest.fail("direct Popen forbidden"))
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(client.time, "sleep", lambda *_: pytest.fail("startup deadline should elapse immediately"))

    result = client.dispatch("session_start", {}, {"request_id": "r", "idempotency_key": "k"})

    _assert_not_submitted(result)
    assert result["error"]["type"] == "ControlStartupError"
    assert "did not become ready" in result["error"]["message"]
    assert "outside the MCP SDK Job" in result["error"]["action"]
    assert len(spawn_calls) == 1
    assert popen_calls == []


@pytest.mark.parametrize(
    "health_error",
    [
        pytest.param(URLError(PermissionError(errno.EPERM, "Operation not permitted")), id="sandbox-eperm"),
        pytest.param(TimeoutError("health timeout detail"), id="health-timeout"),
    ],
)
def test_health_preflight_failure_is_not_submitted_and_does_not_spawn(
    monkeypatch, tmp_path, health_error
):
    endpoint = _write_endpoint(tmp_path)
    request_operations = []
    alive_checks = []
    spawn_calls = []
    popen_calls = []

    def request(_endpoint, payload, _timeout):
        request_operations.append(payload["operation"])
        if payload["operation"] != "session_health":
            pytest.fail("the requested operation RPC must not be entered")
        raise health_error

    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(
        client,
        "_alive",
        lambda pid, birth: alive_checks.append((pid, birth)) or True,
    )
    monkeypatch.setattr(
        client,
        "_spawn_control_daemon",
        lambda *args: spawn_calls.append(args) or pytest.fail("no replacement daemon"),
    )
    monkeypatch.setattr(
        client.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)) or pytest.fail("Popen forbidden"),
    )

    result = client.dispatch(
        "model_load",
        {"artifact_id": "must-not-dispatch"},
        {"request_id": "r", "idempotency_key": "k", "rpc_timeout_s": 0},
    )

    _assert_not_submitted(result)
    assert request_operations == ["session_health"]
    assert alive_checks == [(endpoint["pid"], endpoint["process_start_epoch_ms"])]
    assert spawn_calls == []
    assert popen_calls == []
    assert result["error"]["type"] == "RuntimeError"
    assert result["error"]["message"] == "Control preflight failed; the requested operation was not submitted."
    assert "Operation not permitted" not in str(result)
    assert "health timeout detail" not in str(result)
    assert "private-token-must-not-leak" not in str(result)


def test_control_home_preflight_failure_is_not_submitted_and_redacts_detail(monkeypatch):
    monkeypatch.setattr(
        client,
        "control_home",
        lambda: (_ for _ in ()).throw(PermissionError("secret token detail")),
    )
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("no RPC expected"))
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *args: pytest.fail("no spawn expected"))

    result = client.dispatch("model_inspect", {}, {"request_id": "r", "idempotency_key": "k"})

    _assert_not_submitted(result)
    assert result["error"]["type"] == "PermissionError"
    assert result["error"]["message"] == "Control preflight failed; the requested operation was not submitted."
    assert "secret token detail" not in str(result)


def test_malformed_control_endpoint_is_not_submitted(monkeypatch, tmp_path):
    (tmp_path / "control.json").write_text("{malformed", encoding="utf-8")
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_request", lambda *args: pytest.fail("no RPC expected"))
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *args: pytest.fail("no spawn expected"))

    result = client.dispatch("model_inspect", {}, {"request_id": "r", "idempotency_key": "k"})

    _assert_not_submitted(result)
    assert result["error"]["type"] == "JSONDecodeError"
    assert result["error"]["message"] == "Control preflight failed; the requested operation was not submitted."


@pytest.mark.parametrize(
    "failure_factory,expected_type",
    [
        pytest.param(lambda: HTTPError("http://127.0.0.1:4321/rpc", 503, "private HTTP detail", None, None), "HTTPError", id="http-error"),
        pytest.param(lambda: json.JSONDecodeError("private parse detail", "secret", 0), "JSONDecodeError", id="malformed-response"),
        pytest.param(lambda: RuntimeError("private runtime detail"), "RuntimeError", id="runtime-error"),
    ],
)
def test_operation_rpc_failures_after_successful_health_remain_unknown_once(
    monkeypatch, tmp_path, failure_factory, expected_type
):
    _write_endpoint(tmp_path)
    request_operations = []

    def request(_endpoint, payload, _timeout):
        operation = payload["operation"]
        request_operations.append(operation)
        if operation == "session_health":
            return {"success": True}
        raise failure_factory()

    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *args: pytest.fail("healthy daemon must not spawn"))

    result = client.dispatch(
        "run_study",
        {},
        {"request_id": "r", "idempotency_key": "k", "rpc_timeout_s": 0},
    )

    assert request_operations == ["session_health", "run_study"]
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["type"] == expected_type
    assert result["data"]["status"] == "UNKNOWN"
    assert result["execution"] == {"request_id": "r", "idempotency_key": "k"}
    assert result["error"]["safe_retry"] is False
    assert "private" not in str(result)
    assert "secret" not in str(result)


def test_public_mcp_adapter_preserves_pre_dispatch_envelope(monkeypatch, tmp_path):
    from comsol_mcp import _mcp_gateway
    from comsol_mcp._g2_tools import operation_call

    endpoint = _write_endpoint(tmp_path)
    request_operations = []
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)

    def request(_endpoint, payload, _timeout):
        request_operations.append(payload["operation"])
        if payload["operation"] != "session_health":
            pytest.fail("public adapter dispatched after failed health")
        raise URLError(PermissionError(errno.EPERM, "Operation not permitted"))

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(
        client,
        "_alive",
        lambda pid, birth: pid == endpoint["pid"] and birth == endpoint["process_start_epoch_ms"],
    )
    monkeypatch.setattr(client, "_spawn_control_daemon", lambda *args: pytest.fail("no replacement daemon"))
    monkeypatch.setattr(_mcp_gateway, "current_tool_profile", lambda: "full")
    monkeypatch.setattr(_mcp_gateway, "is_tool_published", lambda *args, **kwargs: True)

    class CaptureMCP:
        def __init__(self):
            self.tools = {}

        def add_tool(self, function, **options):
            self.tools[options.get("name") or function.__name__] = function

    mcp = CaptureMCP()
    _mcp_gateway.GatewayRegistry(mcp, dispatcher=client.dispatch).add_tool(operation_call)
    result = asyncio.run(
        mcp.tools["operation_call"](
            operation_id="model.load",
            arguments={"artifact_id": "must-not-dispatch"},
            execution={"request_id": "r", "idempotency_key": "k"},
        )
    )

    assert request_operations == ["session_health"]
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "CONTROL_REQUEST_NOT_SUBMITTED"
    assert result.structuredContent["execution"] == {"request_id": "r", "idempotency_key": "k"}
    assert result.structuredContent["data"]["status"] == "NOT_SUBMITTED"
    assert result.structuredContent["data"]["operation_dispatched"] is False
    assert result.structuredContent["data"]["execution_state_unknown"] is False
    assert result.structuredContent["data"]["dispatch_stage"] == "pre_dispatch"
