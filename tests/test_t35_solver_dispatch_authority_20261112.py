"""T35 — synthetic project-authority boundary tests for the real catalog dispatcher.

Exercises the actual ``ControlDaemon._dispatch_catalog_job_control_impl``
``solver.log_read`` project-authority branch (comsol_mcp/_control_daemon.py
lines 12028-12048) by unbound method invocation against a task-owned inert
receiver that carries only ``.store`` and ``.project_authority``.  No real
``ControlDaemon`` is constructed or started, no daemon/Worker/model/transport
is touched, and the authority is an explicitly fake recorder.

Real components used unchanged:
- ``_g2_registry.validate_call`` (catalog schema for ``solver.log_read``:
  required ``project_id`` + ``job_id``, closed object, optional ``cursor``/``limit``);
- a fresh ``OperationStore(':memory:')`` with jobs created through the real
  ``begin`` path (metadata ``execution.project_id`` drives the durable binding)
  and events appended through the real ``add_event`` path;
- the real streaming reader ``solver_log_read`` (``_g3_solver_ops.py``), whose
  snapshot, result projection and joined event fold are wrapped in pass-through
  spies that only record call ordering — return values are never replaced.

This is a control-plane software boundary test only: it is neither MCP-wire
acceptance nor real authority acceptance, and its synthetic projects/jobs never
claim any production or engine behavior.  The tests were authored but NOT run
by the author; the parent runs them under a separately adapted identity gate.
"""

from __future__ import annotations

from contextlib import contextmanager
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError, PreWriteRefusal
from comsol_mcp._operation_store import OperationStore

INNER_OPERATION = "solver.log_read"
DENIAL_CODE = "T35_FAKE_AUTHORITY_DENIED"
DENIAL_MESSAGE = "synthetic t35 fake authority denial"
_EVENT_ROW_KEYS = {"id", "job_id", "event", "metadata", "created_at"}


class _FakeProjectAuthority:
    """Fake authority that records (project_id, action) and permits or denies.

    It is deliberately not the production ``ProjectAuthority``; it carries no
    durable admission and makes no real authorization claim.
    """

    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[tuple[str, str]] = []

    def authorize_operation(self, project_id: str, action: str) -> None:
        self.calls.append((project_id, action))
        if self.deny:
            raise ExecutionContractError(DENIAL_CODE, DENIAL_MESSAGE)


class _InertDaemonReceiver:
    """Task-owned inert receiver: only the two attributes the branch touches."""

    def __init__(self, store: OperationStore, project_authority: _FakeProjectAuthority) -> None:
        self.store = store
        self.project_authority = project_authority


def _install_read_spies(store: OperationStore, log: list[tuple[str, str]]) -> None:
    """Wrap the real snapshot, result projection and fold in pass-through counters.

    Return values are produced by the real methods; the spies only append a
    call record so assertions can observe authorization/read ordering.
    Install only after fixture setup so setup calls never enter the log.
    """
    real_job = store.job
    real_snapshot = store.solver_log_snapshot
    real_projection = store.project_operation_result_error
    real_fold = store.fold_job_event_prefix

    def spy_job(job_id: str) -> Any:
        log.append(("unbounded_job_read", job_id))
        return real_job(job_id)

    @contextmanager
    def spy_snapshot(job_id: str):
        log.append(("job_read", job_id))
        with real_snapshot(job_id) as job:
            assert store.db.in_transaction
            yield job

    def spy_projection(operation_id: str, **kwargs: Any) -> Any:
        assert store.db.in_transaction
        log.append(("project_result", operation_id))
        return real_projection(operation_id, **kwargs)

    def spy_fold(job_id: str, **kwargs: Any) -> Any:
        assert store.db.in_transaction
        assert kwargs.get("_join_read_snapshot") is True
        log.append(("fold", job_id))
        return real_fold(job_id, **kwargs)

    store.job = spy_job  # type: ignore[method-assign]
    store.solver_log_snapshot = spy_snapshot  # type: ignore[method-assign]
    store.project_operation_result_error = spy_projection  # type: ignore[method-assign]
    store.fold_job_event_prefix = spy_fold  # type: ignore[method-assign]


def _create_project_job(store: OperationStore, *, project_id: str, event_names: list[str]) -> str:
    """Create one durable job through the real ``begin``/``add_event`` paths."""
    record, reused = store.begin(
        request_id=f"t35-req-{project_id}",
        idempotency_key=f"t35-idem-{project_id}",
        request_hash="0" * 64,
        operation=INNER_OPERATION,
        metadata={"execution": {"project_id": project_id}, "t35_synthetic": True},
    )
    assert reused is False
    job_id = record["job_id"]
    assert isinstance(job_id, str) and job_id
    for name in event_names:
        store.add_event(job_id, name, {"marker": name})
    return job_id


def _dispatch(receiver: _InertDaemonReceiver, *, envelope_project: str, inner: dict[str, Any]) -> dict[str, Any]:
    """Invoke the real dispatcher branch via unbound method (no daemon instance).

    The inert receiver deliberately violates the annotated ``self`` type; the
    selected branch only ever reads ``.store`` and ``.project_authority``.
    """
    return ControlDaemon._dispatch_catalog_job_control_impl(  # type: ignore[arg-type]
        receiver,
        INNER_OPERATION,
        dict(inner),
        execution={"project_id": envelope_project},
    )


def test_t35_authorized_matching_project_job_reads_real_events_and_v1_cursor_continuation() -> None:
    """Authorized envelope+inner project and matching durable job: real page,
    stable v1 cursor continuation from the same job, one fold per read, and
    fake authorization recorded before any job/fold read."""
    project_id = "t35-proj-authorized-A"
    event_names = ["T35EventAlpha", "T35EventBravo", "T35EventCharlie"]
    store = OperationStore(":memory:")
    try:
        job_id = _create_project_job(store, project_id=project_id, event_names=event_names)
        operation_id = store.db.execute(
            "SELECT operation_id FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        log: list[tuple[str, str]] = []

        class _OrderedAuthority(_FakeProjectAuthority):
            def authorize_operation(self, project_id: str, action: str) -> None:
                super().authorize_operation(project_id, action)
                log.append(("authorize", project_id))

        authority = _OrderedAuthority()
        _install_read_spies(store, log)
        receiver = _InertDaemonReceiver(store, authority)

        response = _dispatch(
            receiver,
            envelope_project=project_id,
            inner={"project_id": project_id, "job_id": job_id, "limit": 2},
        )
        assert response["success"] is True
        data = response["data"]
        assert data["job_id"] == job_id
        assert data["project_id"] == project_id
        assert data["job_status"] == "QUEUED"
        assert [event["event"] for event in data["events"]] == event_names[:2]
        assert data["event_count"] == 2
        assert all(_EVENT_ROW_KEYS == set(event) for event in data["events"])
        assert all(event["job_id"] == job_id for event in data["events"])
        assert all(event["metadata"] == {"marker": event["event"]} for event in data["events"])
        assert data["snapshot"]["status"] == "COMPLETE"
        assert data["snapshot"]["event_count"] == 3
        first_digests = (data["snapshot"]["id_sha256"], data["snapshot"]["events_sha256"])
        assert all(isinstance(digest, str) and digest for digest in first_digests)
        cursor = data["next_cursor"]
        assert isinstance(cursor, str) and cursor

        # Ordering: authorization was logged before the first job/fold read.
        kinds = [entry[0] for entry in log]
        assert kinds == ["authorize", "job_read", "project_result", "fold"]
        assert log[2] == ("project_result", operation_id)
        assert authority.calls == [(project_id, "inspect")]
        assert kinds.count("job_read") == 1 and kinds.count("fold") == 1

        continuation = _dispatch(
            receiver,
            envelope_project=project_id,
            inner={"project_id": project_id, "job_id": job_id, "cursor": cursor, "limit": 2},
        )
        assert continuation["success"] is True
        cont = continuation["data"]
        assert cont["job_id"] == job_id
        assert [event["event"] for event in cont["events"]] == event_names[2:]
        assert cont["next_cursor"] is None
        # The frozen v1 prefix re-folds to identical digests across reads.
        assert (cont["snapshot"]["id_sha256"], cont["snapshot"]["events_sha256"]) == first_digests
        assert cont["snapshot"]["event_count"] == 3
        # Exactly one snapshot, result projection and fold per read.
        assert [entry[0] for entry in log] == [
            "authorize", "job_read", "project_result", "fold",
            "authorize", "job_read", "project_result", "fold",
        ]
    finally:
        store.close()


def test_t35_denied_authority_propagates_distinctive_error_without_any_read() -> None:
    """Denied fake authority with an otherwise valid payload: the distinctive
    denial propagates and zero job/fold reads happened.  The identical payload
    through a permitted fake authority is a separate positive control, so a
    payload/schema failure can never masquerade as an authorization PASS."""
    project_id = "t35-proj-denied-B"
    store = OperationStore(":memory:")
    try:
        job_id = _create_project_job(
            store, project_id=project_id, event_names=["T35DeniedProbe"]
        )
        operation_id = store.db.execute(
            "SELECT operation_id FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        log: list[tuple[str, str]] = []
        _install_read_spies(store, log)

        denied = _FakeProjectAuthority(deny=True)
        receiver = _InertDaemonReceiver(store, denied)
        inner = {"project_id": project_id, "job_id": job_id}
        with pytest.raises(ExecutionContractError) as raised:
            _dispatch(receiver, envelope_project=project_id, inner=inner)
        assert raised.value.code == DENIAL_CODE
        assert str(raised.value) == DENIAL_MESSAGE
        # The denial was an authorization decision, not an input rejection:
        # the fake authority was consulted exactly once before the raise.
        assert denied.calls == [(project_id, "inspect")]
        assert log == []

        # Separate authorized positive control with the identical payload.
        permitted = _FakeProjectAuthority()
        permitted_receiver = _InertDaemonReceiver(store, permitted)
        response = _dispatch(permitted_receiver, envelope_project=project_id, inner=inner)
        assert response["success"] is True
        assert response["data"]["job_id"] == job_id
        assert response["data"]["event_count"] == 1
        assert log == [("job_read", job_id), ("project_result", operation_id), ("fold", job_id)]
    finally:
        store.close()


def test_t35_inner_outer_project_mismatch_raises_dispatcher_mismatch_after_authorization_without_reads() -> None:
    """Mismatched inner vs outer project: the fake authority is called first
    with the envelope project, then the real dispatcher raises
    ``PROJECT_IDENTITY_MISMATCH`` before any job/fold read.  A second dispatch
    with valid exact binding proves the target route itself works."""
    outer_project = "t35-proj-outer-C"
    inner_project = "t35-proj-inner-C"
    store = OperationStore(":memory:")
    try:
        job_id = _create_project_job(
            store, project_id=outer_project, event_names=["T35MismatchProbe"]
        )
        log: list[tuple[str, str]] = []
        _install_read_spies(store, log)

        class _OrderedAuthority(_FakeProjectAuthority):
            def authorize_operation(self, project_id: str, action: str) -> None:
                super().authorize_operation(project_id, action)
                log.append(("authorize", project_id))

        authority = _OrderedAuthority()
        receiver = _InertDaemonReceiver(store, authority)
        with pytest.raises(ExecutionContractError) as raised:
            _dispatch(
                receiver,
                envelope_project=outer_project,
                inner={"project_id": inner_project, "job_id": job_id},
            )
        assert raised.value.code == "PROJECT_IDENTITY_MISMATCH"
        # Authorization ran first (with the envelope project) and no read followed.
        assert authority.calls == [(outer_project, "inspect")]
        assert log == [("authorize", outer_project)]

        # Independent valid exact binding: the same route completes successfully.
        valid = _FakeProjectAuthority()
        response = _dispatch(
            _InertDaemonReceiver(store, valid),
            envelope_project=outer_project,
            inner={"project_id": outer_project, "job_id": job_id},
        )
        assert response["success"] is True
        assert response["data"]["job_id"] == job_id
        assert valid.calls == [(outer_project, "inspect")]
    finally:
        store.close()


def test_t35_cross_project_job_fails_reader_binding_after_job_read_without_fold() -> None:
    """Envelope and inner project are mutually consistent and permitted, but
    the durable job is bound to a different project: the real reader performs
    the job read, detects the binding mismatch (PreWriteRefusal from
    ``_job_project_binding``, not the dispatcher equality check), and the fold
    never runs.  A matching-job control in the same store completes normally."""
    supplied_project = "t35-proj-supplied-D"
    foreign_project = "t35-proj-foreign-D"
    store = OperationStore(":memory:")
    try:
        foreign_job = _create_project_job(
            store, project_id=foreign_project, event_names=["T35ForeignProbe"]
        )
        matching_job = _create_project_job(
            store, project_id=supplied_project, event_names=["T35MatchingProbe"]
        )
        matching_operation = store.db.execute(
            "SELECT operation_id FROM jobs WHERE job_id=?", (matching_job,)
        ).fetchone()[0]
        log: list[tuple[str, str]] = []
        _install_read_spies(store, log)

        authority = _FakeProjectAuthority()
        receiver = _InertDaemonReceiver(store, authority)
        with pytest.raises(ExecutionContractError) as raised:
            _dispatch(
                receiver,
                envelope_project=supplied_project,
                inner={"project_id": supplied_project, "job_id": foreign_job},
            )
        assert raised.value.code == "PROJECT_IDENTITY_MISMATCH"
        # Reader-level refusal (PreWriteRefusal), distinct from the dispatcher's
        # bare envelope/inner equality check in the mismatch test above.
        assert type(raised.value) is PreWriteRefusal
        # The real reader read the job but never reached the fold.
        assert log == [("job_read", foreign_job)]
        assert authority.calls == [(supplied_project, "inspect")]

        # Matching-job control: same store, same supplied project, route works.
        control = _FakeProjectAuthority()
        response = _dispatch(
            _InertDaemonReceiver(store, control),
            envelope_project=supplied_project,
            inner={"project_id": supplied_project, "job_id": matching_job},
        )
        assert response["success"] is True
        assert response["data"]["job_id"] == matching_job
        assert response["data"]["events"][0]["event"] == "T35MatchingProbe"
        assert log[-3:] == [
            ("job_read", matching_job), ("project_result", matching_operation), ("fold", matching_job),
        ]
    finally:
        store.close()
