from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from comsol_mcp import _g2_property_path_guard as guard
from comsol_mcp import _project_root as project_root
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._managed_backend import ManagedBackend


def test_imports_are_from_the_disposable_candidate() -> None:
    candidate_root = Path(__file__).resolve().parents[2]
    assert Path(guard.__file__).resolve() == candidate_root / "comsol_mcp" / "_g2_property_path_guard.py"


def decide(text: str, root: Path, binding=None) -> tuple[bool, str]:
    shape = guard.classify_string_leaf(text)
    return guard.adjudicate_path_leaf(
        text,
        shape,
        project_root=str(root),
        engine_host_proven=True,
        project_root_binding=binding,
    )


def test_plain_strings_expressions_and_ordered_in_project_path(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "nested").mkdir(parents=True)
    (root / "value.txt").write_text("inside", encoding="utf-8")
    binding = project_root.open_project_root_binding(root)
    try:
        for value in ("linear", "f1", "m/s^-1", "file", "u+v"):
            assert guard.classify_string_leaf(value) is None
        allowed, reason = decide(f"{root}/nested/../value.txt", root, binding)
        assert (allowed, reason) == (True, "inside_project_boundary")
        assert decide(f"{root}/../outside.txt", root, binding) == (
            False, "outside_project_boundary")
    finally:
        binding.close()


def test_symlink_is_rejected_before_following_dotdot(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "value.txt").write_text("inside", encoding="utf-8")
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    binding = project_root.open_project_root_binding(root)
    try:
        assert decide(f"{root}/link/../value.txt", root, binding) == (
            False, "symlink_component")
    finally:
        binding.close()


def test_multiple_link_target_remains_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = root / "value.txt"
    target.write_text("inside", encoding="utf-8")
    try:
        os.link(target, root / "alias.txt")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"filesystem hard links unavailable: {exc}")
    binding = project_root.open_project_root_binding(root)
    try:
        assert decide(str(target), root, binding) == (False, "multiple_link_target")
    finally:
        binding.close()


def test_replaced_descendant_during_open_is_refused_without_outside_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    original_branch = root / "branch"
    original_branch.mkdir(parents=True)
    outside.mkdir()
    inside_file = original_branch / "probe.txt"
    outside_file = outside / "probe.txt"
    inside_file.write_text("in-project", encoding="utf-8")
    outside_file.write_text("outside", encoding="utf-8")
    outside_infos = (os.stat(outside), os.stat(outside_file))
    outside_identities = {(info.st_dev, info.st_ino) for info in outside_infos}
    binding = project_root.open_project_root_binding(root)

    real_open = os.open
    real_stat = os.stat
    real_lstat = os.lstat
    real_fstat = os.fstat
    supported_dir_fd = os.supports_dir_fd
    swapped = False
    outside_metadata_reads: list[object] = []

    def note_if_outside(info) -> None:
        if (info.st_dev, info.st_ino) in outside_identities:
            outside_metadata_reads.append(info)

    def intercepted_open(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if path == "branch" and kwargs.get("dir_fd") is not None and not swapped:
            os.rename(root / "branch", root / "branch.saved")
            os.symlink(outside, root / "branch", target_is_directory=True)
            swapped = True
        return fd

    def monitored_stat(path, *args, **kwargs):
        info = real_stat(path, *args, **kwargs)
        note_if_outside(info)
        return info

    def monitored_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        note_if_outside(info)
        return info

    def monitored_fstat(fd: int):
        info = real_fstat(fd)
        note_if_outside(info)
        return info

    try:
        with monkeypatch.context() as patch:
            patch.setattr(guard.os, "open", intercepted_open)
            patch.setattr(guard.os, "stat", monitored_stat)
            patch.setattr(guard.os, "lstat", monitored_lstat)
            patch.setattr(guard.os, "fstat", monitored_fstat)
            patch.setattr(
                guard.os, "supports_dir_fd",
                supported_dir_fd | {intercepted_open, monitored_stat},
            )
            allowed, reason = decide(f"{root}/branch/probe.txt", root, binding)
        assert swapped
        assert (allowed, reason) == (False, "symlink_component")
        assert outside_metadata_reads == []
    finally:
        binding.close()


def test_missing_component_keeps_existing_no_deeper_probe_behavior(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    assert decide(f"{root}/missing/child.txt", root) == (
        True, "inside_project_boundary")


def test_dotdot_after_missing_or_non_directory_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "value.txt").write_text("inside", encoding="utf-8")
    for predecessor in ("missing", "value.txt"):
        assert decide(f"{root}/{predecessor}/../value.txt", root) == (
            False, "component_uninspectable")


def test_unbindable_project_root_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root_link = tmp_path / "project-link"
    root_link.symlink_to(target, target_is_directory=True)
    assert decide(f"{root_link}/value.txt", root_link) == (
        False, "root_binding_unavailable")


def test_walker_fd_close_failure_fails_closed_and_closes_each_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "value.txt").write_text("inside", encoding="utf-8")
    binding = project_root.open_project_root_binding(root)
    real_dup = os.dup
    real_open = os.open
    real_close = os.close
    supported_dir_fd = os.supports_dir_fd
    owned: set[int] = set()
    close_counts: dict[int, int] = {}
    failed_once: set[int] = set()

    def tracked_dup(fd: int) -> int:
        result = real_dup(fd)
        owned.add(result)
        return result

    def tracked_open(path, flags, *args, **kwargs):
        result = real_open(path, flags, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            owned.add(result)
        return result

    def injected_close(fd: int) -> None:
        close_counts[fd] = close_counts.get(fd, 0) + 1
        real_close(fd)
        if fd in owned and fd not in failed_once:
            failed_once.add(fd)
            raise OSError("synthetic close confirmation failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(guard.os, "dup", tracked_dup)
            patch.setattr(guard.os, "open", tracked_open)
            patch.setattr(guard.os, "close", injected_close)
            patch.setattr(
                guard.os, "supports_dir_fd", supported_dir_fd | {tracked_open}
            )
            with pytest.raises(ExecutionContractError) as raised:
                decide(f"{root}/nested/value.txt", root, binding)
        assert raised.value.code == "ACCESS_VIOLATION"
        assert owned
        assert all(close_counts.get(fd) == 1 for fd in owned)
        assert not binding._closed
    finally:
        binding.close()


def test_owned_root_binding_release_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "value.txt").write_text("inside", encoding="utf-8")
    real_open_binding = project_root.open_project_root_binding
    real_close = os.close
    owned_binding_fds: set[int] = set()

    def tracked_open_binding(path):
        binding = real_open_binding(path)
        owned_binding_fds.add(binding._fd)
        return binding

    def injected_close(fd: int) -> None:
        real_close(fd)
        if fd in owned_binding_fds:
            owned_binding_fds.remove(fd)
            raise OSError("synthetic root binding close failure")

    with monkeypatch.context() as patch:
        patch.setattr(project_root, "open_project_root_binding", tracked_open_binding)
        patch.setattr(guard.os, "close", injected_close)
        with pytest.raises(ExecutionContractError) as raised:
            decide(f"{root}/value.txt", root)
    assert raised.value.code == "ACCESS_VIOLATION"
    assert owned_binding_fds == set()


def test_backend_scoped_screen_uses_its_authorized_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "authorized-project"
    decoy_root = tmp_path / "service-decoy"
    root.mkdir()
    decoy_root.mkdir()
    target = root / "parameter.txt"
    target.write_text("inside", encoding="utf-8")
    authorized_binding = project_root.open_project_root_binding(root)
    service_decoy_binding = project_root.open_project_root_binding(decoy_root)
    service = SimpleNamespace(
        ledger=SimpleNamespace(session_id="synthetic-session"),
        project_root_binding=service_decoy_binding,
    )
    backend = ManagedBackend(
        tmp_path / "backend-home", object(), service=service, registry={},
        project_root=root, host_permission_ceiling=set(),
    )
    authorizations: list[tuple[str, str]] = []

    def authorize(project_id: str, permission: str):
        authorizations.append((project_id, permission))
        assert project_id == "authorized-project-id"
        return {"workspace": str(root)}

    backend.project_authorizer = authorize
    backend.endpoint_key = "synthetic-loopback"
    from comsol_mcp import _artifact_store
    monkeypatch.setattr(_artifact_store, "local_engine_host_identity", lambda _endpoint: "127.0.0.1")
    real_adjudicate = guard.adjudicate_path_leaf
    bindings_seen: list[object] = []

    def capture_binding(*args, **kwargs):
        bindings_seen.append(kwargs.get("project_root_binding"))
        return real_adjudicate(*args, **kwargs)

    monkeypatch.setattr(guard, "adjudicate_path_leaf", capture_binding)
    try:
        screen = backend._g2_property_path_screen("authorized-project-id")
        with backend.project_root_scope(authorized_binding):
            assert backend.project_root_binding is authorized_binding
            screen({"data": str(target)}, where="synthetic authorized property")
        assert bindings_seen == [authorized_binding]
        assert authorizations == [("authorized-project-id", "project_write")]
        assert authorized_binding._closed
        assert not service_decoy_binding._closed
    finally:
        backend.close()
        if not authorized_binding._closed:
            authorized_binding.close()
        service_decoy_binding.close()
