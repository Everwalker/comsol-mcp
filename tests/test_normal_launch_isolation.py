from __future__ import annotations

import hashlib
import json
import os
import pathlib
import types

import pytest

import test_full_project_release as existing_release_tests

release = existing_release_tests.release


def _completed(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_process_guard_ignores_poisoned_path_and_cannot_accept_its_self_only_snapshot(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    malicious_bin = tmp_path / "poisoned-bin"
    malicious_bin.mkdir()
    (malicious_bin / "ps").write_text("inert same-name executable fixture\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(malicious_bin))
    monkeypatch.setattr(release, "_is_windows_runtime", lambda: False)
    monkeypatch.setattr(release, "_is_executable_file", lambda path: path == "/bin/ps")
    commands: list[list[str]] = []

    def inert_capture(argv: list[str], **_kwargs: object) -> types.SimpleNamespace:
        commands.append(list(argv))
        if argv[0] == "ps":
            # A PATH-selected attacker can return a plausible self-only snapshot.
            return _completed(f"{os.getpid()} 1 /usr/local/bin/python3.12 /tmp/release.py\n")
        return _completed("")

    monkeypatch.setattr(release, "_capture", inert_capture)
    result = release._process_guard(tmp_path / "owned-venv")

    assert result["status"] == "UNKNOWN"
    assert commands and commands[0][0] == "/bin/ps"


def test_process_guard_refuses_before_capture_when_fixed_tool_is_unavailable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(release, "_is_windows_runtime", lambda: False)
    monkeypatch.setattr(release, "_is_executable_file", lambda _path: False)
    monkeypatch.setattr(release.shutil, "which", lambda _name: "/tmp/poisoned/ps")
    monkeypatch.setattr(release, "_capture", lambda argv, **_kwargs: commands.append(list(argv)))

    result = release._process_guard(tmp_path / "owned-venv")

    assert result["status"] == "UNKNOWN"
    assert commands == []


class _FakeWideBuffer:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.value = ""

    def __len__(self) -> int:
        return self.capacity


class _FakeGetSystemDirectory:
    def __init__(self, directory: str, return_value: int | bool | None = None) -> None:
        self.directory = directory
        self.return_value = return_value
        self.argtypes: object = None
        self.restype: object = None
        self.calls: list[tuple[object, int]] = []

    def __call__(self, buffer: _FakeWideBuffer, size: int) -> int | bool:
        self.calls.append((buffer, size))
        buffer.value = self.directory
        return len(self.directory) if self.return_value is None else self.return_value


def _fake_ctypes(directory: str = r"C:\Windows\System32", return_value: int | bool | None = None):
    getter = _FakeGetSystemDirectory(directory, return_value)
    loads: list[tuple[str, int]] = []

    def load(name: str, *, winmode: int):
        loads.append((name, winmode))
        return types.SimpleNamespace(GetSystemDirectoryW=getter)

    api = types.SimpleNamespace(
        WinDLL=load,
        POINTER=lambda item: ("POINTER", item),
        c_wchar="WCHAR",
        c_uint="UINT",
        create_unicode_buffer=_FakeWideBuffer,
    )
    return api, getter, loads


def test_windows_powershell_resolver_uses_bounded_system32_api_leaf() -> None:
    api, getter, loads = _fake_ctypes()

    path = release._windows_powershell_path(api)

    assert path == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert loads == [("kernel32", 0x800)]
    assert getter.argtypes == [("POINTER", "WCHAR"), "UINT"]
    assert getter.restype == "UINT"
    assert getter.calls and getter.calls[0][1] == 32768


@pytest.mark.parametrize("return_value", [0, 32768, 32769, True, 1])
def test_windows_powershell_resolver_rejects_invalid_return_lengths(return_value: int | bool) -> None:
    api, _getter, loads = _fake_ctypes(return_value=return_value)

    assert release._windows_powershell_path(api) is None
    assert loads == [("kernel32", 0x800)]


def test_windows_process_guard_uses_only_resolved_system_powershell(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    pid = os.getpid()
    rows = [{"ProcessId": pid, "ParentProcessId": 1, "Name": "python.exe",
             "ExecutablePath": r"C:\Python\python.exe", "CommandLine": r"C:\Python\python.exe release.py"}]
    commands: list[list[str]] = []
    monkeypatch.setattr(release, "_is_windows_runtime", lambda: True)
    monkeypatch.setattr(release, "_windows_powershell_path", lambda _ctypes_module=None: powershell)
    monkeypatch.setattr(release, "_is_executable_file", lambda path: path == powershell)
    monkeypatch.setattr(release.shutil, "which", lambda _name: pytest.fail("PATH fallback was used"))

    def inert_capture(argv: list[str], **_kwargs: object) -> types.SimpleNamespace:
        commands.append(list(argv))
        return _completed(json.dumps(rows))

    monkeypatch.setattr(release, "_capture", inert_capture)
    result = release._process_guard(tmp_path / "owned-venv")

    assert result["status"] == "CLEAR"
    assert commands and commands[0][0] == powershell


def test_windows_process_guard_refuses_missing_system_resolution_without_dispatch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(release, "_is_windows_runtime", lambda: True)
    monkeypatch.setattr(release, "_windows_powershell_path", lambda _ctypes_module=None: None)
    monkeypatch.setattr(release.shutil, "which", lambda _name: pytest.fail("PATH fallback was used"))
    monkeypatch.setattr(release, "_capture", lambda argv, **_kwargs: commands.append(list(argv)))

    result = release._process_guard(tmp_path / "owned-venv")

    assert result["status"] == "UNKNOWN"
    assert commands == []


class _InertListener:
    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def bind(self, _address: tuple[str, int]) -> None:
        return None

    def listen(self, _backlog: int) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def getsockname(self) -> tuple[str, int]:
        return "127.0.0.1", 45678

    def accept(self):
        raise TimeoutError("inert listener has no peer")


def test_offline_install_uses_fixed_sandbox_and_full_isolated_lexical_python_argv(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release, "_is_executable_file", lambda path: path == "/usr/bin/sandbox-exec")
    monkeypatch.setattr(release.shutil, "which", lambda _name: "/tmp/poisoned/sandbox-exec")
    monkeypatch.setattr(release.socket, "socket", lambda *_args, **_kwargs: _InertListener())

    base_python = tmp_path / "base-python"
    base_python.write_text("inert base interpreter placeholder\n", encoding="utf-8")
    lexical_python = tmp_path / "lexical-venv" / "bin" / "python"
    lexical_python.parent.mkdir(parents=True)
    lexical_python.symlink_to(base_python)
    evidence = tmp_path / "offline-evidence"
    args = types.SimpleNamespace(
        python=lexical_python,
        requirements=tmp_path / "requirements.lock",
        wheelhouse=tmp_path / "wheelhouse",
        venv=tmp_path / "target-venv",
        evidence=evidence,
    )
    calls: list[list[str]] = []

    def inert_capture(argv: list[str], **_kwargs: object) -> types.SimpleNamespace:
        calls.append(list(argv))
        return _completed(json.dumps({"status": "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT"}))

    monkeypatch.setattr(release, "_capture", inert_capture)
    result = release.offline_install(args, pathlib.Path(release.__file__))

    assert result["status"] == "PASS_PROCESS_ISOLATED_INSTALL_AND_IMPORT"
    assert calls and calls[0][0] == "/usr/bin/sandbox-exec"
    assert calls[0][3:7] == [str(lexical_python), "-I", "-B", str(pathlib.Path(release.__file__))]
    assert calls[0][3] == str(lexical_python)


def test_offline_install_refuses_before_listener_or_evidence_when_fixed_sandbox_is_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release, "_is_executable_file", lambda _path: False)
    monkeypatch.setattr(release.shutil, "which", lambda _name: "/tmp/poisoned/sandbox-exec")
    monkeypatch.setattr(release.socket, "socket", lambda *_args, **_kwargs: pytest.fail("listener was created"))
    args = types.SimpleNamespace(
        python=tmp_path / "python", requirements=tmp_path / "requirements.lock",
        wheelhouse=tmp_path / "wheelhouse", venv=tmp_path / "target-venv", evidence=tmp_path / "evidence",
    )

    with pytest.raises(release.ReleaseError, match="sandbox-exec is unavailable"):
        release.offline_install(args, pathlib.Path(release.__file__))
    assert not args.evidence.exists()


def test_normal_metadata_children_keep_lexical_venv_and_direct_isolation_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
) -> None:
    lexical_python = tmp_path / "venv" / "bin" / "python"
    recorded: list[list[str]] = []
    package_digest = hashlib.sha256(b"[]").hexdigest()
    distributions = [{"name": "pip", "version": "25.0.1"}]
    distribution_digest = hashlib.sha256(
        json.dumps(distributions, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    def inert_run_record(argv: list[str], **_kwargs: object) -> dict[str, object]:
        recorded.append(list(argv))
        if "importlib.metadata as m" in " ".join(argv):
            stdout = "1.2.3\n"
        elif "from importlib import metadata" in " ".join(argv):
            stdout = json.dumps({"distributions": distributions, "digest": distribution_digest}) + "\n"
        else:
            stdout = json.dumps({"root": "/inert/package", "files": [], "digest": package_digest}) + "\n"
        return {"argv": argv, "exit_code": 0, "stdout": stdout, "stderr": "", "duration_seconds": 0.0}

    monkeypatch.setattr(release, "run_record", inert_run_record)
    release.package_fingerprint(lexical_python)
    release.distribution_fingerprint(lexical_python)
    assert release._installed_version(lexical_python) == "1.2.3"

    assert len(recorded) == 3
    for argv in recorded:
        assert argv[0] == str(lexical_python)
        assert argv[1:3] == ["-I", "-B"]
