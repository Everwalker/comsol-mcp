from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from comsol_mcp._windows_t037_broker import (
    CtypesWindowsScmApi,
    ERROR_SUCCESS,
    SERVICE_CONTROL_INTERROGATE,
    SERVICE_CONTROL_STOP,
    SERVICE_RUNNING,
    SERVICE_START_PENDING,
    SERVICE_STOPPED,
    SERVICE_STOP_PENDING,
    _SCM_TEST_API_TOKEN,
    WindowsScmError,
    WindowsScmServiceBroker,
    scm_abi_layout,
)


class _NativeExport:
    def __init__(self, callback=lambda *_: 1):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class _ScmApi:
    def __init__(self):
        self.handler = None
        self.status = []
        self.dispatch_count = 0

    def start_service_ctrl_dispatcher(self, service_name, callback):
        self.dispatch_count += 1
        assert service_name == "ComsolHost"
        callback(0, None)

    def register_service_ctrl_handler_ex(self, service_name, handler):
        assert service_name == "ComsolHost"
        self.handler = handler
        return 19

    def set_service_status(self, _handle, status):
        self.status.append(status)


def test_scm_abi_uses_fixed_win32_widths_and_named_exports_only():
    layout = scm_abi_layout()
    assert layout["DWORD_size"] == 4
    assert layout["BOOL_size"] == 4
    assert layout["WCHAR_size"] == 2
    assert layout["SERVICE_STATUS_size"] == 28
    assert layout["SERVICE_STATUS_offsets"] == {
        "dwServiceType": 0, "dwCurrentState": 4, "dwControlsAccepted": 8,
        "dwWin32ExitCode": 12, "dwServiceSpecificExitCode": 16,
        "dwCheckPoint": 20, "dwWaitHint": 24,
    }
    exports = SimpleNamespace(**{name: _NativeExport() for name in (
        "StartServiceCtrlDispatcherW", "RegisterServiceCtrlHandlerExW", "SetServiceStatus")})
    kernel = SimpleNamespace(GetLastError=_NativeExport(lambda: 5))
    bound = CtypesWindowsScmApi.bind_exports(exports, kernel)
    assert set(bound) == {
        "StartServiceCtrlDispatcherW", "RegisterServiceCtrlHandlerExW", "SetServiceStatus", "GetLastError"}
    assert exports.StartServiceCtrlDispatcherW.restype.__name__ == "c_int"
    assert len(exports.RegisterServiceCtrlHandlerExW.argtypes) == 3
    with pytest.raises(WindowsScmError) as missing:
        CtypesWindowsScmApi.bind_exports(SimpleNamespace(), kernel)
    assert missing.value.code == "SCM_API_EXPORT_UNAVAILABLE"


def test_scm_service_protocol_registers_reports_and_stops_without_fallback():
    api = _ScmApi()
    ready = threading.Event()
    allow_return = threading.Event()
    observed = {}

    def service_main(runtime):
        runtime.mark_running()
        observed["runtime"] = runtime
        ready.set()
        assert allow_return.wait(3)

    broker = WindowsScmServiceBroker._for_software_test(
        "ComsolHost", service_main, api=api, token=_SCM_TEST_API_TOKEN,
    )
    thread = threading.Thread(target=broker.run, daemon=True)
    thread.start()
    assert ready.wait(2)
    assert [row.current_state for row in api.status] == [SERVICE_START_PENDING, SERVICE_RUNNING]
    assert api.handler(SERVICE_CONTROL_INTERROGATE, 0, None, None) == ERROR_SUCCESS
    assert api.handler(SERVICE_CONTROL_STOP, 0, None, None) == ERROR_SUCCESS
    allow_return.set()
    thread.join(3)
    assert not thread.is_alive()
    assert api.dispatch_count == 1
    assert [row.current_state for row in api.status] == [
        SERVICE_START_PENDING, SERVICE_RUNNING, SERVICE_STOP_PENDING, SERVICE_STOPPED]
    assert observed["runtime"].stop_requested is True
    with pytest.raises(WindowsScmError) as second:
        broker.run()
    assert second.value.code == "SCM_DISPATCH_ALREADY_STARTED"


def test_scm_service_main_cannot_return_without_running_or_stop_signal():
    api = _ScmApi()
    broker = WindowsScmServiceBroker._for_software_test(
        "ComsolHost", lambda _runtime: None, api=api, token=_SCM_TEST_API_TOKEN,
    )
    with pytest.raises(WindowsScmError) as captured:
        broker.run()
    assert captured.value.code == "SCM_SERVICE_RUNTIME_FAILED"
    assert api.status[-1].current_state == SERVICE_STOPPED


def test_windows_t037_internal_request_uses_existing_control_queue_and_rejects_public_wire(tmp_path, monkeypatch):
    # Reuse the actual typed profile/plan/provider fixture and OperationStore
    # authorization semantics exercised by the host-change production tests.
    from tests.test_windows_host_change import _apply_grant, _plan, _profile, _write_provider
    from comsol_mcp._windows_t037 import WindowsT037ProfileV2
    from comsol_mcp._windows_host_change import WindowsHostChangeExecutor
    from comsol_mcp._control_daemon import ControlDaemon

    monkeypatch.setenv("COMSOL_MCP_HOST_CONTROL", "1")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    home = tmp_path / "control"
    first = ControlDaemon(home, project_root=project_root, registry={})
    created = first.dispatch({
        "operation": "project.create",
        "arguments": {
            "label": "t037-broker", "workspace": "t037-broker",
            "policy": {"permissions": ["inspect", "project_write", "compute", "host_control"]},
        },
        "execution": {"request_id": "create-t037-broker", "idempotency_key": "create-t037-broker"},
    })
    assert created["success"] is True
    project = created["data"]["project"]
    first.close()

    profile = replace(_profile(), project_id=project["project_id"])
    plan = _plan(profile)
    authorization_ref = "internal-apply-once"
    provider = _write_provider(
        tmp_path / "host-provider", profile,
        [_apply_grant(profile, plan, ref=authorization_ref)],
    )
    provider_path = Path(provider._path)
    provider.close()
    # The host loader is a Windows-only pinned-handle provider. The route test
    # injects the exact already-parsed typed object; no Windows observation or
    # policy mutation is claimed by this software fixture.
    profile_path = r"C:\private\t037-profile.json"
    profile_wrapper = WindowsT037ProfileV2(
        host_resource_profile=profile,
        source_sha256=hashlib.sha256(b"trusted-profile-fixture").hexdigest(),
        source_identity={"device": 1, "inode": 2},
        security_semantics="SOFTWARE_FIXTURE_ONLY",
    )
    monkeypatch.setattr("comsol_mcp._windows_t037.load_trusted_profile", lambda _path: profile_wrapper)
    daemon = ControlDaemon(
        home, project_root=project_root, registry={}, host_provider_config=provider_path,
        windows_t037_profile=profile_path,
    )

    direct = daemon.dispatch({"operation": "host.change.apply", "arguments": {}, "execution": {}})
    nested = daemon.dispatch({
        "operation": "operation_call",
        "arguments": {"operation_id": "host.change.apply", "arguments": {}},
        "execution": {},
    })
    assert direct["error"]["code"] == "HOST_CONTROL_INTERNAL_ONLY"
    assert nested["error"]["code"] == "HOST_CONTROL_INTERNAL_ONLY"
    assert daemon.store.db.execute(
        "SELECT COUNT(*) FROM host_control_admissions").fetchone()[0] == 0

    api = _ScmApi()
    ready, allow_return = threading.Event(), threading.Event()
    runtime_ref = {}

    def service_main(runtime):
        runtime.mark_running()
        runtime_ref["runtime"] = runtime
        ready.set()
        assert allow_return.wait(4)

    broker = WindowsScmServiceBroker._for_software_test(
        "ComsolHost", service_main, api=api, token=_SCM_TEST_API_TOKEN,
    )
    broker_thread = threading.Thread(target=broker.run, daemon=True)
    broker_thread.start()
    assert ready.wait(2)
    issuer = daemon._windows_t037_host_control_issuer(runtime_ref["runtime"])
    request = issuer.issue(runtime_ref["runtime"], phase="APPLY", plan=plan,
                           authorization_ref=authorization_ref)

    class _FakeExecutor:
        def __init__(self):
            self.profile = profile
            self.authority = daemon.project_authority
            self.provider = daemon._scoped_host_provider
            self.api = SimpleNamespace()  # actual original registry owner, no native acquisition

        def apply(self, selected_plan, *, approval):
            assert selected_plan is plan
            assert approval.authorization_ref == authorization_ref
            return {"result": "serial-lane-complete", "plan_sha256": selected_plan.sha256}

    def fake_factory(cls, **kwargs):
        assert kwargs["store"] is daemon.store
        assert kwargs["authority"] is daemon.project_authority
        assert kwargs["trusted_profile_path"] == profile_path
        return _FakeExecutor()

    monkeypatch.setattr(WindowsHostChangeExecutor, "from_trusted_profile_path", classmethod(fake_factory))
    entered, release = threading.Event(), threading.Event()

    def queue_blocker():
        entered.set()
        assert release.wait(3)

    blocker = daemon.queue.submit(queue_blocker)
    assert entered.wait(2)
    future = daemon._submit_internal_host_control(request)
    assert isinstance(future, Future)
    assert not future.done()
    release.set()
    blocker.result(timeout=2)
    assert future.result(timeout=2) == {"result": "serial-lane-complete", "plan_sha256": plan.sha256}
    with pytest.raises(WindowsScmError) as replay:
        daemon._submit_internal_host_control(request)
    assert replay.value.code == "HOST_CONTROL_REQUEST_REPLAYED"

    composed = daemon._submit_scm_host_control(
        runtime_ref["runtime"], phase="APPLY", plan=plan,
        authorization_ref=authorization_ref)
    assert composed.result(timeout=2) == {"result": "serial-lane-complete", "plan_sha256": plan.sha256}

    assert api.handler(SERVICE_CONTROL_STOP, 0, None, None) == ERROR_SUCCESS
    allow_return.set()
    broker_thread.join(3)
    assert not broker_thread.is_alive()
    with pytest.raises(WindowsScmError) as stopped:
        daemon._submit_scm_host_control(
            runtime_ref["runtime"], phase="APPLY", plan=plan,
            authorization_ref=authorization_ref)
    assert stopped.value.code == "HOST_CONTROL_SCM_RUNTIME_REQUIRED"
    try:
        forged = object()
        with pytest.raises(WindowsScmError) as fake:
            daemon._submit_internal_host_control(forged)
        assert fake.value.code == "HOST_CONTROL_INTERNAL_REQUEST_REQUIRED"
    finally:
        daemon.close()



def test_production_scm_entrypoint_composes_actual_serve_and_drains_on_stop(tmp_path, monkeypatch):
    from tests._scm_publisher_software_fixture import scm_publisher_chain
    with scm_publisher_chain(monkeypatch, tmp_path) as s:
        errors = []
        def run():
            try:s.cd.main(s.main_args())
            except BaseException as exc:errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        assert s.entered.wait(2), errors
        assert s.scm.status == [SERVICE_START_PENDING, SERVICE_RUNNING]
        assert s.scm.handler(SERVICE_CONTROL_STOP, 0, None, None) == ERROR_SUCCESS
        thread.join(4)
        assert not thread.is_alive() and errors == []
        assert s.events == ['daemon-created', 'listener-bound', 'trusted-issuer',
                            'listener-shutdown', 'listener-close', 'drained-close']
        assert s.status_rows[-1].current_state == SERVICE_STOPPED
        assert s.daemons[0]._close_complete and s.close_calls == 1
        assert s.constructor_calls == s.servers[0].closed_count == 1
        assert len(s.runtimes) == 1 and s.daemons[0]._windows_t037_scm_runtime is s.runtime
        assert s.model.events.count('rename') == s.model.events.count('delete') == 1
        assert s.owners[0]._stream.closed


def test_production_scm_entrypoint_denies_profile_mismatch_before_daemon_or_dispatch(tmp_path, monkeypatch):
    from comsol_mcp import _control_daemon as control
    from comsol_mcp import _windows_t037 as t037
    from tests.test_windows_host_change import _profile
    profile = t037.WindowsT037ProfileV2(
        host_resource_profile=_profile(), source_sha256='e' * 64,
        source_identity={'device': 1, 'inode': 2}, security_semantics='SOFTWARE_FIXTURE_ONLY')
    monkeypatch.setattr(t037, 'load_trusted_profile', lambda _path: profile)
    def forbidden(*_args, **_kwargs):
        pytest.fail('must refuse before daemon or SCM dispatch side effects')
    monkeypatch.setattr(control, 'ControlDaemon', forbidden)
    monkeypatch.setattr('comsol_mcp._windows_t037_broker.WindowsScmServiceBroker', forbidden)
    with pytest.raises(WindowsScmError) as mismatch:
        control.serve_windows_scm_service(tmp_path/'home', service_name='OtherService',
                                         windows_t037_profile='pinned-profile')
    assert mismatch.value.code == 'SCM_PROFILE_SERVICE_MISMATCH'
    with pytest.raises(WindowsScmError) as missing:
        control.serve_windows_scm_service(tmp_path/'home', service_name='ComsolHost',
                                         windows_t037_profile=None)
    assert missing.value.code == 'SCM_PROFILE_REQUIRED'



def test_production_scm_listener_init_failure_closes_daemon_without_running(tmp_path, monkeypatch):
    from tests._scm_publisher_software_fixture import scm_publisher_chain
    from comsol_mcp import _windows_scm_publisher as publisher
    from comsol_mcp import _windows_scm_publisher_native as native
    from comsol_mcp._execution_contract import ExecutionContractError
    with scm_publisher_chain(monkeypatch, tmp_path) as s:
        def fail_after_constructor_entered(server):
            assert server is s.servers[0] and server.socket.fileno() >= 0
            raise OSError('synthetic listener creation return lost after original entry')
        s.on_init = fail_after_constructor_entered
        with pytest.raises(WindowsScmError) as failed:
            s.cd.main(s.main_args())
        assert failed.value.code == 'SCM_SERVICE_RUNTIME_FAILED'
        assert s.scm.status == [SERVICE_START_PENDING, SERVICE_STOPPED]
        assert s.events == ['daemon-created', 'listener-bound', 'drained-close']
        assert s.daemons[0]._close_complete and s.close_calls == 1
        owner = s.owners[0]
        assert publisher._ROOTS[id(owner)] is owner
        assert owner._held_unknown and owner._server is s.servers[0]
        assert owner._server_created is None and owner._namespace._borrowed[0] is owner._stream
        assert not owner._stream.closed and s.constructor_calls == 1
        assert s.servers[0].closed_count == s.servers[0].shutdowns == 0
        assert not {'rename', 'write', 'delete'} & set(s.model.events)
        before = (list(s.sdk.calls), list(s.sdk.native_effects), list(s.model.events))
        with pytest.raises(native.PublisherError):owner.listener(object)
        with pytest.raises(native.PublisherError):owner.finish_after_daemon()
        assert (s.sdk.calls, s.sdk.native_effects, s.model.events) == before
        assert s.constructor_calls == 1 and not owner._stream.closed
        # ENTERED is UNKNOWN: keep the original listener/lock, no invented release.
        with pytest.raises(ExecutionContractError) as busy:
            s.cd.ProcessLock(s.home/'control.lock')
        assert busy.value.code == 'ENGINE_BUSY'
        assert publisher._ROOTS[id(owner)] is owner and not owner._stream.closed


def test_production_scm_listener_preconstructor_binding_failure_releases_original_lock(tmp_path, monkeypatch):
    from tests._scm_publisher_software_fixture import scm_publisher_chain
    from comsol_mcp import _windows_scm_publisher as publisher
    from comsol_mcp import _windows_scm_publisher_native as native
    with scm_publisher_chain(monkeypatch, tmp_path) as s:
        # Original binding validation rejects BEFORE __new__ / _ServerInit entry.
        monkeypatch.setattr(publisher, '_ServerInit', lambda *args: pytest.fail('constructor entered'))
        with pytest.raises(WindowsScmError) as failed:s.cd.main(s.main_args())
        assert failed.value.code == 'SCM_SERVICE_RUNTIME_FAILED'
        assert s.scm.status == [SERVICE_START_PENDING, SERVICE_STOPPED]
        assert s.events == ['daemon-created', 'drained-close']
        assert s.constructor_calls == 0 and s.servers == [] and s.close_calls == 1
        owner = s.owners[0]
        assert owner._server is None and not owner._held_unknown
        assert owner._namespace.owner.retired and owner._stream.closed
        assert not publisher._ROOTS and not native._OWNERS
        assert s.sdk.native_effects and not s.model.active
        assert not {'rename', 'write', 'delete'} & set(s.model.events)
        # Known no-effect original cleanup, observed by real singleton reacquisition.
        lock = s.cd.ProcessLock(s.home/'control.lock')
        lock.close()
        assert lock.handle.closed


@pytest.mark.parametrize('bootstrap_state',['pending','failed','late_failed','still_pending'])

def test_service_bootstrap_observation_retains_pending_handle_on_stop_and_reports_failures(
        tmp_path,monkeypatch,bootstrap_state):
    from tests._scm_publisher_software_fixture import scm_publisher_chain, bootstrap_group_plan
    from comsol_mcp import _windows_t037_bootstrap as bootstrap
    from tests.test_windows_t037_bootstrap import _raw
    with scm_publisher_chain(monkeypatch,tmp_path) as s:
        # Complete, source-bound intent replaces the old single-filter fixture;
        # only the explicit submit seam below returns a synthetic accepted Future.
        profile=s.profile;plan=bootstrap_group_plan(profile)
        document={'schema':bootstrap.SCHEMA,'service_name':profile.service_name,
                  'profile_source_sha256':profile.source_sha256,'host_profile_sha256':profile.sha256,
                  'project_id':profile.host_resource_profile.project_id,'phase':'APPLY',
                  'authorization_ref':'apply-once','plan':{'payload_json':plan.payload_json,'sha256':plan.sha256},
                  'receipt':None}
        assert bootstrap.parse_bootstrap(_raw(document),profile).plan==plan
        def read_request(path):
            assert path=='explicit-request'
            return _raw(document)
        monkeypatch.setattr(bootstrap,'read_private_bootstrap',read_request)
        future=Future();closed=[];errors=[];submissions=[]
        if bootstrap_state=='failed':future.set_exception(RuntimeError('synthetic host bootstrap failure'))
        # Deliberately synthetic accepted handle at ONLY this existing submit seam.
        # The original issuer/provider/runtime validation above still executes.
        def submit(daemon,runtime,**request):
            assert daemon is s.daemons[0] and runtime is s.runtime and runtime.is_active
            assert request==dict(phase='APPLY',plan=plan,authorization_ref='apply-once',receipt=None)
            assert daemon._windows_t037_scm_runtime is runtime
            submissions.append(request)
            return future
        monkeypatch.setattr(s.cd.ControlDaemon,'_submit_scm_host_control',submit)
        def before_original_close(daemon):
            assert daemon._scm_bootstrap_future is future and not future.cancelled()
            if bootstrap_state in {'pending','late_failed','still_pending'}:
                assert not future.done() # STOP observation has not inferred/canceled the effect.
                if bootstrap_state=='pending':future.set_result('software-drained-terminal')
                elif bootstrap_state=='late_failed':future.set_exception(RuntimeError('late drain failure'))
            closed.append(True)
        s.on_close=before_original_close
        def run():
            try:s.cd.main(s.main_args('--windows-scm-bootstrap-request','explicit-request'))
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start()
        assert s.entered.wait(2),errors
        if bootstrap_state!='failed':assert s.scm.handler(SERVICE_CONTROL_STOP,0,None,None)==0
        thread.join(4)
        assert not thread.is_alive() and closed==[True] and s.close_calls==1
        assert s.daemons[0]._close_complete and s.daemons[0]._scm_bootstrap_future is future
        assert len(submissions)==1 and not future.cancelled()
        if bootstrap_state!='pending':
            assert len(errors)==1 and errors[0].code=='SCM_SERVICE_RUNTIME_FAILED'
            assert s.status_rows[-1].service_specific_exit_code==1
        else:assert errors==[]
        assert s.status_rows[-1].current_state==SERVICE_STOPPED
        assert s.constructor_calls==s.servers[0].closed_count==1 and s.servers[0].shutdowns==1
        assert s.model.events.count('rename')==s.model.events.count('delete')==1
        assert s.owners[0]._stream.closed
