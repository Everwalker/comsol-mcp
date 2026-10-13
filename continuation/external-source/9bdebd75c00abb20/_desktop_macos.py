"""macOS COMSOL window identity observer; Accessibility controls stay unsupported.

Quartz supplies the observed window/PID association, while psutil supplies
process birth and login-user identity.  Accessibility trust is inspected without
prompting.  This module never sends input or captures the screen.
"""
from __future__ import annotations

import os
from pathlib import Path
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


class MacOSMetadataAdapter(MetadataOnlyDesktopAdapter):
    """Read visible window and process identity through Quartz/psutil."""

    def status(self, runtime_id: str | None = None) -> dict[str, Any]:
        profile = versioned_native_profile("macos")
        if __import__("sys").platform != "darwin":
            return {"platform": "macos", "permission_status": "UNKNOWN", "control_status": UNSUPPORTED_CONTROL,
                    "metadata_status": "WRONG_PLATFORM", **profile}
        try:
            from ApplicationServices import AXIsProcessTrusted
            trusted = bool(AXIsProcessTrusted())
        except Exception:
            trusted = False
        return {
            "platform": "macos",
            "permission_status": "AVAILABLE" if trusted else BLOCKED_PERMISSION,
            "control_status": UNSUPPORTED_CONTROL,
            "metadata_status": "AVAILABLE",
            "accessibility_trusted": trusted,
            "control_note": "metadata observer only; no Accessibility control or screen capture is implemented",
            **profile,
        }

    def enumerate_windows(self, runtime_id: str | None = None) -> list[WindowObservation]:
        if __import__("sys").platform != "darwin":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "macOS Desktop metadata is available only on macOS", category=UNSUPPORTED_CONTROL)
        try:
            import psutil
            import Quartz
        except ImportError as exc:
            raise DesktopOperationError("WINDOW_METADATA_UNAVAILABLE", "Quartz and psutil are required for macOS process/window identity", category=UNSUPPORTED_CONTROL) from exc
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        ) or []
        rows: list[WindowObservation] = []
        for item in infos:
            pid = int(item.get(Quartz.kCGWindowOwnerPID, 0) or 0)
            owner = str(item.get(Quartz.kCGWindowOwnerName, "") or "")
            if pid <= 0 or "comsol" not in owner.lower():
                continue
            try:
                process = psutil.Process(pid)
                executable = process.exe()
                basename = Path(executable).name.lower()
                if "mphserver" in basename or "server" in basename:
                    continue
                birth = process.create_time()
                uid = process.uids().real
                desktop_session = os.getsid(pid)
                version = self._bundle_version(executable)
                if not version:
                    version = "UNVERIFIED"
                identity = WindowIdentity(
                    platform="macos",
                    native_window_id=f"CGWindow:{int(item.get(Quartz.kCGWindowNumber, 0))}",
                    process_id=pid,
                    process_birth=f"{birth:.6f}",
                    login_id=f"uid:{uid}",
                    desktop_session_id=f"process-session:{desktop_session}",
                    comsol_version=version,
                )
                rows.append(WindowObservation(
                    identity=identity,
                    title=str(item.get(Quartz.kCGWindowName, "") or ""),
                    control_status=UNSUPPORTED_CONTROL,
                ))
            except Exception:
                # A window without complete birth/session/version evidence gets
                # no lease and cannot be selected by its title alone.
                continue
        return rows

    @staticmethod
    def _bundle_version(executable: str) -> str | None:
        try:
            from Foundation import NSBundle
        except ImportError:
            return None
        candidate = Path(executable)
        bundle = next((parent for parent in candidate.parents if parent.suffix == ".app"), None)
        if bundle is None:
            return None
        loaded = NSBundle.bundleWithPath_(str(bundle))
        if loaded is None:
            return None
        info = loaded.infoDictionary() or {}
        value = info.objectForKey_("CFBundleShortVersionString") or info.objectForKey_("CFBundleVersion")
        return str(value) if value else None
