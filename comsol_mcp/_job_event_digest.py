"""Incremental canonical SHA-256 digests for one v1 job-event sequence (T35 helper).

This internal helper lets a caller keep the exact v1 snapshot digest pair while
streaming event rows, so the full event list and its cumulative encoded bytes
never need to be retained in memory:

- events digest  == sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True,
  separators=(",", ":"), allow_nan=False).encode("utf-8"))
- ids digest     == sha256(",".join(str(row["id"]) for row in rows).encode("ascii"))

The original whole-list encoders are ``_event_digest`` and ``_event_ids_digest``
in ``comsol_mcp/_g3_solver_ops.py`` (lines 644-651), with the same canonical
JSON options used by ``_job_event_snapshot_sha256`` in
``comsol_mcp/_operation_store.py`` (lines 155-160).  Python's json encoder
emits an array as ``b"["`` plus each element's own canonical encoding joined by
``b","`` and closed by ``b"]"``, so feeding each row's standalone canonical
bytes into one running SHA-256 in append order is byte-identical to hashing
the whole list at once.

Memory shape: the instance retains only two hash states, the last accepted id
and a counter.  It never stores historical rows or accumulated encoded bytes;
each ``add`` serializes exactly one row, and that single-row canonical JSON
carries a single-row transient cost.  No claim is made that any surrounding
system is memory-bounded.

This helper performs no SQLite, network, or file access, invokes no product
business logic, and is not yet wired into any solver route.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

# Byte-for-byte the options of the v1 whole-list encoders named above.
_CANONICAL_JSON_OPTIONS: dict[str, Any] = {
    "ensure_ascii": False,
    "sort_keys": True,
    "separators": (",", ":"),
    "allow_nan": False,
}


class CanonicalJobEventDigests:
    """Streaming (ids_sha256, events_sha256) pair for a strictly increasing
    sequence of job-event rows.

    ``add(row)`` accepts a dict (or dict subclass) event row — a Mapping that
    the original whole-list ``json.dumps`` could not encode is rejected
    identically — and takes its identity from ``row["id"]``, which must be a
    positive true ``int`` (bool excluded) strictly greater than the previously
    accepted id.  All identity validation and the complete single-row
    canonical serialization happen before any state update, so a serialization
    or identity error leaves the digests, id order and count unchanged.

    ``snapshot()`` returns ``(id_sha256_hex, events_sha256_hex)`` with no side
    effect; it may be called repeatedly and at any point, and more rows may
    still be appended afterwards.

    ``count`` is a read-only property (no setter; assigning to it raises
    ``AttributeError``).
    """

    __slots__ = ("_events_hash", "_ids_hash", "_last_id", "_count")

    def __init__(self) -> None:
        self._events_hash = hashlib.sha256()
        self._events_hash.update(b"[")
        self._ids_hash = hashlib.sha256()
        self._last_id: int | None = None
        self._count = 0

    @property
    def count(self) -> int:
        """Number of events accepted so far (read-only)."""
        return self._count

    def add(self, row: Mapping[str, Any]) -> None:
        if not isinstance(row, Mapping):
            raise TypeError("job event row must be a mapping")
        if "id" not in row:
            raise ValueError("job event row is missing its 'id' field")
        event_id = row["id"]
        if type(event_id) is not int:
            raise ValueError(
                f"job event id must be a true int, got {type(event_id).__name__}"
            )
        if event_id <= 0:
            raise ValueError("job event id must be a positive integer")
        if self._last_id is not None and event_id <= self._last_id:
            raise ValueError("job event ids must be strictly increasing")
        # Complete the entire canonical serialization first; any TypeError,
        # ValueError or UnicodeError raised here leaves every digest, the id
        # order and the count untouched.
        canonical = json.dumps(row, **_CANONICAL_JSON_OPTIONS).encode("utf-8")
        id_bytes = str(event_id).encode("ascii")
        if self._count:
            self._events_hash.update(b",")
            self._ids_hash.update(b",")
        self._events_hash.update(canonical)
        self._ids_hash.update(id_bytes)
        self._last_id = event_id
        self._count += 1

    def snapshot(self) -> tuple[str, str]:
        """Non-destructive ``(id_sha256_hex, events_sha256_hex)`` snapshot."""
        events = self._events_hash.copy()
        events.update(b"]")
        return self._ids_hash.hexdigest(), events.hexdigest()
