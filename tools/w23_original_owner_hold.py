"""Task-specific W23 UNKNOWN holder. No SDK calls, PID adoption, or retries."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
from uuid import UUID, uuid4

REQUEST_NAME = "W23_EXCEPTION_STOP_REQUEST.json"
ONCE_NAME = "W23_EXCEPTION_STOP_CONSUMED.json"
SCHEMA = "w23-original-owner-exception-stop/v1"


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError("duplicate recovery key")
        result[key] = value
    return result


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_uid, st.st_mode, st.st_nlink, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _text(value):
    try: return str(value)
    except BaseException: return "diagnostic text unavailable"


class W23OriginalOwnerHold:
    """Retain original object graph and fence it until original children exit."""
    def __init__(self, *, binding, freeze_sha256, source_sha256, evidence: Path, writer,
                 clock=time.time, sleeper=time.sleep):
        self.binding = copy.deepcopy(binding)
        self.freeze_sha256, self.source_sha256 = freeze_sha256, source_sha256
        self._base_authority = json.dumps({"binding": binding, "freeze_sha256": freeze_sha256,
            "source_sha256": source_sha256}, sort_keys=True, allow_nan=False)
        self._held_proof = None
        self.evidence, self.writer, self.clock, self.sleeper = evidence, writer, clock, sleeper
        self.owner_nonce = str(uuid4())
        self._server = self._worker = self._daemon = self._backend = self._service = None
        self._server_process = self._worker_process = None
        self._original_pids = {"server": None, "worker": None}
        self._births = {"server": None, "worker": None}
        self._runtime_identity = None
        self._original_runtime_json = None
        self._attach_captured = False
        self._request_guard = None
        self._lifecycle_guards = {}
        self.first_cause = None
        self.summary = {}
        self.diagnostic_errors = []
        self.fenced = False
        self.entered_epoch = None
        self.deadline_epoch = None
        self.consumed = False
        self.signal_attempts = []
        self._root_fd = None
        self._root_identity = None
        self._summary_written = False
        self._last_write_epoch = None
        if binding is not None:
            self._namespace_fd()  # Pin original private inode before native birth.

    def diagnostic(self, phase, exc):
        # Bounded diagnostics must never replace first cause or release handles.
        self.diagnostic_errors[:] = (self.diagnostic_errors + [{"phase": phase, "type": type(exc).__name__, "error": _text(exc)[:512]}])[-16:]

    def cause(self, exc):
        if self.first_cause is None:
            self.first_cause = exc

    def bind_server(self, server):
        if self._server is not None and self._server is not server:
            raise RuntimeError("cannot replace original W23 server owner")
        self._server = server

    def capture_server(self):
        process = getattr(self._server, "proc", None)
        if self._server_process is None and process is not None:
            self._server_process = process
            self._original_pids["server"] = process.pid
        elif process is not None and process is not self._server_process:
            raise RuntimeError("original Server Popen was replaced")
        identity = getattr(self._server, "process_identity", None)
        if self._births["server"] is None and isinstance(identity, dict):
            self._births["server"] = copy.deepcopy(identity)

    def bind_worker(self, worker):
        if self._worker is not None and self._worker is not worker:
            raise RuntimeError("cannot replace original W23 Worker")
        self._worker = worker
        original_request = worker._request
        def guarded_request(*args, **kwargs):
            if self.fenced:
                raise RuntimeError("W23_UNKNOWN_OWNER_FENCED_NO_MORE_WORKER_REQUESTS")
            return original_request(*args, **kwargs)
        self._request_guard = guarded_request
        worker._request = guarded_request
        for name in ("start", "close"):
            original = getattr(worker, name, None)
            if not callable(original):
                continue
            def guarded_lifecycle(*args, _original=original, **kwargs):
                if self.fenced:
                    raise RuntimeError("W23_UNKNOWN_OWNER_FENCED_NO_MORE_WORKER_LIFECYCLE")
                return _original(*args, **kwargs)
            self._lifecycle_guards[name] = guarded_lifecycle
            setattr(worker, name, guarded_lifecycle)

    def worker_process_factory(self, *args, **kwargs):
        if self.fenced or self._worker_process is not None:
            raise RuntimeError("W23 Worker birth cannot replay or replace original Popen")
        process = subprocess.Popen(*args, **kwargs)
        # Retain the returned exact handle before Worker diagnostics can fail.
        self._worker_process = process
        self._original_pids["worker"] = process.pid
        return process

    def capture_worker(self, identity=None):
        process = getattr(self._worker, "_process", None)
        if self._worker_process is None and process is not None:
            self._worker_process = process
            self._original_pids["worker"] = process.pid
        elif process is not None and process is not self._worker_process:
            raise RuntimeError("original Worker Popen was replaced")
        if self._births["worker"] is None and isinstance(identity, dict):
            self._births["worker"] = copy.deepcopy(identity)

    def bind_daemon(self, daemon):
        if self._daemon is not None and self._daemon is not daemon:
            raise RuntimeError("cannot replace original W23 daemon")
        self._daemon = daemon
        self._backend = daemon.backend
        self._service = self._backend.service
        self._runtime_identity = copy.deepcopy(self._backend.worker_identity)
        self._original_runtime_json = json.dumps(self._runtime_identity, sort_keys=True, allow_nan=False)

    def capture_attach(self):
        if self.fenced:
            raise RuntimeError("held runtime attachment cannot be rebound")
        if self._backend is not None:
            current = copy.deepcopy(self._backend.worker_identity)
            if self._attach_captured:
                if (json.dumps(current, sort_keys=True, allow_nan=False) != self._original_runtime_json
                        or self._backend.service is not self._service):
                    raise RuntimeError("original attached runtime identity cannot be replaced")
                return
            # Successful production server_connect constructs ExecutionService.
            # Capture that single legitimate transition before any hold fence.
            self._service = self._backend.service
            self._runtime_identity = current
            self._original_runtime_json = json.dumps(current, sort_keys=True, allow_nan=False)
            self._attach_captured = True

    def _graph_matches(self):
        for role, process in (("server", self._server_process), ("worker", self._worker_process)):
            if process is not None and (type(process.pid) is not int or process.pid != self._original_pids[role]):
                return False
        if self._server is None or self._server.proc is not self._server_process:
            return False
        if self._worker is not None and (self._server.worker is not self._worker
                or self._worker._process is not self._worker_process or self._worker._request is not self._request_guard):
            return False
        if self._worker is not None and any(getattr(self._worker, name, None) is not guard for name, guard in self._lifecycle_guards.items()):
            return False
        if self._daemon is not None and (self._daemon.backend is not self._backend
                or self._daemon.worker is not self._worker or self._backend.worker is not self._worker
                or self._backend.service is not self._service
                or self._original_runtime_json is None
                or json.dumps(self._backend.worker_identity, sort_keys=True, allow_nan=False) != self._original_runtime_json):
            return False
        return True

    def _fence_admission(self):
        self.fenced = True
        daemon = self._daemon
        if daemon is not None:
            def refused_dispatch(*args, **kwargs):
                raise RuntimeError("W23_UNKNOWN_OWNER_FENCED_NO_MORE_MANAGED_REQUESTS")
            # Independent guarded assignments: monitor/receipt diagnostics must
            # not prevent fencing the remaining actual owner admission paths.
            for phase, action in (
                ("dispatch", lambda: setattr(daemon, "dispatch", refused_dispatch)),
                ("closing", lambda: setattr(daemon, "_closing", True)),
                ("monitor", lambda: daemon.closed.set()),
                ("worker_admission", lambda: setattr(daemon, "_worker_retirement_state", "W23_UNKNOWN_HOLD"))):
                try: action()
                except BaseException as exc: self.diagnostic("daemon_fence:" + phase, exc)

    def fence(self, cleanup=None):
        first_entry = not self.fenced
        self._fence_admission()  # Keep handles fenced before any diagnostic.
        if first_entry:
            try: self._held_proof = json.dumps(self._build_proof(), sort_keys=True, allow_nan=False)
            except BaseException as exc: self.diagnostic("freeze_exception_authority", exc)
        if isinstance(cleanup, dict):
            try:
                if self.first_cause is None:
                    self.cause(RuntimeError(cleanup.get("error") or cleanup.get("reason") or "normal owned cleanup refused; UNKNOWN retained"))
                self.summary["cleanup_status"] = cleanup.get("status")
                self.summary["original_cleanup_refusal"] = copy.deepcopy(cleanup)
            except BaseException as exc: self.diagnostic("cleanup_refusal_receipt", exc)
        if self.entered_epoch is None:
            try: self.entered_epoch = self.clock()
            except BaseException as exc: self.diagnostic("hold_entry_clock", exc)
        if first_entry:
            try: self.persist(self.observe())
            except BaseException as exc: self.diagnostic("first_hold_receipt", exc)

    def observe(self):
        observed = {}
        for role, process in (("worker", self._worker_process), ("server", self._server_process)):
            if process is None:
                observed[role] = {"created": False, "state": "NOT_CREATED"}
                continue
            try:
                code = process.poll()
                if code is not None and type(code) is not int:
                    raise RuntimeError("original Popen poll returned no typed exit observation")
                observed[role] = {"created": True, "state": "ALIVE" if code is None else "EXITED", "returncode": code}
            except BaseException as exc:
                self.diagnostic(role + "_poll", exc)
                observed[role] = {"created": True, "state": "OBSERVATION_UNKNOWN"}
        return observed

    @staticmethod
    def terminal(observed):
        return all(row["state"] in {"NOT_CREATED", "EXITED"} for row in observed.values())

    def _build_proof(self):
        base = json.loads(self._base_authority)
        binding = base["binding"]
        if not binding or not self._graph_matches():
            raise ValueError("fresh binding/original object graph unavailable")
        UUID(binding["activity_id"])
        for role, process in (("server", self._server_process), ("worker", self._worker_process)):
            identity = self._births[role]
            if process is not None and (not isinstance(identity, dict) or identity.get("pid") != process.pid
                    or not isinstance(identity.get("birth"), str) or not identity["birth"]
                    or not isinstance(identity.get("command_sha256"), str) or len(identity["command_sha256"]) != 64):
                raise ValueError("original process birth witness unavailable")
        return {"schema": SCHEMA, "action": "SIGTERM_ORIGINAL_WORKER_THEN_SERVER_ONLY",
                "activity_uuid": binding["activity_id"], "owner_nonce": self.owner_nonce,
                "freeze_sha256": base["freeze_sha256"], "source_manifest_sha256": base["source_sha256"],
                "runtime_binding_sha256": binding["binding_sha256"],
                "processes": copy.deepcopy(self._births), "attached_runtime_identity": copy.deepcopy(self._runtime_identity)}

    def _proof(self):
        if not self._graph_matches():
            raise ValueError("original owner graph no longer matches")
        if self.fenced:
            if self._held_proof is None:
                raise ValueError("exception authority unavailable at original hold entry")
            return json.loads(self._held_proof)
        return self._build_proof()

    def _namespace_fd(self):
        root = Path(json.loads(self._base_authority)["binding"]["namespace"])
        st = root.lstat()
        if root.parent != Path("/private/tmp") or root.resolve(strict=True) != root or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            raise ValueError("exception recovery requires exact private owned fresh namespace")
        if self._root_fd is None:
            self._root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._root_identity = (st.st_dev, st.st_ino)
        opened = os.fstat(self._root_fd)
        if (st.st_dev, st.st_ino) != self._root_identity or (opened.st_dev, opened.st_ino) != self._root_identity:
            raise ValueError("original recovery namespace inode changed")
        return self._root_fd

    def _read_request(self):
        fd = self._namespace_fd()
        before = os.stat(REQUEST_NAME, dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1 or not 0 < before.st_size <= 32768):
            raise ValueError("recovery request must be owned ordinary nofollow mode0600 single-link bounded file")
        with os.fdopen(os.open(REQUEST_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd), "rb") as stream:
            opened = os.fstat(stream.fileno()); raw = stream.read(32769); after = os.fstat(stream.fileno())
        current = os.stat(REQUEST_NAME, dir_fd=fd, follow_symlinks=False)
        if any(_identity(before) != _identity(row) for row in (opened, after, current)) or len(raw) > 32768:
            raise ValueError("recovery request changed during pinned read")
        request = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
        expected = self._proof()
        if not isinstance(request, dict) or set(request) != set(expected) | {"request_id", "authorization"}:
            raise ValueError("exception request closed schema differs")
        if any(json.dumps(request.get(k), sort_keys=True, allow_nan=False) != json.dumps(v, sort_keys=True, allow_nan=False)
               or type(request.get(k)) is not type(v) for k, v in expected.items()):
            raise ValueError("exception request does not match frozen original-owner proof")
        if not isinstance(request["request_id"], str) or str(UUID(request["request_id"])) != request["request_id"]:
            raise ValueError("canonical exception request UUID required")
        authorization = request["authorization"]
        if (not isinstance(authorization, dict) or set(authorization) != {"kind", "authorization_ref"}
                or authorization["kind"] != "DIRECT_HUMAN_EXCEPTION_STOP"
                or not isinstance(authorization["authorization_ref"], str) or not 1 <= len(authorization["authorization_ref"]) <= 256):
            raise ValueError("Main direct-human task exception authorization reference required")
        return request, raw, fd

    def consume_stop_request(self):
        if self.consumed or not self.fenced:
            return
        try:
            request, raw, fd = self._read_request()
            if not self._graph_matches():
                raise ValueError("original owner graph changed before exception consumption")
            self._namespace_fd()
            marker = os.open(ONCE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            self.consumed = True  # No replay even if marker fsync/diagnostics fail.
            with os.fdopen(marker, "wb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.fsync(fd)
            self.summary["exception_stop_request_id"] = request["request_id"]
            for role, process in (("worker", self._worker_process), ("server", self._server_process)):
                if process is None:
                    continue
                if not self._graph_matches():
                    raise ValueError("original object graph changed before SIGTERM")
                self._namespace_fd()
                try:
                    code = process.poll()
                    if code is None:
                        self.signal_attempts.append(role)
                        process.terminate()  # Exact original retained Popen; no PID signal.
                        process.wait(timeout=15)
                except BaseException as exc:
                    self.diagnostic(role + "_exception_SIGTERM_wait", exc)
        except FileNotFoundError:
            pass
        except BaseException as exc:
            self.diagnostic("exception_request_refused_or_unverified", exc)

    def persist(self, observed):
        try: now = self.clock()
        except BaseException as exc:
            self.diagnostic("hold_receipt_clock", exc)
            now = None
        self.summary.update({"status": "FAIL_OR_INCOMPLETE_ORIGINAL_OWNER_HOLD",
            "standard_exact_cleanup": "UNVERIFIED", "durable_unknown_preserved": True,
            "owner_hold": {"owner_nonce": self.owner_nonce, "entered_epoch": self.entered_epoch,
                "observation_epoch": now, "action_deadline_epoch": self.deadline_epoch,
                "hold_unresolved": not self.terminal(observed), "child_observation": observed,
                "original_objects_retained": True, "sdk_and_managed_admission_fenced": self.fenced,
                "request_consumed_once": self.consumed, "SIGTERM_attempts": list(self.signal_attempts),
                "diagnostic_errors": copy.deepcopy(self.diagnostic_errors),
                "first_cause": {"type": type(self.first_cause).__name__, "error": _text(self.first_cause)} if self.first_cause is not None else {"error": self.summary.get("error")},
                "native_lifetime_within_budget": "UNRESOLVED_WHILE_ORIGINAL_CHILDREN_ALIVE" if not self.terminal(observed) else "SEE_FINAL_BIRTH_INCLUSIVE_BUDGET",
                "exception_stop_is_not_standard_cleanup_or_native_acceptance": True}})
        self.summary["finished_at_utc"] = None
        budget = self.summary.setdefault("birth_inclusive_budget", {})
        budget.update({"finished_epoch_includes_cleanup": None, "elapsed_seconds_includes_cleanup": None,
            "standard_cleanup_complete": False, "child_lifetime_resolved": self.terminal(observed),
            "native_lifetime_within_budget": "UNRESOLVED_WHILE_ORIGINAL_CHILDREN_ALIVE" if not self.terminal(observed) else "SEE_FINAL_BIRTH_INCLUSIVE_BUDGET"})
        try:
            self.summary["owner_hold"]["exception_request_expected_proof"] = self._proof()
        except BaseException as exc:
            self.summary["owner_hold"]["exception_recovery_blocker"] = _text(exc)
        try:
            throttled = (not self.terminal(observed) and now is not None
                and self._last_write_epoch is not None and now-self._last_write_epoch < 5)
        except BaseException as exc:
            self.diagnostic("hold_receipt_throttle", exc)
            throttled = False
        if throttled:
            return
        self._last_write_epoch = now
        rows = [("OWNER_HOLD.json", {"status": self.summary["status"], "cleanup_status": self.summary.get("cleanup_status"),
                 "durable_unknown_preserved": True, "owner_hold": self.summary["owner_hold"]})]
        if not self._summary_written or self.terminal(observed):
            rows.append(("summary.json", self.summary))
        for name, value in rows:
            try:
                self.writer(self.evidence / name, value)
                if name == "summary.json": self._summary_written = True
            except BaseException as exc:
                self.diagnostic("write:" + name, exc)

    def run(self):
        try: self.fence()
        except BaseException as exc:
            self.cause(exc); self.diagnostic("initial_hold_fence", exc)
            self._fence_admission()
        while True:
            try: observed = self.observe()
            except BaseException as exc:
                self.diagnostic("hold_observation_unavailable", exc)
                observed = None
            if observed is not None:
                try: self.persist(observed)
                except BaseException as exc: self.diagnostic("hold_receipt", exc)
            if observed is not None and self.terminal(observed):
                self.summary.update(status="FAIL_OR_INCOMPLETE_ORIGINAL_OWNER_HOLD",
                    standard_exact_cleanup="UNVERIFIED", durable_unknown_preserved=True)
                self.summary.setdefault("owner_hold", {})["hold_unresolved"] = False
                if self._root_fd is not None:
                    try: os.close(self._root_fd); self._root_fd = None
                    except BaseException as exc: self.diagnostic("terminal_namespace_fd_close", exc)
                return self.summary
            try: self.consume_stop_request()
            except BaseException as exc: self.diagnostic("exception_stop_request_diagnostic", exc)
            try:
                self.sleeper(0.5)
            except BaseException as exc:
                self.diagnostic("observation_wait", exc)
                # Even Ctrl-C must not discard a created child owner.
                try: threading.Event().wait(0.5)
                except BaseException as nested: self.diagnostic("fallback_wait", nested)
