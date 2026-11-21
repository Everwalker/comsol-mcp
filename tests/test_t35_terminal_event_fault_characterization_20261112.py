"""T35: characterization of terminal-event write faults in ``ControlDaemon._finish``.

Scope (current candidate behaviour, characterization only):
``ControlDaemon._finish`` (_control_daemon.py:13102-13132) commits the durable
terminal write first — ``store.finish`` (_operation_store.py:1870-1934) and
``store.update_job`` (_operation_store.py:2001-2061) — and only afterwards
writes the terminal status event via ``store.add_event``
(_operation_store.py:2238-2243), then calls
``self._release_terminal_resource_reservations`` for a terminal status.

What is REAL (product code under test):
- ``ControlDaemon._finish`` is invoked as the real unbound function against an
  inert receiver; no ``ControlDaemon`` instance is ever constructed, and no
  scheduler, service, engine or Worker is invoked.
- The real ``OperationStore`` (memory-only SQLite, path ``":memory:"``)
  performs admission (``begin``), ``finish``, ``update_job``, ``add_event``
  and the readback used by the assertions (``get_operation``, ``job``,
  ``events``).

What is FAKE (bounded scaffolding, no attestation value):
- ``_FinishReceiver`` supplies only the two attributes ``_finish`` actually
  touches: ``store`` and ``_release_terminal_resource_reservations``.  The
  release surface is a spy that records calls and returns a fixed synthetic
  observation; it proves only that the candidate code path reached (or
  skipped) the release call, never real resource release.
- ``InjectedTerminalEventFailure`` is a synthetic exception raised by a test
  wrapper installed around the fresh store's ``add_event`` for the exact
  ``SUCCEEDED`` terminal event call.  It is NOT a disk-failure simulation,
  NOT an observed production fault, and these assertions characterize the
  current candidate (event write sequenced after the committed durable
  terminal write, exception propagated unwrapped, release skipped) — they do
  not claim this is the final ideal terminal contract.

These tests were written, not executed, per task rules.
"""
from __future__ import annotations

from typing import Any, cast

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._operation_store import OperationStore


class InjectedTerminalEventFailure(RuntimeError):
    """Synthetic fault raised in place of one terminal event insertion."""


class _TerminalResourceReleaseSpy:
    """Records release invocations; returns a fixed synthetic observation."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _release_terminal_resource_reservations(self, job_id: str) -> dict[str, Any]:
        self.calls.append(job_id)
        return {
            "state": "COMPLETE",
            "disk": {"state": "COMPLETE", "reservation_state": "NOT_CONFIGURED",
                     "diagnostic_durable": True},
            "memory": {"state": "COMPLETE", "reservation_state": "NOT_CONFIGURED",
                       "diagnostic_durable": True},
        }


class _FinishReceiver:
    """Inert receiver exposing only what ``_finish`` reads from ``self``."""

    def __init__(self) -> None:
        # Real ledger, memory-only: no SQLite file is opened on disk.
        self.store = OperationStore(":memory:")
        self.release_spy = _TerminalResourceReleaseSpy()
        self._release_terminal_resource_reservations = (
            self.release_spy._release_terminal_resource_reservations
        )


def _admitted(receiver: _FinishReceiver, *, request_id: str, idempotency_key: str) -> dict[str, Any]:
    """Open one fresh QUEUED operation/job pair through the real store.begin."""
    record, reused = receiver.store.begin(
        request_id=request_id,
        idempotency_key=idempotency_key,
        request_hash="t35-synthetic-request-hash",
        operation="study.run",
        metadata={"synthetic_marker": "t35-terminal-event-fault"},
    )
    assert reused is False, record
    assert record["job_id"], record
    return record


def _success_envelope() -> dict[str, Any]:
    return {"success": True, "data": {"status": "SUCCEEDED", "study": "synthetic-t35"}}


def test_finish_success_writes_rows_terminal_event_and_exactly_one_release():
    """Normal control: committed rows, one real SUCCEEDED event, one release."""
    receiver = _FinishReceiver()
    try:
        record = _admitted(receiver, request_id="t35-ok-req", idempotency_key="t35-ok-key")
        envelope = _success_envelope()

        returned = ControlDaemon._finish(
            cast(ControlDaemon, receiver), record, envelope, "SUCCEEDED")

        assert returned is envelope, returned
        assert returned["execution"] == {
            "request_id": record["request_id"],
            "operation_id": record["operation_id"],
            "request_hash": record["request_hash"],
            "idempotency_key": record["idempotency_key"],
            "job_id": record["job_id"],
        }, returned["execution"]

        operation = receiver.store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "SUCCEEDED", operation["status"]
        assert operation["result"] == returned, operation["result"]
        assert operation["finished_at"] is not None

        job = receiver.store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "SUCCEEDED", job["status"]
        assert job["finished_at"] is not None
        assert job["result"] == returned

        events = receiver.store.events(record["job_id"])
        assert [event["event"] for event in events] == ["SUCCEEDED"], events
        assert events[0]["metadata"]["operation_id"] == record["operation_id"], events[0]

        assert receiver.release_spy.calls == [record["job_id"]], receiver.release_spy.calls
    finally:
        receiver.store.close()


def test_terminal_succeeded_event_add_event_fault_keeps_committed_rows_and_skips_release():
    """Fault control: only the fresh store's add_event is replaced; it raises
    exactly on the SUCCEEDED terminal-event call.  Characterizes the current
    candidate: the durable finish/update_job commits survive, the terminal
    event row is absent, and the release spy has observed nothing."""
    receiver = _FinishReceiver()
    store = receiver.store
    original_add_event = store.add_event
    add_event_calls: list[str] = []
    try:
        record = _admitted(receiver, request_id="t35-fault-req", idempotency_key="t35-fault-key")
        envelope = _success_envelope()

        def fault_on_terminal_event(job_id: str, event: str,
                                    metadata: dict[str, Any] | None = None) -> None:
            add_event_calls.append(event)
            if event == "SUCCEEDED":
                raise InjectedTerminalEventFailure("synthetic terminal event write fault")
            return original_add_event(job_id, event, metadata)

        store.add_event = fault_on_terminal_event

        raised: BaseException | None = None
        try:
            ControlDaemon._finish(
                cast(ControlDaemon, receiver), record, envelope, "SUCCEEDED")
        except InjectedTerminalEventFailure as exc:
            raised = exc

        # The injected fault was hit exactly once, on the SUCCEEDED terminal
        # event call, and _finish propagated that exact exception unwrapped.
        # (Any other raised exception escapes this test uncaught by design.)
        assert add_event_calls == ["SUCCEEDED"], add_event_calls
        assert type(raised) is InjectedTerminalEventFailure, raised

        # finish() and update_job() committed before the failed event write:
        operation = store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "SUCCEEDED", operation["status"]
        assert operation["result"] == envelope, operation["result"]
        assert operation["finished_at"] is not None

        job = store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "SUCCEEDED", job["status"]
        assert job["finished_at"] is not None
        assert job["result"] == envelope

        # The terminal status event row is absent, and no other event was added.
        events = store.events(record["job_id"])
        assert [event["event"] for event in events] == [], events

        # The fault preceded the terminal release call: the spy saw nothing.
        assert receiver.release_spy.calls == [], receiver.release_spy.calls
    finally:
        store.add_event = original_add_event
        store.close()
