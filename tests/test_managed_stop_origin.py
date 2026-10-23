from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

import pytest

from comsol_mcp._control_daemon import ControlDaemon
from comsol_mcp._execution_contract import ExecutionContractError
from comsol_mcp._host_scoped_provider import CONFIG_SCHEMA, HostScopedProvider, scope_sha256
from comsol_mcp._session_context import CanonicalSocket, SessionRuntimeConfig, runtime_state_root
from comsol_mcp._session_server import OwnedServerLauncher
from comsol_mcp._windows_t037 import T037SessionProcessFactory
from comsol_mcp import _windows_t037 as win
from comsol_mcp import _windows_t037_broker as broker
from tests.test_windows_network_intent import effective_group_fixture
from tests.test_windows_t037_broker import _ScmApi

_PRODUCTION_T037_FACTORY_CALL = T037SessionProcessFactory.__call__


class _SoftwareChild:
    def __init__(self, pid: int, live: dict[int, "_SoftwareChild"]):
        self.pid = pid
        self.returncode = None
        self._live = live

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@contextmanager
def _original_scoped_chain(monkeypatch, *, native_child_profile=False):
    """Real daemon/SCM/origin/launcher functions with explicit software OS seams."""
    root_parent = Path("/private/tmp") if Path("/private/tmp").is_dir() else Path(tempfile.gettempdir())
    base = Path(tempfile.mkdtemp(prefix="mso-", dir=root_parent))
    os.chmod(base, 0o700)
    daemon = provider = service = None
    scm = _ScmApi()
    ready, release = threading.Event(), threading.Event()
    errors = []
    thread = None
    children: dict[int, _SoftwareChild] = {}
    next_pid = [48120]
    try:
        monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
        workspace_root = base / "w"
        workspace_root.mkdir(mode=0o700)
        installation = base / "i"
        server = installation / "bin" / "win64" / "comsolmphserver.exe"
        server.parent.mkdir(parents=True)
        server.write_bytes(b"software-only COMSOL Server image")
        mac_launcher = installation / "bin" / "comsol"
        mac_launcher.write_bytes(b"software-only macOS COMSOL launcher")
        config = installation / "bin" / "servers" / "webbridge" / "conf" / "server.xml"
        config.parent.mkdir(parents=True)
        config.write_text('<Server><Service><Connector port="2036" address="0.0.0.0" /></Service></Server>',
                          encoding="utf-8")
        classpath = installation / "client.jar"
        classpath.write_bytes(b"software-only classpath identity")
        java = base / "j" / "java.exe"
        java.parent.mkdir()
        java.write_bytes(b"software-only java identity")

        state_root = runtime_state_root(base / "c", platform_name="windows")
        runtime = SessionRuntimeConfig(
            runtime_id="comsol64", comsol_version="6.4.0.293",
            installation_root=installation, java_executable=java,
            classpath=(classpath,), preferences_dir=base / "p", session_state_root=state_root,
        )

        def process_identity(pid):
            child = children.get(pid)
            return {"alive": child is not None and child.poll() is None,
                    "start_epoch_ms": 1_800_000_000_000 + pid}

        def listeners(port):
            return [(pid, CanonicalSocket("127.0.0.1", port)) for pid, child in children.items()
                    if child.poll() is None]

        original_write_text = Path.write_text

        def software_server_port_write_text(path, data, *args, **kwargs):
            # COMSOL selects an ephemeral port when the normal console command
            # receives -port 0. Model that child-side port-file readback while
            # keeping this fixture entirely local and software-only.
            if (path.name == "server.port" and path.is_relative_to(base)
                    and data == "0"):
                data = "23061"
            return original_write_text(path, data, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", software_server_port_write_text)

        launcher = OwnedServerLauncher(
            process_identity_reader=process_identity, listener_reader=listeners,
            platform_name="windows", ready_timeout_s=0.5, poll_interval_s=0.001,
        )
        daemon = ControlDaemon(
            base / "c", project_root=workspace_root, registry={},
            session_runtime_resolver=lambda _runtime, _project, _session, _root, _state: runtime,
            session_server_launcher=launcher,
        )
        project_result = daemon.dispatch({
            "operation": "project.create",
            "arguments": {"label": "managed-stop-software", "workspace": "task",
                          "policy": {"permissions": ["inspect", "project_write", "compute", "host_control"]}},
            "execution": {"request_id": "managed-stop-project-create",
                          "idempotency_key": "managed-stop-project-create"},
        })
        assert project_result["success"] is True, project_result
        project_id = project_result["data"]["project"]["project_id"]

        profile, _sdk, _snapshot = effective_group_fixture()
        profile = replace(profile, host_resource_profile=replace(
            profile.host_resource_profile, project_id=project_id,
        ))
        if native_child_profile:
            from comsol_mcp._windows_job_limits import JobLimitsV1
            profile = replace(profile, job_limits=JobLimitsV1(4096, 16384, 4),
                              wrapper_schema=win.PROFILE_SCHEMA_V3)
        daemon._windows_t037_profile = profile
        daemon._windows_t037_profile_path = "SOFTWARE_PINNED_T037_PROFILE"
        daemon._require_t037_policy = lambda _operation: None
        monkeypatch.setattr(win, "load_trusted_profile", lambda _path: profile)
        monkeypatch.setattr(T037SessionProcessFactory, "preflight", lambda _self: None)

        def software_factory_call(factory, command, **_options):
            factory._api = object()
            factory._job = object()
            next_pid[0] += 1
            child = _SoftwareChild(next_pid[0], children)
            children[child.pid] = child
            port_file = Path(command[command.index("-portfile") + 1])
            port_file.write_text(command[command.index("-port") + 1], encoding="ascii")
            return child

        def software_factory_close(factory):
            factory._closed = True
            factory._job = None

        monkeypatch.setattr(T037SessionProcessFactory, "__call__", software_factory_call)
        monkeypatch.setattr(T037SessionProcessFactory, "close", software_factory_close)

        stop_scope = {
            "schema": "comsol-mcp-managed-stop-scope/1",
            "runtime_id": {"id": runtime.runtime_id,
                           "class": "comsol_mcp._session_context.SessionRuntimeConfig"},
            "platform": "windows", "ownership_mode": "originalManagedLifetime",
            "host_source": {
                "t037_source_sha256": profile.source_sha256,
                "host_resource_profile_sha256": profile.host_resource_profile.sha256,
                "host_id": profile.host_resource_profile.host_id,
            },
        }
        authorization_ref = "software-only-managed-stop-ref"
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        grant = {
            "project_id": project_id, "action": "session.stop",
            "authorization_ref_sha256": hashlib.sha256(authorization_ref.encode()).hexdigest(),
            "scope_sha256": scope_sha256(project_id, "session.stop", stop_scope),
            "expires_at_utc": expiry,
        }
        provider_dir = base / "provider"
        provider_dir.mkdir(mode=0o700)
        provider_path = provider_dir / "provider.json"
        provider_path.write_text(json.dumps({"schema": CONFIG_SCHEMA, "destinations": [], "grants": [grant]},
                                            sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.chmod(provider_path, 0o600)
        provider = HostScopedProvider.load(provider_path)
        daemon._scoped_host_provider = provider
        daemon.project_authority.authorization_verifier = provider
        daemon.backend.scoped_host_provider = provider

        def service_main(runtime_owner):
            runtime_owner.mark_running()
            daemon._retain_t037_scm_runtime(runtime_owner)
            ready.set()
            if not release.wait(10):
                raise TimeoutError("software SCM chain did not receive teardown")

        service = broker.WindowsScmServiceBroker._for_software_test(
            profile.service_name, service_main, api=scm, token=broker._SCM_TEST_API_TOKEN,
        )

        def run_service():
            try:
                service.run()
            except BaseException as exc:
                errors.append(exc)
                ready.set()

        thread = threading.Thread(target=run_service, name="managed-stop-software-scm")
        thread.start()
        assert ready.wait(3) and not errors, errors
        yield {
            "base": base, "daemon": daemon, "provider": provider, "profile": profile,
            "runtime": runtime, "project_id": project_id, "children": children,
            "scm": scm, "service": service, "authorization_ref": authorization_ref,
            "stop_scope": stop_scope,
        }
    finally:
        if scm.handler is not None:
            try:
                scm.handler(broker.SERVICE_CONTROL_STOP, 0, None, None)
            except Exception:
                pass
        release.set()
        if thread is not None:
            thread.join(timeout=3)
        if daemon is not None:
            daemon.close()
        if provider is not None:
            provider.close()
        shutil.rmtree(base, ignore_errors=True)
        assert thread is None or not thread.is_alive()
        assert not errors, errors


def test_non_windows_console_start_without_scoped_provider_remains_baseline_compatible(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon = ctx["daemon"]
        daemon._windows_t037_profile = None
        daemon._windows_t037_profile_path = None
        daemon._windows_t037_scm_binding = None
        daemon._windows_t037_scm_runtime = None
        daemon._scoped_host_provider = None
        daemon.project_authority.authorization_verifier = None
        daemon.backend.scoped_host_provider = None
        launcher = daemon.session_server_launcher
        launcher.platform_name = "darwin"
        calls = []

        def create_child(command, **_options):
            calls.append(tuple(command))
            child = _SoftwareChild(48198, ctx["children"])
            ctx["children"][child.pid] = child
            Path(command[command.index("-portfile") + 1]).write_text(
                command[command.index("-port") + 1], encoding="ascii")
            return child

        launcher.process_factory = create_child
        result = _start(daemon, ctx["project_id"], "baseline-non-windows-console-start")
        assert result.get("success") is True, result
        assert len(calls) == 1
        assert getattr(daemon, "_managed_stop_origins", {}) == {}


def _start(daemon, project_id, key):
    return daemon.dispatch({
        "operation": "session.start",
        "arguments": {"project_id": project_id, "idempotency_key": key,
                       "runtime_id": "comsol64"},
        "execution": {},
    })


def _stop(daemon, project_id, session_id, authorization_ref, key):
    return daemon.dispatch({
        "operation": "session.stop",
        "arguments": {"project_id": project_id, "session_id": session_id,
                       "idempotency_key": key, "authorization_ref": authorization_ref},
        "execution": {},
    })


def _install_software_native_api(monkeypatch, ctx):
    """Use the production sealed-child implementation with test-only OS APIs."""
    from types import SimpleNamespace
    from tests.test_windows_suspended_ownership import _launch_api

    profile, api, events, state = _launch_api(
        monkeypatch, cleanup_fail=True, profile_override=ctx["profile"],
    )
    assert profile is ctx["profile"]
    native_api_class = win._Win32LaunchApi
    api_constructor = lambda: api
    api_constructor.CREATE_SUSPENDED = native_api_class.CREATE_SUSPENDED
    monkeypatch.setattr(win, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(win, "_Win32LaunchApi", api_constructor)
    # The exact production __call__ enters the real factory-birth scope and
    # calls create_suspended_appcontainer_child with session_factory=self.
    monkeypatch.setattr(T037SessionProcessFactory, "__call__",
                        _PRODUCTION_T037_FACTORY_CALL)
    return api, events, state


def test_original_scm_factory_birth_and_normal_stop_share_one_exact_origin(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        stop_ref = ctx["authorization_ref"]
        stop_effects = []
        original_stop = daemon.session_server_launcher.stop

        def count_stop(handle, **kwargs):
            stop_effects.append(handle)
            return original_stop(handle, **kwargs)

        daemon.session_server_launcher.stop = count_stop
        stopped = []
        for index in range(2):
            started = _start(daemon, project_id, f"managed-stop-start-{index}")
            assert started["success"] is True, started
            session_id = started["data"]["session_id"]
            origin = daemon._managed_stop_origins[(project_id, session_id)]
            assert origin.handoff_state == "READY"
            assert origin.scope == ctx["stop_scope"]

            denied_policy = daemon.dispatch({
                "operation": "project.policy_set",
                "arguments": {"project_id": project_id,
                              "policy": {"permissions": ["inspect", "project_write", "compute"]},
                              "authorization_ref": stop_ref,
                              "idempotency_key": f"managed-stop-policy-denied-{index}"},
                "execution": {"project_id": project_id,
                              "idempotency_key": f"managed-stop-policy-denied-{index}"},
            })
            assert denied_policy["success"] is False
            assert denied_policy["error"]["code"] == "AUTHORIZATION_REQUIRED"

            # session.stop consults only the scoped verifier; it never calls
            # the provider's always-false legacy project-policy callback.
            with monkeypatch.context() as no_fallback:
                no_fallback.setattr(HostScopedProvider, "__call__",
                                    lambda *_args: pytest.fail("scoped stop fell back to __call__"))
                wrong_ref = _stop(daemon, project_id, session_id, "wrong-managed-stop-ref",
                                  f"managed-stop-wrong-ref-{index}")
                assert wrong_ref["success"] is False
                assert wrong_ref["error"]["code"] == "AUTHORIZATION_REQUIRED"
                assert ctx["children"][origin.process.pid].poll() is None
                conflict = _stop(daemon, project_id, session_id, stop_ref,
                                 f"managed-stop-wrong-ref-{index}")
                assert conflict["success"] is False
                assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
                assert ctx["children"][origin.process.pid].poll() is None

                result = _stop(daemon, project_id, session_id, stop_ref, f"managed-stop-stop-{index}")
            assert result["success"] is True, result
            assert result["data"]["state"] == "STOPPED"
            assert result["data"]["authorization_ref_sha256"] == hashlib.sha256(stop_ref.encode()).hexdigest()
            assert ctx["children"][origin.process.pid].poll() == 0
            assert daemon._managed_stop_origins[(project_id, session_id)] is origin
            assert (project_id, session_id) not in daemon._session_server_handles
            audit = [event for event in daemon.store.events(result["execution"]["job_id"])
                     if event["event"] == "ManagedStopAuthorization"]
            assert len(audit) == 1
            serialized = json.dumps(audit[0]["metadata"], sort_keys=True).casefold()
            assert stop_ref.casefold() not in serialized
            assert "authorization_ref_sha256" in serialized
            stopped.append(result)

        # A cached terminal delivery is not a fresh stop authorization/effect.
        ctx["provider"].close()
        cached = _stop(daemon, project_id, stopped[0]["data"]["session_id"], stop_ref,
                       "managed-stop-stop-0")
        assert cached == stopped[0]
        no_op = _stop(daemon, project_id, stopped[0]["data"]["session_id"], stop_ref,
                      "managed-stop-terminal-noop")
        assert no_op["success"] is True and no_op["data"]["already_stopped"] is True
        assert len(stop_effects) == 2
        assert len([event for event in daemon.store.events(stopped[0]["execution"]["job_id"])
                    if event["event"] == "ManagedStopAuthorization"]) == 1
        assert len(daemon._managed_stop_origins) == 2
        assert {item.scope.__repr__() for item in daemon._managed_stop_origins.values()} == {
            ctx["stop_scope"].__repr__(),
        }


def test_original_scm_origin_handoff_and_scoped_stop_clean_actual_sealed_child(monkeypatch):
    from comsol_mcp._windows_t037 import _RetainedSuspendedChild

    with _original_scoped_chain(monkeypatch, native_child_profile=True) as ctx:
        api, events, state = _install_software_native_api(monkeypatch, ctx)
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-native-child-start")
        assert not started["success"]
        assert started["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert daemon.store.job(started["execution"]["job_id"])["status"] == "UNKNOWN"
        session_id = started["data"]["session_id"]
        key = (project_id, session_id)
        origin = daemon._managed_stop_origins[key]
        handle = daemon._session_server_handles[key]
        process = handle.process
        assert type(process) is _RetainedSuspendedChild
        assert process._original_factory is origin.factory
        assert process._owner is handle
        assert handle.suspended_launch_recovery is process
        assert process.never_resumed is True
        assert origin.handoff_state == "CHILD_HANDLE_RETAINED"
        assert "CREATE_SUSPENDED" in events and "RESUME" not in events
        terminate_count = sum(isinstance(item, tuple) and item[0] == "terminate" for item in events)
        assert terminate_count == 1

        wrong_ref = _stop(daemon, project_id, session_id, "wrong-native-stop-ref",
                          "managed-stop-native-wrong-ref")
        assert not wrong_ref["success"]
        assert wrong_ref["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert sum(isinstance(item, tuple) and item[0] == "terminate" for item in events) == terminate_count
        assert not process._closed and state["alive"] is True

        # The test API now reports the original child exited after its one
        # failed launch-cleanup attempt. Public session.stop must release that
        # same sealed owner without issuing a second termination request.
        state["alive"] = False
        stopped = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                        "managed-stop-native-child-stop")
        assert stopped["success"] is True, stopped
        evidence = stopped["data"]["native_child_exit_evidence"]
        assert evidence["never_resumed_native_child"] is True
        assert evidence["exit_confirmed"] is True and evidence["child_reaped"] is True
        assert process._closed and handle.retired
        assert daemon._managed_stop_origins[key] is origin
        assert daemon.store.job(started["execution"]["job_id"])["status"] == "UNKNOWN"
        assert sum(isinstance(item, tuple) and item[0] == "terminate" for item in events) == terminate_count


def test_missing_child_handle_handoff_keeps_native_origin_unusable(monkeypatch):
    from comsol_mcp._windows_t037 import _RetainedSuspendedChild

    with _original_scoped_chain(monkeypatch, native_child_profile=True) as ctx:
        api, events, state = _install_software_native_api(monkeypatch, ctx)
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        launcher = daemon.session_server_launcher
        original_start = launcher.start

        def omit_child_handle_handoff(runtime, project, session, **options):
            handoff = options["owner_handoff"]

            def skip_only_child_handle(stage, **facts):
                if stage != "CHILD_HANDLE":
                    return handoff(stage, **facts)

            options["owner_handoff"] = skip_only_child_handle
            return original_start(runtime, project, session, **options)

        launcher.start = omit_child_handle_handoff
        started = _start(daemon, project_id, "managed-stop-native-missing-handoff-start")
        assert not started["success"] and started["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        session_id = started["data"]["session_id"]
        key = (project_id, session_id)
        origin = daemon._managed_stop_origins[key]
        process = daemon._session_server_handles[key].process
        assert type(process) is _RetainedSuspendedChild
        assert origin.handoff_state == "PRE_CREATE_BOUND"
        before = sum(isinstance(item, tuple) and item[0] == "terminate" for item in events)

        refused = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                        "managed-stop-native-missing-handoff-stop")
        assert not refused["success"]
        assert refused["error"]["code"] == "MANAGED_STOP_ORIGIN_UNKNOWN"
        assert daemon.store.job(refused["execution"]["job_id"])["status"] == "UNKNOWN"
        assert process._owner is daemon._session_server_handles[key]
        assert not process._closed and state["alive"] is True
        assert sum(isinstance(item, tuple) and item[0] == "terminate" for item in events) == before


def test_native_cleanup_unknown_retry_does_not_repeat_termination_effect(monkeypatch):
    from comsol_mcp._windows_t037 import _RetainedSuspendedChild

    with _original_scoped_chain(monkeypatch, native_child_profile=True) as ctx:
        api, events, state = _install_software_native_api(monkeypatch, ctx)
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-native-unknown-start")
        assert not started["success"] and started["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        session_id = started["data"]["session_id"]
        process = daemon._session_server_handles[(project_id, session_id)].process
        assert type(process) is _RetainedSuspendedChild
        assert process.never_resumed is True and state["alive"] is True
        initial_terminations = sum(isinstance(item, tuple) and item[0] == "terminate" for item in events)
        assert initial_terminations == 1

        first = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                      "managed-stop-native-unknown-stop-1")
        assert not first["success"] and first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert first["error"]["safe_retry"] is False
        after_first = sum(isinstance(item, tuple) and item[0] == "terminate" for item in events)
        assert after_first == initial_terminations

        second = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                       "managed-stop-native-unknown-stop-2")
        assert not second["success"] and second["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert second["error"]["safe_retry"] is False
        assert sum(isinstance(item, tuple) and item[0] == "terminate" for item in events) == initial_terminations

        # Only an observed terminal child can let this software fixture finish;
        # no second termination request is introduced for test cleanup.
        state["alive"] = False
        terminal = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                         "managed-stop-native-unknown-terminal-observed")
        assert terminal["success"] is True, terminal
        assert process._closed
        assert sum(isinstance(item, tuple) and item[0] == "terminate" for item in events) == initial_terminations


def test_origin_copy_and_map_removal_fail_closed_without_reconstructing_owner(monkeypatch):
    import copy

    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-drift-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        key = (project_id, session_id)
        origin = daemon._managed_stop_origins[key]
        process = origin.process

        with pytest.raises(ExecutionContractError, match="cannot be copied"):
            copy.copy(origin)

        daemon._managed_stop_origins.pop(key)
        missing = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-root-removed")
        assert missing["success"] is False
        assert missing["error"]["code"] == "MANAGED_STOP_ORIGIN_UNKNOWN"
        assert daemon.store.job(missing["execution"]["job_id"])["status"] == "UNKNOWN"
        assert process.poll() is None


def test_forged_sealed_lookalike_cannot_replace_original_daemon_root(monkeypatch):
    from comsol_mcp._managed_stop_origin import ManagedStopOrigin

    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-forged-root-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        key = (project_id, session_id)
        origin = daemon._managed_stop_origins[key]
        process = origin.process
        forged = object.__new__(ManagedStopOrigin)
        for field in ManagedStopOrigin.__slots__:
            if field != "__weakref__":
                setattr(forged, field, getattr(origin, field))
        daemon._managed_stop_origins[key] = forged

        result = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                       "managed-stop-forged-root")
        assert result["success"] is False
        assert result["error"]["code"] == "MANAGED_STOP_ORIGIN_UNKNOWN"
        assert process.poll() is None
        assert daemon._managed_stop_origins[key] is forged


def test_current_trusted_source_drift_fences_stop_before_native_effect(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-source-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = daemon._managed_stop_origins[(project_id, session_id)].process

        monkeypatch.setattr(win, "load_trusted_profile", lambda _path: replace(
            ctx["profile"], source_sha256="e" * 64,
        ))
        drifted = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-source-drift")
        assert drifted["success"] is False
        assert drifted["error"]["code"] == "MANAGED_STOP_ORIGIN_UNKNOWN"
        assert daemon.store.job(drifted["execution"]["job_id"])["status"] == "UNKNOWN"
        assert process.poll() is None


def test_created_child_without_staged_handoff_remains_unknown_and_is_never_adopted(monkeypatch):
    from comsol_mcp._managed_stop_origin import ManagedStopOrigin

    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        stop_calls = []
        original_stop = daemon.session_server_launcher.stop
        daemon.session_server_launcher.stop = lambda handle, **kwargs: (
            stop_calls.append(handle), original_stop(handle, **kwargs),
        )[1]
        original_handoff = ManagedStopOrigin.launcher_handoff

        def lose_child_handoff(self, stage, **facts):
            if stage == "CHILD_HANDLE":
                raise RuntimeError("software-only staged-handoff fault")
            return original_handoff(self, stage, **facts)

        monkeypatch.setattr(ManagedStopOrigin, "launcher_handoff", lose_child_handoff)
        started = _start(daemon, project_id, "managed-stop-handoff-loss")
        assert started["success"] is False
        assert started["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        session_id = started["data"]["session_id"]
        origin = daemon._managed_stop_origins[(project_id, session_id)]
        assert origin.handoff_state == "PRE_CREATE_BOUND"
        assert origin.handle is None
        retained = daemon._session_server_handles[(project_id, session_id)]

        stopped = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                        "managed-stop-after-lost-handoff")
        assert stopped["success"] is False
        assert stopped["error"]["code"] == "SERVER_OWNERSHIP_MISMATCH"
        assert stop_calls == []
        assert daemon.session_lifecycle.get(project_id, session_id)["state"] == "UNKNOWN"
        assert daemon._session_server_handles[(project_id, session_id)] is retained
        assert daemon._managed_stop_origins[(project_id, session_id)] is origin


def test_absent_prebirth_child_keeps_diagnostic_root_without_stop_authority(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]

        def fail_before_child(_factory, _command, **_options):
            raise OSError("software-only process-create refusal")

        monkeypatch.setattr(T037SessionProcessFactory, "__call__", fail_before_child)
        started = _start(daemon, project_id, "managed-stop-no-child-start")
        assert started["success"] is False
        assert started["error"]["code"] == "SERVER_START_FAILED"
        session_id = started["data"]["session_id"]
        origin = daemon._managed_stop_origins[(project_id, session_id)]
        assert origin.handoff_state == "PRE_CREATE_BOUND"
        assert origin.handle is None
        assert (project_id, session_id) not in daemon._session_server_handles
        stopped = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                        "managed-stop-no-child-stop")
        assert stopped["success"] is True
        assert stopped["data"]["already_stopped"] is True


def test_permission_is_rechecked_after_initial_admission_and_before_stop(monkeypatch):
    from comsol_mcp._managed_stop_origin import ManagedStopOrigin

    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-permission-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        origin = daemon._managed_stop_origins[(project_id, session_id)]
        process = origin.process
        original_validate = ManagedStopOrigin.validate_for_stop
        ceiling = set(daemon.backend.host_permission_ceiling)

        def drift_permission_after_origin_validation(self, *args, **kwargs):
            result = original_validate(self, *args, **kwargs)
            daemon.backend.host_permission_ceiling.discard("host_control")
            return result

        with monkeypatch.context() as local:
            local.setattr(ManagedStopOrigin, "validate_for_stop", drift_permission_after_origin_validation)
            refused = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                            "managed-stop-permission-drift")
        assert refused["success"] is False
        assert refused["error"]["code"] == "PERMISSION_DENIED"
        assert process.poll() is None
        daemon.backend.host_permission_ceiling = ceiling

        accepted = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                         "managed-stop-permission-restored")
        assert accepted["success"] is True, accepted
        assert process.poll() == 0


def test_provider_file_drift_refuses_before_stop(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-provider-drift-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = daemon._managed_stop_origins[(project_id, session_id)].process
        with ctx["provider"]._path.open("ab") as target:
            target.write(b" ")
        refused = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                        "managed-stop-provider-file-drift")
        assert refused["success"] is False
        assert refused["error"]["code"] == "AUTHORIZATION_STATE_UNKNOWN"
        assert process.poll() is None


def test_origin_requires_same_runtime_provider_scm_factory_job_child_and_handle_objects(monkeypatch):
    from comsol_mcp._managed_stop_origin import ManagedStopOriginError

    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-object-binding-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        key = (project_id, session_id)
        origin = daemon._managed_stop_origins[key]
        factory, handle = origin.factory, origin.handle
        faults = [
            (daemon._session_runtime_configs, key, object()),
            (daemon._session_t037_process_factories, key, object()),
            (daemon._managed_stop_origins, key, object()),
        ]
        for mapping, map_key, replacement in faults:
            original = mapping[map_key]
            mapping[map_key] = replacement
            with pytest.raises(ManagedStopOriginError):
                origin.validate_for_stop(daemon, handle)
            mapping[map_key] = original

        original_binding = daemon._windows_t037_scm_binding
        daemon._windows_t037_scm_binding = tuple(list(original_binding))
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        daemon._windows_t037_scm_binding = original_binding

        original_scm_runtime = daemon._windows_t037_scm_runtime
        daemon._windows_t037_scm_runtime = object()
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        daemon._windows_t037_scm_runtime = original_scm_runtime

        original_launcher = daemon.session_server_launcher
        daemon.session_server_launcher = object.__new__(OwnedServerLauncher)
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        daemon.session_server_launcher = original_launcher

        original_provider = daemon._scoped_host_provider
        daemon._scoped_host_provider = object.__new__(HostScopedProvider)
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        daemon._scoped_host_provider = original_provider

        original_job, original_api = factory._job, factory._api
        factory._job = object()
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        factory._job = original_job
        factory._api = object()
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        factory._api = original_api

        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, object())
        original_process = handle.process
        handle.process = object()
        with pytest.raises(ManagedStopOriginError):
            origin.validate_for_stop(daemon, handle)
        handle.process = original_process

        result = _stop(daemon, project_id, session_id, ctx["authorization_ref"],
                       "managed-stop-object-binding-stop")
        assert result["success"] is True, result


def test_expired_scoped_grant_refuses_without_effect_then_fresh_valid_request_stops(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-expiry-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        process = daemon._managed_stop_origins[(project_id, session_id)].process

        saved_grants = list(ctx["provider"]._grants)
        row = saved_grants[0]
        ctx["provider"]._grants[:] = [(*row[:4], "2000-01-01T00:00:00Z")]
        expired = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-expired")
        assert expired["success"] is False
        assert expired["error"]["code"] == "AUTHORIZATION_REQUIRED"
        assert process.poll() is None
        ctx["provider"]._grants[:] = saved_grants

        stopped = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-after-drift")
        assert stopped["success"] is True, stopped
        assert process.poll() == 0


def test_original_stop_unknown_is_not_reissued_under_a_new_key(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-unknown-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        origin = daemon._managed_stop_origins[(project_id, session_id)]
        process = origin.process
        launcher = daemon.session_server_launcher
        original_stop = launcher.stop
        calls = []

        def unknown_once(handle, **kwargs):
            calls.append(handle)
            raise OSError("software-only uncertain stop boundary")

        launcher.stop = unknown_once
        first = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-unknown-1")
        assert first["success"] is False
        assert first["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert calls == [origin.handle]
        launcher.stop = original_stop

        second = _stop(daemon, project_id, session_id, ctx["authorization_ref"], "managed-stop-unknown-2")
        assert second["success"] is False
        assert second["error"]["code"] == "EXECUTION_STATE_UNKNOWN"
        assert calls == [origin.handle]
        assert process.poll() is None


def test_blocking_stop_holds_neither_provider_nor_store_lock_and_metadata_remains_responsive(monkeypatch):
    with _original_scoped_chain(monkeypatch) as ctx:
        daemon, project_id = ctx["daemon"], ctx["project_id"]
        started = _start(daemon, project_id, "managed-stop-lock-start")
        assert started["success"] is True, started
        session_id = started["data"]["session_id"]
        launcher = daemon.session_server_launcher
        original_stop = launcher.stop
        entered, release = threading.Event(), threading.Event()
        result = []

        def blocked_stop(handle, **kwargs):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("software stop boundary did not release")
            return original_stop(handle, **kwargs)

        launcher.stop = blocked_stop

        def run_stop():
            result.append(_stop(daemon, project_id, session_id, ctx["authorization_ref"],
                                "managed-stop-lock-stop"))

        thread = threading.Thread(target=run_stop, name="managed-stop-lock-check")
        thread.start()
        try:
            assert entered.wait(3), "stop did not reach the blocking exact-handle boundary"
            assert ctx["provider"]._lock.acquire(blocking=False)
            ctx["provider"]._lock.release()
            assert daemon.store.lock.acquire(blocking=False)
            daemon.store.lock.release()
            inspected = daemon.dispatch({
                "operation": "project.inspect",
                "arguments": {"project_id": project_id},
                "execution": {"project_id": project_id},
            })
            assert inspected["success"] is True, inspected
        finally:
            release.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert len(result) == 1 and result[0]["success"] is True, result
