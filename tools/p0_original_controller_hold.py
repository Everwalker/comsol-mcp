"""P0-only hold notification and original production owner observation. No stop API."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time

REQUEST = "P0_HOLD_ONLY.json"
RECEIPT = "P0_OWNER_HOLD.json"
SCHEMA = "p0-original-controller-hold/1"


def pairs(rows):
    result = {}
    for key, value in rows:
        if key in result: raise ValueError("duplicate hold field")
        result[key] = value
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def file_signature(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def text(exc):
    try: return str(exc)[:1024]
    except BaseException: return "diagnostic text unavailable"


def private_fd(root):
    root = Path(root)
    st = root.lstat()
    if root.parent != Path("/private/tmp") or root.resolve(strict=True) != root or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise ValueError("exact private P0 namespace required")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    after = os.fstat(fd)
    if (st.st_dev, st.st_ino) != (after.st_dev, after.st_ino):
        os.close(fd); raise ValueError("namespace changed")
    return fd, (st.st_dev, st.st_ino)


def publish_hold(root, binding, firstcause):
    fd, _ = private_fd(root)
    try:
        body = {"schema": SCHEMA, "action": "HOLD_ONLY_NO_STOP", "binding": binding,
                "firstcause": {"type": type(firstcause).__name__, "error": text(firstcause)}}
        raw = canonical(body).encode()
        with os.fdopen(os.open(REQUEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd), "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.fsync(fd)
    finally: os.close(fd)


class P0OriginalControllerOwner:
    """Observer receives only the newly constructed real production daemon."""
    def __init__(self, root, binding, writer, clock=time.monotonic, sleeper=time.sleep):
        self.root, self.binding_json = Path(root), canonical(binding)
        self.writer, self.clock, self.sleeper = writer, clock, sleeper
        self.fd, self.root_inode = private_fd(self.root)
        self.daemon = None; self.containers = {}; self.refs = {}; self.workers = {}; self.backends = {}
        self.guards = {}; self.fenced = False; self.consumed = False; self.firstcause = None
        self.frozen_backend_graph = {}
        self.diagnostics = []; self.proof_unavailable = []; self.listener_failed = False
        self.last_write = None; self.original_dispatch = None
        self.lock = threading.RLock()

    def diagnostic(self, stage, exc):
        self.diagnostics[:] = (self.diagnostics + [{"stage": stage, "type": type(exc).__name__, "error": text(exc)}])[-16:]

    def cause(self, exc):
        if self.firstcause is None: self.firstcause = exc

    def bind(self, daemon, *, guard_birth=False):
        if self.daemon is not None: raise RuntimeError("original daemon cannot be replaced")
        self.daemon = daemon
        self.default_backend = daemon._default_backend
        self.session_registry = daemon.session_registry
        self.session_scheduler = daemon.session_scheduler
        for name in ("_session_worker_handles", "_session_server_handles", "_session_backends"):
            self.containers[name] = getattr(daemon, name)
        self.original_dispatch = daemon.dispatch
        def dispatch(request):
            if self.fenced: raise RuntimeError("P0_ORIGINAL_OWNER_FENCED")
            try:
                result = self.original_dispatch(request)
            except BaseException as exc:
                self.cause(exc); self.fence(); raise
            finally:
                self.capture()
            if isinstance(result, dict) and ("UNKNOWN" in str(result.get("error", {})) or result.get("data", {}).get("status") in {"UNKNOWN", "RUNNING", "PENDING"}):
                self.cause(RuntimeError("public controller outcome uncertain: " + str(result.get("error", {}))))
                self.fence()
            return result
        self.dispatch_guard = dispatch
        daemon.dispatch = dispatch
        if guard_birth:
            self.install_original_birth_guards()
        self.capture()

    def install_original_birth_guards(self):
        # Isolated task-owned controller only: original classes/objects still
        # perform normal construction and every allowed effect themselves.
        from comsol_mcp._java_worker import PersistentJavaWorker
        self.original_worker_methods = {}
        for name in ("start", "_request", "close"):
            original = getattr(PersistentJavaWorker, name)
            self.original_worker_methods[name] = original
            def guarded(worker, *a, _name=name, _original=original, **k):
                self._worker(("unregistered",str(id(worker))),worker)
                if self.fenced: raise RuntimeError("P0_ORIGINAL_WORKER_FENCED")
                if _name == "start" and not hasattr(worker,"_p0_original_factory_guard"):
                    factory = worker._process_factory or subprocess.Popen
                    def birth(*args,**kwargs):
                        if self.fenced: raise RuntimeError("P0_UNKNOWN_NO_NEXT_WORKER_BIRTH")
                        if ("worker_birth",id(worker)) in self.refs:
                            raise RuntimeError("P0_ORIGINAL_WORKER_BIRTH_CANNOT_REPLAY")
                        process = factory(*args,**kwargs)
                        self._retain(("worker_birth",id(worker)),worker,process)
                        return process
                    worker._p0_original_factory_guard = birth
                    worker._process_factory = birth
                try: return _original(worker,*a,**k)
                except BaseException as exc:
                    self.cause(exc)
                    raise
                finally: self.capture()
            setattr(PersistentJavaWorker,name,guarded)
        launcher = self.daemon.session_server_launcher
        original_start = launcher.start
        def start(*a,**k):
            if self.fenced: raise RuntimeError("P0_UNKNOWN_NO_NEXT_SERVER_START")
            try:
                handle = original_start(*a,**k)
                self._retain(("server_handle",id(handle)),handle,handle.process)
                return handle
            except BaseException as exc:
                self.cause(exc)
                handle = getattr(exc,"handle",None)
                if handle is not None:
                    self._retain(("server_handle",id(handle)),handle,handle.process)
                raise
            finally:self.capture()
        launcher.start = start
        factory = launcher.process_factory
        def server_birth(*a,**k):
            if self.fenced: raise RuntimeError("P0_UNKNOWN_NO_NEXT_SERVER_BIRTH")
            if any(key[0]=="server_birth" for key in self.refs):
                raise RuntimeError("P0_ORIGINAL_SERVER_BIRTH_CANNOT_REPLAY")
            process = factory(*a,**k)
            self._retain(("server_birth",id(process)),launcher,process)
            return process
        launcher.process_factory = server_birth

    def guard_worker(self, worker):
        with self.lock:
            self._guard_worker(worker)

    def _guard_worker(self, worker):
        if id(worker) in self.guards: return
        guards = {}
        for name in ("_request", "start", "close"):
            original = getattr(worker, name, None)
            if not callable(original): continue
            def guard(*a, _original=original, **k):
                if self.fenced: raise RuntimeError("P0_ORIGINAL_WORKER_FENCED")
                return _original(*a, **k)
            setattr(worker, name, guard); guards[name] = guard
        self.guards[id(worker)] = (worker, guards)

    def capture(self):
        with self.lock:
            if self.daemon is None: return
            try:
                current_root = self.root.lstat()
                if ((current_root.st_dev,current_root.st_ino) != self.root_inode
                        or not stat.S_ISDIR(current_root.st_mode) or current_root.st_uid != os.getuid()
                        or stat.S_IMODE(current_root.st_mode) != 0o700):
                    self.proof_unavailable.append("original private namespace changed")
                for name, container in self.containers.items():
                    if getattr(self.daemon, name) is not container:
                        self.proof_unavailable.append("original container swapped: " + name)
                if (self.daemon._default_backend is not self.default_backend
                        or self.daemon.session_registry is not self.session_registry
                        or self.daemon.session_scheduler is not self.session_scheduler):
                    self.proof_unavailable.append("original default backend/registry/scheduler swapped")
                for key, handle in list(self.containers["_session_server_handles"].items()):
                    process = handle.process
                    self._retain(("server", key), handle, process)
                for key, backend in list(self.containers["_session_backends"].items()):
                    previous = self.backends.setdefault(key, backend)
                    if previous is not backend: self.proof_unavailable.append("original backend swapped")
                    if self.fenced:
                        graph = (getattr(backend,"service",None),canonical(getattr(backend,"worker_identity",None)))
                        original_graph = self.frozen_backend_graph.setdefault(key,graph)
                        if graph[0] is not original_graph[0] or graph[1] != original_graph[1]:
                            self.proof_unavailable.append("original Service/runtime identity swapped")
                    worker = getattr(backend, "worker", None)
                    if worker is not None: self._worker(key, worker)
                for key, worker in list(self.containers["_session_worker_handles"].items()): self._worker(key, worker)
                worker = self.daemon.worker
                if worker is not None: self._worker(("default", "default"), worker)
                if self.daemon.dispatch is not self.dispatch_guard:
                    self.proof_unavailable.append("original dispatch guard replaced")
                for guarded, guards in self.guards.values():
                    if any(getattr(guarded,name,None) is not guard for name,guard in guards.items()):
                        self.proof_unavailable.append("original Worker guard replaced")
            except BaseException as exc:
                self.diagnostic("capture", exc); self.proof_unavailable.append("owner capture incomplete")
            self.proof_unavailable[:] = list(dict.fromkeys(self.proof_unavailable))[-16:]
            if self.proof_unavailable and not self.fenced:
                self.cause(RuntimeError("original owner graph proof unavailable"))
                self.fence()

    def _worker(self, key, worker):
        original = self.workers.setdefault(key, worker)
        if original is not worker: self.proof_unavailable.append("original Worker swapped")
        # Retain swapped objects as well; do not discard either child's handle.
        self.guard_worker(worker)
        process = getattr(worker, "_process", None)
        if process is not None: self._retain(("worker", key), worker, process)

    def _retain(self, key, owner, process):
        with self.lock:
            record = self.refs.get(key)
            if record is None:
                self.refs[key] = (owner, process, process.pid)
            elif record[0] is not owner or record[1] is not process or record[2] != process.pid:
                self.proof_unavailable.append("original process/object swapped")
                self.refs.setdefault(("swapped", key, id(process)), (owner, process, process.pid))

    def _fence_admission(self):
        self.fenced = True  # Before any fallible diagnostic/graph observation.
        daemon = self.daemon
        if daemon is not None:
            for stage, action in (
                ("closing",lambda:setattr(daemon,"_closing",True)),
                ("monitor",lambda:daemon.closed.set()),
                ("worker_admission",lambda:setattr(daemon,"_worker_retirement_state","P0_UNKNOWN_HOLD"))):
                try:action()
                except BaseException as exc:self.diagnostic("fence:"+stage,exc)

    def fence(self):
        first = not self.fenced
        self._fence_admission()
        try:
            with self.lock:
                self.capture()
                daemon = self.daemon
                if daemon is None:return
                for key in self.workers:
                    if key[0] in {"default", "unregistered"}: continue
                    try:
                        context = daemon.session_registry.get(*key)
                        daemon.session_scheduler.fence_owned_server_lane(context)
                    except BaseException as exc: self.diagnostic("owned_lane_fence_unavailable", exc)
        except BaseException as exc:self.diagnostic("fence_graph",exc)
        if first:
            try:self.persist()
            except BaseException as exc:self.diagnostic("fence_first_receipt",exc)

    def request(self):
        if self.consumed: return
        try:
            st = self.root.lstat(); opened = os.fstat(self.fd)
            if (st.st_dev, st.st_ino) != self.root_inode or (opened.st_dev, opened.st_ino) != self.root_inode or stat.S_IMODE(st.st_mode) != 0o700:
                raise ValueError("original private namespace changed")
            before = os.stat(REQUEST, dir_fd=self.fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or not 0 < before.st_size <= 32768:
                raise ValueError("hold notification must be owned ordinary0600 singlelink bounded file")
            with os.fdopen(os.open(REQUEST, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd), "rb") as stream:
                at_open = os.fstat(stream.fileno()); raw = stream.read(32769); after = os.fstat(stream.fileno())
            current = os.stat(REQUEST, dir_fd=self.fd, follow_symlinks=False)
            if any(file_signature(x) != file_signature(before) for x in (at_open, after, current)): raise ValueError("hold notification changed")
            body = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            if (not isinstance(body, dict) or set(body) != {"schema","action","binding","firstcause"}
                    or body["schema"] != SCHEMA or body["action"] != "HOLD_ONLY_NO_STOP" or canonical(body["binding"]) != self.binding_json
                    or not isinstance(body["firstcause"],dict) or set(body["firstcause"]) != {"type","error"}
                    or any(not isinstance(v,str) or len(v)>1024 for v in body["firstcause"].values())):
                raise ValueError("closed source/activity/nonce hold binding differs")
            self.consumed = True
            self.cause(RuntimeError(body["firstcause"]["type"] + ": " + body["firstcause"]["error"]))
            self.fence()
        except FileNotFoundError: pass
        except BaseException as exc: self.diagnostic("hold_notification_refused", exc)

    def observe(self):
        self.capture(); rows = []
        for key, (owner, process, pid) in list(self.refs.items()):
            try:
                if type(process.pid) is not int or process.pid != pid: raise ValueError("original pid/object differs")
                code = process.poll()
                if code is not None and type(code) is not int: raise ValueError("untyped exit observation")
                state = "ALIVE" if code is None else "EXITED"
            except BaseException as exc: self.diagnostic("original_poll",exc);code=None;state="OBSERVATION_UNKNOWN"
            rows.append({"role":str(key[0]),"pid":pid,"state":state,"returncode":code})
        return rows

    def persist(self):
        rows = self.observe()
        terminal = (self.fenced and not self.proof_unavailable
            and (self.daemon is not None or self.listener_failed)
            and all(r["state"]=="EXITED" for r in rows))
        # A partially completed original birth may still be in production dispatch.
        try:
            if self.daemon is not None and (self.daemon._control_dispatch_inflight or self.daemon._worker_admission_inflight): terminal=False
            if self.daemon is not None and (self.daemon.running or self.session_scheduler._scheduled_tasks): terminal=False
        except BaseException as exc:
            terminal=False;self.diagnostic("terminal_inflight_proof_unavailable",exc)
        result={"schema":SCHEMA,"binding":json.loads(self.binding_json),"status":"FAIL_UNKNOWN_OWNER_HOLD",
                "fenced":self.fenced,"hold_consumed_once":self.consumed,"terminal_original_children":terminal,
                "children":rows,"proof_unavailable":self.proof_unavailable,"diagnostics":self.diagnostics,
                "firstcause":{"type":type(self.firstcause).__name__,"error":text(self.firstcause)} if self.firstcause is not None else None,
                "native_lifetime_budget":"UNRESOLVED" if not terminal else "OBSERVED_EXIT_NO_NATIVE_ACCEPTANCE",
                "standard_cleanup_complete":False,"durable_UNKNOWN_not_rewritten":True}
        try:
            now=self.clock()
            should_write=terminal or self.last_write is None or now-self.last_write>=5
        except BaseException as exc:
            self.diagnostic("receipt_clock",exc)
            now=None
            should_write=True
        if should_write:
            self.last_write=now
            try:self.writer(self.root/"evidence"/RECEIPT,result)
            except BaseException as exc:self.diagnostic("write",exc)
        return terminal

    def watch(self, listener_done):
        while True:
            try:
                self.capture();self.request()
                if listener_done.is_set() and not self.fenced:
                    self.cause(RuntimeError("original production listener ended"));self.listener_failed=True;self.fence()
                if self.fenced and self.persist(): return
                self.sleeper(0.05 if not self.fenced else 0.5)
            except BaseException as exc:
                self.cause(exc);self.diagnostic("owner_watch",exc)
                self._fence_admission()
                try:self.fence()
                except BaseException as nested:self.diagnostic("fallback_fence",nested)
                try:
                    if self.persist():return
                except BaseException as nested:self.diagnostic("fallback_receipt_or_proof",nested)
                try:threading.Event().wait(0.5)
                except BaseException as nested:self.diagnostic("wait",nested)


async def retain_original_controller(controller, summary, summary_path, writer, sleeper, clock=time.monotonic):
    """Keep the exact client Popen until actual exit; never terminate or RPC."""
    summary["status"]="FAIL_ORIGINAL_CONTROLLER_HOLD"
    while True:
        try:
            code=controller.poll()
            if code is not None and type(code) is not int:raise ValueError("untyped controller exit")
        except BaseException as exc:
            summary.setdefault("hold_diagnostics",[]).append({"type":type(exc).__name__,"error":text(exc)});code=None
        summary["controller_exit_code"]=code
        summary["controller_original_popen_retained"]=True
        summary["budget"]["actual_native_lifetime"]="UNRESOLVED" if code is None else "CONTROLLER_EXIT_OBSERVED_NATIVE_ACCEPTANCE_NOT_INFERRED"
        try:writer(summary_path,summary)
        except BaseException as exc:summary.setdefault("hold_diagnostics",[]).append({"type":type(exc).__name__,"error":text(exc)})
        summary["hold_diagnostics"]=summary.get("hold_diagnostics",[])[-16:]
        if code is not None:return
        try:await sleeper(0.5)
        except BaseException as exc:summary.setdefault("hold_diagnostics",[]).append({"type":type(exc).__name__,"error":text(exc)})
