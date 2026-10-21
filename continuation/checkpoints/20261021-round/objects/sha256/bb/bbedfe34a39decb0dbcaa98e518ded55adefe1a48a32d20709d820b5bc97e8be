"""Adversarial tests of the actual task holder; no JVM/COMSOL/endpoints."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from uuid import uuid4
import pytest
from tools.w23_original_owner_hold import W23OriginalOwnerHold, REQUEST_NAME, ONCE_NAME


class Child:
    def __init__(self, pid, signals):
        self.pid, self.code, self.signals = pid, None, signals
        self.poll_error = False
    def poll(self):
        if self.poll_error: raise OSError("poll diagnostic failed")
        return self.code
    def terminate(self):
        self.signals.append(self.pid); self.code = -15
    def wait(self, timeout):
        assert timeout <= 15
        if self.code is None: raise subprocess.TimeoutExpired("original", timeout)
        return self.code


class Worker:
    def __init__(self, child):
        self._process = child
        self.calls = []
    def _request(self, *args, **kwargs): self.calls.append("request")
    def start(self, *args, **kwargs): self.calls.append("start")
    def close(self): self.calls.append("close")


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True))


@pytest.fixture
def holder():
    with tempfile.TemporaryDirectory(prefix="w23-owned-holder-", dir="/private/tmp") as directory:
        root = Path(directory);root.chmod(0o700)
        evidence=root/"evidence";evidence.mkdir(mode=0o700)
        signals=[]; server_child=Child(31001, signals); worker_child=Child(31002, signals)
        worker=Worker(worker_child)
        birth=lambda p:{"pid":p.pid,"birth":"owned-created-birth-"+str(p.pid),"command_sha256":"a"*64}
        server=SimpleNamespace(proc=server_child,worker=worker,process_identity=birth(server_child))
        backend=SimpleNamespace(worker=worker,service=object(),worker_identity={"worker_instance_id":"w","connection_epoch":2})
        daemon=SimpleNamespace(backend=backend,worker=worker,_admission_lock=threading.RLock(),closed=threading.Event(),dispatch=lambda *a:None)
        owner=W23OriginalOwnerHold(binding={"namespace":str(root),"activity_id":str(uuid4()),"binding_sha256":"b"*64},
            freeze_sha256="c"*64,source_sha256="d"*64,evidence=evidence,writer=write)
        owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(birth(worker_child))
        owner.bind_daemon(daemon);owner.capture_attach();owner.summary={"error":"original model_load failure","unknown":{"status":"UNKNOWN","safe_retry":False}}
        original=RuntimeError("original failure");owner.cause(original);owner.deadline_epoch=0;owner.fence({"status":"LEFT_RUNNING_ACTIVE_OR_UNKNOWN"})
        yield root,owner,server,worker,daemon,signals,original
        if owner._root_fd is not None: os.close(owner._root_fd)


def request(owner):
    return {**owner._proof(),"request_id":str(uuid4()),"authorization":{"kind":"DIRECT_HUMAN_EXCEPTION_STOP","authorization_ref":"SOFTWARE_FIXTURE_AUTHORIZED_HARMLESS_CHILDREN_ONLY"}}


def publish(root, body):
    path=root/REQUEST_NAME
    path.write_text(json.dumps(body));path.chmod(0o600)
    return path


@pytest.mark.parametrize("fault",["uuid","nonce","freeze","source","runtime","server_birth","worker_birth","float_pid","attached_epoch","extra","authorization_extra","authorization_kind","duplicate","malformed","symlink","hardlink","mode","once"])
def test_bad_request_cannot_signal_or_rewrite_unknown(holder,fault):
    root,owner,server,worker,daemon,signals,original=holder
    body=request(owner)
    if fault in {"uuid","nonce","freeze","source","runtime"}:
        key={"uuid":"activity_uuid","nonce":"owner_nonce","freeze":"freeze_sha256","source":"source_manifest_sha256","runtime":"runtime_binding_sha256"}[fault];body[key]="wrong"
    elif fault in {"server_birth","worker_birth"}:body["processes"][fault.split("_")[0]]["birth"]="other"
    elif fault=="float_pid":body["processes"]["worker"]["pid"]=float(worker._process.pid)
    elif fault=="attached_epoch":body["attached_runtime_identity"]["connection_epoch"]=3
    elif fault=="extra":body["caller_trusted"] = True
    elif fault=="authorization_extra":body["authorization"]["extra"] = True
    elif fault=="authorization_kind":body["authorization"]["kind"]="NORMAL_CLEANUP"
    path=publish(root,body)
    if fault=="duplicate":path.write_text('{"schema":"x","schema":"x"}')
    elif fault=="malformed":path.write_text('{')
    elif fault=="symlink":path.rename(root/"target");path.symlink_to(root/"target")
    elif fault=="hardlink":os.link(path,root/"alias")
    elif fault=="mode":path.chmod(0o644)
    elif fault=="once":(root/ONCE_NAME).write_text("already consumed")
    owner.consume_stop_request()
    assert signals==[]
    assert owner.first_cause is original
    assert owner.summary["unknown"]=={"status":"UNKNOWN","safe_retry":False}


@pytest.mark.parametrize("fault",["server_popen","worker_popen","worker_object","backend_object","backend_worker","service","runtime","coordinated_runtime"])
def test_original_object_graph_swaps_never_adopt_new_handles(holder,fault):
    root,owner,server,worker,daemon,signals,original=holder
    body=request(owner);outsider=Child(39999,signals)
    if fault=="server_popen":server.proc=outsider
    elif fault=="worker_popen":worker._process=outsider
    elif fault=="worker_object":server.worker=Worker(outsider)
    elif fault=="backend_object":daemon.backend=copy.copy(daemon.backend)
    elif fault=="backend_worker":daemon.backend.worker=Worker(outsider)
    elif fault=="service":daemon.backend.service=object()
    elif fault=="runtime":daemon.backend.worker_identity["connection_epoch"]=3
    elif fault=="coordinated_runtime":
        daemon.backend.worker_identity["connection_epoch"]=3;owner._runtime_identity["connection_epoch"]=3
    publish(root,body);owner.consume_stop_request()
    assert signals==[] and owner._server_process.pid==31001 and owner._worker_process.pid==31002
    assert owner.first_cause is original


def test_poll_write_failure_keeps_original_firstcause_objects_and_budget(holder):
    root,owner,server,worker,daemon,signals,original=holder
    worker._process.poll_error=True
    owner.writer=lambda *a:(_ for _ in ()).throw(OSError("durable diagnostic write failed"))
    owner._last_write_epoch=None  # Exercise the next durable write, beyond entry throttle.
    observed=owner.observe();owner.persist(observed)
    assert not owner.terminal(observed) and owner._worker_process is worker._process
    assert owner.first_cause is original and signals==[]
    assert owner.summary["owner_hold"]["native_lifetime_within_budget"]=="UNRESOLVED_WHILE_ORIGINAL_CHILDREN_ALIVE"
    assert owner.summary["birth_inclusive_budget"]["finished_epoch_includes_cleanup"] is None
    assert len(owner.diagnostic_errors)>=2


def test_natural_exit_returns_fail_without_sdk_or_signals(holder):
    root,owner,server,worker,daemon,signals,original=holder
    def exit_both(_):server.proc.code=0;worker._process.code=0
    owner.sleeper=exit_both
    result=owner.run()
    assert result["status"].startswith("FAIL") and signals==[] and owner.first_cause is original
    assert result["unknown"]["status"]=="UNKNOWN"
    assert result["standard_exact_cleanup"]=="UNVERIFIED"
    for call in (worker._request,worker.start,worker.close,daemon.dispatch):
        with pytest.raises(RuntimeError,match="FENCED"):call()
    assert worker.calls==[] and daemon.closed.is_set()


def test_authorized_stop_is_once_worker_then_server_original_popen_only(holder):
    root,owner,server,worker,daemon,signals,original=holder
    publish(root,request(owner));owner.consume_stop_request();owner.consume_stop_request()
    assert signals==[31002,31001] and owner.consumed
    assert (root/ONCE_NAME).is_file() and (root/ONCE_NAME).stat().st_mode&0o777==0o600
    assert owner.first_cause is original and owner.summary["unknown"]["status"]=="UNKNOWN"


def test_request_does_not_follow_replaced_original_namespace(holder):
    import shutil
    root,owner,server,worker,daemon,signals,original=holder
    body=request(owner);moved=root.with_name(root.name+"-original")
    root.rename(moved);root.mkdir(mode=0o700)
    try:
        publish(root,body);owner.consume_stop_request()
        assert signals==[] and not owner.consumed
    finally:
        shutil.rmtree(moved)


def test_pid_field_swap_and_mutable_authority_cannot_replace_original(holder):
    root,owner,server,worker,daemon,signals,original=holder
    body=request(owner)
    owner.freeze_sha256="e"*64;owner.binding["activity_id"]=str(uuid4())
    body["freeze_sha256"]=owner.freeze_sha256;body["activity_uuid"]=owner.binding["activity_id"]
    publish(root,body);owner.consume_stop_request()
    assert signals==[]
    worker._process.pid=39999
    publish(root,request(owner) if owner._graph_matches() else body);owner.consume_stop_request()
    assert signals==[] and owner._original_pids["worker"]==31002


def test_authorized_timeout_no_sigkill_or_repeat_and_alive_handle_retained(holder):
    root,owner,server,worker,daemon,signals,original=holder
    worker._process.terminate=lambda:signals.append(31002)
    publish(root,request(owner));owner.consume_stop_request();owner.consume_stop_request()
    assert signals==[31002,31001] and owner.observe()["worker"]["state"]=="ALIVE"
    assert owner._worker_process is worker._process and owner.first_cause is original
    assert owner.signal_attempts==["worker","server"]


def test_once_marker_fsync_failure_never_signals_or_replays(holder,monkeypatch):
    root,owner,server,worker,daemon,signals,original=holder
    publish(root,request(owner))
    monkeypatch.setattr(os,"fsync",lambda *a:(_ for _ in ()).throw(OSError("fsync failed")))
    owner.consume_stop_request();owner.consume_stop_request()
    assert owner.consumed and signals==[] and owner.first_cause is original


def test_untyped_poll_does_not_masquerade_as_exit(holder):
    root,owner,server,worker,daemon,signals,original=holder
    worker._process.poll=lambda:"UNKNOWN"
    observed=owner.observe()
    assert observed["worker"]["state"]=="OBSERVATION_UNKNOWN" and not owner.terminal(observed)


def test_actual_control_daemon_monitor_fenced_and_durable_unknown_unchanged(tmp_path):
    import time
    from tests.test_g3_model_delivery import _environment
    from tests.test_artifact_preview import _store_snapshot
    with tempfile.TemporaryDirectory(prefix="w23-real-monitor-",dir="/private/tmp") as directory:
        root=Path(directory);root.chmod(0o700);evidence=root/"evidence";evidence.mkdir(mode=0o700)
        daemon,worker,service,project,workspace,ref,install=_environment(tmp_path)
        signals=[];server_child=Child(32001,signals);worker_child=Child(32002,signals)
        calls=[];worker._process=worker_child;worker._request=lambda *a,**k:calls.append("rpc") or {}
        worker.health=lambda **k:worker._request({"type":"health"},timeout_s=0.2)
        worker.close=lambda:calls.append("close")
        identity=lambda p:{"pid":p.pid,"birth":"software-owned-"+str(p.pid),"command_sha256":"f"*64}
        server=SimpleNamespace(proc=server_child,worker=worker,process_identity=identity(server_child))
        owner=W23OriginalOwnerHold(binding={"namespace":str(root),"activity_id":str(uuid4()),"binding_sha256":"b"*64},freeze_sha256="c"*64,source_sha256="d"*64,evidence=evidence,writer=write)
        try:
            owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(identity(worker_child));owner.bind_daemon(daemon);owner.capture_attach()
            record,_=daemon.store.begin(request_id="synthetic-unknown",idempotency_key="synthetic-unknown",request_hash="synthetic",operation="model.load")
            daemon.store.finish(record["operation_id"],status="UNKNOWN",result={"success":False,"error":{"code":"EXECUTION_STATE_UNKNOWN","safe_retry":False}})
            owner.fence({"status":"LEFT_RUNNING_ACTIVE_OR_UNKNOWN"})
            daemon.monitor.join(timeout=1)
            assert not daemon.monitor.is_alive()
            baseline=_store_snapshot(daemon.store);count=len(calls)
            owner.persist(owner.observe());time.sleep(0.1)
            assert len(calls)==count and _store_snapshot(daemon.store)==baseline
            assert daemon.store.job(record["job_id"])["status"]=="UNKNOWN"
            with pytest.raises(RuntimeError,match="FENCED"):daemon.dispatch({"operation":"model.inspect"})
            assert signals==[]
        finally:
            server_child.code=0;worker_child.code=0
            daemon.close()
            if owner._root_fd is not None:os.close(owner._root_fd)


def test_real_harmless_python_children_stop_by_original_popen_only(tmp_path):
    import psutil
    with tempfile.TemporaryDirectory(prefix="w23-holder-real-",dir="/private/tmp") as directory:
        root=Path(directory);root.chmod(0o700);evidence=root/"evidence";evidence.mkdir(mode=0o700)
        children=[subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]) for _ in range(2)]
        try:
            worker=Worker(children[1])
            def birth(p):
                actual=psutil.Process(p.pid)
                assert actual.ppid()==os.getpid() and actual.uids().real==os.getuid()
                return {"pid":p.pid,"birth":"observed-start-epoch:"+str(actual.create_time()),
                        "command_sha256":hashlib.sha256(" ".join(actual.cmdline()).encode()).hexdigest()}
            server=SimpleNamespace(proc=children[0],worker=worker,process_identity=birth(children[0]))
            owner=W23OriginalOwnerHold(binding={"namespace":str(root),"activity_id":str(uuid4()),"binding_sha256":"b"*64},freeze_sha256="c"*64,source_sha256="d"*64,evidence=evidence,writer=write)
            owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(birth(children[1]));owner.summary={"unknown":{"status":"UNKNOWN","safe_retry":False}}
            domain=root/"original-unknown-domain.json";domain.write_text('{"status":"UNKNOWN","safe_retry":false}')
            before=hashlib.sha256(domain.read_bytes()).hexdigest()
            owner.fence();publish(root,request(owner));result=owner.run()
            assert all(p.poll() is not None for p in children)
            assert owner.signal_attempts==["worker","server"] and owner._server_process is children[0] and owner._worker_process is children[1]
            assert result["unknown"]=={"status":"UNKNOWN","safe_retry":False} and result["standard_exact_cleanup"]=="UNVERIFIED"
            assert hashlib.sha256(domain.read_bytes()).hexdigest()==before
        finally:
            for child in children:
                if child.poll() is None:child.terminate()
                child.wait(timeout=15)
            if 'owner' in locals() and owner._root_fd is not None:os.close(owner._root_fd)


def test_runner_wrapper_retains_owner_when_cleanup_diagnostic_raises(holder,monkeypatch):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    root,prepared,server,worker,daemon,signals,original=holder
    original_request=worker._request
    # Use new original child graph for actual production wrapper, not the already fenced fixture.
    worker=Worker(prepared._worker_process);server.worker=worker;daemon.worker=worker;daemon.backend.worker=worker
    def body(*args):
        owner=args[-1];owner.summary={"error":"original native call failure","unknown":{"status":"UNKNOWN","safe_retry":False}}
        owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(prepared._births["worker"]);owner.bind_daemon(daemon);owner.capture_attach();owner.cause(original)
        def exit_both(_):server.proc.code=0;worker._process.code=0
        owner.sleeper=exit_both
        raise OSError("cleanup diagnostic failed before receipt write")
    monkeypatch.setattr(runner,"_execute_native_campaign_body",body)
    result=runner._execute_native_campaign({},root/"freeze","c"*64,{},root/"model",root/"work",root/"evidence",{"fresh_runtime_binding":prepared.binding,"source_manifest_sha256":"d"*64})
    assert result["owner_hold"]["first_cause"]["error"]=="original failure" and signals==[]
    assert result["unknown"]["status"]=="UNKNOWN" and result["status"].startswith("FAIL")


def test_single_production_attach_transition_captures_new_service(holder):
    root,held,server,worker,daemon,signals,original=holder
    owner=W23OriginalOwnerHold(binding=held.binding,freeze_sha256="c"*64,source_sha256="d"*64,evidence=root/"evidence",writer=write)
    worker=Worker(held._worker_process);server.worker=worker;daemon.worker=worker;daemon.backend.worker=worker
    daemon.backend.service=None;daemon.backend.worker_identity=None
    try:
        owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(held._births["worker"]);owner.bind_daemon(daemon)
        assert owner._graph_matches()
        service=object();daemon.backend.service=service;daemon.backend.worker_identity={"worker_instance_id":"attached","connection_epoch":3}
        owner.capture_attach();assert owner._graph_matches() and owner._service is service
        daemon.backend.service=object()
        with pytest.raises(RuntimeError,match="cannot be replaced"):owner.capture_attach()
        assert not owner._graph_matches()
    finally:
        os.close(owner._root_fd)


def test_harmless_worker_birth_handle_retained_before_worker_diagnostics(holder):
    root,held,server,worker,daemon,signals,original=holder
    owner=W23OriginalOwnerHold(binding=held.binding,freeze_sha256="c"*64,source_sha256="d"*64,evidence=root/"evidence",writer=write)
    child=None
    try:
        worker=Worker(None);owner.bind_worker(worker)
        child=owner.worker_process_factory([sys.executable,"-c","import time; time.sleep(60)"])
        assert owner._worker_process is child and worker._process is None
        owner.cause(OSError("Worker diagnostics failed before assigning process"));owner.fence()
        assert owner.observe()["worker"]["state"]=="ALIVE"
        with pytest.raises(RuntimeError,match="cannot replay"):owner.worker_process_factory([sys.executable,"-c","pass"])
        assert owner._worker_process is child and owner.first_cause is not None
    finally:
        if child is not None:
            if child.poll() is None:child.terminate()
            child.wait(timeout=15)
        os.close(owner._root_fd)


@pytest.mark.parametrize("fault",["clock","fence_once","fence_always","receipt_always","proof_interrupt","sleep_interrupt"])
def test_actual_original_children_retained_across_secondary_diagnostics(tmp_path,monkeypatch,fault):
    import time
    with tempfile.TemporaryDirectory(prefix="w23-secondary-owner-",dir="/private/tmp") as directory:
        root=Path(directory);root.chmod(0o700);evidence=root/"evidence";evidence.mkdir(mode=0o700)
        children=[subprocess.Popen([sys.executable,"-c","import time;time.sleep(.8)"]) for _ in range(2)]
        owner=W23OriginalOwnerHold(binding={"namespace":str(root),"activity_id":str(uuid4()),"binding_sha256":"b"*64},freeze_sha256="c"*64,source_sha256="d"*64,evidence=evidence,writer=write)
        worker=Worker(children[1]);server=SimpleNamespace(proc=children[0],worker=worker,process_identity=None)
        backend=SimpleNamespace(worker=worker,service=object(),worker_identity={"worker_instance_id":"software","connection_epoch":1})
        daemon=SimpleNamespace(backend=backend,worker=worker,closed=threading.Event(),dispatch=lambda _:None)
        owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker();owner.bind_daemon(daemon);owner.capture_attach()
        owner.summary={"error":"first original native failure","unknown":{"status":"UNKNOWN","safe_retry":False}}
        original=RuntimeError("first original failure");owner.cause(original)
        attempts=[];results=[]
        if fault=="clock":owner.clock=lambda:(_ for _ in ()).throw(OSError("persistent clock diagnostic"))
        elif fault.startswith("fence"):
            actual=owner.fence
            def fail():
                attempts.append("fence")
                if fault=="fence_always" or len(attempts)==1:raise OSError("secondary fence diagnostic")
                return actual()
            monkeypatch.setattr(owner,"fence",fail)
        elif fault=="receipt_always":monkeypatch.setattr(owner,"persist",lambda _:(_ for _ in ()).throw(OSError("persistent receipt diagnostic")))
        elif fault=="proof_interrupt":monkeypatch.setattr(owner,"_build_proof",lambda:(_ for _ in ()).throw(KeyboardInterrupt("proof diagnostic")))
        elif fault=="sleep_interrupt":owner.sleeper=lambda _:(_ for _ in ()).throw(KeyboardInterrupt("sleep diagnostic"))
        thread=threading.Thread(target=lambda:results.append(owner.run()),daemon=True);thread.start()
        try:
            time.sleep(.12)
            assert thread.is_alive() and all(p.poll() is None for p in children)
            assert owner._server_process is children[0] and owner._worker_process is children[1]
            assert owner.fenced and daemon.closed.is_set() and owner.first_cause is original
            with pytest.raises(RuntimeError,match="FENCED"):worker._request({})
            assert worker.calls==[] and owner.signal_attempts==[]
            for p in children:p.wait(timeout=3) # Natural sleep completion; no signal.
            thread.join(timeout=3)
            assert not thread.is_alive() and len(results)==1 and results[0]["status"].startswith("FAIL")
            assert results[0]["unknown"]=={"status":"UNKNOWN","safe_retry":False}
            assert owner.first_cause is original and owner.signal_attempts==[] and owner.diagnostic_errors
        finally:
            for p in children:
                if p.poll() is None:p.terminate()
                p.wait(timeout=15)
            thread.join(timeout=3)
            if owner._root_fd is not None:os.close(owner._root_fd)


def test_runner_terminal_clock_failure_returns_fail_with_unknown_unchanged(holder,monkeypatch):
    from tools import run_native_w23_saved_mph_diagnostic as runner
    root,prepared,server,worker,daemon,signals,original=holder
    worker=Worker(prepared._worker_process);server.worker=worker;daemon.worker=worker;daemon.backend.worker=worker
    def body(*args):
        owner=args[-1];owner.summary={"error":"first native failure","unknown":{"status":"UNKNOWN","safe_retry":False},"cleanup_status":"CLEANUP_UNVERIFIED","birth_inclusive_budget":{"birth_epoch":1}}
        owner.bind_server(server);owner.capture_server();owner.bind_worker(worker);owner.capture_worker(prepared._births["worker"]);owner.bind_daemon(daemon);owner.capture_attach();owner.cause(original)
        server.proc.code=0;worker._process.code=0
        return owner.summary
    monkeypatch.setattr(runner,"_execute_native_campaign_body",body)
    monkeypatch.setattr(runner.time,"time",lambda:(_ for _ in ()).throw(OSError("terminal clock diagnostic")))
    result=runner._execute_native_campaign({},root/"freeze","c"*64,{},root/"model",root/"work",root/"evidence",{"fresh_runtime_binding":prepared.binding,"source_manifest_sha256":"d"*64})
    assert result["status"].startswith("FAIL") and result["unknown"]=={"status":"UNKNOWN","safe_retry":False}
    assert result["owner_hold"]["first_cause"]["error"]=="original failure" and signals==[]
    assert result["terminal_time_diagnostic"]=="UNAVAILABLE_NOT_NATIVE_ACCEPTANCE"
