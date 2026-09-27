from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from comsol_mcp._desktop_win32_identity import (
    Win32IdentityError,
    encode_creation_time_utc,
    observe_process_birth_and_session,
)


PyWinDateTimeFixture = type("datetime", (datetime,), {"__module__": "pywintypes"})


def _pywin_datetime(*args, tzinfo=timezone.utc):
    return PyWinDateTimeFixture(*args, tzinfo=tzinfo)


def test_creation_time_encodes_real_pywin32_mapping_shape_and_fractional_utc():
    value = _pywin_datetime(2026, 9, 26, 12, 34, 56, 123000)

    encoded = encode_creation_time_utc({
        "CreationTime": value,
        "ExitTime": _pywin_datetime(2026, 9, 26, 12, 34, 57),
        "KernelTime": 100,
        "UserTime": 200,
    })

    assert encoded == "UTC:2026-09-26T12:34:56.123000Z"


def test_creation_time_normalizes_aware_non_utc_value_without_losing_fraction():
    plus_two = timezone(timedelta(hours=2))
    value = _pywin_datetime(2026, 9, 26, 14, 34, 56, 987000, tzinfo=plus_two)

    assert encode_creation_time_utc({"CreationTime": value}) == "UTC:2026-09-26T12:34:56.987000Z"


@pytest.mark.parametrize("process_times", [
    (datetime(2026, 9, 26, tzinfo=timezone.utc), None, 0, 0),
    {},
    {"CreationTime": None},
    {"CreationTime": "2026-09-26T12:00:00Z"},
    {"CreationTime": datetime(2026, 9, 26)},
])
def test_creation_time_rejects_non_mapping_missing_mistyped_or_naive_values(process_times):
    with pytest.raises(Win32IdentityError):
        encode_creation_time_utc(process_times)


def test_session_api_is_called_on_win32ts_not_win32process():
    process_handle = object()
    calls = []
    win32process = SimpleNamespace(
        GetProcessTimes=lambda handle: calls.append(("times", handle)) or {
            "CreationTime": _pywin_datetime(2026, 9, 26, 12, 34, 56, 789000),
            "ExitTime": _pywin_datetime(2026, 9, 26, 12, 34, 57),
            "KernelTime": 123,
            "UserTime": 456,
        },
    )
    win32ts = SimpleNamespace(
        ProcessIdToSessionId=lambda pid: calls.append(("session", pid)) or 7,
    )

    observed = observe_process_birth_and_session(win32process, win32ts, process_handle, 321)

    assert observed == ("UTC:2026-09-26T12:34:56.789000Z", 7)
    assert calls == [("times", process_handle), ("session", 321)]
    assert not hasattr(win32process, "ProcessIdToSessionId")
    assert callable(win32ts.ProcessIdToSessionId)


def test_missing_win32ts_api_and_invalid_session_id_fail_closed():
    win32process = SimpleNamespace(GetProcessTimes=lambda _handle: {
        "CreationTime": _pywin_datetime(2026, 9, 26, tzinfo=timezone.utc),
    })

    with pytest.raises(Win32IdentityError, match="win32ts.ProcessIdToSessionId"):
        observe_process_birth_and_session(win32process, SimpleNamespace(), object(), 321)

    for bad_session in (True, -1, "7"):
        win32ts = SimpleNamespace(ProcessIdToSessionId=lambda _pid, value=bad_session: value)
        with pytest.raises(Win32IdentityError, match="invalid session id"):
            observe_process_birth_and_session(win32process, win32ts, object(), 321)


@pytest.mark.parametrize("pid", [True, 0, -1, "321"])
def test_invalid_process_id_fails_before_native_api_calls(pid):
    with pytest.raises(Win32IdentityError, match="positive integer"):
        observe_process_birth_and_session(SimpleNamespace(), SimpleNamespace(), object(), pid)
