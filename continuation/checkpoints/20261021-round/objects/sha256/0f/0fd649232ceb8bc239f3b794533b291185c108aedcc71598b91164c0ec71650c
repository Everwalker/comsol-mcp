#!/usr/bin/env python3
"""Run a synthetic, deny-by-default macOS process-isolation acceptance probe.

This command verifies a candidate SBPL policy against two separately launched
roles (``worker`` and ``owned_server``). It does not launch COMSOL and it does
not establish service-account, ACL, license, or production integration
acceptance. The supported macOS mechanism is deprecated; retain that limitation
in any release claim.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import platform
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SOURCE = REPO_ROOT / "tools" / "t037_probe_child.c"
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
EXPECTED_DENIAL_ERRNOS = {errno.EACCES, errno.EPERM}
PROBE_ROLES = ("worker", "owned_server")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sbpl_string(value: str | Path) -> str:
    text = str(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("SBPL path contains a control character")
    return json.dumps(text, ensure_ascii=True)


def _candidate_profile(
    *,
    probe_binary: Path,
    scratch: Path,
    allowed_loopback_port: int,
) -> str:
    """Build the exact per-role profile used by this synthetic test.

    The read-only system bootstrap rules are a small subset of the local
    ``system.sb`` baseline. No Apple private profile is imported. The only
    executable path, writable tree, and outbound TCP destination added here
    are the probe binary, one task-owned scratch directory, and one exact
    localhost port.
    """

    probe = _sbpl_string(probe_binary)
    scratch_path = _sbpl_string(scratch)
    return f"""(version 1)
(deny default)

;; Minimal system bootstrap subset observed in the local system.sb baseline.
(allow syscall*)
(allow mach-bootstrap)
(allow sysctl-read)
(allow file-read* file-test-existence
       (subpath \"/System\")
       (subpath \"/usr/lib\")
       (subpath \"/usr/share\")
       (literal \"/private/etc/localtime\")
       (literal \"/etc\")
       (literal \"/tmp\")
       (literal \"/var\")
       (literal \"/\"))
(allow file-map-executable
       (subpath \"/System/Library/Frameworks\")
       (subpath \"/System/Library/PrivateFrameworks\")
       (subpath \"/usr/lib\"))

;; A controlled process must be able to inspect path ancestors for its own
;; executable and scratch root, but it receives no general home-directory
;; content access.
(allow file-read-metadata
       (path-ancestors {probe})
       (path-ancestors {scratch_path}))
(allow file-read* file-map-executable (literal {probe}))
(allow file-read* file-write* (subpath {scratch_path}))
(allow process-exec (literal {probe}))
(allow network-outbound (remote ip \"localhost:{allowed_loopback_port}\"))
"""


def _start_loopback_listener() -> tuple[socket.socket, threading.Event, threading.Thread]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    listener.settimeout(8.0)
    accepted = threading.Event()

    def accept_one() -> None:
        try:
            connection, _address = listener.accept()
        except OSError:
            return
        accepted.set()
        connection.close()

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()
    return listener, accepted, thread


def _read_child_record(process: subprocess.Popen[bytes], timeout_seconds: float) -> tuple[dict[str, Any] | None, str]:
    if process.stdout is None:
        return None, "stdout pipe was not created"
    ready, _write, _error = select.select([process.stdout], [], [], timeout_seconds)
    if not ready:
        return None, "probe did not produce its identity/result record before timeout"
    line = process.stdout.readline()
    if not line:
        return None, "probe exited without a result record"
    try:
        record = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"probe result record was invalid JSON: {type(exc).__name__}"
    if not isinstance(record, dict):
        return None, "probe result record was not an object"
    return record, ""


def _ps_identity(pid: int) -> dict[str, Any]:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "pid=,uid=,gid=,lstart=,comm="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode != 0 or not output or result.stderr.strip():
        return {
            "status": "UNKNOWN",
            "returncode": result.returncode,
            "stderr": result.stderr.strip(),
        }
    rows = [line.strip() for line in output.splitlines() if line.strip()]
    if len(rows) != 1:
        return {"status": "UNKNOWN", "reason": "ps returned an unexpected row count", "rows": len(rows)}
    fields = rows[0].split()
    if len(fields) < 6:
        return {"status": "UNKNOWN", "reason": "ps row was structurally incomplete", "row": rows[0]}
    try:
        observed_pid, observed_uid, observed_gid = map(int, fields[:3])
    except ValueError:
        return {"status": "UNKNOWN", "reason": "ps PID or identity fields were malformed", "row": rows[0]}
    return {
        "status": "OBSERVED",
        "pid": observed_pid,
        "uid": observed_uid,
        "gid": observed_gid,
        "birth_text": " ".join(fields[3:-1]),
        "command": fields[-1],
        "raw_row": rows[0],
    }


def _ps_absence(pid: int) -> dict[str, Any]:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "pid="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode == 1 and not output and not result.stderr.strip():
        return {"status": "ABSENT", "returncode": 1, "stdout": "", "stderr": ""}
    if result.returncode == 0 and output == str(pid):
        return {"status": "PRESENT", "returncode": 0, "stdout": output, "stderr": result.stderr.strip()}
    return {
        "status": "UNKNOWN",
        "returncode": result.returncode,
        "stdout": output,
        "stderr": result.stderr.strip(),
    }


def _run_role(
    *,
    role: str,
    probe_binary: Path,
    evidence_dir: Path,
    fixture_root: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    scratch = (fixture_root / role / "scratch").resolve()
    synthetic_home = (fixture_root / role / "synthetic-home").resolve()
    scratch.mkdir(parents=True)
    (synthetic_home / ".ssh").mkdir(parents=True)
    sentinel = (synthetic_home / ".ssh" / "known_hosts").resolve()
    sentinel.write_bytes(b"T037-SYNTHETIC-SENTINEL-KEEP\n")
    sentinel_before = _sha256_file(sentinel)

    allowed_listener, allowed_accepted, allowed_thread = _start_loopback_listener()
    denied_listener, denied_accepted, denied_thread = _start_loopback_listener()
    allowed_port = int(allowed_listener.getsockname()[1])
    denied_port = int(denied_listener.getsockname()[1])
    while denied_port == allowed_port:
        denied_listener.close()
        denied_listener, denied_accepted, denied_thread = _start_loopback_listener()
        denied_port = int(denied_listener.getsockname()[1])

    profile_text = _candidate_profile(
        probe_binary=probe_binary,
        scratch=scratch,
        allowed_loopback_port=allowed_port,
    )
    profile_path = evidence_dir / f"{role}.candidate.sb"
    with profile_path.open("x", encoding="utf-8") as stream:
        stream.write(profile_text)
    profile_hash = _sha256_file(profile_path)

    sentinel_process = subprocess.Popen(
        ["/bin/sleep", "60"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    sentinel_process_identity = _ps_identity(sentinel_process.pid)

    command = [
        str(SANDBOX_EXEC),
        "-f",
        str(profile_path),
        str(probe_binary),
        role,
        str(scratch),
        str(sentinel),
        str(allowed_port),
        str(denied_port),
        str(sentinel_process.pid),
    ]
    process_record: dict[str, Any] = {
        "role": role,
        "command": command,
        "probe_executable": str(probe_binary),
        "profile_sha256": profile_hash,
        "allowed_loopback_port": allowed_port,
        "denied_loopback_port": denied_port,
        "sentinel_sha256_before": sentinel_before,
        "unrelated_sentinel_process": {
            "pid": sentinel_process.pid,
            "before": sentinel_process_identity,
        },
    }
    process: subprocess.Popen[bytes] | None = None
    raw_record: dict[str, Any] | None = None
    parse_error = ""
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        process_record["launcher_pid"] = process.pid
        raw_record, parse_error = _read_child_record(process, timeout_seconds)
        if raw_record is not None:
            process_record["child_record"] = raw_record
            process_record["process_identity"] = _ps_identity(process.pid)
            process_record["unrelated_sentinel_process"]["alive_after_probe"] = (
                sentinel_process.poll() is None
            )
            if sentinel_process.poll() is None:
                process_record["unrelated_sentinel_process"]["after"] = _ps_identity(
                    sentinel_process.pid
                )
            if process.stdin is not None:
                process.stdin.write(b"x")
                process.stdin.flush()
        else:
            process_record["result_record_error"] = parse_error
            process.kill()
        try:
            _stdout_tail, stderr = process.communicate(timeout=timeout_seconds)
            process_record["returncode"] = process.returncode
            process_record["stderr"] = stderr.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            process.kill()
            _stdout_tail, stderr = process.communicate(timeout=2)
            process_record["returncode"] = process.returncode
            process_record["stderr"] = stderr.decode("utf-8", errors="replace")
            process_record["wait_status"] = "TIMEOUT_AFTER_RELEASE_OR_KILL"
    except Exception as exc:  # retain unexpected runner faults as unknown evidence
        process_record["runner_error"] = f"{type(exc).__name__}: {exc}"
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=2)
    finally:
        allowed_listener.close()
        denied_listener.close()
        allowed_thread.join(timeout=1)
        denied_thread.join(timeout=1)
        if sentinel_process.poll() is None:
            sentinel_process.terminate()
            try:
                sentinel_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                sentinel_process.kill()
                sentinel_process.wait(timeout=2)
        process_record["unrelated_sentinel_process"]["runner_cleanup_returncode"] = (
            sentinel_process.returncode
        )
        process_record["unrelated_sentinel_process"]["cleanup_inventory"] = _ps_absence(
            sentinel_process.pid
        )

    process_record["allowed_endpoint_accepted"] = allowed_accepted.is_set()
    process_record["denied_endpoint_accepted"] = denied_accepted.is_set()
    process_record["sentinel_sha256_after"] = _sha256_file(sentinel)
    process_record["sentinel_unchanged"] = (
        process_record["sentinel_sha256_before"] == process_record["sentinel_sha256_after"]
    )
    process_record["classification"] = _classify_role(process_record)
    return process_record


def _is_expected_denial(value: Any) -> bool:
    return isinstance(value, int) and value in EXPECTED_DENIAL_ERRNOS


def _classify_role(record: dict[str, Any]) -> str:
    """Require every explicit positive and negative control to be observed."""

    raw = record.get("child_record")
    if not isinstance(raw, dict):
        return "UNKNOWN"
    identity = record.get("process_identity")
    if not isinstance(identity, dict) or identity.get("status") != "OBSERVED":
        return "UNKNOWN"
    if (
        identity.get("pid") != record.get("launcher_pid")
        or raw.get("pid") != record.get("launcher_pid")
        or identity.get("uid") != raw.get("uid")
        or identity.get("gid") != raw.get("gid")
        or identity.get("command") != record.get("probe_executable")
    ):
        return "UNKNOWN"
    if record.get("returncode") != 0 or record.get("stderr"):
        return "UNKNOWN"
    if raw.get("role") != record.get("role"):
        return "UNKNOWN"
    if not record.get("sentinel_unchanged"):
        return "FAIL"
    if record.get("denied_endpoint_accepted"):
        return "FAIL"
    unrelated = record.get("unrelated_sentinel_process")
    if not isinstance(unrelated, dict):
        return "UNKNOWN"
    if unrelated.get("alive_after_probe") is False:
        return "FAIL"
    before_identity = unrelated.get("before")
    after_identity = unrelated.get("after")
    if (
        not isinstance(before_identity, dict)
        or before_identity.get("status") != "OBSERVED"
        or before_identity.get("pid") != unrelated.get("pid")
    ):
        return "UNKNOWN"
    if (
        not isinstance(after_identity, dict)
        or after_identity.get("status") != "OBSERVED"
        or after_identity.get("pid") != before_identity.get("pid")
        or after_identity.get("uid") != before_identity.get("uid")
        or after_identity.get("gid") != before_identity.get("gid")
        or after_identity.get("birth_text") != before_identity.get("birth_text")
        or after_identity.get("command") != before_identity.get("command")
    ):
        return "UNKNOWN"
    cleanup_inventory = unrelated.get("cleanup_inventory")
    if not isinstance(cleanup_inventory, dict) or cleanup_inventory.get("status") != "ABSENT":
        return "UNKNOWN"
    if not record.get("allowed_endpoint_accepted"):
        return "UNKNOWN"

    positive_ok = (
        raw.get("allowed_write_errno") == 0
        and raw.get("allowed_read_errno") == 0
        and raw.get("allowed_readback") == "probe-ok"
        and raw.get("allowed_network_phase") == 3
        and raw.get("allowed_network_errno") == 0
    )
    denied_ok = (
        _is_expected_denial(raw.get("protected_read_errno"))
        and _is_expected_denial(raw.get("protected_write_errno"))
        and raw.get("denied_network_phase") == 2
        and _is_expected_denial(raw.get("denied_network_errno"))
        and _is_expected_denial(raw.get("sentinel_query_errno"))
        and _is_expected_denial(raw.get("sentinel_signal_errno"))
        and _is_expected_denial(raw.get("spawn_error"))
        and raw.get("spawned_status") == -1
    )
    if positive_ok and denied_ok:
        return "PASS"
    if (
        raw.get("allowed_write_errno") not in (0, None)
        or raw.get("allowed_read_errno") not in (0, None)
        or raw.get("allowed_network_phase") == 3 and not record.get("allowed_endpoint_accepted")
        or raw.get("protected_read_errno") == 0
        or raw.get("protected_write_errno") == 0
        or raw.get("denied_network_phase") == 3
        or raw.get("sentinel_query_errno") == 0
        or raw.get("sentinel_signal_errno") == 0
        or raw.get("spawn_error") == 0
        or record.get("sentinel_unchanged") is False
    ):
        return "FAIL"
    return "UNKNOWN"


def run_acceptance(evidence_dir: Path, timeout_seconds: float = 8.0) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise RuntimeError("this candidate profile runner is macOS-only; no other target is claimed")
    if not SANDBOX_EXEC.is_file():
        raise RuntimeError("/usr/bin/sandbox-exec is unavailable")
    if not PROBE_SOURCE.is_file():
        raise RuntimeError(f"probe source is missing: {PROBE_SOURCE}")
    if not evidence_dir.is_absolute():
        raise ValueError("evidence directory must be an absolute path")
    if evidence_dir.exists() and evidence_dir.is_symlink():
        raise ValueError("evidence directory must not be a symlink")
    evidence_dir = evidence_dir.resolve()
    if evidence_dir.exists():
        if not evidence_dir.is_dir() or any(evidence_dir.iterdir()):
            raise ValueError("evidence directory must be new and empty")
    else:
        evidence_dir.mkdir(parents=True)

    compiler = shutil.which("cc") or shutil.which("clang")
    if compiler is None:
        raise RuntimeError("no C compiler is available for the synthetic OS-control probe")

    with tempfile.TemporaryDirectory(prefix="t037-isolation-") as temp_name:
        fixture_root = Path(temp_name).resolve()
        probe_binary = (fixture_root / "t037_probe_child").resolve()
        compile_result = subprocess.run(
            [compiler, "-Wall", "-Wextra", "-Werror", "-O2", str(PROBE_SOURCE), "-o", str(probe_binary)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        compile_receipt = {
            "compiler": compiler,
            "command": [compiler, "-Wall", "-Wextra", "-Werror", "-O2", str(PROBE_SOURCE), "-o", str(probe_binary)],
            "returncode": compile_result.returncode,
            "stdout": compile_result.stdout,
            "stderr": compile_result.stderr,
            "source_sha256": _sha256_file(PROBE_SOURCE),
            "binary_sha256": _sha256_file(probe_binary) if compile_result.returncode == 0 else None,
        }
        roles: list[dict[str, Any]] = []
        if compile_result.returncode == 0:
            for role in PROBE_ROLES:
                roles.append(
                    _run_role(
                        role=role,
                        probe_binary=probe_binary,
                        evidence_dir=evidence_dir,
                        fixture_root=fixture_root,
                        timeout_seconds=timeout_seconds,
                    )
                )

    classifications = [role.get("classification") for role in roles]
    if compile_result.returncode != 0 or any(item == "FAIL" for item in classifications):
        controls_status = "FAIL"
        exit_code = 1
    elif len(roles) != len(PROBE_ROLES) or any(item != "PASS" for item in classifications):
        controls_status = "UNKNOWN"
        exit_code = 2
    else:
        controls_status = "PASS"
        exit_code = 0

    current_uid = os.getuid() if hasattr(os, "getuid") else None
    current_gid = os.getgid() if hasattr(os, "getgid") else None
    role_uids = [
        role.get("child_record", {}).get("uid")
        for role in roles
        if isinstance(role.get("child_record"), dict)
    ]
    role_gids = [
        role.get("child_record", {}).get("gid")
        for role in roles
        if isinstance(role.get("child_record"), dict)
    ]
    receipt = {
        "schema": "comsol-mcp-t037-isolation-candidate/1",
        "observed_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate_process_controls": controls_status,
        "t037_acceptance": "UNVERIFIED",
        "scope": {
            "roles": list(PROBE_ROLES),
            "probe_type": "synthetic_compiled_c_process",
            "host_policy_changes": False,
            "account_created": False,
            "acl_or_firewall_changed": False,
            "comsol_launched": False,
            "production_worker_or_server_integrated": False,
            "sandbox_exec_deprecated": True,
            "isolated_service_account_verified": False,
            "windows_target_verified": False,
        },
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.mac_ver()[0],
            "machine": platform.machine(),
            "interactive_uid": current_uid,
            "interactive_gid": current_gid,
            "role_uids": role_uids,
            "role_gids": role_gids,
            "same_uid_as_interactive": all(uid == current_uid for uid in role_uids),
        },
        "compile": compile_receipt,
        "roles": roles,
        "limitations": [
            "A synthetic probe PASS is not proof that COMSOL Java worker or COMSOL Server works under this policy.",
            "sandbox-exec is deprecated; no supported production policy has been approved.",
            "The test uses the current account and does not prove a separate service identity or ACL boundary.",
            "Windows is an original target and remains unverified by this macOS-only candidate.",
            "No license-manager, real MCP endpoint, model, save, GUI, study, or solver operation was tested.",
        ],
    }
    receipt_path = evidence_dir / "receipt.json"
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write("\n")
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_sha256"] = _sha256_file(receipt_path)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return {"exit_code": exit_code, "receipt": receipt}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-dir",
        required=True,
        type=Path,
        help="new empty absolute directory for raw candidate profiles and receipt",
    )
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.timeout_seconds > 30:
        parser.error("--timeout-seconds must be in (0, 30]")
    try:
        result = run_acceptance(args.evidence_dir, args.timeout_seconds)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "candidate_process_controls": "UNKNOWN",
                    "t037_acceptance": "UNVERIFIED",
                    "runner_error": f"{type(exc).__name__}: {exc}",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
