"""T20 path-shape candidate: additional deterministic fake-engine counterexamples.

The public managed route is real; the owner-isolation verifier and worker are
fakes. All files and links are disposable tmp_path fixtures, not real models.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_t20_artifact_boundary import _call, _envelope, _make_project
from tests.test_t20_generic_filename_boundary_20261108 import _property_body


def test_absolute_dotdot_must_not_hide_intermediate_symlink_from_guard(tmp_path, monkeypatch):
    daemon, project, root, _model, node, execution = _make_project(tmp_path, "t20-abs-dotdot", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
        outside = tmp_path / "synthetic-other-project"
        (outside / "dir").mkdir(parents=True)
        (outside / "secret.dat").write_text("disposable synthetic data\n")
        (root / "link").symlink_to(outside / "dir", target_is_directory=True)
        # Do not use resolve/normpath to construct the user-supplied token:
        # actual filesystem resolution follows link *before* processing '..'.
        raw = str(root / "link") + "/../secret.dat"
        resolved = Path(raw).resolve(strict=True)
        assert resolved == (outside / "secret.dat").resolve()
        assert not resolved.is_relative_to(root.resolve())
        reached = []
        original_set = node.set

        def record_target(name, typed):
            if name == "filename":
                reached.append(Path(typed["data"]).resolve(strict=True))
            return original_set(name, typed)

        monkeypatch.setattr(node, "set", record_target)
        result = _call(daemon, "node.property_set", _property_body("filename", raw),
                       _envelope(project, execution, "absolute-dotdot-symlink"))
        assert not result["success"] and reached == [], {
            "status": result.get("success"),
            "setter_reached": bool(reached),
            "fake_resolved_outside": any(not p.is_relative_to(root.resolve()) for p in reached),
        }
        assert node.values.get("filename") is None
    finally:
        daemon.close()


@pytest.mark.parametrize("value", [r"C:secret.dat", r"D:dir\secret.dat"])
def test_windows_drive_relative_shape_refused_before_generic_setter(tmp_path, monkeypatch, value):
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "t20-drive-relative", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation", lambda: {"synthetic_owned_server": True})
        result = _call(daemon, "node.property_set", _property_body("filename", value),
                       _envelope(project, execution, "drive-relative"))
        assert not result["success"] and node.values.get("filename") is None, {
            "status": result.get("success"),
            "fake_setter_received_value": node.values.get("filename") == value,
        }
    finally:
        daemon.close()
