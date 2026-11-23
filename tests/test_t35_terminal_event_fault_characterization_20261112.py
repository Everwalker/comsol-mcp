"""T35: terminal-event write faults in ``ControlDaemon._finish``.

Scope (current candidate behaviour, characterization only):
``ControlDaemon._finish`` (_control_daemon.py:13102-13132) commits the durable
terminal write first — ``store.finish`` (_operation_store.py:1870-1934) and
``store.update_job`` (_operation_store.py:2001-2061) — and only afterwards
writes the terminal status event via ``store.add_event``
(_operation_store.py:2238-2243), then attempts
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
  ``SUCCEEDED`` terminal event call. It is not a disk-failure simulation.
  The prior characterization is preserved in the isolated baseline; this
  version asserts the new response-only warning and release-attempt behavior.
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
        self.logged_exceptions = 0

    def _log_exception(self) -> None:
        self.logged_exceptions += 1


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


def test_terminal_succeeded_event_fault_reports_warning_and_attempts_release():
    """A failed terminal event leaves the committed result and reports the fault."""
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

        returned = ControlDaemon._finish(
            cast(ControlDaemon, receiver), record, envelope, "SUCCEEDED")

        # The event fault is reported as a non-durable control warning, not
        # as an engine failure. The already serialized result has no warning.
        assert add_event_calls == ["SUCCEEDED"], add_event_calls
        assert returned is envelope
        warning = returned["execution"]["post_commit_warning"]
        assert warning["failed_steps"] == ["terminal_event_write"]
        assert warning["diagnostic_durable"] is False
        assert receiver.logged_exceptions == 1
        stored_envelope = {
            **envelope,
            "execution": {k: v for k, v in envelope["execution"].items()
                          if k != "post_commit_warning"},
        }

        # finish() and update_job() committed before the failed event write:
        operation = store.get_operation(record["operation_id"])
        assert operation is not None, record["operation_id"]
        assert operation["status"] == "SUCCEEDED", operation["status"]
        assert operation["result"] == stored_envelope, operation["result"]
        assert operation["finished_at"] is not None

        job = store.job(record["job_id"])
        assert job is not None, record["job_id"]
        assert job["status"] == "SUCCEEDED", job["status"]
        assert job["finished_at"] is not None
        assert job["result"] == stored_envelope

        # The terminal status event row is absent, and no other event was added.
        events = store.events(record["job_id"])
        assert [event["event"] for event in events] == [], events

        # The fake release was attempted once; this is not proof of an actual
        # resource release.
        assert receiver.release_spy.calls == [record["job_id"]]
    finally:
        store.add_event = original_add_event
        store.close()
