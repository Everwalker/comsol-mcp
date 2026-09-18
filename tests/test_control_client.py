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
    monkeypatch.setattr(client, "_alive", lambda pid: True)
    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("replacement")))
    import pytest
    with pytest.raises(RuntimeError, match="no replacement"):
        client.ensure_control()
