"""T35 — bounded job-scope integration edge tests (2026-11-12).

Three synthetic integration edges of the proposed project-scoped cached
job-read contract, authored but NOT executed by this file's author (the
parent binds source identity and runs everything):

1. ``test_t35ji_job_wait_does_not_hold_store_lock_while_asleep_with_completion_witness``
   Canonical ``job.wait`` through the real unbound outer dispatcher must
   NOT retain the store lock / an open SQLite read transaction while it
   sleeps. A second task-owned thread's real ``store.update_job`` commit
   is witnessed as actually completing while the wait thread is still
   paused inside an instrumented sleep, after which the wait thread
   returns the real terminal job with the exact synthetic marker.

2. ``test_t35ji_scoped_legacy_alias_enforces_durable_project_binding``
   (parameterized over ``run_study_status`` and
   ``visible_main_workflow_status``) — the legacy scoped aliases route
   through the same outer ``_dispatch_admitted`` envelope gate: the
   A-bound synthetic job is served with its exact result marker under an
   A envelope, refused without any marker under an inspect-granted B
   envelope (the B authority call is still recorded), and a third call
   with no outer execution project identity fails closed.

3. ``test_t35ji_wal_second_connection_commit_keeps_reader_snapshot_consistent``
   On ONE file-backed database under pytest ``tmp_path`` (never an
   existing basetemp, never an old database): enter
   ``store.project_job_read_snapshot(job_id, A)`` (assert ``True`` and the
   actual WAL journal mode), let a second independent ``sqlite3``
   connection commit a job-metadata-only A→B rebind while the reader
   transaction is open (the linked operation stays A), assert the writer
   commit really landed, then assert the same-snapshot ``store.job`` read
   still sees the pre-rebind A metadata plus the durable result — not a
   torn B job + A operation. After the snapshot exits, fresh A and B
   snapshot checks are both ``False`` for the divergent record, while a
   separate consistent A record remains readable.

Scope and honesty limits:
- This is the in-process control-plane model only: real unbound
  ``ControlDaemon._dispatch_admitted`` /
  ``_dispatch_catalog_job_control_impl`` / ``_control_read`` against the
  parent-v2 inert receiver and FAKE per-project authority. Supplied
  execution project IDs are never authenticated principals; nothing here
  claims public MCP transport reachability or real grants.
- The fixture helpers are imported from the live parent-v2 job-read test
  module — this file deliberately does not duplicate the dispatcher.
- Test 1 models same-process threading + one shared SQLite connection
  only; it claims nothing about process crashes, OS signals, or daemon
  services. The daemon module's ``time`` attribute is temporarily
  replaced ONLY for the wait route (original ``monotonic``, instrumented
  ``sleep``) and is restored in ``finally``; the global ``time`` module is
  never patched. Test-owned threads are always joined with bounded
  timeouts before the store closes, and an unjoinable thread fails loudly
  rather than silently claiming cleanup.
- Test 3's coherent snapshot may linearize BEFORE the writer commit;
  that is coherent visibility, not proof of prompt permission revocation
  or principal authentication, and nothing here proves crash durability.
- The store context manager ``project_job_read_snapshot`` and the scoped
  daemon wiring are the in-flight fixed interface documented by
  ``_state/t35-log-overflow-parent-20261112/job_scope_store_implementation_brief_20261112.md``
  and ``daemon_job_scope_candidate1.py.txt``; if either API differs, these
  tests fail truthfully instead of passing vacuously.
- All projects/jobs/results/updates are unique synthetic literals. No
  real model, Worker, SSH, engine, service, network, credential,
  historical DB/job/UNKNOWN state, cancel/reconcile, destructive
  operation, signal, or subprocess is touched. No pass is claimed here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

import comsol_mcp._control_daemon as control_daemon_module
from comsol_mcp._operation_store import OperationStore
from tests.test_t35_job_read_project_scope_20261112 import (
    PROJECT_A,
    PROJECT_B,
    _canonical_route,
    _create_synthetic_job,
    _FakeProjectAuthority,
    _InertDispatchReceiver,
    _outer_route,
    _serialized,
)


# ---------------------------------------------------------------------------
# Edge 1 — job.wait must not retain the store lock/transaction while asleep,
# with an actual same-process completion witness.
# ---------------------------------------------------------------------------

def test_t35ji_job_wait_does_not_hold_store_lock_while_asleep_with_completion_witness() -> None:
    """Real ``job.wait`` route: pause its poll sleep on a bounded Event and
    prove a second thread's real ``store.update_job`` SUCCEEDED commit
    completes WHILE the wait thread is still inside sleep (a retained store
    lock or open read transaction would block it), then that the wait
    thread itself returns the terminal job with the exact marker.

    Models same-process threading/SQLite only — not process crash or OS
    signal behaviour. The daemon module's ``time`` attribute is swapped to
    a minimal object (original ``monotonic`` + instrumented ``sleep``)
    around the wait route only and restored in ``finally``; global
    ``time.sleep`` is never patched.
    """
    result_marker = "T35JI-WAIT-RESULT-DURING-SLEEP-8c4e"
    witness_marker = "T35JI-WAIT-WITNESS-2b91"
    store = OperationStore(":memory:")
    threads: list[threading.Thread] = []
    try:
        # Queued (non-terminal) synthetic job; authorized under A.
        job_id = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="wait-edge-20261112"
        )
        queued = store.job(job_id)
        assert isinstance(queued, dict) and queued["status"] == "QUEUED"
        assert queued.get("result") is None

        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}})
        receiver = _InertDispatchReceiver(store, authority)

        original_time = control_daemon_module.time
        entered_sleep = threading.Event()
        release_sleep = threading.Event()
        left_sleep = threading.Event()
        writer_done = threading.Event()
        results: dict[str, Any] = {}
        errors: dict[str, BaseException] = {}

        class _PausingSleepTime:
            """Minimal stand-in for the daemon module's ``time`` attribute."""

            monotonic = staticmethod(original_time.monotonic)

            def sleep(self, seconds: float) -> None:
                entered_sleep.set()
                # Bounded wait so the wait thread can never hang forever.
                if not release_sleep.wait(timeout=20.0):
                    errors["sleep"] = TimeoutError("test did not release the paused sleep")
                left_sleep.set()

        def _run_wait() -> None:
            try:
                results["wait"] = _canonical_route(
                    receiver,
                    "job.wait",
                    {"job_id": job_id, "timeout_s": 15.0, "poll_interval_s": 0.2},
                    PROJECT_A,
                )
            except BaseException as exc:  # surfaced by the main thread below
                errors["wait"] = exc

        def _run_writer() -> None:
            try:
                store.update_job(
                    job_id,
                    "SUCCEEDED",
                    {"t35_synthetic": True, "t35ji_wait_witness": witness_marker},
                    result={"success": True, "data": {"marker": result_marker}},
                )
                writer_done.set()
            except BaseException as exc:  # surfaced by the main thread below
                errors["writer"] = exc

        wait_thread = threading.Thread(target=_run_wait, name="t35ji-wait-edge", daemon=False)
        writer_thread = threading.Thread(target=_run_writer, name="t35ji-writer-edge", daemon=False)
        threads = [wait_thread, writer_thread]
        try:
            # Cleanup below is registered (lexically) before either thread starts.
            control_daemon_module.time = _PausingSleepTime()
            wait_thread.start()
            assert entered_sleep.wait(timeout=10.0), (
                f"job.wait never entered the instrumented sleep; errors={errors!r}"
            )
            assert wait_thread.is_alive()
            writer_thread.start()
            assert writer_done.wait(timeout=10.0), (
                "real store.update_job did NOT complete within 10s while "
                "job.wait was paused inside sleep (the wait route likely "
                f"retains the store lock/read transaction); errors={errors!r}"
            )
            assert not left_sleep.is_set(), (
                "wait thread left sleep before the concurrent update completed; "
                "the completion witness is not a same-sleep-window proof"
            )
            release_sleep.set()
            wait_thread.join(timeout=20.0)
            assert not wait_thread.is_alive(), "wait thread did not join within 20s"
            writer_thread.join(timeout=20.0)
            assert not writer_thread.is_alive(), "writer thread did not join within 20s"
            assert not errors, f"test-owned thread exception(s): {errors!r}"
            response = results.get("wait")
            assert isinstance(response, dict) and response.get("success") is True, (
                f"wait route returned non-success: {response!r}"
            )
            data = response["data"]
            assert data["job_id"] == job_id
            assert data["status"] == "SUCCEEDED"
            assert data["result"]["data"]["marker"] == result_marker
            assert witness_marker in json.dumps(data, sort_keys=True, default=str)
            # The fake inspect authority ran once for the dispatch; the
            # per-poll snapshot re-entry must not re-authorize.
            assert authority.calls == [(PROJECT_A, "inspect")]
        finally:
            release_sleep.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(timeout=20.0)
            control_daemon_module.time = original_time
            # An unjoinable thread must surface truthfully; the store stays
            # open in that case rather than falsely claiming full cleanup.
            alive = [thread.name for thread in threads if thread.is_alive()]
            assert not alive, (
                "T35JI wait/writer thread could not be joined; refusing to "
                f"claim cleanup: {alive!r}"
            )
    finally:
        # Do not close a connection still in use by either test-owned thread.
        if not any(thread.is_alive() for thread in threads):
            store.close()


# ---------------------------------------------------------------------------
# Edge 2 — legacy scoped aliases share the outer envelope gate.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "legacy_alias",
    ["run_study_status", "visible_main_workflow_status"],
)
def test_t35ji_scoped_legacy_alias_enforces_durable_project_binding(legacy_alias: str) -> None:
    """Each legacy scoped alias: A envelope + A-bound synthetic job serves the
    exact result marker; an inspect-granted B envelope must refuse the same
    job with no marker anywhere in the serialized response while still
    recording the B authority call; an explicitly absent outer execution
    project ID fails closed (PROJECT_IDENTITY_REQUIRED) without the marker.

    No reconcile/cancel path is exercised. Positive and negative controls are
    both required per alias so a blanket denial cannot pass as correct.
    """
    result_marker = f"T35JI-ALIAS-{legacy_alias}-RESULT-5e72"
    store = OperationStore(":memory:")
    try:
        bound_job = _create_synthetic_job(
            store,
            project_id=PROJECT_A,
            tag=f"alias-{legacy_alias}-bound-a",
            result_marker=result_marker,
        )
        authority = _FakeProjectAuthority({PROJECT_A: {"inspect"}, PROJECT_B: {"inspect"}})
        receiver = _InertDispatchReceiver(store, authority)

        granted = _outer_route(
            receiver, legacy_alias, {"job_id": bound_job}, {"project_id": PROJECT_A}
        )
        assert granted.get("success") is True, f"{legacy_alias}: A envelope refused its own job"
        assert granted["data"]["job_id"] == bound_job
        assert granted["data"]["result"]["data"]["marker"] == result_marker

        foreign = _outer_route(
            receiver, legacy_alias, {"job_id": bound_job}, {"project_id": PROJECT_B}
        )
        assert foreign.get("success") is not True, (
            f"{legacy_alias}: B envelope disclosed the A-bound job row"
        )
        assert result_marker not in _serialized(None, foreign), (
            f"{legacy_alias}: durable result marker leaked into the B refusal"
        )

        identity_absent = _outer_route(receiver, legacy_alias, {"job_id": bound_job}, {})
        assert identity_absent.get("success") is not True, (
            f"{legacy_alias}: missing outer execution.project_id was not refused"
        )
        assert result_marker not in _serialized(None, identity_absent), (
            f"{legacy_alias}: result marker leaked into the no-identity refusal"
        )
        assert (identity_absent.get("error") or {}).get("code") == "PROJECT_IDENTITY_REQUIRED"

        # The absent-identity call must fail before any authority call, and
        # the A/B calls must each have run exactly once in route order.
        assert authority.calls == [(PROJECT_A, "inspect"), (PROJECT_B, "inspect")]
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Edge 3 — WAL file-backed snapshot coherence across an independent writer.
# ---------------------------------------------------------------------------

def test_t35ji_wal_second_connection_commit_keeps_reader_snapshot_consistent(tmp_path: Path) -> None:
    """One new file-backed DB at ``tmp_path / 't35-job-scope.sqlite3'`` (pytest
    tmp_path only; the parent runner binds it to a fresh task-owned scratch
    path). Enter ``project_job_read_snapshot(job_id, A)`` — assert ``True``
    and the actual WAL journal mode — then, while that reader transaction is
    open, a SEPARATE ``sqlite3`` connection commits a synthetic job-metadata
    A→B rebind (the linked operation stays A). The writer commit must be
    confirmed before the SAME reader snapshot fetches the real ``store.job``,
    which must still show the previously bound A metadata plus the durable
    result (no torn B-job + A-operation view). After exit, fresh A and B
    snapshot checks are both False for the divergent record, while a
    separate consistent A job remains readable.

    A coherent snapshot may linearize before the writer commit: that is
    coherent visibility, NOT proof of prompt permission revocation, real
    principal authentication, or crash durability. Only the two exact
    own connections are closed; nothing is deleted, unlinked or overwritten.
    """
    db_path = tmp_path / "t35-job-scope.sqlite3"
    divergent_marker = "T35JI-WAL-DIVERGENT-A-77d3"
    consistent_marker = "T35JI-WAL-CONSISTENT-A-9c02"
    store = OperationStore(db_path)
    conn2 = None
    try:
        divergent_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="wal-divergent-a", result_marker=divergent_marker
        )
        consistent_job = _create_synthetic_job(
            store, project_id=PROJECT_A, tag="wal-consistent-a", result_marker=consistent_marker
        )
        conn2 = sqlite3.connect(str(db_path), isolation_level=None)

        with store.project_job_read_snapshot(divergent_job, PROJECT_A) as snapshot_ok:
            assert snapshot_ok is True, "authorized A snapshot on an A-bound job must yield True"
            journal_mode = conn2.execute("PRAGMA journal_mode").fetchone()
            assert str(journal_mode[0]).lower() == "wal", (
                f"file-backed store must actually run in WAL mode, got {journal_mode!r}"
            )

            # Job-metadata-only A→B rebind on the independent connection
            # while the reader transaction is open; operation metadata stays A.
            before = conn2.execute(
                "SELECT metadata FROM jobs WHERE job_id = ?", (divergent_job,)
            ).fetchall()
            assert len(before) == 1, "the synthetic job row must exist for the writer connection"
            raw_meta = before[0][0]
            meta = json.loads(raw_meta) if isinstance(raw_meta, (str, bytes, bytearray)) else dict(raw_meta)
            assert meta["execution"]["project_id"] == PROJECT_A
            execution = dict(meta.get("execution") or {})
            execution["project_id"] = PROJECT_B
            meta["execution"] = execution
            meta["project_id"] = PROJECT_B
            meta["t35ji_wal_rebind"] = "A_to_B"
            updated = conn2.execute(
                "UPDATE jobs SET metadata = ? WHERE job_id = ?",
                (json.dumps(meta, sort_keys=True), divergent_job),
            )
            assert updated.rowcount == 1, "writer connection rebind UPDATE touched no row"
            assert not conn2.in_transaction, "writer UPDATE must be committed before reader fetch"
            after = conn2.execute(
                "SELECT metadata FROM jobs WHERE job_id = ?", (divergent_job,)
            ).fetchone()[0]
            after_meta = json.loads(after)
            assert after_meta["execution"]["project_id"] == PROJECT_B
            assert after_meta["project_id"] == PROJECT_B
            assert after_meta["t35ji_wal_rebind"] == "A_to_B"

            # Same reader snapshot: coherent pre-writer visibility of the job
            # row, including its durable result — never a torn B job + A op.
            row = store.job(divergent_job)
            assert isinstance(row, dict) and row["job_id"] == divergent_job
            assert row["status"] == "SUCCEEDED"
            assert row["metadata"]["execution"]["project_id"] == PROJECT_A
            assert row["operation"]["metadata"]["execution"]["project_id"] == PROJECT_A
            assert row["result"]["data"]["marker"] == divergent_marker
            blob = json.dumps(row, sort_keys=True, default=str)
            assert divergent_marker in blob, "same-snapshot read lost the durable result"
            assert PROJECT_A in blob, "same-snapshot read must still see the A binding"
            assert PROJECT_B not in blob, "same-snapshot read saw the concurrent B rebind (torn view)"
            assert "t35ji_wal_rebind" not in blob

        # Fresh checks after the snapshot: the divergent record matches
        # neither project, because job (B) and operation (A) now disagree.
        with store.project_job_read_snapshot(divergent_job, PROJECT_A) as fresh_a:
            assert fresh_a is False, "divergent record must not pass a fresh A snapshot check"
        with store.project_job_read_snapshot(divergent_job, PROJECT_B) as fresh_b:
            assert fresh_b is False, "divergent record must not pass a fresh B snapshot check"

        # A separate record with consistent A bindings on both sides stays
        # readable inside its own snapshot.
        with store.project_job_read_snapshot(consistent_job, PROJECT_A) as consistent_ok:
            assert consistent_ok is True, "consistent A-bound job must stay snapshot-readable"
            consistent_row = store.job(consistent_job)
            assert isinstance(consistent_row, dict) and consistent_row["job_id"] == consistent_job
            assert consistent_row["result"]["data"]["marker"] == consistent_marker
    finally:
        if conn2 is not None:
            conn2.close()
        store.close()
