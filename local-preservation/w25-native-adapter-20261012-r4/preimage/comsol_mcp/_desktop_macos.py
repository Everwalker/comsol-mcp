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
        result = {
            "platform": "macos",
            "permission_status": "UNKNOWN",
            "control_status": UNSUPPORTED_CONTROL,
            "metadata_status": "UNAVAILABLE",
            "accessibility_trusted": None,
            "permission_scope": "ACTUAL_PRODUCT_CALLER_ONLY_NOT_CUA_CERTIFICATION",
            "metadata_qualification": "API availability only; process/window inventory not queried by status",
            "control_note": "metadata observer only; no Accessibility control or screen capture is implemented",
            **profile,
        }
        stage = "dependency_import"
        try:
            # Lazy imports: constructing the default adapter never loads these
            # frameworks. Status checks no windows, tree, pixels or prompt API.
            import ApplicationServices
            import Quartz
            import psutil
            from Foundation import NSBundle
            stage = "api_availability"
            required = (
                (ApplicationServices, "AXIsProcessTrusted"),
                (Quartz, "CGWindowListCopyWindowInfo"),
                (psutil, "Process"),
                (NSBundle, "bundleWithPath_"),
            )
            for provider, name in required:
                if not callable(getattr(provider, name, None)):
                    raise AttributeError(f"required metadata API unavailable: {name}")
            for name in ("kCGWindowListOptionOnScreenOnly", "kCGWindowListExcludeDesktopElements",
                         "kCGNullWindowID", "kCGWindowOwnerPID", "kCGWindowOwnerName",
                         "kCGWindowNumber", "kCGWindowName"):
                if not hasattr(Quartz, name):
                    raise AttributeError(f"required window metadata constant unavailable: {name}")
            stage = "nonprompt_accessibility_trust"
            trusted = bool(ApplicationServices.AXIsProcessTrusted())
        except Exception as exc:
            result["metadata_error"] = {
                "stage": stage, "type": type(exc).__name__, "message": str(exc),
            }
            return result
        result.update(
            permission_status="AVAILABLE" if trusted else BLOCKED_PERMISSION,
            metadata_status="AVAILABLE", accessibility_trusted=trusted,
        )
        return result

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


class MacOSFrameworkGateway:
    """Lazy product framework calls; this gateway never selects a COMSOL control.

    ``framework_loader`` is a host-owned software-test seam, not request data.
    There is deliberately no guessed Java Shell/menu/model selector profile.
    """

    def __init__(self, framework_loader=None):
        self._loader = framework_loader
        self._frameworks = None

    def _load(self):
        if self._frameworks is None:
            try:
                if self._loader is None:
                    import ApplicationServices
                    import Quartz
                    self._frameworks = (ApplicationServices, Quartz)
                else:
                    self._frameworks = self._loader()
            except ImportError as exc:
                raise DesktopOperationError("WINDOW_METADATA_UNAVAILABLE", "product runtime lacks PyObjC ApplicationServices/Quartz", safe_retry=True) from exc
        return self._frameworks

    def permissions(self):
        accessibility, quartz = self._load()
        try:
            trusted = bool(accessibility.AXIsProcessTrusted())
            capture_allowed = bool(quartz.CGPreflightScreenCaptureAccess())
        except AttributeError as exc:
            raise DesktopOperationError("WINDOW_METADATA_UNAVAILABLE", "product framework permission API unavailable", safe_retry=True) from exc
        return {
            "accessibility_trusted": trusted,
            "screen_capture_allowed": capture_allowed,
            "permission_scope": "ACTUAL_PRODUCT_CALLER_ONLY_NOT_CUA_CERTIFICATION",
        }

    def attribute(self, element, name):
        accessibility, _ = self._load()
        error, value = accessibility.AXUIElementCopyAttributeValue(element, name, None)
        if error != 0:
            raise DesktopOperationError("AX_ATTRIBUTE_UNAVAILABLE", f"Accessibility attribute unavailable: {name}", safe_retry=True)
        return value

    def _point(self, value, kind):
        accessibility, _ = self._load()
        ok, decoded = accessibility.AXValueGetValue(value, kind, None)
        if not ok:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "Accessibility window geometry unavailable", safe_retry=True)
        return decoded

    def target_window(self, identity):
        accessibility, quartz = self._load()
        if not self.permissions()["accessibility_trusted"]:
            raise DesktopOperationError("PERMISSION_REQUIRED", "product Accessibility permission unavailable", category=BLOCKED_PERMISSION, safe_retry=True)
        if not identity.native_window_id.startswith("CGWindow:"):
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "expected an observed Quartz window identity", safe_retry=True)
        number = int(identity.native_window_id.removeprefix("CGWindow:"))
        infos = quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionIncludingWindow, number) or []
        matches = [item for item in infos if item.get(quartz.kCGWindowNumber) == number and item.get(quartz.kCGWindowOwnerPID) == identity.process_id]
        if len(matches) != 1:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "Quartz owner/window is absent or ambiguous", safe_retry=True)
        info = matches[0]
        bounds = info.get(quartz.kCGWindowBounds, {})
        expected = (bounds.get("X"), bounds.get("Y"), bounds.get("Width"), bounds.get("Height"))
        if any(value is None for value in expected):
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "Quartz window bounds incomplete", safe_retry=True)
        app = accessibility.AXUIElementCreateApplication(identity.process_id)
        windows = self.attribute(app, "AXWindows") or []
        observed = []
        for window in windows:
            try:
                position = self._point(self.attribute(window, "AXPosition"), accessibility.kAXValueCGPointType)
                size = self._point(self.attribute(window, "AXSize"), accessibility.kAXValueCGSizeType)
                title = str(self.attribute(window, "AXTitle") or "")
                geometry = (position.x, position.y, size.width, size.height)
                if geometry == expected and title == str(info.get(quartz.kCGWindowName) or ""):
                    observed.append(window)
            except DesktopOperationError:
                continue
        if len(observed) != 1:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "CG/AX association is absent or ambiguous; no focused-window fallback", safe_retry=True)
        return observed[0]

    def window_tree(self, identity, *, max_depth=4, max_nodes=128):
        """Read only one associated window. Metadata is not a model binding."""
        if type(max_depth) is not int or not 0 <= max_depth <= 8 or type(max_nodes) is not int or not 1 <= max_nodes <= 512:
            raise DesktopOperationError("INVALID_REQUEST", "bounded AX inventory limits are invalid", safe_retry=True)
        root = self.target_window(identity)
        result = []
        omitted = False
        pending = [(root, 0, ())]
        while pending and len(result) < max_nodes:
            element, depth, path = pending.pop()
            attributes = {}
            for name in ("AXRole", "AXSubrole", "AXIdentifier", "AXTitle", "AXDescription", "AXEnabled"):
                try:
                    value = self.attribute(element, name)
                    if isinstance(value, (str, bool)):
                        attributes[name] = value[:512] if isinstance(value, str) else value
                except DesktopOperationError:
                    continue
            result.append({"path": list(path), "attributes": attributes})
            try:
                children = self.attribute(element, "AXChildren") or []
            except DesktopOperationError:
                children = []
            available = max(0, max_nodes - len(result) - len(pending))
            if children and depth >= max_depth:
                omitted = True
            elif depth < max_depth:
                # Never claim a complete tree after omitting children at either bound.
                omitted = omitted or len(children) > available
                for index in reversed(range(min(len(children), available))):
                    pending.append((children[index], depth + 1, path + (index,)))
        return {"nodes": result, "truncated": omitted or bool(pending), "scope": "TARGET_WINDOW_AX_METADATA_ONLY", "model_binding": "UNVERIFIED"}


class MacOSAccessibilityObserver(MacOSMetadataAdapter):
    """Product prerequisite provider only; all seven native controls stay unsupported."""

    def __init__(self, *, gateway=None, identity_observer=None):
        self.gateway = gateway if gateway is not None else MacOSFrameworkGateway()
        self.identity_observer = identity_observer

    def status(self, runtime_id=None):
        profile = versioned_native_profile("macos")
        try:
            permissions = self.gateway.permissions()
        except DesktopOperationError as exc:
            return {"platform": "macos", "permission_status": "UNKNOWN", "control_status": UNSUPPORTED_CONTROL,
                    "metadata_status": "UNAVAILABLE", "product_observer_error": exc.code, **profile}
        return {"platform": "macos", "permission_status": "AVAILABLE" if permissions["accessibility_trusted"] else BLOCKED_PERMISSION,
                "control_status": UNSUPPORTED_CONTROL, "metadata_status": "AVAILABLE", **permissions, **profile,
                "source_prerequisite_provider": "LAZY_PRODUCT_AX_QUARTZ_READ_ONLY_IMPLEMENTED",
                "selector_profile": "UNOBSERVED", "model_binding": "UNVERIFIED"}

    def enumerate_windows(self, runtime_id=None):
        if self.identity_observer is not None:
            return list(self.identity_observer.enumerate_windows(runtime_id))
        return super().enumerate_windows(runtime_id)

    def inspect_accessibility(self, identity, *, max_depth=4, max_nodes=128):
        if self.revalidate_window(identity) is None:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "target process/window identity changed before AX observation", safe_retry=True)
        result = self.gateway.window_tree(identity, max_depth=max_depth, max_nodes=max_nodes)
        if self.revalidate_window(identity) is None:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "target process/window identity changed during AX observation", safe_retry=True)
        return {**result, "window_identity_fingerprint": identity.fingerprint(), "native_evidence": "NOT_RUN"}
