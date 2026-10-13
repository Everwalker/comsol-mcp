"""Actual P0 owner class / production daemon containers, no native endpoints."""
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from p0_original_controller_hold import P0OriginalControllerOwner, publish_hold, retain_original_controller, REQUEST
from p0_owned_control_entry import load_config
from comsol_mcp._control_daemon import ControlDaemon


def write(path,value):path.write_text(json.dumps(value,sort_keys=True))


class Worker:
    def __init__(self,process):self._process=process;self.calls=[]
    def _request(self,*a,**k):self.calls.append("request");return {}
    def health(self,**k):return self._request()
    def start(self):self.calls.append("start")
    def close(self):self.calls.append("close")


@pytest.fixture
def owned(tmp_path):
    with tempfile.TemporaryDirectory(prefix="p0-original-owner-",dir="/private/tmp") as directory:
        root=Path(directory);root.chmod(0o700);(root/"evidence").mkdir(mode=0o700)
        binding={"activity_id":root.name,"namespace":str(root),"source_sha256":"a"*64,"harness_sha256":"b"*64,"entry_sha256":"c"*64,"holder_sha256":"d"*64,"owner_nonce":str(uuid4())}
        (tmp_path/"projects").mkdir(mode=0o700)
        daemon=ControlDaemon(tmp_path/"controller",project_root=tmp_path/"projects")
        owner=P0OriginalControllerOwner(root,binding,write);owner.bind(daemon)
        yield root,binding,daemon,owner
        daemon.closed.set();daemon.monitor.join(timeout=1)
        # Tests with fake owner containers must not ask production close to retire them.
        daemon._session_worker_handles.clear();daemon._session_server_handles.clear();daemon._session_backends.clear()
        daemon._default_backend.worker=None
        daemon.close();os.close(owner.fd)


@pytest.mark.parametrize("fault",["source","nonce","activity","extra","binding_extra","wrongaction","wrongtype","duplicate","malformed","symlink","hardlink","mode","nonfinite"])
def test_closed_private_hold_refuses_bad_notifications(owned,fault):
    root,binding,daemon,owner=owned
    altered=copy.deepcopy(binding)
    if fault in {"source","nonce","activity"}:altered[{"source":"source_sha256","nonce":"owner_nonce","activity":"activity_id"}[fault]]="wrong"
    if fault=="binding_extra":altered["extra"]=True
    publish_hold(root,altered,RuntimeError("original"));path=root/REQUEST;body=json.loads(path.read_text())
    if fault=="extra":body["extra"]=True
    if fault=="wrongaction":body["action"]="STOP"
    if fault=="wrongtype":body["firstcause"]["error"]=3
    path.write_text(json.dumps(body))
    if fault=="duplicate":path.write_text('{"schema":"x","schema":"x"}')
    if fault=="malformed":path.write_text('{')
    if fault=="nonfinite":path.write_text('{"value":NaN}')
    if fault=="symlink":path.rename(root/"target");path.symlink_to(root/"target")
    if fault=="hardlink":os.link(path,root/"alias")
    if fault=="mode":path.chmod(0o644)
    owner.request();assert not owner.fenced and not owner.consumed and not daemon.closed.is_set()


def test_real_registered_containers_original_python_children_hold_unknown_unchanged(owned):
    root,binding,daemon,owner=owned
    children=[subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]) for _ in range(2)]
    try:
        worker=Worker(children[1]);backend=SimpleNamespace(worker=worker,service=object(),worker_identity={"worker_instance_id":"actual-software","connection_epoch":1})
        key=("owned-project","owned-session")
        daemon._session_server_handles[key]=SimpleNamespace(process=children[0])
        daemon._session_worker_handles[key]=worker;daemon._session_backends[key]=backend
        daemon._default_backend.worker=worker
        record,_=daemon.store.begin(request_id="unknown",idempotency_key="unknown",request_hash="unknown",operation="model.load")
        daemon.store.finish(record["operation_id"],status="UNKNOWN",result={"success":False,"error":{"code":"EXECUTION_STATE_UNKNOWN","safe_retry":False}})
        before=daemon.store.job(record["job_id"]);owner.capture()
        cause=RuntimeError("original failure");owner.cause(cause);publish_hold(root,binding,RuntimeError("secondary"));owner.request()
        daemon.monitor.join(timeout=1);assert not daemon.monitor.is_alive()
        calls=len(worker.calls);time.sleep(.15);assert len(worker.calls)==calls
        for action in (worker._request,worker.start,worker.close):
            with pytest.raises(RuntimeError,match="FENCED"):action()
        with pytest.raises(RuntimeError,match="FENCED"):daemon.dispatch({})
        assert owner.refs[("server",key)][1] is children[0] and owner.refs[("worker",key)][1] is children[1]
        assert not owner.persist() and owner.firstcause is cause and daemon.store.job(record["job_id"])==before
        assert all(p.poll() is None for p in children) # hold-only never signals
        for p in children:p.terminate();p.wait(timeout=15) # test-owned harmless cleanup only
        assert owner.persist() and daemon.store.job(record["job_id"])==before
        receipt=json.loads((root/"evidence/P0_OWNER_HOLD.json").read_text())
        assert receipt["terminal_original_children"] and not receipt["standard_cleanup_complete"]
    finally:
        for p in children:
            if p.poll() is None:p.terminate()
            p.wait(timeout=15)


@pytest.mark.parametrize("swap",["container","worker","backend","service","runtime","popen","guard","dispatch"])
def test_original_object_swap_blocks_terminal_proof(owned,swap):
    root,binding,daemon,owner=owned
    process=SimpleNamespace(pid=50001,poll=lambda:0);worker=Worker(process);key=("p","s")
    backend=SimpleNamespace(worker=worker,service=object(),worker_identity={"epoch":1})
    daemon._session_worker_handles[key]=worker;daemon._session_backends[key]=backend
    owner.capture();owner.fence()
    if swap=="container":daemon._session_worker_handles={}
    if swap=="worker":daemon._session_worker_handles[key]=Worker(process)
    if swap=="backend":daemon._session_backends[key]=SimpleNamespace(worker=worker,service=backend.service,worker_identity={"epoch":1})
    if swap=="service":backend.service=object()
    if swap=="runtime":backend.worker_identity={"epoch":2}
    if swap=="popen":worker._process=SimpleNamespace(pid=50002,poll=lambda:0)
    if swap=="guard":worker._request=lambda:None
    if swap=="dispatch":daemon.dispatch=lambda _:None
    assert not owner.persist() and owner.proof_unavailable


def test_diagnostic_poll_failure_and_partial_birth_never_releases_owner(owned):
    root,binding,daemon,owner=owned;key=("p","s")
    worker=Worker(None);daemon._session_worker_handles[key]=worker
    daemon._control_dispatch_inflight=1;owner.capture();owner.cause(RuntimeError("first"));owner.fence()
    assert not owner.persist() # admitted original birth still in flight
    process=SimpleNamespace(pid=50001,poll=lambda:(_ for _ in ()).throw(OSError("poll")))
    worker._process=process;daemon._control_dispatch_inflight=0
    owner.writer=lambda *a:(_ for _ in ()).throw(OSError("writer"));owner.last_write=None
    assert not owner.persist() and owner.refs[("worker",key)][1] is process and str(owner.firstcause)=="first"
    assert {r["stage"] for r in owner.diagnostics}>={"write","original_poll"}


def test_notification_once_does_not_replace_firstcause_or_stop(owned):
    root,binding,daemon,owner=owned;original=RuntimeError("first");owner.cause(original)
    publish_hold(root,binding,RuntimeError("second"));owner.request();owner.request()
    assert owner.firstcause is original and owner.fenced and owner.consumed
    assert not hasattr(owner,"terminate") and not hasattr(owner,"consume_stop_request")


def test_real_harmless_original_controller_client_waits_without_terminate(tmp_path):
    child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(.2)"])
    original=child
    summary={"budget":{},"failure":"first"}
    async def observe_sleep(_):await asyncio.sleep(.05)
    try:
        asyncio.run(retain_original_controller(child,summary,tmp_path/"summary",write,observe_sleep))
        assert child is original and child.returncode==0 and summary["status"]=="FAIL_ORIGINAL_CONTROLLER_HOLD"
        assert summary["failure"]=="first" and summary["controller_original_popen_retained"]
    finally:child.wait(timeout=15)


def test_client_poll_and_writer_diagnostics_preserve_exact_handle(tmp_path):
    class Process:
        count=0
        def poll(self):
            self.count+=1
            if self.count==1:raise OSError("poll")
            return 0
        def terminate(self):raise AssertionError("no termination")
    process=Process();summary={"budget":{},"failure":"first"}
    async def sleep(_):pass
    asyncio.run(retain_original_controller(process,summary,tmp_path/"summary",lambda *a:(_ for _ in ()).throw(OSError("writer")),sleep))
    assert process.count==2 and summary["failure"]=="first" and len(summary["hold_diagnostics"])>=2


def test_production_serve_observer_receives_actual_new_daemon_before_listener(tmp_path,monkeypatch):
    from comsol_mcp import _control_daemon as module
    observed=[]
    def observer(daemon):assert type(daemon) is ControlDaemon;observed.append(daemon)
    def listener(home,daemon,*args):assert observed==[daemon];daemon.close();return "ordinary-listener"
    monkeypatch.setattr(module,"_serve_control_listener",listener)
    assert module.serve(tmp_path/"serve",_owner_observer=observer)=="ordinary-listener"
    assert len(observed)==1


def test_actual_production_worker_guards_delegate_then_retain_partial_birth_and_refuse(owned,monkeypatch,tmp_path):
    from comsol_mcp._java_worker import PersistentJavaWorker,JavaWorkerPaths
    root,binding,daemon,owner=owned
    # Fresh owner is only the task entry's isolated production controller.
    second=P0OriginalControllerOwner(root,binding,write)
    originals={name:getattr(PersistentJavaWorker,name) for name in ("start","_request","close")}
    calls=[];process=SimpleNamespace(pid=51001,poll=lambda:None)
    def start(worker,*a,**k):
        calls.append(("start",a,k));worker._process=worker._process_factory(["harmless-original-argv"],text=True)
        raise OSError("original diagnostic after birth")
    def request(worker,*a,**k):calls.append(("request",a,k));return "original-result"
    def close(worker,*a,**k):calls.append(("close",a,k))
    for name,method in (("start",start),("_request",request),("close",close)):monkeypatch.setattr(PersistentJavaWorker,name,method)
    factory_calls=[]
    worker=PersistentJavaWorker(JavaWorkerPaths(tmp_path,tmp_path),state_dir=tmp_path/"worker",process_factory=lambda *a,**k:factory_calls.append((a,k)) or process)
    try:
        second.bind(daemon,guard_birth=True)
        assert worker._request({"type":"software"},timeout_s=1)=="original-result"
        with pytest.raises(OSError,match="diagnostic after birth"):worker.start(startup_timeout_s=7)
        assert second.refs[("worker_birth",id(worker))][1] is process
        assert calls[0]==("request",({"type":"software"},),{"timeout_s":1})
        assert calls[1]==("start",(),{"startup_timeout_s":7})
        assert factory_calls==[((['harmless-original-argv'],),{"text":True})]
        second.cause(RuntimeError("first"));second.fence()
        for action in (lambda:worker.start(),lambda:worker._request({}),lambda:worker.close()):
            with pytest.raises(RuntimeError,match="FENCED"):action()
        assert len(calls)==2 and len(factory_calls)==1 and second.refs[("worker_birth",id(worker))][1] is process
        assert not second.persist()
    finally:
        for name,method in originals.items():setattr(PersistentJavaWorker,name,method)
        process.poll=lambda:0
        os.close(second.fd)


def test_original_launcher_partialbirth_late_error_and_fence_preserves_handles(owned,monkeypatch):
    from comsol_mcp._java_worker import PersistentJavaWorker
    from comsol_mcp._session_server import OwnedServerError
    root,binding,daemon,owner=owned;second=P0OriginalControllerOwner(root,binding,write)
    originals={name:getattr(PersistentJavaWorker,name) for name in ("start","_request","close")}
    process=SimpleNamespace(pid=51002,poll=lambda:None);launcher=daemon.session_server_launcher
    calls=[];launcher.process_factory=lambda *a,**k:calls.append((a,k)) or process
    handle=SimpleNamespace(process=process)
    def start(*a,**k):
        assert a==("runtime","project","session") and k=={"observation_sink":None}
        launcher.process_factory(["original-server"],cwd="private")
        second.fence() # Software stand-in for simultaneous hold notification.
        raise OwnedServerError("original late diagnostic",handle=handle,uncertain=True)
    launcher.start=start
    try:
        second.bind(daemon,guard_birth=True)
        with pytest.raises(OwnedServerError):launcher.start("runtime","project","session",observation_sink=None)
        assert any(row[1] is process for row in second.refs.values()) and second.refs[("server_handle",id(handle))][0] is handle
        with pytest.raises(RuntimeError,match="NO_NEXT"):launcher.start("runtime","project","session")
        with pytest.raises(RuntimeError,match="NO_NEXT"):launcher.process_factory(["next"])
        assert len(calls)==1 and not second.persist()
    finally:
        for name,method in originals.items():setattr(PersistentJavaWorker,name,method)
        process.poll=lambda:0;os.close(second.fd)


def test_actual_owned_server_lane_fence_allows_busy_without_quiescence(owned):
    from tests.test_session_context import _context,_process
    from comsol_mcp._session_context import CanonicalSocket
    root,binding,daemon,owner=owned
    peer=CanonicalSocket("127.0.0.1",2036);identity=_process(52001,"software-owned-birth",peer)
    worker=Worker(SimpleNamespace(pid=52002,poll=lambda:None))
    context=_context("holder",process=identity,ownership="mcp_managed",worker=worker)
    key=context.key;daemon.session_registry.register(context);daemon._session_worker_handles[key]=worker
    daemon.session_scheduler._scheduled_tasks["already-accepted"]={"worker_binding_key":context.worker_binding_key,"state":"RUNNING"}
    owner.capture();owner.fence()
    assert identity.lock_key in daemon.session_scheduler._closed_lane_keys
    assert context.worker_binding_key not in daemon.session_scheduler._closed_session_bindings
    assert not owner.persist() and daemon.session_scheduler._scheduled_tasks["already-accepted"]["state"]=="RUNNING"
    daemon.session_scheduler._scheduled_tasks.clear()


def make_config(root):
    tool=Path(__file__).resolve().parent;source=root/"source";(source/"comsol_mcp").mkdir(parents=True)
    (source/"comsol_mcp/__init__.py").write_text("# immutable software fixture\n")
    files={"comsol_mcp/__init__.py":hashlib.sha256((source/"comsol_mcp/__init__.py").read_bytes()).hexdigest()}
    from p0_original_controller_hold import canonical
    freeze=root/"FREEZE.json";freeze.write_text(json.dumps({"files":files}))
    binding={"activity_id":root.name,"namespace":str(root),"source_sha256":hashlib.sha256(canonical(files).encode()).hexdigest(),"owner_nonce":str(uuid4())}
    for name,field in (("run_v3_public_mcp_smoke.py","harness_sha256"),("p0_owned_control_entry.py","entry_sha256"),("p0_original_controller_hold.py","holder_sha256")):
        binding[field]=hashlib.sha256((tool/name).read_bytes()).hexdigest()
    config={"binding":binding,"source":str(source),"freeze":str(freeze),"home":str(root/"home/control-private")}
    path=root/"P0_OWNER_CONFIG.json";path.write_text(json.dumps(config));path.chmod(0o600)
    return path,config


@pytest.mark.parametrize("fault",[None,"nonce","home","entry","source","extra","binding_extra","mode","symlink"])
def test_entry_exact_source_activity_config_controls_before_production_birth(owned,fault):
    root,binding,daemon,owner=owned;path,config=make_config(root)
    if fault is None:assert load_config(path)==config;return
    if fault=="nonce":config["binding"]["owner_nonce"]="wrong"
    if fault=="home":config["home"]="/outside"
    if fault=="entry":config["binding"]["entry_sha256"]="f"*64
    if fault=="source":config["binding"]["source_sha256"]="f"*64
    if fault=="extra":config["extra"]=True
    if fault=="binding_extra":config["binding"]["extra"]=True
    path.write_text(json.dumps(config))
    if fault=="mode":path.chmod(0o644)
    if fault=="symlink":path.rename(root/"target-config");path.symlink_to(root/"target-config")
    with pytest.raises((ValueError,OSError)):load_config(path)


def test_listener_failure_keeps_actual_original_children_until_natural_terminal(owned):
    root,binding,daemon,owner=owned
    process=SimpleNamespace(pid=53001,code=None)
    process.poll=lambda:process.code
    daemon._session_server_handles[("p","s")]=SimpleNamespace(process=process)
    original=RuntimeError("listener first failure");owner.cause(original)
    done=threading.Event();done.set();observed=[]
    def sleep(_):
        observed.append(owner.refs[("server",("p","s"))][1])
        assert owner.fenced and owner.firstcause is original
        process.code=0
    owner.sleeper=sleep;owner.watch(done)
    assert observed==[process] and owner.firstcause is original
    receipt=json.loads((root/"evidence/P0_OWNER_HOLD.json").read_text())
    assert receipt["terminal_original_children"] and receipt["status"]=="FAIL_UNKNOWN_OWNER_HOLD"


def test_replaced_private_namespace_blocks_terminal_proof(owned):
    root,binding,daemon,owner=owned
    moved=root.with_name(root.name+"-original")
    try:
        root.rename(moved);root.mkdir(mode=0o700)
        owner.fence();assert not owner.persist() and "original private namespace changed" in owner.proof_unavailable
    finally:
        root.rmdir();moved.rename(root)


def test_original_default_serve_unchanged_without_observer(tmp_path,monkeypatch):
    from comsol_mcp import _control_daemon as module
    calls=[]
    def listener(home,daemon,*args):
        calls.append((type(daemon),args));daemon.close();return 7
    monkeypatch.setattr(module,"_serve_control_listener",listener)
    assert module.serve(tmp_path/"default")==7
    assert calls==[(ControlDaemon,(calls[0][1][0],None,None,None))]


@pytest.mark.parametrize("fault",["clock","fence_once","fence_always"])
def test_real_original_child_survives_secondary_diagnostics_until_natural_exit(owned,monkeypatch,fault):
    root,binding,daemon,owner=owned
    child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(.8)"])
    key=("p","s");worker=Worker(child);daemon._session_worker_handles[key]=worker
    owner.capture();original=RuntimeError("first original failure");owner.cause(original)
    done=threading.Event();done.set();failures=[]
    if fault=="clock":owner.clock=lambda:(_ for _ in ()).throw(OSError("persistent clock diagnostic"))
    else:
        actual_fence=owner.fence
        def failing_fence():
            failures.append(1)
            if fault=="fence_always" or len(failures)==1:raise OSError("secondary fence diagnostic")
            return actual_fence()
        monkeypatch.setattr(owner,"fence",failing_fence)
    thread=threading.Thread(target=owner.watch,args=(done,),daemon=True);thread.start()
    try:
        time.sleep(.12)
        assert thread.is_alive() and child.poll() is None and owner.refs[("worker",key)][1] is child
        assert owner.fenced and daemon.closed.is_set() and owner.firstcause is original
        with pytest.raises(RuntimeError,match="FENCED"):worker._request({})
        assert worker.calls==[]
        child.wait(timeout=3) # Natural sleep completion, no signal or SDK.
        thread.join(timeout=3)
        assert not thread.is_alive() and owner.firstcause is original
        receipt=json.loads((root/"evidence/P0_OWNER_HOLD.json").read_text())
        assert receipt["terminal_original_children"] and receipt["status"]=="FAIL_UNKNOWN_OWNER_HOLD"
        assert owner.diagnostics and not receipt["standard_cleanup_complete"]
    finally:
        if child.poll() is None:child.terminate()
        child.wait(timeout=15)
        thread.join(timeout=3)


@pytest.mark.parametrize("mutation",["replace","hardlink","mode","size","ctime"])
def test_startup_config_same_fd_name_metadata_rechecked_after_read(owned,monkeypatch,mutation):
    root,binding,daemon,owner=owned;path,config=make_config(root)
    original_fdopen=os.fdopen
    class DuringRead:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*a):return self.stream.__exit__(*a)
        def fileno(self):return self.stream.fileno()
        def read(self,*a):
            raw=self.stream.read(*a)
            if mutation=="replace":
                replacement=root/"replacement";replacement.write_bytes(raw);replacement.chmod(0o600);replacement.replace(path)
            elif mutation=="hardlink":os.link(path,root/"config-hardlink")
            elif mutation=="mode":path.chmod(0o644)
            elif mutation=="size":path.write_bytes(raw+b' ')
            elif mutation=="ctime":path.chmod(0o400);path.chmod(0o600)
            return raw
    monkeypatch.setattr(os,"fdopen",lambda *a,**k:DuringRead(original_fdopen(*a,**k)))
    with pytest.raises(ValueError,match="changed"):load_config(path)


def test_terminal_inflight_read_failure_remains_unknown_then_returns_failed(owned):
    root,binding,daemon,owner=owned
    process=SimpleNamespace(pid=54001,poll=lambda:0);daemon._session_worker_handles[("p","s")]=Worker(process)
    owner.capture();owner.cause(RuntimeError("first"));owner.fence()
    class Unobserved(dict):
        def __bool__(self):raise OSError("inflight diagnostic inaccessible")
    original=daemon.running;daemon.running=Unobserved()
    assert not owner.persist() and owner.refs[("worker",("p","s"))][1] is process
    daemon.running=original
    assert owner.persist() and str(owner.firstcause)=="first"


def test_startup_config_uid_change_in_post_read_observation_refused(owned,monkeypatch):
    root,binding,daemon,owner=owned;path,config=make_config(root)
    original=os.fstat;calls=[]
    def observe(fd):
        st=original(fd);calls.append(fd)
        # private_fd first observation, then config opened, then read-after.
        if len(calls)==3:
            fields={name:getattr(st,name) for name in ('st_dev','st_ino','st_mode','st_uid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')}
            fields['st_uid']+=1;return SimpleNamespace(**fields)
        return st
    monkeypatch.setattr(os,'fstat',observe)
    with pytest.raises(ValueError,match='changed'):load_config(path)
