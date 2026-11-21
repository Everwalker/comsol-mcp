"""T20: adversarial project subdirectory replacement during reload digest.

Uses the real ControlDaemon/managed path with a fake engine and disposable
project files.  It does not launch COMSOL or access existing runtime resources.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.test_t20_artifact_boundary import (
    _call,
    _envelope,
    _make_project,
    _node_path,
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink race; Windows needs a separate native test")
def test_reload_does_not_open_outside_file_after_subdirectory_swap(tmp_path, monkeypatch):
    daemon, project, root, _model, interp, execution = _make_project(tmp_path, "race", monkeypatch)
    try:
        target_dir = root / "inputs" / "case"
        target_dir.mkdir()
        target = target_dir / "grid.dat"
        target.write_text("AUTHORIZED-PROJECT-DATA\n")
        private_dir = tmp_path / "private"
        private_dir.mkdir()
        private = private_dir / "grid.dat"
        private.write_text("SYNTHETIC-PRIVATE-OUTSIDE-PROJECT\n")
        private_digest = hashlib.sha256(private.read_bytes()).hexdigest()
        private_stat = private.stat()
        private_identity = (private_stat.st_dev, private_stat.st_ino)
        interp.values["filename"] = str(target)

        real_open, real_stat = os.open, os.stat
        opened_identities = []
        swapped = []

        def open_spy(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            info = os.fstat(fd)
            opened_identities.append((info.st_dev, info.st_ino))
            return fd

        def swap_before_stat(path, *args, **kwargs):
            name = os.fspath(path)
            lexical_file = name == str(target) and kwargs.get("dir_fd") is None
            anchored_component = name == "case" and kwargs.get("dir_fd") is not None
            if not swapped and (lexical_file or anchored_component) and kwargs.get("follow_symlinks") is False:
                target_dir.rename(root / "inputs" / "case-before-swap")
                target_dir.symlink_to(private_dir, target_is_directory=True)
                swapped.append(True)
            return real_stat(path, *args, **kwargs)

        # ProjectRootBinding checks os.open membership in supports_dir_fd.
        # Preserve that capability when instrumenting os.open, otherwise the
        # test refuses before it reaches the digest race.
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {open_spy})
        monkeypatch.setattr(os, "open", open_spy)
        # A positive control proves this spy actually observes os.open, not only
        # pathlib.Path.open; the file is a synthetic test fixture.
        control_fd = os.open(private, os.O_RDONLY)
        os.close(control_fd)
        assert private_identity in opened_identities
        opened_identities.clear()
        monkeypatch.setattr(os, "stat", swap_before_stat)

        result = _call(
            daemon,
            "function.data_reload",
            {"path": _node_path(("func", "interpA"))},
            _envelope(project, execution, "reload-parent-swap"),
        )
        assert swapped, "the injected rename must actually run at the read boundary"
        assert private_identity not in opened_identities, "control process opened the outside file"
        assert private_digest not in json.dumps(result), "outside digest leaked into the public response"
    finally:
        daemon.close()
