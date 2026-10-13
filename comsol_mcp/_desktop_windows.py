"""Windows COMSOL window observer and selector-profile-gated UI Automation.

The observer binds top-level COMSOL windows to process creation time, logon
SID, interactive session, and executable version.  The control adapter uses
only exact host-observed UIA selectors from a protected profile; it never
falls back to titles, coordinates, global input, or managed-server Java calls.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

from ._desktop_service import (
    BLOCKED_PERMISSION,
    UNSUPPORTED_CONTROL,
    DesktopOperationError,
    MetadataOnlyDesktopAdapter,
    SavedCopy,
    WindowIdentity,
    WindowObservation,
)
from ._desktop_platforms import versioned_native_profile
from ._desktop_win32_identity import observe_process_birth_and_session
from ._desktop_selector_profile import load_selector_profile
from ._desktop_uia import WindowsUIAutomationGateway


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


class WindowsDesktopAdapter(WindowsMetadataAdapter):
    """Profile-gated native controls using the OS UI Automation client API."""

    def __init__(self, *, profile_path: str | os.PathLike[str] | None = None, gateway=None,
                 capture_fn=None):
        self.profile_path = os.fspath(profile_path) if profile_path is not None else os.environ.get("COMSOL_MCP_DESKTOP_SELECTOR_PROFILE")
        self.gateway = gateway or WindowsUIAutomationGateway()
        self._capture_fn = capture_fn

    @staticmethod
    def _os_build() -> str:
        if sys.platform != "win32":
            raise DesktopOperationError("UNSUPPORTED_PLATFORM", "Windows UI Automation is only available on Windows", safe_retry=True)
        try:
            return str(int(sys.getwindowsversion().build))
        except Exception as exc:
            raise DesktopOperationError("DESKTOP_PROFILE_UNAVAILABLE", "Windows build identity is unavailable", safe_retry=True) from exc

    def _profile(self, identity: WindowIdentity) -> dict[str, Any]:
        return load_selector_profile(self.profile_path, identity, os_build=self._os_build())

    def status(self, runtime_id: str | None = None) -> dict[str, Any]:
        base = super().status(runtime_id)
        if sys.platform != "win32":
            return base
        if not self.profile_path:
            return {**base, "control_status": UNSUPPORTED_CONTROL,
                    "selector_profile": "UNAVAILABLE", "control_note": "no host-observed protected selector profile is installed"}
        try:
            from ._host_scoped_provider import verify_private_path_security
            evidence = verify_private_path_security(self.profile_path, directory=False)
        except Exception as exc:
            return {**base, "control_status": UNSUPPORTED_CONTROL,
                    "selector_profile": "UNTRUSTED", "control_note": getattr(exc, "code", type(exc).__name__)}
        return {**base, "control_status": "AVAILABLE", "selector_profile": "INSTALLED_REQUIRES_EXACT_HOST_MATCH",
                "profile_security": evidence, "control_note": "each window is separately checked against exact product/build and observed selectors"}

    def enumerate_windows(self, runtime_id: str | None = None) -> list[WindowObservation]:
        rows = super().enumerate_windows(runtime_id)
        result = []
        for row in rows:
            try:
                profile = self._profile(row.identity)
                if "active_model_label" not in profile["selectors"]:
                    raise DesktopOperationError("DESKTOP_PROFILE_INVALID", "profile lacks the active-model label observation required for binding", safe_retry=True)
                result.append(WindowObservation(row.identity, row.title, "AVAILABLE"))
            except DesktopOperationError:
                result.append(WindowObservation(row.identity, row.title, UNSUPPORTED_CONTROL))
        return result

    def _selector(self, identity: WindowIdentity, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = self._profile(identity)
        try:
            return profile, profile["selectors"][name]
        except KeyError as exc:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", f"no observed exact UIA selector is installed for {name}", safe_retry=True) from exc

    def observe_model(self, identity: WindowIdentity):
        from ._desktop_service import DesktopModelObservation
        profile, label_selector = self._selector(identity, "active_model_label")
        label = self.gateway.read(identity, label_selector)
        if not label:
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "active model label readback is empty", safe_retry=True)
        selectors = profile["selectors"]
        endpoint = self.gateway.read(identity, selectors["active_server_endpoint"]) if "active_server_endpoint" in selectors else None
        standalone_id = self.gateway.read(identity, selectors["standalone_model_id"]) if "standalone_model_id" in selectors else None
        fingerprint = self.gateway.read(identity, selectors["model_fingerprint"]) if "model_fingerprint" in selectors else None
        dirty = None
        if "model_dirty" in selectors:
            raw_dirty = self.gateway.read(identity, selectors["model_dirty"])
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
        return DesktopModelObservation(mode, server_endpoint=endpoint or None,
                                       standalone_model_id=standalone_id or None,
                                       fingerprint=fingerprint or None, dirty=dirty,
                                       active_model_label=label)

    def show_model(self, identity: WindowIdentity, snapshot):
        _profile, selector = self._selector(identity, "show_model")
        result = self.gateway.invoke(identity, selector, selection=selector["pattern"] == "selection")
        return {"verified": True, "selector_id": result["selector_id"],
                "target_model_ref": snapshot.model_ref.as_dict(), "profile_sha256": _profile["profile_sha256"]}

    @staticmethod
    def _node_digest(path: Any) -> str:
        try:
            raw = json.dumps(path, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise DesktopOperationError("INVALID_REQUEST", "NodePath is not canonical finite JSON", safe_retry=True) from exc
        return hashlib.sha256(raw).hexdigest()

    def select_node(self, identity: WindowIdentity, path: Any):
        profile = self._profile(identity)
        digest = self._node_digest(path)
        selector_id = profile.get("node_paths", {}).get(digest)
        selector = profile["selectors"].get(selector_id) if selector_id else None
        if selector is None:
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "NodePath has no exact host-observed UIA mapping", safe_retry=True)
        result = self.gateway.invoke(identity, selector, selection=selector["pattern"] == "selection")
        return {"verified": True, "selected_path": path, "selector_id": result["selector_id"],
                "profile_sha256": profile["profile_sha256"]}

    def perform_action(self, identity: WindowIdentity, plan):
        profile = self._profile(identity)
        descriptor = profile.get("actions", {}).get(plan.action_id)
        if (not isinstance(descriptor, dict)
                or plan.arguments != {"profile_sha256": profile["profile_sha256"], "descriptor": descriptor}
                or plan.mutates_model is not descriptor.get("mutates_model")):
            raise DesktopOperationError("ACTION_PROFILE_MISMATCH", "action plan is not the exact host-observed profile descriptor", safe_retry=True)
        before = descriptor["before"]
        before_selector = profile["selectors"][before["selector_id"]]
        if self.gateway.read(identity, before_selector) != before["value"]:
            raise DesktopOperationError("ACTION_PRECONDITION_CHANGED", "the observed action precondition changed before UIA invocation", side_effect_started=True, safe_retry=False)
        selector = profile["selectors"][descriptor["selector_id"]]
        result = self.gateway.invoke(identity, selector, selection=selector["pattern"] == "selection")
        after = descriptor["after"]
        after_selector = profile["selectors"][after["selector_id"]]
        try:
            after_value = self.gateway.read(identity, after_selector)
        except DesktopOperationError as exc:
            exc.side_effect_started = True
            exc.safe_retry = False
            raise
        if after_value != after["value"]:
            raise DesktopOperationError("ACTION_POSTCONDITION_UNKNOWN", "UIA action did not produce its exact observed readback", side_effect_started=True, safe_retry=False)
        return {"verified": True, "action_id": plan.action_id, "selector_id": result["selector_id"],
                "precondition": "MATCHED", "postcondition": "MATCHED",
                "profile_sha256": profile["profile_sha256"]}

    def resolve_action(self, identity: WindowIdentity, action):
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
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "action ID has no exact host-observed UIA policy", safe_retry=True)
        before = descriptor["before"]
        actual = self.gateway.read(identity, profile["selectors"][before["selector_id"]])
        if actual != before["value"]:
            raise DesktopOperationError("ACTION_PRECONDITION_FAILED", "observed action precondition does not match the current window", safe_retry=True)
        return DesktopActionPlan(action_id, descriptor["mutates_model"],
                                 {"profile_sha256": profile["profile_sha256"], "descriptor": descriptor})

    def shell_execute(self, identity: WindowIdentity, artifact):
        profile = self._profile(identity)
        shell_names = {"shell_input", "shell_run", "shell_output", "shell_busy"}
        if not shell_names <= set(profile["selectors"]):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "profile lacks the complete observed Java Shell control set", safe_retry=True)
        if artifact.content is None or len(artifact.content) != artifact.size:
            raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "immutable registered source bytes are unavailable", safe_retry=True)
        result = self.gateway.execute_shell(identity, {name: profile["selectors"][name] for name in shell_names},
                                            artifact.content, artifact.sha256)
        return {**result, "artifact_id": artifact.artifact_id,
                "profile_sha256": profile["profile_sha256"],
                "window_identity_fingerprint": identity.fingerprint()}

    def capture(self, identity: WindowIdentity, region: str):
        if region != "window":
            raise DesktopOperationError("CAPTURE_SCOPE_REJECTED", "Windows capture currently supports only the exact observed window region", safe_retry=True)
        if self._capture_fn is None:
            from ._desktop_capture import capture_windows_window
            capture = capture_windows_window(identity)
        else:
            capture = self._capture_fn(identity)
        if capture.target_identity_fingerprint != identity.fingerprint() or capture.region != "window" or capture.scope != "target_window":
            raise DesktopOperationError("CAPTURE_UNVERIFIED", "window capture identity or scope does not match the observed HWND", side_effect_started=True, safe_retry=False)
        return {"verified": True, "region": "window", "scope": "target_window",
                "format": capture.format, "width": capture.width, "height": capture.height,
                "sha256": capture.sha256, "size": len(capture.data), "image_bytes": capture.data,
                "target_identity_fingerprint": identity.fingerprint(),
                "profile_id": "comsol-mcp.windows-window-capture/v1"}

    def save_copy(self, identity: WindowIdentity, destination: Path, source=None):
        profile = self._profile(identity)
        required = {"save_copy", "save_path", "save_confirm", "save_complete"}
        if not required <= set(profile["selectors"]):
            raise DesktopOperationError("DESKTOP_SELECTOR_UNAVAILABLE", "profile lacks exact observed Save Copy and completion selectors", safe_retry=True)
        from ._desktop_service import DesktopModelObservation
        if (not isinstance(source, DesktopModelObservation) or source.mode != "STANDALONE"
                or not isinstance(source.standalone_model_id, str) or not source.standalone_model_id
                or not isinstance(source.active_model_label, str) or not source.active_model_label
                or not isinstance(source.fingerprint, str) or not source.fingerprint
                or type(source.dirty) is not bool):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "Save Copy requires a complete standalone source baseline before UI effects", safe_retry=True)
        before = self.observe_model(identity)
        if (before.mode != source.mode or before.standalone_model_id != source.standalone_model_id
                or before.active_model_label != source.active_model_label
                or before.fingerprint != source.fingerprint or before.dirty != source.dirty):
            raise DesktopOperationError("MODEL_BINDING_UNKNOWN", "standalone source changed before Save Copy", safe_retry=True)
        if self.save_copy_completion_state(identity, side_effect_started=False) != "idle":
            raise DesktopOperationError("SAVE_COPY_NOT_IDLE", "profiled Save Copy control is not in its observed idle state", safe_retry=True)
        selectors = {name: profile["selectors"][name] for name in required}
        self.gateway.save_copy(identity, selectors, str(destination))
        after = self.observe_model(identity)
        if (after.mode != source.mode or after.standalone_model_id != source.standalone_model_id
                or after.active_model_label != source.active_model_label or after.dirty != source.dirty
                or after.fingerprint != source.fingerprint):
            raise DesktopOperationError("SOURCE_MODEL_NOT_PRESERVED", "source signal changed or lacks an exact preservation baseline", side_effect_started=True, safe_retry=False)
        # Completion, ownership and hash are established by the coordinator's
        # pinned host-private staging verifier after UIA returns. Hashing a
        # caller path here would race an unfinished or replaced Save Copy.
        return SavedCopy(str(destination), None, True, bool(after.dirty), str(after.fingerprint), None)

    def save_copy_completion_state(self, identity: WindowIdentity, *, side_effect_started: bool = False) -> str:
        from ._desktop_service import UNSUPPORTED_CONTROL
        try:
            profile = self._profile(identity)
            selector = profile["selectors"].get("save_complete")
            states = selector.get("states") if isinstance(selector, dict) else None
            if not isinstance(states, dict) or set(states) != {"idle", "busy", "complete"}:
                raise DesktopOperationError(
                    "SAVE_COPY_COMPLETION_UNAVAILABLE",
                    "profile lacks a distinct observed idle/busy/complete selector",
                    category=UNSUPPORTED_CONTROL, side_effect_started=side_effect_started,
                    safe_retry=not side_effect_started,
                )
            observed = self.gateway.read(identity, selector)
            for state, expected in states.items():
                if observed == expected:
                    return state
            raise DesktopOperationError(
                "SAVE_COPY_COMPLETION_UNKNOWN", "UIA completion selector returned an unrecognized state",
                category=UNSUPPORTED_CONTROL if not side_effect_started else "MODEL_BINDING_UNKNOWN",
                side_effect_started=side_effect_started, safe_retry=not side_effect_started,
            )
        except DesktopOperationError as exc:
            if side_effect_started:
                exc.side_effect_started = True
                exc.safe_retry = False
                exc.category = "MODEL_BINDING_UNKNOWN"
            raise

    def verify_source_preserved(self, identity: WindowIdentity, source):
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
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
