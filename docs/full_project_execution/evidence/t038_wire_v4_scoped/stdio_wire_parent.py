#!/usr/bin/env python3
"""Bounded parent for one actual MCP stdio server child.

The parent sends finite line-oriented JSON-RPC requests, validates every
returned line with the installed MCP JSONRPCMessage type, stores raw streams,
closes stdin, and records child PID/session/reap and immutable input refs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_INITIAL_SYS_PATH = list(sys.path)
_SENTINEL = 'T038_SENTINEL_DO_NOT_LEAK_20261004'
_MAX_WALL = 180
_MAX_OUT = 4 * 1024 * 1024
_MAX_ERR = 1024 * 1024
_MAX_TOOLS_LIST_PAGES = 8


def _read_binding(path: Path, expected: str) -> tuple[dict[str, Any], dict[str, Any]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("binding must be regular and non-symlink")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    os.set_inheritable(fd, False)
    try:
        st = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mode) != (st.st_dev, st.st_ino, st.st_mode):
            raise RuntimeError("binding changed before read")
        chunks: list[bytes] = []
        while True:
            b = os.read(fd, 1024 * 1024)
            if not b:
                break
            chunks.append(b)
        raw = b"".join(chunks)
        after, named = os.fstat(fd), path.lstat()
        identity = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_mode)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode) or identity != (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns, named.st_mode):
            raise RuntimeError("binding changed while read")
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != st.st_size or digest != expected:
            raise RuntimeError("binding length/hash differs")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("binding root is not an object")
        return value, {"path": str(path), "size_bytes": len(raw), "sha256": digest, "device": st.st_dev, "inode": st.st_ino, "mode": st.st_mode}
    finally:
        os.close(fd)


def _held_dir(path: Path, expected: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode) or path.resolve(strict=True) != path.absolute():
        raise RuntimeError(f"not a fixed real output directory: {path}")
    if (before.st_dev, before.st_ino, before.st_mode) != (expected.get("device"), expected.get("inode"), expected.get("mode")):
        raise RuntimeError("output parent identity differs from binding")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    os.set_inheritable(fd, False)
    opened = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
        os.close(fd)
        raise RuntimeError("output parent changed while opening")
    return fd, {"path": str(path), "device": opened.st_dev, "inode": opened.st_ino, "mode": opened.st_mode}


def _mkdir(fd: int, parent: Path, name: str) -> tuple[int, dict[str, Any]]:
    if not name or name in {".", ".."} or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in name):
        raise RuntimeError("unsafe directory name")
    os.mkdir(name, 0o700, dir_fd=fd)
    os.fsync(fd)
    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    child = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=fd)
    os.set_inheritable(child, False)
    held, named = os.fstat(child), (parent / name).lstat()
    if stat.S_ISLNK(named.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != (held.st_dev, held.st_ino, held.st_mode) or (info.st_dev, info.st_ino, info.st_mode) != (named.st_dev, named.st_ino, named.st_mode):
        os.close(child)
        raise RuntimeError("new output directory identity changed")
    return child, {"path": str(parent / name), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


class _Wire:
    def __init__(self, proc: subprocess.Popen[bytes], message_type: Any, timeout: float):
        assert proc.stdin and proc.stdout and proc.stderr
        self.proc, self.message_type, self.deadline = proc, message_type, time.monotonic() + timeout
        self.selector = selectors.DefaultSelector()
        self.selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stderr.fileno(), False)
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.pending = bytearray()
        self.frames: list[dict[str, Any]] = []
        self.transcript: list[dict[str, Any]] = []
        self.stderr_eof = False
        self.stdout_eof = False

    def send(self, obj: dict[str, Any]) -> None:
        raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()
        self.transcript.append({"direction": "client_to_server", "frame": obj})

    def send_raw(self, raw: bytes) -> None:
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()
        self.transcript.append({"direction": "client_to_server_raw", "base64": __import__("base64").b64encode(raw).decode("ascii")})

    def _consume_line(self, line: bytes) -> dict[str, Any]:
        model = self.message_type.model_validate_json(line)
        value = model.model_dump(mode="json", exclude_none=True)
        if not isinstance(value, dict):
            raise RuntimeError("MCP model did not normalize a JSON object frame")
        self.frames.append(value)
        self.transcript.append({"direction": "server_to_client", "frame": value})
        return value

    def response(self, request_id: Any) -> dict[str, Any]:
        while time.monotonic() < self.deadline:
            while True:
                pos = self.pending.find(b"\n")
                if pos < 0:
                    break
                line = bytes(self.pending[:pos])
                del self.pending[:pos + 1]
                if not line:
                    raise RuntimeError("unexpected blank stdout line; protocol stdout must be JSON lines")
                value = self._consume_line(line)
                if value.get("id") == request_id:
                    return value
            wait = max(0.0, min(0.25, self.deadline - time.monotonic()))
            for key, _ in self.selector.select(wait):
                name = key.data
                stream = key.fileobj
                try:
                    block = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not block:
                    if name == "stdout":
                        self.stdout_eof = True
                    else:
                        self.stderr_eof = True
                    self.selector.unregister(stream)
                    continue
                target = self.stdout if name == "stdout" else self.stderr
                cap = _MAX_OUT if name == "stdout" else _MAX_ERR
                if len(target) + len(block) > cap:
                    raise RuntimeError(f"{name} cap exceeded")
                target.extend(block)
                if name == "stdout":
                    self.pending.extend(block)
            if self.proc.poll() is not None and self.stdout_eof:
                break
        raise TimeoutError(f"no MCP response for request id {request_id!r}")

    def drain(self) -> None:
        while self.selector.get_map() and time.monotonic() < self.deadline:
            for key, _ in self.selector.select(min(.25, max(0.0, self.deadline - time.monotonic()))):
                stream, name = key.fileobj, key.data
                try:
                    block = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not block:
                    if name == "stdout": self.stdout_eof = True
                    else: self.stderr_eof = True
                    self.selector.unregister(stream)
                    continue
                target, cap = (self.stdout, _MAX_OUT) if name == "stdout" else (self.stderr, _MAX_ERR)
                if len(target) + len(block) > cap:
                    raise RuntimeError(f"{name} cap exceeded during shutdown")
                target.extend(block)
                if name == "stdout": self.pending.extend(block)
        if self.pending:
            if not self.pending.endswith(b"\n"):
                raise RuntimeError("trailing partial stdout frame")
            while self.pending:
                pos = self.pending.find(b"\n")
                if pos < 0: break
                line = bytes(self.pending[:pos]); del self.pending[:pos+1]
                if line: self._consume_line(line)
        self.selector.close()


def _case(wire: _Wire, seq: int, name: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    rid = f"t038-{seq:02d}"
    wire.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params})
    return seq, wire.response(rid)


def _call(name: str, arguments: Any) -> dict[str, Any]:
    return {"name": name, "arguments": arguments}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--binding", required=True); p.add_argument("--binding-sha256", required=True)
    p.add_argument("--observer", required=True); p.add_argument("--observer-sha256", required=True)
    p.add_argument("--attempt-name", required=True); p.add_argument("--timeout-seconds", type=int, default=_MAX_WALL)
    a = p.parse_args()
    if not 1 <= a.timeout_seconds <= _MAX_WALL: p.error("timeout must be 1..180 seconds")
    result: dict[str, Any] = {"schema": "T038_WIRE_PARENT_RESULT_V2", "status": "NOT_READY", "wire_cases": [], "acceptance": {"wire": "NOT_RUN"}}
    proc = None; out_fd = attempt_fd = results_fd = None; wire = None
    started = time.monotonic(); rc = 2
    try:
        binding_path = Path(a.binding)
        binding, binding_ref = _read_binding(binding_path, a.binding_sha256)
        runtime = binding["runtime"]
        if _INITIAL_SYS_PATH != runtime.get("sys_path") or str(Path(sys.executable).resolve(strict=True)) != str(Path(runtime["executable"]).resolve(strict=True)):
            raise RuntimeError("parent interpreter does not match bound runtime")
        harness = Path(binding["harness_root"]).resolve(strict=True)
        if Path(__file__).resolve(strict=True).parent != harness:
            raise RuntimeError("parent runner outside bound harness root")
        sys.path[:] = [*runtime["sys_path"], str(harness), str(Path(binding["source"]["root"]).resolve(strict=True))]
        from t038_support import NotReady, json_bytes, read_json_bound, verify_bound_files, verify_source_binding, verify_runtime_binding, write_new_verified
        if binding.get("schema") != "T038_CURRENT_BINDING_V1": raise NotReady("wrong binding schema")
        source_ref, runtime_ref = verify_source_binding(binding), verify_runtime_binding(binding)
        fixed_files = [binding["expected_registry"], binding["source_selection"], binding["runtime_path_readback"], binding["environment_readback"], binding["entrypoint"], binding["gateway"], *binding["sdk_files"], *binding.get("runtime_files", []), *binding["harness_files"]]
        fixed_before = verify_bound_files(fixed_files)
        observer, observer_ref = read_json_bound(a.observer, a.observer_sha256)
        if (observer.get("status") != "PASS_G01_METADATA_OBSERVER"
                or observer.get("source", {}).get("fingerprint") != source_ref["fingerprint"]
                or observer.get("expected_registry", {}).get("sha256") != binding["expected_registry"].get("sha256")):
            raise NotReady("observer is not PASS or its source/static registry differs")
        parent_info = binding["output_parent"]
        out_fd, parent_ref = _held_dir(Path(parent_info["path"]).resolve(strict=True), parent_info)
        attempt_fd, attempt_ref = _mkdir(out_fd, Path(parent_ref["path"]), a.attempt_name)
        attempt_path = Path(attempt_ref["path"])
        results_fd, results_ref = _mkdir(attempt_fd, attempt_path, "results")
        server_fd, server_ref = _mkdir(attempt_fd, attempt_path, "server-home")
        os.close(server_fd)
        results_path, home_path = Path(results_ref["path"]), Path(server_ref["path"])
        derived = {"HOME": "home", "TMPDIR": "tmp", "XDG_CACHE_HOME": "cache", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data"}
        derived_refs: dict[str, Any] = {"results": results_ref, "server_home": server_ref}
        clean_env = dict(binding["fixed_environment"])
        for key, name in derived.items():
            dfd, ref = _mkdir(attempt_fd, attempt_path, name); os.close(dfd); derived_refs[name] = ref; clean_env[key] = ref["path"]
        clean_env["COMSOL_SERVER_MCP_HOME"] = str(home_path)
        child = harness / "stdio_wire_child.py"
        child_row = [x for x in binding["harness_files"] if Path(x["path"]).resolve(strict=True) == child]
        parent_row = [x for x in binding["harness_files"] if Path(x["path"]).resolve(strict=True) == Path(__file__).resolve(strict=True)]
        if len(child_row) != 1 or len(parent_row) != 1: raise NotReady("binding does not hash exactly one parent and child")
        command = [runtime["executable"], "-I", "-B", str(child), "--binding", str(binding_path), "--binding-sha256", a.binding_sha256,
                   "--observer", a.observer, "--observer-sha256", a.observer_sha256, "--results", str(results_path), "--server-home", str(home_path)]
        proc = subprocess.Popen(command, cwd=str(attempt_path), env=clean_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        session_id = os.getsid(proc.pid)
        from mcp.types import JSONRPCMessage, LATEST_PROTOCOL_VERSION
        # Reserve a bounded tail for kill/reap, final pipe drain, and immutable
        # evidence writes inside the overall parent budget.
        wire = _Wire(proc, JSONRPCMessage, float(max(1, a.timeout_seconds - 8)))
        def request(rid: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            wire.send({"jsonrpc":"2.0", "id":rid, "method":method, **({"params":params} if params is not None else {})})
            return wire.response(rid)
        init = request(1, "initialize", {"protocolVersion":LATEST_PROTOCOL_VERSION, "capabilities":{}, "clientInfo":{"name":"t038-synthetic-harness","version":"1"}})
        wire.send({"jsonrpc":"2.0", "method":"notifications/initialized"})
        page = request("list-1", "tools/list", {})
        cursor_probe = request("list-cursor-probe", "tools/list", {"cursor":"t038-cursor-probe"})
        pages = [page]
        first_page_result = page.get("result")
        list_pages_valid = isinstance(first_page_result, dict) and isinstance(first_page_result.get("tools"), list)
        actual_tools = list(first_page_result.get("tools", [])) if isinstance(first_page_result, dict) and isinstance(first_page_result.get("tools"), list) else []
        cursors: list[str] = []
        seen_cursors: set[str] = set()
        duplicate_cursors: list[str] = []
        invalid_cursor = False
        page_limit_reached = False
        next_cursor = first_page_result.get("nextCursor") if isinstance(first_page_result, dict) else None
        while next_cursor is not None:
            if not isinstance(next_cursor, str):
                invalid_cursor = True
                break
            if next_cursor in seen_cursors:
                duplicate_cursors.append(next_cursor)
                break
            if len(pages) >= _MAX_TOOLS_LIST_PAGES:
                page_limit_reached = True
                break
            seen_cursors.add(next_cursor)
            cursors.append(next_cursor)
            pg = request(f"list-page-{len(pages)+1}", "tools/list", {"cursor":next_cursor})
            pages.append(pg)
            pg_result = pg.get("result")
            if not isinstance(pg_result, dict) or not isinstance(pg_result.get("tools"), list):
                list_pages_valid = False
            else:
                actual_tools.extend(pg_result["tools"])
            next_cursor = pg_result.get("nextCursor") if isinstance(pg_result, dict) else None
        tool_names = [tool.get("name") for tool in actual_tools if isinstance(tool, dict)]
        invalid_tool_rows = len(tool_names) != len(actual_tools) or any(not isinstance(name, str) or not name for name in tool_names)
        duplicate_names = sorted({name for name in tool_names if isinstance(name, str) and tool_names.count(name) > 1})
        list_complete = next_cursor is None and not invalid_cursor and list_pages_valid
        baseline_tools = observer.get("tools", [])
        tool_set_match: bool | None = None
        if list_complete and not invalid_tool_rows and not duplicate_names:
            normalize = lambda rows: sorted(rows, key=lambda r: r.get("name", ""))
            tool_set_match = (len(actual_tools) == 112 and len(baseline_tools) == 112
                              and normalize(actual_tools) == normalize(baseline_tools))
        tools_by_name = {t.get("name"):t for t in actual_tools if isinstance(t,dict)}
        if "server_info" not in tools_by_name or "registry_list" not in tools_by_name or "validate.expressions" not in tools_by_name:
            raise NotReady("required real tools absent from actual tools/list")
        call_results: list[dict[str, Any]] = []
        seq = 2
        def do_case(case: str, name: str, args_obj: Any) -> dict[str, Any]:
            nonlocal seq
            seq += 1
            _, resp = _case(wire, seq, case, _call(name, args_obj))
            rec = {"case":case,"tool":name,"response":resp}
            call_results.append(rec)
            return resp
        for case in ("call_success","call_refusal","call_unknown_or_cleanup","call_partial","call_malformed_backend","call_exception_redaction"):
            do_case(case, "server_info", {"execution":{"t038_case":case}})
        req_tool = next((t for t in actual_tools if isinstance(t, dict) and t.get("inputSchema",{}).get("required")), None)
        required_tool_lookup_source = "aggregated_tools_list"
        missing_required_case_not_run = req_tool is None
        if req_tool is None:
            required_tool_lookup_source = "aggregated_tools_list_no_required_tool"
            call_results.append({"case":"missing_required","tool":None,"status":"NOT_RUN","reason":"no required-argument tool was returned in the aggregated tools/list pages"})
        else:
            missing_args = {"execution":{"t038_case":"missing_required"}} if "execution" in req_tool.get("inputSchema",{}).get("properties",{}) else {}
            do_case("missing_required", req_tool["name"], missing_args)
        do_case("extra_top_level", "server_info", {"execution":{"t038_case":"extra_top_level"},"t038_unexpected_top_level":True})
        do_case("null_arguments", "server_info", None)
        # experiment_stage_define has an observed strict nested schema; its
        # invalid nested stage includes an unknown key and omits required
        # fields. This must fail SDK argument validation before dispatch.
        do_case("nested_invalid", "experiment_stage_define", {"execution":{"t038_case":"nested_invalid"},"definition":{"version":2,"stages":[{"stage_id":"synthetic","t038_unexpected_nested":True}]}})
        do_case("unknown_tool", "t038_absent_tool_20261004", {"execution":{"t038_case":"unknown_tool"}})
        wire.send_raw(b"{malformed-t038-frame\n")
        ping = request("ping-after-malformed", "ping", {})
        first = do_case("business_cursor", "registry_list", {"limit":1,"cursor":"","execution":{"t038_case":"business_cursor"}})
        structured = first.get("result",{}).get("structuredContent",{})
        business_cursor = (((structured.get("data") or {}).get("next_cursor")) if isinstance(structured,dict) else None)
        second = do_case("business_cursor_followup", "registry_list", {"limit":1,"cursor":business_cursor or "","execution":{"t038_case":"business_cursor"}})
        proc.stdin.close()
        wire.drain()
        try: returncode = proc.wait(timeout=max(0.1, min(8.0, wire.deadline-time.monotonic()))); reaped=True
        except subprocess.TimeoutExpired: proc.kill(); returncode=proc.wait(timeout=5); reaped=True
        trace_path = results_path / "wire-child-finalization.json"
        child_trace = None; child_trace_ref = None
        try: child_trace, child_trace_ref = read_json_bound(trace_path, _sha256_file(trace_path))
        except BaseException: pass
        raw_ref = write_new_verified(results_path/"wire-stdout.bin", bytes(wire.stdout))
        stderr_ref = write_new_verified(results_path/"wire-stderr.bin", bytes(wire.stderr))
        transcript_ref = write_new_verified(results_path/"wire-transcript.json", json_bytes(wire.transcript))
        if _SENTINEL in wire.stdout.decode("utf-8", "replace") or _SENTINEL in wire.stderr.decode("utf-8", "replace"):
            sentinel_absent = False
        else: sentinel_absent = True
        from t038_support import read_regular
        log_refs_current = []
        sentinel_logs_absent = bool(child_trace)
        for log_ref in (child_trace or {}).get("logs",[]):
            log_data, log_current = read_regular(log_ref["path"])
            log_refs_current.append(log_current)
            sentinel_logs_absent &= log_current["sha256"] == log_ref["sha256"] and _SENTINEL.encode() not in log_data
        dispatcher = (child_trace or {}).get("dispatcher_calls", [])
        case_counts = {name:sum(1 for c in dispatcher if c.get("case")==name) for name in ("call_success","call_refusal","call_unknown_or_cleanup","call_partial","call_malformed_backend","call_exception_redaction","business_cursor","business_cursor_followup","missing_required","extra_top_level","nested_invalid","unknown_tool","unmarked")}
        case_matrix = {r["case"]:{"isError":r.get("response",{}).get("result",{}).get("isError"),"structuredContent":r.get("response",{}).get("result",{}).get("structuredContent"),"response_error":r.get("response",{}).get("error")} for r in call_results}
        structured = lambda name: case_matrix.get(name,{}).get("structuredContent") or {}
        source_after = verify_source_binding(binding); fixed_after = verify_bound_files(fixed_files)
        binding_after, binding_after_ref = _read_binding(binding_path, a.binding_sha256)
        dirs_stable = True
        for fd,path,ref in ((out_fd,Path(parent_ref["path"]),parent_ref),(attempt_fd,attempt_path,attempt_ref),(results_fd,results_path,results_ref)):
            fs,named=os.fstat(fd),path.lstat()
            ident=(ref["device"],ref["inode"],ref["mode"])
            dirs_stable &= (fs.st_dev,fs.st_ino,fs.st_mode)==ident and (named.st_dev,named.st_ino,named.st_mode)==ident and not stat.S_ISLNK(named.st_mode)
        for ref in derived_refs.values():
            named=Path(ref["path"]).lstat()
            dirs_stable &= stat.S_ISDIR(named.st_mode) and not stat.S_ISLNK(named.st_mode) and (named.st_dev,named.st_ino,named.st_mode)==(ref["device"],ref["inode"],ref["mode"])
        child_ok=bool(child_trace and child_trace.get("status")=="PASS_WIRE_CHILD_FINALIZATION" and proc.returncode==0 and wire.stdout_eof and wire.stderr_eof and session_id==proc.pid and reaped)
        tool_errors=lambda name: (case_matrix.get(name,{}).get("response_error") is not None or case_matrix.get(name,{}).get("isError") is True)
        second_data = structured("business_cursor_followup").get("data", {})
        second_ops = second_data.get("operations", []) if isinstance(second_data,dict) else []
        extra_count = case_counts.get("extra_top_level", 0)
        extra_success = (not tool_errors("extra_top_level")
                         and case_matrix.get("extra_top_level", {}).get("isError") is False
                         and structured("extra_top_level").get("data", {}).get("t038_case") == "extra_top_level")
        extra_rejected = tool_errors("extra_top_level")
        extra_behavior = ((extra_count == 0 and extra_rejected)
                          or (extra_count == 1 and extra_success))
        extra_outcome = ("REJECTED_BEFORE_DISPATCH" if extra_count == 0 and extra_rejected
                         else "IGNORED_AND_DISPATCHED" if extra_count == 1 and extra_success
                         else "INCONSISTENT")
        checks={
            "initialize": init.get("result",{}).get("protocolVersion")==LATEST_PROTOCOL_VERSION,
            "tools_list_pages_valid": list_pages_valid,
            "tools_list_complete": list_complete,
            "tools_list_no_duplicate_names": not duplicate_names and not invalid_tool_rows,
            "tools_list_no_duplicate_cursors": not duplicate_cursors,
            "tools_list_within_page_limit": not page_limit_reached,
            "tools_list_matches_g01": tool_set_match is True,
            "all_cases_received": len(call_results)==13 and all(isinstance(c.get("response"), dict) for c in call_results),
            "synthetic_case_coverage": all(case_counts.get(x,0)==1 for x in ("call_success","call_refusal","call_unknown_or_cleanup","call_partial","call_malformed_backend","call_exception_redaction")) and case_counts.get("business_cursor")==2,
            "extra_top_level_observed_behavior": extra_behavior,
            "server_info_success": case_matrix.get("call_success",{}).get("isError") is False and structured("call_success").get("data",{}).get("t038_case")=="call_success",
            "refusal_is_error": case_matrix.get("call_refusal",{}).get("isError") is True and structured("call_refusal").get("error",{}).get("code")=="SYNTHETIC_REFUSAL",
            "unknown_cleanup_is_error": case_matrix.get("call_unknown_or_cleanup",{}).get("isError") is True and structured("call_unknown_or_cleanup").get("cleanup_failed") is True and structured("call_unknown_or_cleanup").get("execution_state_unknown") is True,
            "partial_is_error": case_matrix.get("call_partial",{}).get("isError") is True and structured("call_partial").get("data",{}).get("applied")==["synthetic-a"] and structured("call_partial").get("data",{}).get("not_executed")==["synthetic-b"],
            "malformed_backend_is_error": case_matrix.get("call_malformed_backend",{}).get("isError") is True and structured("call_malformed_backend").get("error")=="Invalid backend result",
            "exception_is_redacted": sentinel_absent and sentinel_logs_absent and case_matrix.get("call_exception_redaction",{}).get("isError") is True and _SENTINEL not in json.dumps(case_matrix.get("call_exception_redaction",{}),ensure_ascii=False),
            "missing_required_rejected_before_dispatch": (not missing_required_case_not_run
                and tool_errors("missing_required") and case_counts.get("missing_required",0)==0),
            "nested_invalid_rejected_before_dispatch": tool_errors("nested_invalid") and case_counts.get("nested_invalid",0)==0,
            "unknown_tool_rejected": tool_errors("unknown_tool") and case_counts.get("unknown_tool",0)==0,
            "malformed_frame_ping_continues": isinstance(ping.get("result"),dict),
            "business_cursor_two_pages": business_cursor=="synthetic-cursor-1" and case_counts.get("business_cursor")==2 and isinstance(second_ops,list) and len(second_ops)==1 and second_ops[0].get("operation_id")=="synthetic/op-b",
            "child_shutdown": child_ok,
            "inputs_revalidated": source_after==source_ref and fixed_after==fixed_before and binding_after==binding and binding_after_ref==binding_ref,
            "directories_stable": dirs_stable,
        }
        all_checks=all(checks.values())
        incomplete_other_checks_ok = all(value for key, value in checks.items()
            if not key.startswith("tools_list_") and key not in {"all_cases_received", "missing_required_rejected_before_dispatch"})
        incomplete_case_state_ok = (checks["all_cases_received"] and checks["missing_required_rejected_before_dispatch"]
            or (missing_required_case_not_run and page_limit_reached))
        incomplete_list_open = (incomplete_other_checks_ok and incomplete_case_state_ok and page_limit_reached and not duplicate_names
                                and not invalid_tool_rows and not duplicate_cursors and not invalid_cursor
                                and list_pages_valid)
        status = ("PASS_WIRE_PARENT_CAPTURE" if all_checks else
                  "INCOMPLETE_WIRE_PARENT_CAPTURE" if incomplete_list_open else
                  "FAIL_WIRE_PARENT_CAPTURE")
        if not cursors:
            pagination_status = "OPEN_NOT_ADVERTISED_SINGLE_PAGE_COMPLETE" if list_complete else "INVALID_OR_INCOMPLETE"
            multipage_acceptance = "OPEN"
        elif list_complete:
            pagination_status = "OBSERVED_MULTI_PAGE_COMPLETE"
            multipage_acceptance = "PASS_OBSERVED"
        elif page_limit_reached:
            pagination_status = "OPEN_PAGE_LIMIT_INCOMPLETE"
            multipage_acceptance = "OPEN_INCOMPLETE"
        elif duplicate_cursors:
            pagination_status = "FAIL_DUPLICATE_CURSOR"
            multipage_acceptance = "FAIL"
        else:
            pagination_status = "INVALID_OR_INCOMPLETE"
            multipage_acceptance = "FAIL"
        wire_acceptance = "PASS" if all_checks else "OPEN_INCOMPLETE" if incomplete_list_open else "FAIL"
        result.update({"status":status,"binding":binding_ref,"source":{"fingerprint":source_ref["fingerprint"],"member_count":source_ref["member_count"]},"runtime":runtime_ref,"observer":observer_ref,"output_parent":parent_ref,"attempt_directory":attempt_ref,"results_directory":results_ref,"derived_directories":derived_refs,"child":{"pid":proc.pid,"session_id":session_id,"session_is_pid":session_id==proc.pid,"returncode":proc.returncode,"reaped":reaped,"stdout_eof":wire.stdout_eof,"stderr_eof":wire.stderr_eof,"raw_stdout":raw_ref,"raw_stderr":stderr_ref,"transcript":transcript_ref,"child_trace":child_trace_ref},"tools_list":{"observed_tool_count":len(actual_tools),"expected_tool_count":112,"complete":list_complete,"matches_g01":tool_set_match,"first_page_cursor":first_page_result.get("nextCursor") if isinstance(first_page_result,dict) else None,"cursor_probe_response":cursor_probe,"observed_pages":len(pages),"page_limit":_MAX_TOOLS_LIST_PAGES,"followed_cursors":cursors,"duplicate_cursors":duplicate_cursors,"duplicate_names":duplicate_names,"invalid_tool_rows":invalid_tool_rows,"incomplete_cursor":next_cursor if next_cursor is not None else None,"page_limit_reached":page_limit_reached,"pagination_status":pagination_status,"required_tool_lookup_source":required_tool_lookup_source},"wire_cases":call_results,"case_counts":case_counts,"case_matrix":case_matrix,"extra_top_level_observation":{"outcome":extra_outcome,"dispatch_count":extra_count,"response_error":case_matrix.get("extra_top_level",{}).get("response_error"),"isError":case_matrix.get("extra_top_level",{}).get("isError")},"log_refs_revalidated":log_refs_current,"checks":checks,"acceptance":{"wire":wire_acceptance,"mcp_tools_list_multipage":multipage_acceptance,"original50_compatibility":"OPEN","28_schema_deltas":"OPEN","T037_OS_isolation":"NOT_ESTABLISHED","COMSOL_JVM_native":"NOT_IN_SCOPE","scientific_acceptance":"NOT_IN_SCOPE"},"input_refs_before":fixed_before,"input_refs_after":fixed_after,"input_revalidated":checks["inputs_revalidated"],"elapsed_seconds":round(time.monotonic()-started,6)})
        rc=0 if status=="PASS_WIRE_PARENT_CAPTURE" else 2
    except BaseException as exc:
        result.update({"status":"NOT_READY_WIRE_PARENT" if type(exc).__name__ in {"NotReady","FileNotFoundError"} else "FAIL_WIRE_PARENT","error":{"type":type(exc).__name__,"message":str(exc)[:1000]}})
        if not ("attempt_path" in locals() and "results_path" in locals()):
            error = result.get("error", {})
            diagnostic = {
                "schema": "T038_WIRE_PARENT_PREATTEMPT_FAILURE_V1",
                "status": str(result.get("status", "FAIL_WIRE_PARENT"))[:64],
                "error": {
                    "type": str(error.get("type", "Unknown"))[:64],
                    "message": str(error.get("message", ""))[:256],
                },
            }
            line = "T038_WIRE_PARENT_PREATTEMPT_FAILURE " + json.dumps(diagnostic, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
            try:
                sys.stderr.write(line)
                sys.stderr.flush()
            except OSError:
                pass
        if proc is not None:
            if proc.poll() is None:
                try:
                    if proc.stdin and not proc.stdin.closed: proc.stdin.close()
                except Exception: pass
                proc.kill()
            try:
                result["child_returncode_after_failure"] = proc.wait(timeout=5)
                result["child_reaped_after_failure"] = True
            except subprocess.TimeoutExpired:
                result["child_reap_failed"] = True; rc=3
        if wire is not None and "results_path" in locals():
            try:
                wire.deadline = time.monotonic() + 5.0
                wire.drain()
            except BaseException as capture_exc:
                result["partial_pipe_drain_error"] = {"type":type(capture_exc).__name__,"message":str(capture_exc)[:300]}
            try:
                result["partial_raw_stdout"] = write_new_verified(results_path/"wire-partial-stdout.bin", bytes(wire.stdout))
                result["partial_raw_stderr"] = write_new_verified(results_path/"wire-partial-stderr.bin", bytes(wire.stderr))
                result["partial_transcript"] = write_new_verified(results_path/"wire-partial-transcript.json", __import__("t038_support").json_bytes(wire.transcript))
            except BaseException as capture_exc:
                result["partial_capture_write_error"] = {"type":type(capture_exc).__name__,"message":str(capture_exc)[:300]}
    finally:
        if wire is not None:
            try: wire.selector.close()
            except Exception: pass
    try:
        if "attempt_path" in locals() and "results_path" in locals():
            parent_result_ref=write_new_verified(attempt_path/"wire-parent-result.json", __import__("t038_support").json_bytes(result))
            result["parent_result_ref"]=parent_result_ref
            sys.stdout.buffer.write(b"T038_WIRE_PARENT_RESULT_REF "+json.dumps(parent_result_ref,sort_keys=True).encode()+b"\n")
            sys.stdout.buffer.flush()
            if "binding" in locals() and "fixed_files" in locals() and "source_ref" in locals() and "fixed_before" in locals() and "binding_ref" in locals():
                finalization={"schema":"T038_WIRE_FINALIZATION_REVALIDATION_V2","status":"NOT_READY","parent_result":parent_result_ref}
                try:
                    final_source=verify_source_binding(binding)
                    final_files=verify_bound_files(fixed_files)
                    final_binding, final_binding_ref=_read_binding(binding_path,a.binding_sha256)
                    result_bytes,result_file_ref=__import__("t038_support").read_regular(attempt_path/"wire-parent-result.json")
                    input_ok=final_source==source_ref and final_files==fixed_before and final_binding==binding and final_binding_ref==binding_ref
                    parent_ok=result_file_ref["sha256"]==parent_result_ref["sha256"] and result_file_ref["size_bytes"]==parent_result_ref["size_bytes"]
                    directory_ok=True
                    for held_fd,held_path,held_ref in ((out_fd,Path(parent_ref["path"]),parent_ref),(attempt_fd,attempt_path,attempt_ref),(results_fd,results_path,results_ref)):
                        if held_fd is None: directory_ok=False; continue
                        fs,named=os.fstat(held_fd),held_path.lstat()
                        identity=(held_ref["device"],held_ref["inode"],held_ref["mode"])
                        directory_ok &= (fs.st_dev,fs.st_ino,fs.st_mode)==identity and (named.st_dev,named.st_ino,named.st_mode)==identity and not stat.S_ISLNK(named.st_mode)
                    for derived_ref in derived_refs.values():
                        named=Path(derived_ref["path"]).lstat()
                        directory_ok &= stat.S_ISDIR(named.st_mode) and not stat.S_ISLNK(named.st_mode) and (named.st_dev,named.st_ino,named.st_mode)==(derived_ref["device"],derived_ref["inode"],derived_ref["mode"])
                    finalization.update({"status":"PASS_WIRE_POST_TERMINAL_REVALIDATION" if input_ok and parent_ok and directory_ok else "FAIL_WIRE_POST_TERMINAL_REVALIDATION","inputs_unchanged":input_ok,"parent_result_unchanged":parent_ok,"directories_stable":directory_ok,"source_after":final_source,"inputs_after":final_files,"binding_after":final_binding_ref,"parent_result_after":result_file_ref})
                except BaseException as final_exc:
                    finalization.update({"status":"FAIL_WIRE_POST_TERMINAL_REVALIDATION","error":{"type":type(final_exc).__name__,"message":str(final_exc)[:500]}})
                finalization_ref=write_new_verified(attempt_path/"wire-finalization-revalidation.json",__import__("t038_support").json_bytes(finalization))
                sys.stdout.buffer.write(b"T038_WIRE_FINALIZATION_REF "+json.dumps(finalization_ref,sort_keys=True).encode()+b"\n")
                sys.stdout.buffer.flush()
                if finalization["status"]!="PASS_WIRE_POST_TERMINAL_REVALIDATION": rc=3
    except BaseException as exc:
        sys.stderr.write(f"T038_WIRE_PARENT_RESULT_FAILURE {type(exc).__name__}\n"); rc=3
    for fd in (results_fd,attempt_fd,out_fd):
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
    if result.get("status")!="PASS_WIRE_PARENT_CAPTURE": sys.stderr.write(f"T038_WIRE_PARENT {result.get('status')}\n")
    return rc


def _sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()


if __name__=="__main__": raise SystemExit(main())
