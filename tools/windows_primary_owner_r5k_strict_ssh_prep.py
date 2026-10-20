"""One-shot strict SSH wrapper for the reviewed R5K3C remote PREP payload.

This source is inert until a separate Main source-review record and direct
human one-shot authorization file bind the exact payload, command, manifest,
and five remote write targets. It is never called by the local package builder.
After Popen returns, every timeout is UNKNOWN while the exact returned Popen
object remains owned; the wrapper passively waits on that same process and
never kills, signals, duplicates, or resends the payload.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "repository/_state/windows-primary-owner-r5k3c-local-prep-20261014"
PAYLOAD_PATH = PACKAGE / "PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py"
CONTRACT_PATH = PACKAGE / "PRIMARY_OWNER_R5K_PREP_CONTRACT.json"
MANIFEST_PATH = PACKAGE / "PRIMARY_OWNER_R5K_LOCAL_PREP_MANIFEST.json"
REQUEST_PATH = ROOT / "repository/docs/full_project_continuation_v3/state/WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_REVIEW_REQUEST_R5K3C.json"
SOURCE_REVIEW_PATH = ROOT / "repository/docs/full_project_continuation_v3/state/WINDOWS_PRIMARY_TOKEN_OWNER_PREP_MAIN_SOURCE_REVIEW_R5K3C.json"
AUTHORIZATION_PATH = ROOT / "repository/_state/windows-primary-owner-r5k3c-prep-once-auth.json"
RUN_ROOT = ROOT / "repository/_state/windows-primary-owner-r5k3c-ssh-prep-20261014"
SSH_PATH = "/usr/bin/ssh"
SSH_USER_HOST = "everwalker@192.168.100.2"
REMOTE_PYTHON = r"C:\Users\Everwalker\AppData\Local\Programs\Python\Python312-comsol-mcp\python.exe"
REMOTE_COMMAND = '"' + REMOTE_PYTHON + '" -I -B -'
EXPECTED_PAYLOAD_SHA256 = "bb8d490ac965c830e60599f2865894a6bd0289d5dab59b92b7d1a227ac35676a"
EXPECTED_PAYLOAD_BYTES = 219696
ACTIVE_CUTOFF_SECONDS = 120
EXPECTED_REMOTE_STATUS = "REMOTE_PREPARED_CLOSED_NO_NATIVE_AUTHORITY"
EXPECTED_TARGETS = [
    r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\helpers-primary-owner-r1\windows_primary_token_owner_probe.py",
    r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\helpers-primary-owner-r1\windows_primary_token_owner_outer_gate.py",
    r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\authority-primary-owner-r1\BINDING.json",
    r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\authority-primary-owner-r1\PREBIRTH_BINDING.json",
    r"C:\Users\Everwalker\AppData\Local\cvw26-current-9f07a952265c\authority-primary-owner-r1\MAIN_APPROVAL.json",
]

OUTPUT_NAMES = (
    "SSH_LAUNCH_INTENT.json", "SSH.stdout", "SSH.stderr", "SSH_STATE.jsonl",
    "SSH_TERMINAL.json", "REMOTE_PREP_REPORT.json",
)
_UNKNOWN_POPEN_HANDLES: list[Any] = []


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                           parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(label + " is not strict duplicate-free UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(label + " root is not an object")
    return value


def read_regular(path: Path, *, maximum: int = 256 * 1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("expected an existing ordinary local file: " + str(path))
    with path.open("rb", buffering=0) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size < 0 or before.st_size > maximum:
            raise RuntimeError("local input exceeds its fixed byte bound: " + str(path))
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    sig = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns)
    if len(raw) > maximum or len(raw) != after.st_size or sig(before) != sig(after):
        raise RuntimeError("local input changed while the exact bytes were read: " + str(path))
    return raw


def ssh_argv() -> list[str]:
    return [
        SSH_PATH, "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "UpdateHostKeys=no", "-o", "ControlMaster=no", "-o", "ControlPath=none",
        SSH_USER_HOST, REMOTE_COMMAND,
    ]


def invocation_binding(payload_sha256: str = EXPECTED_PAYLOAD_SHA256,
                       payload_bytes: int = EXPECTED_PAYLOAD_BYTES) -> dict[str, Any]:
    argv = ssh_argv()
    return {
        "schema": "cv3-windows-primary-owner-r5k3c-ssh-invocation/1",
        "argv": argv,
        "argv_sha256": sha(canonical(argv)),
        "shell": False,
        "target": SSH_USER_HOST,
        "remote_command": REMOTE_COMMAND,
        "payload_sha256": payload_sha256,
        "payload_bytes": payload_bytes,
        "stdin_mode": "one exact binary payload write to the single original Popen stdin",
        "output_mode": "raw binary stdout/stderr redirected directly to exclusive local evidence files",
        "active_cutoff_seconds": ACTIVE_CUTOFF_SECONDS,
        "after_cutoff": "Persist UNKNOWN and passively wait on the same original Popen object to natural terminal; do not kill, signal, close, duplicate, or resend.",
        "remote_write_targets": list(EXPECTED_TARGETS),
        "native_child": "NOT_AUTHORIZED",
        "current_execution": "NOT_RUN_NO_SSH_CONNECTION_NO_PAYLOAD_SENT",
    }


def _source_hashes(payload_raw: bytes, contract_raw: bytes, lease_raw: bytes,
                   controls_raw: bytes, runner_raw: bytes, wrapper_raw: bytes) -> dict[str, str]:
    return {
        "repository/tools/prepare_primary_owner_r5k_local.py": sha(runner_raw),
        "repository/_state/windows-primary-owner-r5k3c-local-prep-20261014/PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py": sha(payload_raw),
        "repository/_state/windows-primary-owner-r5k3c-local-prep-20261014/PRIMARY_OWNER_R5K_PREP_CONTRACT.json": sha(contract_raw),
        "repository/tools/windows_primary_owner_authority_root_lease_r6.py": sha(lease_raw),
        "repository/tools/windows_primary_owner_r5k_strict_ssh_prep.py": sha(wrapper_raw),
        "repository/tests/test_windows_primary_token_owner_r5k_local_package.py": sha(controls_raw),
    }


def expected_authorization(*, payload_sha256: str, payload_bytes: int, manifest_sha256: str,
                           request_sha256: str, source_review_sha256: str,
                           invocation_sha256: str) -> dict[str, Any]:
    """Return the exact authorization schema for review and synthetic controls.

    This function does not create approval. Direct user authorization and a
    Main PASS source review are separate required inputs to the wrapper.
    """
    return {
        "schema": "cv3-windows-primary-owner-r5k3c-human-prep-authorization/1",
        "status": "HUMAN_AUTHORIZED_STRICT_SSH_PREP_ONCE",
        "payload_sha256": payload_sha256,
        "payload_bytes": payload_bytes,
        "manifest_sha256": manifest_sha256,
        "source_review_request_sha256": request_sha256,
        "main_source_review_sha256": source_review_sha256,
        "ssh_invocation_sha256": invocation_sha256,
        "remote_write_targets": list(EXPECTED_TARGETS),
        "remote_write_count": 5,
        "active_cutoff_seconds": ACTIVE_CUTOFF_SECONDS,
        "passive_wait_same_original_popen_after_cutoff": True,
        "resend_or_retry": False,
        "native_child_authority": False,
        "COMSOL_GUI_JVM_authority": False,
    }


def _validate_inputs(payload_path: Path, contract_path: Path, manifest_path: Path,
                     request_path: Path, source_review_path: Path, authorization_path: Path,
                     runner_path: Path, lease_path: Path, controls_path: Path,
                     wrapper_path: Path) -> tuple[bytes, dict[str, Any]]:
    payload_raw = read_regular(payload_path)
    if len(payload_raw) != EXPECTED_PAYLOAD_BYTES or sha(payload_raw) != EXPECTED_PAYLOAD_SHA256:
        raise RuntimeError("PREP payload bytes differ from the exact R5K3C approved-candidate digest")
    contract_raw = read_regular(contract_path)
    contract = strict_json(contract_raw, "R5K3C PREP contract")
    manifest_raw = read_regular(manifest_path)
    manifest = strict_json(manifest_raw, "R5K3C candidate manifest")
    request_raw = read_regular(request_path)
    request = strict_json(request_raw, "R5K3C Main review request")
    review_raw = read_regular(source_review_path)
    review = strict_json(review_raw, "R5K3C Main source review")
    authorization_raw = read_regular(authorization_path, maximum=1024 * 1024)
    authorization = strict_json(authorization_raw, "human PREP authorization")
    runner_raw, lease_raw = read_regular(runner_path), read_regular(lease_path)
    controls_raw, wrapper_raw = read_regular(controls_path), read_regular(wrapper_path)
    files = _source_hashes(payload_raw, contract_raw, lease_raw, controls_raw,
                           runner_raw, wrapper_raw)
    invocation = invocation_binding()
    if contract.get("local_payload", {}).get("sha256") != EXPECTED_PAYLOAD_SHA256:
        raise RuntimeError("contract does not bind the exact approved payload SHA")
    invocation_contract = contract.get("remote_invocation")
    if (not isinstance(invocation_contract, dict)
            or invocation_contract.get("invocation") != invocation
            or invocation_contract.get("strict_ssh_wrapper_sha256") != sha(wrapper_raw)):
        raise RuntimeError("contract SSH command/wrapper binding differs from this exact wrapper")
    if (manifest.get("files", {}).get("PRIMARY_OWNER_R5K_REMOTE_PREP_SCRIPT.py") !=
            {"sha256": EXPECTED_PAYLOAD_SHA256, "bytes": EXPECTED_PAYLOAD_BYTES}):
        raise RuntimeError("candidate manifest does not bind the exact remote payload")
    if request.get("status") != "PENDING_MAIN_SOURCE_REVIEW_NO_REMOTE_AUTHORITY":
        raise RuntimeError("review request must remain scoped to source review, not remote authority")
    review_files = {row.get("path"): row.get("sha256")
                    for row in request.get("main_source_review_scope_six_files", []) if isinstance(row, dict)}
    if review_files != files:
        raise RuntimeError("local source bytes differ from the exact six-file Main-review request")
    if (review.get("schema") != "cv3-windows-primary-owner-prep-source-review-r5k3c/1"
            or review.get("status") != "PASS_SOURCE_REVIEW_NO_NATIVE_AUTHORITY"
            or review.get("source_review_request_sha256") != sha(request_raw)
            or review.get("candidate_manifest_sha256") != sha(manifest_raw)
            or review.get("hashes") != files):
        raise RuntimeError("Main review does not PASS this exact R5K3C source/manifest/request")
    expected = expected_authorization(
        payload_sha256=EXPECTED_PAYLOAD_SHA256, payload_bytes=EXPECTED_PAYLOAD_BYTES,
        manifest_sha256=sha(manifest_raw), request_sha256=sha(request_raw),
        source_review_sha256=sha(review_raw), invocation_sha256=sha(canonical(invocation)),
    )
    if authorization != expected:
        raise RuntimeError("direct human authorization is absent or does not bind this exact one-shot PREP")
    return payload_raw, {
        "payload_sha256": sha(payload_raw), "payload_bytes": len(payload_raw),
        "contract_sha256": sha(contract_raw), "manifest_sha256": sha(manifest_raw),
        "review_request_sha256": sha(request_raw), "source_review_sha256": sha(review_raw),
        "authorization_sha256": sha(authorization_raw), "invocation": invocation,
        "invocation_sha256": sha(canonical(invocation)), "source_hashes": files,
    }


def _write_exclusive(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _append_state(path: Path, status: str, *, pid: int | None, started: float,
                  details: Mapping[str, Any] | None = None) -> None:
    record = {"status": status, "original_local_ssh_pid": pid,
              "elapsed_seconds": time.monotonic() - started, **dict(details or {})}
    with path.open("ab", buffering=0) as stream:
        stream.write(json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
        os.fsync(stream.fileno())


def _output_digest(path: Path) -> dict[str, Any]:
    raw = read_regular(path, maximum=1024 * 1024 * 1024)
    return {"path": str(path), "bytes": len(raw), "sha256": sha(raw)}


_ACTIVE_POPEN_OWNERS: list["OriginalPopenOwner"] = []


class OriginalPopenOwner:
    """Strong owner that never returns while the exact Popen is live.

    Journal and observation failures are diagnostic data. They cannot bypass
    the passive same-object hold, and they never cause a second payload send.
    The guard owns the process object and its original bound communicate/wait
    methods until a natural terminal code is observed.
    """

    def __init__(self, child: Any, *, state_path: Path, started: float,
                 active_cutoff_seconds: float = ACTIVE_CUTOFF_SECONDS,
                 state_writer: Callable[..., Any] = _append_state,
                 fault_hook: Callable[[str], None] | None = None):
        self.child = child
        self.pid = getattr(child, "pid", None)
        self._communicate = child.communicate
        self._wait = child.wait
        self.state_path = state_path
        self.started = started
        self.active_cutoff_seconds = active_cutoff_seconds
        self.state_writer = state_writer
        self.fault_hook = fault_hook
        self.first_cause: BaseException | None = None
        self.diagnostic_errors: list[dict[str, str]] = []
        self.suppressed_diagnostics = 0
        self.failed_journal_statuses: set[str] = set()
        self.payload_send_attempted = False
        self.active_cutoff_exceeded = False
        self.terminal_observed = getattr(child, "returncode", None) is not None
        if not any(item is self for item in _ACTIVE_POPEN_OWNERS):
            _ACTIVE_POPEN_OWNERS.append(self)

    def remember_first(self, cause: BaseException) -> None:
        if self.first_cause is None:
            self.first_cause = cause

    def _remember_diagnostic(self, stage: str, error: BaseException) -> None:
        self.remember_first(error)
        if len(self.diagnostic_errors) < 32:
            self.diagnostic_errors.append({"stage": stage, "error_type": type(error).__name__,
                                           "error": str(error)[:2000]})
        else:
            self.suppressed_diagnostics += 1

    def record_state(self, status: str, details: Mapping[str, Any] | None = None) -> bool:
        if status in self.failed_journal_statuses:
            self.suppressed_diagnostics += 1
            return False
        try:
            self.state_writer(self.state_path, status, pid=self.pid, started=self.started,
                              details=details)
            return True
        except BaseException as exc:
            self.failed_journal_statuses.add(status)
            self._remember_diagnostic("state_journal:" + status, exc)
            return False

    def _fault(self, operation: str) -> None:
        if self.fault_hook is not None:
            self.fault_hook(operation)

    def send_payload_once(self, payload: bytes) -> None:
        if self.payload_send_attempted:
            error = RuntimeError("payload send already entered; no retry or resend")
            self.remember_first(error)
            self.record_state("UNKNOWN_DUPLICATE_SEND_REFUSED", {"action": "NO_RESEND"})
            self.hold_until_terminal()
            return
        self.payload_send_attempted = True
        try:
            self._fault("communicate")
            self._communicate(input=payload, timeout=self.active_cutoff_seconds)
        except subprocess.TimeoutExpired:
            self.active_cutoff_exceeded = True
            self.record_state("UNKNOWN_ACTIVE_CUTOFF_SAME_POPEN_RETAINED", {
                "cutoff_seconds": self.active_cutoff_seconds,
                "action": "PASSIVE_WAIT_SAME_ORIGINAL_POPEN_NO_SIGNAL_NO_RESEND",
            })
        except BaseException as exc:
            self._remember_diagnostic("payload_communicate", exc)
            self.record_state("UNKNOWN_COMMUNICATE_ERROR_SAME_POPEN_RETAINED", {
                "error_type": type(exc).__name__, "error": str(exc),
                "action": "PASSIVE_WAIT_SAME_ORIGINAL_POPEN_NO_SIGNAL_NO_RESEND",
            })
        self.hold_until_terminal()

    def hold_until_terminal(self) -> None:
        """Wait forever if needed, using only this Popen and no input resend."""
        if self.terminal_observed:
            return
        while getattr(self.child, "returncode", None) is None:
            try:
                self._fault("communicate")
                self._communicate()
            except BaseException as exc:
                self._remember_diagnostic("passive_communicate", exc)
                self.record_state("UNKNOWN_PASSIVE_COMMUNICATE_ERROR_SAME_POPEN", {
                    "error_type": type(exc).__name__, "error": str(exc),
                    "action": "RETAIN_AND_WAIT_SAME_ORIGINAL_POPEN_NO_SIGNAL_NO_RESEND",
                })
                # A failed drain may leave the child waiting for stdin. Close
                # only the original input pipe to deliver EOF; do not close the
                # Popen/process handle, signal it, or send another byte.
                stream = getattr(self.child, "stdin", None)
                if stream is not None and not getattr(stream, "closed", True):
                    try:
                        stream.close()
                    except BaseException as close_error:
                        self._remember_diagnostic("original_stdin_eof", close_error)
            if getattr(self.child, "returncode", None) is None:
                try:
                    self._fault("wait")
                    self._wait()
                except BaseException as exc:
                    self._remember_diagnostic("passive_wait", exc)
                    self.record_state("UNKNOWN_WAIT_ERROR_SAME_POPEN_RETAINED", {
                        "error_type": type(exc).__name__, "error": str(exc),
                        "action": "RETAIN_AND_WAIT_SAME_ORIGINAL_POPEN_NO_SIGNAL_NO_RESEND",
                    })
            if getattr(self.child, "returncode", None) is None:
                try:
                    time.sleep(0.01)
                except BaseException as exc:
                    self._remember_diagnostic("passive_wait_pause", exc)

        self.terminal_observed = True
        for index, owner in enumerate(_ACTIVE_POPEN_OWNERS):
            if owner is self:
                del _ACTIVE_POPEN_OWNERS[index]
                break
        self.record_state("NATURAL_TERMINAL_OBSERVED", {
            "returncode": getattr(self.child, "returncode", None),
            "active_cutoff_exceeded": self.active_cutoff_exceeded,
            "first_cause": (type(self.first_cause).__name__ + ":" + str(self.first_cause)
                            if self.first_cause is not None else None),
            "diagnostic_error_count": len(self.diagnostic_errors) + self.suppressed_diagnostics,
        })


def _validate_remote_result(stdout_path: Path, returncode: int) -> tuple[str, dict[str, Any] | None]:
    if returncode != 0:
        return "FAILED_REMOTE_PREP_SSH_NONZERO_NO_REPLAY", None
    raw = read_regular(stdout_path, maximum=1024 * 1024 * 1024)
    try:
        remote = strict_json(raw, "remote PREP stdout")
    except RuntimeError:
        return "FAILED_REMOTE_PREP_STDOUT_INVALID_NO_REPLAY", None
    native = remote.get("native_child")
    if (remote.get("status") != EXPECTED_REMOTE_STATUS
            or remote.get("r14", {}).get("source_helper_count") != 179
            or remote.get("r14", {}).get("stdlib_hash_count") != 179
            or not isinstance(native, dict)
            or native.get("CreateProcessWithTokenW") != "NOT_CALLED"
            or native.get("child_processes") != 0
            or remote.get("remote_objects", {}).get("once_absent") is not True
            or remote.get("remote_objects", {}).get("output_absent") is not True
            or remote.get("remote_objects", {}).get("cases_absent") is not True):
        return "FAILED_REMOTE_PREP_RESULT_SCOPE_NO_REPLAY", remote
    return "PASS_REMOTE_PREP_CLOSED_NO_NATIVE_AUTHORITY", remote


def main(*, popen_factory: Callable[..., Any] = subprocess.Popen,
         run_root: Path = RUN_ROOT, payload_path: Path = PAYLOAD_PATH,
         contract_path: Path = CONTRACT_PATH, manifest_path: Path = MANIFEST_PATH,
         request_path: Path = REQUEST_PATH, source_review_path: Path = SOURCE_REVIEW_PATH,
         authorization_path: Path = AUTHORIZATION_PATH, runner_path: Path = ROOT / "repository/tools/prepare_primary_owner_r5k_local.py",
         lease_path: Path = ROOT / "repository/tools/windows_primary_owner_authority_root_lease_r6.py",
         controls_path: Path = ROOT / "repository/tests/test_windows_primary_token_owner_r5k_local_package.py",
         wrapper_path: Path = Path(__file__).resolve(),
         state_writer: Callable[..., Any] = _append_state,
         fault_hook: Callable[[str], None] | None = None,
         active_cutoff_seconds: float = ACTIVE_CUTOFF_SECONDS) -> int:
    if run_root.exists() or run_root.is_symlink():
        raise RuntimeError("strict SSH run evidence path already exists; no overwrite, retry, or replay")
    payload, binding = _validate_inputs(payload_path, contract_path, manifest_path, request_path,
                                        source_review_path, authorization_path, runner_path,
                                        lease_path, controls_path, wrapper_path)
    run_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    intent_path, stdout_path, stderr_path, state_path, terminal_path, report_path = (
        run_root / name for name in OUTPUT_NAMES
    )
    started = time.monotonic()
    launch_intent = {
        "schema": "cv3-windows-primary-owner-r5k3c-ssh-launch-intent/1",
        "status": "STRICT_SSH_LAUNCH_INTENT_ONE_PAYLOAD_SEND",
        **binding,
        "output_directory": str(run_root),
        "popen_birth": "NOT_YET_OBSERVED",
        "retry": False,
    }
    _write_exclusive(intent_path, canonical(launch_intent))
    state_path.open("xb").close()
    child = None
    with stdout_path.open("xb", buffering=0) as stdout_file, stderr_path.open("xb", buffering=0) as stderr_file:
        try:
            child = popen_factory(
                binding["invocation"]["argv"], stdin=subprocess.PIPE,
                stdout=stdout_file, stderr=stderr_file, shell=False, cwd=str(ROOT),
            )
        except BaseException as exc:
            terminal = {
                "schema": "cv3-windows-primary-owner-r5k3c-ssh-terminal/1",
                "status": "UNKNOWN_POPEN_CONSTRUCTION_NO_REPLAY",
                "original_local_ssh_pid": None, "original_local_ssh_returncode": None,
                "error_type": type(exc).__name__, "error": str(exc),
                "payload_may_have_been_sent": "UNKNOWN", "retry": False,
                **binding,
            }
            try:
                _write_exclusive(terminal_path, canonical(terminal))
                state_writer(state_path, terminal["status"], pid=None, started=started)
                _write_exclusive(report_path, canonical({"status": terminal["status"], "terminal": terminal}))
            except BaseException:
                # There is no returned original Popen to hold. Preserve the
                # launch attempt and never retry, even if local evidence fails.
                pass
            return 1
        # Install the process owner immediately after birth, before logging or
        # any other operation can fail. Its finally path is the only route out
        # of this frame and cannot return while this exact Popen is live.
        owner = OriginalPopenOwner(child, state_path=state_path, started=started,
                                   active_cutoff_seconds=active_cutoff_seconds,
                                   state_writer=state_writer, fault_hook=fault_hook)
        remote = None
        remote_status = "UNKNOWN_POST_POPEN_NO_REPLAY"
        try:
            valid_pid = type(owner.pid) is int and owner.pid > 0
            if not valid_pid:
                owner.remember_first(RuntimeError("original Popen PID is malformed"))
                owner.record_state("UNKNOWN_POPEN_PID_MALFORMED_NO_REPLAY")
            else:
                birth_logged = owner.record_state("ORIGINAL_POPEN_BIRTH_OBSERVED", {
                    "payload_sha256": binding["payload_sha256"],
                    "payload_bytes": binding["payload_bytes"],
                })
                # If the birth record cannot be persisted, do not send even the
                # first byte. Close only stdin by passive communicate and hold
                # the same child to terminal.
                if birth_logged and owner.first_cause is None:
                    owner.send_payload_once(payload)
                else:
                    owner.hold_until_terminal()
            # Keep the owner guard outside all readback/report operations too.
            owner.hold_until_terminal()
            stdout_file.flush(); os.fsync(stdout_file.fileno())
            stderr_file.flush(); os.fsync(stderr_file.fileno())
            remote_status, remote = _validate_remote_result(stdout_path, child.returncode)
            final_status = ("UNKNOWN_POST_POPEN_FAILURE_TERMINAL_NO_REPLAY"
                            if owner.first_cause is not None else remote_status)
        except BaseException as exc:
            owner.remember_first(exc)
            owner.record_state("UNKNOWN_POST_POPEN_EXCEPTION_SAME_OWNER_RETAINED", {
                "error_type": type(exc).__name__, "error": str(exc),
            })
            final_status = "UNKNOWN_POST_POPEN_FAILURE_TERMINAL_NO_REPLAY"
            remote = None
        finally:
            # Unconditional owner guard: log, classification, or report
            # failures cannot let a live local SSH child escape this scope.
            owner.hold_until_terminal()

    terminal = {
        "schema": "cv3-windows-primary-owner-r5k3c-ssh-terminal/1",
        "status": "PASSIVE_TERMINAL_AFTER_ACTIVE_CUTOFF" if owner.active_cutoff_exceeded else "NATURAL_TERMINAL_OBSERVED",
        "original_local_ssh_pid": owner.pid,
        "original_local_ssh_returncode": child.returncode,
        "active_cutoff_seconds": active_cutoff_seconds,
        "active_cutoff_exceeded": owner.active_cutoff_exceeded,
        "elapsed_seconds": time.monotonic() - started,
        "argv": binding["invocation"]["argv"],
        "payload_sha256": binding["payload_sha256"],
        "payload_bytes": binding["payload_bytes"],
        "stdout": _output_digest(stdout_path),
        "stderr": _output_digest(stderr_path),
        "remote_status": remote.get("status") if remote else None,
        "final_classification": final_status,
        "same_original_popen_retained_through_terminal": owner.terminal_observed,
        "payload_send_attempted_once": owner.payload_send_attempted,
        "signals_kills_retries_resends": 0,
        "first_cause": (type(owner.first_cause).__name__ + ":" + str(owner.first_cause)
                        if owner.first_cause is not None else None),
        "diagnostic_errors": owner.diagnostic_errors,
        "suppressed_diagnostic_count": owner.suppressed_diagnostics,
        **{key: binding[key] for key in ("contract_sha256", "manifest_sha256", "review_request_sha256",
                                         "source_review_sha256", "authorization_sha256",
                                         "invocation_sha256", "source_hashes")},
    }
    _write_exclusive(terminal_path, canonical(terminal))
    report = {
        "schema": "cv3-windows-primary-owner-r5k3c-ssh-actual-report/1",
        "status": final_status,
        "remote": remote,
        "terminal": terminal,
        "evidence": {
            "stdout": _output_digest(stdout_path), "stderr": _output_digest(stderr_path),
            "state": _output_digest(state_path), "terminal": _output_digest(terminal_path),
        },
        "replay": False,
        "native_child": "NOT_AUTHORIZED",
    }
    _write_exclusive(report_path, canonical(report))
    return 0 if final_status == "PASS_REMOTE_PREP_CLOSED_NO_NATIVE_AUTHORITY" else 1


def cli_entrypoint(main_callable: Callable[[], int] = main) -> int:
    """Return the CLI status; the final SystemExit is deliberately outside this guard."""
    try:
        code = main_callable()
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int else 1
    except BaseException as exc:
        try:
            sys.stderr.write("STRICT_SSH_PREP_FAILED_NO_RETRY: " + type(exc).__name__ + ": " + str(exc) + "\n")
            sys.stderr.flush()
        except BaseException:
            pass
        return 1
    if type(code) is not int or not 0 <= code <= 255:
        return 1
    return code


if __name__ == "__main__":
    # SystemExit for a successful main return is not caught and converted into
    # a failure string. Any post-Popen exception has already passed through
    # OriginalPopenOwner's unconditional natural-terminal guard in main().
    raise SystemExit(cli_entrypoint())
