"""C01: one adapter that turns a domain result into the control plane's outcome.

Why this module exists
----------------------
The G3 domain modules (W13-W16, ``runtime.*``, ``result.sample_path``) return
their operation's ``data`` mapping and the control plane owns the
``{"success": ...}`` envelope.  Before this module the managed backend wrapped
*any* mapping a domain function returned as ``success=True``, so a result that
carried ``status.ok=False``, ``status.execution_state_unknown=True`` or
``cleanup.cleanup_failed=True`` was still published as a success
(``docs/comsol_mcp_design_v1/G3_1_ROOT_CAUSES.md`` R-02).

This module is the single, declared adapter between the two vocabularies:

* :func:`classify` reads a **closed set of contract keys** at the top level of
  the domain mapping.  It never recurses into user data and never looks for a
  ``success``/``status`` key anywhere but where the contract puts it, because a
  user property (or a material property group) may legitimately be named
  ``success``.
* :class:`DomainOutcome` carries the resulting execution state, the
  verification axis (``verification_status``) *separately* from the execution
  axis, the observed dangerous signals with their provenance, and the dispatch
  stage the deciding evidence comes from.
* :class:`DispatchWitness` records whether an engine call that can mutate the
  model was actually issued during the callback (see "pre-write proof" below).

Precedence (the one rule every entry point shares)
-------------------------------------------------
``unknown`` beats a surface ``success``: an unresolved engine state, an
unverified cleanup, or an explicit unknown flag is never reported as success.
Then ``failed``/``partial`` follow the declared ``failed``/``applied`` lists and
the ``engine_error`` record.  ``verification_status`` is a *separate* axis: an
operation may execute (``partial``/``succeeded``) while its numerical
verification is ``FAILED`` or ``NOT_RUN``.

Pre-write proof (never by exception class name alone)
-----------------------------------------------------
A refusal may claim ``pre_dispatch`` only when **both** hold:

1. the raise site declared the explicit validation stage
   (``ExecutionContractError.stage == "validation"``, normally via
   ``PreWriteRefusal``), and
2. the :class:`DispatchWitness` observed no mutation-class engine method during
   that call - an engine *read* having happened is not a change dispatch.

Any other failure keeps the fail-closed ``EXECUTION_STATE_UNKNOWN``
classification, because a callback that raised after a mutation verb may have
changed the model.

Vocabulary provenance
---------------------
The status/verification tokens below are the tokens the published G3 modules
actually return at the top level of their ``data`` mapping (W13-W16
``_status()``, ``runtime.*``, ``result.sample_path``).  A token that is not in
this table is classified as ``unknown`` rather than as a success, and the raw
token is preserved in ``DomainOutcome.details`` so the gap is diagnosable.
"""
from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------

STATE_SUCCEEDED = "succeeded"
STATE_PARTIAL = "partial"
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"

STATES = (STATE_SUCCEEDED, STATE_PARTIAL, STATE_FAILED, STATE_UNKNOWN)

VERIFICATION_PASSED = "PASSED"
VERIFICATION_FAILED = "FAILED"
VERIFICATION_NOT_RUN = "NOT_RUN"
VERIFICATION_NOT_APPLICABLE = "NOT_APPLICABLE"
VERIFICATION_UNKNOWN = "UNKNOWN"

#: Dispatch stages a decision can be based on.
STAGE_VALIDATION = "validation"
STAGE_POST_DISPATCH = "post_dispatch"

# ---------------------------------------------------------------------------
# declared contract keys and tokens
# ---------------------------------------------------------------------------

#: Top-level keys of a domain ``data`` mapping this adapter understands.  Every
#: other key is passed through untouched (it may be user data).
CONTRACT_KEYS = frozenset({
    "status", "engine_error", "cleanup", "success", "error", "applied", "failed",
    "not_executed", "execution_state_unknown", "engine_state_unknown",
    "partial_change", "verification_status", "execution_status", "verification",
    "invariant_results", "verdict", "checks", "readback_match", "refused", "refusal",
})

#: ``status`` tokens that mean "the engine state is unresolved".
UNKNOWN_STATUS_TOKENS = frozenset({
    "EXECUTION_STATE_UNKNOWN", "UNKNOWN", "ENGINE_STATE_UNKNOWN", "STATE_UNKNOWN",
})

#: ``status`` tokens that mean "the operation did not apply".  ``REFUSED`` is the
#: token the managed backend publishes for a proven pre-write refusal.
FAILED_STATUS_TOKENS = frozenset({"FAILED", "FAIL", "ERROR", "REFUSED", "REJECTED"})

#: ``status`` tokens that mean "part of the operation applied".
PARTIAL_STATUS_TOKENS = frozenset({"PARTIAL_FAILURE", "PARTIAL", "PARTIALLY_APPLIED"})

#: ``status`` tokens that mean "the operation applied / answered".
SUCCEEDED_STATUS_TOKENS = frozenset({
    "APPLIED", "OK", "COMPLETE", "VERIFIED", "OBSERVED", "PASS", "PASSED",
    "SUCCEEDED", "READY", "AVAILABLE", "REFRESHED", "INCOMPLETE",
})

#: Verification tokens as the transaction/mesh/physics validators publish them.
_VERIFICATION_ALIASES: dict[str, str] = {
    "PASS": VERIFICATION_PASSED, "PASSED": VERIFICATION_PASSED, "VERIFIED": VERIFICATION_PASSED,
    "FAIL": VERIFICATION_FAILED, "FAILED": VERIFICATION_FAILED,
    "NOT_RUN": VERIFICATION_NOT_RUN, "NOT_EVALUATED": VERIFICATION_NOT_RUN,
    "INCOMPLETE": VERIFICATION_NOT_RUN, "UNKNOWN": VERIFICATION_UNKNOWN,
    "NOT_APPLICABLE": VERIFICATION_NOT_APPLICABLE,
}

#: Declared numbers of a status mapping (the W13-W16 ``_status()`` contract).
_STATUS_MAPPING_KEYS = frozenset({
    "ok", "status", "partial_change", "execution_state_unknown", "engine_error",
    "applied", "failed", "not_executed", "applied_count", "failed_count",
    "not_executed_count", "readback", "readback_match",
})

#: Keys whose presence declares that the operation carries a verification axis.
VERIFICATION_EVIDENCE_KEYS = ("verification_status", "verification", "invariant_results", "verdict", "checks")


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _count(value: Any) -> int | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    return None


def _error_record(value: Any) -> dict[str, Any] | None:
    """Copy the declared ``code``/``message``/``safe_retry`` fields of an error."""
    if not isinstance(value, Mapping):
        return None
    record: dict[str, Any] = {}
    for key in ("code", "message", "safe_retry", "details"):
        if key in value:
            record[key] = value[key]
    return record or None


# ---------------------------------------------------------------------------
# dispatch witness
# ---------------------------------------------------------------------------

#: Engine methods that mutate the model.  The list is the verb vocabulary the
#: published G2/G3 modules actually call through a worker handle (``_call`` /
#: ``call_probe``); a name that starts with one of these prefixes, or that is
#: listed verbatim, is treated as a mutation.  Anything else is a read, which
#: is exactly what "an engine read is not a change dispatch" requires: the
#: witness is only ever used to *deny* a pre-dispatch claim.
MUTATION_METHOD_PREFIXES = (
    "set", "create", "remove", "rename", "move", "add", "delete", "import",
    "clear", "build", "finalize", "run", "solve", "save", "load", "generate",
    "insert", "edit", "update", "append", "replace", "reset", "attach",
    "detach", "enable", "disable", "split", "copy", "merge", "apply", "commit",
)

#: Selection methods that mutate selection state
SELECTION_MUTATION_METHODS = frozenset({
    "all", "named", "inherit",
})

#: Overloaded methods that are getters with 0 args, setters with 1+ args
OVERLOADED_SETTER_METHODS = frozenset({
    "label", "active", "lengthUnit", "comments", "name", "tag", "model",
})

#: Pure accessor / query methods that never mutate the model
KNOWN_READ_METHODS = frozenset({
    "tags", "uniquetag", "getComsolVersion", "getFilePath",
    "getType", "getString", "getDouble", "getInt", "getBoolean",
    "getStringArray", "getDoubleArray", "getIntArray",
    "getReal", "getImag", "getComplex", "getCoordinates", "getCoordinatesShape",
    "getValue", "getData", "getEntryKeys", "getEntryKeyIndex", "getEntryTypes",
    "index", "ndims", "size", "hasField", "isInheriting", "isActive", "hasProduct",
    "param", "variable", "component", "modelNode", "physics", "material", "study", "sol",
    "mesh", "result", "numerical", "plot", "export", "field", "prop", "dataset",
    "feature", "selection", "measure", "cminpack", "batch", "func", "probe",
    "table", "node", "get", "problems", "getNData", "getTableData",
    "getColumnHeaders", "getRowHeaders", "getNRows", "getFilledReal",
    "getFilledImag", "getImagData", "getPVals",
    "getLastComputationTime", "getLastComputationDate", "getLastComputationVersion",
    "isComplex", "isAxisymmetric", "isPlotGroup", "getSDim", "getSolutioninfo",
    "getOuterSolnum", "getMaxInner", "getLevelNames", "getSolnum", "getSolnums",
    "properties", "getPNames", "getPvals", "getUnits", "getUnit", "getPNamesOuter", "getPUnitsOuter",
    "getSolverSequence",
})


def is_mutation_call(method: str, args: Sequence[Any] = (), command: str = "call", receiver: Any = None) -> bool:
    """True when invoking an engine command/method can change the model.

    Distinguishes pure accessors from setters/mutations using method name,
    signature/arguments, receiver context, and command type. Unknown methods,
    trusted code execution, and unclassifiable calls default to True (fail-closed).
    """
    if not isinstance(method, str) or not method:
        return False
    if command in ("code_execute", "trusted_code", "execute_java_code"):
        return True
    if command == "code_compile":
        return False
    if command == "modelutil":
        if method in ("create", "load", "remove", "clear"):
            return True
        if method in ("tags", "uniquetag", "getComsolVersion", "disconnect"):
            return False
        return True

    # Check explicit mutation prefixes first
    if method.startswith(MUTATION_METHOD_PREFIXES):
        return True

    # Selection direct mutations
    if method in SELECTION_MUTATION_METHODS:
        return True

    # Overloaded getter/setter methods (0 args = read, 1+ args = write)
    if method in OVERLOADED_SETTER_METHODS:
        return len(args) > 0

    # Special handling for geom:
    # selection.geom(dim, entities) or selection.geom(dim) sets selection geometry.
    # component.geom("geom1") or model.geom() navigates geometry.
    if method == "geom":
        if len(args) >= 2:
            return True
        if len(args) == 1 and isinstance(args[0], int):
            return True
        return False

    # Known pure accessors and container navigators
    if method in KNOWN_READ_METHODS:
        return False

    # Fail-closed: any unclassified or unknown method must be treated as a potential mutation
    return True


def is_mutation_method(method: str) -> bool:
    """Backwards-compatible check: True when ``method`` can mutate the model."""
    return is_mutation_call(method, args=())


@dataclass
class DispatchWitness:
    """Records the engine calls issued during one management callback.

    ``record`` is called by the worker handle layer for every method dispatched,
    recording command, receiver, method, and arguments.
    """

    methods: list[str] = field(default_factory=list)
    first_mutation: str | None = None
    dispatches: list[dict[str, Any]] = field(default_factory=list)

    def record(self, method: str, *args: Any, command: str = "call", receiver: Any = None) -> None:
        if not isinstance(method, str) or not method:
            return
        self.methods.append(method)
        is_mut = is_mutation_call(method, args=args, command=command, receiver=receiver)
        self.dispatches.append({
            "command": command,
            "method": method,
            "args_count": len(args),
            "receiver": str(receiver) if receiver else None,
            "is_mutation": is_mut,
        })
        if self.first_mutation is None and is_mut:
            self.first_mutation = method

    @property
    def mutation_issued(self) -> bool:
        return self.first_mutation is not None

    @property
    def call_count(self) -> int:
        return len(self.methods)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_calls": self.call_count,
            "mutation_method": self.first_mutation,
            "mutation_issued": self.mutation_issued,
            "methods": sorted(set(self.methods)),
            "dispatches": self.dispatches,
        }


_WITNESS: contextvars.ContextVar[DispatchWitness | None] = contextvars.ContextVar(
    "comsol_mcp_dispatch_witness", default=None
)


@contextlib.contextmanager
def witness_scope() -> Iterator[DispatchWitness]:
    """Install a fresh witness for the enclosed callback; nested scopes stack."""
    witness = DispatchWitness()
    token = _WITNESS.set(witness)
    try:
        yield witness
    finally:
        _WITNESS.reset(token)


def record_engine_method(method: str, *args: Any, command: str = "call", receiver: Any = None) -> None:
    """Feed one dispatched engine method into the active witness, if any."""
    witness = _WITNESS.get()
    if witness is not None:
        witness.record(method, *args, command=command, receiver=receiver)


def current_witness() -> DispatchWitness | None:
    return _WITNESS.get()


# ---------------------------------------------------------------------------
# the outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DomainOutcome:
    """The unified result of one domain operation."""

    operation: str
    state: str
    verification_status: str
    failure: dict[str, Any] | None = None
    engine_error: dict[str, Any] | None = None
    cleanup_failed: bool = False
    execution_state_unknown: bool = False
    verification: dict[str, Any] | None = None
    applied_count: int | None = None
    failed_count: int | None = None
    not_executed_count: int | None = None
    status_token: str | None = None
    execution_status: str | None = None
    dispatch_stage: str = STAGE_POST_DISPATCH
    witness: dict[str, Any] | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.state == STATE_SUCCEEDED

    @property
    def unknown(self) -> bool:
        return self.state == STATE_UNKNOWN

    @property
    def dirty(self) -> bool:
        """True when a mutation may have happened (the model must not be trusted)."""
        return self.state in {STATE_PARTIAL, STATE_UNKNOWN} or bool(self.applied_count)

    def error_envelope(self) -> dict[str, Any] | None:
        """The ``error`` object an MCP caller sees for a non-success outcome."""
        if self.success:
            return None
        if self.failure is not None:
            return dict(self.failure)
        if self.engine_error is not None:
            return {
                "code": self.engine_error.get("code") or "ENGINE_CALL_FAILED",
                "message": self.engine_error.get("message") or "the engine refused the operation",
                "safe_retry": bool(self.engine_error.get("safe_retry")),
            }
        if self.state == STATE_UNKNOWN:
            return {
                "code": "EXECUTION_STATE_UNKNOWN",
                "message": "the operation left an unresolved engine state",
                "safe_retry": False,
            }
        return {
            "code": "OPERATION_FAILED",
            "message": "the operation did not apply",
            "safe_retry": False,
        }

    def envelope_fields(self) -> dict[str, Any]:
        """Fields a callback envelope must carry so every later layer agrees."""
        return {
            "success": self.success,
            "domain_state": self.state,
            "verification_status": self.verification_status,
            "execution_state_unknown": self.unknown,
            "partial_change": self.state == STATE_PARTIAL or bool(self.applied_count),
            "cleanup_failed": self.cleanup_failed,
            "dispatch_stage": self.dispatch_stage,
        }

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation": self.operation,
            "state": self.state,
            "verification_status": self.verification_status,
            "cleanup_failed": self.cleanup_failed,
            "execution_state_unknown": self.execution_state_unknown,
            "dispatch_stage": self.dispatch_stage,
        }
        for name in ("applied_count", "failed_count", "not_executed_count", "status_token", "execution_status"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        for name in ("failure", "engine_error", "verification", "witness"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.details:
            payload["details"] = dict(self.details)
        return payload


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def _classify_status(status: Any, details: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Interpret the declared ``status`` key; returns (state hint, extra signals)."""
    signals: dict[str, Any] = {}
    if status is None:
        return None, signals
    if isinstance(status, str):
        token = status.strip().upper()
        details["status_token"] = status
        signals["status_token"] = token
        if token in UNKNOWN_STATUS_TOKENS:
            return STATE_UNKNOWN, signals
        if token in FAILED_STATUS_TOKENS:
            return STATE_FAILED, signals
        if token in PARTIAL_STATUS_TOKENS:
            return STATE_PARTIAL, signals
        if token in SUCCEEDED_STATUS_TOKENS:
            return STATE_SUCCEEDED, signals
        # An unrecognised token is never a success: report it and stay unknown.
        details["unrecognised_status_token"] = status
        return STATE_UNKNOWN, signals
    if isinstance(status, Mapping):
        unexpected = set(status) - _STATUS_MAPPING_KEYS
        if unexpected:
            details["status_unexpected_keys"] = sorted(str(key) for key in unexpected)
        ok = _as_bool(status.get("ok"))
        if ok is False:
            signals["status_ok_false"] = True
        token = _string(status.get("status"))
        if token is not None:
            inner, inner_signals = _classify_status(token, details)
            signals.update(inner_signals)
            signals["status_token"] = inner_signals.get("status_token", token.upper())
            if inner is not None:
                signals["status_state"] = inner
        for name in ("execution_state_unknown", "partial_change"):
            value = _as_bool(status.get(name))
            if value is not None:
                signals[f"status_{name}"] = value
        if isinstance(status.get("engine_error"), Mapping):
            signals["status_engine_error"] = dict(status["engine_error"])  # type: ignore[arg-type]
        applied = _count(status.get("applied"))
        failed = _count(status.get("failed"))
        not_executed = _count(status.get("not_executed"))
        for name, value in (("applied", applied), ("failed", failed), ("not_executed", not_executed)):
            if value is not None:
                signals[f"status_{name}_count"] = value
        if _as_bool(status.get("readback_match")) is False:
            signals["status_readback_match"] = False
        return None, signals
    details["status_unexpected_type"] = type(status).__name__
    return STATE_UNKNOWN, signals


def _classify_verification(data: Mapping[str, Any], details: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Read the verification axis; never infer it from the execution axis."""
    declared = [key for key in VERIFICATION_EVIDENCE_KEYS if key in data]
    status_block = data.get("status")
    readback_mismatch = (
        data.get("readback_match") is False
        or (isinstance(status_block, Mapping) and status_block.get("readback_match") is False)
    )
    if not declared and not readback_mismatch:
        return VERIFICATION_NOT_APPLICABLE, None
    evidence: dict[str, Any] = {"declared_by": declared}
    raw_status = data.get("verification_status")
    candidate = _string(raw_status)
    verification = data.get("verification")
    if candidate is None and isinstance(verification, Mapping):
        candidate = _string(verification.get("status"))
    invariants = data.get("invariant_results")
    if candidate is None and isinstance(invariants, Mapping):
        candidate = _string(invariants.get("status"))
        if isinstance(invariants.get("checks"), Sequence):
            evidence["invariant_checks"] = len(invariants["checks"])  # type: ignore[arg-type]
    verdict = _string(data.get("verdict"))
    checks = data.get("checks")
    if candidate is None and verdict is not None:
        candidate = verdict
        evidence["verdict"] = verdict
        if isinstance(checks, Sequence):
            evidence["checks_total"] = len(checks)  # type: ignore[arg-type]
            evidence["checks_failed"] = sum(
                1 for row in checks
                if isinstance(row, Mapping) and str(row.get("status", "")).upper() in {"FAIL", "FAILED"}
            )
    if isinstance(verification, Mapping):
        evidence["verification"] = {key: verification.get(key) for key in ("status", "reason", "scope") if key in verification}
    if readback_mismatch:
        # The engine's own readback contradicted the request: that is a failed
        # verification whatever else the operation declared.
        evidence["readback_match"] = False
        evidence["declared_status"] = candidate
        evidence["reason"] = "the engine readback did not confirm the requested values"
        return VERIFICATION_FAILED, evidence
    if candidate is None:
        # The operation carries verification evidence but published no verdict:
        # that is NOT_RUN, never a pass.
        evidence["reason"] = "the operation declared a verification axis but published no verdict"
        return VERIFICATION_NOT_RUN, evidence
    mapped = _VERIFICATION_ALIASES.get(candidate.strip().upper())
    if mapped is None:
        details["unrecognised_verification_token"] = candidate
        evidence["raw_status"] = candidate
        return VERIFICATION_UNKNOWN, evidence
    evidence["raw_status"] = candidate
    return mapped, evidence


def classify(operation: str, data: Mapping[str, Any] | None, *,
             dispatch_stage: str = STAGE_POST_DISPATCH,
             witness: DispatchWitness | Mapping[str, Any] | None = None,
             engine_changed: bool | None = None) -> DomainOutcome:
    """Classify one domain ``data`` mapping into a :class:`DomainOutcome`.

    Only :data:`CONTRACT_KEYS` are read, and only at the top level of ``data``.
    """
    if data is None:
        return DomainOutcome(
            operation=operation, state=STATE_UNKNOWN,
            verification_status=VERIFICATION_NOT_RUN if operation else VERIFICATION_NOT_APPLICABLE,
            execution_state_unknown=True,
            dispatch_stage=dispatch_stage,
            witness=_witness_dict(witness),
            details={"reason": "the operation returned no data mapping"},
        )
    if not isinstance(data, Mapping):
        return DomainOutcome(
            operation=operation, state=STATE_UNKNOWN,
            verification_status=VERIFICATION_NOT_APPLICABLE,
            execution_state_unknown=True,
            dispatch_stage=dispatch_stage,
            witness=_witness_dict(witness),
            details={"reason": f"the operation returned {type(data).__name__}, not a mapping"},
        )

    details: dict[str, Any] = {}
    signals: dict[str, Any] = {}
    witness_payload = _merge_witness_provenance(
        data.get("witness"), _witness_dict(witness)
    )
    witness_mutation = _witness_mutation_state(witness_payload)

    status_state, status_signals = _classify_status(data.get("status"), details)
    signals.update(status_signals)

    for name in ("execution_state_unknown", "engine_state_unknown", "cleanup_failed", "partial_change"):
        value = _as_bool(data.get(name))
        if value is not None:
            signals[name] = value
    cleanup = data.get("cleanup")
    if isinstance(cleanup, Mapping):
        failed = _as_bool(cleanup.get("cleanup_failed"))
        if failed is not None:
            signals["cleanup_failed"] = failed
            signals["cleanup_created"] = _as_bool(cleanup.get("created"))
            signals["cleanup_verified_removed"] = _as_bool(cleanup.get("verified_removed"))
        if failed and isinstance(cleanup.get("error"), Mapping):
            signals["cleanup_error"] = dict(cleanup["error"])  # type: ignore[arg-type]

    engine_error = _error_record(data.get("engine_error")) or _error_record(signals.get("status_engine_error"))
    applied_count = _count(data.get("applied"))
    if applied_count is None:
        applied_count = signals.get("status_applied_count")
    failed_count = _count(data.get("failed"))
    if failed_count is None:
        failed_count = signals.get("status_failed_count")
    not_executed_count = _count(data.get("not_executed"))
    if not_executed_count is None:
        not_executed_count = signals.get("status_not_executed_count")

    declared_success = _as_bool(data.get("success"))
    success_record = _error_record(data.get("refusal")) or _error_record(data.get("error"))
    declared_refusal = _as_bool(data.get("refused")) is True
    verification_status, verification = _classify_verification(data, details)

    unknown_signal = bool(
        signals.get("execution_state_unknown")
        or signals.get("engine_state_unknown")
        or signals.get("cleanup_failed")
        or signals.get("status_execution_state_unknown")
    )
    mutation_evidence = bool(applied_count) or bool(signals.get("partial_change")) or bool(signals.get("status_partial_change"))
    # A callback can publish a refusal after the worker has already dispatched a
    # mutation-class method.  The witness is the authoritative provenance for
    # that fact; applied/partial counters are only a separate, domain-level hint.
    # A missing/null witness contributes no mutation evidence, but it also
    # cannot satisfy the separate witness proof required for a clean refusal.
    # A true mutation witness is the authoritative contradiction when present.
    if witness_mutation is True:
        mutation_evidence = True
        signals["witness_mutation_issued"] = True
    elif witness_mutation is False:
        signals["witness_mutation_issued"] = False
    elif witness_payload is not None:
        signals["witness_mutation_issued"] = None
    if engine_changed is True:
        mutation_evidence = True

    # A clean refusal needs both proofs: an explicit validation stage and an
    # existing witness that explicitly says no mutation was dispatched.  A
    # mutation witness, a post-dispatch stage, a missing/null witness, or any
    # other mutation evidence leaves the pre-dispatch claim unproven and must
    # poison the state for reconciliation.  Do not let the refusal's own error
    # code mask that UNKNOWN outcome, and preserve the observed stage below.
    refusal_proven = (
        declared_refusal
        and dispatch_stage == STAGE_VALIDATION
        and witness_mutation is False
        and not unknown_signal
        and not mutation_evidence
    )
    refusal_contradiction = declared_refusal and not refusal_proven
    if refusal_contradiction:
        signals["refusal_proof_contradiction"] = True
        unknown_signal = True
        details["unproven_pre_dispatch"] = True
        details["refusal_proof"] = {
            "dispatch_stage": dispatch_stage,
            "witness_mutation_issued": witness_mutation,
        }
        if success_record is not None:
            details["contradictory_refusal"] = dict(success_record)

    # -- precedence 1: an unresolved engine state or an unverified cleanup -----
    if refusal_proven:
        # A refusal the dispatcher could prove happened before its first engine
        # mutation is a clean failure: no unknown state, nothing to reconcile.
        state = STATE_FAILED
    elif unknown_signal or status_state == STATE_UNKNOWN:
        state = STATE_UNKNOWN
    # -- precedence 2: explicit failure records -------------------------------
    elif engine_error is not None or failed_count or status_state == STATE_FAILED:
        state = STATE_PARTIAL if (applied_count or mutation_evidence) else STATE_FAILED
    elif status_state == STATE_PARTIAL:
        # The operation's own status token is more specific than a bare
        # ``success: false``: it applied part of its work.
        state = STATE_PARTIAL
    elif signals.get("status_ok_false"):
        # ``status.ok == false`` is a failure declaration even when the module
        # published no ``failed`` entry; it must not be masked by a surface
        # ``success: true``.
        state = STATE_PARTIAL if mutation_evidence else STATE_FAILED
    elif success_record is not None and declared_success is False:
        state = STATE_PARTIAL if mutation_evidence else STATE_FAILED
    elif declared_success is False:
        state = STATE_PARTIAL if mutation_evidence else STATE_FAILED
    elif failed_count:
        state = STATE_PARTIAL if applied_count else STATE_FAILED
    else:
        state = STATE_SUCCEEDED

    # A ``partial_change`` flag alone is not a failure: a property write that
    # applied (and therefore changed the model) legitimately reports it
    # together with ``success: true``.

    # Execution and verification are two axes.  A run that executed but did not
    # verify its numbers is not a clean success, but the execution status stays
    # reported as it is (never folded into the failure).
    execution_status = _execution_status(data)
    if state == STATE_SUCCEEDED and verification_status == VERIFICATION_FAILED:
        state = STATE_PARTIAL
    # A refusal whose proof was contradicted is an UNKNOWN reconciliation case,
    # even though the refusal block carries an otherwise usable error code.
    failure = None if refusal_contradiction else success_record
    if state != STATE_SUCCEEDED and failure is None:
        declared_failure = _first_failed_record(data.get("failed"))
        if declared_failure is not None:
            failure = declared_failure
        elif engine_error is not None:
            failure = {
                "code": engine_error.get("code") or "ENGINE_CALL_FAILED",
                "message": engine_error.get("message") or "the engine refused the operation",
                "safe_retry": bool(engine_error.get("safe_retry")),
            }
        elif signals.get("cleanup_error"):
            failure = {
                "code": (signals["cleanup_error"].get("code") or "EXECUTION_STATE_UNKNOWN"),
                "message": (signals["cleanup_error"].get("message")
                            or "the operation could not verify its cleanup"),
                "safe_retry": False,
            }
        elif state == STATE_UNKNOWN:
            failure = {
                "code": "EXECUTION_STATE_UNKNOWN",
                "message": "the operation left an unresolved engine state",
                "safe_retry": False,
            }
        elif details.get("unrecognised_status_token"):
            failure = {
                "code": "UNKNOWN_DOMAIN_STATUS",
                "message": f"the operation reported the unrecognised status "
                           f"{details['unrecognised_status_token']!r}",
                "safe_retry": False,
            }
        else:
            failure = {
                "code": "OPERATION_FAILED",
                "message": "the operation reported a failed domain status",
                "safe_retry": False,
            }

    return DomainOutcome(
        operation=operation,
        state=state,
        verification_status=verification_status,
        failure=failure,
        engine_error=engine_error,
        cleanup_failed=bool(signals.get("cleanup_failed")),
        execution_state_unknown=bool(
            signals.get("execution_state_unknown")
            or signals.get("engine_state_unknown")
            or signals.get("refusal_proof_contradiction")
        ),
        verification=verification,
        applied_count=applied_count,
        failed_count=failed_count,
        not_executed_count=not_executed_count,
        status_token=details.get("status_token"),
        execution_status=execution_status,
        dispatch_stage=dispatch_stage,
        witness=witness_payload,
        details=details,
    )


def _execution_status(data: Mapping[str, Any]) -> str | None:
    """The declared execution status, independent of the verification axis.

    Declared positions only: ``execution_status`` or ``execution.status``.  A
    caller's own nested data is never searched for it.
    """
    value = data.get("execution_status")
    if value is None:
        execution = data.get("execution")
        if isinstance(execution, Mapping):
            value = execution.get("status")
    text = _string(value)
    return text.upper() if text else None


def _first_failed_record(value: Any) -> dict[str, Any] | None:
    """The first declared failure of a domain ``failed`` list, if it carries one.

    The W13-W16 modules publish ``failed`` as a list of records whose ``error``
    object holds the engine/contract code.  Only that declared position is read;
    nothing else in the list is interpreted (a caller's own payload may live
    there).
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = _error_record(item.get("error"))
        if record is not None:
            return record
    return None


def _witness_dict(witness: DispatchWitness | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if witness is None:
        return None
    if isinstance(witness, DispatchWitness):
        return witness.as_dict()
    return dict(witness)


def _witness_mutation_state(witness: Mapping[str, Any] | None) -> bool | None:
    """Return the explicit mutation bit from existing witness provenance.

    ``False`` is proof only when it is explicitly published.  A missing or
    null bit is unknown; it cannot satisfy the pre-dispatch proof and must not
    be inferred from a method list or from the absence of a witness.
    """
    if not isinstance(witness, Mapping):
        return None
    value = witness.get("mutation_issued")
    return value if isinstance(value, bool) else None


def _merge_witness_provenance(*containers: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Carry an already-published witness into envelope classification.

    The inner domain record is preferred for descriptive fields, while the
    mutation bit is monotone: an inner/outer ``True`` cannot be cleared by an
    outer ``False``.  An explicit ``null`` keeps a false record from becoming
    a fabricated clean proof.  This only merges existing records; it never
    creates a new dispatch or a synthetic clean witness.
    """
    candidates: list[Mapping[str, Any]] = [
        value for value in containers
        if isinstance(value, Mapping)
    ]
    if not candidates:
        return None
    merged: dict[str, Any] = {}
    states: list[bool | None] = []
    has_explicit_state = False
    for candidate in candidates:
        for key, value in candidate.items():
            if key == "mutation_issued":
                has_explicit_state = True
                if isinstance(value, bool):
                    states.append(value)
                else:
                    states.append(None)
                continue
            if key not in merged:
                merged[key] = value
    if states:
        # A true witness is monotone and cannot be masked by an outer false.
        # Conversely, an explicit null keeps a false witness from becoming a
        # fabricated clean proof.
        if any(value is True for value in states):
            merged["mutation_issued"] = True
        elif any(value is None for value in states):
            merged["mutation_issued"] = None
        else:
            merged["mutation_issued"] = False
    elif has_explicit_state:
        merged["mutation_issued"] = None
    return merged


def _envelope_witness(envelope: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any] | None:
    """Find witness provenance in the published envelope without re-dispatching."""
    candidates: list[Mapping[str, Any] | None] = []
    for container in (detail, envelope):
        if not isinstance(container, Mapping):
            continue
        direct = container.get("witness")
        candidates.append(direct if isinstance(direct, Mapping) else None)
        domain_outcome = container.get("domain_outcome")
        if isinstance(domain_outcome, Mapping):
            nested = domain_outcome.get("witness")
            candidates.append(nested if isinstance(nested, Mapping) else None)
        error = container.get("error")
        if isinstance(error, Mapping):
            error_details = error.get("details")
            if isinstance(error_details, Mapping):
                nested = error_details.get("witness")
                candidates.append(nested if isinstance(nested, Mapping) else None)
    return _merge_witness_provenance(*candidates)


# ---------------------------------------------------------------------------
# envelope / final-state helpers shared by every entry point
# ---------------------------------------------------------------------------


def domain_envelope(operation: str, data: Mapping[str, Any], *,
                    dispatch_stage: str = STAGE_POST_DISPATCH,
                    witness: DispatchWitness | Mapping[str, Any] | None = None,
                    engine_changed: bool | None = None) -> dict[str, Any]:
    """Wrap a domain ``data`` mapping in the shared callback envelope."""
    outcome = classify(operation, data, dispatch_stage=dispatch_stage, witness=witness,
                       engine_changed=engine_changed)
    envelope: dict[str, Any] = {"data": dict(data), **outcome.envelope_fields()}
    envelope["error"] = outcome.error_envelope()
    envelope["domain_outcome"] = outcome.as_dict()
    return envelope


def _merge_contract_envelopes(envelope: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any]:
    """Merge outer envelope and inner detail without discarding danger signals (F05)."""
    merged: dict[str, Any] = {key: detail[key] for key in CONTRACT_KEYS if key in detail}
    for key in CONTRACT_KEYS:
        if key in envelope and key not in merged:
            merged[key] = envelope[key]

    # F05: Danger signals must only escalate; outer False/None cannot overwrite inner True
    for flag in ("execution_state_unknown", "engine_state_unknown", "cleanup_failed", "partial_change"):
        d_val = _as_bool(detail.get(flag))
        e_val = _as_bool(envelope.get(flag))
        if d_val is True or e_val is True:
            merged[flag] = True
        elif d_val is False or e_val is False:
            if merged.get(flag) is not True:
                merged[flag] = False

    # Cleanup mapping merging
    d_clean = detail.get("cleanup")
    e_clean = envelope.get("cleanup")
    clean_dict: dict[str, Any] = {}
    if isinstance(d_clean, Mapping):
        clean_dict.update(d_clean)
    if isinstance(e_clean, Mapping):
        clean_dict.update(e_clean)
    if clean_dict:
        if (isinstance(d_clean, Mapping) and d_clean.get("cleanup_failed") is True) or \
           (isinstance(e_clean, Mapping) and e_clean.get("cleanup_failed") is True) or \
           merged.get("cleanup_failed") is True:
            clean_dict["cleanup_failed"] = True
            merged["cleanup_failed"] = True
        merged["cleanup"] = clean_dict

    # Status merging: UNKNOWN > PARTIAL > FAILED > SUCCEEDED
    def _status_rank(st_val: Any) -> tuple[int, str]:
        if isinstance(st_val, Mapping):
            token = _string(st_val.get("status")) or ("APPLIED" if st_val.get("ok") is True else "FAILED")
            if st_val.get("execution_state_unknown") is True or token in UNKNOWN_STATUS_TOKENS:
                return (4, "EXECUTION_STATE_UNKNOWN")
            if st_val.get("partial_change") is True or token in PARTIAL_STATUS_TOKENS:
                return (3, token or "PARTIAL_FAILURE")
            if st_val.get("ok") is False or token in FAILED_STATUS_TOKENS:
                return (2, token or "FAILED")
            return (1, token or "APPLIED")
        token = _string(st_val)
        if not token:
            return (0, "")
        token_upper = token.upper()
        if token_upper in UNKNOWN_STATUS_TOKENS:
            return (4, token_upper)
        if token_upper in PARTIAL_STATUS_TOKENS:
            return (3, token_upper)
        if token_upper in FAILED_STATUS_TOKENS:
            return (2, token_upper)
        if token_upper in SUCCEEDED_STATUS_TOKENS:
            return (1, token_upper)
        return (4, token)

    d_rank, _ = _status_rank(detail.get("status"))
    e_rank, _ = _status_rank(envelope.get("status"))
    if d_rank > e_rank and d_rank >= 2:
        merged["status"] = detail["status"]
    elif e_rank > d_rank and e_rank >= 2:
        merged["status"] = envelope["status"]
    elif "status" in envelope:
        merged["status"] = envelope["status"]
    elif "status" in detail:
        merged["status"] = detail["status"]

    # Success / ok: False wins over True
    d_succ = _as_bool(detail.get("success"))
    e_succ = _as_bool(envelope.get("success"))
    if d_succ is False or e_succ is False:
        merged["success"] = False
    elif d_succ is True and e_succ is True:
        merged["success"] = True
    elif d_succ is not None:
        merged["success"] = d_succ
    elif e_succ is not None:
        merged["success"] = e_succ

    # Counts: keep non-zero counts
    for count_key in ("applied", "applied_count", "failed", "failed_count", "not_executed", "not_executed_count"):
        d_cnt = _count(detail.get(count_key))
        e_cnt = _count(envelope.get(count_key))
        if d_cnt or e_cnt:
            merged[count_key] = max(d_cnt or 0, e_cnt or 0)

    # Errors: preserve error records
    for err_key in ("error", "engine_error", "failure", "refusal"):
        if err_key in detail and err_key not in envelope:
            merged[err_key] = detail[err_key]

    return merged


def classify_envelope(envelope: Mapping[str, Any], *, engine_changed: bool | None = None) -> DomainOutcome:
    """Classify a callback envelope (or a domain mapping) with the shared rule.

    The envelope form is what the managed backend, the G2 property callbacks and
    the legacy/fallback callbacks return; it carries the same contract keys plus
    ``domain_state`` when the G3 domain adapter already classified the call.
    """
    raw_detail = envelope.get("data")
    detail: Mapping[str, Any] = raw_detail if isinstance(raw_detail, Mapping) else {}
    declared_state = _string(envelope.get("domain_state"))
    merged = _merge_contract_envelopes(envelope, detail)
    witness = _envelope_witness(envelope, detail)
    dispatch_stage = (
        _string(envelope.get("dispatch_stage"))
        or _string(detail.get("dispatch_stage"))
        or STAGE_POST_DISPATCH
    )
    outcome = classify(
        str(envelope.get("operation") or detail.get("operation") or ""), merged,
        dispatch_stage=dispatch_stage,
        witness=witness,
        engine_changed=engine_changed,
    )
    if declared_state not in STATES or declared_state == outcome.state:
        return outcome
    # The domain adapter's own classification always wins: it saw the raw data
    # mapping and the mutation witness. Only escalate (never soften) a state.
    order = {STATE_SUCCEEDED: 0, STATE_PARTIAL: 1, STATE_FAILED: 2, STATE_UNKNOWN: 3}
    if order[declared_state] <= order[outcome.state]:
        return outcome
    failure = outcome.failure
    if declared_state == STATE_UNKNOWN:
        failure = {
            "code": "EXECUTION_STATE_UNKNOWN",
            "message": "the domain adapter classified the operation as an unresolved engine state",
            "safe_retry": False,
        }
    return replace(outcome, state=declared_state, failure=failure,
                   execution_state_unknown=outcome.execution_state_unknown or declared_state == STATE_UNKNOWN)


def final_state(envelope: Mapping[str, Any], *, engine_changed: bool | None = None) -> tuple[str, bool]:
    """The one final judgement every entry point uses: ``(outcome, changed)``.

    A non-``failed`` outcome of a write-ticketed operation means the model must
    be treated as changed: a ticket is only taken for a non-inspect effect, so
    "succeeded", "partial" and "unknown" all invalidate the previous revision.
    A clean failure changes nothing unless the engine fingerprint moved.
    """
    outcome = classify_envelope(envelope, engine_changed=engine_changed)
    changed = (
        outcome.state != STATE_FAILED
        or bool(outcome.applied_count)
        or bool(engine_changed)
    )
    return outcome.state, changed


__all__ = [
    "CONTRACT_KEYS",
    "DomainOutcome",
    "DispatchWitness",
    "FAILED_STATUS_TOKENS",
    "MUTATION_METHOD_PREFIXES",
    "PARTIAL_STATUS_TOKENS",
    "STAGE_POST_DISPATCH",
    "STAGE_VALIDATION",
    "STATES",
    "STATE_FAILED",
    "STATE_PARTIAL",
    "STATE_SUCCEEDED",
    "STATE_UNKNOWN",
    "SUCCEEDED_STATUS_TOKENS",
    "UNKNOWN_STATUS_TOKENS",
    "VERIFICATION_EVIDENCE_KEYS",
    "VERIFICATION_FAILED",
    "VERIFICATION_NOT_APPLICABLE",
    "VERIFICATION_NOT_RUN",
    "VERIFICATION_PASSED",
    "VERIFICATION_UNKNOWN",
    "classify",
    "classify_envelope",
    "current_witness",
    "domain_envelope",
    "final_state",
    "is_mutation_method",
    "record_engine_method",
    "witness_scope",
]
