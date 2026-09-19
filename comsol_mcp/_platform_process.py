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
