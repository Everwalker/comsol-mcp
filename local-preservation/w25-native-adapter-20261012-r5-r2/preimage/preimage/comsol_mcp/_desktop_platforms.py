"""Explicit factory for native metadata adapters; never supplies test fixtures."""
from __future__ import annotations

import sys

from ._desktop_service import MetadataOnlyDesktopAdapter


_UNAVAILABLE_DESKTOP_OPERATIONS = {
    "desktop.bind": "MODEL_BINDING_UNKNOWN: no observed Desktop endpoint/current-model provider",
    "desktop.show_model": "UNSUPPORTED_CONTROL: no verified current-window model selector",
    "desktop.select_node": "UNSUPPORTED_CONTROL: no observed COMSOL tree selector or NodePath readback",
    "desktop.capture": "UNSUPPORTED_CONTROL: capture requires an exact target-window adapter and trusted artifact sink",
    "desktop.action": "UNSUPPORTED_CONTROL: no observed, versioned COMSOL control selectors",
    "desktop.shell_execute": "UNSUPPORTED_CONTROL: Java Shell is available only through an observed Windows UIA profile; macOS COMSOL documentation does not expose this Shell",
    "desktop.migrate_standalone": "UNSUPPORTED_CONTROL: no current standalone identity/dirty-state/save-copy UI provider",
}


def versioned_native_profile(platform: str) -> dict[str, object]:
    """Describe source capability precisely; never infer native acceptance."""
    if platform == "windows":
        return {
            "profile_id": "comsol-mcp.windows-desktop-adapter/v2",
            "platform_api": "pywin32 process/window identity + OS UI Automation client Value/Invoke/Selection patterns + isolated PrintWindow target HWND capture",
            "comsol_version_policy": "exact executable ProductVersion must match the protected host-observed selector profile; native acceptance remains NOT_RUN",
            "source_capabilities": {"desktop.status": "PROCESS_WINDOW_IDENTITY_SOURCE_IMPLEMENTED",
                                    "desktop.shell_execute": "WINDOWS_UIAUTOMATION_PROFILE_GATED",
                                    "desktop.capture": "EXACT_TARGET_WINDOW_PIXEL_SOURCE_IMPLEMENTED",
                                    "desktop.show_model": "WINDOWS_UIAUTOMATION_PROFILE_GATED",
                                    "desktop.select_node": "EXACT_NODEPATH_HASH_UIAUTOMATION_PROFILE_GATED",
                                    "desktop.action": "EXACT_ACTION_ID_UIAUTOMATION_PROFILE_GATED",
                                    "desktop.migrate_standalone": "SAVE_COPY_UIAUTOMATION_PROFILE_GATED_AND_SOURCE_READBACK_REQUIRED"},
            "operation_gaps": dict(_UNAVAILABLE_DESKTOP_OPERATIONS),
            "native_evidence": "NOT_RUN",
        }
    if platform == "macos":
        return {
            "profile_id": "comsol-mcp.macos-desktop-adapter/v2",
            "platform_api": "Quartz window inventory + psutil process birth/session + Accessibility selector profile + ScreenCaptureKit exact-window capture",
            "comsol_version_policy": "report app bundle version only; no COMSOL 6.3/6.4 build is native-certified by this profile",
            "source_capabilities": {"desktop.status": "PROCESS_WINDOW_IDENTITY_SOURCE_IMPLEMENTED"},
            "operation_gaps": dict(_UNAVAILABLE_DESKTOP_OPERATIONS),
            "native_evidence": "NOT_RUN",
        }
    return {
        "profile_id": "comsol-mcp.metadata-only/v1",
        "platform_api": "no native observer",
        "comsol_version_policy": "UNVERIFIED",
        "source_capabilities": {},
        "operation_gaps": {"desktop.*": "UNSUPPORTED_CONTROL"},
        "native_evidence": "NOT_RUN",
    }


def create_native_metadata_adapter(*, macos_accessibility_observer: bool = False) -> MetadataOnlyDesktopAdapter:
    """Return the host-native adapter, with each action gated by observed profile."""
    if sys.platform == "win32":
        from ._desktop_windows import WindowsDesktopAdapter
        return WindowsDesktopAdapter()
    if sys.platform == "darwin":
        from ._desktop_macos import MacOSDesktopAdapter
        return MacOSDesktopAdapter()
    return MetadataOnlyDesktopAdapter()
