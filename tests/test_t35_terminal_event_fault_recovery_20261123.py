"""Narrow regression tests for terminal-event failures after durable finish.

The real ControlDaemon._finish and memory-only SQLite OperationStore are
exercised with a fake receiver.  No scheduler, Worker, engine, or real resource
coordinator is constructed or called.
"""
from __future__ import annotations

from typing import Any, cast

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._mcp_gateway import mcp_result
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._session_lifecycle import SessionLifecycleStore, new_lifecycle_record


class InjectedTerminalEventFailure(RuntimeError):
    """Synthetic exception replacing exactly one terminal event insert."""


class InjectedTerminalLogFailure(RuntimeError):
    """Synthetic exception replacing the post-commit error log write."""


class InjectedFinishedMetadataFailure(RuntimeError):
    """Synthetic exception replacing the post-commit job metadata update."""


class _TerminalResourceReleaseSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.observation: dict[str, Any] = {
            "state": "COMPLETE",
            "disk": {"state": "COMPLETE", "reservation_state": "NOT_CONFIGURED",
                     "diagnostic_durable": True},
            "memory": {"state": "COMPLETE", "reservation_state": "NOT_CONFIGURED",
                       "diagnostic_durable": True},
        }

    def release(self, job_id: str) -> dict[str, Any]:
        self.calls.append(job_id)
        return self.observation


class _FinishReceiver:
    def __init__(self) -> None:
        self.store = OperationStore(":memory:")
        self.session_lifecycle = SessionLifecycleStore(self.store)
        self.release_spy = _TerminalResourceReleaseSpy()
        self._release_terminal_resource_reservations = self.release_spy.release
        self.logged_exceptions = 0
        self.fail_logging = False
        self.log_failure_raised = False

    def _log_exception(self) -> None:
        self.logged_exceptions += 1
        if self.fail_logging:
            self.log_failure_raised = True
            raise InjectedTerminalLogFailure("synthetic control error log write fault")


def _admitted(receiver: _FinishReceiver, request_id: str, key: str) -> dict[str, Any]:
    record, reused = receiver.store.begin(
        request_id=request_id,
        idempotency_key=key,
        request_hash="t35-terminal-event-fault-hash",
        operation="study.run",
        metadata={"synthetic_marker": "t35-terminal-event-fault"},
    )
    assert not reused
    assert record["job_id"]
    return record


def _result() -> dict[str, Any]:
    return {"success": True, "data": {"status": "SUCCEEDED", "study": "synthetic-t35"}}


def _assert_committed_success(receiver: _FinishReceiver, record: dict[str, Any], result: dict[str, Any]) -> None:
    operation = receiver.store.get_operation(record["operation_id"])
    job = receiver.store.job(record["job_id"])
    assert operation is not None and job is not None
    assert operation["status"] == job["status"] == "SUCCEEDED"
    returned_core = {**result, "execution": dict(result["execution"])}
    returned_core["execution"].pop("post_commit_warning", None)
    assert operation["result"] == job["result"] == returned_core
    assert operation["finished_at"] is not None
    assert job["finished_at"] is not None


def _assert_post_commit_warning(result: dict[str, Any], failed_steps: list[str],
                                release_observation: dict[str, Any]) -> dict[str, Any]:
    warning = result["execution"]["post_commit_warning"]
    assert warning == {
        "code": "POST_COMMIT_WRITE_FAILURE",
        "failed_steps": failed_steps,
        "diagnostic_durable": False,
        "durability_scope": "operation_result",
        "resource_release_observation": release_observation,
    }
    return warning


def _fault_terminal_event(receiver: _FinishReceiver) -> list[str]:
    store = receiver.store
    original_add_event = store.add_event
    calls: list[str] = []

    def fail_succeeded_event(job_id: str, event: str,
                             metadata: dict[str, Any] | None = None) -> None:
        calls.append(event)
        if event == "SUCCEEDED":
            raise InjectedTerminalEventFailure("synthetic terminal event write fault")
        original_add_event(job_id, event, metadata)

    store.add_event = fail_succeeded_event
    return calls


def test_event_fault_returns_committed_result_logs_fault_attempts_release_and_retries_idempotently():
    receiver = _FinishReceiver()
    original_add_event = receiver.store.add_event
    try:
        record = _admitted(receiver, "t35-fault-req", "t35-fault-key")
        result = _result()
        event_calls = _fault_terminal_event(receiver)
        receiver.release_spy.observation = {
            "state": "UNKNOWN",
            "disk": {"state": "UNKNOWN", "reservation_state": None,
                     "diagnostic_durable": False},
            "memory": {"state": "UNKNOWN", "reservation_state": None,
                       "diagnostic_durable": False},
        }
        release_observation: dict[str, Any] = {}

        returned = ControlDaemon._finish(
            cast(ControlDaemon, receiver), record, result, "SUCCEEDED",
            _native_resource_release_observation=release_observation,
        )

        assert returned is result
        assert event_calls == ["SUCCEEDED"]
        assert receiver.logged_exceptions == 1
        _assert_committed_success(receiver, record, returned)
        assert receiver.store.events(record["job_id"]) == []
        assert receiver.release_spy.calls == [record["job_id"]]
        assert release_observation == receiver.release_spy.observation
        assert release_observation["state"] == "UNKNOWN"
        _assert_post_commit_warning(returned, ["terminal_event_write"], release_observation)

        operation = receiver.store.get_operation(record["operation_id"])
        assert operation is not None
        persisted_result = operation["result"]
        assert "post_commit_warning" not in persisted_result["execution"]
        assert persisted_result != returned

        retry, reused = receiver.store.begin(
            request_id=record["request_id"],
            idempotency_key=record["idempotency_key"],
            request_hash=record["request_hash"],
            operation="study.run",
            metadata={"synthetic_marker": "t35-terminal-event-fault"},
        )
        assert reused is True
        assert retry["result"] == persisted_result
        assert retry["result"] != returned
        assert retry["job_id"] == record["job_id"]
        assert receiver.release_spy.calls == [record["job_id"]]
        assert receiver.store.events(record["job_id"]) == []
    finally:
        receiver.store.add_event = original_add_event
        receiver.store.close()


def test_event_fault_preserves_atomic_lifecycle_transition():
    receiver = _FinishReceiver()
    original_add_event = receiver.store.add_event
    try:
        record = _admitted(receiver, "t35-lifecycle-req", "t35-lifecycle-key")
        prior = receiver.session_lifecycle.save(new_lifecycle_record(
            project_id="t35-project",
            session_id="t35-session",
            state="CONNECTED",
            client_state="CONNECTED",
            server_state="SHARED",
            server_ownership="shared",
        ))
        proposed = {**prior, "state": "DISCONNECTED", "client_state": "DISCONNECTED"}
        _fault_terminal_event(receiver)
        result = _result()

        returned = ControlDaemon._finish(
            cast(ControlDaemon, receiver), record, result, "SUCCEEDED",
            _native_lifecycle_transition=(proposed, prior["revision"]),
        )

        assert returned is result
        _assert_committed_success(receiver, record, returned)
        lifecycle = receiver.session_lifecycle.get("t35-project", "t35-session")
        assert lifecycle is not None
        assert lifecycle["state"] == "DISCONNECTED"
        assert lifecycle["client_state"] == "DISCONNECTED"
        assert lifecycle["revision"] == prior["revision"] + 1
        assert receiver.store.events(record["job_id"]) == []
        assert receiver.release_spy.calls == [record["job_id"]]
        assert receiver.logged_exceptions == 1
        _assert_post_commit_warning(
            returned, ["terminal_event_write"], receiver.release_spy.observation)
    finally:
        receiver.store.add_event = original_add_event
        receiver.store.close()


def test_event_and_logger_fault_return_committed_result_without_success_claims():
    receiver = _FinishReceiver()
    original_add_event = receiver.store.add_event
    try:
        record = _admitted(receiver, "t35-double-fault-req", "t35-double-fault-key")
        result = _result()
        event_calls = _fault_terminal_event(receiver)
        receiver.fail_logging = True
        receiver.release_spy.observation = {
            "state": "UNKNOWN",
            "disk": {"state": "UNKNOWN", "reservation_state": None,
                     "diagnostic_durable": False},
            "memory": {"state": "UNKNOWN", "reservation_state": None,
                       "diagnostic_durable": False},
        }
        release_observation: dict[str, Any] = {}
        returned = None
        raised: BaseException | None = None

        try:
            returned = ControlDaemon._finish(
                cast(ControlDaemon, receiver), record, result, "SUCCEEDED",
                _native_resource_release_observation=release_observation,
            )
        except InjectedTerminalLogFailure as exc:
            raised = exc

        assert event_calls == ["SUCCEEDED"]
        assert receiver.logged_exceptions == 1
        assert receiver.log_failure_raised is True
        _assert_committed_success(receiver, record, result)
        assert receiver.store.events(record["job_id"]) == []
        assert receiver.release_spy.calls == [record["job_id"]]
        assert release_observation == receiver.release_spy.observation
        assert release_observation["state"] == "UNKNOWN"
        persisted = receiver.store.get_operation(record["operation_id"])
        assert persisted is not None
        assert "post_commit_warning" not in persisted["result"]["execution"]
        warning = _assert_post_commit_warning(
            result, ["terminal_event_write", "control_errors_log_write"], release_observation)
        public_response = mcp_result(returned)
        assert public_response.isError is False
        assert public_response.structuredContent["execution"]["post_commit_warning"] == warning
        assert raised is None, raised
        assert returned is result
    finally:
        receiver.store.add_event = original_add_event
        receiver.store.close()


def test_post_commit_update_job_fault_does_not_skip_event_or_release():
    receiver = _FinishReceiver()
    store = receiver.store
    original_update_job = store.update_job
    try:
        record = _admitted(receiver, "t35-update-job-fault-req", "t35-update-job-fault-key")
        result = _result()
        receiver.release_spy.observation = {
            "state": "UNKNOWN",
            "disk": {"state": "UNKNOWN", "reservation_state": None,
                     "diagnostic_durable": False},
            "memory": {"state": "UNKNOWN", "reservation_state": None,
                       "diagnostic_durable": False},
        }
        release_observation: dict[str, Any] = {}

        def fail_update_job(job_id: str, status: str, metadata: dict[str, Any] | None = None,
                            *, result: dict[str, Any] | None = None) -> None:
            raise InjectedFinishedMetadataFailure("synthetic finished metadata write fault")

        store.update_job = fail_update_job
        returned = ControlDaemon._finish(
            cast(ControlDaemon, receiver), record, result, "SUCCEEDED",
            _native_resource_release_observation=release_observation,
        )

        assert returned is result
        _assert_committed_success(receiver, record, result)
        assert [event["event"] for event in store.events(record["job_id"])] == ["SUCCEEDED"]
        job = store.job(record["job_id"])
        assert job is not None and "finished_observed_at" not in job["metadata"]
        assert receiver.logged_exceptions == 1
        assert receiver.release_spy.calls == [record["job_id"]]
        assert release_observation == receiver.release_spy.observation
        assert release_observation["state"] == "UNKNOWN"
        _assert_post_commit_warning(
            returned, ["job_metadata_write"], release_observation)
    finally:
        store.update_job = original_update_job
        store.close()

