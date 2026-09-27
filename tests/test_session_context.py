from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import json
import logging
from pathlib import Path
import threading
import time

import pytest

from comsol_mcp._session_context import (
    CanonicalSocket,
    OwnedServerProcessIdentity,
    SessionContextError,
    SessionContextMissing,
    SessionEndpointIdentity,
    SessionEndpointScheduler,
    SessionIdentityConflict,
    SessionIdentityUnknown,
    SessionRegistryConflict,
    SessionRuntimeConfig,
    SessionRuntimeContext,
    SessionRuntimeRegistry,
    SessionBindingBusy,
    SessionSchedulerClosed,
    capture_session_callback,
    current_session_context,
    use_session_context,
)
from comsol_mcp._managed_backend import ManagedBackend
from comsol_mcp._operation_store import OperationStore
from comsol_mcp._state import _read_workflow_state, _write_workflow_state
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._connection import _disconnect_locked, _ensure_client_shell
from comsol_mcp._server import _ContextFileHandler, runtime_setting
from comsol_mcp._state import _run_tool


def _process(pid: int, birth: str, *listeners: CanonicalSocket) -> OwnedServerProcessIdentity:
    return OwnedServerProcessIdentity(
        pid=pid,
        birth=birth,
        executable="/opt/comsol/bin/comsolmphserver",
        listener_sockets=tuple(listeners),
    )


_AUTO_PEER = object()


def _context(
    name: str,
    *,
    port: int = 2036,
    peer: CanonicalSocket | None | object = _AUTO_PEER,
    process: OwnedServerProcessIdentity | None = None,
    epoch: int = 1,
    ownership: str = "shared",
    project_id: str | None = None,
    session_id: str | None = None,
    worker: object | None = None,
    remote_client_factory: object | None = None,
    state_root: Path | None = None,
    project_root: Path | None = None,
) -> SessionRuntimeContext:
    if peer is _AUTO_PEER:
        peer = CanonicalSocket("127.0.0.1", port)
    root = state_root or Path("/private/tmp") / f"session-context-{name}"
    runtime = SessionRuntimeConfig(
        runtime_id=f"comsol64-{name}",
        comsol_version="6.4.0.293",
        installation_root=Path("/opt/comsol64"),
        java_executable=Path("/opt/comsol64/java/bin/java"),
        classpath=(Path("/opt/comsol64/plugins/client.jar"),),
        preferences_dir=Path("/private/tmp") / f"prefs-{name}",
        session_state_root=root,
    )
    endpoint = SessionEndpointIdentity(
        host="localhost",
        port=port,
        worker_epoch=epoch,
        observed_peer=peer,
        owned_process=process,
    )
    return SessionRuntimeContext(
        project_id=project_id or f"project-{name}",
        session_id=session_id or f"session-{name}",
        project_root=project_root or Path("/private/tmp") / f"workspace-{name}",
        runtime=runtime,
        endpoint=endpoint,
        worker=worker if worker is not None else object(),
        remote_client_factory=remote_client_factory,
        server_ownership=ownership,
        process_identity=process,
    )


def test_context_access_fails_closed_and_scope_restores_after_nested_error():
    first = _context("first")
    second = _context("second", port=2037)
    with pytest.raises(SessionContextMissing):
        current_session_context()

    with use_session_context(first):
        assert current_session_context() is first
        with pytest.raises(RuntimeError, match="fixture failure"):
            with use_session_context(second):
                assert current_session_context() is second
                raise RuntimeError("fixture failure")
        assert current_session_context() is first

    with pytest.raises(SessionContextMissing):
        current_session_context()


def test_callback_captures_explicit_context_for_background_thread():
    context = _context("callback")
    with use_session_context(context):
        callback = capture_session_callback(lambda: current_session_context())
    result: list[SessionRuntimeContext] = []
    thread = threading.Thread(target=lambda: result.append(callback()))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == [context]
    with pytest.raises(SessionContextMissing):
        capture_session_callback(lambda: None)


def test_runtime_and_endpoint_are_immutable_and_worker_epoch_is_not_session_id():
    context = _context("epoch", project_id="project-a", session_id="logical-session", epoch=4)
    assert context.worker_epoch == 4
    assert context.key == ("project-a", "logical-session")
    with pytest.raises(FrozenInstanceError):
        context.runtime.comsol_version = "6.3"
    with pytest.raises(FrozenInstanceError):
        context.endpoint.worker_epoch = 5
    with pytest.raises(SessionContextError, match="create a new session context"):
        context.endpoint = SessionEndpointIdentity(
            "localhost", 2036, 5, CanonicalSocket("127.0.0.1", 2036)
        )
    with pytest.raises(SessionContextError, match="create a new session context"):
        context.worker = object()


def test_context_mutable_model_and_job_state_are_isolated():
    first = _context("mutable-a")
    second = _context("mutable-b", port=2037)
    first.current_model = "ModelA"
    first.owned_model_tags.add("ModelA")
    first.background_jobs["job-a"] = {"state": "RUNNING"}
    first.workflow_state["model"] = "a.mph"
    assert second.current_model is None
    assert second.owned_model_tags == set()
    assert second.background_jobs == {}
    assert second.workflow_state == {}
    assert first.lock is not second.lock


def test_derived_session_paths_are_hashed_and_project_scoped(tmp_path):
    state = tmp_path / "state"
    one = _context("path-one", project_id="../project", session_id="../../escape", state_root=state)
    two = _context("path-two", project_id="other", session_id="../../escape", state_root=state)
    assert one.paths.root.is_relative_to(state.resolve())
    assert two.paths.root.is_relative_to(state.resolve())
    assert one.paths.root != two.paths.root
    assert one.paths.workflow_file.parent == one.paths.root


@pytest.mark.parametrize("symlink_target", ["root", "sessions", "session"])
def test_derived_session_paths_reject_symlink_escape(tmp_path, symlink_target):
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    project = _context("symlink", state_root=state)
    if symlink_target == "root":
        state.symlink_to(outside, target_is_directory=True)
    elif symlink_target == "sessions":
        state.mkdir()
        (state / "sessions").symlink_to(outside, target_is_directory=True)
    else:
        sessions = state / "sessions"
        sessions.mkdir(parents=True)
        digest = project.paths.root.name
        (sessions / digest).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SessionContextError, match="symlink"):
        _ = project.paths


def test_runtime_classpath_is_a_real_immutable_tuple():
    context = _context("classpath")
    assert isinstance(context.runtime.classpath, tuple)
    with pytest.raises(FrozenInstanceError):
        context.runtime.classpath += (Path("/tmp/extra.jar"),)
    with pytest.raises(ValueError, match="actual non-empty tuple"):
        SessionRuntimeConfig(
            runtime_id="runtime",
            comsol_version="6.4",
            installation_root=Path("/opt/comsol"),
            java_executable=Path("/opt/java"),
            classpath=[Path("/opt/client.jar")],  # type: ignore[arg-type]
            preferences_dir=Path("/tmp/prefs"),
            session_state_root=Path("/tmp/state"),
        )


def test_lazy_client_factories_and_disconnect_are_session_local(monkeypatch):
    import comsol_mcp._server as server_module

    class FakeClient:
        def __init__(self, name):
            self.name = name
            self.disconnect_count = 0

        def disconnect(self):
            self.disconnect_count += 1

    first_client = FakeClient("first")
    second_client = FakeClient("second")
    monkeypatch.setattr(server_module, "_remote_client_factory", lambda: pytest.fail("global factory used"))
    first = _context("lazy-first", remote_client_factory=lambda: first_client)
    second = _context("lazy-second", port=2037, remote_client_factory=lambda: second_client)

    with use_session_context(first):
        assert _ensure_client_shell() is first_client
        first.client_connected = True
        _disconnect_locked()
        assert first.client is first_client
        assert first.client_retired is True
        assert first.client_connected is False
    with use_session_context(second):
        assert _ensure_client_shell() is second_client
        assert second.client is second_client
        assert second.client_connected is False
    assert first_client.disconnect_count == 1
    assert second_client.disconnect_count == 0


def test_managed_lazy_client_never_falls_back_to_process_global_factory(monkeypatch):
    import comsol_mcp._server as server_module

    monkeypatch.setattr(server_module, "_remote_client_factory", lambda: object())
    context = _context("no-global-fallback", remote_client_factory=None, worker=object())
    with use_session_context(context):
        with pytest.raises(RuntimeError, match="Worker-owned client factory"):
            _ensure_client_shell()


def test_legacy_runtime_accessors_route_locks_jobs_and_files_by_session(tmp_path):
    root = tmp_path / "shared-project"
    root.mkdir()
    state_root = tmp_path / "private-state"
    first = _context("accessor-a", project_id="same", session_id="a",
                     project_root=root, state_root=state_root)
    second = _context("accessor-b", port=2037, project_id="same", session_id="b",
                      project_root=root, state_root=state_root)
    barrier = threading.Barrier(2)

    def run(context):
        with use_session_context(context):
            result = json.loads(_run_tool("session_access", lambda: (
                barrier.wait(timeout=2), {"session": current_session_context().session_id}
            )[1]))
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(run, first)
        two = pool.submit(run, second)
        assert one.result(timeout=4)["data"]["session"] == "a"
        assert two.result(timeout=4)["data"]["session"] == "b"

    assert first.paths.status_file.is_file()
    assert second.paths.status_file.is_file()
    assert first.paths.operations_file.is_file()
    assert second.paths.operations_file.is_file()
    assert first.paths.status_file != second.paths.status_file
    assert first.paths.operations_file != second.paths.operations_file
    assert json.loads(first.paths.status_file.read_text())["last_command"] == "session_access"
    assert json.loads(second.paths.status_file.read_text())["last_command"] == "session_access"
    assert first.background_jobs is not second.background_jobs
    assert first.background_jobs_lock is not second.background_jobs_lock
    with use_session_context(first):
        assert runtime_setting("OUTPUTS_DIR") == root / "outputs"
        assert runtime_setting("DEFAULT_PORT") == 2036
    with use_session_context(second):
        assert runtime_setting("OUTPUTS_DIR") == root / "outputs"
        assert runtime_setting("DEFAULT_PORT") == 2037


def test_session_log_handler_routes_to_selected_private_log(tmp_path):
    first = _context("log-a", state_root=tmp_path / "state")
    second = _context("log-b", port=2037, state_root=tmp_path / "state")
    handler = _ContextFileHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    for context, message in ((first, "only-a"), (second, "only-b")):
        record = logging.LogRecord("f04", logging.ERROR, __file__, 1, message, (), None)
        with use_session_context(context):
            handler.handle(record)
    assert first.paths.server_log.read_text().strip() == "only-a"
    assert second.paths.server_log.read_text().strip() == "only-b"


def test_registry_requires_exact_project_session_and_exact_removal():
    registry = SessionRuntimeRegistry()
    old = _context("registry", project_id="project-1", session_id="stable-session", epoch=1)
    replacement = _context("registry-new", project_id="project-1", session_id="stable-session", epoch=2)
    assert registry.register(old) is old
    assert registry.get("project-1", "stable-session") is old
    with pytest.raises(SessionRegistryConflict, match="already has a live"):
        registry.register(replacement)
    with pytest.raises(SessionRegistryConflict, match="exact removal"):
        registry.remove("project-1", "stable-session", expected=replacement)
    registry.remove("project-1", "stable-session", expected=old)
    assert registry.register(replacement) is replacement
    assert registry.get("project-1", "stable-session").worker_epoch == 2


def test_actual_peer_address_unifies_requested_aliases():
    peer = CanonicalSocket("0:0:0:0:0:0:0:1", 2036)
    assert peer.address == "::1"
    assert peer.lock_key == "socket:[::1]:2036"
    owned = _process(9001, "birth-a", peer)
    left = _context("alias-localhost", peer=peer, process=owned, ownership="mcp_managed")
    right = _context("alias-ipv6", peer=peer, process=owned, epoch=2, ownership="mcp_managed")
    assert left.endpoint.socket_lock_key == right.endpoint.socket_lock_key
    assert left.endpoint.owned_process.lock_key == right.endpoint.owned_process.lock_key


def test_unobserved_peer_uses_global_exclusive_lane_and_owned_birth_still_requires_attestation():
    scheduler = SessionEndpointScheduler()
    no_peer = _context("no-peer", peer=None)
    peer = CanonicalSocket("127.0.0.1", 2044)
    owned = _context("peer-owned", peer=peer,
                     process=_process(9003, "birth-peer-owned", peer), ownership="mcp_managed")
    unknown_entered = threading.Event()
    unknown_release = threading.Event()
    owned_entered = threading.Event()

    def block_unknown():
        unknown_entered.set()
        assert unknown_release.wait(2)

    try:
        unknown_future = scheduler.submit(no_peer, block_unknown)
        assert unknown_entered.wait(1)
        owned_future = scheduler.submit(owned, owned_entered.set)
        time.sleep(0.05)
        assert not owned_entered.is_set()
        unknown_release.set()
        unknown_future.result(timeout=2)
        owned_future.result(timeout=2)
        assert owned_entered.is_set()
    finally:
        unknown_release.set()
        scheduler.close()

    wrong_process = _process(9002, "birth-wrong", CanonicalSocket("127.0.0.1", 2045))
    with pytest.raises(ValueError, match="does not attest"):
        _context("wrong-listener", peer=peer, process=wrong_process, ownership="mcp_managed")


def test_retirement_fence_wins_while_owned_task_waits_for_unknown_global_gate():
    peer = CanonicalSocket("127.0.0.1", 2046)
    owned = _context("fenced-after-global-wait", port=2046, peer=peer,
                     process=_process(9004, "birth-fenced-after-wait", peer), ownership="mcp_managed")
    scheduler = SessionEndpointScheduler()
    unknown_entered = threading.Event()
    release_unknown = threading.Event()
    owned_waiting_for_gate = threading.Event()
    callback_calls = []
    original_known_lease = scheduler._global_gate.known_server_lease

    @contextmanager
    def observed_known_lease():
        # Signal from inside the queued task immediately before the original
        # global lease blocks behind the unknown-exclusive holder.
        owned_waiting_for_gate.set()
        with original_known_lease():
            yield

    scheduler._global_gate.known_server_lease = observed_known_lease

    def hold_unknown():
        unknown_entered.set()
        assert release_unknown.wait(2)

    try:
        unknown_future = scheduler.submit(None, hold_unknown)
        assert unknown_entered.wait(1)
        owned_future = scheduler.submit(owned, callback_calls.append, "invoked")
        assert owned_waiting_for_gate.wait(1)
        scheduler.fence_owned_server_lane(owned)
        release_unknown.set()
        unknown_future.result(timeout=2)
        with pytest.raises(SessionSchedulerClosed, match="fenced before callback admission"):
            owned_future.result(timeout=2)
        assert callback_calls == []
    finally:
        release_unknown.set()
        scheduler.close()


def test_two_different_proven_server_processes_overlap():
    peer_a = CanonicalSocket("127.0.0.1", 2036)
    peer_b = CanonicalSocket("127.0.0.1", 2037)
    first = _context("parallel-a", peer=peer_a,
                     process=_process(9101, "birth-a", peer_a), ownership="mcp_managed")
    second = _context("parallel-b", port=2037, peer=peer_b,
                      process=_process(9102, "birth-b", peer_b), ownership="mcp_managed")
    scheduler = SessionEndpointScheduler()
    barrier = threading.Barrier(2)

    def overlap(context):
        assert current_session_context() is context
        barrier.wait(timeout=2)
        return context.worker_epoch

    try:
        left = scheduler.submit(first, overlap, first)
        right = scheduler.submit(second, overlap, second)
        assert (left.result(timeout=3), right.result(timeout=3)) == (1, 1)
    finally:
        scheduler.close()


def test_same_server_connection_epochs_and_listener_sockets_serialize():
    socket_a = CanonicalSocket("127.0.0.1", 2036)
    socket_b = CanonicalSocket("127.0.0.1", 2037)
    process = _process(9200, "one-birth", socket_a, socket_b)
    first = _context("same-process-a", peer=socket_a, process=process,
                     epoch=1, ownership="mcp_managed")
    second = _context("same-process-b", port=2037, peer=socket_b, process=process,
                      epoch=2, ownership="mcp_managed")
    scheduler = SessionEndpointScheduler()
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def hold():
        entered.set()
        assert release.wait(2)

    try:
        one = scheduler.submit(first, hold)
        assert entered.wait(1)
        two = scheduler.submit(second, second_entered.set)
        time.sleep(0.05)
        assert not second_entered.is_set()
        release.set()
        one.result(timeout=2)
        two.result(timeout=2)
        assert second_entered.is_set()
    finally:
        release.set()
        scheduler.close()


def test_scheduler_quiescence_is_exact_to_the_worker_session_binding():
    peer_a = CanonicalSocket("127.0.0.1", 2047)
    peer_b = CanonicalSocket("127.0.0.1", 2048)
    process = _process(9250, "same-server-birth", peer_a, peer_b)
    first = _context("quiescence-a", port=2047, peer=peer_a, process=process,
                     ownership="mcp_managed", project_id="p", session_id="a")
    second = _context("quiescence-b", port=2048, peer=peer_b, process=process,
                      ownership="mcp_managed", project_id="p", session_id="b")
    scheduler = SessionEndpointScheduler()
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        assert release.wait(2)

    try:
        running = scheduler.submit(first, block)
        assert started.wait(1)
        queued = scheduler.submit(second, lambda: None)
        first_snapshot = scheduler.quiescence_snapshot(first)
        second_snapshot = scheduler.quiescence_snapshot(second)
        assert first_snapshot["status"] == "BUSY"
        assert first_snapshot["counts"] == {
            "session_queued": 0, "session_running": 1,
            "worker_queued": 0, "worker_running": 1,
            "owned_server_lane_queued": 1, "owned_server_lane_running": 1,
            "unknown_global_exclusive_queued": 0,
            "unknown_global_exclusive_running": 0,
        }
        assert first_snapshot["worker_status"] == "BUSY"
        assert first_snapshot["server_lane_status"] == "BUSY"
        assert second_snapshot["status"] == "BUSY"
        assert second_snapshot["counts"]["session_queued"] == 1
        assert second_snapshot["counts"]["session_running"] == 0
        assert second_snapshot["counts"]["worker_queued"] == 1
        assert second_snapshot["counts"]["worker_running"] == 0
        release.set()
        running.result(timeout=2)
        queued.result(timeout=2)
        assert scheduler.quiescence_snapshot(first)["status"] == "QUIESCENT"
        assert scheduler.quiescence_snapshot(second)["status"] == "QUIESCENT"
    finally:
        release.set()
        scheduler.close()


def test_session_binding_retirement_is_atomic_quiescence_fence_and_can_release_before_dispatch():
    context = _context("binding-retirement", peer=None)
    scheduler = SessionEndpointScheduler()
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        assert release.wait(2)

    try:
        future = scheduler.submit(context, block)
        assert started.wait(1)
        with pytest.raises(SessionBindingBusy, match="accepted work"):
            scheduler.fence_session_binding(context)
        release.set()
        future.result(timeout=2)

        key = scheduler.fence_session_binding(context)
        assert key == context.worker_binding_key
        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            scheduler.submit(context, lambda: None)

        scheduler.release_session_binding(context)
        assert scheduler.submit(context, lambda: "reopened").result(timeout=2) == "reopened"
    finally:
        release.set()
        scheduler.close()


def test_uncertain_binding_soft_close_blocks_new_submits_but_drains_accepted_work():
    scheduler = SessionEndpointScheduler()
    context = _context("uncertain-soft-close")
    first_entered, release_first = threading.Event(), threading.Event()
    second_ran = threading.Event()
    try:
        def first_task():
            first_entered.set()
            assert release_first.wait(3)
            return "first-finished"

        first = scheduler.submit(context, first_task)
        assert first_entered.wait(1)
        second = scheduler.submit(context, lambda: second_ran.set() or "second-finished")
        scheduler.close_session_binding_admission(context)

        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            scheduler.submit(context, lambda: pytest.fail("new work reached an uncertain Worker"))

        release_first.set()
        assert first.result(timeout=2) == "first-finished"
        assert second.result(timeout=2) == "second-finished"
        assert second_ran.is_set()
        scheduler.release_session_binding(context)
        with pytest.raises(SessionSchedulerClosed, match="binding is fenced"):
            scheduler.submit(context, lambda: None)
    finally:
        release_first.set()
        scheduler.close()


def test_quiescence_covers_exact_worker_shared_across_projects():
    peer = CanonicalSocket("127.0.0.1", 2049)
    process = _process(9251, "shared-worker-server-birth", peer)
    shared_worker = object()
    first = _context(
        "shared-worker-project-a", port=2049, peer=peer, process=process,
        ownership="mcp_managed", project_id="project-a", session_id="session-a",
        worker=shared_worker,
    )
    second = _context(
        "shared-worker-project-b", port=2049, peer=peer, process=process,
        ownership="mcp_managed", project_id="project-b", session_id="session-b",
        worker=shared_worker,
    )
    scheduler = SessionEndpointScheduler()
    started = threading.Event()
    release = threading.Event()

    def block():
        started.set()
        assert release.wait(2)

    try:
        running = scheduler.submit(first, block)
        assert started.wait(1)
        queued = scheduler.submit(second, lambda: None)
        readback = scheduler.quiescence_snapshot(first)
        assert readback["binding"]["project_id"] == "project-a"
        assert readback["scope"] == "session+worker+owned-server-lane+unknown-global-exclusive"
        assert readback["counts"]["session_running"] == 1
        assert readback["counts"]["session_queued"] == 0
        assert readback["counts"]["worker_running"] == 1
        assert readback["counts"]["worker_queued"] == 1
        assert readback["worker_status"] == "BUSY"
        assert scheduler.quiescence_snapshot(second)["worker_status"] == "BUSY"
        release.set()
        running.result(timeout=2)
        queued.result(timeout=2)
        assert scheduler.quiescence_snapshot(first)["worker_status"] == "QUIESCENT"
    finally:
        release.set()
        scheduler.close()


def test_same_process_lane_preserves_fifo_submission_order():
    peer = CanonicalSocket("127.0.0.1", 2040)
    context = _context("fifo", port=2040, peer=peer,
                       process=_process(9300, "birth-fifo", peer), ownership="mcp_managed")
    scheduler = SessionEndpointScheduler()
    started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def head():
        order.append("head")
        started.set()
        assert release.wait(2)

    try:
        first = scheduler.submit(context, head)
        assert started.wait(1)
        second = scheduler.submit(context, order.append, "second")
        third = scheduler.submit(context, order.append, "third")
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
        third.result(timeout=2)
        assert order == ["head", "second", "third"]
    finally:
        release.set()
        scheduler.close()


def test_unknown_lane_excludes_owned_lane_in_both_directions():
    peer = CanonicalSocket("127.0.0.1", 2050)
    owned = _context("owned", port=2050, peer=peer,
                     process=_process(9400, "birth-owned", peer), ownership="mcp_managed")
    unknown = _context("unknown", port=2051, peer=None, ownership="shared")
    scheduler = SessionEndpointScheduler()
    first_started = threading.Event()
    first_release = threading.Event()
    second_started = threading.Event()

    def block_first():
        first_started.set()
        assert first_release.wait(2)

    try:
        first = scheduler.submit(owned, block_first)
        assert first_started.wait(1)
        second = scheduler.submit(unknown, second_started.set)
        time.sleep(0.05)
        assert not second_started.is_set()
        first_release.set()
        first.result(timeout=2)
        second.result(timeout=2)
        assert second_started.is_set()
    finally:
        first_release.set()
        scheduler.close()


def test_busy_owned_lane_backlog_does_not_starve_distinct_owned_lane():
    peer_a = CanonicalSocket("127.0.0.1", 2052)
    peer_b = CanonicalSocket("127.0.0.1", 2053)
    lane_a = _context("backlog-a", port=2052, peer=peer_a,
                      process=_process(9501, "birth-a", peer_a), ownership="mcp_managed")
    lane_b = _context("independent-b", port=2053, peer=peer_b,
                      process=_process(9502, "birth-b", peer_b), ownership="mcp_managed")
    scheduler = SessionEndpointScheduler()
    first_started = threading.Event()
    release = threading.Event()
    independent_started = threading.Event()

    def block_head():
        first_started.set()
        assert release.wait(3)

    futures = []
    try:
        futures.append(scheduler.submit(lane_a, block_head))
        assert first_started.wait(1)
        futures.extend(scheduler.submit(lane_a, lambda: None) for _ in range(64))
        independent = scheduler.submit(lane_b, independent_started.set)
        assert independent_started.wait(1)
        independent.result(timeout=1)
        release.set()
        for future in futures:
            future.result(timeout=4)
    finally:
        release.set()
        scheduler.close()


def test_two_process_births_cannot_claim_the_same_socket():
    peer = CanonicalSocket("127.0.0.1", 2054)
    scheduler = SessionEndpointScheduler()
    first = _context("owner-one", port=2054, peer=peer,
                     process=_process(9601, "birth-one", peer), ownership="mcp_managed")
    second = _context("owner-two", port=2054, peer=peer,
                      process=_process(9602, "birth-two", peer), ownership="mcp_managed")
    try:
        scheduler.submit(first, lambda: None).result(timeout=2)
        with pytest.raises(SessionIdentityConflict, match="another live process birth"):
            scheduler.submit(second, lambda: None)
    finally:
        scheduler.close()


def test_session_binding_cannot_be_replaced_while_work_is_queued():
    peer = CanonicalSocket("127.0.0.1", 2060)
    process = _process(9700, "birth-stable", peer)
    old_worker = object()
    context = _context("queued-binding", port=2060, peer=peer, process=process,
                       ownership="mcp_managed", worker=old_worker)
    scheduler = SessionEndpointScheduler()
    entered = threading.Event()
    release = threading.Event()
    observed = []

    def blocked():
        entered.set()
        assert release.wait(2)
        observed.append((current_session_context().worker, current_session_context().worker_epoch))

    try:
        future = scheduler.submit(context, blocked)
        assert entered.wait(1)
        with pytest.raises(SessionContextError, match="new session context"):
            context.worker = object()
        with pytest.raises(SessionContextError, match="new session context"):
            context.endpoint = SessionEndpointIdentity(
                "localhost", 2060, 2, peer, process
            )
        release.set()
        future.result(timeout=2)
        assert observed == [(old_worker, 1)]
    finally:
        release.set()
        scheduler.close()


def test_scheduler_close_rejects_new_work_and_drains_accepted_work():
    context = _context("closing")
    scheduler = SessionEndpointScheduler()
    started = threading.Event()
    release = threading.Event()
    future = scheduler.submit(context, lambda: (started.set(), release.wait(2)))
    assert started.wait(1)
    close_done = threading.Event()
    closer = threading.Thread(target=lambda: (scheduler.close(), close_done.set()))
    closer.start()
    deadline = time.monotonic() + 1
    while not scheduler.closed and time.monotonic() < deadline:
        time.sleep(0.005)
    assert scheduler.closed
    with pytest.raises(SessionSchedulerClosed):
        scheduler.submit(context, lambda: None)
    assert not close_done.is_set()
    release.set()
    future.result(timeout=2)
    closer.join(timeout=2)
    assert close_done.is_set()


def test_same_project_different_sessions_get_isolated_workflow_files(tmp_path):
    project_root = tmp_path / "registered-workspace"
    project_root.mkdir()
    home = tmp_path / "daemon-home"
    home.mkdir()
    store = OperationStore(home / "operations.sqlite3")
    backend = ManagedBackend(
        home,
        store,
        service=None,
        worker=None,
        registry={},
        project_root=project_root,
    )
    state_root = tmp_path / "private-session-state"
    first = _context(
        "same-project-session-a",
        project_id="same-project",
        session_id="session-a",
        state_root=state_root,
        project_root=project_root,
    )
    second = _context(
        "same-project-session-b",
        project_id="same-project",
        session_id="session-b",
        state_root=state_root,
        project_root=project_root,
    )
    barrier = threading.Barrier(2)

    def write_and_read(context, value):
        with use_session_context(context):
            with backend.project_root_scope(project_root):
                _write_workflow_state({"session_marker": value})
                barrier.wait(timeout=2)
                return _read_workflow_state()["session_marker"]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(write_and_read, first, "from-a")
            right = pool.submit(write_and_read, second, "from-b")
            assert left.result(timeout=4) == "from-a"
            assert right.result(timeout=4) == "from-b"
        assert first.paths.workflow_file != second.paths.workflow_file
        assert first.paths.workflow_file.is_file()
        assert second.paths.workflow_file.is_file()
    finally:
        backend.close()
        store.close()


def test_session_workflow_scope_rejects_a_different_project_root(tmp_path):
    first_root = tmp_path / "project-one"
    second_root = tmp_path / "project-two"
    first_root.mkdir()
    second_root.mkdir()
    home = tmp_path / "daemon-home"
    store = OperationStore(home / "operations.sqlite3")
    backend = ManagedBackend(
        home,
        store,
        service=None,
        worker=None,
        registry={},
        project_root=first_root,
    )
    context = _context("root-mismatch", project_root=first_root)
    try:
        with use_session_context(context):
            with backend.project_root_scope(second_root):
                with pytest.raises(ExecutionContractError, match="does not match the active registered project"):
                    _read_workflow_state()
    finally:
        backend.close()
        store.close()
