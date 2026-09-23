"""Minimal process identity queries used by private control-plane children.

Windows cannot use POSIX ``kill(pid, 0)`` as a liveness probe for another
user's process.  This module queries process handles without signalling them.
The optional creation timestamp lets private rendezvous records distinguish a
reused PID from their original child.
"""
from __future__ import annotations

import os
from typing import TypedDict


class ProcessIdentity(TypedDict):
    alive: bool
    start_epoch_ms: int | None


def _windows_process_identity(pid: int) -> ProcessIdentity:
    """Read liveness and creation time without sending a Windows signal."""
    import ctypes
    from ctypes import wintypes

    query_limited_information = 0x1000
    still_active = 259
    invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                         ctypes.POINTER(wintypes.FILETIME))
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        # Access denied cannot establish death and must block a replacement.
        return {"alive": ctypes.get_last_error() != invalid_parameter, "start_epoch_ms": None}
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return {"alive": True, "start_epoch_ms": None}
        if exit_code.value != still_active:
            return {"alive": False, "start_epoch_ms": None}
        creation = wintypes.FILETIME()
        ignored_exit = wintypes.FILETIME()
        ignored_kernel = wintypes.FILETIME()
        ignored_user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(ignored_exit),
                                        ctypes.byref(ignored_kernel), ctypes.byref(ignored_user)):
            return {"alive": True, "start_epoch_ms": None}
        windows_ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        # FILETIME is 100ns ticks from 1601-01-01; Unix milliseconds start in 1970.
        return {"alive": True, "start_epoch_ms": windows_ticks // 10_000 - 11_644_473_600_000}
    finally:
        kernel32.CloseHandle(handle)


def process_identity(pid: int, *, platform_name: str | None = None) -> ProcessIdentity:
    """Return a conservative private-child identity without terminating it."""
    if type(pid) is not int or pid <= 1:
        return {"alive": False, "start_epoch_ms": None}
    if (platform_name or os.name) == "nt":
        return _windows_process_identity(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {"alive": False, "start_epoch_ms": None}
    except PermissionError:
        return {"alive": True, "start_epoch_ms": None}
    return {"alive": True, "start_epoch_ms": None}


def terminate_process_tree(pid: int, *, timeout_s: float = 5.0, platform_name: str | None = None) -> bool:
    """Safely terminate a specific process and its descendants; verify exit within timeout.

    Returns True if the target process is confirmed dead; False if termination timed out.
    Does not kill unrelated processes or match by process name.
    """
    import time
    if type(pid) is not int or pid <= 1:
        return True

    is_win = (platform_name or os.name) == "nt"

    if is_win:
        import ctypes
        from ctypes import wintypes
        import subprocess

        # Use taskkill /PID <pid> /T /F to kill the specific process tree on Windows
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=timeout_s, check=False)
        except Exception:
            # Fallback to direct TerminateProcess
            process_terminate = 0x0001
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(process_terminate, False, pid)
            if handle:
                try:
                    kernel32.TerminateProcess(handle, 1)
                finally:
                    kernel32.CloseHandle(handle)

        # Wait and verify actual exit
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            ident = _windows_process_identity(pid)
            if not ident["alive"]:
                return True
            time.sleep(0.05)
        return not _windows_process_identity(pid)["alive"]

    # POSIX (macOS / Linux)
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        pass

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ident = process_identity(pid, platform_name="posix")
        if not ident["alive"]:
            return True
        # After 0.5s of SIGTERM, escalate to SIGKILL
        if time.monotonic() > (deadline - timeout_s + 0.5):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
        time.sleep(0.05)

    return not process_identity(pid, platform_name="posix")["alive"]


# Windows Process Creation Flags
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def is_process_in_job(pid: int | None = None) -> bool:
    """Check if the current process (or specified pid on Windows) is assigned to a Job Object."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    in_job = wintypes.BOOL()
    if kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
        return bool(in_job.value)
    return False


def validate_windows_path_security(path_str: str) -> None:
    """Enforce strict Windows filesystem boundaries (D06).

    Rejects:
    - Alternate Data Streams (ADS): path segments containing ':' (except standard drive letters like 'C:')
    - Reserved DOS device names: CON, PRN, AUX, NUL, COM1-9, LPT1-9
    - Trailing dots or spaces on path components
    - UNC network shares unless explicitly handled
    """
    import re
    from pathlib import PureWindowsPath

    # Check for UNC path
    if path_str.startswith(("\\\\", "//")):
        raise ValueError(f"UNC network paths are rejected: {path_str}")

    p = PureWindowsPath(path_str)
    parts = list(p.parts)
    if not parts:
        return

    # Check drive letter if present
    start_idx = 0
    if len(parts[0]) == 2 and parts[0][1] == ":" and parts[0][0].isalpha():
        start_idx = 1
    elif len(parts[0]) == 3 and parts[0][1:3] in (":\\", ":/"):
        start_idx = 1

    for part in parts[start_idx:]:
        # Alternate Data Stream check
        if ":" in part:
            raise ValueError(f"Alternate Data Stream (ADS) is rejected: {path_str}")
        # Trailing dots and spaces
        if part.endswith((".", " ")):
            raise ValueError(f"Trailing dot or space in path component is rejected: {path_str}")
        # Reserved DOS device names
        stem = part.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem):
            raise ValueError(f"Windows reserved device name is rejected: {path_str}")

