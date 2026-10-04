#!/usr/bin/env python3
"""Single-child bounded launcher for prospective T038 checks.

Only ``--mode g01`` is currently implemented. This parent never imports the
candidate package. It owns the one child process and preserves raw pipe bytes,
process/session/reap facts, and held output-directory identities.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_INITIAL_SYS_PATH = list(sys.path)
_MAX_STDOUT = 4 * 1024 * 1024
_MAX_STDERR = 1024 * 1024
_MAX_WALL_SECONDS = 180
_GO_SCHEMA = "T038_G01_ONE_USE_RUNTIME_GO_V1"
_GO_SCOPE = "G01_METADATA_OBSERVER_ONLY"


def _read_binding(path: Path, expected: str) -> tuple[dict[str, Any], dict[str, Any]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError("binding must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    os.set_inheritable(fd, False)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            raise RuntimeError("binding identity changed before read")
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        raw = b"".join(chunks)
        after_fd = os.fstat(fd)
        after_path = path.lstat()
        full = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_mode)
        if full != (after_fd.st_dev, after_fd.st_ino, after_fd.st_size, after_fd.st_mtime_ns, after_fd.st_mode):
            raise RuntimeError("binding changed while read")
        if full != (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns, after_path.st_mode):
            raise RuntimeError("binding path changed while read")
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != opened.st_size or digest != expected:
            raise RuntimeError("binding length or SHA-256 differs from the explicit binding")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("binding root must be a JSON object")
        return value, {
            "path": str(path), "size_bytes": len(raw), "sha256": digest,
            "device": opened.st_dev, "inode": opened.st_ino,
            "mtime_ns": opened.st_mtime_ns, "mode": opened.st_mode,
        }
    finally:
        os.close(fd)


def _held_directory(path: Path, expected: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode) or path.resolve(strict=True) != path.absolute():
        raise RuntimeError(f"bound output directory is not a real path: {path}")
    identity = (before.st_dev, before.st_ino, before.st_mode)
    if identity != (expected.get("device"), expected.get("inode"), expected.get("mode")):
        raise RuntimeError("output directory differs from its frozen identity")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    os.set_inheritable(fd, False)
    opened = os.fstat(fd)
    if identity != (opened.st_dev, opened.st_ino, opened.st_mode):
        os.close(fd)
        raise RuntimeError("output directory changed between readback and open")
    return fd, {"path": str(path), "device": opened.st_dev, "inode": opened.st_ino, "mode": opened.st_mode}


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RuntimeError("GO timestamps must be UTC RFC3339 values ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RuntimeError("GO timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RuntimeError("GO timestamp must use UTC")
    return parsed.astimezone(timezone.utc)


def _validate_go(
    go: dict[str, Any], go_path: Path, go_ref: dict[str, Any], args: argparse.Namespace,
    binding: dict[str, Any], binding_ref: dict[str, Any], manifest: dict[str, Any], manifest_ref: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "schema", "decision", "decision_id", "scope", "issued_at_utc", "expires_at_utc",
        "manifest", "binding", "source_fingerprint", "runtime", "output_parent",
        "attempt_name", "command", "single_use",
    }
    if set(go) != required or go.get("schema") != _GO_SCHEMA or go.get("decision") != "GRANTED":
        raise RuntimeError("one-use G01 GO is missing, ungranted, or has an unexpected shape")
    decision_id = go.get("decision_id")
    if not isinstance(decision_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", decision_id):
        raise RuntimeError("GO decision_id is not a safe unique marker key")
    now = datetime.now(timezone.utc)
    issued = _timestamp(go["issued_at_utc"])
    expires = _timestamp(go["expires_at_utc"])
    if not issued <= now <= expires:
        raise RuntimeError("one-use G01 GO is outside its approved time window")
    if go["scope"] != _GO_SCOPE or go["attempt_name"] != args.attempt_name:
        raise RuntimeError("GO scope or exact attempt name differs from this invocation")
    if go["manifest"] != {"path": str(Path(args.manifest).absolute()), "sha256": args.manifest_sha256}:
        raise RuntimeError("GO does not bind this exact frozen manifest")
    if go["binding"] != {"path": str(Path(args.binding).absolute()), "sha256": args.binding_sha256}:
        raise RuntimeError("GO does not bind this exact current input binding")
    if go["source_fingerprint"] != binding["source"]["fingerprint"]:
        raise RuntimeError("GO source fingerprint differs from the bound 207-member source")
    expected_runtime = {
        "executable": binding["runtime"]["executable"],
        "python_version": binding["runtime"]["python_version"],
        "prefix": binding["runtime"]["prefix"],
        "mcp": binding["runtime"]["distributions"].get("mcp"),
        "anyio": binding["runtime"]["distributions"].get("anyio"),
        "jsonschema": binding["runtime"]["distributions"].get("jsonschema"),
    }
    if go["runtime"] != expected_runtime or go["output_parent"] != binding["output_parent"]:
        raise RuntimeError("GO runtime or output-parent identity differs from the current binding")
    expected_command = {
        "executable": binding["runtime"]["executable"],
        "interpreter_flags": ["-I", "-B"],
        "script": str(Path(__file__).resolve(strict=True)),
        "mode": "g01",
        "manifest_path": str(Path(args.manifest).absolute()),
        "manifest_sha256": args.manifest_sha256,
        "binding_path": str(Path(args.binding).absolute()),
        "binding_sha256": args.binding_sha256,
        "go_path": str(go_path.absolute()),
        "go_sha256": "CLI_SHA256_OF_THIS_GO_FILE",
        "attempt_name": args.attempt_name,
        "timeout_seconds": _MAX_WALL_SECONDS,
    }
    if go["command"] != expected_command or args.timeout_seconds != _MAX_WALL_SECONDS:
        raise RuntimeError("GO command or 180-second limit differs from this invocation")
    if go["single_use"] != {"max_parent_launches": 1, "consume_marker_name": f".t038-go-consumed-{decision_id}.json"}:
        raise RuntimeError("GO one-use marker declaration is invalid")
    manifest_binding = manifest.get("binding", {})
    if (
        manifest.get("status") != "FROZEN_G01_PREPARATION_ONLY_RUNTIME_NOT_RUN"
        or manifest.get("runtime_GO") != "NOT_GRANTED"
        or manifest.get("source_fingerprint") != binding["source"]["fingerprint"]
        or manifest.get("source_member_count") != 207
        or manifest.get("external_output_parent") != binding["output_parent"]
        or manifest_binding != {"path": binding_ref["path"], "sha256": binding_ref["sha256"], "size_bytes": binding_ref["size_bytes"]}
        or manifest.get("files") != binding["harness_files"]
    ):
        raise RuntimeError("frozen manifest is not internally bound to this current input bundle")
    if manifest_ref["path"] != go["manifest"]["path"] or go_ref["path"] != str(go_path):
        raise RuntimeError("GO or manifest path identity differs from the explicit command")
    return {
        "schema": go["schema"], "decision_id": decision_id, "scope": go["scope"],
        "issued_at_utc": go["issued_at_utc"], "expires_at_utc": go["expires_at_utc"],
        "decision_ref": go_ref, "manifest_ref": manifest_ref,
        "binding_ref": binding_ref, "source_fingerprint": go["source_fingerprint"],
        "attempt_name": go["attempt_name"], "timeout_seconds": _MAX_WALL_SECONDS,
        "consume_marker_name": go["single_use"]["consume_marker_name"],
    }


def _mkdir_held(parent_fd: int, parent_path: Path, name: str, mode: int = 0o700) -> tuple[int, dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) or name in {".", ".."}:
        raise RuntimeError(f"unsafe fresh directory name: {name!r}")
    os.mkdir(name, mode=mode, dir_fd=parent_fd)
    os.fsync(parent_fd)
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("new attempt entry is not a directory")
    path = parent_path / name
    opened_fd = os.open(name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
    os.set_inheritable(opened_fd, False)
    opened = os.fstat(opened_fd)
    path_info = path.lstat()
    identity = (info.st_dev, info.st_ino, info.st_mode)
    if identity != (opened.st_dev, opened.st_ino, opened.st_mode) or identity != (path_info.st_dev, path_info.st_ino, path_info.st_mode):
        os.close(opened_fd)
        raise RuntimeError("new directory identity changed after creation")
    return opened_fd, {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode}


def _capture_child(proc: subprocess.Popen[bytes], timeout_seconds: int) -> dict[str, Any]:
    assert proc.stdout is not None and proc.stderr is not None and proc.stdin is not None
    proc.stdin.close()
    streams = {proc.stdout.fileno(): (proc.stdout, "stdout", _MAX_STDOUT), proc.stderr.fileno(): (proc.stderr, "stderr", _MAX_STDERR)}
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    eof = {"stdout": False, "stderr": False}
    overflow: str | None = None
    capture_error: str | None = None
    deadline = time.monotonic() + min(timeout_seconds, _MAX_WALL_SECONDS)
    started = time.monotonic()
    selector = selectors.DefaultSelector()
    for fd, (stream, name, _limit) in streams.items():
        os.set_blocking(fd, False)
        selector.register(stream, selectors.EVENT_READ, name)
    timed_out = False
    try:
        try:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                events = selector.select(min(0.25, max(0.0, deadline - time.monotonic())))
                for key, _mask in events:
                    name = key.data
                    stream, _, limit = streams[key.fd]
                    try:
                        block = os.read(key.fd, min(65536, limit + 1 - len(captured[name])))
                    except BlockingIOError:
                        continue
                    if not block:
                        eof[name] = True
                        selector.unregister(stream)
                        stream.close()
                        continue
                    remaining = limit - len(captured[name])
                    if len(block) > remaining:
                        if remaining > 0:
                            captured[name].extend(block[:remaining])
                        overflow = name
                        break
                    captured[name].extend(block)
                if overflow:
                    break
        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        selector.close()
    terminated_for_limit = timed_out or overflow is not None or capture_error is not None
    if terminated_for_limit and proc.poll() is None:
        proc.kill()
    try:
        returncode = proc.wait(timeout=5)
        reaped = True
    except subprocess.TimeoutExpired:
        returncode = None
        reaped = False
    for name in ("stdout", "stderr"):
        stream = streams[next(fd for fd, data in streams.items() if data[1] == name)][0]
        if not stream.closed:
            stream.close()
    return {
        "stdout": bytes(captured["stdout"]), "stderr": bytes(captured["stderr"]),
        "stdout_eof": eof["stdout"], "stderr_eof": eof["stderr"],
        "timed_out": timed_out, "overflow_stream": overflow,
        "terminated_for_limit": terminated_for_limit,
        "capture_error": capture_error,
        "duration_seconds": round(time.monotonic() - started, 6),
        "returncode": returncode, "reaped": reaped,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["g01"], required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--binding-sha256", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--go-sha256", required=True)
    parser.add_argument("--attempt-name", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=_MAX_WALL_SECONDS)
    args = parser.parse_args()
    if args.timeout_seconds != _MAX_WALL_SECONDS:
        parser.error("the one-use G01 command is fixed to a 180-second maximum")
    if not all(Path(value).is_absolute() for value in (args.manifest, args.binding, args.go)):
        parser.error("manifest, binding, and one-use GO paths must be absolute")

    attempt_fd: int | None = None
    results_fd: int | None = None
    proc: subprocess.Popen[bytes] | None = None
    result: dict[str, Any] = {
        "schema": "T038_PARENT_ATTEMPT_V1", "mode": args.mode,
        "status": "NOT_READY", "scope": "one bounded G01 observer child; no wire mode yet",
    }
    exit_code = 2
    overall_started = time.monotonic()
    output_parent_fd: int | None = None
    go_path = Path(args.go)
    go_value: dict[str, Any] | None = None
    go_ref: dict[str, Any] | None = None
    manifest_value: dict[str, Any] | None = None
    manifest_ref: dict[str, Any] | None = None
    go_validation: dict[str, Any] | None = None
    consume_ref: dict[str, Any] | None = None
    try:
        binding_path = Path(args.binding)
        binding, binding_ref = _read_binding(binding_path, args.binding_sha256)
        manifest_path = Path(args.manifest)
        manifest_value, manifest_ref = _read_binding(manifest_path, args.manifest_sha256)
        go_value, go_ref = _read_binding(go_path, args.go_sha256)
        runtime = binding.get("runtime", {})
        if _INITIAL_SYS_PATH != runtime.get("sys_path"):
            raise RuntimeError("parent interpreter isolated sys.path differs from bound runtime")
        if str(Path(sys.executable).resolve(strict=True)) != str(Path(runtime.get("executable", "")).resolve(strict=True)):
            raise RuntimeError("parent interpreter executable differs from bound runtime")
        harness = Path(binding["harness_root"]).resolve(strict=True)
        if Path(__file__).resolve(strict=True).parent != harness:
            raise RuntimeError("parent launcher is outside bound harness root")
        sys.path[:] = [*runtime["sys_path"], str(harness), str(Path(binding["source"]["root"]).resolve(strict=True))]
        from t038_support import (  # imported only after the current binding and isolated path check
            NotReady, json_bytes, read_json_bound,
            verify_bound_files, verify_runtime_binding, verify_source_binding, write_new_verified,
        )

        if binding.get("schema") != "T038_CURRENT_BINDING_V1":
            raise NotReady("wrong current binding schema")
        source_ref = verify_source_binding(binding)
        runtime_ref = verify_runtime_binding(binding)
        fixed_files = [
            {"path": manifest_ref["path"], "size_bytes": manifest_ref["size_bytes"], "sha256": manifest_ref["sha256"]},
            binding["expected_registry"], binding["source_selection"],
            binding["runtime_path_readback"], binding["environment_readback"], binding["entrypoint"],
            binding["gateway"], *binding["sdk_files"], *binding.get("runtime_files", []), *binding["harness_files"],
        ]
        fixed_refs = verify_bound_files(fixed_files)
        go_validation = _validate_go(
            go_value, go_path, go_ref, args, binding, binding_ref, manifest_value, manifest_ref,
        )
        child_script = harness / "g01_observer_child.py"
        child_rows = [row for row in binding["harness_files"] if Path(row["path"]).resolve(strict=True) == child_script]
        if len(child_rows) != 1:
            raise NotReady("binding does not contain exactly one current G01 child script")
        parent_rows = [row for row in binding["harness_files"] if Path(row["path"]).resolve(strict=True) == Path(__file__).resolve(strict=True)]
        if len(parent_rows) != 1:
            raise NotReady("binding does not contain exactly one current parent runner")
        parent_info = binding["output_parent"]
        output_parent = Path(parent_info["path"]).resolve(strict=True)
        output_parent_fd, parent_ref = _held_directory(output_parent, parent_info)
        consume_marker = output_parent / go_validation["consume_marker_name"]
        consume_ref = write_new_verified(consume_marker, json_bytes({
            "schema": "T038_G01_GO_CONSUMPTION_V1", "decision_id": go_validation["decision_id"],
            "decision_ref": go_ref, "binding_ref": binding_ref, "manifest_ref": manifest_ref,
            "source_fingerprint": binding["source"]["fingerprint"], "attempt_name": args.attempt_name,
            "scope": go_validation["scope"], "max_child_launches": 1,
        }))
        marker_parent = consume_ref.get("parent_ref", {})
        if (marker_parent.get("device"), marker_parent.get("inode"), marker_parent.get("mode")) != (
            parent_ref["device"], parent_ref["inode"], parent_ref["mode"]
        ):
            raise NotReady("one-use GO marker was not created under the held output-parent identity")
        attempt_fd, attempt_ref = _mkdir_held(output_parent_fd, output_parent, args.attempt_name)
        attempt_path = Path(attempt_ref["path"])
        results_fd, results_ref = _mkdir_held(attempt_fd, attempt_path, "results")
        derived: dict[str, dict[str, Any]] = {"OUTPUT_RESULTS": results_ref}
        for name in ("home", "tmp", "cache", "config", "data", "server-home"):
            child_fd, child_ref = _mkdir_held(attempt_fd, attempt_path, name)
            derived[name] = child_ref
            os.close(child_fd)
        clean_env = dict(binding["fixed_environment"])
        clean_env.update({
            "HOME": str(attempt_path / "home"), "TMPDIR": str(attempt_path / "tmp"),
            "XDG_CACHE_HOME": str(attempt_path / "cache"), "XDG_CONFIG_HOME": str(attempt_path / "config"),
            "XDG_DATA_HOME": str(attempt_path / "data"), "COMSOL_SERVER_MCP_HOME": str(attempt_path / "server-home"),
        })
        observer_path = attempt_path / "results" / "g01-observer.json"
        remaining_seconds = min(args.timeout_seconds, _MAX_WALL_SECONDS) - (time.monotonic() - overall_started)
        if remaining_seconds < 1:
            raise NotReady("preflight used the remaining bounded parent-child time budget")
        command = [
            runtime["executable"], "-I", "-B", str(child_script),
            "--binding", str(binding_path), "--binding-sha256", args.binding_sha256,
            "--attempt-device", str(attempt_ref["device"]), "--attempt-inode", str(attempt_ref["inode"]),
            "--attempt-mode", str(attempt_ref["mode"]), "--output", str(observer_path),
        ]
        start_new_session = True
        proc = subprocess.Popen(
            command, cwd=str(attempt_path), env=clean_env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
            start_new_session=start_new_session,
        )
        try:
            child_session = os.getsid(proc.pid)
        except OSError:
            child_session = None
        capture = _capture_child(proc, remaining_seconds)
        out_ref = write_new_verified(attempt_path / "results" / "g01-child.stdout.bin", capture["stdout"])
        err_ref = write_new_verified(attempt_path / "results" / "g01-child.stderr.bin", capture["stderr"])
        observer_value = None
        observer_ref = None
        try:
            observer_value, observer_ref = read_json_bound(observer_path, "" if not observer_path.exists() else _sha256_file(observer_path))
        except BaseException:
            pass

        output_parent_open = os.fstat(output_parent_fd)
        output_parent_named = output_parent.lstat()
        attempt_open = os.fstat(attempt_fd)
        attempt_named = attempt_path.lstat()
        results_open = os.fstat(results_fd)
        results_named = (attempt_path / "results").lstat()
        directories_stable = (
            (output_parent_open.st_dev, output_parent_open.st_ino, output_parent_open.st_mode) ==
            (parent_ref["device"], parent_ref["inode"], parent_ref["mode"]) and
            (output_parent_named.st_dev, output_parent_named.st_ino, output_parent_named.st_mode) ==
            (parent_ref["device"], parent_ref["inode"], parent_ref["mode"]) and
            (attempt_open.st_dev, attempt_open.st_ino, attempt_open.st_mode) ==
            (attempt_ref["device"], attempt_ref["inode"], attempt_ref["mode"]) and
            (attempt_named.st_dev, attempt_named.st_ino, attempt_named.st_mode) ==
            (attempt_ref["device"], attempt_ref["inode"], attempt_ref["mode"]) and
            (results_open.st_dev, results_open.st_ino, results_open.st_mode) ==
            (results_ref["device"], results_ref["inode"], results_ref["mode"]) and
            (results_named.st_dev, results_named.st_ino, results_named.st_mode) ==
            (results_ref["device"], results_ref["inode"], results_ref["mode"])
        )
        source_after = verify_source_binding(binding)
        fixed_after = verify_bound_files(fixed_files)
        binding_after, binding_ref_after = _read_binding(binding_path, args.binding_sha256)
        observer_status = observer_value.get("status") if isinstance(observer_value, dict) else None
        child_clean = (
            capture["reaped"] and capture["returncode"] == 0 and capture["stdout_eof"]
            and capture["stderr_eof"] and not capture["timed_out"] and capture["overflow_stream"] is None
            and capture["capture_error"] is None and child_session == proc.pid
        )
        exact_inputs = source_after == source_ref and fixed_after == fixed_refs and binding_after == binding and binding_ref_after == binding_ref
        parent_elapsed = time.monotonic() - overall_started
        within_time_budget = parent_elapsed <= min(args.timeout_seconds, _MAX_WALL_SECONDS)
        passed = child_clean and observer_status == "PASS_G01_METADATA_OBSERVER" and directories_stable and exact_inputs and within_time_budget
        result.update({
            "status": "PASS_G01_PARENT_CAPTURE_PRE_FINALIZATION" if passed else "FAIL_G01_PARENT_CAPTURE",
            "binding": binding_ref, "manifest": manifest_ref,
            "one_use_go": go_validation, "go_ref": go_ref, "consume_marker": consume_ref,
            "source": {"root": binding["source"]["root"], "fingerprint": binding["source"]["fingerprint"], "member_count": 207},
            "runtime": runtime_ref, "attempt_directory": attempt_ref, "results_directory": results_ref,
            "derived_directories": derived, "output_parent": parent_ref,
            "child": {
                "owned_child_count": 1,
                "pid": proc.pid, "session_id": child_session, "session_is_pid": child_session == proc.pid,
                "start_new_session": start_new_session, "returncode": capture["returncode"], "reaped": capture["reaped"],
                "stdin_closed_before_wait": True, "stdout_eof": capture["stdout_eof"], "stderr_eof": capture["stderr_eof"],
                "timed_out": capture["timed_out"], "overflow_stream": capture["overflow_stream"],
                "terminated_for_limit": capture["terminated_for_limit"],
                "capture_error": capture["capture_error"], "capture_duration_seconds": capture["duration_seconds"],
                "stdout_capture": out_ref, "stderr_capture": err_ref,
                "observer_report": observer_ref, "observer_status": observer_status,
            },
            "directories_stable": directories_stable, "fixed_inputs_before": fixed_refs,
            "fixed_inputs_after": fixed_after, "binding_after": binding_ref_after,
            "source_after": source_after["members"], "fixed_binding_unchanged": exact_inputs,
            "parent_elapsed_before_result_seconds": round(parent_elapsed, 6), "within_time_budget": within_time_budget,
            "actual_wire_protocol": "NOT_RUN", "acceptance_limits": {
                "wire_cases": "NOT_RUN", "original50_compatibility": "OPEN", "28_schema_deltas": "OPEN",
                "MCP_tools_list_multipage": "OPEN", "COMSOL_JVM_native": "NOT_IN_SCOPE",
                "T037_OS_isolation": "NOT_ESTABLISHED",
            },
        })
        exit_code = 0 if passed else 2
    except BaseException as exc:
        bounded_message = " ".join(str(exc).split())[:500]
        result["error"] = {"type": type(exc).__name__, "message": bounded_message}
        result["status"] = "NOT_READY_G01_PARENT" if type(exc).__name__ in {"NotReady", "FileNotFoundError"} else "FAIL_G01_PARENT"
        sys.stderr.write("T038_PARENT_ERROR " + json.dumps({"type": type(exc).__name__, "message": bounded_message}, sort_keys=True) + "\n")
        sys.stderr.flush()
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result["child_reap_failed"] = True
                exit_code = 3
    # Result is retained after child success, failure, timeout, or partial output.
    try:
        if "attempt_path" in locals() and "results_fd" in locals() and results_fd is not None:
            from t038_support import write_new_verified as _write_result
            parent_result_ref = _write_result(attempt_path / "g01-parent-result.json", json_bytes(result))
            finalization: dict[str, Any] = {
                "schema": "T038_G01_POST_TERMINAL_REVALIDATION_V1",
                "status": "FAIL_G01_POST_TERMINAL_REVALIDATION",
                "scope": "pre-finalization-artifact gate: same original GO and all frozen source/input refs rechecked after terminal captures and parent result write; final stdout/exit reports the additional post-artifact input revalidation",
                "parent_result_ref": parent_result_ref,
                "go_ref_before": go_ref,
                "manifest_ref_before": manifest_ref,
                "binding_ref_before": binding_ref if "binding_ref" in locals() else None,
                "consumption_marker_ref": consume_ref,
            }
            finalization_error: str | None = None
            try:
                if go_value is None or go_ref is None or manifest_value is None or manifest_ref is None or go_validation is None:
                    raise NotReady("selected one-use inputs are unavailable for post-terminal revalidation")
                go_after, go_ref_after = _read_binding(go_path, args.go_sha256)
                manifest_after, manifest_ref_after = _read_binding(Path(args.manifest), args.manifest_sha256)
                binding_after, binding_ref_after = _read_binding(Path(args.binding), args.binding_sha256)
                source_after_terminal = verify_source_binding(binding)
                fixed_after_terminal = verify_bound_files(fixed_files)
                input_stability = (
                    go_after == go_value and go_ref_after == go_ref
                    and manifest_after == manifest_value and manifest_ref_after == manifest_ref
                    and binding_after == binding and binding_ref_after == binding_ref
                    and source_after_terminal == source_ref and fixed_after_terminal == fixed_refs
                )
                finalization.update({
                    "go_ref_after": go_ref_after,
                    "manifest_ref_after": manifest_ref_after,
                    "binding_ref_after": binding_ref_after,
                    "source_fingerprint_after": source_after_terminal["fingerprint"],
                    "source_member_count_after": source_after_terminal["member_count"],
                    "fixed_inputs_after": fixed_after_terminal,
                    "same_original_go_bytes_and_file_ref": go_after == go_value and go_ref_after == go_ref,
                    "same_manifest_binding_and_file_ref": manifest_after == manifest_value and manifest_ref_after == manifest_ref,
                    "same_current_binding_bytes_and_file_ref": binding_after == binding and binding_ref_after == binding_ref,
                    "source_and_all_frozen_inputs_unchanged": input_stability,
                })
                if input_stability and result.get("status") == "PASS_G01_PARENT_CAPTURE_PRE_FINALIZATION":
                    finalization["status"] = "PASS_G01_POST_TERMINAL_REVALIDATION"
                else:
                    finalization_error = "post-terminal input refs differ or the child capture did not pass"
            except BaseException as exc:
                finalization_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            if finalization_error is not None:
                finalization["error"] = finalization_error
                exit_code = 2
            finalization_ref = _write_result(attempt_path / "g01-finalization-revalidation.json", json_bytes(finalization))
            final_artifact_input_revalidation = "FAIL"
            final_artifact_input_error: str | None = None
            try:
                if go_value is None or go_ref is None or manifest_value is None or manifest_ref is None or go_validation is None:
                    raise NotReady("selected one-use inputs are unavailable for post-artifact revalidation")
                go_after_artifact, go_ref_after_artifact = _read_binding(go_path, args.go_sha256)
                manifest_after_artifact, manifest_ref_after_artifact = _read_binding(Path(args.manifest), args.manifest_sha256)
                binding_after_artifact, binding_ref_after_artifact = _read_binding(Path(args.binding), args.binding_sha256)
                source_after_artifact = verify_source_binding(binding)
                fixed_after_artifact = verify_bound_files(fixed_files)
                final_artifact_inputs_stable = (
                    go_after_artifact == go_value and go_ref_after_artifact == go_ref
                    and manifest_after_artifact == manifest_value and manifest_ref_after_artifact == manifest_ref
                    and binding_after_artifact == binding and binding_ref_after_artifact == binding_ref
                    and source_after_artifact == source_ref and fixed_after_artifact == fixed_refs
                )
                if not final_artifact_inputs_stable:
                    raise NotReady("post-artifact input refs differ from the original one-use GO and frozen inputs")
                final_artifact_input_revalidation = "PASS"
            except BaseException as exc:
                final_artifact_input_error = type(exc).__name__
                exit_code = 2
            if output_parent_fd is not None:
                held_checks = (
                    (output_parent_fd, output_parent, parent_ref),
                    (attempt_fd, attempt_path, attempt_ref),
                    (results_fd, attempt_path / "results", results_ref),
                )
                for held_fd, held_path, expected_ref in held_checks:
                    held_stat = os.fstat(held_fd)
                    named_stat = held_path.lstat()
                    expected_identity = (expected_ref["device"], expected_ref["inode"], expected_ref["mode"])
                    if (held_stat.st_dev, held_stat.st_ino, held_stat.st_mode) != expected_identity:
                        raise NotReady(f"held output directory identity changed: {held_path}")
                    if stat.S_ISLNK(named_stat.st_mode) or (named_stat.st_dev, named_stat.st_ino, named_stat.st_mode) != expected_identity:
                        raise NotReady(f"named output directory identity changed: {held_path}")
            if finalization["status"] != "PASS_G01_POST_TERMINAL_REVALIDATION":
                exit_code = 2
            if final_artifact_input_revalidation != "PASS":
                exit_code = 2
            sys.stdout.buffer.write(b"T038_PARENT_FINALIZATION " + json.dumps({
                "parent_result_ref": parent_result_ref,
                "finalization_ref": finalization_ref,
                "finalization_status": finalization["status"],
                "final_artifact_input_revalidation": final_artifact_input_revalidation,
                "final_artifact_input_error": final_artifact_input_error,
                "final_directory_identity_check": "PASS",
            }, sort_keys=True).encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
    except BaseException as exc:
        sys.stderr.write(f"T038_PARENT_RESULT_FAILURE {type(exc).__name__}\n")
        exit_code = 3
    finally:
        for fd in (results_fd, attempt_fd, output_parent_fd):
            if fd is not None:
                os.close(fd)
    if exit_code != 0:
        sys.stderr.write(f"T038_PARENT {result['status']}\n")
    return exit_code


def _sha256_file(path: Path) -> str:
    # Only used to bind the observer report just created by this owned child.
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
