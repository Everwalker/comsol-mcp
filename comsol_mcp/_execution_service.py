"""Injectable W05 execution gate for legacy callbacks and the future worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
import json

from ._execution_contract import (
    ExecutionContractError,
    ModelRef,
    SessionLedger,
    canonical_project_path,
    permission_for_legacy_tool,
)


class SnapshotAdapter(Protocol):
    def model_snapshot(self, model_tag: str) -> Mapping[str, Any]: ...


class ExecutionService:
    """Preflight selected identities before a legacy callback can touch COMSOL.

    The adapter provides a scope-limited engine fingerprint and an optional
    ModelChangedHandler-derived counter.  Any observed divergence fails closed;
    an unchanged observation does not establish full external-property coverage
    or an engine CAS guarantee.
    """

    def __init__(self, ledger: SessionLedger, adapter: SnapshotAdapter, *, project_root: str | Path, on_state_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.ledger = ledger
        self.adapter = adapter
        self.project_root = Path(project_root)
        self.on_state_change = on_state_change or (lambda _event: None)

    def bind_model(self, model_tag: str, *, ownership: str = "user_owned") -> dict[str, Any]:
        snapshot = self._snapshot(model_tag)
        ref = self.ledger.bind_model(model_tag, ownership=ownership, fingerprint=snapshot["fingerprint"])
        # Initial bind establishes the baseline; it is not an external change.
        state = self.ledger._state_for(ref)  # internal state intentionally hidden from callers
        state.external_event_counter = snapshot["external_event_counter"]
        state.observed_external_event_counter = snapshot["external_event_counter"]
        result = self.inspect(ref); self._emit("bound", ref); return result

    def inspect(self, model_ref: ModelRef) -> dict[str, Any]:
        self.ledger._state_for(model_ref)
        snapshot = self._snapshot(model_ref.model_tag)
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        return self._metadata(model_ref)

    def reconcile(self, model_ref: ModelRef) -> dict[str, Any]:
        snapshot = self._snapshot(model_ref.model_tag)
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        self.ledger.mark_external_observed(model_ref, fingerprint=snapshot["fingerprint"])
        self._emit("reconciled", model_ref)
        return self._metadata(model_ref)

    def execute_legacy(
        self,
        tool_name: str,
        callback: Callable[[dict[str, Any]], Any],
        arguments: Mapping[str, Any] | None = None,
        *,
        model_ref: ModelRef | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        path_parameters: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Call only after authorization, real selected-model check, and preflight.

        Initial model/session operations may omit ``model_ref``.  They still use
        the server-side legacy registry permission decision and are deliberately
        not given a synthetic revision.
        """
        permission = permission_for_legacy_tool(tool_name)
        if session_id is not None and session_id != self.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "request session_id does not match this control session")
        args = dict(arguments or {})
        for name in path_parameters:
            if name in args and args[name]:
                args[name] = str(canonical_project_path(self.project_root, args[name]))
        if permission not in self.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {permission}")

        if model_ref is None:
            if tool_name not in {
                "model_create", "model_load", "server_connect", "server_disconnect", "server_start",
                "load_visible_main_model", "load_current_main_model", "start_visible_main_workflow",
                "configure_single_main_workflow", "check_server_port", "workflow_info", "mcp_tool_audit",
            }:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "a selected model_ref is required")
            if expected_revision is not None:
                raise ExecutionContractError("INVALID_REQUEST", "unbound session operation cannot carry expected_revision")
            data = self._decode_callback(callback(args))
            return {
                "success": bool(data.get("success", True)), "data": data.get("data", data),
                "error": data.get("error"),
                "execution": {"session_id": self.ledger.session_id, "model_ref": None, "revision": None},
            }

        snapshot = self._snapshot(model_ref.model_tag)
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        if permission == "inspect":
            data = self._decode_callback(callback(args))
            return {"success": bool(data.get("success", True)), "data": data.get("data", data), "error": data.get("error"), **self._metadata(model_ref)}

        ticket = self.ledger.begin_write(
            tool_name, args, model_ref, expected_revision,
            effect=self._effect(tool_name), request_id=request_id, fingerprint=snapshot["fingerprint"],
        )
        try:
            self._emit("active", model_ref, operation_id=ticket.operation_id)
        except Exception as exc:
            self.ledger.finish(ticket, outcome="failed", changed=False)
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "state persistence failed before engine dispatch") from exc
        try:
            data = self._decode_callback(callback(args))
        except Exception as exc:
            # A legacy callback cannot prove it made no engine-side mutation.
            self.ledger.finish(ticket, outcome="unknown", changed=True)
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "legacy callback raised after write dispatch") from exc
        try:
            after = self._snapshot(model_ref.model_tag)
        except Exception as exc:
            self.ledger.finish(ticket, outcome="unknown", changed=True)
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "post-execution snapshot is unavailable") from exc
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=after["external_event_counter"], fingerprint=after["fingerprint"],
        )
        claimed_success = bool(data.get("success", True))
        detail = data.get("data") if isinstance(data.get("data"), Mapping) else {}
        signals = {name: bool(data.get(name) or detail.get(name)) for name in ("partial_change", "failed_item_may_have_changed", "cleanup_failed", "engine_state_unknown", "execution_state_unknown", "applied")}
        engine_executed = bool(data.get("execution_success") or detail.get("execution_success"))
        changed_engine = after["fingerprint"] != snapshot["fingerprint"] or after["external_event_counter"] != snapshot["external_event_counter"]
        if claimed_success:
            outcome, changed = "succeeded", True
        elif signals["engine_state_unknown"] or signals["execution_state_unknown"] or signals["cleanup_failed"]:
            outcome, changed = "unknown", True
        elif any(signals.values()) or str(data.get("status", "")).lower() == "partial" or engine_executed or changed_engine:
            outcome, changed = "partial", True
        else:
            outcome, changed = "failed", False
        result = self.ledger.finish(ticket, outcome=outcome, changed=changed, fingerprint=after["fingerprint"])
        self._emit("finished", model_ref, operation_id=ticket.operation_id)
        if result["outcome"] != "succeeded":
            data = {**data, "success": False, "partial_change": result["partial_change"], "execution_state_unknown": result["execution_state_unknown"]}
        envelope = self._metadata(model_ref)
        envelope["execution"].update({
            "request_id": ticket.request_id,
            "operation_id": ticket.operation_id,
            "request_hash": ticket.request_hash,
        })
        preserved = dict(detail)
        for name, value in data.items():
            if name not in {"success", "data", "error"}: preserved[name] = value
        return {"success": bool(data.get("success", True)) and result["outcome"] == "succeeded", "data": preserved, "error": data.get("error"), **envelope}

    @staticmethod
    def _decode_callback(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "legacy callback returned invalid JSON") from exc
        if not isinstance(value, Mapping) or not isinstance(value.get("success"), bool):
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "legacy callback returned no boolean success envelope")
        return dict(value)

    def _snapshot(self, model_tag: str) -> dict[str, Any]:
        raw = dict(self.adapter.model_snapshot(model_tag))
        selected_tag = raw.get("model_tag", raw.get("tag"))
        if not isinstance(selected_tag, str) or selected_tag != model_tag:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "adapter selected a different model tag")
        if raw.get("server_instance_id") not in (None, self.ledger.server_instance_id):
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "adapter reports another server instance")
        counter = raw.get("external_event_counter", raw.get("external_change_counter"))
        if isinstance(counter, bool) or not isinstance(counter, int) or counter < 0:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "adapter did not provide a valid external event counter")
        if not isinstance(raw.get("fingerprint"), str) or not raw["fingerprint"]:
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "adapter did not provide a fingerprint")
        return {"fingerprint": raw["fingerprint"], "external_event_counter": counter}

    def _metadata(self, model_ref: ModelRef) -> dict[str, Any]:
        state = self.ledger._state_for(model_ref)
        return {
            "execution": {
                "session_id": self.ledger.session_id,
                "model_ref": model_ref.as_dict(),
                "revision": state.revision,
                "dirty": state.dirty,
                "server_ownership": self.ledger.server_ownership,
                "model_ownership": self.ledger.model_ownership.get(model_ref.model_tag),
                "cas_limit": "managed revision is not a COMSOL cross-client atomic CAS",
            }
        }

    def _emit(self, state: str, model_ref: ModelRef, *, operation_id: str | None = None) -> None:
        payload = {"state": state, "model_ref": model_ref.as_dict(), "operation_id": operation_id, **self._metadata(model_ref)["execution"]}
        self.on_state_change(payload)

    @staticmethod
    def _effect(tool_name: str) -> str:
        from ._execution_contract import LEGACY_TOOL_EFFECTS
        return LEGACY_TOOL_EFFECTS[tool_name]
