#!/usr/bin/env python3
"""Finite real-SDK stdio run with a synthetic business-dispatch seam.

This prospective child imports the bound production entrypoint, injects only
GatewayRegistry.dispatcher, and calls its ordinary ``main``. MCP framing,
stdio wrapping, protocol handlers, and shutdown remain production behavior.
"""
from __future__ import annotations

import argparse
import asyncio
import asyncio.selector_events
import concurrent.futures.thread
import contextvars
import functools
import hashlib
import importlib
import inspect
import io
import json
import os
import queue
import socket
import stat
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any

_INITIAL_SYS_PATH = list(sys.path)
_SENTINEL = "T038_SENTINEL_DO_NOT_LEAK_20261004"
_MAX_SECONDS = 180


def _bootstrap_binding(argv: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--binding", required=True)
    p.add_argument("--binding-sha256", required=True)
    a, _ = p.parse_known_args(argv)
    path = Path(a.binding)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise RuntimeError("binding must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    os.set_inheritable(fd, False)
    try:
        opened = os.fstat(fd)
        if (st.st_dev, st.st_ino, st.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            raise RuntimeError("binding identity changed before read")
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        raw = b"".join(chunks)
        after_fd, after_path = os.fstat(fd), path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_mode)
        if identity != (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_mode):
            raise RuntimeError("binding changed while reading")
        if identity != (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_mode):
            raise RuntimeError("binding path changed while reading")
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != opened.st_size or digest != a.binding_sha256:
            raise RuntimeError("binding length/hash differs from explicit grant")
    finally:
        os.close(fd)
    binding = json.loads(raw)
    expected = binding.get("runtime", {}).get("sys_path")
    if not isinstance(expected, list) or _INITIAL_SYS_PATH != expected:
        raise RuntimeError("isolated interpreter sys.path differs from bound runtime")
    harness = Path(binding["harness_root"]).resolve(strict=True)
    source = Path(binding["source"]["root"]).resolve(strict=True)
    if Path(__file__).resolve(strict=True).parent != harness or Path(a.binding).resolve(strict=True) != path.absolute():
        raise RuntimeError("binding or child path has an unbound symlink/root")
    roots = [*expected, str(harness), str(source)]
    if len(roots) != len(set(roots)):
        raise RuntimeError("duplicate import roots")
    sys.path[:] = roots
    sys.dont_write_bytecode = True
    return binding, {"path": str(path), "size_bytes": len(raw), "sha256": digest,
                    "device": opened.st_dev, "inode": opened.st_ino, "mode": opened.st_mode}


_BOOT_BINDING, _BOOT_BINDING_REF = _bootstrap_binding(sys.argv[1:])
from t038_support import (  # noqa: E402
    NotReady, PROVIDER_IMPORT_ROOTS, install_effect_guard,
    install_import_origin_guard, json_bytes, read_json_bound,
    verify_bound_files, verify_runtime_binding, verify_source_binding,
    write_new_verified,
)


def _install_stdio_loop_seam(on_loop: Any) -> dict[str, Any]:
    """Permit one self-pipe only inside AnyIO's exact Runner context entry."""
    import anyio._backends._asyncio as anyio_asyncio

    raw_enter = asyncio.Runner.__enter__
    raw_pair = socket.socketpair
    raw_loop_init = asyncio.selector_events.BaseSelectorEventLoop.__init__
    raw_send, raw_recv = socket.socket.send, socket.socket.recv
    anyio_run_code = anyio_asyncio.AsyncIOBackend.run.__code__
    self_pipe_code = asyncio.selector_events.BaseSelectorEventLoop._make_self_pipe.__code__
    state: dict[str, Any] = {"runner": None, "loop": None, "loop_init_count": 0,
                              "owner_thread": None, "armed": False,
                              "pair_count": 0, "fds": (),
                              "send_count": 0, "recv_count": 0}

    def guarded_pair(*args: Any, **kwargs: Any):
        frame = inspect.currentframe().f_back
        if (args or kwargs or not state["armed"] or state["pair_count"] or frame is None
                or frame.f_code is not self_pipe_code
                or threading.get_ident() != state["owner_thread"]
                or state["loop_init_count"] != 1):
            raise NotReady("unowned socketpair construction denied")
        loop = frame.f_locals.get("self")
        if loop is None or loop is not state["loop"]:
            raise NotReady("selector self-pipe does not match the reserved loop")
        pair = raw_pair()
        if len(pair) != 2 or any(s.family != socket.AF_UNIX or (s.type & 0xF) != socket.SOCK_STREAM for s in pair):
            for s in pair:
                s.close()
            raise NotReady("selector self-pipe is not an AF_UNIX stream pair")
        state.update(loop=loop, pair_count=1, ssock=pair[0], csock=pair[1], fds=tuple(s.fileno() for s in pair))
        return pair

    def tracked_loop_init(loop: Any, *args: Any, **kwargs: Any) -> None:
        if (not state["armed"] or threading.get_ident() != state["owner_thread"]
                or state["loop_init_count"] != 0 or state["loop"] is not None):
            raise NotReady("event-loop construction outside bound AnyIO Runner entry")
        state.update(loop=loop, loop_init_count=1)
        raw_loop_init(loop, *args, **kwargs)
        if (state["pair_count"] != 1 or state.get("ssock") is None or state.get("csock") is None
                or getattr(loop, "_ssock", None) is not state["ssock"]
                or getattr(loop, "_csock", None) is not state["csock"]):
            raise NotReady("selector did not retain exact admitted pair")

    def guarded_enter(runner: asyncio.Runner):
        frame = inspect.currentframe().f_back
        if (state["runner"] is not None or frame is None or frame.f_code is not anyio_run_code
                or not isinstance(runner, asyncio.Runner)):
            raise NotReady("Runner entry is outside the single production AnyIO backend run")
        state.update(runner=runner, owner_thread=threading.get_ident(), armed=True)
        socket.socketpair = guarded_pair
        try:
            entered = raw_enter(runner)
            loop = runner.get_loop()
            if entered is not runner or state["pair_count"] != 1 or state["loop"] is not loop:
                raise NotReady("AnyIO Runner did not own the one admitted selector pair")
            state["runner_id"] = id(runner)
            state["loop_type"] = f"{type(loop).__module__}.{type(loop).__qualname__}"
            return entered
        finally:
            state["armed"] = False
            socket.socketpair = raw_pair
            if state["loop"] is not None:
                on_loop(state["loop"])

    def guarded_send(sock: socket.socket, data: Any, *args: Any, **kwargs: Any) -> int:
        frame = inspect.currentframe().f_back
        method = getattr(getattr(state.get("loop"), "_write_to_self", None), "__func__", None)
        if sock is not state.get("csock") or frame is None or method is None or frame.f_code is not method.__code__ or data != b"\0":
            raise NotReady("unowned selector wakeup send denied")
        state["send_count"] += 1
        return raw_send(sock, data, *args, **kwargs)

    def guarded_recv(sock: socket.socket, size: int, *args: Any, **kwargs: Any) -> bytes:
        frame = inspect.currentframe().f_back
        method = getattr(getattr(state.get("loop"), "_read_from_self", None), "__func__", None)
        if sock is not state.get("ssock") or frame is None or method is None or frame.f_code is not method.__code__:
            raise NotReady("unowned selector self-pipe receive denied")
        state["recv_count"] += 1
        return raw_recv(sock, size, *args, **kwargs)

    asyncio.Runner.__enter__ = guarded_enter  # type: ignore[method-assign]
    socket.socketpair = guarded_pair
    socket.socket.send = guarded_send  # type: ignore[method-assign]
    socket.socket.recv = guarded_recv  # type: ignore[method-assign]
    asyncio.selector_events.BaseSelectorEventLoop.__init__ = tracked_loop_init  # type: ignore[method-assign]
    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.__new__":
            frame = inspect.currentframe()
            while frame is not None and frame.f_code is not guarded_pair.__code__:
                frame = frame.f_back
            if not (state["armed"] and frame is not None):
                raise NotReady("unowned socket construction denied")
    sys.addaudithook(audit)
    return state


class _QueueProxy:
    """Per-executor wrapper allowing audit of a built-in SimpleQueue.put."""
    def __init__(self, raw: Any, check: Any):
        self.raw, self.check = raw, check
    def put(self, item: Any, *args: Any, **kwargs: Any) -> Any:
        self.check(item)
        return self.raw.put(item, *args, **kwargs)
    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self.raw.get(*args, **kwargs)
    def get_nowait(self) -> Any:
        return self.raw.get_nowait()
    def empty(self) -> bool:
        return self.raw.empty()
    def qsize(self) -> int:
        return self.raw.qsize()


def _install_io_and_thread_guards(loop_state: dict[str, Any], files: dict[str, Any], ledger: list[dict[str, Any]], app: Any) -> dict[str, Any]:
    import _thread
    import anyio
    import anyio._backends._asyncio as anyio_asyncio

    state: dict[str, Any] = {"loop": loop_state.get("loop"), "workers": [], "executor_threads": [],
                             "shutdown_threads": [], "started": [], "joined": [],
                             "worker_submissions": [], "executor_submissions": [],
                             "shutdown_markers": 0, "pending_dispatch": None,
                             "dispatch_func": app._gateway.dispatcher}
    cls = anyio_asyncio.WorkerThread
    raw_worker_init, raw_worker_stop = cls.__init__, cls.stop
    def worker_init(worker: Any, *args: Any, **kwargs: Any) -> None:
        raw_worker_init(worker, *args, **kwargs)
        if worker.loop is not state["loop"] or len(state["workers"]) >= 2:
            raise NotReady("unexpected AnyIO worker or loop owner")
        state["workers"].append(worker)
    def worker_stop(worker: Any, *args: Any, **kwargs: Any) -> Any:
        if worker not in state["workers"]:
            raise NotReady("unowned AnyIO worker stop")
        state["worker_stop_called"] = True
        return raw_worker_stop(worker, *args, **kwargs)
    cls.__init__, cls.stop = worker_init, worker_stop

    raw_wrap = anyio.wrap_file
    def wrap_file(file: Any, *args: Any, **kwargs: Any):
        if not isinstance(file, io.TextIOWrapper):
            raise NotReady("SDK stdio is not a TextIOWrapper")
        role = "stdin" if file.buffer is sys.stdin.buffer else "stdout" if file.buffer is sys.stdout.buffer else None
        if role is None or role in files:
            raise NotReady("SDK wrapped an unowned or duplicate stdio stream")
        wrapped = raw_wrap(file, *args, **kwargs)
        if getattr(wrapped, "_fp", None) is not file:
            raise NotReady("AnyIO AsyncFile does not retain the SDK TextIOWrapper")
        files[role] = {"text": file, "async": wrapped, "text_id": id(file), "async_id": id(wrapped)}
        return wrapped
    anyio.wrap_file = wrap_file

    raw_put_nowait = queue.Queue.put_nowait
    def queue_put(q: Any, item: Any) -> None:
        if not any(w.queue is q for w in state["workers"]):
            raise NotReady("unowned AnyIO queue write denied")
        if item is None:
            if not any(w.stopping for w in state["workers"]):
                raise NotReady("AnyIO worker sentinel arrived before stop")
            state["worker_sentinels"] = state.get("worker_sentinels", 0) + 1
        else:
            if not isinstance(item, tuple) or len(item) != 5:
                raise NotReady("AnyIO work item has an unexpected shape")
            ctx, func, args, future, _scope = item
            target = getattr(func, "__self__", None)
            if not isinstance(ctx, contextvars.Context) or target not in [v["text"] for v in files.values()] or future.get_loop() is not state["loop"]:
                raise NotReady("AnyIO work item is not bound to captured SDK stdio")
            method = getattr(func, "__name__", "")
            role = next((n for n, v in files.items() if v["text"] is target), None)
            if (method, role, len(args)) not in {("readline", "stdin", 0), ("write", "stdout", 1), ("flush", "stdout", 0)}:
                raise NotReady("AnyIO task is outside stdio readline/write/flush")
            if method == "write" and (not isinstance(args[0], str) or not args[0].endswith("\n")):
                raise NotReady("MCP stdio writes must be one newline-terminated JSON frame")
            state["worker_submissions"].append({"role": role, "operation": method})
        return raw_put_nowait(q, item)
    queue.Queue.put_nowait = queue_put  # type: ignore[method-assign]

    raw_run_in_executor = asyncio.BaseEventLoop.run_in_executor
    raw_submit = concurrent.futures.ThreadPoolExecutor.submit
    wi_type = concurrent.futures.thread._WorkItem
    def check_work_item(executor: Any, item: Any) -> None:
        if item is None:
            if not state["loop"]._executor_shutdown_called:
                raise NotReady("executor sentinel arrived outside owned loop shutdown")
            state["shutdown_markers"] += 1
            return
        pending = state.get("pending_dispatch")
        if not isinstance(item, wi_type) or pending is None:
            raise NotReady("executor work item has no pending synthetic dispatch")
        if item.fn is not pending or item.args != () or item.kwargs != {}:
            raise NotReady("executor WorkItem differs from the exact submitted dispatch partial")
        state["work_items"] = state.get("work_items", 0) + 1
    def guarded_submit(executor: Any, fn: Any, /, *args: Any, **kwargs: Any):
        if executor is not getattr(state["loop"], "_default_executor", None):
            raise NotReady("only the owned asyncio default executor may submit work")
        pending = state.get("pending_dispatch")
        if pending is None or fn is not pending or args or kwargs:
            raise NotReady("executor submit is not the exact production dispatcher partial")
        raw_queue = executor._work_queue
        if not isinstance(raw_queue, _QueueProxy):
            executor._work_queue = _QueueProxy(raw_queue, lambda item: check_work_item(executor, item))
        state["executor_submissions"].append({"case": state["pending_case"], "operation": state["pending_operation"]})
        return raw_submit(executor, fn, *args, **kwargs)
    def run_in_executor(loop: Any, executor: Any, func: Any):
        if loop is not state["loop"] or executor is not None or not isinstance(func, functools.partial):
            raise NotReady("executor request is not production asyncio.to_thread")
        if not func.args or func.args[0] is not state["dispatch_func"]:
            raise NotReady("asyncio.to_thread target is not the injected dispatcher")
        if state.get("pending_dispatch") is not None:
            raise NotReady("nested dispatcher submission")
        state["pending_dispatch"] = func
        state["pending_case"] = (func.args[3] or {}).get("t038_case", "default")
        state["pending_operation"] = func.args[1]
        try:
            return raw_run_in_executor(loop, executor, func)
        finally:
            state["pending_dispatch"] = None
            state.pop("pending_case", None)
            state.pop("pending_operation", None)
    asyncio.BaseEventLoop.run_in_executor = run_in_executor  # type: ignore[method-assign]
    concurrent.futures.ThreadPoolExecutor.submit = guarded_submit  # type: ignore[method-assign]

    raw_start, raw_lowlevel = threading.Thread.start, _thread.start_new_thread
    authorized: dict[str, Any] = {"thread": None}
    def lowlevel(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any] | None = None):
        t = authorized.get("thread")
        if t is None or target != t._bootstrap or args != () or kwargs not in (None, {}):
            raise NotReady("unowned low-level thread creation denied")
        return raw_lowlevel(target, args, kwargs or {})
    _thread.start_new_thread = lowlevel
    threading._start_new_thread = lowlevel
    def start(thread: threading.Thread) -> None:
        loop = state["loop"]
        if isinstance(thread, cls) and thread in state["workers"] and thread.run.__func__ is cls.run and thread._target is None:
            role = "anyio-worker"
        elif (thread._target is concurrent.futures.thread._worker and len(thread._args) == 4
              and isinstance(thread._args[0], weakref.ReferenceType)
              and thread._args[0]() is loop._default_executor and thread._args[1] is loop._default_executor._work_queue
              and thread._args[2] is None and thread._args[3] == () and not state["executor_threads"]):
            role = "asyncio-default-executor"
        elif (getattr(thread._target, "__self__", None) is loop and getattr(thread._target, "__func__", None) is loop._do_shutdown.__func__
              and len(thread._args) == 1 and isinstance(thread._args[0], asyncio.Future)
              and thread._args[0].get_loop() is loop and not state["shutdown_threads"]):
            role = "runner-executor-shutdown"
        else:
            raise NotReady("thread target or arguments are outside bounded AnyIO/executor lifecycle")
        authorized["thread"] = thread
        try:
            raw_start(thread)
        finally:
            authorized["thread"] = None
        state["started"].append({"kind": role, "thread": thread, "ident": thread.ident, "native_id": thread.native_id})
        (state["workers"] if role == "anyio-worker" else state["executor_threads"] if role == "asyncio-default-executor" else state["shutdown_threads"]).append(thread) if role != "anyio-worker" else None
    threading.Thread.start = start  # type: ignore[method-assign]
    return state


def _synthetic_dispatcher(ledger: list[dict[str, Any]]):
    def dispatch(operation: str, arguments: dict[str, Any], execution: dict[str, Any]):
        marker = execution.get("t038_case", "unmarked") if isinstance(execution, dict) else "unmarked"
        ledger.append({"case": marker, "operation": operation, "argument_keys": sorted(arguments),
                       "thread_ident": threading.get_ident(), "thread_native_id": threading.get_native_id(),
                       "cursor": arguments.get("cursor") if marker == "business_cursor" else None})
        if marker == "call_refusal":
            return {"success": False, "error": {"code": "SYNTHETIC_REFUSAL", "message": "synthetic refusal", "safe_retry": False}, "data": {"t038_case": marker}}
        if marker == "call_unknown_or_cleanup":
            return {"success": True, "execution_state_unknown": True, "cleanup_failed": True, "data": {"t038_case": marker}}
        if marker == "call_partial":
            return {"success": False, "error": {"code": "SYNTHETIC_PARTIAL", "message": "partial synthetic result", "safe_retry": False}, "data": {"t038_case": marker, "status": "PARTIAL", "applied": ["synthetic-a"], "not_executed": ["synthetic-b"]}}
        if marker == "call_malformed_backend":
            return "{not-json"
        if marker == "call_exception_redaction":
            raise RuntimeError(_SENTINEL)
        if marker == "business_cursor":
            cursor = arguments.get("cursor") or ""
            if not cursor:
                return {"success": True, "data": {"operations": [{"operation_id": "synthetic/op-a"}], "next_cursor": "synthetic-cursor-1", "count": 1, "total": 2}}
            if cursor == "synthetic-cursor-1":
                return {"success": True, "data": {"operations": [{"operation_id": "synthetic/op-b"}], "next_cursor": None, "count": 1, "total": 2}}
            return {"success": False, "error": {"code": "INVALID_REQUEST", "message": "unexpected cursor", "safe_retry": False}, "data": {}}
        return {"success": True, "data": {"t038_case": marker, "synthetic": True}}
    return dispatch


def main() -> int:
    p = argparse.ArgumentParser()
    for name in ("binding", "binding-sha256", "observer", "observer-sha256", "results", "server-home"):
        p.add_argument("--" + name, required=True)
    a = p.parse_args()
    started = time.monotonic()
    startup_platform_key = "__CF_USER_TEXT_ENCODING"
    startup_platform_key_present = startup_platform_key in os.environ
    # Python on macOS may inject this startup key. Its value is neither read
    # nor recorded; all other environment keys remain subject to exact match.
    os.environ.pop(startup_platform_key, None)
    ledger: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    report: dict[str, Any] = {"schema": "T038_WIRE_CHILD_FINALIZATION_V2", "status": "NOT_RUN",
                              "scope": "actual MCP stdio with synthetic business dispatcher only",
                              "dispatcher_calls": ledger, "stdio": {}, "threads": {}, "loop": {}, "logs": []}
    rc = 1
    try:
        binding, binding_ref = read_json_bound(a.binding, a.binding_sha256)
        observer, observer_ref = read_json_bound(a.observer, a.observer_sha256)
        if observer.get("status") != "PASS_G01_METADATA_OBSERVER":
            raise NotReady("wire mode requires PASS source-bound G01 observer")
        src_ref, runtime_ref = verify_source_binding(binding), verify_runtime_binding(binding)
        inputs = [binding["expected_registry"], binding["source_selection"], binding["runtime_path_readback"], binding["environment_readback"], binding["entrypoint"], binding["gateway"],
                  *binding["sdk_files"], *binding.get("runtime_files", []), *binding["harness_files"]]
        file_refs = verify_bound_files(inputs)
        if (observer.get("source", {}).get("fingerprint") != src_ref["fingerprint"]
                or observer.get("expected_registry", {}).get("sha256") != binding["expected_registry"].get("sha256")):
            raise NotReady("observer and wire binding differ on source or static registry")
        source_root = Path(binding["source"]["root"]).resolve(strict=True)
        results = Path(a.results).resolve(strict=True)
        server_home = Path(a.server_home).absolute()
        attempt_root = results.parent.resolve(strict=True)
        for key, dirname in (("HOME", "home"), ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache"),
                             ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                             ("COMSOL_SERVER_MCP_HOME", "server-home")):
            expected_dir = attempt_root / dirname
            info = expected_dir.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or expected_dir.resolve(strict=True).parent != attempt_root:
                raise NotReady(f"derived environment path is not an attempt-owned directory: {key}")
            if os.environ.get(key) != str(expected_dir):
                raise NotReady(f"derived environment path differs from the parent-held attempt: {key}")
        if server_home != attempt_root / "server-home" or not server_home.is_dir() or server_home.is_symlink():
            raise NotReady("server home is not the held attempt-owned directory")
        expected_env = dict(binding["fixed_environment"])
        expected_env.update({"HOME": str(attempt_root / "home"), "TMPDIR": str(attempt_root / "tmp"),
                             "XDG_CACHE_HOME": str(attempt_root / "cache"), "XDG_CONFIG_HOME": str(attempt_root / "config"),
                             "XDG_DATA_HOME": str(attempt_root / "data"), "COMSOL_SERVER_MCP_HOME": str(server_home)})
        if dict(os.environ) != expected_env:
            raise NotReady("inherited child environment differs from frozen values and attempt-owned paths")
        os.environ["COMSOL_SERVER_MCP_HOME"] = str(server_home)
        read_roots = [*binding["runtime"]["sys_path"], str(source_root), binding["harness_root"], str(results), str(server_home)]
        # The import guard already bounds runtime module origins. These exact
        # bound input files are additionally permitted for explicit reads.
        read_roots.extend(row["path"] for row in inputs)
        install_import_origin_guard(read_roots, set(PROVIDER_IMPORT_ROOTS))
        install_effect_guard(read_roots, [str(results), str(server_home)])
        thread_state_holder: dict[str, Any] = {}
        def bind_actual_loop(loop: Any) -> None:
            state = thread_state_holder.get("state")
            if state is not None:
                if state.get("loop") is not None and state["loop"] is not loop:
                    raise NotReady("production thread guards observed a second event loop")
                state["loop"] = loop
        loop_state = _install_stdio_loop_seam(bind_actual_loop)
        gateway = importlib.import_module("comsol_mcp._mcp_gateway")
        raw_init = gateway.GatewayRegistry.__init__
        defaults = raw_init.__defaults__ or ()
        if len(defaults) != 1:
            raise NotReady("production GatewayRegistry constructor changed")
        production_dispatch = defaults[0]
        synthetic = _synthetic_dispatcher(ledger)
        def inject(registry: Any, mcp: Any, dispatcher: Any = production_dispatch) -> None:
            if dispatcher is not production_dispatch:
                raise NotReady("unexpected dispatcher at production gateway seam")
            raw_init(registry, mcp, dispatcher=synthetic)
        gateway.GatewayRegistry.__init__ = inject
        try:
            app = importlib.import_module("comsol_mcp.mcp_server")
        finally:
            gateway.GatewayRegistry.__init__ = raw_init
        if Path(app.__file__).resolve(strict=True) != source_root / "comsol_mcp" / "mcp_server.py" or app._gateway.dispatcher is not synthetic:
            raise NotReady("production entrypoint or synthetic dispatcher binding differs")
        thread_state = _install_io_and_thread_guards(loop_state, files, ledger, app)
        thread_state_holder["state"] = thread_state
        app.main()
        loop = loop_state.get("loop")
        for row in thread_state["started"]:
            thread = row["thread"]
            thread.join(timeout=5.0)
            thread_state["joined"].append({"kind": row["kind"], "ident": thread.ident, "is_alive": thread.is_alive()})
        for candidate in [*thread_state["executor_threads"], *thread_state["shutdown_threads"]]:
            if candidate not in [row["thread"] for row in thread_state["started"]]:
                candidate.join(timeout=5.0)
                thread_state["joined"].append({"kind": "owned-thread", "ident": candidate.ident, "is_alive": candidate.is_alive()})
        loop_ok = bool(loop and loop.is_closed() and loop._ssock is None and loop._csock is None)
        joins_ok = bool(thread_state["joined"]) and all(not row["is_alive"] for row in thread_state["joined"])
        required_io = {"readline", "write", "flush"}
        io_seen = {r["operation"] for r in thread_state["worker_submissions"]}
        if time.monotonic() - started > _MAX_SECONDS or not loop_ok or not joins_ok or not required_io <= io_seen:
            raise NotReady("owned loop/threads/real stdio work did not close within bounds")
        log_files: list[dict[str, Any]] = []
        for path in sorted(server_home.rglob("*")):
            if path.is_file() and not path.is_symlink():
                data, ref = __import__("t038_support").read_regular(path)
                log_files.append(ref)
                if _SENTINEL.encode() in data:
                    raise NotReady("exception sentinel leaked to production log")
        report.update({"status": "PASS_WIRE_CHILD_FINALIZATION", "binding": binding_ref, "observer": observer_ref,
                       "source": {"fingerprint": src_ref["fingerprint"], "member_count": src_ref["member_count"]},
                       "runtime": runtime_ref, "input_refs": file_refs, "startup_environment_normalization": {"key": startup_platform_key,"present_at_child_start": startup_platform_key_present,"value_read_or_recorded": False}, "environment": expected_env, "stdio": {k: {"text_wrapper_id": v["text_id"], "async_file_id": v["async_id"], "same_object_wrapped": v["async"]._fp is v["text"]} for k,v in files.items()},
                       "threads": {"started": [{k:v for k,v in r.items() if k!="thread"} for r in thread_state["started"]], "joined": thread_state["joined"], "worker_count": len(thread_state["workers"]), "worker_stop_called": bool(thread_state.get("worker_stop_called")), "worker_sentinels": thread_state.get("worker_sentinels",0), "worker_submissions": thread_state["worker_submissions"], "executor_submissions": thread_state["executor_submissions"], "executor_work_items": thread_state.get("work_items",0), "executor_shutdown_markers": thread_state["shutdown_markers"]},
                       "loop": {"loop_type": loop_state.get("loop_type"), "pair_count": loop_state["pair_count"], "fds": list(loop_state["fds"]), "send_count": loop_state["send_count"], "recv_count": loop_state["recv_count"], "closed": loop_ok}, "logs": log_files, "elapsed_seconds": round(time.monotonic()-started,6)})
        rc = 0
    except BaseException as exc:
        report.update({"status": "NOT_READY_WIRE_CHILD" if isinstance(exc, NotReady) else "FAIL_WIRE_CHILD",
                       "error": {"type": type(exc).__name__, "message": str(exc)[:1000]}, "elapsed_seconds": round(time.monotonic()-started,6)})
        sys.stderr.write(f"T038_WIRE_CHILD {report['status']} {type(exc).__name__}\n")
    try:
        trace = Path(a.results) / "wire-child-finalization.json"
        report_ref = write_new_verified(trace, json_bytes(report))
        sys.stderr.write("T038_WIRE_CHILD_REF " + json.dumps(report_ref, sort_keys=True) + "\n")
    except BaseException as exc:
        sys.stderr.write(f"T038_WIRE_CHILD_TRACE_FAILURE {type(exc).__name__}\n")
        return 3
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
