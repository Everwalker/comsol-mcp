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
    pre_dispatch_failure,
)
from ._domain_outcome import (
    classify_envelope,
    final_state,
)


def _outcome_error(data: Mapping[str, Any], outcome: Any = None) -> dict[str, Any] | None:
    """The error object for a non-success envelope that carried no ``error``.

    Callers of a G3 domain operation receive the published error code of the
    classified outcome instead of a bare ``success: false``.
    """
    record = outcome if outcome is not None else classify_envelope(data)
    if getattr(record, "success", False):
        return None
    return record.error_envelope()


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
        effect: str | None = None,
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
                # C05: a runtime-scoped capability/licence question is answered by
                # the session's runtime, so an unbound call is a valid request and
                # must reach the probe instead of being refused for having no model.
                "runtime_capabilities", "runtime_license_inspect",
            }:
                raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "a selected model_ref is required")
            if expected_revision is not None:
                raise ExecutionContractError("INVALID_REQUEST", "unbound session operation cannot carry expected_revision")
            data = self._decode_callback(callback(args))
            # The fallback entry uses the same final judgement as every other
            # entry point: a refusal or an unresolved state is never success.
            state, _changed = final_state(data)
            return {
                "success": state == "succeeded", "data": data.get("data", data),
                "error": data.get("error") or _outcome_error(data),
                "execution": {"session_id": self.ledger.session_id, "model_ref": None, "revision": None},
            }

        try:
            snapshot = self._snapshot(model_ref.model_tag)
        except ExecutionContractError:
            raise
        except Exception as exc:
            # The callback was never dispatched, so no engine mutation can have
            # happened: this is a retryable transport failure, not an unknown
            # engine state that must block every later operation.
            raise pre_dispatch_failure(
                "ENGINE_UNRESPONSIVE", "the pre-dispatch engine snapshot did not answer", exc, safe_retry=True
            ) from exc
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        if permission == "inspect":
            data = self._decode_callback(callback(args))
            # The shared rule applies to read-effect operations too: an
            # operation that reports an unresolved engine state, an unverified
            # cleanup or an unclean failure must not be published as a success
            # just because its catalogue effect is a read.
            outcome = final_state(data)
            if outcome[0] != "succeeded":
                self._freeze_after_unknown_read(model_ref, data)
            return {"success": outcome[0] == "succeeded", "data": data.get("data", data),
                    "error": data.get("error") or _outcome_error(data), **self._metadata(model_ref)}

        ticket = self.ledger.begin_write(
            tool_name, args, model_ref, expected_revision,
            effect=effect or self._effect(tool_name), request_id=request_id, fingerprint=snapshot["fingerprint"],
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
            # Keep the fail-closed unknown state, but never swallow the original
            # exception: its type, code and details are the only evidence of what
            # the callback actually hit, and that evidence has to survive into
            # the job record.
            self.ledger.finish(ticket, outcome="unknown", changed=True)
            if isinstance(exc, ExecutionContractError) and exc.code == "EXECUTION_STATE_UNKNOWN":
                # Already the fail-closed state, carrying its own evidence
                # (dispatch stage, witness, cause).
                raise
            original_details = getattr(exc, "details", None)
            details: dict[str, Any] = {"cause_type": type(exc).__name__, "cause_message": str(exc)}
            if isinstance(exc, ExecutionContractError):
                # A structured cause keeps its code/stage (and any evidence the
                # raise site attached) inside the fail-closed envelope.
                details["cause_code"] = exc.code
                details["cause_stage"] = exc.stage
                if isinstance(original_details, Mapping):
                    details.update(original_details)
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "legacy callback raised after write dispatch",
                stage="post_dispatch", details=details,
            ).with_cause(exc) from exc
        try:
            after = self._snapshot(model_ref.model_tag)
        except Exception as exc:
            self.ledger.finish(ticket, outcome="unknown", changed=True)
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "post-execution snapshot is unavailable"
            ).with_cause(exc) from exc
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=after["external_event_counter"], fingerprint=after["fingerprint"],
        )
        # C01: one final judgement for every entry point (Java controlled
        # execution, legacy tools, G2 property operations, G3 domain operations
        # and the fallback).  A dangerous signal (unknown engine state, failed
        # cleanup) always beats a surface success, and the mismatch between the
        # pre/post fingerprints is itself evidence of a change.
        changed_engine = (after["fingerprint"] != snapshot["fingerprint"]
                          or after["external_event_counter"] != snapshot["external_event_counter"])
        outcome, changed = final_state(data, engine_changed=changed_engine)
        result = self.ledger.finish(ticket, outcome=outcome, changed=changed, fingerprint=after["fingerprint"])
        self._emit("finished", model_ref, operation_id=ticket.operation_id)
        outcome_record = classify_envelope(data, engine_changed=changed_engine)
        if result["outcome"] != "succeeded":
            data = {**data, "success": False, "partial_change": result["partial_change"],
                    "execution_state_unknown": result["execution_state_unknown"],
                    "verified_outcome": outcome_record.state,
                    "verification_status": outcome_record.verification_status}
        envelope = self._metadata(model_ref)
        envelope["execution"].update({
            "request_id": ticket.request_id,
            "operation_id": ticket.operation_id,
            "request_hash": ticket.request_hash,
        })
        preserved_detail = data.get("data")
        preserved = dict(preserved_detail) if isinstance(preserved_detail, Mapping) else {}
        for name, value in data.items():
            if name not in {"success", "data", "error"}:
                preserved[name] = value
        return {
            "success": result["outcome"] == "succeeded",
            "data": preserved,
            "error": data.get("error") or _outcome_error(data, outcome_record),
            **envelope,
        }

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

    def _freeze_after_unknown_read(self, model_ref: ModelRef, data: Mapping[str, Any]) -> None:
        """Freeze dependent writes after a read-effect operation with a bad outcome.

        A read-effect operation carries no write ticket, but its outcome can
        still be an unresolved engine state (an ephemeral node it could not
        remove, an engine answer it could not interpret).  Recording the dirty
        state is what stops a later write from building on an unverified model.
        """
        record = classify_envelope(data)
        if record.state == "succeeded":
            return
        if record.state not in {"unknown", "partial"}:
            return
        try:
            state = self.ledger._state_for(model_ref)
            state.dirty = True
            state.fingerprint = None
        except ExecutionContractError:
            # The caller already holds a non-success result; a bookkeeping
            # failure must not replace it with a second error.
            pass

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
