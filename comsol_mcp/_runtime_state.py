"""Strict W07 persistence for SessionLedger across control-host restarts."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ._execution_contract import ExecutionTicket, ModelRef, SessionLedger, _ModelState


RUNTIME_STATE_SCHEMA_VERSION = 1


class RuntimeStateError(RuntimeError): pass


def _put(store: Any, table: str, key: str, value: dict[str, Any]) -> None:
    if hasattr(store, "put_metadata"):
        store.put_metadata(table, key, value)
    else:
        store.save_metadata(table, key, value)


def _get(store: Any, table: str, key: str) -> dict[str, Any] | None:
    if hasattr(store, "get_metadata"):
        return store.get_metadata(table, key)
    row = store.db.execute(f"SELECT metadata FROM {table} WHERE " + ("session_id" if table == "sessions" else "runtime_id") + "=?", (key,)).fetchone()
    if row is None: return None
    import json
    return json.loads(row[0])


def _worker_key(identity: Mapping[str, Any]) -> str:
    value = identity.get("runtime_id") or identity.get("worker_instance_id")
    if not isinstance(value, str) or not value: raise RuntimeStateError("worker identity requires runtime_id or worker_instance_id")
    return value


def save_runtime_state(store: Any, ledger: SessionLedger, worker_identity: Mapping[str, Any], active_tickets: Iterable[ExecutionTicket] = ()) -> dict[str, Any]:
    """Persist all ledger fields; ticket metadata is retained for UNKNOWN recovery."""
    identity = dict(worker_identity); runtime_id = _worker_key(identity)
    models = {}
    for tag, state in ledger._models.items():
        models[tag] = {"ref": state.ref.as_dict(), "revision": state.revision, "fingerprint": state.fingerprint,
                       "external_event_counter": state.external_event_counter, "observed_external_event_counter": state.observed_external_event_counter,
                       "dirty": state.dirty, "retired": state.retired, "active_operation_id": state.active_operation_id,
                       "ownership": ledger.model_ownership.get(tag, "user_owned")}
    tickets = [{"operation_id": t.operation_id, "request_id": t.request_id, "request_hash": t.request_hash, "operation": t.operation,
                "model_ref": t.model_ref.as_dict(), "expected_revision": t.expected_revision,
                "external_event_counter": t.external_event_counter, "fingerprint": t.fingerprint} for t in active_tickets]
    state = {"schema_version": RUNTIME_STATE_SCHEMA_VERSION, "worker_identity": identity, "session_id": ledger.session_id,
             "server_instance_id": ledger.server_instance_id, "server_ownership": ledger.server_ownership,
             "permissions": sorted(ledger.permissions), "generations": dict(ledger._generations), "models": models, "active_tickets": tickets}
    _put(store, "sessions", ledger.session_id, state); _put(store, "runtimes", runtime_id, state)
    return state


def restore_runtime_state(store: Any, session_id: str, worker_identity: Mapping[str, Any]) -> tuple[SessionLedger, list[dict[str, Any]]]:
    state = _get(store, "sessions", session_id)
    if not isinstance(state, dict): raise RuntimeStateError("no persisted runtime state")
    if state.get("schema_version") != RUNTIME_STATE_SCHEMA_VERSION: raise RuntimeStateError("unsupported runtime state schema")
    current = dict(worker_identity); saved = state.get("worker_identity")
    if not isinstance(saved, dict): raise RuntimeStateError("persisted worker identity is invalid")
    saved_worker = saved.get("worker_instance_id")
    current_worker = current.get("worker_instance_id")
    saved_epoch = saved.get("connection_epoch")
    current_epoch = current.get("connection_epoch")
    # Missing identity fields are never evidence of continuity: ``None ==
    # None`` must not preserve a potentially stale model handle.
    same_worker = (
        isinstance(saved_worker, str) and bool(saved_worker)
        and isinstance(current_worker, str) and bool(current_worker)
        and saved_worker == current_worker
        and isinstance(saved_epoch, int) and not isinstance(saved_epoch, bool)
        and isinstance(current_epoch, int) and not isinstance(current_epoch, bool)
        and saved_epoch == current_epoch
    )
    server = current.get("server_instance_id") or state.get("server_instance_id")
    if not isinstance(server, str) or not server: raise RuntimeStateError("current worker server_instance_id is required")
    ledger = SessionLedger(session_id, server, server_ownership=state["server_ownership"], permissions=set(state["permissions"]))
    ledger._generations.update({str(k): int(v) for k, v in state["generations"].items()})
    unknown: list[dict[str, Any]] = []
    for tag, raw in state["models"].items():
        ownership = raw["ownership"]
        if same_worker and server == state["server_instance_id"]:
            ref = ModelRef(**raw["ref"]); ledger._models[tag] = _ModelState(ref=ref, revision=int(raw["revision"]), fingerprint=raw.get("fingerprint"), external_event_counter=int(raw["external_event_counter"]), observed_external_event_counter=int(raw["observed_external_event_counter"]), dirty=bool(raw["dirty"]), retired=bool(raw["retired"]), active_operation_id=None)
            ledger.model_ownership[tag] = ownership
        else:
            # Never reuse a ref across worker/epoch replacement: retain the
            # high-water mark, then bind a strictly higher generation.
            ledger._generations[tag] = max(ledger._generations.get(tag, 0), int(raw["ref"]["generation"]))
            ledger.bind_model(tag, ownership=ownership, fingerprint=raw.get("fingerprint"))
            ledger._models[tag].dirty = True
        if raw.get("active_operation_id"):
            ledger._models[tag].dirty = True; unknown.append({"operation_id": raw["active_operation_id"], "status": "UNKNOWN", "safe_retry": False})
    for ticket in state.get("active_tickets", []):
        unknown.append({"operation_id": ticket.get("operation_id"), "status": "UNKNOWN", "safe_retry": False})
    return ledger, unknown
