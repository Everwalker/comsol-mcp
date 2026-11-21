"""Public-wire negative control for the T20 private digest capability.

Real ControlDaemon dispatch and the existing fake engine are used. These tests
are software-only and do not run a COMSOL Server or persistent Worker.
"""
import hashlib

import pytest

from comsol_mcp._g3_w13 import _RELOAD_DIGEST_PROVIDER_KEY
from tests.test_t20_artifact_boundary import _call, _envelope, _make_project, _node_path


@pytest.mark.parametrize("route", ["registry", "direct"])
def test_private_digest_key_cannot_override_provider_on_public_routes(tmp_path, monkeypatch, route):
    daemon, project, root, _model, interp, exe = _make_project(tmp_path, route, monkeypatch)
    try:
        payload = b"synthetic-project-data\n"
        source = root / "inputs" / "data.dat"
        source.write_bytes(payload)
        interp.values["filename"] = str(source)
        path = _node_path(("func", "interpA"))

        def dispatch(body, key, execution):
            envelope = _envelope(project, execution, key)
            if route == "registry":
                return _call(daemon, "function.data_reload", body, envelope)
            return daemon.dispatch({"operation": "function.data_reload", "arguments": body,
                                    "execution": envelope})

        allowed = dispatch({"path": path}, "valid", exe)
        assert allowed["success"] is True, allowed
        assert interp.refreshes == 1
        expected = hashlib.sha256(payload).hexdigest()
        assert allowed["data"]["sha256"]["status"] == "verified"
        assert allowed["data"]["sha256"]["value"] == expected

        attempt = dispatch({"path": path, _RELOAD_DIGEST_PROVIDER_KEY: "forged"},
                           "forged", allowed["execution"])
        if route == "registry":
            # The catalog schema rejects the key before engine dispatch.
            assert attempt["success"] is False, attempt
            assert (attempt.get("error") or {}).get("code") == "INVALID_REQUEST", attempt
            assert interp.refreshes == 1
            assert ((attempt.get("data") or {}).get("sha256") or {}).get("value") is None
        else:
            # The direct route accepts the envelope but replaces the supplied
            # key with the backend's own provider before the adapter sees it.
            assert attempt["success"] is True, attempt
            assert interp.refreshes == 2
            assert attempt["data"]["sha256"]["status"] == "verified"
            assert attempt["data"]["sha256"]["value"] == expected
            assert attempt["data"]["sha256"]["value"] != "forged"
    finally:
        daemon.close()
