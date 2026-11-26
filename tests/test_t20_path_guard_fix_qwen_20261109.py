"""T20 path-shape guard fix tests (2026-11-09): pre-normalization symlink/``..``
hop and Windows drive-relative shapes.

Execution model: omlx/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S (local route, this fix
slice).  Like the parent tests, the public managed route, project authority,
registry admission, managed backend and property adapter are real; the Worker
and the node setter are fakes, and every file/link here is a disposable
``tmp_path`` fixture — nothing here is native COMSOL evidence.

Two confirmed gaps are pinned closed:

1. A POSIX-absolute value whose ``..`` follows an in-project symlink
   (``<root>/link/../secret`` with ``link`` pointing outside the project).
   The kernel follows the symlink *before* applying ``..``, so the value
   resolves outside the project even though ``os.path.normpath`` folds it to
   ``<root>/secret``.  The guard now walks components in resolution order, so
   the symlink component is seen before the fold (``symlink_component``).
2. Windows drive-relative shapes with no separator after the drive letter
   (``C:secret.dat``, ``D:dir\\secret.dat``, bare ``C:``) fell through the
   classifier as ordinary strings and reached the setter.  They are now
   classified ``drive_relative`` and refused on every host
   (``drive_relative_undecidable``).

Positive controls are pinned too: ``linear``/``f1``-class plain text, unit
expressions, and an in-project absolute that folds ``..`` under a *proven
plain existing directory* keep applying, verbatim and unrewritten.

Explicitly NOT claimed: this partial path-shape screen, including its POSIX
descriptor-relative metadata walk, is a file sandbox, a physical-permission
boundary, or a proof that every project-internal path is allowed. The
compatibility cost of that conservatism is pinned below (``<root>/..`` and
``/outside/../<root>/x`` are refused; any
value starting with a single letter plus ``:`` — including ambiguous text such
as ``a:b`` — is refused on every host).  The bare relative ``f1`` semantics
stay open and are not re-decided here; the Windows/UNC assertions below drive
the lexical nt code path on a POSIX host and are not native Windows evidence.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from comsol_mcp import _g2_property_path_guard as guard
from comsol_mcp._g2_property_path_guard import (SHAPE_DRIVE_RELATIVE,
                                               SHAPE_POSIX_ABSOLUTE,
                                               SHAPE_UNC,
                                               SHAPE_WINDOWS_DRIVE,
                                               adjudicate_path_leaf,
                                               classify_string_leaf)
from tests.test_t20_artifact_boundary import (_call, _envelope, _error_code,
                                             _make_project, _node_path)
from tests.test_t20_generic_filename_boundary_20261108 import _property_body


def _refusal_shape(result) -> None:
    """A direct-route refusal must be proven, not merely not-success."""
    assert not result["success"], result
    assert _error_code(result) == "ACCESS_VIOLATION", result
    assert result["data"]["status"] == "REFUSED", result
    assert result["data"]["refused"] is True, result
    assert result["data"]["witness"]["mutation_issued"] is False, result
    assert result["error"]["stage"] == "validation", result


# ---------------------------------------------------------------------------
# gap 1: absolute ``..`` must not hide an in-project symlink hop
# ---------------------------------------------------------------------------


def test_absolute_dotdot_after_in_project_symlink_refused_before_setter(tmp_path, monkeypatch):
    daemon, project, root, _model, node, execution = _make_project(tmp_path, "fix-abs-dotdot", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        outside = tmp_path / "synthetic-other-project"
        (outside / "dir").mkdir(parents=True)
        (outside / "secret.dat").write_text("disposable synthetic data\n")
        (root / "link").symlink_to(outside / "dir", target_is_directory=True)
        # Built without resolve/normpath on purpose: the OS follows the link
        # before processing "..", so this token really does land outside.
        raw = str(root / "link") + "/../secret.dat"
        assert Path(raw).resolve(strict=True) == (outside / "secret.dat").resolve()
        assert not Path(raw).resolve(strict=True).is_relative_to(root.resolve())

        reached = []
        original_set = node.set

        def record_target(name, typed):
            if name == "filename":
                reached.append(Path(typed["data"]).resolve(strict=True))
            return original_set(name, typed)

        monkeypatch.setattr(node, "set", record_target)
        result = _call(daemon, "node.property_set", _property_body("filename", raw),
                       _envelope(project, execution, "fix-abs-dotdot-1"))
        _refusal_shape(result)
        assert reached == [], {"setter_reached": reached, "input": raw}
        assert result["error"]["details"]["findings"][0]["reason"] == "symlink_component"
        assert result["error"]["details"]["findings"][0]["shape"] == SHAPE_POSIX_ABSOLUTE
        assert node.values.get("filename") is None
    finally:
        daemon.close()


def test_absolute_in_project_dotdot_fold_after_plain_directory_still_applies(tmp_path, monkeypatch):
    """Positive control: the walk is not a blanket ban on ``..`` in absolutes.

    ``<root>/inputs/../inputs/ok.dat`` folds back inside the project with only
    plain existing components in front of the ``..``, which matches how the OS
    resolves it; the value must still apply, verbatim (the guard adjudicates,
    it never rewrites the dispatched value).
    """
    daemon, project, root, _model, node, execution = _make_project(tmp_path, "fix-fold-ok", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        target = root / "inputs" / "ok.dat"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("0.0 1.0\n")
        raw = str(root / "inputs" / ".." / "inputs" / "ok.dat")
        assert Path(raw).resolve(strict=True) == target.resolve()
        result = _call(daemon, "node.property_set", _property_body("filename", raw),
                       _envelope(project, execution, "fix-fold-ok-1"))
        assert result["success"], result
        assert node.values["filename"] == raw
    finally:
        daemon.close()


def test_absolute_dotdot_at_root_boundary_and_sibling_prefix_refused(tmp_path, monkeypatch):
    """Documented compatibility cost of the ordered walk: a ``..`` at the root
    boundary, and a value that only folds back inside after escaping the root
    prefix, are refused rather than normalized into the project."""
    daemon, project, root, _model, node, execution = _make_project(tmp_path, "fix-boundary", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        for index, value in enumerate([str(root / ".."),
                                       str(root.parent / "x" / root.name / "inputs" / "a.dat")]):
            result = _call(daemon, "node.property_set", _property_body("filename", value),
                           _envelope(project, execution, f"fix-boundary-{index}"))
            _refusal_shape(result)
            assert result["error"]["details"]["findings"][0]["reason"] == "outside_project_boundary"
        assert node.values.get("filename") is None
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# gap 2: Windows drive-relative shapes without a separator after the drive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [r"C:secret.dat", r"D:dir\secret.dat", "C:", r"C:..\secret.dat"])
def test_windows_drive_relative_values_refused_before_setter(tmp_path, monkeypatch, value):
    # ``filename`` on a known file-backed type is refused earlier by the
    # T19.a known-file gate (pinned by
    # tests/test_t19a_import_filename_semantics_20261123.py), so this pin of
    # the drive-relative shape screen uses the non-known-file ``source``
    # property, which reaches the same screen unchanged.
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "fix-drive-rel", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        result = _call(daemon, "node.property_set", _property_body("source", value),
                       _envelope(project, execution, f"fix-drive-rel-{value}"))
        _refusal_shape(result)
        assert result["error"]["details"]["findings"][0]["shape"] == SHAPE_DRIVE_RELATIVE
        assert result["error"]["details"]["findings"][0]["reason"] == "drive_relative_undecidable"
        assert node.values.get("source") is None
    finally:
        daemon.close()


def test_single_letter_colon_text_is_refused_as_documented_cost(tmp_path, monkeypatch):
    """The drive-letter screen is shape-based, so an ambiguous text such as
    ``a:b`` is refused too.  This pins the compatibility cost openly instead of
    hiding it: a two-letter prefix is not a drive shape and keeps applying."""
    daemon, project, _root, _model, node, execution = _make_project(tmp_path, "fix-cost", monkeypatch)
    try:
        monkeypatch.setattr(daemon.backend, "_require_g2_isolation",
                            lambda: {"synthetic_owned_server": True})
        blocked = _call(daemon, "node.property_set", _property_body("source", "a:b"),
                        _envelope(project, execution, "fix-cost-blocked"))
        _refusal_shape(blocked)
        assert blocked["error"]["details"]["findings"][0]["reason"] == "drive_relative_undecidable"
        assert node.values.get("source") is None

        kept = _call(daemon, "node.property_set", {
            "path": _node_path(("func", "interpA")),
            "properties": [
                {"name": "source", "value": {"kind": "string", "shape": [], "data": "ab:cd"}},
                {"name": "interp", "value": {"kind": "string", "shape": [], "data": "linear"}},
                {"name": "argunit", "value": {"kind": "string", "shape": [], "data": "m/s^-1"}},
            ]}, _envelope(project, execution, "fix-cost-kept"))
        assert kept["success"], kept
        assert node.values["source"] == "ab:cd"
        assert node.values["interp"] == "linear"
        assert node.values["argunit"] == "m/s^-1"
    finally:
        daemon.close()


# ---------------------------------------------------------------------------
# pure helper controls: classifier, walk semantics, probe discipline
# ---------------------------------------------------------------------------


class _OsProbeShim:
    """Stand-in for the guard's ``os`` under the descriptor-pinned contract.

    ``open`` and ``stat`` are tracking wrappers that delegate to the real os
    functions.  The shim-local ``supports_dir_fd`` set includes the wrappers,
    so the guard's own capability check (an identity comparison of
    ``os.open``/``os.stat`` against ``os.supports_dir_fd``) stays satisfied
    through them.  Each wrapper fails the test at call time when the guard
    stats or opens a bare path name, opens without ``O_NOFOLLOW``, opens a
    non-directory or a writeable target, stats with symlink following
    enabled, or traverses through a base descriptor that is not an open
    directory.  Every guard metadata access is therefore actively verified
    to be descriptor-pinned, no-follow and read-only, and no file content is
    ever opened.  Fd-level calls (``dup``/``fstat``/``close``) and lexical
    helpers pass through.  The approved root's binding open (the walk's
    self-open path) runs inside ``_project_root`` with its own ``os`` and is
    outside this shim; that binding path is pinned by the project-root
    binding tests.
    """

    def __init__(self, name: str):
        self.probes: list[dict] = []
        self.name = name
        self.path = os.path
        self.sep = os.sep
        self.open = self._tracked_open
        self.stat = self._tracked_stat
        self.supports_dir_fd = set(os.supports_dir_fd) | {self.open, self.stat}

    def _discipline(self, kind: str, path, kwargs: dict, flags: int | None) -> None:
        if "dir_fd" not in kwargs:
            raise AssertionError(
                f"guard {kind}() ran on bare path name {os.fspath(path)!r}; "
                "the contract only allows descriptor-relative access")
        component = os.fspath(path)
        if os.path.isabs(component) or os.sep in component or component in {".", ".."}:
            raise AssertionError(
                f"guard {kind}() traversed the non-component path {component!r}")
        if flags is not None:
            if not flags & getattr(os, "O_NOFOLLOW", 0):
                raise AssertionError(f"guard {kind}() opened {component!r} without O_NOFOLLOW")
            if not flags & getattr(os, "O_DIRECTORY", 0):
                raise AssertionError(
                    f"guard {kind}() opened the non-directory target {component!r}; "
                    "adjudication never opens file content")
            if flags & (os.O_WRONLY | os.O_RDWR | getattr(os, "O_CREAT", 0)
                        | getattr(os, "O_TRUNC", 0) | getattr(os, "O_APPEND", 0)):
                raise AssertionError(f"guard {kind}() requested write access to {component!r}")
        elif kwargs.get("follow_symlinks") is not False:
            raise AssertionError(
                f"guard {kind}() probed {component!r} with symlink following enabled")
        if not stat.S_ISDIR(os.fstat(kwargs["dir_fd"]).st_mode):
            raise AssertionError(
                f"guard {kind}() traversed through base descriptor {kwargs['dir_fd']} "
                "that is not an open directory")
        self.probes.append({"op": kind, "component": component, "dir_fd": kwargs["dir_fd"]})

    def _tracked_open(self, path, *args, **kwargs):
        self._discipline("open", path, kwargs,
                         args[0] if args else kwargs.get("flags", 0))
        return os.open(path, *args, **kwargs)

    def _tracked_stat(self, path, *args, **kwargs):
        self._discipline("stat", path, kwargs, None)
        return os.stat(path, *args, **kwargs)

    def lstat(self, path, *args, **kwargs):
        self.probes.append({"op": "lstat", "component": os.fspath(path),
                            "dir_fd": kwargs.get("dir_fd")})
        return os.lstat(path, *args, **kwargs)

    def __getattr__(self, item):
        return getattr(os, item)


def _fixture_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs" / "a.dat").write_text("0.0 1.0\n")
    (root / "dir").mkdir()
    outside = tmp_path / "other" / "dir"
    outside.mkdir(parents=True)
    (tmp_path / "other" / "secret.dat").write_text("disposable synthetic data\n")
    (root / "link").symlink_to(outside, target_is_directory=True)
    return root


@pytest.mark.parametrize("value,shape", [
    (r"C:secret.dat", SHAPE_DRIVE_RELATIVE),
    (r"D:dir\secret.dat", SHAPE_DRIVE_RELATIVE),
    ("C:", SHAPE_DRIVE_RELATIVE),
    (r"C:..\secret.dat", SHAPE_DRIVE_RELATIVE),
    (r"C:\synthetic\input.dat", SHAPE_WINDOWS_DRIVE),
    ("C:/synthetic/input.dat", SHAPE_WINDOWS_DRIVE),
    (r"\\synthetic-server\share\input.dat", SHAPE_UNC),
    ("linear", None),
    ("f1", None),
    ("m/s^-1", None),
    ("ab:cd", None),
    ("a..b", None),
    ("inputs/data.dat", None),
    (str(Path("/approved/project/inputs/data.dat")), SHAPE_POSIX_ABSOLUTE),
])
def test_classifier_shapes(value, shape):
    assert classify_string_leaf(value) is shape


def test_walk_refuses_symlink_hop_and_root_escape_but_keeps_plain_folds(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path)
    shim = _OsProbeShim(name=os.name)
    monkeypatch.setattr(guard, "os", shim)

    def adjudicate(value: str) -> tuple[bool, str]:
        shape = classify_string_leaf(value)
        assert shape is not None, value
        return adjudicate_path_leaf(value, shape, project_root=str(root),
                                    engine_host_proven=True)

    # the hop: symlink component is seen before the following ".."
    assert adjudicate(str(root / "link") + "/../secret.dat") == (False, "symlink_component")
    assert adjudicate(str(root / "link") + "/../../secret.dat") == (False, "symlink_component")
    assert adjudicate(str(root / "link")) == (False, "symlink_component")
    # ".." folding above the root, or at the root boundary
    assert adjudicate(str(root / "inputs" / ".." / ".." / "secret.dat")) == (False, "outside_project_boundary")
    assert adjudicate(str(root / "..")) == (False, "outside_project_boundary")
    # sibling name and unrelated root are refused without any stat of the sibling
    assert adjudicate(f"{root}X/inputs/a.dat") == (False, "outside_project_boundary")
    assert adjudicate(str(tmp_path / "other" / "secret.dat")) == (False, "outside_project_boundary")
    # plain folds and not-yet-existing targets stay allowed (undecidable-free)
    assert adjudicate(str(root / "dir" / ".." / "inputs" / "a.dat")) == (True, "inside_project_boundary")
    assert adjudicate(str(root / "inputs" / ".." / "inputs" / "fresh.dat")) == (True, "inside_project_boundary")
    assert adjudicate(str(root / "inputs" / "." / "a.dat")) == (True, "inside_project_boundary")

    # probe discipline: every guard metadata access recorded above happened
    # through an open directory descriptor (checked inside the wrappers at
    # call time), no-follow (O_NOFOLLOW component opens, follow_symlinks=False
    # stats), read-only, and on single component names; file content was
    # never opened; bare-name stat/open would fail the wrappers, and any lstat
    # is rejected by the assertion below.
    assert shim.probes
    assert all(p["op"] in {"stat", "open"} and p["dir_fd"] is not None
               and p["component"] and os.sep not in p["component"]
               and p["component"] not in {".", ".."} for p in shim.probes)


@pytest.mark.parametrize("value,reason", [
    (r"C:\approved\dir\..\x.dat", "absolute_traversal_unscannable"),
    (r"C:\approved\..\x.dat", "absolute_traversal_unscannable"),
    (r"\\synthetic-server\share\a\..\b", "absolute_traversal_unscannable"),
    (r"C:\other\x.dat", "outside_project_boundary"),
])
def test_nt_lexical_code_path_refuses_parent_segments(value, reason, monkeypatch):
    """Drives the lexical nt branch of the guard on this POSIX host by naming
    the guard's host as nt; this is a code-path control, NOT native Windows
    evidence, and it claims only lexical containment for the allowed case."""
    shim = _OsProbeShim(name="nt")
    monkeypatch.setattr(guard, "os", shim)
    shape = classify_string_leaf(value)
    assert shape is not None, value
    allowed, reported = adjudicate_path_leaf(value, shape,
                                             project_root=r"C:\approved", engine_host_proven=True)
    assert not allowed and reported == reason, (value, allowed, reported)
    allowed, reported = adjudicate_path_leaf(r"C:\approved\inputs\x.dat", SHAPE_WINDOWS_DRIVE,
                                             project_root=r"C:\approved", engine_host_proven=True)
    assert allowed and reported == "inside_project_boundary_lexical_only"
    assert shim.probes == []  # the nt branch performs no filesystem probe


def test_drive_relative_refusal_is_host_independent(monkeypatch):
    for host in ("posix", "nt"):
        shim = _OsProbeShim(name=host)
        monkeypatch.setattr(guard, "os", shim)
        assert adjudicate_path_leaf(r"C:secret.dat", SHAPE_DRIVE_RELATIVE,
                                    project_root="/approved/project",
                                    engine_host_proven=True) == (False, "drive_relative_undecidable")
        assert adjudicate_path_leaf(r"C:secret.dat", SHAPE_DRIVE_RELATIVE,
                                    project_root=None,
                                    engine_host_proven=False) == (False, "drive_relative_undecidable")
        assert shim.probes == []
