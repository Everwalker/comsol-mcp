"""Shared pywin32 process-birth and session identity extraction.

The pywin32 API returns ``GetProcessTimes`` as a mapping whose
``CreationTime`` value is an aware UTC ``pywintypes.datetime``.  Session
lookup is exposed by ``win32ts``, not ``win32process``.  Keeping these details
here makes the metadata observer and capture revalidation use the same
fail-closed identity encoding.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


class Win32IdentityError(ValueError):
    """pywin32 did not provide a complete process identity."""


def encode_creation_time_utc(process_times: Mapping[str, Any]) -> str:
    """Encode the pywin32 creation datetime as stable UTC ISO-8601 text.

    ``FileTimeToSystemTime`` currently exposes millisecond precision through
    pywin32's datetime value.  Preserve every supplied fractional digit and
    never coerce the value to an integer timestamp (which would discard the
    date and time).  The trailing ``Z`` makes the UTC basis explicit.
    """
    if not isinstance(process_times, Mapping):
        raise Win32IdentityError("GetProcessTimes must return a mapping")
    created = process_times.get("CreationTime")
    if not isinstance(created, datetime):
        raise Win32IdentityError("GetProcessTimes CreationTime must be a datetime")
    if created.tzinfo is None or created.utcoffset() is None:
        raise Win32IdentityError("GetProcessTimes CreationTime must be timezone-aware")
    created_utc = created.astimezone(timezone.utc)
    return "UTC:" + created_utc.isoformat(timespec="microseconds").replace("+00:00", "Z")


def observe_process_birth_and_session(
    win32process: Any,
    win32ts: Any,
    process_handle: Any,
    process_id: int,
) -> tuple[str, int]:
    """Read process birth from ``win32process`` and session from ``win32ts``."""
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise Win32IdentityError("process id must be a positive integer")
    get_process_times = getattr(win32process, "GetProcessTimes", None)
    if not callable(get_process_times):
        raise Win32IdentityError("win32process.GetProcessTimes is unavailable")
    process_times = get_process_times(process_handle)
    birth = encode_creation_time_utc(process_times)

    process_id_to_session_id = getattr(win32ts, "ProcessIdToSessionId", None)
    if not callable(process_id_to_session_id):
        raise Win32IdentityError("win32ts.ProcessIdToSessionId is unavailable")
    session_id = process_id_to_session_id(process_id)
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 0:
        raise Win32IdentityError("win32ts.ProcessIdToSessionId returned an invalid session id")
    return birth, session_id
