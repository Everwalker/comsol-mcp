"""Late-bound Windows UI Automation gateway.

The helper is a fixed PowerShell/.NET UIA client launched without a shell.
Request data travels only as strict JSON on stdin; registered Java source is
placed in an observed UIA ValuePattern and never interpolated into a command.
This module does not send global keyboard/mouse input.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from typing import Any, Callable, Mapping

from ._desktop_service import DesktopOperationError, WindowIdentity


_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_BYTES = 512 * 1024
_POWERSHELL = r'''$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
$raw = [Console]::In.ReadToEnd()
$r = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$scope = [System.Windows.Automation.TreeScope]::Descendants
$windowHandle = [IntPtr]::new([Int64]$r.hwnd)
$window = [System.Windows.Automation.AutomationElement]::FromHandle($windowHandle)
if ($null -eq $window -or $window.Current.ProcessId -ne [int]$r.process_id) { throw 'UIA_WINDOW_PROCESS_MISMATCH' }
function Find-Exact($root, $sel, $expectedPid) {
    $p = $sel.properties
    $conditions = [System.Collections.Generic.List[System.Windows.Automation.Condition]]::new()
    $conditions.Add([System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::AutomationIdProperty, [string]$p.automation_id))
    $conditions.Add([System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::FrameworkIdProperty, [string]$p.framework_id))
    if ($p.PSObject.Properties.Name -contains 'name') { $conditions.Add([System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, [string]$p.name)) }
    $condition = [System.Windows.Automation.AndCondition]::new([System.Windows.Automation.Condition[]]$conditions.ToArray())
    $found = $root.FindAll($scope, $condition)
    $matches = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
    for ($i = 0; $i -lt $found.Count; $i++) {
        $item = $found.Item($i)
        if ($item.Current.ProcessId -eq [int]$expectedPid -and $item.Current.ControlType.Id -eq [int]$p.control_type_id) { $matches.Add($item) }
    }
    if ($matches.Count -ne 1) { throw ('UIA_SELECTOR_CARDINALITY:' + $sel.selector_id + ':' + $matches.Count) }
    return $matches[0]
}
function Read-Exact($element, $selector) {
    $pattern = $null
    switch ([string]$selector.readback) {
        'name' { return [string]$element.Current.Name }
        'value' {
            if (-not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { throw ('UIA_VALUE_PATTERN_UNAVAILABLE:' + $selector.selector_id) }
            return [string]$pattern.Current.Value
        }
        'text' {
            if (-not $element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) { throw ('UIA_TEXT_PATTERN_UNAVAILABLE:' + $selector.selector_id) }
            return [string]$pattern.DocumentRange.GetText(200001)
        }
        default { throw 'UIA_READBACK_UNSUPPORTED' }
    }
}
function Invoke-Exact($element, $selector) {
    if ([string]$selector.pattern -eq 'selection') {
        $pattern = $null
        if (-not $element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) { throw ('UIA_SELECTION_PATTERN_UNAVAILABLE:' + $selector.selector_id) }
        $pattern.Select()
        return [bool]$pattern.Current.IsSelected
    } elseif ([string]$selector.pattern -eq 'invoke') {
        $pattern = $null
        if (-not $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { throw ('UIA_INVOKE_PATTERN_UNAVAILABLE:' + $selector.selector_id) }
        $pattern.Invoke()
        return $true
    } else { throw ('UIA_PROFILE_PATTERN_REJECTED:' + $selector.selector_id) }
}
function Set-Exact($element, $selector, $value) {
    if ([string]$selector.pattern -ne 'value') { throw ('UIA_PROFILE_PATTERN_REJECTED:' + $selector.selector_id) }
    $pattern = $null
    if (-not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { throw ('UIA_VALUE_PATTERN_UNAVAILABLE:' + $selector.selector_id) }
    $pattern.SetValue([string]$value)
    if ([string]$pattern.Current.Value -cne [string]$value) { throw ('UIA_VALUE_READBACK_MISMATCH:' + $selector.selector_id) }
}
$result = $null
switch ([string]$r.action) {
    'read' {
        $sel = $r.selector
        $element = Find-Exact $window $sel $r.process_id
        $result = @{ value = (Read-Exact $element $sel); selector_id = $sel.selector_id }
    }
    'invoke' {
        $sel = $r.selector
        $element = Find-Exact $window $sel $r.process_id
        Invoke-Exact $element $sel
        $result = @{ invoked = $true; selector_id = $sel.selector_id }
    }
    'select' {
        $sel = $r.selector
        $element = Find-Exact $window $sel $r.process_id
        $selected = Invoke-Exact $element $sel
        $result = @{ selected = [bool]$selected; selector_id = $sel.selector_id; readback = (Read-Exact $element $sel) }
    }
    'set_value' {
        $sel = $r.selector
        $element = Find-Exact $window $sel $r.process_id
        Set-Exact $element $sel $r.value
        $sha = [Security.Cryptography.SHA256]::Create(); $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$r.value)); $sha.Dispose()
        $result = @{ set = $true; selector_id = $sel.selector_id; value_sha256 = ([BitConverter]::ToString($hash).Replace('-', '').ToLowerInvariant()) }
    }
    'shell_execute' {
        if (-not $r.source -or [Text.Encoding]::UTF8.GetByteCount([string]$r.source) -gt 524288) { throw 'UIA_SOURCE_LIMIT' }
        $inputSel = $r.selectors.shell_input; $runSel = $r.selectors.shell_run
        $outputSel = $r.selectors.shell_output; $busySel = $r.selectors.shell_busy
        $inputElement = Find-Exact $window $inputSel $r.process_id
        $runElement = Find-Exact $window $runSel $r.process_id
        $outputElement = Find-Exact $window $outputSel $r.process_id
        $busyElement = Find-Exact $window $busySel $r.process_id
        $idle = [string]$busySel.states.idle; $busyValue = [string]$busySel.states.busy
        $beforeBusy = Read-Exact $busyElement $busySel
        if ($beforeBusy -cne $idle) { throw 'UIA_SHELL_NOT_IDLE' }
        $beforeOutput = Read-Exact $outputElement $outputSel
        Set-Exact $inputElement $inputSel ([string]$r.source)
        Invoke-Exact $runElement $runSel
        $deadline = [DateTime]::UtcNow.AddSeconds([int]$r.timeout_seconds)
        $busySeen = $false; $last = $beforeOutput; $stable = 0; $finalOutput = $beforeOutput
        while ([DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 250
            $busyNow = Read-Exact (Find-Exact $window $busySel $r.process_id) $busySel
            if ($busyNow -ceq $busyValue) { $busySeen = $true; $stable = 0; continue }
            if ($busyNow -cne $idle) { throw 'UIA_SHELL_BUSY_STATE_UNRECOGNIZED' }
            $finalOutput = Read-Exact (Find-Exact $window $outputSel $r.process_id) $outputSel
            if ($finalOutput.Length -gt 200000) { throw 'UIA_SHELL_OUTPUT_LIMIT' }
            if ($finalOutput -ceq $last) { $stable++ } else { $stable = 0; $last = $finalOutput }
            if ($stable -ge 3 -and ($busySeen -or $finalOutput -cne $beforeOutput)) { break }
        }
        if ($stable -lt 3 -or (-not $busySeen -and $finalOutput -ceq $beforeOutput)) { throw 'UIA_SHELL_COMPLETION_UNVERIFIED' }
        $result = @{ executed = $true; output = $finalOutput; output_changed = ($finalOutput -cne $beforeOutput); busy_observed = $busySeen; source_sha256 = $r.source_sha256 }
    }
    'save_copy' {
        $saveCopy = Find-Exact $window $r.selectors.save_copy $r.process_id
        Invoke-Exact $saveCopy $r.selectors.save_copy
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        $dialog = $null; $pathElement = $null; $confirm = $null
        while ([DateTime]::UtcNow -lt $deadline -and $null -eq $pathElement) {
            Start-Sleep -Milliseconds 200
            $root = [System.Windows.Automation.AutomationElement]::RootElement
            $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$r.process_id)
            $top = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
            $candidates = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
            for ($i = 0; $i -lt $top.Count; $i++) { try { $candidates.Add((Find-Exact $top.Item($i) $r.selectors.save_path $r.process_id)) } catch {} }
            if ($candidates.Count -gt 1) { throw 'UIA_SAVE_DIALOG_AMBIGUOUS' }
            if ($candidates.Count -eq 1) { $pathElement = $candidates[0] }
        }
        if ($null -eq $pathElement) { throw 'UIA_SAVE_DIALOG_NOT_FOUND' }
        Set-Exact $pathElement $r.selectors.save_path ([string]$r.destination)
        if (Test-Path -LiteralPath ([string]$r.destination)) { throw 'UIA_SAVE_DESTINATION_APPEARED' }
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$r.process_id)
        $top = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $condition)
        $buttons = [System.Collections.Generic.List[System.Windows.Automation.AutomationElement]]::new()
        for ($i = 0; $i -lt $top.Count; $i++) { try { $buttons.Add((Find-Exact $top.Item($i) $r.selectors.save_confirm $r.process_id)) } catch {} }
        if ($buttons.Count -ne 1) { throw 'UIA_SAVE_CONFIRM_AMBIGUOUS' }
        Invoke-Exact $buttons[0] $r.selectors.save_confirm
        $result = @{ save_dialog_confirmed = $true; destination = [string]$r.destination }
    }
    default { throw 'UIA_ACTION_UNSUPPORTED' }
}
ConvertTo-Json -InputObject $result -Compress -Depth 8
'''


def _encode_powershell(script: str = _POWERSHELL) -> str:
    return base64.b64encode(script.encode("utf-16le")).decode("ascii")


def _run_powershell(request: Mapping[str, Any], *, timeout_s: float = 65.0) -> Mapping[str, Any]:
    if sys.platform != "win32":
        raise DesktopOperationError("UNSUPPORTED_PLATFORM", "Windows UI Automation is only available on Windows", safe_retry=True)
    raw = json.dumps(dict(request), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > _MAX_REQUEST_BYTES:
        raise DesktopOperationError("UIA_REQUEST_LIMIT", "UI Automation request exceeds its fixed size limit", safe_retry=True)
    executable = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"
    try:
        completed = subprocess.run(
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", _encode_powershell()],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s,
            check=False, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise DesktopOperationError("UIA_TIMEOUT", "Windows UI Automation gateway exceeded its bounded deadline", side_effect_started=True, safe_retry=False) from exc
    except OSError as exc:
        raise DesktopOperationError("UIA_UNAVAILABLE", "Windows UI Automation gateway could not start its OS-provided PowerShell host", safe_retry=True) from exc
    if len(completed.stdout) > _MAX_RESPONSE_BYTES or len(completed.stderr) > 64 * 1024:
        raise DesktopOperationError("UIA_RESPONSE_LIMIT", "Windows UI Automation gateway returned oversized output", side_effect_started=True, safe_retry=False)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace")[-2048:].strip()
        raise DesktopOperationError("UIA_ACTION_UNVERIFIED", f"Windows UI Automation did not verify the requested action ({message or 'no detail'})", side_effect_started=True, safe_retry=False)
    try:
        value = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopOperationError("UIA_READBACK_UNKNOWN", "Windows UI Automation returned no strict JSON readback", side_effect_started=True, safe_retry=False) from exc
    if not isinstance(value, dict):
        raise DesktopOperationError("UIA_READBACK_UNKNOWN", "Windows UI Automation readback must be an object", side_effect_started=True, safe_retry=False)
    return value


class WindowsUIAutomationGateway:
    """Exact-process UIA gateway; ``runner`` is a software-test seam only."""

    def __init__(self, runner: Callable[..., Mapping[str, Any]] | None = None):
        self._runner = runner or _run_powershell

    @staticmethod
    def _base(identity: WindowIdentity) -> dict[str, Any]:
        if identity.platform != "windows" or not identity.native_window_id.startswith("HWND:"):
            raise DesktopOperationError("WINDOW_IDENTITY_REJECTED", "UIA requires a canonical observed Windows HWND", safe_retry=True)
        try:
            hwnd = int(identity.native_window_id.removeprefix("HWND:"), 16)
        except ValueError as exc:
            raise DesktopOperationError("WINDOW_IDENTITY_REJECTED", "UIA HWND is malformed", safe_retry=True) from exc
        if hwnd <= 0 or identity.process_id <= 0 or identity.process_birth.upper() in {"UNKNOWN", "UNVERIFIED"}:
            raise DesktopOperationError("WINDOW_IDENTITY_REJECTED", "UIA requires complete process birth and HWND evidence", safe_retry=True)
        return {"hwnd": hwnd, "process_id": identity.process_id}

    def read(self, identity: WindowIdentity, selector: Mapping[str, Any]) -> str:
        result = self._runner({**self._base(identity), "action": "read", "selector": dict(selector)})
        if result.get("selector_id") != selector.get("selector_id") or not isinstance(result.get("value"), str):
            raise DesktopOperationError("UIA_READBACK_UNKNOWN", "UIA selector readback did not match its exact selector", side_effect_started=True, safe_retry=False)
        return result["value"]

    def invoke(self, identity: WindowIdentity, selector: Mapping[str, Any], *, selection: bool = False) -> Mapping[str, Any]:
        action = "select" if selection else "invoke"
        result = self._runner({**self._base(identity), "action": action, "selector": dict(selector)})
        expected = "selected" if selection else "invoked"
        if result.get(expected) is not True or result.get("selector_id") != selector.get("selector_id"):
            raise DesktopOperationError("UIA_READBACK_UNKNOWN", "UIA did not confirm the exact control pattern", side_effect_started=True, safe_retry=False)
        if selection and (not isinstance(result.get("readback"), str)
                          or not result["readback"]):
            raise DesktopOperationError("UIA_READBACK_UNKNOWN", "UIA selection did not read back the exact selected control", side_effect_started=True, safe_retry=False)
        return result

    def set_value(self, identity: WindowIdentity, selector: Mapping[str, Any], value: str) -> None:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_SOURCE_BYTES:
            raise DesktopOperationError("UIA_VALUE_LIMIT", "UIA text value exceeds the fixed bound", safe_retry=True)
        expected = __import__("hashlib").sha256(value.encode("utf-8")).hexdigest()
        result = self._runner({**self._base(identity), "action": "set_value", "selector": dict(selector), "value": value})
        if result.get("set") is not True or result.get("selector_id") != selector.get("selector_id") or result.get("value_sha256") != expected:
            raise DesktopOperationError("UIA_READBACK_UNKNOWN", "UIA value setter did not return exact content-hash readback", side_effect_started=True, safe_retry=False)

    def execute_shell(self, identity: WindowIdentity, selectors: Mapping[str, Any], source: bytes,
                      source_sha256: str, *, timeout_s: int = 60) -> Mapping[str, Any]:
        if not isinstance(source, bytes) or len(source) > _MAX_SOURCE_BYTES:
            raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "registered Java source exceeds the UIA shell size bound", safe_retry=True)
        import hashlib
        actual = hashlib.sha256(source).hexdigest()
        if actual != source_sha256:
            raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "registered Java source changed before the UIA gateway", safe_retry=True)
        try:
            text = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DesktopOperationError("SOURCE_ARTIFACT_UNVERIFIED", "Java source is not valid UTF-8", safe_retry=True) from exc
        if type(timeout_s) is not int or not 1 <= timeout_s <= 60:
            raise DesktopOperationError("INVALID_REQUEST", "UIA shell timeout must be between 1 and 60 seconds", safe_retry=True)
        result = self._runner({**self._base(identity), "action": "shell_execute", "selectors": dict(selectors),
                               "source": text, "source_sha256": actual, "timeout_seconds": timeout_s}, timeout_s=timeout_s + 5)
        if (result.get("executed") is not True or result.get("source_sha256") != actual
                or not isinstance(result.get("output"), str) or len(result["output"]) > 200000
                or (result.get("busy_observed") is not True and result.get("output_changed") is not True)):
            raise DesktopOperationError("UIA_SHELL_READBACK_UNKNOWN", "Java Shell execution lacks exact source and completion/output readback", side_effect_started=True, safe_retry=False)
        return {**result, "source_sha256": actual, "output_sha256": __import__("hashlib").sha256(result["output"].encode("utf-8")).hexdigest(),
                "gateway": "WINDOWS_UIAUTOMATION_COMSOL_JAVA_SHELL"}

    def save_copy(self, identity: WindowIdentity, selectors: Mapping[str, Any], destination: str) -> None:
        result = self._runner({**self._base(identity), "action": "save_copy", "selectors": dict(selectors), "destination": destination}, timeout_s=20)
        if result.get("save_dialog_confirmed") is not True or result.get("destination") != destination:
            raise DesktopOperationError("SAVE_COPY_UNVERIFIED", "UIA save dialog did not confirm the exact target path", side_effect_started=True, safe_retry=False)
