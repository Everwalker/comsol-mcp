"""Offline tests for process-query output redaction in the Windows probe."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


RUNNER_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_function_evaluate_probe.py"
_SPEC = importlib.util.spec_from_file_location("function_evaluate_probe_under_test", RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = probe
_SPEC.loader.exec_module(probe)

SECRET_PATH = r"C:\Users\example\private\secret-comsol\comsolmphserver.exe"
SECRET_COMMAND_LINE = r'"C:\Users\example\private\comsolmphserver.exe" -secret "never-log-this"'


def _process_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ProcessId": 4312,
        "Name": "comsolmphserver.exe",
        "PathMissing": False,
        "CommandLineMissing": False,
        "ExecutablePath": SECRET_PATH,
        "CommandLine": SECRET_COMMAND_LINE,
        "CreationDate": "2026-09-28T12:00:00.000000Z",
    }
    row.update(overrides)
    return row


def _completed(stdout: str = "", *, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_empty_existing_process_query_passes_with_only_safe_projection() -> None:
    with patch.object(probe.subprocess, "run", return_value=_completed("\r\n#< CLIXML\r\n")) as run:
        assert probe.assert_no_existing_comsol_processes() == []

    command = run.call_args.args[0][-1]
    assert "$ErrorActionPreference='Stop'" in command
    assert "ProcessId,Name" in command
    assert "PathMissing" in command
    assert "CommandLineMissing" in command
    assert "ExecutablePath,CommandLine" not in command
    assert " + $_.ExecutablePath" not in command
    assert " + $_.CommandLine" not in command


def test_existing_process_is_refused_and_only_name_pid_and_missing_flags_escape() -> None:
    result = _completed(json.dumps(_process_row()))
    with patch.object(probe.subprocess, "run", return_value=result):
        with pytest.raises(RuntimeError) as caught:
            probe.assert_no_existing_comsol_processes()

    message = str(caught.value)
    assert "comsolmphserver.exe" in message
    assert "PID=4312" in message
    assert "PathMissing=False" in message
    assert "CommandLineMissing=False" in message
    assert SECRET_PATH not in message
    assert SECRET_COMMAND_LINE not in message


@pytest.mark.parametrize(
    "result",
    [
        _completed("SECRET_PATH=" + SECRET_PATH + " SECRET_COMMAND=" + SECRET_COMMAND_LINE, returncode=1,
                   stderr=SECRET_PATH + " " + SECRET_COMMAND_LINE),
        _completed("not-json " + SECRET_PATH + " " + SECRET_COMMAND_LINE),
    ],
)
def test_process_query_failures_refuse_start_without_echoing_output(result: SimpleNamespace) -> None:
    with patch.object(probe.subprocess, "run", return_value=result):
        with pytest.raises(RuntimeError) as caught:
            probe.assert_no_existing_comsol_processes()

    message = str(caught.value)
    assert "refusing to start" in message
    assert SECRET_PATH not in message
    assert SECRET_COMMAND_LINE not in message


def test_process_query_exception_fails_closed_without_echoing_exception() -> None:
    failure = subprocess.TimeoutExpired(
        cmd=["powershell.exe", SECRET_PATH, SECRET_COMMAND_LINE],
        timeout=15,
        output=SECRET_PATH,
        stderr=SECRET_COMMAND_LINE,
    )
    with patch.object(probe.subprocess, "run", side_effect=failure):
        with pytest.raises(RuntimeError) as caught:
            probe.assert_no_existing_comsol_processes()

    message = str(caught.value)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "refusing to start" in message
    assert SECRET_PATH not in message
    assert SECRET_COMMAND_LINE not in message
    assert SECRET_PATH not in rendered
    assert SECRET_COMMAND_LINE not in rendered


def test_unexpected_process_name_is_rejected_without_echoing_it() -> None:
    result = _completed(json.dumps(_process_row(Name=SECRET_PATH)))
    with patch.object(probe.subprocess, "run", return_value=result):
        with pytest.raises(RuntimeError) as caught:
            probe.assert_no_existing_comsol_processes()

    assert "refusing to start" in str(caught.value)
    assert SECRET_PATH not in str(caught.value)


def test_owned_process_cim_and_summary_are_redacted() -> None:
    with patch.object(probe.subprocess, "run", return_value=_completed(json.dumps(_process_row()))) as run:
        identity = probe.cim_process(4312)

    assert identity == {
        "process_id": 4312,
        "name": "comsolmphserver.exe",
        "path_missing": False,
        "command_line_missing": False,
        "creation_date": "2026-09-28T12:00:00.000000Z",
    }
    serialized_summary = json.dumps({"actual_process_cim": identity})
    assert SECRET_PATH not in serialized_summary
    assert SECRET_COMMAND_LINE not in serialized_summary
    assert "ExecutablePath" not in serialized_summary
    assert "CommandLine" not in serialized_summary
    command = run.call_args.args[0][-1]
    assert "$ErrorActionPreference='Stop'" in command
    assert "Select-Object ProcessId,Name,CreationDate" in command
    assert "Select-Object ProcessId,ExecutablePath,CommandLine" not in command


def test_owned_process_cim_query_failure_is_non_disclosing() -> None:
    result = _completed(
        SECRET_PATH + " " + SECRET_COMMAND_LINE,
        returncode=1,
        stderr=SECRET_PATH + " " + SECRET_COMMAND_LINE,
    )
    with patch.object(probe.subprocess, "run", return_value=result):
        identity = probe.cim_process(4312)

    assert identity is None
    assert SECRET_PATH not in json.dumps({"actual_process_cim": identity})
    assert SECRET_COMMAND_LINE not in json.dumps({"actual_process_cim": identity})


def test_owned_process_cim_exception_does_not_escape_or_disclose() -> None:
    failure = subprocess.TimeoutExpired(
        cmd=["powershell.exe", SECRET_PATH, SECRET_COMMAND_LINE],
        timeout=15,
        output=SECRET_PATH,
        stderr=SECRET_COMMAND_LINE,
    )
    with patch.object(probe.subprocess, "run", side_effect=failure):
        identity = probe.cim_process(4312)

    summary = json.dumps({"actual_process_cim": identity})
    assert identity is None
    assert SECRET_PATH not in summary
    assert SECRET_COMMAND_LINE not in summary


def test_owned_process_cim_rejects_unexpected_creation_text_without_disclosing_it() -> None:
    result = _completed(json.dumps(_process_row(CreationDate=SECRET_COMMAND_LINE)))
    with patch.object(probe.subprocess, "run", return_value=result):
        identity = probe.cim_process(4312)

    summary = json.dumps({"actual_process_cim": identity})
    assert identity is None
    assert SECRET_PATH not in summary
    assert SECRET_COMMAND_LINE not in summary
