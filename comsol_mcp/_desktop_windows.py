"""Windows COMSOL window identity observer; UI controls stay unsupported.

This adapter is intentionally read-only.  It discovers top-level COMSOL
process windows and binds their HWND to process creation time, logon SID,
interactive session, and executable version.  It does not drive controls,
capture pixels, or infer the active model from the window title.
"""
from __future__ import annotations

from typing import Any

from ._desktop_service import (
    BLOCKED_PERMISSION,
    UNSUPPORTED_CONTROL,
    DesktopOperationError,
    MetadataOnlyDesktopAdapter,
    WindowIdentity,
    WindowObservation,
)
from ._desktop_platforms import versioned_native_profile
from ._desktop_win32_identity import observe_process_birth_and_session


class WindowsMetadataAdapter(MetadataOnlyDesktopAdapter):
    """Read process/window birth identity using pywin32 on Windows only."""

    def status(self, runtime_id: str | None = None) -> dict[str, Any]:
        profile = versioned_native_profile("windows")
        if __import__("sys").platform != "win32":
            return {"platform": "windows", "permission_status": "UNKNOWN", "control_status": UNSUPPORTED_CONTROL,
                    "metadata_status": "WRONG_PLATFORM", **profile}
        return {"platform": "windows", "permission_status": "AVAILABLE", "control_status": UNSUPPORTED_CONTROL,
                "metadata_status": "AVAILABLE", "control_note": "metadata observer only; window controls are not implemented",
                **profile}

    def enumerate_windows(self, runtime_id: str | None = None) -> list[WindowObservation]:
        if __import__("sys").platform != "win32":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "Windows Desktop metadata is available only on Windows", category=UNSUPPORTED_CONTROL)
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
            import win32security
            import win32ts
        except ImportError as exc:
            raise DesktopOperationError("WINDOW_METADATA_UNAVAILABLE", "pywin32 is required for Windows process/window identity", category=UNSUPPORTED_CONTROL) from exc

        rows: list[WindowObservation] = []

        def visit(hwnd: int, _context: object) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return True
            process = None
            token = None
            try:
                process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                executable = win32process.QueryFullProcessImageName(process, 0)
                basename = executable.replace("/", "\\").rsplit("\\", 1)[-1].lower()
                if not basename.startswith("comsol") or "mphserver" in basename or basename == "comsolmphserver.exe":
                    return True
                process_birth, session_number = observe_process_birth_and_session(
                    win32process, win32ts, process, int(pid),
                )
                token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)
                token_user = win32security.GetTokenInformation(token, win32security.TokenUser)
                login_id = win32security.ConvertSidToStringSid(token_user[0])
                version_info = win32api.GetFileVersionInfo(executable, "\\")
                product_ms = int(version_info.get("ProductVersionMS", 0))
                product_ls = int(version_info.get("ProductVersionLS", 0))
                if product_ms or product_ls:
                    version = ".".join(str(part) for part in (
                        product_ms >> 16, product_ms & 0xFFFF, product_ls >> 16, product_ls & 0xFFFF,
                    ))
                else:
                    version = "UNVERIFIED"
                identity = WindowIdentity(
                    platform="windows",
                    native_window_id=f"HWND:{int(hwnd):x}",
                    process_id=int(pid),
                    process_birth=process_birth,
                    login_id=str(login_id),
                    desktop_session_id=f"WTS:{session_number}",
                    comsol_version=version,
                )
                rows.append(WindowObservation(
                    identity=identity,
                    title=win32gui.GetWindowText(hwnd) or "",
                    control_status=UNSUPPORTED_CONTROL,
                ))
            except Exception:
                # Incomplete process identity is not upgraded to a caller handle.
                return True
            finally:
                if token is not None:
                    try:
                        win32api.CloseHandle(token)
                    except Exception:
                        pass
                if process is not None:
                    try:
                        win32api.CloseHandle(process)
                    except Exception:
                        pass
            return True

        try:
            win32gui.EnumWindows(visit, None)
        except Exception as exc:
            raise DesktopOperationError("WINDOW_ENUMERATION_FAILED", f"Windows window enumeration failed: {type(exc).__name__}", category=UNSUPPORTED_CONTROL) from exc
        return rows
