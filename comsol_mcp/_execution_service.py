"""Injectable W05 execution gate for legacy callbacks and the future worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Protocol
import json
import hashlib

from ._execution_contract import (
    _G3_CATALOG_EFFECTS,
    ExecutionContractError,
    ModelRef,
    SessionLedger,
    canonical_project_path,
    permission_for_legacy_tool,
    permission_for_effect,
    pre_dispatch_failure,
)
from ._domain_outcome import (
    classify_envelope,
    final_state,
)


_FINGERPRINT_COMPONENT_KEYS = frozenset({
    "schema_version", "tag", "label", "file_path", "time_modified_epoch_ms", "parameters",
})
_FINGERPRINT_PARAMETER_KEYS = frozenset({"name", "expression"})
_SIGNED_LONG_MAX = (1 << 63) - 1

#: The four immediate cause keys of a post-dispatch envelope always identify
#: the immediate callback exception.  A callback may legally attach its own
#: ``details`` mapping, including keys with these names (or the reserved
#: preservation-container name below), so colliding entries never overwrite
#: the top-level immediate values.
#:
#: Disclosure boundary (T08 F-2 parent adjudication 2026-11-09): the baseline
#: envelope's ``with_cause`` overwrote a callback's own top-level
#: ``cause_type``/``cause_message``, so those two original values were never
#: part of the published JSON.  This merge keeps that prior suppression
#: boundary: neither value is re-published via an alias, a container, or
#: anywhere else in ``as_dict()``.  They remain available in memory on the
#: preserved ``__cause__`` exception chain.  The original colliding
#: ``cause_code``/``cause_stage`` (which the baseline did publish top-level)
#: are kept as distinguishable nested facts under the container key.
_IMMEDIATE_CAUSE_KEYS = ("cause_type", "cause_message", "cause_code", "cause_stage")
_SUPPRESSED_CALLBACK_CAUSE_KEYS = ("cause_type", "cause_message")
_CALLBACK_CAUSE_FACTS_KEY = "callback_cause_facts"


def _merge_callback_cause_details(details: dict[str, Any], original_details: Mapping[str, Any]) -> None:
    """Merge a structured callback's own evidence without layer aliasing.

    * A mapping that never touches the immediate cause keys or the reserved
      container name merges flat, byte-compatibly with the historical shape.
    * Otherwise the four immediate keys keep only the immediate error's own
      values; the callback's original ``cause_code``/``cause_stage`` (facts
      the baseline published top-level) move verbatim into one small nested
      facts container instead of being dropped or aliased over the top level.
    * The callback's original ``cause_type``/``cause_message`` values are not
      published at all — matching the baseline's post-``with_cause`` public
      disclosure exactly — and are not duplicated into any container; they
      stay in memory on the ``__cause__`` chain.  The complete unrelated
      original mapping is never copied wholesale.
    * Every other original key (publication facts and any pre-existing
      ``nested_cause_*`` claims) keeps its existing top-level name and value;
      no original key is ever overwritten.
    * If the callback itself used the reserved container name, that original
      top-level value is preserved verbatim inside the facts container under
      the same name.  The top-level entry at that name then becomes a dict
      (a disclosed, type-changing compatibility note for a naive consumer of
      that exact key); no value is lost.
    """
    if not any(key in _IMMEDIATE_CAUSE_KEYS or key == _CALLBACK_CAUSE_FACTS_KEY
               for key in original_details):
        details.update(original_details)
        return
    for key, value in original_details.items():
        if key in _IMMEDIATE_CAUSE_KEYS or key == _CALLBACK_CAUSE_FACTS_KEY:
            continue
        details[key] = value
    facts: dict[str, Any] = {key: original_details[key]
                             for key in ("cause_code", "cause_stage") if key in original_details}
    if _CALLBACK_CAUSE_FACTS_KEY in original_details:
        facts[_CALLBACK_CAUSE_FACTS_KEY] = original_details[_CALLBACK_CAUSE_FACTS_KEY]
    if facts:
        details[_CALLBACK_CAUSE_FACTS_KEY] = facts


#: --- T08 F-2 r3: wrapper-origin private cause-claim carrier ---------------
#: Hypothesis made testable per the r3 brief; NOT a security boundary against
#: arbitrary trusted in-process Python code.
#:
#: Baseline projected the callback's own ``cause_code``/``cause_stage``
#: mapping claims onto durable stage evidence because the wrapper merged the
#: callback's details over the immediate values.  The F-2 envelope fix stops
#: that shadowing, which also silently removed the lower layer from the
#: daemon's stage projection.  This carrier re-records, at the trusted
#: wrapper construction site, the exact effective values the baseline
#: projection would have published (the callback's mapping entry when the
#: key was present, otherwise the exception's own attributes), so
#: ``comsol_mcp._control_daemon._stage_failure_cause_details`` can restore
#: them as a provenance-checked *claim*.  A claim is not proof that any
#: publication or dispatch stage actually happened.  The carrier is never
#: serialised (not in ``details`` nor ``as_dict()``) and the projection
#: re-validates exact carrier class, token-sealed construction and live
#: ``__cause__`` chain identity.  No mapping spelling is whitelisted for
#: this purpose; forged plain mappings are never read for it.
_WRAPPED_CAUSE_CLAIMS_KEY = "_legacy_wrapped_cause_claims"
_WRAPPED_CAUSE_CLAIMS_TOKEN = object()


class _WrappedCauseClaims:
    """Sealed in-process record of a wrapped callback's lower cause claims."""

    __slots__ = ("_source", "effective_code", "effective_stage")

    def __init__(self, token: object, source: BaseException,
                 effective_code: Any, effective_stage: Any) -> None:
        if token is not _WRAPPED_CAUSE_CLAIMS_TOKEN:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                "wrapped-cause carrier is constructed only by the execution service wrapper",
            )
        self._source = source
        self.effective_code = effective_code
        self.effective_stage = effective_stage

    def claims_for(self, wrapper_exc: Any) -> "dict[str, Any] | None":
        """Return the recorded claims only while the real chain still matches."""
        if not isinstance(wrapper_exc, BaseException):
            return None
        if wrapper_exc.__cause__ is not self._source:
            return None
        return {"cause_code": self.effective_code, "cause_stage": self.effective_stage}


def _java_string_sort_key(value: str) -> bytes:
    """Match Java String.compareTo ordering over UTF-16 code units."""
    return value.encode("utf-16-be", errors="strict")


def _validated_fingerprint_components(value: Any, *, model_tag: str, fingerprint: str) -> dict[str, Any]:
    """Validate the versioned raw witness and prove it hashes to the legacy digest."""
    def invalid() -> ExecutionContractError:
        return ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "Worker fingerprint components are missing or invalid",
        )

    if not isinstance(value, Mapping) or set(value) != _FINGERPRINT_COMPONENT_KEYS:
        raise invalid()
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise invalid()
    tag, label, file_path = value.get("tag"), value.get("label"), value.get("file_path")
    if (not isinstance(tag, str) or not tag or tag != model_tag
            or not isinstance(label, str) or not isinstance(file_path, str)
            or any("\x00" in item for item in (tag, label, file_path))):
        raise invalid()
    time_modified = value.get("time_modified_epoch_ms")
    if type(time_modified) is not int or not 0 <= time_modified <= _SIGNED_LONG_MAX:
        raise invalid()
    raw_parameters = value.get("parameters")
    if not isinstance(raw_parameters, list):
        raise invalid()
    rows: list[dict[str, str]] = []
    names: list[str] = []
    for row in raw_parameters:
        if not isinstance(row, Mapping) or set(row) != _FINGERPRINT_PARAMETER_KEYS:
            raise invalid()
        name, expression = row.get("name"), row.get("expression")
        if (not isinstance(name, str) or not name or not isinstance(expression, str)
                or "\x00" in name or "\x00" in expression):
            raise invalid()
        names.append(name)
        rows.append({"name": name, "expression": expression})
    try:
        ordered_names = sorted(names, key=_java_string_sort_key)
    except (UnicodeError, ValueError):
        raise invalid() from None
    if len(names) != len(set(names)) or names != ordered_names:
        raise invalid()
    try:
        source = (tag + "\x00" + label + "\x00" + file_path + "\x00"
                  + str(time_modified) + "\x00"
                  + "".join(f"{row['name']}={row['expression']}\x00" for row in rows))
        computed = hashlib.sha256(source.encode("utf-8", errors="strict")).hexdigest()
    except (UnicodeError, ValueError):
        raise invalid() from None
    if not isinstance(fingerprint, str) or computed != fingerprint:
        raise invalid()
    return {
        "schema_version": 1,
        "tag": tag,
        "label": label,
        "file_path": file_path,
        "time_modified_epoch_ms": time_modified,
        "parameters": rows,
    }


def _fingerprint_coverage(snapshot: Mapping[str, Any]) -> str:
    if snapshot.get("fingerprint_components_status") != "AVAILABLE_V1":
        return "UNKNOWN"
    return "tag+label+file_path+time_modified+parameters"


def _outcome_error(data: Mapping[str, Any], outcome: Any = None) -> dict[str, Any] | None:
    """The error object for a non-success envelope that carried no ``error``.

    Callers of a G3 domain operation receive the published error code of the
    classified outcome instead of a bare ``success: false``.
    """
    record = outcome if outcome is not None else classify_envelope(data)
    if getattr(record, "success", False):
        return None
    return record.error_envelope()
    return None


def _function_evaluate_read_ticket_proven(tool_name: str, effect: str | None,
                                          data: Mapping[str, Any], outcome: str,
                                          engine_changed: bool) -> bool:
    """Prove the one built-in function-evaluation ticket made no model change.

    Function evaluation retains the ordinary permission check, serialized write
    ticket, revision preflight, and before/after engine snapshots.  Only after
    that full path succeeds may a server-created domain witness certify its
    exact read-only Java dispatches and suppress the success ticket's default
    revision increment.  No caller-provided read-only flag participates.
    """
    if tool_name != "function_evaluate" or effect != "evaluate" or engine_changed or outcome != "succeeded":
        return False
    if data.get("effect") != "evaluate" or data.get("success") is not True:
        return False
    domain = data.get("domain_outcome")
    if not isinstance(domain, Mapping) or domain.get("operation") != "function.evaluate" or domain.get("state") != "succeeded":
        return False
    witness = domain.get("witness")
    if not isinstance(witness, Mapping):
        return False
    calls = witness.get("engine_calls")
    dispatches = witness.get("dispatches")
    if (isinstance(calls, bool) or not isinstance(calls, int) or calls < 1
            or not isinstance(dispatches, list) or len(dispatches) != calls
            or witness.get("mutation_issued") is not False
            or witness.get("mutation_method") is not None):
        return False
    if any(not isinstance(row, Mapping) or row.get("is_mutation") is not False for row in dispatches):
        return False
    detail = data.get("data")
    if not isinstance(detail, Mapping):
        return False
    return (
        detail.get("schema_version") == "comsol-mcp.function-evaluate/1.0.0"
        and detail.get("status") == "OBSERVED"
        and detail.get("ok") is True
        and detail.get("sample_completion") == "SUCCEEDED"
        and detail.get("sample_failure_count") == 0
        and detail.get("partial_change") is False
        and detail.get("execution_state_unknown") is False
        and domain.get("cleanup_failed") is False
        and domain.get("execution_state_unknown") is False
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
        self._project_root_context = ContextVar(f"execution_project_root_{id(self)}", default=None)
        self._project_root_binding_context = ContextVar(f"execution_project_root_binding_{id(self)}", default=None)
        self.on_state_change = on_state_change or (lambda _event: None)
        self._owned_branch_context = ContextVar(f"owned_branch_proof_{id(self)}", default=None)

    @property
    def project_root(self) -> Path:
        scoped = self._project_root_context.get(None)
        return scoped if scoped is not None else self._base_project_root

    @project_root.setter
    def project_root(self, value: str | Path) -> None:
        self._base_project_root = Path(value)

    @property
    def project_root_binding(self):
        return self._project_root_binding_context.get(None)

    @contextmanager
    def project_root_scope(self, root: str | Path):
        """Thread-local path plus its already-open project-root identity."""
        from ._project_root import (ProjectRootBinding, close_binding_releasing,
                                    open_project_root_binding)
        owns_binding = not isinstance(root, ProjectRootBinding)
        binding = open_project_root_binding(root) if owns_binding else root
        candidate = binding.path
        token = self._project_root_context.set(candidate)
        binding_token = self._project_root_binding_context.set(binding)
        primary_error: BaseException | None = None
        try:
            yield candidate
        except BaseException as exc:
            # Local capture (never sys.exc_info): a failed release in the
            # finally must be recorded on the in-flight primary instead of
            # replacing it, and an unrelated outer handler must not be fed.
            primary_error = exc
            raise
        finally:
            self._project_root_binding_context.reset(binding_token)
            self._project_root_context.reset(token)
            if owns_binding:
                close_binding_releasing(binding, primary_error)

    @contextmanager
    def _owned_branch_scope(self):
        # Internal serial-backend scope; no wire field can create this identity.
        proof = {}
        token = self._owned_branch_context.set(proof)
        try:
            yield proof
        finally:
            self._owned_branch_context.reset(token)

    def _owned_branch_proven(self, proof, data, ref, snapshot, after):
        if proof is None or self._owned_branch_context.get() is not proof:
            return False
        detail = data.get("data")
        if not isinstance(detail, Mapping) or detail.get("revision_proof") != proof:
            return False
        source_before, source_after = proof.get("source_before"), proof.get("source_after")
        if not isinstance(source_before, Mapping) or not isinstance(source_after, Mapping):
            return False
        if any(source_before.get(k) != snapshot[k] or source_after.get(k) != after[k]
               for k in ("fingerprint", "external_event_counter")):
            return False
        newref = proof.get("branch_ref")
        calls = proof.get("mutation_targets")
        try:
            from ._execution_contract import model_ref_from_mapping
            branch_state = self.ledger._state_for(model_ref_from_mapping(dict(newref)))
        except (ExecutionContractError, TypeError, ValueError):
            return False
        if (branch_state.ref.as_dict() != detail.get("branch_model_ref")
                or branch_state.revision != detail.get("branch_revision")
                or branch_state.dirty or branch_state.active_operation_id
                or self.ledger.model_ownership.get(branch_state.ref.model_tag) != "mcp_owned"):
            return False
        persistence = detail.get("persistence")
        if not isinstance(persistence, Mapping):
            return False
        revision_record = persistence.get("revision_record")
        project_binding = persistence.get("project_binding")
        if not isinstance(project_binding, Mapping):
            return False
        expected_record = {"model_ref": branch_state.ref.as_dict(), "revision": branch_state.revision,
                           "dirty": branch_state.dirty, "fingerprint": branch_state.fingerprint,
                           "active_operation_id": branch_state.active_operation_id,
                           "project_id": project_binding.get("project_id"), "attribution": "PROJECT_BOUND"}
        if (json.dumps(revision_record, sort_keys=True, separators=(",", ":"))
                != json.dumps(expected_record, sort_keys=True, separators=(",", ":"))
                or proof.get("persisted_revision_record") != revision_record):
            return False
        return (proof.get("action") == "checkpoint.branch" and proof.get("source_ref") == ref.as_dict()
                and proof.get("source_before") == proof.get("source_after")
                and proof.get("pointer_unchanged") is True and proof.get("identity_unchanged") is True
                and proof.get("copy_verified") is True and proof.get("persistence_verified") is True
                and proof.get("unknown") is False and proof.get("cleanup_errors") == []
                and isinstance(newref, Mapping) and newref != ref.as_dict()
                and newref.get("model_tag") == proof.get("owned_tag")
                and isinstance(proof.get("clone_sha256"), str) and len(proof["clone_sha256"]) == 64
                and isinstance(proof.get("operation_id"), str) and bool(proof["operation_id"])
                and isinstance(calls, list) and len(calls) == 1
                and calls[0].get("method") == "load" and calls[0].get("model_tag") == proof["owned_tag"]
                and detail.get("persistence", {}).get("verified") is True
                and detail.get("source_pointer_restored") is True
                and detail.get("source_unchanged_within_scope") is True
                and detail.get("status") == "SUCCEEDED" and data.get("success") is True)

    def bind_model(self, model_tag: str, *, ownership: str = "user_owned") -> dict[str, Any]:
        snapshot = self._snapshot(model_tag)
        ref = self.ledger.bind_model(model_tag, ownership=ownership, fingerprint=snapshot["fingerprint"])
        # Initial bind establishes the baseline; it is not an external change.
        state = self.ledger._state_for(ref)  # internal state intentionally hidden from callers
        state.external_event_counter = snapshot["external_event_counter"]
        state.observed_external_event_counter = snapshot["external_event_counter"]
        result = self.inspect(ref); self._emit("bound", ref); return result

    def inspect(self, model_ref: ModelRef) -> dict[str, Any]:
        evidence = self.inspect_evidence(model_ref)
        evidence.pop("model_snapshot", None)
        return evidence

    def inspect_evidence(self, model_ref: ModelRef) -> dict[str, Any]:
        """Read the selected model and return its scoped snapshot witness.

        The witness covers only the adapter's documented fingerprint and
        external-change counter. It is useful for recovery audits, but it is
        not a COMSOL-side atomic CAS or full-model coverage claim.
        """
        self.ledger._state_for(model_ref)
        snapshot = self._snapshot(model_ref.model_tag)
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        return {
            **self._metadata(model_ref),
            "model_snapshot": {
                "model_tag": model_ref.model_tag,
                "server_instance_id": self.ledger.server_instance_id,
                "fingerprint": snapshot["fingerprint"],
                "external_event_counter": snapshot["external_event_counter"],
                "coverage": _fingerprint_coverage(snapshot),
                "cas_guarantee": "NONE",
                "fingerprint_components_status": snapshot["fingerprint_components_status"],
                **({"fingerprint_components": snapshot["fingerprint_components"]}
                   if snapshot["fingerprint_components_status"] == "AVAILABLE_V1" else {}),
            },
        }

    def reconcile(self, model_ref: ModelRef) -> dict[str, Any]:
        snapshot = self._snapshot(model_ref.model_tag)
        self.ledger.observe_engine_state(
            model_ref,
            external_event_counter=snapshot["external_event_counter"],
            fingerprint=snapshot["fingerprint"],
        )
        self.ledger.mark_external_observed(model_ref, fingerprint=snapshot["fingerprint"])
        self._emit("reconciled", model_ref)
        return {
            **self._metadata(model_ref),
            "model_snapshot": {
                "model_tag": model_ref.model_tag,
                "server_instance_id": self.ledger.server_instance_id,
                "fingerprint": snapshot["fingerprint"],
                "external_event_counter": snapshot["external_event_counter"],
                "coverage": _fingerprint_coverage(snapshot),
                "cas_guarantee": "NONE",
                "fingerprint_components_status": snapshot["fingerprint_components_status"],
                **({"fingerprint_components": snapshot["fingerprint_components"]}
                   if snapshot["fingerprint_components_status"] == "AVAILABLE_V1" else {}),
            },
        }

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
        _owned_branch_proof: Mapping[str, Any] | None = None,
        terminal_model_close: bool = False,
        _ephemeral_binding: Any = None,
        force_changed_on_success: bool = False,
    ) -> dict[str, Any]:
        """Call only after authorization, real selected-model check, and preflight.

        Initial model/session operations may omit ``model_ref``.  They still use
        the server-side legacy registry permission decision and are deliberately
        not given a synthetic revision.
        """
        args = dict(arguments or {})
        # Re-resolve these two dynamic operations at the session gate from
        # their exact operation ID and effective arguments. The request body
        # and the caller-supplied ``effect`` keyword are not authorization
        # inputs; only a precise top-level inspect action resolves to READ.
        g3_manage_operations = {
            "material_group_manage": "material.group_manage",
            "physics_multiphysics_manage": "physics.multiphysics_manage",
            "material.group_manage": "material.group_manage",
            "physics.multiphysics_manage": "physics.multiphysics_manage",
        }
        g3_operation = g3_manage_operations.get(tool_name)
        if tool_name == "api_invoke":
            permission = permission_for_effect(effect)
        elif g3_operation is not None:
            from ._g3_ops import execution_effect
            catalog_effect = execution_effect(g3_operation, args) or "DYNAMIC"
            trusted_effect = _G3_CATALOG_EFFECTS.get(str(catalog_effect).upper())
            if trusted_effect is None:
                raise ExecutionContractError(
                    "PERMISSION_DENIED", "G3 operation has no trusted session effect classification",
                    stage="pre_dispatch",
                )
            effect = trusted_effect
            permission = permission_for_effect(trusted_effect)
        else:
            # api_invoke and every other legacy route keep their existing
            # authorization semantics.
            permission = permission_for_legacy_tool(tool_name)
        if session_id is not None and session_id != self.ledger.session_id:
            raise ExecutionContractError("MODEL_IDENTITY_MISMATCH", "request session_id does not match this control session")
        for name in path_parameters:
            if name in args and args[name]:
                args[name] = str(canonical_project_path(self.project_root, args[name]))
        if permission not in self.ledger.permissions:
            raise ExecutionContractError("PERMISSION_DENIED", f"permission required: {permission}")

        if model_ref is None:
            if tool_name not in {
                "model_list", "model_create", "model_load", "server_connect", "server_disconnect", "server_start",
                "load_visible_main_model", "load_current_main_model", "start_visible_main_workflow",
                "configure_single_main_workflow", "check_server_port", "workflow_info", "mcp_tool_audit",
                # C05: a runtime-scoped capability/licence question is answered by
                # the session's runtime, so an unbound call is a valid request and
                # must reach the probe instead of being refused for having no model.
                "runtime_capabilities", "runtime_license_inspect",
                "runtime_discover", "runtime_inspect", "runtime_doctor", "runtime_compatibility_report",
                "runtime_license_checkout", "runtime_render_probe",
                "validate_report", "validate.report",
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
                "execution": {"session_id": self.ledger.session_id, "model_ref": None, "revision": None, "operation_id": request_id},
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
        ephemeral_scope = None
        try:
            from ._ephemeral_result_ticket import ticket_scope
            with ticket_scope(_ephemeral_binding, ticket, self, callback, args) as ephemeral_scope:
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
                    _merge_callback_cause_details(details, original_details)
            wrapped = ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN", "legacy callback raised after write dispatch",
                stage="post_dispatch", details=details,
            ).with_cause(exc)
            if isinstance(exc, ExecutionContractError):
                # T08 F-2 r3: record the baseline-effective lower cause values
                # at the trusted construction site: the callback's own mapping
                # entry when it carried the key (even an explicit None, which
                # the baseline projection then omitted), otherwise the
                # exception's own code/stage attributes.  The daemon restores
                # these only after re-validating the sealed carrier against
                # the live exception chain; a claim, never an attestation.
                if isinstance(original_details, Mapping):
                    effective_code = (original_details["cause_code"]
                                      if "cause_code" in original_details else exc.code)
                    effective_stage = (original_details["cause_stage"]
                                       if "cause_stage" in original_details else exc.stage)
                else:
                    effective_code, effective_stage = exc.code, exc.stage
                setattr(wrapped, _WRAPPED_CAUSE_CLAIMS_KEY, _WrappedCauseClaims(
                    _WRAPPED_CAUSE_CLAIMS_TOKEN, exc, effective_code, effective_stage))
            raise wrapped from exc
        # T13 bound write: a callback that already declared an unresolved engine
        # state must not be followed by a fresh model snapshot.  That snapshot
        # would itself be a new engine operation taken while the real state is
        # exactly the reported uncertainty.  The shared classifier decides the
        # first stop condition on the returned envelope; additionally an
        # explicit EXECUTION_STATE_UNKNOWN/UNKNOWN code on a declared immediate
        # error/refusal/engine_error record is a local bound-path stop signal.
        # This changes no classifier, fallback, cursor, exception or read path.
        returned_outcome = classify_envelope(data)
        returned_state_unknown = returned_outcome.state == "unknown"
        if not returned_state_unknown:
            for declared in (data, data.get("data")):
                if not isinstance(declared, Mapping):
                    continue
                for record_key in ("error", "refusal", "engine_error"):
                    record = declared.get(record_key)
                    if (isinstance(record, Mapping)
                            and record.get("code") in ("EXECUTION_STATE_UNKNOWN", "UNKNOWN")):
                        returned_state_unknown = True
                        break
                if returned_state_unknown:
                    break
        if returned_state_unknown:
            # Resolve the existing ticket UNKNOWN with a dirty, untrusted post
            # fingerprint instead of dispatching another snapshot.  The
            # classifier record stays as judged; the published execution state
            # escalates to UNKNOWN so a code-only signal cannot leave a definite
            # failed/clean result.  Observed partial_change=True is retained at
            # its recognized top/immediate-data positions, and published
            # safe_retry on immediate declared error-like records is projected
            # to false by copy — the callback's own mappings are never mutated
            # and code/message/other fields are preserved verbatim.
            result = self.ledger.finish(ticket, outcome="unknown", changed=True, fingerprint=None)
            self._emit("finished", model_ref, operation_id=ticket.operation_id)
            detail_before = data.get("data")
            reported_partial = (data.get("partial_change") is True
                                or (isinstance(detail_before, Mapping)
                                    and detail_before.get("partial_change") is True))
            data = {**data, "success": False,
                    "partial_change": True if reported_partial else result["partial_change"],
                    "execution_state_unknown": True,
                    # The branch has decided the execution state is UNKNOWN;
                    # the published execution axis must say so instead of the
                    # classifier's pre-escalation state. The verification axis
                    # below is separate and stays exactly as classified.
                    "verified_outcome": "unknown",
                    "verification_status": returned_outcome.verification_status}
            # Keep empty placeholders empty so they cannot mask a later error.
            for record_key in ("error", "refusal", "engine_error"):
                record = data.get(record_key)
                if isinstance(record, Mapping):
                    data[record_key] = {**record, "safe_retry": False} if record else {}
            detail_after = data.get("data")
            if isinstance(detail_after, Mapping):
                detail_copy = dict(detail_after)
                for record_key in ("error", "refusal", "engine_error"):
                    record = detail_copy.get(record_key)
                    if isinstance(record, Mapping):
                        detail_copy[record_key] = {**record, "safe_retry": False} if record else {}
                data["data"] = detail_copy
            # The final published error is chosen here, never from the stale
            # classifier object: a declared direct or immediate-data
            # error/refusal/engine_error record (already a copy with
            # safe_retry projected false) keeps its code, message and
            # diagnostic fields; otherwise the classifier's own non-success
            # envelope is copied; only when neither exists — an
            # originally-successful classification — is the ordinary UNKNOWN
            # error synthesized honestly. The copy pins safe_retry=False and
            # never mutates callback-owned mappings.
            final_error: dict[str, Any] | None = None
            for declared in (data, data.get("data")):
                if not isinstance(declared, Mapping):
                    continue
                for record_key in ("error", "refusal", "engine_error"):
                    record = declared.get(record_key)
                    if isinstance(record, Mapping) and record:
                        final_error = dict(record)
                        break
                if final_error is not None:
                    break
            if final_error is None:
                final_error = (returned_outcome.error_envelope()
                               or {"code": "EXECUTION_STATE_UNKNOWN",
                                   "message": "the operation left an unresolved engine state",
                                   "safe_retry": False})
            final_error = {**final_error, "safe_retry": False}
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
                    if (name in {"partial_change", "execution_state_unknown"}
                            and (preserved.get(name) is True or value is True)):
                        preserved[name] = True
                        continue
                    preserved[name] = value
            return {
                "success": False,
                "data": preserved,
                "error": final_error,
                **envelope,
            }
        if terminal_model_close and model_ref is not None and data.get("success") is True:
            detail = data.get("data")
            proof = isinstance(detail, Mapping) and (
                detail.get("status") == "CLOSED"
                and detail.get("model_ref") == model_ref.as_dict()
                and detail.get("model_tag") == model_ref.model_tag
                and detail.get("model_tag_absent") is True
                and detail.get("other_model_tags_preserved") is True
                and detail.get("terminal_close_verified") is True
            )
            if not proof:
                self.ledger.finish(ticket, outcome="unknown", changed=True, fingerprint=None)
                self.on_state_change({"state": "unknown", "model_ref": model_ref.as_dict(),
                                      "operation_id": ticket.operation_id, "revision": self.ledger._models[model_ref.model_tag].revision,
                                      "dirty": True, "reason": "terminal_model_close_readback_unverified"})
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "model.close lacks exact terminal removal readback",
                    stage="post_dispatch", details={"safe_retry": False},
                )
            finished = self.ledger.finish(ticket, outcome="succeeded", changed=True, fingerprint=None)
            self.ledger.retire_model(model_ref)
            state_change = {
                "state": "retired", "model_ref": model_ref.as_dict(),
                "operation_id": ticket.operation_id, "revision": finished["revision"],
                "dirty": False, "model_ownership": self.ledger.model_ownership.get(model_ref.model_tag),
                "server_ownership": self.ledger.server_ownership,
            }
            try:
                # Persist the retired binding as one final state.  If persistence
                # fails, the prior durable active-ticket row remains an UNKNOWN
                # fence rather than a clean live model.
                self.on_state_change(state_change)
            except Exception as exc:
                state = self.ledger._models[model_ref.model_tag]
                state.retired = False
                state.dirty = True
                state.fingerprint = None
                raise ExecutionContractError(
                    "EXECUTION_STATE_UNKNOWN", "model was removed but the retired binding did not persist",
                    stage="post_dispatch", details={"cause_type": type(exc).__name__, "safe_retry": False},
                ) from exc
            envelope = {
                "execution": {
                    "session_id": self.ledger.session_id, "model_ref": model_ref.as_dict(),
                    "revision": finished["revision"], "dirty": False,
                    "server_ownership": self.ledger.server_ownership,
                    "model_ownership": self.ledger.model_ownership.get(model_ref.model_tag),
                    "cas_limit": "managed revision is not a COMSOL cross-client atomic CAS",
                    "request_id": ticket.request_id, "operation_id": ticket.operation_id,
                    "request_hash": ticket.request_hash,
                },
            }
            preserved = dict(detail)
            for name, value in data.items():
                if name not in {"success", "data", "error"}:
                    preserved.setdefault(name, value)
            return {"success": True, "data": preserved, "error": None, **envelope}
        if terminal_model_close and model_ref is None:
            self.ledger.finish(ticket, outcome="unknown", changed=True)
            raise ExecutionContractError("EXECUTION_STATE_UNKNOWN", "terminal close ticket has no exact ModelRef",
                                         stage="post_dispatch", details={"safe_retry": False})
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
        # plot.render persists the target plot group's dataset binding, while
        # the Worker model snapshot does not fingerprint plot groups.  Its
        # managed caller therefore conservatively advances revision on success.
        if force_changed_on_success and outcome == "succeeded":
            changed = True
        if _function_evaluate_read_ticket_proven(tool_name, effect, data, outcome, changed_engine):
            changed = False
        if (tool_name == "result_at_points" and effect == "evaluate" and ephemeral_scope is not None
                and ephemeral_scope.consume(data, outcome, changed_engine, callback)):
            changed = False
        if tool_name == "checkpoint_branch" and effect == "project_write" and outcome == "succeeded":
            if not changed_engine and self._owned_branch_proven(_owned_branch_proof, data, model_ref, snapshot, after):
                changed = False
            else:
                outcome, changed = "unknown", True
                data = {**data, "success": False, "execution_state_unknown": True,
                        "error": {"code": "EXECUTION_STATE_UNKNOWN", "message": "branch source proof not verified", "safe_retry": False}}
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
                # A domain adapter can know that work partially applied even
                # when the shared ledger can only classify the final ticket as
                # UNKNOWN. Preserve that stronger danger signal; the ledger's
                # outcome boolean must not downgrade domain evidence.
                if (name in {"partial_change", "execution_state_unknown"}
                        and (preserved.get(name) is True or value is True)):
                    preserved[name] = True
                    continue
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
        snapshot = {"fingerprint": raw["fingerprint"], "external_event_counter": counter,
                    "fingerprint_components_status": "UNAVAILABLE_LEGACY"}
        if "fingerprint_components" in raw:
            snapshot["fingerprint_components"] = _validated_fingerprint_components(
                raw["fingerprint_components"], model_tag=model_tag, fingerprint=raw["fingerprint"],
            )
            snapshot["fingerprint_components_status"] = "AVAILABLE_V1"
        return snapshot

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
