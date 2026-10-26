"""Run focused software tests with irreversible, fail-closed effect guards."""
from __future__ import annotations

import ctypes
import importlib.abc
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import urllib.request


TESTS = pathlib.Path(__file__).resolve().parent
ROOT = TESTS.parent
REPOSITORY = ROOT.parent.parent
EXPECTED_NAME = "universal-normal-launch-isolation-repair-20261026-r1"
if ROOT.name != EXPECTED_NAME or ROOT.parent.name != "_state":
    raise SystemExit("effect gate refused: runner is outside the fresh private candidate")
if not (sys.flags.isolated and sys.flags.ignore_environment and sys.flags.no_user_site
        and sys.dont_write_bytecode):
    raise SystemExit("effect gate refused: invoke with the absolute Repo/.venv/bin/python -I -B")

_RESERVED_ENVIRONMENT_KEYS = ("HOME", "home", "CODEX_HOME")
_initial_reserved_environment = {
    key: (key in os.environ, os.environ.get(key)) for key in _RESERVED_ENVIRONMENT_KEYS
}

BLOCKED: list[str] = []
BLOCKED_CONTEXTS: list[str] = []


def _caller_context() -> str:
    try:
        frame = sys._getframe(2)
        gate_file = os.path.realpath(__file__)
        while frame is not None and os.path.realpath(frame.f_code.co_filename) == gate_file:
            frame = frame.f_back
        if frame is None:
            return "unknown"
        return f"{pathlib.Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}:{frame.f_lineno}"
    except (AttributeError, ValueError):
        return "unknown"


def _deny(name: str):
    def denied(*_args, **_kwargs):
        BLOCKED.append(name)
        raise PermissionError(f"offline effect gate denied {name}")
    return denied


def _inside_candidate(value: object) -> bool:
    try:
        raw = os.fsdecode(os.fspath(value))
    except (TypeError, ValueError):
        return False
    if not os.path.isabs(raw):
        raw = os.path.join(os.getcwd(), raw)
    target = os.path.realpath(raw)
    root = os.path.realpath(ROOT)
    try:
        return os.path.commonpath((root, target)) == root
    except ValueError:
        return False


def _write_target_class(value: object) -> str:
    try:
        raw = os.path.realpath(os.fsdecode(os.fspath(value)))
        null = os.path.realpath(os.devnull)
        if raw == null:
            return "null-device"
        if os.path.commonpath((os.path.realpath(ROOT), raw)) == os.path.realpath(ROOT):
            return "candidate"
        if os.path.commonpath((os.path.realpath(REPOSITORY), raw)) == os.path.realpath(REPOSITORY):
            return "repository-outside-candidate"
        return "outside-repository"
    except (TypeError, ValueError):
        return "non-path"


def _audit(event: str, args: tuple[object, ...]) -> None:
    if (event.startswith(("subprocess.", "socket.", "ctypes.dlopen", "os.exec", "os.spawn"))
            or event in {"os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.kill",
                         "os.wait", "os.waitpid"}):
        BLOCKED.append(event)
        raise PermissionError(f"offline effect gate denied {event}")
    if event == "os.chdir" and args and not _inside_candidate(args[0]):
        BLOCKED.append("chdir_outside_candidate")
        raise PermissionError("offline effect gate denied chdir outside candidate")
    if event == "open" and args:
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        writing = isinstance(mode, str) and any(flag in mode for flag in "wax+")
        if isinstance(flags, int):
            writing = writing or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        null_device = False
        try:
            null_device = os.path.realpath(os.fsdecode(os.fspath(args[0]))) == os.path.realpath(os.devnull)
        except (TypeError, ValueError):
            pass
        if writing and not (_inside_candidate(args[0]) or null_device):
            BLOCKED.append("write_outside_candidate:" + _write_target_class(args[0]))
            BLOCKED_CONTEXTS.append(_caller_context())
            raise PermissionError("offline effect gate denied write outside candidate")
    two_path_events = {"os.rename", "os.replace", "os.link", "os.symlink"}
    if event in two_path_events and len(args) >= 2:
        if not (_inside_candidate(args[0]) and _inside_candidate(args[1])):
            BLOCKED.append("filesystem_effect_outside_candidate")
            raise PermissionError("offline effect gate denied filesystem effect outside candidate")
    path_events = {"os.mkdir", "os.remove", "os.rmdir", "os.unlink", "os.chmod", "os.chown",
                   "os.truncate", "os.utime"}
    if event in path_events and args and not _inside_candidate(args[0]):
        BLOCKED.append("filesystem_effect_outside_candidate")
        raise PermissionError("offline effect gate denied filesystem effect outside candidate")


# Patch ordinary APIs before pytest or any project module is imported. The
# audit hook below also catches aliases and lower-level calls.
for _name in ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
    if hasattr(subprocess, _name):
        setattr(subprocess, _name, _deny("subprocess." + _name))
for _name in ("system", "popen", "fork", "forkpty", "posix_spawn", "kill", "wait", "waitpid"):
    if hasattr(os, _name):
        setattr(os, _name, _deny("os." + _name))
for _name in ("connect", "connect_ex", "bind", "listen"):
    if hasattr(socket.socket, _name):
        setattr(socket.socket, _name, _deny("socket." + _name))
for _name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
    if hasattr(socket, _name):
        setattr(socket, _name, _deny("socket." + _name))
urllib.request.urlopen = _deny("urllib.request.urlopen")
for _name in ("CDLL", "PyDLL", "WinDLL", "OleDLL"):
    if hasattr(ctypes, _name):
        setattr(ctypes, _name, _deny("ctypes." + _name))
if hasattr(ctypes, "create_unicode_buffer"):
    ctypes.create_unicode_buffer = _deny("ctypes.create_unicode_buffer")
if hasattr(os, "startfile"):
    os.startfile = _deny("os.startfile")
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory = _deny("os.add_dll_directory")


class _NativeModuleFence(importlib.abc.MetaPathFinder):
    _DENIED_ROOTS = {"comsol", "comsol_mcp", "jpype", "jpype1", "java", "jnius", "clr", "win32com"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0].lower() in self._DENIED_ROOTS:
            BLOCKED.append("native_module:" + fullname.partition(".")[0].lower())
            raise PermissionError("offline effect gate denied COMSOL/JDK/native module import")
        return None


sys.meta_path.insert(0, _NativeModuleFence())
sys.addaudithook(_audit)

# Keep stdlib default temporary files inside the private candidate.  This avoids
# probing host /tmp directories while test fixtures create inert temp folders.
_temporary_root = ROOT / ".runner-temp"
_temporary_root.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_temporary_root)

_initial_env = dict(os.environ)
_plugin_env_name = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
_plugin_env_present = _plugin_env_name in os.environ
_plugin_env_value = os.environ.get(_plugin_env_name)
os.environ[_plugin_env_name] = "1"
_pytest_env = dict(os.environ)

import pytest  # noqa: E402  (effect guards must be installed first)

_arguments = sys.argv[1:]
if not _arguments:
    raise SystemExit("effect gate refused: no explicit focused test paths supplied")
_args = ["-p", "no:cacheprovider", "-s", "--log-file", str(ROOT / "evidence" / "pytest-offline.log"),
         "--junitxml", str(ROOT / "evidence" / "pytest-offline.junit.xml"),
         "--basetemp", str(ROOT / ".test-tmp"), *_arguments]
try:
    _exit_code = int(pytest.main(_args))
finally:
    if _plugin_env_present:
        os.environ[_plugin_env_name] = _plugin_env_value or ""
    else:
        os.environ.pop(_plugin_env_name, None)
    _final_env = dict(os.environ)
    _final_reserved_environment = {
        key: (key in os.environ, os.environ.get(key)) for key in _RESERVED_ENVIRONMENT_KEYS
    }
    if _final_reserved_environment != _initial_reserved_environment:
        print("effect gate refused: HOME/home/CODEX_HOME changed", file=sys.stderr)
        _exit_code = _exit_code or 90
    if _final_env != _initial_env:
        _changed = sorted(key for key in set(_final_env) | set(_initial_env)
                          if _final_env.get(key) != _initial_env.get(key))
        print("effect gate refused: runner environment changed keys=" + ",".join(_changed), file=sys.stderr)
        _exit_code = _exit_code or 90
if BLOCKED:
    print("effect gate refused calls=" + ",".join(BLOCKED), file=sys.stderr)
    if BLOCKED_CONTEXTS:
        print("effect gate blocked write callers=" + ",".join(BLOCKED_CONTEXTS), file=sys.stderr)
    _exit_code = _exit_code or 91
else:
    print("effect_gate=PASS; subprocess/network/native/process-query=DENIED; writes=candidate-or-null-only; blocked_attempts=0")
raise SystemExit(_exit_code)
