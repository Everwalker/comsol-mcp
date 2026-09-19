"""Static transaction planning and bounded partial-failure bookkeeping."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping
from uuid import uuid4

from ._execution_contract import ExecutionContractError
from ._g2_registry import validate_call


@dataclass(slots=True)
class TransactionRecord:
    transaction_id: str
    model_ref: Mapping[str, Any] | None
    actions: list[dict[str, Any]]
    invariants: list[dict[str, Any]]
    checkpoint_policy: str = "on_failure"
    status: str = "PREVIEW"
    applied: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    not_executed: list[dict[str, Any]] = field(default_factory=list)
    checkpoint_id: str | None = None
    execution_state_unknown: bool = False
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id, "model_ref": dict(self.model_ref) if self.model_ref else None,
            "actions": self.actions, "invariants": self.invariants, "checkpoint_policy": self.checkpoint_policy,
            "status": self.status, "applied": self.applied, "failed": self.failed,
            "not_executed": self.not_executed, "checkpoint_id": self.checkpoint_id,
            "execution_state_unknown": self.execution_state_unknown, "created_at": self.created_at,
        }


def _action_operation(action: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    operation = action.get("operation_id", action.get("operation"))
    arguments = action.get("arguments", {})
    if not isinstance(operation, str) or not operation:
        raise ExecutionContractError("INVALID_REQUEST", "transaction action requires operation_id")
    if not isinstance(arguments, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", "transaction action arguments must be an object")
    # A transaction action is nested beneath an outer, ticketed transaction;
    # its model identity and idempotency gate are supplied by that outer call.
    validate_call(operation, arguments, allow_unbound_identity=True)
    return operation, dict(arguments)


def preview_transaction(actions: Any, invariants: Any = None, *, model_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(actions, list):
        raise ExecutionContractError("INVALID_REQUEST", "actions must be an array")
    if invariants is not None and not isinstance(invariants, list):
        raise ExecutionContractError("INVALID_REQUEST", "invariants must be an array")
    rows: list[dict[str, Any]] = []
    writes = computes = 0
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            raise ExecutionContractError("INVALID_REQUEST", f"action {index} must be an object")
        operation, arguments = _action_operation(action)
        entry = validate_call(operation, arguments, allow_unbound_identity=True)
        effect = entry.effect.upper()
        writes += effect in {"WRITE", "STATE_WRITE", "FILE_WRITE", "TRUSTED_CODE"}
        computes += effect in {"COMPUTE", "EVALUATE"}
        rows.append({"index": index, "operation_id": operation, "effect": entry.effect,
                     "status": "READY", "model_ref": dict(model_ref) if model_ref else None})
    return {"status": "PREVIEW", "static_only": True, "engine_called": False,
            "actions": rows, "action_count": len(rows), "write_count": writes,
            "compute_count": computes, "invariants": list(invariants or []),
            "warnings": ["Static preview does not build geometry, execute code, or establish solver convergence."]}


def run_transaction(
    actions: list[dict[str, Any]],
    *,
    runner: Callable[[str, dict[str, Any], int], Mapping[str, Any]],
    invariants: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
    transaction_id: str | None = None,
) -> TransactionRecord:
    record = TransactionRecord(transaction_id or "txn-" + uuid4().hex, None, actions, list(invariants or []))
    record.checkpoint_id = checkpoint_id
    for index, action in enumerate(actions):
        operation, arguments = _action_operation(action)
        try:
            result = dict(runner(operation, arguments, index))
        except Exception as exc:
            result = {"success": False, "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": str(exc), "safe_retry": False}}
        row = {"index": index, "operation_id": operation, "result": result}
        if result.get("success") is True:
            record.applied.append(row)
            continue
        record.failed.append(row)
        record.not_executed.extend({"index": later, "operation_id": str(actions[later].get("operation_id", actions[later].get("operation", "")))} for later in range(index + 1, len(actions)))
        error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
        record.execution_state_unknown = bool(
            result.get("execution_state_unknown")
            or result.get("cleanup_failed")
            or error.get("code") == "EXECUTION_STATE_UNKNOWN"
            or error.get("code") == "UNKNOWN"
        )
        break
    if record.failed:
        record.status = "UNKNOWN" if record.execution_state_unknown else ("PARTIAL" if record.applied else "FAILED")
    else:
        record.status = "SUCCEEDED"
    return record


def transaction_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TransactionStore:
    """Durable JSON records for plan/trial/partial-failure evidence."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._records: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(value, dict): self._records = value
            except (OSError, json.JSONDecodeError):
                self._records = {}

    def put(self, record: TransactionRecord | Mapping[str, Any]) -> dict[str, Any]:
        value = record.as_dict() if isinstance(record, TransactionRecord) else dict(record)
        identifier = str(value.get("transaction_id") or "")
        if not identifier:
            raise ValueError("transaction_id is required")
        self._records[identifier] = value
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._records, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return value

    def get(self, identifier: str) -> dict[str, Any] | None:
        value = self._records.get(identifier)
        return dict(value) if isinstance(value, dict) else None

    def list(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self._records.values()]
