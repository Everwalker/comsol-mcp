"""Static transaction planning and bounded partial-failure bookkeeping.

This sidecar is the sole authority for transaction records. OperationStore
SQLite is the sole authority for checkpoint/job metadata; checkpoint_id here
is only a reference, not a second checkpoint record.

R04: ``execution_status`` (did every action run) and ``verification_status``
(did the declared invariants hold on the bound model) are separate claims.
Invariants are a small, typed, executable vocabulary - not free-form text - and
a required invariant that is unsupported or malformed is rejected before the
first action is dispatched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from datetime import datetime, timezone
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
    # R04: execution and verification are different claims, bound to one model
    # observation.  ``status`` mirrors ``execution_status`` for compatibility.
    execution_status: str = "NOT_EXECUTED"
    verification_status: str = "NOT_RUN"
    invariant_results: dict[str, Any] = field(default_factory=dict)
    revision: int | None = None
    pre_revision: int | None = None
    error: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id, "model_ref": dict(self.model_ref) if self.model_ref else None,
            "actions": self.actions, "invariants": self.invariants, "checkpoint_policy": self.checkpoint_policy,
            "status": self.status, "applied": self.applied, "failed": self.failed,
            "not_executed": self.not_executed, "checkpoint_id": self.checkpoint_id,
            "execution_state_unknown": self.execution_state_unknown, "created_at": self.created_at,
            "execution_status": self.execution_status, "verification_status": self.verification_status,
            "invariant_results": self.invariant_results, "revision": self.revision,
            "pre_revision": self.pre_revision, "error": self.error,
        }


# ---------------------------------------------------------------------------
# R04: typed, executable invariant vocabulary.
# ---------------------------------------------------------------------------

# Every supported type is data-only: paths are NodePath values (never an
# expression), values are TypedValue objects, and each check names exactly the
# fields it needs.  There is deliberately no free-form predicate or code hook.
INVARIANT_TYPES: dict[str, frozenset[str]] = {
    "node_exists": frozenset({"path"}),
    "node_type": frozenset({"path", "type_id"}),
    "property_equals": frozenset({"path", "name", "value"}),
    "property_tolerance": frozenset({"path", "name", "value", "tolerance"}),
    "selection_non_empty": frozenset({"path", "selection_name"}),
    "selection_dimension": frozenset({"path", "dimension"}),
}


def _invariant_path_error(value: Any) -> tuple[str, str] | None:
    from ._g2_contract import NodePath
    try:
        NodePath.from_wire(value)
    except ExecutionContractError as exc:
        return str(exc), exc.code
    return None


def _validate_invariant_declaration(kind: str, declaration: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(error, code)`` for one declaration of a supported type."""
    from ._g2_contract import validate_typed_value

    unknown = sorted(set(declaration) - ({"type", "required"} | INVARIANT_TYPES[kind]))
    if unknown:
        return f"unsupported invariant fields: {', '.join(unknown)}", "INVALID_INVARIANT"
    path_error = _invariant_path_error(declaration.get("path"))
    if path_error is not None:
        return path_error
    if kind == "node_type":
        if not isinstance(declaration.get("type_id"), str) or not declaration["type_id"]:
            return "node_type requires a non-empty type_id", "INVALID_INVARIANT"
    elif kind in {"property_equals", "property_tolerance"}:
        if not isinstance(declaration.get("name"), str) or not declaration["name"]:
            return f"{kind} requires a non-empty property name", "INVALID_INVARIANT"
    if kind == "property_equals":
        try:
            validate_typed_value(declaration.get("value"))
        except ExecutionContractError as exc:
            return f"property_equals value is invalid: {exc}", "INVALID_INVARIANT"
    elif kind == "property_tolerance":
        for field_name in ("value", "tolerance"):
            number = declaration.get(field_name)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)):
                return f"property_tolerance {field_name} must be a finite number", "INVALID_INVARIANT"
        if float(declaration["tolerance"]) < 0:
            return "property_tolerance tolerance must not be negative", "INVALID_INVARIANT"
    elif kind == "selection_non_empty":
        name = declaration.get("selection_name")
        if name is not None and (not isinstance(name, str) or not name):
            return "selection_name must be a non-empty string when present", "INVALID_INVARIANT"
    elif kind == "selection_dimension":
        dimension = declaration.get("dimension")
        if isinstance(dimension, bool) or not isinstance(dimension, int) or not 0 <= dimension <= 3:
            return "selection_dimension dimension must be an integer between 0 and 3", "INVALID_INVARIANT"
    return None, None


def parse_invariants(invariants: Any) -> list[dict[str, Any]]:
    """Classify every declaration without touching the engine.

    Each row reports ``supported``, ``required`` and the exact error that makes
    a declaration unsupported, so a caller can reject required ones before any
    action and still report optional ones as NOT_RUN.
    """
    if invariants is None:
        return []
    if not isinstance(invariants, list):
        raise ExecutionContractError("INVALID_REQUEST", "invariants must be an array")
    rows: list[dict[str, Any]] = []
    for index, declaration in enumerate(invariants):
        row: dict[str, Any] = {"index": index, "declaration": declaration, "type": None,
                               "required": True, "supported": False, "error": None, "code": None}
        rows.append(row)
        if not isinstance(declaration, Mapping):
            row.update(error="invariant must be an object", code="INVALID_INVARIANT")
            continue
        required = declaration.get("required", True)
        if type(required) is not bool:
            row.update(error="required must be a boolean", code="INVALID_INVARIANT")
            continue
        row["required"] = required
        kind = declaration.get("type")
        if not isinstance(kind, str) or not kind:
            row.update(error="invariant type is required", code="INVALID_INVARIANT")
            continue
        row["type"] = kind
        if kind not in INVARIANT_TYPES:
            row.update(error=f"unsupported invariant type {kind!r}; supported: {', '.join(sorted(INVARIANT_TYPES))}",
                       code="INVARIANT_UNSUPPORTED")
            continue
        error, code = _validate_invariant_declaration(kind, declaration)
        if error:
            row.update(error=error, code=code)
            continue
        row["supported"] = True
    return rows


def validate_invariants(invariants: Any) -> list[dict[str, Any]]:
    """Reject any unsupported/malformed *required* invariant before any action."""
    rows = parse_invariants(invariants)
    for row in rows:
        if row["required"] and row["error"]:
            raise ExecutionContractError(row["code"] or "INVALID_INVARIANT",
                                         f"invariant {row['index']}: {row['error']}")
    return rows


def _invariant_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    required = [check for check in checks if check.get("required")]
    optional = [check for check in checks if not check.get("required")]
    return {
        "total": len(checks), "not_run": sum(1 for check in checks if check.get("status") == "NOT_RUN"),
        "required": len(required), "required_passed": sum(1 for check in required if check.get("status") == "PASS"),
        "required_failed": sum(1 for check in required if check.get("status") != "PASS"),
        "optional": len(optional), "optional_passed": sum(1 for check in optional if check.get("status") == "PASS"),
        "optional_failed": sum(1 for check in optional if check.get("status") != "PASS"),
    }


def _verification_results(*, status: str, checks: list[dict[str, Any]], model_ref: Any, revision: Any,
                          unsupported: list[dict[str, Any]], reason: str | None) -> dict[str, Any]:
    return {"scope": "model_state", "status": status, "checks": checks, "unsupported": unsupported,
            "model_ref": dict(model_ref) if isinstance(model_ref, Mapping) else None,
            "revision": revision, "counts": _invariant_counts(checks), "reason": reason}


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
    # A static preview still classifies the declared invariants: a required
    # unsupported/malformed declaration is rejected here, exactly as the apply
    # path does before its first action.
    declarations = validate_invariants(invariants)
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
            "invariant_checks": [{"index": row["index"], "type": row["type"], "required": row["required"],
                                  "supported": row["supported"], "error": row["error"]} for row in declarations],
            "warnings": ["Static preview does not build geometry, execute code, or establish solver convergence.",
                         "Declared invariants are evaluated against the live bound model only when the transaction applies."]}


def run_transaction(
    actions: list[dict[str, Any]],
    *,
    runner: Callable[[str, dict[str, Any], int], Mapping[str, Any]],
    invariants: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
    transaction_id: str | None = None,
    model_ref: Mapping[str, Any] | None = None,
    revision: int | None = None,
    pre_revision: int | None = None,
    invariant_verifier: Callable[[Mapping[str, Any], int], Mapping[str, Any]] | None = None,
) -> TransactionRecord:
    """Execute every action, then evaluate the declared invariants for real.

    The preflight runs before the first action: an unsupported or malformed
    *required* invariant aborts the call, while an optional one is recorded as
    NOT_RUN and never counted as a pass.  ``invariant_verifier`` is supplied by
    the engine layer and reads the live bound model; its reply is normalized so
    an unknown status can never become a pass.
    """
    declarations = validate_invariants(invariants)
    required_supported = [row for row in declarations if row["required"] and row["supported"]]
    if required_supported and invariant_verifier is None:
        raise ExecutionContractError(
            "INVARIANT_UNSUPPORTED",
            "a required invariant was declared but no live model verifier is available",
        )
    record = TransactionRecord(transaction_id or "txn-" + uuid4().hex,
                               dict(model_ref) if isinstance(model_ref, Mapping) else None,
                               actions, list(invariants or []), checkpoint_id=checkpoint_id)
    record.revision, record.pre_revision = revision, pre_revision
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
    record.execution_status = record.status

    unsupported = [{"index": row["index"], "type": row["type"], "required": row["required"],
                    "reason": row["error"]} for row in declarations if not row["supported"]]
    if record.failed:
        # A failed action set keeps its existing FAILED/PARTIAL/UNKNOWN meaning.
        # Invariants are NOT_RUN: claiming PASS/FAIL for a state the actions
        # never reached would be a fabricated verification claim.
        record.verification_status = "NOT_RUN"
        record.invariant_results = _verification_results(
            status="NOT_RUN", checks=[], model_ref=record.model_ref, revision=record.revision,
            unsupported=unsupported, reason="actions did not complete")
        record.error = {"code": ("EXECUTION_STATE_UNKNOWN" if record.status == "UNKNOWN"
                                 else "PARTIAL_FAILURE" if record.status == "PARTIAL" else "EXECUTION_FAILED"),
                        "message": "transaction did not apply every action", "safe_retry": False}
        return record
    if not declarations:
        record.verification_status = "NOT_RUN"
        record.invariant_results = _verification_results(
            status="NOT_RUN", checks=[], model_ref=record.model_ref, revision=record.revision,
            unsupported=[], reason="no invariants declared")
        return record

    checks: list[dict[str, Any]] = []
    for row in declarations:
        base = {"index": row["index"], "type": row["type"], "required": row["required"],
                "path": (row["declaration"] or {}).get("path") if isinstance(row["declaration"], Mapping) else None,
                "scope": "model_state"}
        if not row["supported"]:
            checks.append({**base, "status": "NOT_RUN", "reason": "unsupported_invariant",
                           "observed": None, "expected": None, "detail": row["error"]})
            continue
        declaration = {key: value for key, value in row["declaration"].items() if key != "required"}
        try:
            reply = dict(invariant_verifier(declaration, row["index"]))
        except Exception as exc:
            # A required check that could not be evaluated is not established.
            checks.append({**base, "status": "FAIL" if row["required"] else "NOT_RUN",
                           "reason": "evaluation_failed", "detail": f"{type(exc).__name__}: {exc}",
                           "observed": None, "expected": None})
            continue
        status = reply.get("status")
        if status not in {"PASS", "FAIL", "NOT_RUN"}:
            reply["detail"] = f"verifier returned an unsupported status {status!r}"
            status = "NOT_RUN"
        checks.append({**base, "status": status, "observed": reply.get("observed"),
                       "expected": reply.get("expected"), "reason": reply.get("reason"),
                       "detail": reply.get("detail")})
    required_failed = any(check["required"] and check["status"] != "PASS" for check in checks)
    optional_failed = any(not check["required"] and check["status"] != "PASS" for check in checks)
    status = "FAILED" if required_failed else ("PARTIAL" if optional_failed else "VERIFIED")
    record.verification_status = status
    record.invariant_results = _verification_results(
        status=status, checks=checks, model_ref=record.model_ref, revision=record.revision,
        unsupported=unsupported, reason=None)
    if status == "FAILED":
        record.error = {"code": "VERIFICATION_FAILED", "safe_retry": False,
                        "message": "transaction actions applied but required invariants did not hold on the bound model"}
    return record


def transaction_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TransactionStoreError(ExecutionContractError):
    """STORE_CORRUPT blocks reads/writes until explicitly audited recovery."""

    def __init__(self, path: Path, sha256: str | None, summary: str):
        self.path, self.sha256, self.summary = path, sha256, summary
        super().__init__("STORE_CORRUPT", f"transaction store {path}: {summary} (sha256={sha256})")


class TransactionStore:
    """Durable JSON records for plan/trial/partial-failure evidence."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._records: dict[str, dict[str, Any]] = {}
        self._blocked: TransactionStoreError | None = None
        raw = None
        try:
            raw = self.path.read_bytes()
            value = json.loads(raw)
            if not isinstance(value, dict) or not all(isinstance(row, dict) for row in value.values()):
                raise ValueError("store must be an object of transaction records")
            self._records = value
        except FileNotFoundError:
            pass  # Only absence permits normal empty initialization.
        except (OSError, ValueError, UnicodeError) as exc:
            digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
            self._blocked = TransactionStoreError(self.path, digest, f"{type(exc).__name__}: {exc}")

    def _require_ready(self) -> None:
        if self._blocked is not None:
            raise self._blocked

    def _load_published(self) -> dict[str, Any]:
        """Re-read the published file so damage is never silently overwritten.

        The file is small, so every read/write re-reads it.  An out-of-band
        write (or corruption) that happened after this instance was constructed
        therefore blocks the store instead of letting a stale in-memory copy
        replace durable history.
        """
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            self._blocked = TransactionStoreError(self.path, None, f"{type(exc).__name__}: {exc}")
            raise self._blocked from exc
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            self._blocked = TransactionStoreError(self.path, hashlib.sha256(raw).hexdigest(), f"{type(exc).__name__}: {exc}")
            raise self._blocked from exc
        if not isinstance(value, dict) or not all(isinstance(row, dict) for row in value.values()):
            self._blocked = TransactionStoreError(self.path, hashlib.sha256(raw).hexdigest(),
                                                  "store must be an object of transaction records")
            raise self._blocked
        return value

    def _records_view(self) -> dict[str, Any]:
        # In-memory rows win over the published file (read-your-writes), while
        # rows published by another instance stay visible.
        return {**self._load_published(), **self._records}

    def _publish(self, records: Mapping[str, Any]) -> None:
        temporary = self.path.with_name(self.path.name + ".tmp-" + uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(records, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                # Flush bytes before atomic publication: replace alone can publish
                # a file whose contents have not reached durable storage.
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            # Persist the directory entry as well, so the replacement survives a
            # crash rather than only the file contents.
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
            except OSError:
                pass
            else:
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def recover(self, *, destination_dir: str | Path | None = None) -> dict[str, Any]:
        """Explicitly preserve a BLOCKED store and rebuild it.

        The damaged bytes are copied verbatim (never truncated, re-encoded or
        interpreted) to ``<path>.corrupt-<UTCstamp>`` - or into an explicit
        ``destination_dir`` - and the returned audit records the sha256 of the
        preserved bytes, the blocking error and the recovery time.  Recovery is
        never automatic: it is refused while the store is healthy.
        """
        if self._blocked is None:
            raise ExecutionContractError("INVALID_REQUEST", "transaction store is not BLOCKED")
        blocking = self._blocked
        target_dir = Path(destination_dir) if destination_dir is not None else self.path.parent
        target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        recovered_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raw = None
        audit: dict[str, Any] = {"store_path": str(self.path), "path": str(self.path),
                                 "recovered_at": recovered_at, "error": blocking.summary,
                                 "blocking_sha256": blocking.sha256, "source_present": raw is not None,
                                 "backup_path": None, "sha256": None, "bytes": 0,
                                 "records_dropped": len(self._records)}
        if raw is not None:
            backup = target_dir / (self.path.name + ".corrupt-" + recovered_at)
            with backup.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            audit.update({"backup_path": str(backup), "sha256": hashlib.sha256(raw).hexdigest(),
                          "bytes": len(raw)})
        self._publish({})
        self._records = {}
        self._blocked = None
        return audit

    def put(self, record: TransactionRecord | Mapping[str, Any]) -> dict[str, Any]:
        self._require_ready()
        value = record.as_dict() if isinstance(record, TransactionRecord) else dict(record)
        identifier = str(value.get("transaction_id") or "")
        if not identifier:
            raise ValueError("transaction_id is required")
        records = {**self._load_published(), **self._records, identifier: value}
        self._publish(records)
        self._records = records
        return value

    def get(self, identifier: str) -> dict[str, Any] | None:
        self._require_ready()
        value = self._records_view().get(identifier)
        return dict(value) if isinstance(value, dict) else None

    def list(self) -> list[dict[str, Any]]:
        self._require_ready()
        return [dict(value) for value in self._records_view().values()]
