from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

from comsol_mcp._desktop_capture import capture_macos_screen_capture_kit_window
from comsol_mcp._desktop_macos import MacOSDesktopAdapter, MacOSFrameworkGateway
from comsol_mcp._desktop_selector_profile import (
    PROFILE_SCHEMA, _require_security_evidence, _strict_json,
    load_selector_profile, validate_profile,
)
from comsol_mcp._desktop_service import DesktopModelObservation, DesktopOperationError, TrustedSourceArtifact, WindowIdentity
from comsol_mcp._desktop_uia import WindowsUIAutomationGateway, _POWERSHELL
from comsol_mcp._desktop_windows import WindowsDesktopAdapter


WINDOWS_ID = WindowIdentity("windows", "HWND:4a", 42, "UTC:2026-10-12T00:00:00.000000Z",
                            "S-1-5-21-100-200-300-1001", "WTS:2", "6.4.0.293")
MAC_ID = WindowIdentity("macos", "CGWindow:7", 123, "birth:123", "uid:501", "session:test", "6.4.0.293")


def _selector(platform: str, selector_id: str, *, pattern: str, readback: str = "name", states=None,
              property_id: str | None = None):
    if platform == "windows":
        properties = {"automation_id": property_id or selector_id, "control_type_id": 50000, "framework_id": "WPF"}
        kind = "uia"
    else:
        properties = {"identifier": property_id or selector_id, "role": "AXTextField"}
        kind = "ax"
    result = {"selector_id": selector_id, "kind": kind, "properties": properties,
              "readback": readback, "pattern": pattern}
    if states is not None:
        result["states"] = states
    return result


def _profile(platform: str, *, selectors=None, os_build="26100", comsol_version="6.4.0.293"):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": PROFILE_SCHEMA,
        "profile_id": "observed-test-profile-r1",
        "platform": platform,
        "comsol_version": comsol_version,
        "os_build": os_build,
        "observed_at_utc": now,
        "observation_sha256": "a" * 64,
        "selectors": selectors or {"active_model_label": _selector(platform, "label", pattern="read", readback="value")},
        "node_paths": {},
        "actions": {},
    }


def _one_pixel_png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def test_selector_profile_strict_json_exact_host_match_and_save_copy_schema(tmp_path, monkeypatch):
    save_selectors = {
        "active_model_label": _selector("windows", "active_model_label", property_id="label", pattern="read", readback="value"),
        "save_copy": _selector("windows", "save_copy", property_id="save-copy", pattern="invoke"),
        "save_path": _selector("windows", "save_path", property_id="save-path", pattern="value", readback="value"),
        "save_confirm": _selector("windows", "save_confirm", property_id="save-ok", pattern="invoke"),
        "save_complete": _selector("windows", "save_complete", pattern="read", readback="value",
                                    states={"idle": "Ready", "busy": "Saving", "complete": "Saved"}),
    }
    raw = json.dumps(_profile("windows", selectors=save_selectors), separators=(",", ":")).encode()
    path = tmp_path / "profile.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    loaded = load_selector_profile(path, WINDOWS_ID, os_build="26100")
    assert loaded["selectors"]["save_copy"]["pattern"] == "invoke"
    assert loaded["profile_sha256"] == hashlib.sha256(raw).hexdigest()
    incomplete_save_profile = _profile("windows", selectors={
        key: value for key, value in save_selectors.items() if key != "save_complete"
    })
    with pytest.raises(DesktopOperationError, match="completion selectors"):
        validate_profile(incomplete_save_profile, platform_name="windows",
                          comsol_version=WINDOWS_ID.comsol_version, os_build="26100")
    with pytest.raises(DesktopOperationError, match="strict UTF-8 JSON"):
        _strict_json(b'{"schema":"x","schema":"y"}')
    with pytest.raises(DesktopOperationError) as mismatch:
        validate_profile(_profile("windows"), platform_name="windows", comsol_version="6.4.0.293", os_build="26101")
    assert mismatch.value.code == "DESKTOP_PROFILE_STALE"
    with pytest.raises(DesktopOperationError):
        validate_profile(_profile("windows", selectors={"save_as": _selector("windows", "save_as", pattern="invoke")}),
                         platform_name="windows", comsol_version=WINDOWS_ID.comsol_version, os_build="26100")


def test_selector_profile_uses_exact_windows_provider_contract_and_acl_digest():
    good = {"identity": {"device": 7, "inode": 22, "security_sha256": "a" * 64},
            "security_semantics": "windows-read-control-owner-dacl-persistent-acls",
            "security_sha256": "a" * 64}
    assert _require_security_evidence(good, host_platform="windows") == good
    for bad in (
        {**good, "security_semantics": "windows-owner-dacl-handle"},
        {**good, "security_semantics": "windows-read-control-owner-dacl-persistent-acls+unknown"},
        {**good, "security_sha256": "b" * 64},
        {**good, "identity": {"device": 7, "inode": 22}},
    ):
        with pytest.raises(DesktopOperationError) as caught:
            _require_security_evidence(bad, host_platform="windows")
        assert caught.value.code == "DESKTOP_PROFILE_UNTRUSTED"


def test_windows_profile_fd_uses_provider_native_identity_width_without_closing_crt_handle(monkeypatch):
    import sys
    import comsol_mcp._desktop_selector_profile as profile_module

    observations = []

    class API:
        def info(self, handle, *, directory=False):
            observations.append(("info", handle, directory))
            return ((0xFEDCBA98, 0xFEDCBA9876543210), 17)

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(
        get_osfhandle=lambda fd: observations.append(("fd", fd)) or 0x123456789ABCDEF0))
    monkeypatch.setattr("comsol_mcp._windows_parameter_publish._API", API)
    # CPython's fstat identity may use FileIdInfo's wider volume/file IDs;
    # profile admission must compare the provider's own Win32 identity view.
    stat_view = SimpleNamespace(st_dev=3, st_ino=(1 << 120) + 9, st_size=17)
    assert profile_module._descriptor_identity(41, stat_view, windows=True) == (
        0xFEDCBA98, 0xFEDCBA9876543210)
    assert observations == [("fd", 41), ("info", 0x123456789ABCDEF0, False)]


def test_selector_profile_verify_open_replacement_is_rejected(tmp_path, monkeypatch):
    import comsol_mcp._desktop_selector_profile as profile_module
    import comsol_mcp._host_scoped_provider as provider

    raw = json.dumps(_profile("windows"), separators=(",", ":")).encode()
    path = tmp_path / "profile.json"
    replacement = tmp_path / "replacement.json"
    displaced = tmp_path / "displaced.json"
    path.write_bytes(raw)
    replacement.write_bytes(raw.replace(b"observed-test-profile-r1", b"replacement-profile-r1"))
    path.chmod(0o600)
    replacement.chmod(0o600)
    real_verify = provider.verify_private_path_security
    calls = 0

    def replace_after_initial_verification(value, *, directory, expected_identity=None):
        nonlocal calls
        calls += 1
        evidence = real_verify(value, directory=directory, expected_identity=expected_identity)
        if calls == 1:
            path.replace(displaced)
            replacement.replace(path)
        return evidence

    monkeypatch.setattr(provider, "verify_private_path_security", replace_after_initial_verification)
    with pytest.raises(DesktopOperationError) as caught:
        profile_module._read_private_profile(path)
    assert caught.value.code == "DESKTOP_PROFILE_UNTRUSTED"
    assert calls == 1
    assert displaced.read_bytes() == raw


def test_selector_profile_acl_change_during_read_is_rejected(tmp_path, monkeypatch):
    import comsol_mcp._desktop_selector_profile as profile_module
    import comsol_mcp._host_scoped_provider as provider

    if os.name == "nt":
        # Windows descriptor hashing is covered by the provider ABI controls;
        # here use its exact identity/security-digest comparison contract.
        before = _require_security_evidence({
            "identity": {"device": 7, "inode": 22, "security_sha256": "a" * 64},
            "security_semantics": "windows-read-control-owner-dacl-persistent-acls",
            "security_sha256": "a" * 64,
        }, host_platform="windows")
        after = {**before, "identity": {**before["identity"], "security_sha256": "b" * 64},
                 "security_sha256": "b" * 64}
        from comsol_mcp._desktop_selector_profile import _same_security_evidence
        assert not _same_security_evidence(before, after)
        return
    if sys.platform != "darwin":
        pytest.skip("real ACL-change behavior requires Darwin ACL support")

    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="desktop-profile-acl-", dir="/private/tmp") as raw_dir:
        parent = Path(raw_dir)
        parent.chmod(0o700)
        path = parent / "profile.json"
        path.write_bytes(json.dumps(_profile("macos"), separators=(",", ":")).encode())
        path.chmod(0o600)
        real_verify = provider.verify_private_path_security
        calls = 0

        def add_acl_before_final_security_check(fd, size):
            nonlocal calls
            block = original_read(fd, size)
            if block and not calls:
                calls += 1
                subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(path)],
                               check=True, capture_output=True, text=True)
            return block

        original_read = os.read
        monkeypatch.setattr(profile_module.os, "read", add_acl_before_final_security_check)
        with pytest.raises(DesktopOperationError) as caught:
            profile_module._read_private_profile(path)
        assert caught.value.code in {"HOST_PROVIDER_CONFIG_INVALID", "DESKTOP_PROFILE_CHANGED"}
        assert calls == 1
        # The fixture is private and temporary; remove only the ACL we added.
        subprocess.run(["/bin/chmod", "-a", "everyone allow read", str(path)],
                       check=False, capture_output=True, text=True)


def test_selector_profile_rejects_duplicate_selectors_and_unobserved_states():
    first = _selector("windows", "active_model_label", property_id="same", pattern="read", readback="value")
    second = _selector("windows", "show_model", property_id="same", pattern="invoke")
    with pytest.raises(DesktopOperationError) as duplicate:
        validate_profile(_profile("windows", selectors={"active_model_label": first, "show_model": second}),
                         platform_name="windows", comsol_version=WINDOWS_ID.comsol_version, os_build="26100")
    assert duplicate.value.code == "DESKTOP_PROFILE_DUPLICATE"
    dirty = _selector("macos", "model_dirty", property_id="dirty", pattern="read", readback="value", states={"dirty": "Yes", "clean": "No"})
    valid = _profile("macos", selectors={"model_dirty": dirty}, os_build="23G93")
    assert validate_profile(valid, platform_name="macos", comsol_version=MAC_ID.comsol_version,
                            os_build="23G93")["selectors"]["model_dirty"]["states"] == {"dirty": "Yes", "clean": "No"}
    bad = dict(valid)
    bad["selectors"] = {"model_dirty": dict(dirty, states={"dirty": "same", "clean": "same"})}
    with pytest.raises(DesktopOperationError):
        validate_profile(bad, platform_name="macos", comsol_version=MAC_ID.comsol_version, os_build="23G93")


def test_profile_loader_refuses_missing_profile_before_any_platform_probe():
    with pytest.raises(DesktopOperationError) as caught:
        load_selector_profile(None, WINDOWS_ID, os_build="26100")
    assert caught.value.code == "DESKTOP_PROFILE_UNAVAILABLE"


def test_windows_uia_gateway_carries_exact_source_and_requires_execution_readback():
    source = b"// registered immutable Java source\n"
    requests = []

    def runner(request, **_kwargs):
        requests.append(request)
        return {"executed": True, "source_sha256": request["source_sha256"], "output": "Done",
                "busy_observed": True, "output_changed": False}

    gateway = WindowsUIAutomationGateway(runner)
    selectors = {name: {"selector_id": name} for name in ("shell_input", "shell_run", "shell_output", "shell_busy")}
    reply = gateway.execute_shell(WINDOWS_ID, selectors, source, hashlib.sha256(source).hexdigest())
    assert reply["gateway"] == "WINDOWS_UIAUTOMATION_COMSOL_JAVA_SHELL"
    assert reply["output_sha256"] == hashlib.sha256(b"Done").hexdigest()
    assert requests[0]["hwnd"] == 0x4A and requests[0]["process_id"] == 42
    assert requests[0]["process_birth"] == WINDOWS_ID.process_birth
    assert requests[0]["login_id"] == WINDOWS_ID.login_id
    assert requests[0]["desktop_session_id"] == WINDOWS_ID.desktop_session_id
    assert requests[0]["source"] == source.decode()
    assert requests[0]["source_sha256"] == hashlib.sha256(source).hexdigest()

    calls = []
    unverified = WindowsUIAutomationGateway(lambda request, **_kw: calls.append(request) or {
        "executed": False, "source_sha256": request["source_sha256"], "output": "",
        "busy_observed": False, "output_changed": False,
    })
    with pytest.raises(DesktopOperationError) as caught:
        unverified.execute_shell(WINDOWS_ID, selectors, source, hashlib.sha256(source).hexdigest())
    assert caught.value.code == "UIA_SHELL_READBACK_UNKNOWN" and caught.value.safe_retry is False
    assert len(calls) == 1
    calls.clear()
    with pytest.raises(DesktopOperationError):
        unverified.execute_shell(WINDOWS_ID, selectors, source, "0" * 64)
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("process_birth", "birth:42"),
    ("login_id", "uid:501"),
    ("desktop_session_id", "session:2"),
])
def test_windows_uia_rejects_incomplete_process_identity_before_runner(field, value):
    from dataclasses import replace

    calls = []
    gateway = WindowsUIAutomationGateway(lambda request, **_kw: calls.append(request) or {})
    identity = replace(WINDOWS_ID, **{field: value})
    with pytest.raises(DesktopOperationError) as caught:
        gateway.read(identity, {"selector_id": "label"})
    assert caught.value.code == "WINDOW_IDENTITY_REJECTED"
    assert calls == []


def test_windows_uia_native_script_pins_process_handle_and_checks_identity_around_effects():
    assert "OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE" in _POWERSHELL
    assert "GetProcessTimes(process" in _POWERSHELL
    assert "ProcessIdToSessionId(processId" in _POWERSHELL
    assert "OpenProcessToken(process, TOKEN_QUERY" in _POWERSHELL
    assert "GetWindowThreadProcessId(hwnd" in _POWERSHELL
    assert '[DllImport("user32.dll", SetLastError=true)] private static extern uint GetWindowThreadProcessId' in _POWERSHELL
    assert "function Invoke-Exact" in _POWERSHELL and "$pin.AssertTarget($windowHandle)" in _POWERSHELL
    assert "finally {\n    $pin.Dispose()\n}" in _POWERSHELL


def test_windows_save_copy_refuses_source_id_drift_before_ui_effect(tmp_path):
    source = DesktopModelObservation("STANDALONE", standalone_model_id="id-A", active_model_label="model-A",
                                     fingerprint="a" * 64, dirty=False)
    selectors = {
        "active_model_label": _selector("windows", "active_model_label", pattern="read", readback="value"),
        "standalone_model_id": _selector("windows", "standalone_model_id", pattern="read", readback="value"),
        "model_fingerprint": _selector("windows", "model_fingerprint", pattern="read", readback="value"),
        "model_dirty": _selector("windows", "model_dirty", pattern="read", readback="value",
                                 states={"dirty": "Dirty", "clean": "Clean"}),
        "save_copy": _selector("windows", "save_copy", pattern="invoke"),
        "save_path": _selector("windows", "save_path", pattern="value", readback="value"),
        "save_confirm": _selector("windows", "save_confirm", pattern="invoke"),
        "save_complete": _selector("windows", "save_complete", pattern="read", readback="value",
                                    states={"idle": "Ready", "busy": "Saving", "complete": "Saved"}),
    }
    class Gateway:
        def __init__(self):
            self.saves = []

        def read(self, _identity, selector):
            return {
                "active_model_label": "model-A",
                "standalone_model_id": "id-B",  # Only the native source ID drifted.
                "model_fingerprint": "a" * 64,
                "model_dirty": "Clean",
            }[selector["selector_id"]]

        def save_copy(self, *args):
            self.saves.append(args)

    gateway = Gateway()
    adapter = WindowsDesktopAdapter(gateway=gateway)
    adapter._profile = lambda _identity: {"selectors": selectors, "profile_sha256": "b" * 64}
    with pytest.raises(DesktopOperationError) as caught:
        adapter.save_copy(WINDOWS_ID, tmp_path / "stage.mph", source)
    assert caught.value.code == "MODEL_BINDING_UNKNOWN"
    assert gateway.saves == []


def test_windows_and_macos_source_preservation_report_observed_current_model_id():
    source = DesktopModelObservation("STANDALONE", standalone_model_id="id-A", active_model_label="model-A",
                                     fingerprint="a" * 64, dirty=False)
    windows_selectors = {
        name: _selector("windows", name, pattern="read", readback="value",
                        states={"dirty": "Dirty", "clean": "Clean"} if name == "model_dirty" else None)
        for name in ("active_model_label", "standalone_model_id", "model_fingerprint", "model_dirty")
    }
    windows_values = {"active_model_label": "model-A", "standalone_model_id": "id-B",
                      "model_fingerprint": "a" * 64, "model_dirty": "Clean"}
    windows = WindowsDesktopAdapter(gateway=SimpleNamespace(
        read=lambda _identity, selector: windows_values[selector["selector_id"]]))
    windows._profile = lambda _identity: {"selectors": windows_selectors, "profile_sha256": "c" * 64}
    windows_result = windows.verify_source_preserved(WINDOWS_ID, source)
    assert windows_result.still_open is False and windows_result.standalone_model_id == "id-B"

    mac_selectors = {
        name: _selector("macos", name, pattern="read", readback="value",
                        states={"dirty": "Dirty", "clean": "Clean"} if name == "model_dirty" else None)
        for name in ("active_model_label", "standalone_model_id", "model_fingerprint", "model_dirty")
    }
    mac = MacOSDesktopAdapter(gateway=SimpleNamespace(
        read_selector=lambda _identity, selector: windows_values[selector["selector_id"]]))
    mac._profile = lambda _identity: {"selectors": mac_selectors, "profile_sha256": "d" * 64}
    mac_result = mac.verify_source_preserved(MAC_ID, source)
    assert mac_result.still_open is False and mac_result.standalone_model_id == "id-B"


def test_windows_adapter_uses_profile_gated_uia_for_shell_not_engine_route():
    source = b"// local-only W25 test source"
    received = []

    def runner(request, **_kwargs):
        received.append(request)
        return {"executed": True, "source_sha256": request["source_sha256"], "output": "returned",
                "busy_observed": True, "output_changed": True}

    selectors = {
        "shell_input": _selector("windows", "shell_input", property_id="input", pattern="value", readback="value"),
        "shell_run": _selector("windows", "shell_run", property_id="run", pattern="invoke"),
        "shell_output": _selector("windows", "shell_output", property_id="output", pattern="read", readback="text"),
        "shell_busy": _selector("windows", "shell_busy", property_id="busy", pattern="read", readback="value", states={"busy": "Running", "idle": "Ready"}),
    }
    adapter = WindowsDesktopAdapter(gateway=WindowsUIAutomationGateway(runner))
    adapter._profile = lambda _identity: {"selectors": selectors, "profile_sha256": "f" * 64}
    artifact = TrustedSourceArtifact("a" * 64, hashlib.sha256(source).hexdigest(), len(source), "test-registration", source)
    result = adapter.shell_execute(WINDOWS_ID, artifact)
    assert result["source_sha256"] == artifact.sha256
    assert result["window_identity_fingerprint"] == WINDOWS_ID.fingerprint()
    assert received[0]["action"] == "shell_execute"
    assert received[0]["source"] == source.decode("utf-8")


def _action_profile():
    selector_id = "action_" + "b" * 64
    selectors = {
        "active_model_label": _selector("windows", "active_model_label", property_id="label", pattern="read", readback="value"),
        selector_id: _selector("windows", selector_id, property_id="refresh", pattern="invoke"),
    }
    profile = _profile("windows", selectors=selectors)
    profile["actions"] = {"comsol.safe.refresh": {
        "selector_id": selector_id,
        "mutates_model": False,
        "before": {"selector_id": "active_model_label", "value": "model-one"},
        "after": {"selector_id": "active_model_label", "value": "model-one"},
    }}
    return validate_profile(profile, platform_name="windows", comsol_version=WINDOWS_ID.comsol_version,
                            os_build="26100")


def test_windows_action_requires_host_profile_and_exact_before_after_readbacks():
    profile = _action_profile()
    trace = []
    values = {"active_model_label": "model-one"}

    def runner(request, **_kwargs):
        trace.append((request["action"], request.get("selector", {}).get("selector_id")))
        if request["action"] == "read":
            return {"selector_id": request["selector"]["selector_id"],
                    "value": values[request["selector"]["selector_id"]]}
        return {"invoked": True, "selector_id": request["selector"]["selector_id"]}

    adapter = WindowsDesktopAdapter(gateway=WindowsUIAutomationGateway(runner))
    adapter._profile = lambda _identity: {**profile, "profile_sha256": "d" * 64}
    plan = adapter.resolve_action(WINDOWS_ID, {"action_id": "comsol.safe.refresh", "target": "window"})
    result = adapter.perform_action(WINDOWS_ID, plan)
    assert result["verified"] is True and result["precondition"] == result["postcondition"] == "MATCHED"
    assert [row[0] for row in trace] == ["read", "read", "invoke", "read"]
    with pytest.raises(DesktopOperationError) as caller_claim:
        adapter.resolve_action(WINDOWS_ID, {"action_id": "comsol.safe.refresh", "target": "window", "mutates_model": True})
    assert caller_claim.value.code == "ACTION_NOT_AUTHORIZED"

    trace.clear()
    values["active_model_label"] = "changed-before"
    with pytest.raises(DesktopOperationError) as stale:
        adapter.resolve_action(WINDOWS_ID, {"action_id": "comsol.safe.refresh", "target": "window"})
    assert stale.value.code == "ACTION_PRECONDITION_FAILED"
    assert not any(action == "invoke" for action, _selector_id in trace)

    trace.clear()
    values["active_model_label"] = "model-one"
    reads = 0

    def postcondition_runner(request, **_kwargs):
        nonlocal reads
        trace.append(request["action"])
        if request["action"] == "read":
            reads += 1
            value = "model-one" if reads < 3 else "different-model"
            return {"selector_id": request["selector"]["selector_id"], "value": value}
        return {"invoked": True, "selector_id": request["selector"]["selector_id"]}

    adapter.gateway = WindowsUIAutomationGateway(postcondition_runner)
    plan = adapter.resolve_action(WINDOWS_ID, {"action_id": "comsol.safe.refresh", "target": "window"})
    with pytest.raises(DesktopOperationError) as readback_loss:
        adapter.perform_action(WINDOWS_ID, plan)
    assert readback_loss.value.code == "ACTION_POSTCONDITION_UNKNOWN"
    assert readback_loss.value.safe_retry is False and trace.count("invoke") == 1


class _SCShareableContent:
    windows = []
    calls = []

    @classmethod
    def getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(cls, exclude, onscreen, callback):
        cls.calls.append((exclude, onscreen))
        callback(SimpleNamespace(windows=lambda: list(cls.windows)), None)


class _SCFilter:
    def __init__(self, window):
        self.window = window

    @classmethod
    def alloc(cls):
        return object.__new__(cls)

    def initWithDesktopIndependentWindow_(self, window):
        self.window = window
        return self


class _SCConfiguration:
    def __init__(self):
        self.width = None
        self.height = None

    @classmethod
    def alloc(cls):
        return object.__new__(cls)

    def init(self):
        self.width = self.height = None
        return self

    def setWidth_(self, value):
        self.width = value

    def setHeight_(self, value):
        self.height = value


class _SCScreenshotManager:
    calls = []

    @classmethod
    def captureImageWithFilter_configuration_completionHandler_(cls, content_filter, configuration, callback):
        cls.calls.append((content_filter, configuration))
        callback(object(), None)


class _Bitmap:
    @classmethod
    def alloc(cls):
        return object.__new__(cls)

    def initWithCGImage_(self, _image):
        return self

    def representationUsingType_properties_(self, kind, _properties):
        assert kind == "PNG"
        return _one_pixel_png()


def _sc_frameworks(pid=123):
    application = SimpleNamespace(processID=lambda: pid)
    frame = SimpleNamespace(size=SimpleNamespace(width=1.0, height=1.0))
    window = SimpleNamespace(windowID=lambda: 7, owningApplication=lambda: application,
                             frame=lambda: frame)
    _SCShareableContent.windows = [window]
    _SCShareableContent.calls = []
    _SCScreenshotManager.calls = []
    sc = SimpleNamespace(SCShareableContent=_SCShareableContent, SCContentFilter=_SCFilter,
                         SCStreamConfiguration=_SCConfiguration, SCScreenshotManager=_SCScreenshotManager)
    appkit = SimpleNamespace(NSBitmapImageRep=_Bitmap, NSBitmapImageFileTypePNG="PNG")
    return appkit, sc, window


def test_macos_capture_uses_exact_screencapturekit_window_and_real_png_bytes(monkeypatch):
    appkit, sc, window = _sc_frameworks()
    gateway = MacOSFrameworkGateway(capture_framework_loader=lambda: (appkit, sc))
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("14.0", ("", "", ""), ""))
    image = capture_macos_screen_capture_kit_window(MAC_ID, gateway=gateway)
    assert image.data == _one_pixel_png()
    assert image.format == "PNG" and image.region == "window" and image.scope == "target_window"
    assert image.target_identity_fingerprint == MAC_ID.fingerprint()
    assert _SCShareableContent.calls == [(False, True)]
    assert len(_SCScreenshotManager.calls) == 1
    content_filter, config = _SCScreenshotManager.calls[0]
    assert content_filter.window is window
    assert (config.width, config.height) == (1, 1)


def test_macos_capture_refuses_wrong_pid_and_shell_is_platform_typed_unsupported(monkeypatch):
    appkit, sc, _window = _sc_frameworks(pid=999)
    gateway = MacOSFrameworkGateway(capture_framework_loader=lambda: (appkit, sc))
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("14.0", ("", "", ""), ""))
    with pytest.raises(DesktopOperationError) as caught:
        capture_macos_screen_capture_kit_window(MAC_ID, gateway=gateway)
    assert caught.value.code == "WINDOW_IDENTITY_UNKNOWN"
    assert _SCScreenshotManager.calls == []
    adapter = MacOSDesktopAdapter(gateway=SimpleNamespace())
    with pytest.raises(DesktopOperationError) as shell:
        adapter.shell_execute(MAC_ID, None)
    assert shell.value.code == "UNSUPPORTED_PLATFORM"


def test_macos_adapter_capture_returns_only_exact_window_scope(monkeypatch):
    appkit, sc, _window = _sc_frameworks()

    class PermissionGateway(MacOSFrameworkGateway):
        def permissions(self):
            return {"accessibility_trusted": True, "screen_capture_allowed": True}

    gateway = PermissionGateway(capture_framework_loader=lambda: (appkit, sc))
    adapter = MacOSDesktopAdapter(gateway=gateway)
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("14.0", ("", "", ""), ""))
    result = adapter.capture(MAC_ID, "window")
    assert result["verified"] is True and result["scope"] == "target_window"
    assert result["format"] == "PNG" and result["image_bytes"] == _one_pixel_png()
    with pytest.raises(DesktopOperationError) as wrong_region:
        adapter.capture(MAC_ID, "graphics")
    assert wrong_region.value.code == "CAPTURE_SCOPE_REJECTED"


def test_macos_capture_refuses_pre_screencapturekit_os_before_permission_or_api(monkeypatch):
    class Gateway:
        calls = 0
        def permissions(self):
            self.calls += 1
            return {"screen_capture_allowed": True}
    gateway = Gateway()
    adapter = MacOSDesktopAdapter(gateway=gateway)
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("13.6.7", ("", "", ""), ""))
    with pytest.raises(DesktopOperationError) as caught:
        adapter.capture(MAC_ID, "window")
    assert caught.value.code == "SCREEN_CAPTURE_API_UNAVAILABLE"
    assert gateway.calls == 0


@pytest.mark.parametrize("release", ["12.3", "13.0", "13.6.7", "unreadable"])
def test_macos_screenshotmanager_gate_refuses_below_14_before_permissions(release, monkeypatch):
    class Gateway:
        calls = 0
        def permissions(self):
            self.calls += 1
            return {"screen_capture_allowed": True}
    gateway = Gateway()
    adapter = MacOSDesktopAdapter(gateway=gateway)
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: (release, ("", "", ""), ""))
    with pytest.raises(DesktopOperationError) as caught:
        adapter.capture(MAC_ID, "window")
    assert caught.value.code == "SCREEN_CAPTURE_API_UNAVAILABLE"
    assert gateway.calls == 0


@pytest.mark.parametrize("bad_window", [
    SimpleNamespace(windowID=7, owningApplication=lambda: SimpleNamespace(processID=lambda: 123), frame=lambda: SimpleNamespace(size=SimpleNamespace(width=1, height=1))),
    SimpleNamespace(windowID=lambda: 7, owningApplication=None, frame=lambda: SimpleNamespace(size=SimpleNamespace(width=1, height=1))),
    SimpleNamespace(windowID=lambda: 7, owningApplication=lambda: SimpleNamespace(processID=None), frame=lambda: SimpleNamespace(size=SimpleNamespace(width=1, height=1))),
    SimpleNamespace(windowID=lambda: 7, owningApplication=lambda: SimpleNamespace(processID=lambda: 123), frame=None),
])
def test_macos_screenshotmanager_requires_callable_nonnull_objc_getters(bad_window, monkeypatch):
    appkit, sc, _ = _sc_frameworks()
    _SCShareableContent.windows = [bad_window]
    _SCScreenshotManager.calls = []
    gateway = MacOSFrameworkGateway(capture_framework_loader=lambda: (appkit, sc))
    monkeypatch.setattr("comsol_mcp._desktop_capture.sys.platform", "darwin")
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("14.0", ("", "", ""), ""))
    with pytest.raises(DesktopOperationError) as caught:
        capture_macos_screen_capture_kit_window(MAC_ID, gateway=gateway)
    assert caught.value.code in {"WINDOW_IDENTITY_UNKNOWN", "SCREEN_CAPTURE_API_UNAVAILABLE"}
    assert _SCScreenshotManager.calls == []


def test_macos_framework_gateway_refuses_pre14_before_loading_screencapturekit(monkeypatch):
    calls = []
    gateway = MacOSFrameworkGateway(capture_framework_loader=lambda: calls.append("load") or (None, None))
    monkeypatch.setattr("comsol_mcp._desktop_macos.platform.mac_ver", lambda: ("13.6.7", ("", "", ""), ""))
    with pytest.raises(DesktopOperationError) as caught:
        gateway.capture_window(MAC_ID)
    assert caught.value.code == "SCREEN_CAPTURE_API_UNAVAILABLE"
    assert calls == []


def test_macos_gateway_sets_only_exact_profiled_ax_value_and_reads_it_back(monkeypatch):
    class AXValue:
        def __init__(self):
            self.value = ""
            self.calls = []

        def AXUIElementSetAttributeValue(self, element, name, value):
            self.calls.append(("set", name, value))
            element[name] = value
            return 0

        def AXUIElementCopyAttributeValue(self, element, name, _):
            self.calls.append(("read", name))
            return (0, element.get(name))

    ax = AXValue()
    gateway = MacOSFrameworkGateway(lambda: (ax, SimpleNamespace()))
    element = {}
    monkeypatch.setattr(gateway, "_find_exact_selector", lambda identity, selector: element)
    selector = _selector("macos", "save_path", pattern="value", readback="value")
    result = gateway.set_selector_value(MAC_ID, selector, "/project/copy.mph")
    assert result == {"set": True, "selector_id": "save_path", "readback": "/project/copy.mph"}
    assert ax.calls == [("set", "AXValue", "/project/copy.mph"), ("read", "AXValue")]
    with pytest.raises(DesktopOperationError):
        gateway.set_selector_value(MAC_ID, dict(selector, pattern="read"), "/project/copy.mph")
    assert len(ax.calls) == 2


def test_macos_gateway_save_copy_uses_only_three_observed_selectors(tmp_path, monkeypatch):
    gateway = MacOSFrameworkGateway(lambda: (SimpleNamespace(), SimpleNamespace()))
    trace = []
    monkeypatch.setattr(gateway, "perform_selector", lambda identity, selector: trace.append(("perform", selector["selector_id"])) or {"performed": True})
    monkeypatch.setattr(gateway, "set_selector_value", lambda identity, selector, value: trace.append(("set", selector["selector_id"], value)) or {"set": True})
    selectors = {
        "save_copy": _selector("macos", "save_copy", pattern="invoke"),
        "save_path": _selector("macos", "save_path", pattern="value", readback="value"),
        "save_confirm": _selector("macos", "save_confirm", pattern="invoke"),
    }
    destination = tmp_path / "copy.mph"
    result = gateway.save_copy(MAC_ID, selectors, str(destination))
    assert result["save_dialog_confirmed"] is True
    assert trace == [("perform", "save_copy"), ("set", "save_path", str(destination)),
                     ("perform", "save_confirm")]
    destination.touch()
    with pytest.raises(DesktopOperationError) as existing:
        gateway.save_copy(MAC_ID, selectors, str(destination))
    assert existing.value.code == "SAVE_COPY_PATH_REJECTED"


def test_macos_adapter_save_copy_requires_baseline_and_verifies_bytes_and_source(tmp_path):
    class Gateway:
        def __init__(self):
            self.saves = []
            self.completion_values = ["Ready", "Saving", "Saved"]

        def read_selector(self, _identity, selector):
            if selector["selector_id"] == "save_complete":
                return self.completion_values.pop(0) if self.completion_values else "Saved"
            return {
                "active_model_label": "standalone-A",
                "standalone_model_id": "id-A",
                "model_fingerprint": "a" * 64,
                "model_dirty": "Dirty",
            }[selector["selector_id"]]

        def save_copy(self, _identity, selectors, destination):
            self.saves.append((tuple(sorted(selectors)), destination))
            Path(destination).write_bytes(b"synthetic-mph-bytes")

    selectors = {
        "active_model_label": _selector("macos", "active_model_label", pattern="read", readback="value"),
        "standalone_model_id": _selector("macos", "standalone_model_id", pattern="read", readback="value"),
        "model_fingerprint": _selector("macos", "model_fingerprint", pattern="read", readback="value"),
        "model_dirty": _selector("macos", "model_dirty", pattern="read", readback="value", states={"dirty": "Dirty", "clean": "Clean"}),
        "save_copy": _selector("macos", "save_copy", pattern="invoke"),
        "save_path": _selector("macos", "save_path", pattern="value", readback="value"),
        "save_confirm": _selector("macos", "save_confirm", pattern="invoke"),
        "save_complete": _selector("macos", "save_complete", pattern="read", readback="value",
                                    states={"idle": "Ready", "busy": "Saving", "complete": "Saved"}),
    }
    gateway = Gateway()
    adapter = MacOSDesktopAdapter(gateway=gateway)
    adapter._profile = lambda _identity: {"selectors": selectors, "profile_sha256": "b" * 64}
    source = DesktopModelObservation("STANDALONE", standalone_model_id="id-A", fingerprint="a" * 64,
                                     dirty=True, active_model_label="standalone-A")
    destination = tmp_path / "standalone-copy.mph"
    result = adapter.save_copy(MAC_ID, destination, source)
    assert result.path == str(destination)
    assert result.sha256 is None and result.size_bytes is None
    assert result.source_preserved is True and result.source_dirty_after is True
    assert gateway.saves == [(tuple(sorted({"save_copy", "save_path", "save_confirm", "save_complete"})), str(destination))]
    assert adapter.save_copy_completion_state(MAC_ID, side_effect_started=True) == "busy"
    assert adapter.save_copy_completion_state(MAC_ID, side_effect_started=True) == "complete"
    with pytest.raises(DesktopOperationError) as stale:
        adapter.save_copy(MAC_ID, tmp_path / "stale.mph", DesktopModelObservation("STANDALONE", active_model_label="other"))
    assert stale.value.code == "MODEL_BINDING_UNKNOWN"
    assert len(gateway.saves) == 1


def test_platform_save_copy_requires_observed_completion_before_any_native_dialog_effect(tmp_path):
    source = DesktopModelObservation("STANDALONE", standalone_model_id="id-A", active_model_label="model-A",
                                     fingerprint="a" * 64, dirty=False)
    selectors = {
        "active_model_label": _selector("windows", "active_model_label", pattern="read", readback="value"),
        "standalone_model_id": _selector("windows", "standalone_model_id", pattern="read", readback="value"),
        "model_fingerprint": _selector("windows", "model_fingerprint", pattern="read", readback="value"),
        "model_dirty": _selector("windows", "model_dirty", pattern="read", readback="value",
                                 states={"dirty": "Dirty", "clean": "Clean"}),
        "save_copy": _selector("windows", "save_copy", pattern="invoke"),
        "save_path": _selector("windows", "save_path", pattern="value", readback="value"),
        "save_confirm": _selector("windows", "save_confirm", pattern="invoke"),
    }

    class Gateway:
        def __init__(self):
            self.effects = []

        def read(self, _identity, selector):
            return {"active_model_label": "model-A", "standalone_model_id": "id-A",
                    "model_fingerprint": "a" * 64, "model_dirty": "Clean"}[selector["selector_id"]]

        def save_copy(self, *args):
            self.effects.append(args)

    gateway = Gateway()
    adapter = WindowsDesktopAdapter(gateway=gateway)
    adapter._profile = lambda _identity: {"selectors": selectors, "profile_sha256": "d" * 64}
    with pytest.raises(DesktopOperationError) as caught:
        adapter.save_copy(WINDOWS_ID, tmp_path / "must-not-write.mph", source)
    assert caught.value.code == "DESKTOP_SELECTOR_UNAVAILABLE"
    assert gateway.effects == []
