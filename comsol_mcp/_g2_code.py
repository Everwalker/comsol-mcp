"""Bound-model Java code contracts for W10.

Compilation and execution are separate operations.  The Python side records
the source hash and diagnostics, while the persistent Worker owns the only
COMSOL Model object that may be injected into the entrypoint.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, PreWriteRefusal
from ._project_root import ProjectRootBinding, open_project_root_binding


_ENTRYPOINT_RE = re.compile(r"(?:class|interface|record)\s+([A-Za-z_$][\w$]*)")
_DECLARED_EFFECT_RE = re.compile(r"(?m)^\s*//\s*effect\s*:\s*([A-Za-z_]+)\s*$")
MAX_JAVA_SOURCE_BYTES = 2 * 1024 * 1024
MAX_JAVA_SOURCE_BASE64_CHARS = ((MAX_JAVA_SOURCE_BYTES + 2) // 3) * 4


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    path: Path
    relative_parts: tuple[str, ...]
    source: str
    raw_bytes: bytes
    sha256: str
    file_identity: Mapping[str, int]


def _relative_source_path(root: Path, requested: str | Path) -> tuple[Path, tuple[str, ...]]:
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExecutionContractError("ACCESS_VIOLATION", "source path could not be resolved safely") from exc
    if resolved.suffix.lower() != ".java":
        raise ExecutionContractError("ARTIFACT_MISSING", "source_artifact must be an existing project-local .java file")
    try:
        if resolved == root:
            parts: tuple[str, ...] = ()
        elif os.name == "nt":
            import ntpath
            root_text, source_text = os.fspath(root), os.fspath(resolved)
            common = ntpath.commonpath((ntpath.normcase(root_text), ntpath.normcase(source_text)))
            if common != ntpath.normcase(root_text):
                raise ValueError("outside project")
            relative = ntpath.relpath(source_text, root_text)
            parts = tuple(part for part in relative.split("\\") if part)
        else:
            parts = resolved.relative_to(root).parts
    except ValueError as exc:
        raise ExecutionContractError("PERMISSION_DENIED", "path escapes the approved project root") from exc
    if not parts or any(part in {".", "..", ".phase1-private", ".phase2-private", "control-private"} for part in parts):
        raise ExecutionContractError("PERMISSION_DENIED", "source path is not an allowed project-local Java file")
    return resolved, tuple(parts)


def read_source_snapshot(project_root: str | Path | ProjectRootBinding,
                         source_artifact: str | Path) -> SourceSnapshot:
    # Invalid source input is refused before opening the project root or making
    # any engine call; the dispatch witness still verifies that no write began.
    if not isinstance(source_artifact, (str, Path)) or not str(source_artifact).strip():
        raise PreWriteRefusal("INVALID_REQUEST", "source_artifact is required")
    owned_binding = not isinstance(project_root, ProjectRootBinding)
    binding = open_project_root_binding(project_root) if owned_binding else project_root
    try:
        try:
            path, parts = _relative_source_path(binding.path, source_artifact)
            raw, identity = binding.read_file(parts, max_bytes=MAX_JAVA_SOURCE_BYTES)
            try:
                source = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ExecutionContractError("INVALID_REQUEST", "source artifact must contain valid UTF-8") from exc
            return SourceSnapshot(path=path, relative_parts=parts, source=source, raw_bytes=raw,
                                  sha256=hashlib.sha256(raw).hexdigest(), file_identity=dict(identity))
        except ExecutionContractError as exc:
            raise _host_side_refusal(exc) from exc
    finally:
        if owned_binding:
            binding.close()


def worker_source_payload(snapshot: SourceSnapshot) -> dict[str, str]:
    """Build the private v2 Worker fields from the exact bytes just described."""
    encoded = base64.b64encode(snapshot.raw_bytes).decode("ascii")
    if len(snapshot.raw_bytes) > MAX_JAVA_SOURCE_BYTES or len(encoded) > MAX_JAVA_SOURCE_BASE64_CHARS:
        raise ExecutionContractError("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
    return {"source_name": snapshot.path.name, "source_snapshot_b64": encoded}


def read_source(project_root: str | Path, source_artifact: str) -> tuple[Path, str, str]:
    snapshot = read_source_snapshot(project_root, source_artifact)
    return snapshot.path, snapshot.source, snapshot.sha256


def _host_side_refusal(exc: ExecutionContractError) -> ExecutionContractError:
    """Give a host-side-only failure the explicit pre-dispatch (validation) stage.

    Path decisions, one bounded project-root read, and pure source analysis
    cannot issue an engine call, so the managed backend may publish their code
    as a definite refusal instead of EXECUTION_STATE_UNKNOWN. The claim is
    never trusted alone: _managed_backend._dispatch_with_witness still requires
    the dispatch witness to show no mutation-class engine dispatch happened.
    Failures that already declare a stage, or that report an unresolved engine
    state, keep their fail-closed shape.
    """
    if exc.stage is not None or exc.code == "EXECUTION_STATE_UNKNOWN":
        return exc
    return PreWriteRefusal(exc.code, str(exc), details=dict(exc.details))


def describe_snapshot(snapshot: SourceSnapshot, entrypoint: str = "") -> dict[str, Any]:
    try:
        return _describe_snapshot(snapshot, entrypoint)
    except ExecutionContractError as exc:
        raise _host_side_refusal(exc) from exc


def _describe_snapshot(snapshot: SourceSnapshot, entrypoint: str) -> dict[str, Any]:
    path, source, digest = snapshot.path, snapshot.source, snapshot.sha256
    classes = _ENTRYPOINT_RE.findall(source)
    chosen = entrypoint.strip() if isinstance(entrypoint, str) else ""
    if not chosen:
        chosen = classes[0] if len(classes) == 1 else ""
    if not chosen:
        raise ExecutionContractError("INVALID_REQUEST", "entrypoint is required when source does not contain exactly one class")
    if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:#[A-Za-z_$][\w$]*)?", chosen):
        raise ExecutionContractError("INVALID_REQUEST", "entrypoint must be a Java class or class#method name")
    if re.search(r"(?s)\bstatic\s*\{", source):
        raise ExecutionContractError("TRUSTED_CODE_REJECTED", "source contains a static initializer")
    declared = [value.lower() for value in _DECLARED_EFFECT_RE.findall(source)]
    return {"source_artifact": str(path), "source_sha256": digest, "bytes": len(snapshot.raw_bytes),
            "entrypoint": chosen, "declared_effects": declared, "class_names": classes,
            "trusted_code_required": True,
            "limitations": ["Compilation and execution are not an OS sandbox; trusted_code requires explicit server permission."]}


def describe_source(project_root: str | Path | ProjectRootBinding,
                    source_artifact: str | Path, entrypoint: str = "") -> dict[str, Any]:
    return describe_snapshot(read_source_snapshot(project_root, source_artifact), entrypoint)


def compile_result(*, description: Mapping[str, Any], worker_reply: Mapping[str, Any] | None = None) -> dict[str, Any]:
    reply = dict(worker_reply or {})
    payload = reply.get("result") if isinstance(reply.get("result"), Mapping) else reply
    failure = reply.get("failure") if isinstance(reply.get("failure"), Mapping) else {}
    diagnostics = payload.get("diagnostics") if isinstance(payload, Mapping) else None
    if not isinstance(diagnostics, list):
        nested = failure.get("diagnostics") if isinstance(failure, Mapping) else None
        diagnostics = nested if isinstance(nested, list) else []
    claimed_success = (worker_reply is not None and reply.get("ok") is True
                       and reply.get("status") == "SUCCEEDED")
    source_matches = (isinstance(payload, Mapping)
                      and payload.get("source_sha256") == description.get("source_sha256")
                      and payload.get("entrypoint") == description.get("entrypoint"))
    success = (claimed_success and reply.get("_source_binding_verified") is True
               and isinstance(payload, Mapping) and payload.get("compiled") is True and source_matches)
    failure_code = (failure.get("code") if isinstance(failure, Mapping) else None) or reply.get("code")
    if claimed_success and not source_matches:
        failure_code = "WORKER_SOURCE_BINDING_MISMATCH"
    error_code = str(failure_code) if failure_code in {
        "SOURCE_HASH_REQUIRED", "SOURCE_HASH_MISMATCH", "WORKER_SOURCE_BINDING_MISMATCH",
        "WORKER_SOURCE_BINDING_UNAVAILABLE",
    } else "COMPILE_ERROR"
    return {"success": success, "data": {"source_sha256": description["source_sha256"],
            "entrypoint": description["entrypoint"], "compiled": success,
            "worker_source_sha256": payload.get("source_sha256") if isinstance(payload, Mapping) else None,
            "worker_entrypoint": payload.get("entrypoint") if isinstance(payload, Mapping) else None,
            "diagnostics": diagnostics, "artifact": payload.get("artifact") if isinstance(payload, Mapping) else None},
            "error": None if success else {"code": error_code, "phase": "compile",
            "message": str(reply.get("message") or "Java compilation failed"), "partial_changes": False,
            "safe_retry": failure_code == "COMPILE_ERROR", "engine_state": "IDLE",
            **({"worker_failure_code": str(failure_code)} if failure_code else {})}}


def execution_result(*, description: Mapping[str, Any], worker_reply: Mapping[str, Any],
                     before: Mapping[str, Any] | None = None, after: Mapping[str, Any] | None = None,
                     expected_model_tag: str | None = None) -> dict[str, Any]:
    reply = dict(worker_reply) if isinstance(worker_reply, Mapping) else {}
    success_claim = reply.get("ok") is True or reply.get("status") == "SUCCEEDED"
    ok = reply.get("ok") is True and reply.get("status") == "SUCCEEDED"
    failure = reply.get("failure") if isinstance(reply.get("failure"), Mapping) else {}
    worker_result = reply.get("result")
    binding_matches = (
        reply.get("_source_binding_verified") is True
        and
        isinstance(worker_result, Mapping)
        and worker_result.get("executed") is True
        and worker_result.get("source_sha256") == description.get("source_sha256")
        and worker_result.get("entrypoint") == description.get("entrypoint")
        and (expected_model_tag is None or worker_result.get("model_tag") == expected_model_tag)
    )
    public_reply = {key: value for key, value in reply.items() if key != "_source_binding_verified"}
    data = {"source_sha256": description["source_sha256"], "entrypoint": description["entrypoint"],
            "worker": public_reply, "before": dict(before or {}), "after": dict(after or {}),
            "worker_source_sha256": worker_result.get("source_sha256") if isinstance(worker_result, Mapping) else None,
            "worker_entrypoint": worker_result.get("entrypoint") if isinstance(worker_result, Mapping) else None,
            "source_binding_verified": bool(ok and binding_matches),
            "readback": worker_result if isinstance(worker_result, Mapping) else {}}
    if ok and binding_matches:
        return {"success": True, "data": data, "error": None, "execution_success": True}
    # The persistent Worker wraps an engine exception in its durable
    # ``failure`` object.  Equal shallow fingerprints after a dispatched Java
    # call cannot prove that no unsupported property changed, so nested
    # execution_state_unknown must propagate before any retry decision.
    unknown_signal = bool(
        reply.get("execution_state_unknown")
        or failure.get("execution_state_unknown")
        or reply.get("status") in {"UNKNOWN", "RUNNING"}
        or (success_claim and not (ok and binding_matches))
    )
    failure_code = failure.get("code") or reply.get("code")
    # Only Worker outcomes with a protocol-level proof that the model action
    # did not run stay known. A missing, incomplete, or malformed post-dispatch
    # reply can follow a real action and must never be made retryable by a
    # shallow snapshot.
    known_pre_dispatch = (
        reply.get("ok") is False and reply.get("status") == "FAILED"
        and reply.get("execution_state_unknown") is not True
        and failure.get("execution_state_unknown") is not True
        and (reply.get("source_operation_dispatched") is False
             or failure.get("source_operation_dispatched") is False)
    )
    known_source_rejection = (
        failure_code in {"SOURCE_HASH_REQUIRED", "SOURCE_HASH_MISMATCH",
                          "WORKER_SOURCE_BINDING_MISMATCH", "WORKER_SOURCE_BINDING_UNAVAILABLE"}
        and reply.get("ok") is False and reply.get("status") == "FAILED"
        and (reply.get("execution_state_unknown") is False
             or failure.get("execution_state_unknown") is False)
    )
    diagnostics = failure.get("diagnostics", reply.get("diagnostics"))
    known_compile_failure = (
        failure_code in {"COMPILE_ERROR", "COMPILE_UNAVAILABLE", "TRUSTED_CODE_REJECTED"}
        and reply.get("ok") is False
        and reply.get("status") == "FAILED" and isinstance(diagnostics, list)
        and reply.get("execution_state_unknown") is not True
        and failure.get("execution_state_unknown") is not True
    )
    known_failure = known_pre_dispatch or known_source_rejection or known_compile_failure
    unknown = unknown_signal or not known_failure
    partial_change = bool(
        reply.get("partial_change")
        or failure.get("partial_change")
        or reply.get("failed_item_may_have_changed")
        or failure.get("failed_item_may_have_changed")
        or (after and before and after != before)
        or unknown
    )
    return {"success": False, "data": {**data, "partial_change": partial_change,
            "execution_state_unknown": unknown}, "error": {"code": "EXECUTION_STATE_UNKNOWN" if unknown else (
                str(failure_code) if known_source_rejection or known_compile_failure else "ENGINE_CALL_FAILED"),
            "phase": "execute", "message": str(reply.get("message") or reply.get("failure") or "Java execution failed"),
            "partial_changes": partial_change, "safe_retry": False,
            "engine_state": "UNKNOWN" if unknown else "IDLE",
            **({"worker_failure_code": str(failure_code)} if failure_code else {})},
            "execution_success": False}
