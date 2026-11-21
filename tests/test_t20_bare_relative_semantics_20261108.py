"""T20: a bare relative filename is not distinguishable by path shape.

Exercises the real public route with a fake engine whose working directory is
explicitly modeled as outside the project. The fixture does NOT establish the
working directory or behavior of any real COMSOL process.
"""
from pathlib import Path

from tests.test_t20_artifact_boundary import _call, _envelope, _make_project, _node_path


def _body(name, value):
    return {"path": _node_path(("func", "interpA")), "properties": [{
        "name": name, "value": {"kind": "string", "shape": [], "data": value}}]}


def test_plain_identifier_still_works_via_real_public_route(tmp_path, monkeypatch):
    daemon, project, _, _, node, execution = _make_project(tmp_path, "plain-f1", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
        result = _call(daemon, "node.property_set", _body("funcname", "f1"),
                       _envelope(project, execution, "plain-f1"))
        assert result["success"], result
        assert node.values["funcname"] == "f1"
    finally:
        daemon.close()


def test_unbound_engine_cwd_cannot_turn_bare_filename_into_external_authority(tmp_path, monkeypatch):
    daemon, project, root, _, node, execution = _make_project(tmp_path, "bare-f1", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
        fake_engine_cwd = tmp_path / "synthetic-other-project"
        fake_engine_cwd.mkdir()
        (fake_engine_cwd / "f1").write_text("synthetic external data\n")
        reached = []
        original_set = node.set

        def record_engine_target(name, typed):
            if name == "filename":
                target = Path(typed["data"])
                if not target.is_absolute():
                    target = fake_engine_cwd / target
                reached.append(target.resolve())
            return original_set(name, typed)

        monkeypatch.setattr(node, "set", record_engine_target)
        result = _call(daemon, "node.property_set", _body("filename", "f1"),
                       _envelope(project, execution, "bare-f1"))
        # An implementation can refuse before setter, or map an authorized
        # project file to an engine-resolved path. Success that reaches an
        # outside target under this fake resolution must not count as safe.
        assert (not reached and not result["success"]) or (reached and all(p.is_relative_to(root) for p in reached)), {
            "success": result["success"], "engine_setter_called": bool(reached),
            "fake_resolved_outside": any(not p.is_relative_to(root) for p in reached),
            "input": "f1",
        }
    finally:
        daemon.close()
