"""Synthetic public-route controls for generic file-property scope (T19/T20).

The owner-isolation verifier is stubbed to model an already-owned server; the
project authority, registry admission, managed backend, write service and
property adapter are real. The fake Worker does not establish native behavior.
"""
from __future__ import annotations

from tests.test_t20_artifact_boundary import _call, _envelope, _make_project, _node_path


def _property_body(name, value):
    return {
        "path": _node_path(("func", "interpA")),
        "properties": [{"name": name, "value": {"kind": "string", "shape": [], "data": value}}],
    }


def test_owned_project_generic_non_file_string_remains_usable(tmp_path, monkeypatch):
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "t20-generic-ok", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
        result = _call(daemon, "node.property_set", _property_body("interp", "linear"),
                       _envelope(project, execution, "generic-string-control"))
        assert result["success"], result
        assert node.values["interp"] == "linear"
    finally:
        daemon.close()


def test_owned_project_generic_external_file_property_is_refused_before_set(tmp_path, monkeypatch):
    daemon, project, root, _model, node, execution = _make_project(tmp_path, "t20-generic-outside", monkeypatch)
    try:
        isolation_calls = []
        def assumed_owned_server():
            isolation_calls.append(True)
            return {"synthetic_owned_server": True}
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", assumed_owned_server)
        outside = tmp_path / "synthetic-other-project" / "input.dat"
        outside.parent.mkdir()
        outside.write_text("disposable external test input\n")
        assert not outside.is_relative_to(root)
        result = _call(daemon, "node.property_set", _property_body("filename", str(outside)),
                       _envelope(project, execution, "generic-external-path"))
        assert isolation_calls == [True]
        assert not result["success"] and node.values.get("filename") is None, {
            "public_result_success": result["success"],
            "fake_worker_received_external_path": node.values.get("filename") == str(outside),
            "isolation_gate_calls": len(isolation_calls),
        }
    finally:
        daemon.close()
