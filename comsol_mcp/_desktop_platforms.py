"""Explicit factory for native metadata adapters; never supplies test fixtures."""
from __future__ import annotations

import sys

from ._desktop_service import MetadataOnlyDesktopAdapter


_UNAVAILABLE_DESKTOP_OPERATIONS = {
    "desktop.bind": "MODEL_BINDING_UNKNOWN: no observed Desktop endpoint/current-model provider",
    "desktop.show_model": "UNSUPPORTED_CONTROL: no verified current-window model selector",
    "desktop.select_node": "UNSUPPORTED_CONTROL: no observed COMSOL tree selector or NodePath readback",
    "desktop.capture": "UNSUPPORTED_CONTROL: no model binding or trusted artifact sink; the Windows HWND pixel helper is not routed here",
    "desktop.action": "UNSUPPORTED_CONTROL: no observed, versioned COMSOL control selectors",
    "desktop.shell_execute": "UNSUPPORTED_CONTROL: Java Shell is interactive UI; the managed-server Java route is not Desktop Shell execution",
    "desktop.migrate_standalone": "UNSUPPORTED_CONTROL: no current standalone identity/dirty-state/save-copy UI provider",
}


def versioned_native_profile(platform: str) -> dict[str, object]:
    """Describe source capability precisely; never infer native acceptance."""
    if platform == "windows":
        return {
            "profile_id": "comsol-mcp.windows-desktop-identity/v1",
            "platform_api": "pywin32 Win32 HWND/process identity; PrintWindow is a separate lower-level primitive",
            "comsol_version_policy": "report executable ProductVersion only; no COMSOL 6.3/6.4 build is native-certified by this profile",
            "source_capabilities": {"desktop.status": "PROCESS_WINDOW_IDENTITY_SOURCE_IMPLEMENTED"},
            "operation_gaps": dict(_UNAVAILABLE_DESKTOP_OPERATIONS),
            "native_evidence": "NOT_RUN",
        }
    if platform == "macos":
        return {
            "profile_id": "comsol-mcp.macos-desktop-identity/v1",
            "platform_api": "Quartz window inventory + psutil process birth/session + optional Accessibility trust query",
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


def create_native_metadata_adapter() -> MetadataOnlyDesktopAdapter:
    """Return the host's read-only identity observer, with controls still gated."""
    if sys.platform == "win32":
        from ._desktop_windows import WindowsMetadataAdapter
        return WindowsMetadataAdapter()
    if sys.platform == "darwin":
        from ._desktop_macos import MacOSMetadataAdapter
        return MacOSMetadataAdapter()
    return MetadataOnlyDesktopAdapter()
