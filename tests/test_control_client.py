from comsol_mcp import _control_client as client


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


def test_windows_breakaway_denial_fails_closed_without_transport_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "control_home", lambda: tmp_path)
    monkeypatch.setattr(client, "_is_windows", lambda: True)
    monkeypatch.setattr(client, "_read_endpoint", lambda home: None)
    calls = []

    def denied(*args, **kwargs):
        calls.append((args, kwargs))
        raise PermissionError("breakaway denied")

    monkeypatch.setattr(client.subprocess, "Popen", denied)
    import pytest

    with pytest.raises(client.ControlStartupError, match="outside the MCP SDK Job"):
        client.ensure_control()
    assert len(calls) == 1
    assert "creationflags" in calls[0][1]
    assert "start_new_session" not in calls[0][1]


def test_dispatch_preserves_unknown_code_but_explains_windows_prerequisite(monkeypatch):
    def denied():
        raise client.ControlStartupError(
            "Windows denied CREATE_BREAKAWAY_FROM_JOB; start the daemon outside the MCP SDK Job",
            action="python -m comsol_mcp._control_daemon --home PATH",
        )

    monkeypatch.setattr(client, "ensure_control", denied)
    result = client.dispatch("server_connect", {}, {"request_id": "r", "idempotency_key": "k"})
    assert result["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
    assert result["error"]["type"] == "ControlStartupError"
    assert "outside the MCP SDK Job" in result["error"]["message"]
    assert result["error"]["action"].startswith("python -m")
