"""macOS COMSOL observer, exact AX selectors and ScreenCaptureKit capture.

Quartz supplies observed window/PID association, psutil supplies process
birth/session identity, observed AX selectors gate controls, and ScreenCaptureKit
captures only the exact target window.  COMSOL Java Shell remains Windows-only.
"""
from __future__ import annotations

import os
import hashlib
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import threading
import sys
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ._desktop_service import (
    BLOCKED_PERMISSION,
    UNSUPPORTED_CONTROL,
    DesktopOperationError,
    MetadataOnlyDesktopAdapter,
    WindowIdentity,
    WindowObservation,
)
from ._desktop_platforms import versioned_native_profile
from ._desktop_selector_profile import load_selector_profile


AX_MESSAGING_TIMEOUT_SECONDS = 5.0
AX_SELECTOR_SEARCH_BUDGET_SECONDS = 30.0
AX_TREE_READ_BUDGET_SECONDS = 30.0
AX_SELECTOR_ATTRIBUTE_NAMES_LIMIT = 256
MAX_TASK_OWNED_QUARTZ_WINDOWS_PER_INVENTORY = 16


def _require_screencapturekit_14() -> None:
    """SCScreenshotManager is a macOS 14 API; fail before framework calls."""
    release = platform.mac_ver()[0]
    match = re.match(r"^(\d+)\.(\d+)(?:\.|$)", release) if isinstance(release, str) else None
    if not match or tuple(map(int, match.groups())) < (14, 0):
        raise DesktopOperationError(
            "SCREEN_CAPTURE_API_UNAVAILABLE",
            "SCScreenshotManager window capture requires a readable macOS 14.0 or newer release",
            safe_retry=True,
        )


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


def macos_process_command_sha256(executable: str, argv: Sequence[str]) -> str:
    """Hash the exact observed executable path and argv without retaining either."""
    if not isinstance(executable, str) or not executable.startswith("/") or \
            not isinstance(argv, Sequence) or isinstance(argv, (str, bytes)) or \
            not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("process command identity must contain an absolute executable and string argv")
    raw = json.dumps([executable, list(argv)], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskOwnedMacOSProcessIdentity:
    """Frozen supervisor observation for one member of a task-owned process tree.

    The launcher actor must create these rows from the exact Popen process tree
    after recording process birth, UID, session/group, executable, and command
    digest. ``bundle_identifier`` and ``comsol_version`` bind descendants to the
    verified COMSOL application bundle that created the tree.
    """

    process_id: int
    process_birth: str
    real_uid: int
    parent_pid: int
    process_group_id: int
    session_id: int
    executable_path: str
    command_sha256: str
    bundle_identifier: str
    comsol_version: str

    def __post_init__(self) -> None:
        if isinstance(self.process_id, bool) or not isinstance(self.process_id, int) or self.process_id <= 0:
            raise ValueError("owned process PID must be positive")
        if not isinstance(self.process_birth, str) or not re.fullmatch(r"[0-9]+\.[0-9]{6}", self.process_birth):
            raise ValueError("owned process birth must be a six-decimal process start time")
        for name in ("real_uid", "parent_pid", "process_group_id", "session_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"owned process {name} must be a nonnegative integer")
        if self.parent_pid <= 0 or self.process_group_id <= 0 or self.session_id <= 0:
            raise ValueError("owned process parent/group/session identity is incomplete")
        if not isinstance(self.executable_path, str) or not self.executable_path.startswith("/"):
            raise ValueError("owned process executable path must be absolute")
        if not isinstance(self.command_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.command_sha256):
            raise ValueError("owned process command digest must be lowercase SHA-256")
        if self.bundle_identifier != "com.comsol.comsol":
            raise ValueError("owned process is not bound to the COMSOL Multiphysics bundle")
        if not isinstance(self.comsol_version, str) or not self.comsol_version or self.comsol_version == "UNVERIFIED":
            raise ValueError("owned process COMSOL bundle version is unverified")


class TaskOwnedMacOSWindowObserver(MacOSMetadataAdapter):
    """PID/birth-pinned window observer for a single supervised COMSOL launch.

    Construction binds to the exact live Popen child retained by this Python
    supervisor; an integer supervisor PID or serialized launch descriptor is
    not an ownership token. That same handle must remain live through inventory.
    Quartz returns a session-wide list, so this observer reads only OwnerPID for
    foreign rows and discards them immediately. It reads WindowNumber only for
    a process row whose live birth/UID/session/group/executable/command digest
    still matches the launch supervisor's frozen ownership record. Titles,
    bounds, owner names, AX trees, and pixels are never requested here.
    """

    def __init__(self, owned_processes: Sequence[TaskOwnedMacOSProcessIdentity], *, original_root: subprocess.Popen):
        if sys.platform != "darwin":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "task-owned macOS window identity is available only on macOS", category=UNSUPPORTED_CONTROL)
        if type(original_root) is not subprocess.Popen:
            raise ValueError("task-owned observation requires the original Popen root handle")
        root_pid = original_root.pid
        if isinstance(root_pid, bool) or not isinstance(root_pid, int) or root_pid <= 0:
            raise ValueError("original Popen root PID must be positive")
        supervisor_pid = os.getpid()
        self._original_root = original_root
        self._root_pid = root_pid
        self._supervisor_pid = supervisor_pid
        self._assert_original_root_live()
        if not isinstance(owned_processes, Sequence) or isinstance(owned_processes, (str, bytes)) or \
                not 1 <= len(owned_processes) <= 64 or \
                any(not isinstance(row, TaskOwnedMacOSProcessIdentity) for row in owned_processes):
            raise ValueError("owned process snapshot must contain 1 to 64 typed rows")
        by_pid = {row.process_id: row for row in owned_processes}
        if len(by_pid) != len(owned_processes):
            raise ValueError("owned process snapshot contains duplicate PIDs")
        if supervisor_pid in by_pid:
            raise ValueError("supervisor PID cannot be a child in its owned process snapshot")
        root = by_pid.get(root_pid)
        if root is None or root.parent_pid != supervisor_pid:
            raise ValueError("owned process snapshot must match the original Popen root spawned by this supervisor")
        if sum(row.parent_pid == supervisor_pid for row in owned_processes) != 1:
            raise ValueError("owned process snapshot must have exactly one direct supervisor child")
        if root.process_group_id != root.process_id or root.session_id != root.process_id:
            raise ValueError("owned Popen root must own its isolated process group and session")
        for row in owned_processes:
            if row.real_uid != os.getuid() or row.process_group_id != root.process_group_id or \
                    row.session_id != root.session_id or \
                    (row.process_id != root.process_id and row.parent_pid not in by_pid):
                raise ValueError("owned process snapshot has mismatched UID, group, session, or parent")
            if row.process_id == root.process_id:
                continue
            ancestors = {row.process_id}
            parent_pid = row.parent_pid
            while parent_pid != root.process_id:
                if parent_pid in ancestors or parent_pid not in by_pid:
                    raise ValueError("owned process snapshot has a cycle or an unrooted descendant")
                ancestors.add(parent_pid)
                parent_pid = by_pid[parent_pid].parent_pid
        self._owned_processes = by_pid
        self._assert_original_root_live()

    def _assert_original_root_live(self) -> None:
        """Retain and require the same live child handle through observation."""
        try:
            if type(self._original_root) is not subprocess.Popen or \
                    self._original_root.pid != self._root_pid or self._original_root.poll() is not None:
                raise DesktopOperationError(
                    "WINDOW_IDENTITY_UNKNOWN", "original task-owned Popen root is terminal or changed", safe_retry=True,
                )
        except DesktopOperationError:
            raise
        except Exception as exc:
            raise DesktopOperationError(
                "WINDOW_IDENTITY_UNKNOWN", "original task-owned Popen handle is unavailable", safe_retry=True,
            ) from exc

    @staticmethod
    def _process_record(pid: int):
        try:
            import psutil
            return psutil.Process(pid)
        except Exception as exc:
            raise DesktopOperationError(
                "WINDOW_IDENTITY_UNKNOWN", "task-owned process identity is unavailable", safe_retry=True,
            ) from exc

    def _assert_process_current(self, expected: TaskOwnedMacOSProcessIdentity) -> None:
        """Fail closed before reading window metadata if any process identity drifted."""
        process = self._process_record(expected.process_id)
        try:
            birth = f"{process.create_time():.6f}"
            real_uid = process.uids().real
            parent_pid = process.ppid()
            executable = process.exe()
            argv = process.cmdline()
            process_group_id = os.getpgid(expected.process_id)
            session_id = os.getsid(expected.process_id)
        except Exception as exc:
            raise DesktopOperationError(
                "WINDOW_IDENTITY_UNKNOWN", "task-owned process identity could not be revalidated", safe_retry=True,
            ) from exc
        if birth != expected.process_birth or real_uid != expected.real_uid or \
                parent_pid != expected.parent_pid or process_group_id != expected.process_group_id or \
                session_id != expected.session_id or executable != expected.executable_path or \
                macos_process_command_sha256(executable, argv) != expected.command_sha256:
            raise DesktopOperationError(
                "WINDOW_IDENTITY_UNKNOWN", "task-owned process birth, UID, command, or session changed", safe_retry=True,
            )

    def enumerate_windows(self, runtime_id: str | None = None) -> list[WindowObservation]:
        rows = self._collect_owned_window_observations(runtime_id)
        if len(rows) > 1:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "task-owned COMSOL process tree has multiple visible windows", safe_retry=True)
        return rows

    def _collect_owned_window_observations(self, runtime_id: str | None = None) -> list[WindowObservation]:
        """Collect owned Quartz identity rows for the bounded task actor only.

        This metadata-only internal path preserves the original Popen root and
        full process validation on both sides of the inventory. Public window
        enumeration remains strict and refuses more than one owned row; callers
        of this internal collector may persist ambiguity, but must not select or
        access AX while it is present.
        """
        self._assert_original_root_live()
        try:
            return self._enumerate_windows_for_live_root(runtime_id)
        finally:
            # The exact Popen object is held by this observer. A process exit
            # during inventory invalidates the observation; no PID takeover is
            # adopted or reconstructed.
            self._assert_original_root_live()

    def _enumerate_windows_for_live_root(self, runtime_id: str | None = None) -> list[WindowObservation]:
        if __import__("sys").platform != "darwin":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "macOS Desktop metadata is available only on macOS", category=UNSUPPORTED_CONTROL)
        try:
            import Quartz
        except ImportError as exc:
            raise DesktopOperationError("WINDOW_METADATA_UNAVAILABLE", "Quartz is required for task-owned macOS window identity", category=UNSUPPORTED_CONTROL) from exc

        for expected in self._owned_processes.values():
            self._assert_process_current(expected)
        infos = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        ) or []
        rows: list[WindowObservation] = []
        seen_window_ids: set[int] = set()
        for item in infos:
            # OwnerPID is the sole property read for a foreign window row.
            try:
                raw_pid = item.get(Quartz.kCGWindowOwnerPID, 0)
                if isinstance(raw_pid, bool) or not isinstance(raw_pid, int):
                    continue
                expected = self._owned_processes.get(raw_pid)
                if expected is None:
                    continue
                self._assert_process_current(expected)
                raw_window_id = item.get(Quartz.kCGWindowNumber, 0)
                if isinstance(raw_window_id, bool) or not isinstance(raw_window_id, int) or raw_window_id <= 0:
                    raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "owned task window handle is malformed", safe_retry=True)
                if raw_window_id in seen_window_ids:
                    raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "owned task window handle is ambiguous", safe_retry=True)
                seen_window_ids.add(raw_window_id)
                if len(rows) >= MAX_TASK_OWNED_QUARTZ_WINDOWS_PER_INVENTORY:
                    raise DesktopOperationError(
                        "WINDOW_IDENTITY_UNKNOWN",
                        "task-owned Quartz inventory exceeds the fixed retained-row cap",
                        safe_retry=True,
                    )
                identity = WindowIdentity(
                    platform="macos",
                    native_window_id=f"CGWindow:{raw_window_id}",
                    process_id=expected.process_id,
                    process_birth=expected.process_birth,
                    login_id=f"uid:{expected.real_uid}",
                    desktop_session_id=f"process-session:{expected.session_id}",
                    comsol_version=expected.comsol_version,
                )
                rows.append(WindowObservation(identity=identity, title="", control_status=UNSUPPORTED_CONTROL))
            except DesktopOperationError:
                raise
            except Exception as exc:
                raise DesktopOperationError(
                    "WINDOW_IDENTITY_UNKNOWN", "task-owned Quartz window identity is incomplete", safe_retry=True,
                ) from exc
        for expected in self._owned_processes.values():
            self._assert_process_current(expected)
        return rows


class MacOSFrameworkGateway:
    """Lazy product framework calls; this gateway never selects a COMSOL control.

    ``framework_loader`` is a host-owned software-test seam, not request data.
    There is deliberately no guessed Java Shell/menu/model selector profile.
    """

    def __init__(self, framework_loader=None, capture_framework_loader=None):
        self._loader = framework_loader
        self._capture_loader = capture_framework_loader
        self._frameworks = None
        self._capture_frameworks = None

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

    def _load_capture(self):
        if self._capture_frameworks is None:
            try:
                if self._capture_loader is None:
                    import AppKit
                    import ScreenCaptureKit
                    self._capture_frameworks = (AppKit, ScreenCaptureKit)
                else:
                    loaded = self._capture_loader()
                    if isinstance(loaded, (tuple, list)) and len(loaded) == 2:
                        self._capture_frameworks = tuple(loaded)
                    else:
                        raise TypeError("capture framework loader must return AppKit and ScreenCaptureKit")
            except ImportError as exc:
                raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit and AppKit are required for exact-window capture", safe_retry=True) from exc
            except DesktopOperationError:
                raise
            except Exception as exc:
                raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit/AppKit loader did not return the exact framework pair", safe_retry=True) from exc
        return self._capture_frameworks

    @staticmethod
    def _wait_callback(start, *, timeout_s=15.0):
        complete = threading.Event()
        result = {"accepting": True, "value": None, "error": None}
        result_lock = threading.Lock()

        def callback(value, error):
            with result_lock:
                if not result["accepting"]:
                    return
                result["value"] = value
                result["error"] = error
                complete.set()

        start(callback)
        if not complete.wait(timeout_s):
            # ScreenCaptureKit does not expose cancellation for these callback
            # requests. Stop retaining any late image/error and leave the
            # already-dispatched capture outcome terminal/uncertain.
            with result_lock:
                result["accepting"] = False
                result["value"] = None
                result["error"] = None
            raise DesktopOperationError("SCREEN_CAPTURE_TIMEOUT", "ScreenCaptureKit completion callback exceeded its bounded deadline; late results are discarded", safe_retry=False)
        with result_lock:
            result["accepting"] = False
            value, error = result["value"], result["error"]
            result["value"] = None
            result["error"] = None
        if error is not None:
            raise DesktopOperationError("SCREEN_CAPTURE_FAILED", "ScreenCaptureKit reported a capture error", safe_retry=True)
        return value

    def capture_window(self, identity):
        """Capture the exact observed SCWindow, never a display or desktop."""
        _require_screencapturekit_14()
        try:
            window_id = int(identity.native_window_id.removeprefix("CGWindow:"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise DesktopOperationError("WINDOW_IDENTITY_REJECTED", "ScreenCaptureKit requires a canonical observed CGWindow identity", safe_retry=True) from exc
        if identity.platform != "macos" or window_id <= 0:
            raise DesktopOperationError("WINDOW_IDENTITY_REJECTED", "ScreenCaptureKit requires the exact observed CGWindow identity", safe_retry=True)

        def call_getter(receiver, name, *, code="WINDOW_IDENTITY_UNKNOWN"):
            getter = getattr(receiver, name, None)
            if not callable(getter):
                raise DesktopOperationError(code, f"ScreenCaptureKit {name} accessor is unavailable", safe_retry=True)
            try:
                value = getter()
            except Exception as exc:
                raise DesktopOperationError(code, f"ScreenCaptureKit {name} accessor failed", safe_retry=True) from exc
            if value is None:
                raise DesktopOperationError(code, f"ScreenCaptureKit {name} accessor returned no value", safe_retry=True)
            return value

        appkit, sc = self._load_capture()
        shareable_class = getattr(sc, "SCShareableContent", None)
        list_method = getattr(shareable_class, "getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_", None)
        if not callable(list_method):
            raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit shareable-window API is unavailable", safe_retry=True)
        content = self._wait_callback(lambda callback: list_method(False, True, callback))
        if content is None:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "ScreenCaptureKit returned no shareable content", safe_retry=True)
        try:
            windows = list(call_getter(content, "windows"))
        except TypeError as exc:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "ScreenCaptureKit windows accessor did not return a sequence", safe_retry=True) from exc
        if not windows:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "ScreenCaptureKit returned no shareable windows", safe_retry=True)
        matches = []
        for candidate in windows:
            observed_id = call_getter(candidate, "windowID")
            application = call_getter(candidate, "owningApplication")
            observed_pid = call_getter(application, "processID")
            if (isinstance(observed_id, bool) or not isinstance(observed_id, int) or observed_id <= 0
                    or isinstance(observed_pid, bool) or not isinstance(observed_pid, int) or observed_pid <= 0):
                raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "ScreenCaptureKit returned malformed window or process identity", safe_retry=True)
            if observed_id == window_id and observed_pid == identity.process_id:
                matches.append(candidate)
        if len(matches) != 1:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "ScreenCaptureKit window ID and owning PID are absent or ambiguous", safe_retry=True)
        window = matches[0]
        filter_class = getattr(sc, "SCContentFilter", None)
        config_class = getattr(sc, "SCStreamConfiguration", None)
        manager = getattr(sc, "SCScreenshotManager", None)
        init_filter = getattr(filter_class, "alloc", None)
        if not callable(init_filter) or not callable(getattr(config_class, "alloc", None)):
            raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit window filter/configuration constructors are unavailable", safe_retry=True)
        try:
            content_filter = filter_class.alloc().initWithDesktopIndependentWindow_(window)
            configuration = config_class.alloc().init()
            frame = call_getter(window, "frame", code="SCREEN_CAPTURE_API_UNAVAILABLE")
            size = getattr(frame, "size", None)
            if size is None:
                raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit frame has no size", safe_retry=True)
            width_value, height_value = getattr(size, "width", None), getattr(size, "height", None)
            if (isinstance(width_value, bool) or not isinstance(width_value, (int, float))
                    or isinstance(height_value, bool) or not isinstance(height_value, (int, float))):
                raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit frame dimensions are malformed", safe_retry=True)
            width_float, height_float = float(width_value), float(height_value)
            if not (math.isfinite(width_float) and math.isfinite(height_float)):
                raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit frame dimensions are non-finite", safe_retry=True)
            width, height = int(round(width_float)), int(round(height_float))
            if not (1 <= width <= 8192 and 1 <= height <= 8192 and width * height <= 12_000_000):
                raise DesktopOperationError("CAPTURE_OUTPUT_REJECTED", "ScreenCaptureKit window frame exceeds image bounds", safe_retry=True)
            configuration.setWidth_(width)
            configuration.setHeight_(height)
        except DesktopOperationError:
            raise
        except Exception as exc:
            raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "ScreenCaptureKit window filter/configuration could not be prepared", safe_retry=True) from exc
        capture = getattr(manager, "captureImageWithFilter_configuration_completionHandler_", None)
        if not callable(capture):
            raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "SCScreenshotManager exact-window image API is unavailable", safe_retry=True)
        cg_image = self._wait_callback(lambda callback: capture(content_filter, configuration, callback))
        if cg_image is None:
            raise DesktopOperationError("SCREEN_CAPTURE_FAILED", "ScreenCaptureKit returned no window image", safe_retry=True)
        bitmap_class = getattr(appkit, "NSBitmapImageRep", None)
        png_type = getattr(appkit, "NSBitmapImageFileTypePNG", None)
        if bitmap_class is None or png_type is None:
            raise DesktopOperationError("SCREEN_CAPTURE_API_UNAVAILABLE", "AppKit PNG image encoder is unavailable", safe_retry=True)
        try:
            bitmap = bitmap_class.alloc().initWithCGImage_(cg_image)
            encoded = bitmap.representationUsingType_properties_(png_type, {})
            data = bytes(encoded)
        except Exception as exc:
            raise DesktopOperationError("SCREEN_CAPTURE_FAILED", "captured CGImage could not be encoded to PNG", side_effect_started=True, safe_retry=False) from exc
        return {"data": data, "width": width, "height": height,
                "capture_api": "SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_",
                "window_id": window_id, "process_id": identity.process_id}

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

    @staticmethod
    def _ax_error_name(code):
        return {
            -25200: "failure",
            -25201: "illegal_argument",
            -25202: "invalid_ui_element",
            -25203: "invalid_ui_element_observer",
            -25204: "cannot_complete",
            -25205: "attribute_unsupported",
            -25206: "action_unsupported",
            -25207: "notification_unsupported",
            -25208: "not_implemented",
            -25209: "notification_already_registered",
            -25210: "notification_not_registered",
            -25211: "api_disabled",
            -25212: "no_value",
            -25213: "parameterized_attribute_unsupported",
            -25214: "not_enough_precision",
        }.get(code, "unknown")

    def _set_ax_messaging_timeout(self, element, *, timeout_s=None):
        """Bound one exact AX element IPC; never use the global/system timeout."""
        accessibility, _ = self._load()
        setter = getattr(accessibility, "AXUIElementSetMessagingTimeout", None)
        if not callable(setter):
            raise DesktopOperationError(
                "AX_TIMEOUT_UNAVAILABLE",
                "Accessibility runtime lacks the per-element messaging timeout API",
                safe_retry=True,
            )
        timeout = AX_MESSAGING_TIMEOUT_SECONDS if timeout_s is None else min(
            float(timeout_s), AX_MESSAGING_TIMEOUT_SECONDS
        )
        if not math.isfinite(timeout) or timeout <= 0:
            raise DesktopOperationError("AX_OPERATION_TIMEOUT", "Accessibility operation budget expired", safe_retry=True)
        try:
            result = setter(element, float(timeout))
        except Exception as exc:
            raise DesktopOperationError(
                "AX_TIMEOUT_UNAVAILABLE",
                "Accessibility per-element messaging timeout could not be configured",
                safe_retry=True,
            ) from exc
        if isinstance(result, bool) or not isinstance(result, int) or result != 0:
            raise DesktopOperationError(
                "AX_TIMEOUT_UNAVAILABLE",
                "Accessibility rejected the per-element messaging timeout",
                safe_retry=True,
                diagnostics={"kind": "AX_TIMEOUT_SETUP", "ax_error_code": result if isinstance(result, int) else None},
            )

    def attribute(self, element, name, *, scope="owned_window_tree", timeout_s=None):
        accessibility, _ = self._load()
        self._set_ax_messaging_timeout(element, timeout_s=timeout_s)
        error, value = accessibility.AXUIElementCopyAttributeValue(element, name, None)
        if error != 0:
            try:
                code = int(error)
            except (TypeError, ValueError, OverflowError):
                code = None
            # Only narrowly scoped readiness reads can be transient. The
            # caller still has to revalidate the exact owned Quartz window
            # and original process identity before every retry.
            readiness_attribute = (
                (name == "AXWindows" and scope == "application_window_map")
                or (name in {"AXPosition", "AXSize"} and scope == "window_association")
            )
            readiness_transient = readiness_attribute and code in {-25204, -25212}
            error_name = self._ax_error_name(code) if code is not None else "malformed"
            diagnostics = {
                "kind": "AX_ERROR",
                "ax_error_code": code,
                "ax_error_name": error_name,
                "attribute": str(name),
                "scope": str(scope),
                "readiness_transient": readiness_transient,
            }
            raise DesktopOperationError(
                "AX_ATTRIBUTE_TRANSIENT" if readiness_transient else "AX_ATTRIBUTE_UNAVAILABLE",
                f"Accessibility attribute read failed ({error_name}, {code}): {name} in {scope}",
                safe_retry=False,
                diagnostics=diagnostics,
            )
        return value

    def supported_attribute_names(self, element, *, timeout_s=None):
        """Return a small, validated AX attribute-name list for one element."""
        accessibility, _ = self._load()
        copier = getattr(accessibility, "AXUIElementCopyAttributeNames", None)
        if not callable(copier):
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_UNAVAILABLE",
                "Accessibility runtime lacks the per-element attribute-name API",
                safe_retry=False,
            )
        self._set_ax_messaging_timeout(element, timeout_s=timeout_s)
        try:
            result = copier(element, None)
        except Exception as exc:
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_UNAVAILABLE",
                "Accessibility attribute names could not be read from the exact element",
                safe_retry=False,
            ) from exc
        if not isinstance(result, (tuple, list)) or len(result) != 2:
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_MALFORMED",
                "Accessibility returned a malformed attribute-name result",
                safe_retry=False,
            )
        error, names = result
        if isinstance(error, bool) or not isinstance(error, int) or error != 0:
            code = error if isinstance(error, int) and not isinstance(error, bool) else None
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_UNAVAILABLE",
                "Accessibility could not prove the exact element's supported attributes",
                safe_retry=False,
                diagnostics={"kind": "AX_ERROR", "ax_error_code": code,
                             "ax_error_name": self._ax_error_name(code) if code is not None else "malformed",
                             "scope": "selector_search"},
            )
        if names is None or isinstance(names, (str, bytes, bytearray, Mapping)) or not callable(getattr(names, "__getitem__", None)):
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_MALFORMED",
                "Accessibility attribute names are absent, malformed, duplicated, or over the fixed bound",
                safe_retry=False,
                diagnostics={"scope": "selector_search", "attribute_name_limit": AX_SELECTOR_ATTRIBUTE_NAMES_LIMIT},
            )
        try:
            name_count = len(names)
            if name_count > AX_SELECTOR_ATTRIBUTE_NAMES_LIMIT:
                raise ValueError("attribute-name array exceeds its fixed bound")
            normalized_names = tuple(names[index] for index in range(name_count))
        except Exception as exc:
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_MALFORMED",
                "Accessibility attribute names could not be read as a bounded array",
                safe_retry=False,
                diagnostics={"scope": "selector_search", "attribute_name_limit": AX_SELECTOR_ATTRIBUTE_NAMES_LIMIT},
            ) from exc
        if (any(not isinstance(name, str) or not name or "\x00" in name for name in normalized_names)
                or len(set(normalized_names)) != len(normalized_names)):
            raise DesktopOperationError(
                "AX_ATTRIBUTE_NAMES_MALFORMED",
                "Accessibility attribute names contain malformed or duplicate entries",
                safe_retry=False,
                diagnostics={"scope": "selector_search", "attribute_name_limit": AX_SELECTOR_ATTRIBUTE_NAMES_LIMIT},
            )
        return normalized_names

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
        if not self._valid_window_geometry(expected):
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "Quartz window bounds incomplete", safe_retry=True)
        app = accessibility.AXUIElementCreateApplication(identity.process_id)
        windows = self.attribute(app, "AXWindows", scope="application_window_map") or []
        observed = []
        for window in windows:
            try:
                position = self._point(self.attribute(window, "AXPosition", scope="window_association"), accessibility.kAXValueCGPointType)
                size = self._point(self.attribute(window, "AXSize", scope="window_association"), accessibility.kAXValueCGSizeType)
                title = str(self.attribute(window, "AXTitle", scope="window_association") or "")
                geometry = (position.x, position.y, size.width, size.height)
                if not self._valid_window_geometry(geometry):
                    raise DesktopOperationError(
                        "WINDOW_IDENTITY_UNKNOWN", "Accessibility window geometry is empty or malformed",
                        safe_retry=False,
                        diagnostics={"scope": "window_association", "attribute": "AXPosition/AXSize"},
                    )
                if geometry == expected and title == str(info.get(quartz.kCGWindowName) or ""):
                    observed.append(window)
            except DesktopOperationError as exc:
                # Unsupported/disabled AX errors are terminal evidence. Do not
                # hide them as an absent window or retry a broader inventory.
                if exc.diagnostics.get("kind") == "AX_ERROR":
                    raise
                continue
        if len(observed) != 1:
            raise DesktopOperationError("WINDOW_IDENTITY_UNKNOWN", "CG/AX association is absent or ambiguous; no focused-window fallback", safe_retry=True)
        return observed[0]

    @staticmethod
    def _valid_window_geometry(geometry):
        """Require finite coordinates and positive dimensions for CG/AX association."""
        import math

        if not isinstance(geometry, (tuple, list)) or len(geometry) != 4:
            return False
        values = []
        for value in geometry:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            number = float(value)
            if not math.isfinite(number):
                return False
            values.append(number)
        return values[2] > 0 and values[3] > 0

    def window_tree(self, identity, *, max_depth=4, max_nodes=128):
        """Read only one associated window. Metadata is not a model binding."""
        if type(max_depth) is not int or not 0 <= max_depth <= 8 or type(max_nodes) is not int or not 1 <= max_nodes <= 512:
            raise DesktopOperationError("INVALID_REQUEST", "bounded AX inventory limits are invalid", safe_retry=True)
        root = self.target_window(identity)
        deadline = time.monotonic() + AX_TREE_READ_BUDGET_SECONDS

        def read_attribute(element, name):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopOperationError("AX_TREE_TIMEOUT", "bounded AX metadata read exceeded its total time budget", safe_retry=True)
            return self.attribute(element, name, scope="owned_window_tree", timeout_s=remaining)

        result = []
        omitted = False
        pending = [(root, 0, ())]
        while pending and len(result) < max_nodes:
            element, depth, path = pending.pop()
            attributes = {}
            attribute_observations = {}
            for name in ("AXRole", "AXSubrole", "AXIdentifier", "AXTitle", "AXDescription", "AXEnabled"):
                try:
                    value = read_attribute(element, name)
                    if isinstance(value, (str, bool)):
                        attributes[name] = value[:512] if isinstance(value, str) else value
                except DesktopOperationError as exc:
                    diagnostics = exc.diagnostics
                    if not (diagnostics.get("kind") == "AX_ERROR"
                            and diagnostics.get("ax_error_code") in {-25205, -25212}):
                        raise
                    code = diagnostics["ax_error_code"]
                    attribute_observations[name] = {
                        "status": "ATTRIBUTE_UNSUPPORTED" if code == -25205 else "NO_VALUE",
                        "ax_error_code": code,
                        "ax_error_name": diagnostics.get("ax_error_name"),
                        "scope": "owned_window_tree",
                    }
            node = {"path": list(path), "attributes": attributes,
                    "attribute_observations": attribute_observations}
            try:
                children = read_attribute(element, "AXChildren") or []
            except DesktopOperationError as exc:
                diagnostics = exc.diagnostics
                if not (diagnostics.get("kind") == "AX_ERROR"
                        and diagnostics.get("ax_error_code") in {-25205, -25212}):
                    raise
                children = []
                code = diagnostics["ax_error_code"]
                node["children_observation"] = {
                    "status": "ATTRIBUTE_UNSUPPORTED" if code == -25205 else "NO_VALUE",
                    "ax_error_code": code,
                    "ax_error_name": diagnostics.get("ax_error_name"),
                    "scope": "owned_window_tree",
                }
                # The metadata interface did not prove whether descendants
                # exist, so this tree cannot support completeness claims.
                omitted = True
            else:
                node["children_observation"] = {"status": "OBSERVED", "child_count": len(children)}
            result.append(node)
            available = max(0, max_nodes - len(result) - len(pending))
            if children and depth >= max_depth:
                omitted = True
            elif depth < max_depth:
                # Never claim a complete tree after omitting children at either bound.
                omitted = omitted or len(children) > available
                for index in reversed(range(min(len(children), available))):
                    pending.append((children[index], depth + 1, path + (index,)))
        return {"nodes": result, "truncated": omitted or bool(pending), "scope": "TARGET_WINDOW_AX_METADATA_ONLY", "model_binding": "UNVERIFIED"}

    def _find_exact_selector(self, identity, selector):
        if selector.get("kind") != "ax":
            raise DesktopOperationError("DESKTOP_PROFILE_MISMATCH", "macOS adapter received a non-AX selector", safe_retry=True)
        root = self.target_window(identity)
        properties = selector.get("properties", {})
        if (not isinstance(properties, Mapping)
                or not {"identifier", "role"} <= set(properties)
                or set(properties) - {"identifier", "role", "subrole", "title"}):
            raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "AX selector properties are malformed", safe_retry=True)
        matches = []
        pending = [root]
        seen = 0
        property_attributes = {"identifier": "AXIdentifier", "role": "AXRole",
                              "subrole": "AXSubrole", "title": "AXTitle"}
        required = {"identifier", "role"}

        def read_attribute(element, name):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopOperationError("DESKTOP_SELECTOR_TIMEOUT", "AX selector search exceeded its total time budget", safe_retry=True)
            return self.attribute(element, name, scope="selector_search", timeout_s=remaining)

        deadline = time.monotonic() + AX_SELECTOR_SEARCH_BUDGET_SECONDS
        while pending and seen < 4096:
            element = pending.pop()
            seen += 1
            observed = {}
            unavailable = set()
            for key in properties:
                try:
                    observed[key] = read_attribute(element, property_attributes[key])
                except DesktopOperationError as exc:
                    diagnostics = exc.diagnostics
                    if (diagnostics.get("kind") == "AX_ERROR"
                            and diagnostics.get("ax_error_code") in {-25205, -25212}):
                        unavailable.add(key)
                    else:
                        raise
            required_match = not (required & unavailable) and all(
                observed.get(key) == properties[key] for key in required
            )
            if required_match:
                unknown_optional = (set(properties) - required) & unavailable
                if unknown_optional:
                    raise DesktopOperationError(
                        "DESKTOP_SELECTOR_MATCH_UNKNOWN",
                        "AX selector cardinality is unknown because a matching element lacks a profiled optional attribute",
                        safe_retry=True,
                    )
                if all(observed.get(key) == value for key, value in properties.items()):
                    matches.append(element)
            try:
                children_value = read_attribute(element, "AXChildren")
            except DesktopOperationError as exc:
                diagnostics = exc.diagnostics
                if (diagnostics.get("kind") == "AX_ERROR"
                        and diagnostics.get("ax_error_code") == -25205):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DesktopOperationError(
                            "DESKTOP_SELECTOR_TIMEOUT",
                            "AX selector search exceeded its total time budget before proving a semantic leaf",
                            safe_retry=True,
                        ) from exc
                    try:
                        supported_names = self.supported_attribute_names(element, timeout_s=remaining)
                    except DesktopOperationError as names_exc:
                        raise DesktopOperationError(
                            "DESKTOP_SELECTOR_TREE_INCOMPLETE",
                            "AX selector search cannot prove uniqueness because leaf attributes are unknown",
                            safe_retry=True,
                            diagnostics={"scope": "selector_search", "child_tree_status": "ATTRIBUTE_NAMES_UNVERIFIED",
                                         "attribute_names_error": names_exc.code},
                        ) from names_exc
                    if "AXChildren" not in supported_names:
                        children_value = []
                    else:
                        raise DesktopOperationError(
                            "DESKTOP_SELECTOR_TREE_INCOMPLETE",
                            "AXChildren is listed as supported but its value could not be read",
                            safe_retry=True,
                            diagnostics={"scope": "selector_search", "child_tree_status": "SUPPORTED_VALUE_UNREADABLE",
                                         "ax_error_code": diagnostics.get("ax_error_code"),
                                         "ax_error_name": diagnostics.get("ax_error_name")},
                        ) from exc
                elif (diagnostics.get("kind") == "AX_ERROR"
                        and diagnostics.get("ax_error_code") == -25212):
                    raise DesktopOperationError(
                        "DESKTOP_SELECTOR_TREE_INCOMPLETE",
                        "AX selector search cannot prove uniqueness while a target-window subtree is unreadable",
                        safe_retry=True,
                        diagnostics={"scope": "selector_search", "child_tree_status": diagnostics.get("ax_error_name")},
                    ) from exc
                else:
                    raise
            if children_value is None or isinstance(children_value, (str, bytes, bytearray, Mapping)):
                raise DesktopOperationError(
                    "DESKTOP_SELECTOR_TREE_INCOMPLETE",
                    "AX selector search received no verifiable child sequence",
                    safe_retry=True,
                )
            try:
                children = list(children_value)
            except (TypeError, ValueError) as exc:
                raise DesktopOperationError(
                    "DESKTOP_SELECTOR_TREE_INCOMPLETE",
                    "AX selector search received a malformed child sequence",
                    safe_retry=True,
                ) from exc
            remaining = max(0, 4096 - seen - len(pending))
            if len(children) > remaining:
                raise DesktopOperationError("DESKTOP_SELECTOR_TREE_LIMIT", "AX selector search exceeds its fixed 4096-node bound", safe_retry=True)
            pending.extend(reversed(children))
        if pending or seen >= 4096:
            raise DesktopOperationError("DESKTOP_SELECTOR_TREE_LIMIT", "AX selector search was incomplete at the node bound", safe_retry=True)
        if len(matches) != 1:
            raise DesktopOperationError("DESKTOP_SELECTOR_CARDINALITY", "AX selector is absent or ambiguous in the target window", safe_retry=True)
        return matches[0]

    def read_selector(self, identity, selector):
        element = self._find_exact_selector(identity, selector)
        readback = selector.get("readback")
        if readback == "name":
            name = "AXTitle"
        elif readback == "value":
            name = "AXValue"
        else:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "macOS AX readback supports only title and value attributes", safe_retry=True)
        value = self.attribute(element, name)
        if not isinstance(value, str):
            raise DesktopOperationError("DESKTOP_SELECTOR_READBACK_UNKNOWN", "AX selector readback is not a string", safe_retry=True)
        return value

    def perform_selector(self, identity, selector):
        element = self._find_exact_selector(identity, selector)
        accessibility, _ = self._load()
        if selector.get("pattern") == "invoke":
            action = getattr(accessibility, "kAXPressAction", "AXPress")
        elif selector.get("pattern") == "selection":
            action = getattr(accessibility, "kAXSelectAction", None)
            if action is None:
                raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "this Accessibility runtime does not expose the selection action constant", safe_retry=True)
        else:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "AX action profile must declare invoke or selection", safe_retry=True)
        self._set_ax_messaging_timeout(element)
        error, names = accessibility.AXUIElementCopyActionNames(element, None)
        if error != 0 or not names or action not in names:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "observed AX element does not expose the profile's exact action", safe_retry=True)
        self._set_ax_messaging_timeout(element)
        result = accessibility.AXUIElementPerformAction(element, action)
        if result != 0:
            raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "AX action failed", side_effect_started=True, safe_retry=False)
        readback = None
        if selector.get("pattern") == "selection":
            error, readback = accessibility.AXUIElementCopyAttributeValue(element, "AXSelected", None)
            if error != 0 or readback is not True:
                raise DesktopOperationError("DESKTOP_ACTION_UNVERIFIED", "AX selection did not read back AXSelected=true", side_effect_started=True, safe_retry=False)
        return {"performed": True, "selector_id": selector.get("selector_id"), "action": str(action),
                "selected": readback if selector.get("pattern") == "selection" else None}

    def set_selector_value(self, identity, selector, value):
        """Set only a host-profiled AX Value field and verify its exact value."""
        if selector.get("pattern") != "value" or not isinstance(value, str) or "\x00" in value:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "AX value assignment requires a profiled Value field and bounded text", safe_retry=True)
        element = self._find_exact_selector(identity, selector)
        accessibility, _ = self._load()
        setter = getattr(accessibility, "AXUIElementSetAttributeValue", None)
        if not callable(setter):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNSUPPORTED", "Accessibility value assignment API is unavailable", safe_retry=True)
        self._set_ax_messaging_timeout(element)
        result = setter(element, "AXValue", value)
        if result != 0:
            raise DesktopOperationError("DESKTOP_VALUE_SET_FAILED", "Accessibility rejected the profiled destination field", side_effect_started=True, safe_retry=False)
        try:
            readback = self.attribute(element, "AXValue", scope="selector_readback")
        except DesktopOperationError as exc:
            raise DesktopOperationError(
                "DESKTOP_VALUE_READBACK_UNKNOWN",
                "Accessibility accepted the destination but exact value readback failed",
                side_effect_started=True,
                safe_retry=False,
                diagnostics={"scope": "selector_readback", "cause_code": exc.code,
                             "cause_kind": exc.diagnostics.get("kind")},
            ) from exc
        except Exception as exc:
            raise DesktopOperationError(
                "DESKTOP_VALUE_READBACK_UNKNOWN",
                "Accessibility accepted the destination but exact value readback failed",
                side_effect_started=True,
                safe_retry=False,
                diagnostics={"scope": "selector_readback", "cause_type": type(exc).__name__},
            ) from exc
        if readback != value:
            raise DesktopOperationError("DESKTOP_VALUE_READBACK_UNKNOWN", "Save Copy destination did not read back exactly", side_effect_started=True, safe_retry=False)
        return {"set": True, "selector_id": selector.get("selector_id"), "readback": readback}

    def save_copy(self, identity, selectors, destination):
        """Use exact observed AX selectors for the native Save Copy dialog."""
        required = {"save_copy", "save_path", "save_confirm"}
        if not isinstance(selectors, Mapping) or not required <= set(selectors):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "profile lacks exact AX Save Copy, path, and confirmation selectors", safe_retry=True)
        target = Path(destination)
        if (not target.is_absolute() or target.suffix.lower() != ".mph"
                or target.resolve(strict=False) != target or not target.parent.is_dir()
                or target.exists() or target.is_symlink()):
            raise DesktopOperationError("SAVE_COPY_PATH_REJECTED", "Save Copy requires a canonical new project-authorized MPH path", safe_retry=True)
        # Opening the dialog is the first effect.  Every later failure is
        # uncertain because the Desktop may have changed state or written bytes.
        self.perform_selector(identity, selectors["save_copy"])
        try:
            self.set_selector_value(identity, selectors["save_path"], str(target))
            if target.exists() or target.is_symlink():
                raise DesktopOperationError("SAVE_COPY_DESTINATION_APPEARED", "Save Copy destination appeared before confirmation", side_effect_started=True, safe_retry=False)
            self.perform_selector(identity, selectors["save_confirm"])
        except DesktopOperationError as exc:
            exc.side_effect_started = True
            exc.safe_retry = False
            raise
        return {"save_dialog_confirmed": True, "destination": str(target),
                "profiled_selectors": sorted(required)}

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


class MacOSDesktopAdapter(MacOSAccessibilityObserver):
    """Native profile-gated AX controls with ScreenCaptureKit target capture."""

    def __init__(self, *, profile_path=None, gateway=None, identity_observer=None):
        super().__init__(gateway=gateway, identity_observer=identity_observer)
        self.profile_path = os.fspath(profile_path) if profile_path is not None else os.environ.get("COMSOL_MCP_DESKTOP_SELECTOR_PROFILE")

    @staticmethod
    def _os_build():
        if sys.platform != "darwin":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "macOS selector profiles are only valid on macOS", safe_retry=True)
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.osversion"],
                                    capture_output=True, check=False, timeout=2.0,
                                    text=True, encoding="utf-8", errors="strict")
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise DesktopOperationError("DESKTOP_PROFILE_UNAVAILABLE", "exact macOS build identity is unavailable", safe_retry=True) from exc
        build = result.stdout.strip()
        if result.returncode != 0 or not re.fullmatch(r"[0-9A-Za-z.]{1,64}", build):
            raise DesktopOperationError("DESKTOP_PROFILE_UNAVAILABLE", "exact macOS build identity is malformed or unavailable", safe_retry=True)
        return build

    def _profile(self, identity):
        if not self.profile_path:
            raise DesktopOperationError("DESKTOP_PROFILE_UNAVAILABLE", "no host-observed selector profile is installed", safe_retry=True)
        return load_selector_profile(self.profile_path, identity, os_build=self._os_build())

    def status(self, runtime_id=None):
        base = super().status(runtime_id)
        if __import__("sys").platform != "darwin" or not self.profile_path:
            return {**base, "control_status": UNSUPPORTED_CONTROL,
                    "selector_profile": "UNAVAILABLE" if not self.profile_path else "WRONG_PLATFORM",
                    "java_shell": "UNSUPPORTED_ON_MACOS"}
        try:
            from ._host_scoped_provider import verify_private_path_security
            security = verify_private_path_security(self.profile_path, directory=False)
        except Exception as exc:
            return {**base, "control_status": UNSUPPORTED_CONTROL,
                    "selector_profile": "UNTRUSTED", "profile_error": getattr(exc, "code", type(exc).__name__),
                    "java_shell": "UNSUPPORTED_ON_MACOS"}
        return {**base, "control_status": "AVAILABLE", "selector_profile": "INSTALLED_REQUIRES_EXACT_HOST_MATCH",
                "profile_security": security, "java_shell": "UNSUPPORTED_ON_MACOS"}

    def enumerate_windows(self, runtime_id=None):
        rows = super().enumerate_windows(runtime_id)
        result = []
        for row in rows:
            try:
                profile = self._profile(row.identity)
                if "active_model_label" not in profile["selectors"]:
                    raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "profile lacks active model label", safe_retry=True)
                result.append(WindowObservation(row.identity, row.title, "AVAILABLE"))
            except DesktopOperationError:
                result.append(WindowObservation(row.identity, row.title, UNSUPPORTED_CONTROL))
        return result

    def observe_model(self, identity):
        from ._desktop_service import DesktopModelObservation
        profile = self._profile(identity)
        try:
            label = self.gateway.read_selector(identity, profile["selectors"]["active_model_label"])
        except KeyError as exc:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "no observed active-model label selector is installed", safe_retry=True) from exc
        if not label:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "active model label readback is empty", safe_retry=True)
        endpoint = None
        standalone_id = None
        fingerprint = None
        dirty = None
        selectors = profile["selectors"]
        if "active_server_endpoint" in selectors:
            endpoint = self.gateway.read_selector(identity, selectors["active_server_endpoint"]) or None
        if "standalone_model_id" in selectors:
            standalone_id = self.gateway.read_selector(identity, selectors["standalone_model_id"]) or None
        if "model_fingerprint" in selectors:
            fingerprint = self.gateway.read_selector(identity, selectors["model_fingerprint"]) or None
        if "model_dirty" in selectors:
            raw_dirty = self.gateway.read_selector(identity, selectors["model_dirty"])
            states = selectors["model_dirty"].get("states", {})
            if raw_dirty == states.get("dirty"):
                dirty = True
            elif raw_dirty == states.get("clean"):
                dirty = False
            else:
                raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "model dirty selector returned an unrecognized observed state", safe_retry=True)
        if endpoint:
            mode = "UNVERIFIED"
        elif standalone_id and fingerprint and dirty is not None:
            mode = "STANDALONE"
        else:
            mode = "UNVERIFIED"
        return DesktopModelObservation(mode, server_endpoint=endpoint,
                                       standalone_model_id=standalone_id,
                                       fingerprint=fingerprint, dirty=dirty,
                                       active_model_label=label)

    def preflight_show_model(self, identity, snapshot):
        """Refuse before any UI effect unless post-show binding evidence exists."""
        profile = self._profile(identity)
        selectors = profile["selectors"]
        if "show_model" not in selectors:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "no observed macOS current-model selector is installed", safe_retry=True)
        if not {"active_model_label", "active_server_endpoint"} <= set(selectors):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "show_model requires the observed active label and exact server endpoint selectors", safe_retry=True)
        if not isinstance(getattr(snapshot, "server_endpoint", None), str) or not snapshot.server_endpoint:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "show_model requires a typed managed server endpoint", safe_retry=True)
        return {"ready": True, "profile_sha256": profile["profile_sha256"]}

    def show_model(self, identity, snapshot):
        self.preflight_show_model(identity, snapshot)
        profile = self._profile(identity)
        selector = profile["selectors"]["show_model"]
        self.gateway.perform_selector(identity, selector)
        return {"verified": True, "target_model_ref": snapshot.model_ref.as_dict(), "profile_sha256": profile["profile_sha256"]}

    @staticmethod
    def _node_digest(path):
        import hashlib, json
        return hashlib.sha256(json.dumps(path, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()

    def select_node(self, identity, path):
        profile = self._profile(identity)
        selector_id = profile.get("node_paths", {}).get(self._node_digest(path))
        selector = profile["selectors"].get(selector_id) if selector_id else None
        if selector is None:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "NodePath has no exact observed AX selector", safe_retry=True)
        self.gateway.perform_selector(identity, selector)
        return {"verified": True, "selected_path": path, "selector_id": selector_id,
                "profile_sha256": profile["profile_sha256"]}

    def perform_action(self, identity, plan):
        profile = self._profile(identity)
        descriptor = profile.get("actions", {}).get(plan.action_id)
        if (not isinstance(descriptor, dict)
                or plan.arguments != {"profile_sha256": profile["profile_sha256"], "descriptor": descriptor}
                or plan.mutates_model is not descriptor.get("mutates_model")):
            raise DesktopOperationError("ACTION_PROFILE_MISMATCH", "action plan is not the exact host-observed AX profile descriptor", safe_retry=True)
        before = descriptor["before"]
        before_selector = profile["selectors"][before["selector_id"]]
        if self.gateway.read_selector(identity, before_selector) != before["value"]:
            raise DesktopOperationError("ACTION_PRECONDITION_CHANGED", "the observed AX action precondition changed before invocation", side_effect_started=True, safe_retry=False)
        selector = profile["selectors"][descriptor["selector_id"]]
        result = self.gateway.perform_selector(identity, selector)
        after = descriptor["after"]
        after_selector = profile["selectors"][after["selector_id"]]
        try:
            after_value = self.gateway.read_selector(identity, after_selector)
        except DesktopOperationError as exc:
            exc.side_effect_started = True
            exc.safe_retry = False
            raise
        if after_value != after["value"]:
            raise DesktopOperationError("ACTION_POSTCONDITION_UNKNOWN", "AX action did not produce its exact observed readback", side_effect_started=True, safe_retry=False)
        return {"verified": True, "action_id": plan.action_id, "selector_id": result["selector_id"],
                "precondition": "MATCHED", "postcondition": "MATCHED",
                "profile_sha256": profile["profile_sha256"]}

    def resolve_action(self, identity, action):
        from ._desktop_service import DesktopActionPlan
        if not isinstance(action, dict) or set(action) - {"action_id", "target", "arguments"}:
            raise DesktopOperationError("ACTION_NOT_AUTHORIZED", "Desktop action request contains fields outside the observed host profile", safe_retry=True)
        action_id = action.get("action_id")
        if (not isinstance(action_id, str) or action.get("target") != "window"
                or ("arguments" in action and (not isinstance(action["arguments"], dict) or action["arguments"]))):
            raise DesktopOperationError("ACTION_NOT_AUTHORIZED", "profile-gated actions accept only an exact window action with no unobserved arguments", safe_retry=True)
        profile = self._profile(identity)
        descriptor = profile.get("actions", {}).get(action_id)
        if not isinstance(descriptor, dict):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "action ID has no exact host-observed AX policy", safe_retry=True)
        before = descriptor["before"]
        actual = self.gateway.read_selector(identity, profile["selectors"][before["selector_id"]])
        if actual != before["value"]:
            raise DesktopOperationError("ACTION_PRECONDITION_FAILED", "observed action precondition does not match the current window", safe_retry=True)
        return DesktopActionPlan(action_id, descriptor["mutates_model"],
                                 {"profile_sha256": profile["profile_sha256"], "descriptor": descriptor})

    def preflight_capture(self, identity, region):
        """Check non-prompting prerequisites before a binding challenge/effect marker."""
        if region != "window":
            raise DesktopOperationError("CAPTURE_SCOPE_REJECTED", "macOS capture currently supports only the exact ScreenCaptureKit window filter", safe_retry=True)
        _require_screencapturekit_14()
        permission = self.gateway.permissions()
        if permission.get("screen_capture_allowed") is not True:
            raise DesktopOperationError("PERMISSION_REQUIRED", "macOS Screen Recording permission is unavailable", category=BLOCKED_PERMISSION, safe_retry=True)
        return {"ready": True, "scope": "target_window", "permission_scope": permission.get("permission_scope")}

    def capture(self, identity, region):
        from ._desktop_capture import capture_macos_screen_capture_kit_window
        self.preflight_capture(identity, region)
        image = capture_macos_screen_capture_kit_window(identity, gateway=self.gateway)
        return {"verified": True, "region": image.region, "scope": image.scope,
                "format": image.format, "width": image.width, "height": image.height,
                "sha256": image.sha256, "size": len(image.data), "image_bytes": image.data,
                "target_identity_fingerprint": image.target_identity_fingerprint,
                "capture_api": "ScreenCaptureKit/SCScreenshotManager"}

    def shell_execute(self, identity, artifact):
        raise DesktopOperationError("UNSUPPORTED_PLATFORM", "COMSOL Java Shell is an interactive Windows-only workflow; macOS Shell execution is unsupported", safe_retry=True)

    def save_copy(self, identity, destination, source=None):
        from ._desktop_service import SavedCopy
        from ._desktop_service import DesktopModelObservation
        profile = self._profile(identity)
        required = {"save_copy", "save_path", "save_confirm", "save_complete"}
        if not required <= set(profile["selectors"]):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "profile lacks exact observed AX Save Copy and completion selectors", safe_retry=True)
        destination = Path(destination)
        if (not isinstance(source, DesktopModelObservation) or source.mode != "STANDALONE"
                or not source.active_model_label or not source.standalone_model_id
                or not source.fingerprint or type(source.dirty) is not bool):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Save Copy requires a complete standalone source baseline before UI effects", safe_retry=True)
        before = self.observe_model(identity)
        if (before.mode != source.mode or before.active_model_label != source.active_model_label
                or before.standalone_model_id != source.standalone_model_id
                or before.fingerprint != source.fingerprint or before.dirty != source.dirty):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "standalone source changed before Save Copy", safe_retry=True)
        if self.save_copy_completion_state(identity, side_effect_started=False) != "idle":
            raise DesktopOperationError("SAVE_COPY_NOT_IDLE", "profiled Save Copy control is not in its observed idle state", safe_retry=True)
        if (not destination.is_absolute() or destination.suffix.lower() != ".mph"
                or destination.resolve(strict=False) != destination
                or not destination.parent.is_dir() or destination.exists() or destination.is_symlink()):
            raise DesktopOperationError("SAVE_COPY_PATH_REJECTED", "Save Copy requires a canonical new project-authorized MPH target", safe_retry=True)
        self.gateway.save_copy(identity, {key: profile["selectors"][key] for key in required}, str(destination))
        after = self.observe_model(identity)
        if (after.mode != source.mode or after.active_model_label != source.active_model_label
                or after.standalone_model_id != source.standalone_model_id
                or after.dirty != source.dirty or after.fingerprint != source.fingerprint):
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "source model signal changed or lacks an exact preservation baseline", side_effect_started=True, safe_retry=False)
        return SavedCopy(str(destination), None, True, bool(after.dirty), str(after.fingerprint), None)

    def save_copy_completion_state(self, identity, *, side_effect_started=False):
        try:
            profile = self._profile(identity)
            selector = profile["selectors"].get("save_complete")
            states = selector.get("states") if isinstance(selector, Mapping) else None
            if not isinstance(states, Mapping) or set(states) != {"idle", "busy", "complete"}:
                raise DesktopOperationError(
                    "SAVE_COPY_COMPLETION_UNAVAILABLE",
                    "profile lacks a distinct observed idle/busy/complete selector",
                    category=UNSUPPORTED_CONTROL, side_effect_started=side_effect_started,
                    safe_retry=not side_effect_started,
                )
            observed = self.gateway.read_selector(identity, selector)
            for state, expected in states.items():
                if observed == expected:
                    return state
            raise DesktopOperationError(
                "SAVE_COPY_COMPLETION_UNKNOWN", "AX completion selector returned an unrecognized state",
                category=UNSUPPORTED_CONTROL if not side_effect_started else MODEL_BINDING_UNKNOWN,
                side_effect_started=side_effect_started, safe_retry=not side_effect_started,
            )
        except DesktopOperationError as exc:
            if side_effect_started:
                exc.side_effect_started = True
                exc.safe_retry = False
                exc.category = MODEL_BINDING_UNKNOWN
            raise

    def verify_source_preserved(self, identity, source):
        from ._desktop_service import SourcePreservation
        current = self.observe_model(identity)
        return SourcePreservation(
            current.mode == source.mode and current.active_model_label == source.active_model_label
            and current.standalone_model_id == source.standalone_model_id
            and current.fingerprint == source.fingerprint and current.dirty == source.dirty,
            current.standalone_model_id or "", current.fingerprint or "", bool(current.dirty),
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _file_sha256(path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
