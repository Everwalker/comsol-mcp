"""Bound-model Java code contracts for W10.

Compilation and execution are separate operations.  The Python side records
the source hash and diagnostics, while the persistent Worker owns the only
COMSOL Model object that may be injected into the entrypoint.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any, Mapping

from ._execution_contract import ExecutionContractError, canonical_project_path


_ENTRYPOINT_RE = re.compile(r"(?:class|interface|record)\s+([A-Za-z_$][\w$]*)")
_DECLARED_EFFECT_RE = re.compile(r"(?m)^\s*//\s*effect\s*:\s*([A-Za-z_]+)\s*$")


def read_source(project_root: str | Path, source_artifact: str) -> tuple[Path, str, str]:
    if not isinstance(source_artifact, str) or not source_artifact.strip():
        raise ExecutionContractError("INVALID_REQUEST", "source_artifact is required")
    path = canonical_project_path(project_root, source_artifact)
    if not path.is_file() or path.suffix.lower() != ".java":
        raise ExecutionContractError("ARTIFACT_MISSING", "source_artifact must be an existing project-local .java file")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ExecutionContractError("INVALID_REQUEST", "source artifact exceeds the 2 MiB source limit")
    source = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return path, source, digest


def describe_source(project_root: str | Path, source_artifact: str, entrypoint: str = "") -> dict[str, Any]:
    path, source, digest = read_source(project_root, source_artifact)
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
    return {"source_artifact": str(path), "source_sha256": digest, "bytes": len(source.encode("utf-8")),
            "entrypoint": chosen, "declared_effects": declared, "class_names": classes,
            "trusted_code_required": True,
            "limitations": ["Compilation and execution are not an OS sandbox; trusted_code requires explicit server permission."]}


def compile_result(*, description: Mapping[str, Any], worker_reply: Mapping[str, Any] | None = None) -> dict[str, Any]:
    reply = dict(worker_reply or {})
    payload = reply.get("result") if isinstance(reply.get("result"), Mapping) else reply
    failure = reply.get("failure") if isinstance(reply.get("failure"), Mapping) else {}
    diagnostics = payload.get("diagnostics") if isinstance(payload, Mapping) else None
    if not isinstance(diagnostics, list):
        nested = failure.get("diagnostics") if isinstance(failure, Mapping) else None
        diagnostics = nested if isinstance(nested, list) else []
    success = bool(reply.get("ok", reply.get("success", False))) and bool(payload.get("compiled", False)) if worker_reply is not None and isinstance(payload, Mapping) else False
    return {"success": success, "data": {"source_sha256": description["source_sha256"],
            "entrypoint": description["entrypoint"], "compiled": success,
            "diagnostics": diagnostics, "artifact": payload.get("artifact") if isinstance(payload, Mapping) else None},
            "error": None if success else {"code": "COMPILE_ERROR", "phase": "compile",
            "message": str(reply.get("message") or "Java compilation failed"), "partial_changes": False,
            "safe_retry": True, "engine_state": "IDLE"}}


def execution_result(*, description: Mapping[str, Any], worker_reply: Mapping[str, Any],
                     before: Mapping[str, Any] | None = None, after: Mapping[str, Any] | None = None) -> dict[str, Any]:
    ok = bool(worker_reply.get("ok", worker_reply.get("success", False)))
    failure = worker_reply.get("failure") if isinstance(worker_reply.get("failure"), Mapping) else {}
    data = {"source_sha256": description["source_sha256"], "entrypoint": description["entrypoint"],
            "worker": dict(worker_reply), "before": dict(before or {}), "after": dict(after or {}),
            "readback": worker_reply.get("result", worker_reply.get("data", {}))}
    if ok:
        return {"success": True, "data": data, "error": None, "execution_success": True}
    # The persistent Worker wraps an engine exception in its durable
    # ``failure`` object.  Equal shallow fingerprints after a dispatched Java
    # call cannot prove that no unsupported property changed, so nested
    # execution_state_unknown must propagate before any retry decision.
    unknown = bool(
        worker_reply.get("execution_state_unknown")
        or failure.get("execution_state_unknown")
        or worker_reply.get("status") in {"UNKNOWN", "RUNNING"}
    )
    partial_change = bool(
        worker_reply.get("partial_change")
        or failure.get("partial_change")
        or worker_reply.get("failed_item_may_have_changed")
        or failure.get("failed_item_may_have_changed")
        or (after and before and after != before)
    )
    return {"success": False, "data": {**data, "partial_change": partial_change,
            "execution_state_unknown": unknown}, "error": {"code": "EXECUTION_STATE_UNKNOWN" if unknown else "ENGINE_CALL_FAILED",
            "phase": "execute", "message": str(worker_reply.get("message") or worker_reply.get("failure") or "Java execution failed"),
            "partial_changes": partial_change, "safe_retry": False,
            "engine_state": "UNKNOWN" if unknown else "IDLE"}}
