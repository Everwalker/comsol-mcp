"""Offline contract tests for the public C15 control helper.

These tests do not claim COMSOL execution.  The fake client only checks request order,
UNKNOWN-job discipline, and the exact same-key/body retry contract used by the live
ActionClient.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from tools.g33_control_cases import run_control_cases


class _FakeControlClient:
    def __init__(self, *, quiescent: bool = True, solve_success: bool = False,
                 solve_has_job: bool = True, quiescent_after: int | None = None,
                 solve_running: bool = False, omit_reconcile_metadata: bool = False,
                 retry_job_id: str | None = None, retry_operation: str = "study.run",
                 append_log_on_retry: bool = False) -> None:
        self.state = {
            "ref": {
                "session_id": "fake-session",
                "server_instance_id": "fake-server",
                "model_tag": "ChainB",
                "generation": 7,
            }
        }
        self.quiescent = quiescent
        self.solve_success = solve_success
        self.solve_has_job = solve_has_job
        self.quiescent_after = quiescent_after
        self.solve_running = solve_running
        self.omit_reconcile_metadata = omit_reconcile_metadata
        self.retry_job_id = retry_job_id or "job-chain-b-c15"
        self.retry_operation = retry_operation
        self.append_log_on_retry = append_log_on_retry
        self._log_mutated = False
        self.reconcile_reads = 0
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.retry_calls: list[str] = []
        self.job_id = "job-chain-b-c15"

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        self.calls.append((operation, dict(body), dict(kwargs)))
        if operation == "study.run":
            if self.solve_running:
                return {"success": True, "data": {"status": "RUNNING", "rpc_wait_expired": True,
                                                   "job_id": self.job_id}}
            if self.solve_success:
                return {"success": True, "data": {"status": "SUCCEEDED"}}
            execution = {"job_id": self.job_id} if self.solve_has_job else {}
            return {
                "success": False,
                "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "solve remains in flight"},
                "data": {"execution_state_unknown": True},
                "execution": execution,
            }
        if operation == "job_status":
            return {"success": True, "data": {"job_id": self.job_id, "status": "RUNNING"}}
        if operation == "job_log":
            events = [{"level": "INFO", "message": "solver active"}]
            if self._log_mutated:
                events.append({"level": "INFO", "message": "unexpected retry event"})
            return {"success": True, "data": {"job_id": self.job_id, "status": "RUNNING",
                                                "events": events, "truncated": False}}
        if operation == "job_result":
            return {"success": False, "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                                   "message": "result is not terminal"},
                    "data": {"job_id": self.job_id, "status": "UNKNOWN",
                             "execution_state_unknown": True}}
        if operation == "job_reconcile":
            self.reconcile_reads += 1
            observed_quiescent = self.quiescent and (
                self.quiescent_after is None or self.reconcile_reads > self.quiescent_after
            )
            data = {"job_id": self.job_id,
                    "status": "SUCCEEDED" if observed_quiescent else "RUNNING"}
            if not self.omit_reconcile_metadata:
                data["metadata"] = {"reconciled_quiescent": observed_quiescent,
                                    "replay_performed": False}
            return {"success": True, "data": data}
        raise AssertionError(operation)

    async def retry(self, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self.retry_calls.append(key)
        self._log_mutated = self.append_log_on_retry
        return (
            {"success": True, "data": {"job_id": self.retry_job_id, "status": "SUCCEEDED"}},
            {
                "key": key,
                "request_id": "request-original",
                "operation": self.retry_operation,
                "job_id": self.retry_job_id,
                "body_sha256": "body-digest",
                "retry_body_sha256": "body-digest",
                "same_body": True,
                "success": True,
                "error_code": None,
            },
        )


class _TerminalStatusAfterPollClient(_FakeControlClient):
    def __init__(self) -> None:
        super().__init__(quiescent=False)
        self.status_reads = 0

    async def action(self, operation: str, body: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        if operation == "job_status":
            self.calls.append((operation, dict(body), dict(kwargs)))
            self.status_reads += 1
            status = "SUCCEEDED" if self.status_reads >= 2 else "RUNNING"
            return {"success": True, "data": {"job_id": self.job_id, "status": status}}
        return await super().action(operation, body, **kwargs)


def test_unknown_job_is_queried_before_exact_same_key_retry(tmp_path: Path) -> None:
    client = _FakeControlClient()
    result = asyncio.run(run_control_cases(client, tmp_path / "c15"))

    assert result["overall"] == "PASS", result
    rows = {row["case"]: row for row in result["rows"]}
    assert rows["solve-unknown"]["status"] == "PASS"
    assert rows["unknown-query-before-retry"]["status"] == "PASS"
    assert rows["active-control-response"]["status"] == "PASS"
    assert rows["same-key-retry"]["status"] == "PASS"
    assert client.retry_calls == ["g33-control-001-solve"]
    # Order contract, not an exact call multiset: the first arc observes the unknown job and
    # reconciles it, and the retry is bracketed by log snapshots taken after that gate.
    calls = [call[0] for call in client.calls]
    assert calls[:5] == ["study.run", "job_status", "job_log", "job_result", "job_reconcile"], calls
    tail = calls[5:]
    assert set(tail) <= {"job_log", "job_status", "job_reconcile"}, tail
    assert tail.count("job_log") >= 2 and tail[-1] == "job_log", tail
    assert all(call[2].get("reconcile") is False for call in client.calls)
    assert all(call[2].get("key") for call in client.calls)
    assert len({call[2]["key"] for call in client.calls}) == len(client.calls)
    retry_assertions = rows["same-key-retry"]["assertions"]
    assert retry_assertions["same_job"] is True
    assert retry_assertions["same_operation"] is True
    assert retry_assertions["job_log_complete"] is True
    assert retry_assertions["job_log_events_unchanged"] is True
    assert (tmp_path / "c15" / "g33_control_cases.json").is_file()
    assert (tmp_path / "c15" / "g33_control_cases.final.json").is_file()


def test_active_unknown_is_not_retried_until_reconcile_proves_quiescent(tmp_path: Path) -> None:
    client = _FakeControlClient(quiescent=False)
    result = asyncio.run(run_control_cases(client, tmp_path / "active",
                                           poll_timeout_s=0.0, poll_interval_s=0.0))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "BLOCKED", result
    assert rows["active-control-response"]["status"] == "PASS"
    assert rows["same-key-retry"]["status"] == "BLOCKED"
    assert result["needs_retained_runtime"] is True
    assert client.retry_calls == []
    assert [call[0] for call in client.calls] == [
        "study.run", "job_status", "job_log", "job_result", "job_reconcile",
    ]


def test_active_unknown_is_polled_with_fresh_observation_keys_until_quiescent(tmp_path: Path) -> None:
    client = _FakeControlClient(quiescent=True, quiescent_after=1)
    result = asyncio.run(run_control_cases(client, tmp_path / "poll",
                                           poll_timeout_s=1.0, poll_interval_s=0.0))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "PASS", result
    assert rows["active-control-response"]["status"] == "PASS"
    assert rows["same-key-retry"]["status"] == "PASS"
    assert result["needs_retained_runtime"] is False
    # Same order contract: the poll continues past the first reconcile and the closing
    # snapshot is a log read taken once quiescence is proven.
    calls = [call[0] for call in client.calls]
    assert calls[:5] == ["study.run", "job_status", "job_log", "job_result", "job_reconcile"], calls
    tail = calls[5:]
    assert set(tail) <= {"job_log", "job_status", "job_reconcile"}, tail
    assert tail.count("job_status") >= 1 and tail[-1] == "job_log", tail
    keys = [call[2]["key"] for call in client.calls]
    assert len(keys) == len(set(keys))
    assert rows["same-key-retry"]["assertions"]["job_log_events_unchanged"] is True


def test_success_running_rpc_wait_expired_is_observed_until_quiescent(tmp_path: Path) -> None:
    """A success envelope with an active job is not accepted as a completed solve."""
    client = _FakeControlClient(solve_running=True, quiescent=False)
    result = asyncio.run(run_control_cases(client, tmp_path / "rpc-wait",
                                           poll_timeout_s=0.0, poll_interval_s=0.0))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "BLOCKED", result
    assert rows["solve-unknown"]["status"] == "PASS"
    assert rows["solve-unknown"]["assertions"]["active_success_response"] is True
    assert "solve-active-window" not in rows
    assert rows["same-key-retry"]["status"] == "BLOCKED"
    assert result["needs_retained_runtime"] is True
    assert [call[0] for call in client.calls] == [
        "study.run", "job_status", "job_log", "job_result", "job_reconcile",
    ]


def test_terminal_reconcile_status_releases_retry_without_metadata(tmp_path: Path) -> None:
    client = _FakeControlClient(omit_reconcile_metadata=True)
    result = asyncio.run(run_control_cases(client, tmp_path / "terminal-no-metadata"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "PASS", result
    assert rows["same-key-retry"]["status"] == "PASS"
    assert rows["same-key-retry"]["assertions"]["quiescent"] is True
    assert result["needs_retained_runtime"] is False
    assert rows["same-key-retry"]["assertions"]["job_log_events_unchanged"] is True
    calls = [call[0] for call in client.calls]
    assert calls[:5] == ["study.run", "job_status", "job_log", "job_result", "job_reconcile"], calls
    tail = calls[5:]
    assert tail.count("job_log") >= 2 and tail[-1] == "job_log", tail


def test_terminal_job_status_ends_poll_and_allows_exact_retry(tmp_path: Path) -> None:
    client = _TerminalStatusAfterPollClient()
    result = asyncio.run(run_control_cases(client, tmp_path / "terminal-status",
                                           poll_timeout_s=1.0, poll_interval_s=0.0))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "PASS", result
    assert rows["same-key-retry"]["status"] == "PASS"
    assert rows["same-key-retry"]["assertions"]["quiescent"] is True
    assert result["needs_retained_runtime"] is False
    calls = [call[0] for call in client.calls]
    assert calls[:5] == ["study.run", "job_status", "job_log", "job_result", "job_reconcile"], calls
    tail = calls[5:]
    assert set(tail) <= {"job_log", "job_status", "job_reconcile"}, tail
    assert tail.count("job_status") >= 1 and tail[-1] == "job_log", tail


def test_same_key_retry_fails_when_public_job_log_changes(tmp_path: Path) -> None:
    client = _FakeControlClient(append_log_on_retry=True)
    result = asyncio.run(run_control_cases(client, tmp_path / "log-drift"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "FAIL", result
    assert rows["same-key-retry"]["status"] == "FAIL"
    assert rows["same-key-retry"]["assertions"]["job_log_events_unchanged"] is False


def test_same_key_retry_fails_when_job_or_operation_identity_changes(tmp_path: Path) -> None:
    client = _FakeControlClient(retry_job_id="job-other", retry_operation="solver.run")
    result = asyncio.run(run_control_cases(client, tmp_path / "identity-drift"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "FAIL", result
    assert rows["same-key-retry"]["status"] == "FAIL"
    assert rows["same-key-retry"]["assertions"]["same_job"] is False
    assert rows["same-key-retry"]["assertions"]["same_operation"] is False


def test_fast_solve_does_not_invent_an_active_window_or_replay(tmp_path: Path) -> None:
    client = _FakeControlClient(solve_success=True)
    result = asyncio.run(run_control_cases(client, tmp_path / "fast"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "BLOCKED", result
    assert rows["solve-active-window"]["status"] == "NOT_RUN"
    assert rows["same-key-retry"]["status"] == "NOT_RUN"
    assert client.retry_calls == []
    assert [call[0] for call in client.calls] == ["study.run"]


def test_unknown_without_job_id_is_a_failure_and_never_retried(tmp_path: Path) -> None:
    client = _FakeControlClient(solve_has_job=False)
    result = asyncio.run(run_control_cases(client, tmp_path / "malformed"))

    rows = {row["case"]: row for row in result["rows"]}
    assert result["overall"] == "FAIL", result
    assert rows["solve-unknown"]["status"] == "FAIL"
    assert rows["same-key-retry"]["status"] == "BLOCKED"
    assert client.retry_calls == []
    assert [call[0] for call in client.calls] == ["study.run"]
